"""The standalone scan's progress reporting and cancellation.

The bar on the auditing screen used to be a sweep: the audit ran inline
in the POST, so there was no way to know how far along it was and nothing
to report even if there had been. It now runs on a worker thread and
reports through the same ``progress`` callback the project-scoped path
uses, so these tests exercise the worker directly — the part that has to
be right for the number on screen to mean anything.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest
from typing_extensions import override

from auto_a11y.pdf.scan_store import ScanRecord, ScanStore
from auto_a11y.web.routes.pdf_scan import CANCELLED_REASON, run_scan_in_thread

OWNER = "507f1f77bcf86cd799439011"


@pytest.fixture
def pdf_bytes(tmp_path: Path) -> bytes:
    path = tmp_path / "source.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    with pdf.open_metadata() as meta:
        meta["dc:title"] = "Progress fixture"
    pdf.save(path)
    return path.read_bytes()


@pytest.fixture
def store(tmp_path: Path) -> ScanStore:
    return ScanStore(base_dir=tmp_path / "storage")


def _scan(store: ScanStore, pdf_bytes: bytes) -> str:
    record = store.create(
        owner_user_id=OWNER, pdf_bytes=pdf_bytes, original_filename="a.pdf",
    )
    run_scan_in_thread(
        store=store, record=record, api_key=None, ai_model=None,
    )
    return record.scan_id


def test_a_completed_scan_reports_a_full_bar(
    store: ScanStore, pdf_bytes: bytes
) -> None:
    """The last thing written is 100%, so the bar never stops at 97%."""
    scan_id = _scan(store, pdf_bytes)

    fraction, _step = store.get_progress(OWNER, scan_id)

    assert fraction == 1.0


def test_a_completed_scan_is_audited(store: ScanStore, pdf_bytes: bytes) -> None:
    scan_id = _scan(store, pdf_bytes)

    record = store.get(OWNER, scan_id)

    assert record is not None
    assert record.status == "audited"
    assert record.result.get("check_results")


class _RecordingStore(ScanStore):
    """A store that remembers every progress tick, then stores it.

    A subclass rather than a monkeypatch: the worker takes the store as
    an argument, so overriding the method on an instance it is handed is
    both simpler and something the type checkers can follow.
    """

    def __init__(self, base_dir: Path) -> None:
        super().__init__(base_dir)
        self.ticks: list[tuple[float, str]] = []

    @override
    def set_progress(
        self, record: ScanRecord, *, fraction: float, step: str
    ) -> None:
        self.ticks.append((fraction, step))
        super().set_progress(record, fraction=fraction, step=step)


def test_progress_moves_through_named_stages(
    tmp_path: Path, pdf_bytes: bytes
) -> None:
    """The step text is the audit's own stage names, not invented copy."""
    store = _RecordingStore(tmp_path / "storage")
    record = store.create(
        owner_user_id=OWNER, pdf_bytes=pdf_bytes, original_filename="a.pdf",
    )

    run_scan_in_thread(
        store=store, record=record, api_key=None, ai_model=None,
    )

    assert len(store.ticks) > 3, "a bar with three ticks is barely a bar"
    fractions = [f for f, _ in store.ticks]
    assert fractions == sorted(fractions), (
        "progress went backwards: "
        + ", ".join(f"{s}={f}" for f, s in store.ticks)
    )
    assert any("check" in step.lower() for _, step in store.ticks)


def test_a_cancelled_scan_stops_and_says_so(
    store: ScanStore, pdf_bytes: bytes
) -> None:
    """Cancel is honoured at the next tick rather than by killing a thread."""
    record = store.create(
        owner_user_id=OWNER, pdf_bytes=pdf_bytes, original_filename="a.pdf",
    )
    store.request_cancel(OWNER, record.scan_id)

    run_scan_in_thread(
        store=store, record=record, api_key=None, ai_model=None,
    )

    stored = store.get(OWNER, record.scan_id)
    assert stored is not None
    assert stored.status == "audit_failed"
    assert stored.error_reason == CANCELLED_REASON


def test_a_cancelled_scan_writes_no_result(
    store: ScanStore, pdf_bytes: bytes
) -> None:
    """Half an audit is not a report; the results page must not show one."""
    record = store.create(
        owner_user_id=OWNER, pdf_bytes=pdf_bytes, original_filename="a.pdf",
    )
    store.request_cancel(OWNER, record.scan_id)

    run_scan_in_thread(
        store=store, record=record, api_key=None, ai_model=None,
    )

    stored = store.get(OWNER, record.scan_id)
    assert stored is not None
    assert stored.result == {}


def test_a_broken_pdf_is_recorded_rather_than_raised(
    store: ScanStore
) -> None:
    """The worker has no request to fail into — it must persist and stop."""
    record = store.create(
        owner_user_id=OWNER,
        pdf_bytes=b"%PDF-1.7\nnot really a pdf",
        original_filename="broken.pdf",
    )

    run_scan_in_thread(
        store=store, record=record, api_key=None, ai_model=None,
    )

    stored = store.get(OWNER, record.scan_id)
    assert stored is not None
    assert stored.status == "audit_failed"
    assert stored.error_reason
    assert stored.error_reason != CANCELLED_REASON
