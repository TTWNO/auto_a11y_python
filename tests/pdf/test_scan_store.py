"""Tests for the standalone scan store.

Covers the two properties the store exists to guarantee: a scan round-trips
through disk with its audit intact, and one user's scan is unreachable from
another user's id — including via a crafted scan id.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pikepdf
import pytest

from auto_a11y.pdf.audit.pipeline import run_audit
from auto_a11y.pdf.models import AuditResult, CheckResult
from auto_a11y.pdf.scan_store import ScanStore, result_payload

OWNER = "507f1f77bcf86cd799439011"
OTHER = "507f1f77bcf86cd799439012"


@pytest.fixture
def pdf_bytes(tmp_path: Path) -> bytes:
    """A minimal one-page PDF with a title."""
    path = tmp_path / "source.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    with pdf.open_metadata() as meta:
        meta["dc:title"] = "Scan store fixture"
    pdf.save(path)
    return path.read_bytes()


@pytest.fixture
def store(tmp_path: Path) -> ScanStore:
    return ScanStore(base_dir=tmp_path / "storage")


def test_create_writes_bytes_and_opens_an_auditing_manifest(
    store: ScanStore, pdf_bytes: bytes
) -> None:
    record = store.create(
        owner_user_id=OWNER, pdf_bytes=pdf_bytes, original_filename="doc.pdf"
    )
    assert record.status == "auditing"
    assert record.file_size_bytes == len(pdf_bytes)
    assert store.pdf_path(OWNER, record.scan_id).read_bytes() == pdf_bytes
    assert store.images_dir(OWNER, record.scan_id).is_dir()


def test_finish_round_trips_the_audit_through_disk(
    store: ScanStore, pdf_bytes: bytes
) -> None:
    record = store.create(
        owner_user_id=OWNER, pdf_bytes=pdf_bytes, original_filename="doc.pdf"
    )
    audit = run_audit(
        store.pdf_path(OWNER, record.scan_id),
        images_out_dir=store.images_dir(OWNER, record.scan_id),
    )
    store.finish(record, audit)

    reloaded = store.get(OWNER, record.scan_id)
    assert reloaded is not None
    assert reloaded.status == "audited"
    assert reloaded.page_count == 1
    assert reloaded.count("pass_count") == audit.pass_count
    assert reloaded.count("fail_count") == audit.fail_count
    # NA stays distinct from PASS across the disk round-trip — conflating
    # them is what made a scanned PDF report 88 passes out of 105.
    assert reloaded.count("na_count") == audit.na_count
    assert len(reloaded.result["check_results"]) == len(audit.check_results)


def test_fail_records_the_reason_and_clears_the_result(
    store: ScanStore, pdf_bytes: bytes
) -> None:
    record = store.create(
        owner_user_id=OWNER, pdf_bytes=pdf_bytes, original_filename="doc.pdf"
    )
    store.fail(record, "CorruptPdf: could not open")

    reloaded = store.get(OWNER, record.scan_id)
    assert reloaded is not None
    assert reloaded.status == "audit_failed"
    assert reloaded.error_reason == "CorruptPdf: could not open"
    assert reloaded.result == {}


def test_another_user_cannot_read_a_scan(store: ScanStore, pdf_bytes: bytes) -> None:
    record = store.create(
        owner_user_id=OWNER, pdf_bytes=pdf_bytes, original_filename="doc.pdf"
    )
    assert store.get(OTHER, record.scan_id) is None
    assert store.list_for_user(OTHER) == []
    assert store.delete(OTHER, record.scan_id) is False
    # The real owner's copy survived the other user's delete attempt.
    assert store.get(OWNER, record.scan_id) is not None


@pytest.mark.parametrize(
    "scan_id",
    [
        "../../../etc/passwd",
        "..",
        "not-hex",
        "",
        "6b9ae14783c441859d77ec17afe82f5",   # 31 chars
        "6b9ae14783c441859d77ec17afe82f555",  # 33 chars
        "6B9AE14783C441859D77EC17AFE82F55",   # uppercase
    ],
)
def test_malformed_scan_ids_are_refused(store: ScanStore, scan_id: str) -> None:
    """A crafted id must be rejected before it can become a path."""
    assert store.get(OWNER, scan_id) is None
    assert store.delete(OWNER, scan_id) is False


def test_delete_removes_the_scan(store: ScanStore, pdf_bytes: bytes) -> None:
    record = store.create(
        owner_user_id=OWNER, pdf_bytes=pdf_bytes, original_filename="doc.pdf"
    )
    assert store.delete(OWNER, record.scan_id) is True
    assert store.get(OWNER, record.scan_id) is None
    assert not store.pdf_path(OWNER, record.scan_id).exists()


def _restamp(store: ScanStore, scan_id: str, when: datetime) -> None:
    """Rewrite a manifest's timestamp, so ordering is not left to the clock."""
    manifest = store.pdf_path(OWNER, scan_id).parent / "scan.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["scanned_at"] = when.isoformat()
    manifest.write_text(json.dumps(payload), encoding="utf-8")


def test_list_for_user_is_newest_first(store: ScanStore, pdf_bytes: bytes) -> None:
    first = store.create(
        owner_user_id=OWNER, pdf_bytes=pdf_bytes, original_filename="first.pdf"
    )
    second = store.create(
        owner_user_id=OWNER, pdf_bytes=pdf_bytes, original_filename="second.pdf"
    )
    # Both manifests land inside the same clock tick on a fast disk, so pin
    # the timestamps rather than relying on creation order.
    _restamp(store, first.scan_id, datetime(2026, 1, 1, 9, 0))
    _restamp(store, second.scan_id, datetime(2026, 1, 2, 9, 0))

    names = [r.original_filename for r in store.list_for_user(OWNER)]
    assert names == ["second.pdf", "first.pdf"]


def test_result_payload_keeps_every_verdict() -> None:
    """PASS and NA must survive into the payload, not just the faults.

    A report that lists only failures cannot say what was checked.
    """
    checks = [
        CheckResult(name="a", standard="PDF/UA", result="FAIL", details="d"),
        CheckResult(name="b", standard="PDF/UA", result="PASS", details="d"),
        CheckResult(name="c", standard="PDF/UA", result="NA", details="d"),
    ]
    audit = AuditResult.from_checks(
        pdf_path=Path("x.pdf"),
        pdf_version="1.7",
        page_count=1,
        declared_lang="en",
        detected_lang="en",
        check_results=checks,
        ai_analysis=None,
    )
    payload = result_payload(audit)

    assert len(payload["check_results"]) == 3
    assert payload["pass_count"] == 1
    assert payload["fail_count"] == 1
    assert payload["na_count"] == 1


# ---------------------------------------------------------------------------
# Progress and cancellation
# ---------------------------------------------------------------------------

# Both live on disk rather than in memory because the audit runs on a
# worker thread while the browser polls through whichever request thread
# it lands on — and a desktop build may serve those from separate
# processes.


def test_progress_is_zero_before_the_audit_ticks(
    store: ScanStore, pdf_bytes: bytes
) -> None:
    """A scan that has not reported yet reads as zero, not as an error."""
    record = store.create(owner_user_id=OWNER, pdf_bytes=pdf_bytes,
                          original_filename="a.pdf")

    assert store.get_progress(OWNER, record.scan_id) == (0.0, "")


def test_progress_round_trips_through_disk(
    store: ScanStore, pdf_bytes: bytes
) -> None:
    record = store.create(owner_user_id=OWNER, pdf_bytes=pdf_bytes,
                          original_filename="a.pdf")

    store.set_progress(record, fraction=0.42, step="Extracting colors")

    assert store.get_progress(OWNER, record.scan_id) == (
        0.42, "Extracting colors",
    )


def test_progress_is_clamped_to_the_bar(
    store: ScanStore, pdf_bytes: bytes
) -> None:
    """A stage that overshoots must not render a bar past its own end."""
    record = store.create(owner_user_id=OWNER, pdf_bytes=pdf_bytes,
                          original_filename="a.pdf")

    store.set_progress(record, fraction=1.4, step="over")
    assert store.get_progress(OWNER, record.scan_id)[0] == 1.0

    store.set_progress(record, fraction=-0.2, step="under")
    assert store.get_progress(OWNER, record.scan_id)[0] == 0.0


def test_a_corrupt_progress_file_reads_as_no_progress(
    store: ScanStore, pdf_bytes: bytes
) -> None:
    """A poll that catches a torn write shows the bar, not a stack trace."""
    record = store.create(owner_user_id=OWNER, pdf_bytes=pdf_bytes,
                          original_filename="a.pdf")
    store.set_progress(record, fraction=0.5, step="half")
    path = store.base_dir / OWNER / record.scan_id / "progress.json"
    path.write_text("{not json", encoding="utf-8")

    assert store.get_progress(OWNER, record.scan_id) == (0.0, "")


def test_progress_of_another_users_scan_is_not_readable(
    store: ScanStore, pdf_bytes: bytes
) -> None:
    record = store.create(owner_user_id=OWNER, pdf_bytes=pdf_bytes,
                          original_filename="a.pdf")
    store.set_progress(record, fraction=0.7, step="secret")

    assert store.get_progress(OTHER, record.scan_id) == (0.0, "")


def test_cancel_is_not_requested_by_default(
    store: ScanStore, pdf_bytes: bytes
) -> None:
    record = store.create(owner_user_id=OWNER, pdf_bytes=pdf_bytes,
                          original_filename="a.pdf")

    assert store.cancel_requested(OWNER, record.scan_id) is False


def test_cancel_is_visible_to_the_worker(
    store: ScanStore, pdf_bytes: bytes
) -> None:
    """The audit thread reads this at each progress tick to know to stop."""
    record = store.create(owner_user_id=OWNER, pdf_bytes=pdf_bytes,
                          original_filename="a.pdf")

    store.request_cancel(OWNER, record.scan_id)

    assert store.cancel_requested(OWNER, record.scan_id) is True


def test_one_users_cancel_does_not_stop_anothers_scan(
    store: ScanStore, pdf_bytes: bytes
) -> None:
    record = store.create(owner_user_id=OWNER, pdf_bytes=pdf_bytes,
                          original_filename="a.pdf")

    store.request_cancel(OTHER, record.scan_id)

    assert store.cancel_requested(OWNER, record.scan_id) is False
