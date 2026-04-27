"""Tests for the async :class:`PdfRunner` wrapper.

Mocks the synchronous ``run_audit`` pipeline so these tests exercise
:class:`PdfRunner` orchestration logic in isolation:

* magic-byte and size validation;
* dedup-or-create flow against a mocked :class:`Database`;
* status transitions around a mocked audit;
* exception paths (``GhostscriptMissing``, ``CorruptPdf``, generic);
* pre-allocated ObjectId pattern keeps storage paths and Mongo ``_id`` aligned;
* ``CheckResult`` → ``Violation`` mapping routes by impact.

A real :class:`PdfStorage` on a tmp_path is used because it's just
filesystem operations — cheaper than mocking it and verifies the
allocate / write_pdf_bytes contract too.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from bson import ObjectId

# ``auto_a11y.core`` must be imported before ``auto_a11y.testing`` because
# ``core`` triggers a chain (``website_manager`` → ``testing_job`` →
# ``auto_a11y.testing``) that re-enters this package mid-init when the
# ``testing`` package is loaded first. Loading ``core`` up front lets the
# chain fully resolve before we touch the ``testing`` namespace. The
# ``import as _``/``del`` pattern keeps strict type-checkers from flagging
# the otherwise-unused import.
import auto_a11y.core as _core_preload
del _core_preload
from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus
from auto_a11y.models.test_result import (
    ImpactLevel,
    TargetType,
)
# Alias to dodge pytest's ``Test*`` class-collection heuristic, which
# trips on the ``TestResult`` dataclass and emits a noisy
# ``PytestCollectionWarning``. The aliased name is used everywhere.
from auto_a11y.models.test_result import TestResult as _TestResult
from auto_a11y.pdf.errors import (
    CannotAuditFetchFailedDocument,
    CorruptPdf,
    GhostscriptMissing,
    NotAPdf,
    PdfDocumentNotFound,
    PdfTooLarge,
)
from auto_a11y.pdf.storage import PdfStorage
# ``testing.pdf_runner`` first: it transitively imports
# ``auto_a11y.pdf.audit.pipeline`` which loads
# ``auto_a11y.pdf.audit.checks`` which expects ``auto_a11y.pdf.models``
# to be fully initialised. Importing ``pdf_runner`` before ``pdf.models``
# lets the audit chain finish first, mirroring
# :file:`tests/pdf/test_pipeline.py`.
from auto_a11y.testing.pdf_runner import PdfRunner
from auto_a11y.pdf.models import AuditResult, CheckResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db() -> MagicMock:
    """Bare :class:`MagicMock` standing in for :class:`Database`."""
    db = MagicMock()
    return db


@pytest.fixture
def storage(tmp_path: Path) -> PdfStorage:
    """Real :class:`PdfStorage` rooted on the test's tmp_path."""
    return PdfStorage(base_dir=tmp_path)


@pytest_asyncio.fixture
async def runner(
    mock_db: MagicMock, storage: PdfStorage
) -> AsyncIterator[PdfRunner]:
    r = PdfRunner(mock_db, storage, max_parallel=1)
    try:
        yield r
    finally:
        r.shutdown()


def _make_doc(
    *,
    sha256: str = "a" * 64,
    website_id: str = "w1",
    project_id: str = "p1",
    status: PdfDocumentStatus = PdfDocumentStatus.PENDING,
    mongo_id: ObjectId | None = None,
    storage_relpath: str | None = None,
    images_relpath: str | None = None,
) -> PdfDocument:
    """Build a :class:`PdfDocument` with sensible defaults for testing."""
    oid = mongo_id or ObjectId()
    sr = storage_relpath or f"{website_id}/{oid}/pdf.pdf"
    ir = images_relpath or f"{website_id}/{oid}/images/"
    doc = PdfDocument(
        website_id=website_id,
        project_id=project_id,
        source_url=None,
        source_type="manual_url",
        discovered_from_page_id=None,
        discovered_from_user_id=None,
        sha256=sha256,
        file_size_bytes=10,
        storage_relpath=sr,
        images_relpath=ir,
        original_filename="doc.pdf",
        pdf_version=None,
        page_count=None,
        declared_lang=None,
        detected_lang=None,
        lang_confidence=None,
        status=status,
        error_reason=None,
        last_audit_result_id=None,
        discovered_at=datetime.now(),
        last_audited_at=None,
    )
    doc.mongo_id = oid
    return doc


def _make_audit_result(
    *,
    check_results: list[CheckResult] | None = None,
    pdf_version: str | None = "1.7",
    page_count: int = 3,
    declared_lang: str | None = "en-US",
    detected_lang: str | None = None,
) -> AuditResult:
    return AuditResult.from_checks(
        pdf_path=Path("/fake/path.pdf"),
        pdf_version=pdf_version,
        page_count=page_count,
        declared_lang=declared_lang,
        detected_lang=detected_lang,
        check_results=check_results or [],
        ai_analysis=None,
    )


# A minimal valid PDF byte-stream — just needs the %PDF- header for the
# magic-byte check; the audit itself is mocked so the bytes' validity
# beyond that is irrelevant.
_TINY_PDF_BYTES = b"%PDF-1.4\n%fake\n%%EOF\n"


# ---------------------------------------------------------------------------
# create_or_find_pdf_document
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_or_find_rejects_non_pdf_bytes(runner: PdfRunner) -> None:
    """Bytes lacking the %PDF- header raise :class:`NotAPdf`."""
    with pytest.raises(NotAPdf):
        await runner.create_or_find_pdf_document(
            b"definitely not a pdf",
            website_id="w1",
            project_id="p1",
            source_type="manual_url",
            discovered_from_page_id=None,
            discovered_from_user_id=None,
            original_filename="x.pdf",
            source_url=None,
        )


@pytest.mark.asyncio
async def test_create_or_find_rejects_oversize(
    mock_db: MagicMock, storage: PdfStorage
) -> None:
    """Bytes larger than ``max_size_mb`` raise :class:`PdfTooLarge`."""
    # 1 MB cap for this test.
    r = PdfRunner(mock_db, storage, max_parallel=1, max_size_mb=1)
    try:
        big = b"%PDF-1.4\n" + b"x" * (2 * 1024 * 1024)
        with pytest.raises(PdfTooLarge):
            await r.create_or_find_pdf_document(
                big,
                website_id="w1",
                project_id="p1",
                source_type="manual_url",
                discovered_from_page_id=None,
                discovered_from_user_id=None,
                original_filename="big.pdf",
                source_url=None,
            )
    finally:
        r.shutdown()


@pytest.mark.asyncio
async def test_create_or_find_returns_existing_on_dedup(
    runner: PdfRunner, mock_db: MagicMock
) -> None:
    """A SHA-match returns the existing doc and skips storage/insert."""
    existing = _make_doc()
    mock_db.find_pdf_document_by_sha256.return_value = existing

    result = await runner.create_or_find_pdf_document(
        _TINY_PDF_BYTES,
        website_id="w1",
        project_id="p1",
        source_type="manual_url",
        discovered_from_page_id=None,
        discovered_from_user_id=None,
        original_filename="x.pdf",
        source_url=None,
    )
    assert result is existing
    mock_db.create_pdf_document.assert_not_called()


@pytest.mark.asyncio
async def test_create_or_find_inserts_on_miss(
    runner: PdfRunner,
    mock_db: MagicMock,
    storage: PdfStorage,
    tmp_path: Path,
) -> None:
    """A SHA-miss writes bytes to storage and inserts the doc.

    Verifies the pre-allocated ObjectId pattern: the storage path
    embeds the ObjectId we hand to ``create_pdf_document`` via
    ``doc.mongo_id``, so the on-disk layout and Mongo ``_id`` agree
    from the start.
    """
    mock_db.find_pdf_document_by_sha256.return_value = None
    mock_db.create_pdf_document.return_value = "ignored-id"

    doc = await runner.create_or_find_pdf_document(
        _TINY_PDF_BYTES,
        website_id="w1",
        project_id="p1",
        source_type="manual_url",
        discovered_from_page_id=None,
        discovered_from_user_id=None,
        original_filename="x.pdf",
        source_url=None,
    )
    # The storage path embeds the doc's ObjectId.
    assert doc.mongo_id is not None
    expected = tmp_path / "w1" / str(doc.mongo_id) / "pdf.pdf"
    assert expected.is_file()
    assert expected.read_bytes() == _TINY_PDF_BYTES
    # The doc was actually inserted.
    mock_db.create_pdf_document.assert_called_once()
    inserted = mock_db.create_pdf_document.call_args.args[0]
    assert isinstance(inserted, PdfDocument)
    assert inserted.mongo_id == doc.mongo_id
    assert inserted.status == PdfDocumentStatus.PENDING
    assert str(doc.mongo_id) in inserted.storage_relpath


# ---------------------------------------------------------------------------
# audit_pdf_document
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_unknown_pdf_raises_not_found(
    runner: PdfRunner, mock_db: MagicMock
) -> None:
    """An unknown id raises :class:`PdfDocumentNotFound`."""
    mock_db.get_pdf_document.return_value = None
    with pytest.raises(PdfDocumentNotFound):
        await runner.audit_pdf_document("does-not-exist")
    mock_db.update_pdf_document.assert_not_called()


@pytest.mark.asyncio
async def test_audit_fetch_failed_raises(
    runner: PdfRunner, mock_db: MagicMock
) -> None:
    """A ``FETCH_FAILED`` document raises and writes nothing."""
    doc = _make_doc(status=PdfDocumentStatus.FETCH_FAILED)
    mock_db.get_pdf_document.return_value = doc
    with pytest.raises(CannotAuditFetchFailedDocument):
        await runner.audit_pdf_document(str(doc.mongo_id))
    mock_db.update_pdf_document.assert_not_called()
    mock_db.create_test_result.assert_not_called()


@pytest.mark.asyncio
async def test_audit_happy_path_transitions_and_persists(
    runner: PdfRunner,
    mock_db: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: PENDING → AUDITING → AUDITED, TestResult persisted."""
    doc = _make_doc(status=PdfDocumentStatus.PENDING)
    mock_db.get_pdf_document.return_value = doc
    mock_db.create_test_result.return_value = "tr-id-1"

    # ``MagicMock.call_args_list`` captures argument *references*. Since
    # ``audit_pdf_document`` mutates ``doc`` in place, snapshotting the
    # status at each call is the only way to verify the transition.
    observed: list[PdfDocumentStatus] = []

    def _record(d: PdfDocument) -> bool:
        observed.append(d.status)
        return True

    mock_db.update_pdf_document.side_effect = _record

    audit = _make_audit_result(
        check_results=[
            CheckResult(
                name="Document title set",
                standard="PDF/UA, WCAG 2.4.2",
                result="FAIL",
                details="No title",
            ),
            CheckResult(
                name="No suspect tags",
                standard="Matterhorn 09-004",
                result="WARN",
                details="Suspect tag found",
            ),
        ],
        pdf_version="1.6",
        page_count=5,
        declared_lang="en-CA",
    )

    def _stub(*args: Any, **kwargs: Any) -> AuditResult:
        return audit

    monkeypatch.setattr("auto_a11y.testing.pdf_runner.run_audit", _stub)

    result = await runner.audit_pdf_document(str(doc.mongo_id))

    # Two updates: AUDITING transition + AUDITED post-audit.
    assert observed == [PdfDocumentStatus.AUDITING, PdfDocumentStatus.AUDITED]
    # Final document state reflects the AuditResult metadata.
    assert doc.pdf_version == "1.6"
    assert doc.page_count == 5
    assert doc.declared_lang == "en-CA"
    assert doc.last_audit_result_id == "tr-id-1"
    assert doc.last_audited_at is not None
    # TestResult was persisted with the right target.
    mock_db.create_test_result.assert_called_once()
    persisted: _TestResult = mock_db.create_test_result.call_args.args[0]
    assert persisted.target_type is TargetType.PDF_DOCUMENT
    assert persisted.target_id == str(doc.mongo_id)
    assert persisted.page_id is None
    # Returned TestResult is identical to the persisted one.
    assert result is persisted


@pytest.mark.asyncio
async def test_audit_routes_violations_by_impact(
    runner: PdfRunner,
    mock_db: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FAIL → violations, WARN → warnings, INFO → info on the TestResult."""
    doc = _make_doc()
    mock_db.get_pdf_document.return_value = doc
    mock_db.create_test_result.return_value = "tr-id"

    audit = _make_audit_result(
        check_results=[
            # FAIL → violations[]
            CheckResult(
                name="PDF is tagged",
                standard="PDF/UA, WCAG 1.3.1",
                result="FAIL",
                details="Not tagged",
            ),
            # WARN → warnings[]
            CheckResult(
                name="Bookmarks present",
                standard="PDF/UA, WCAG 2.4.5",
                result="WARN",
                details="No bookmarks",
            ),
            # INFO → info[]
            CheckResult(
                name="Text contrast (WCAG AA)",
                standard="WCAG 1.4.3",
                result="INFO",
                details="No color data",
            ),
            # PASS → discarded by to_violation
            CheckResult(
                name="Document title set",
                standard="PDF/UA, WCAG 2.4.2",
                result="PASS",
                details="OK",
            ),
        ],
    )

    def _stub(*args: Any, **kwargs: Any) -> AuditResult:
        return audit

    monkeypatch.setattr("auto_a11y.testing.pdf_runner.run_audit", _stub)

    result = await runner.audit_pdf_document(str(doc.mongo_id))
    assert len(result.violations) == 1
    assert result.violations[0].impact is ImpactLevel.HIGH
    assert len(result.warnings) == 1
    assert result.warnings[0].impact is ImpactLevel.MEDIUM
    assert len(result.info) == 1
    assert result.info[0].impact is ImpactLevel.LOW
    # Metadata captures the audit's count summary (mirrors
    # ``AuditResult.fail_count``/``warn_count``/``info_count``/``pass_count``).
    assert result.metadata['fail_count'] == 1
    assert result.metadata['warn_count'] == 1
    assert result.metadata['info_count'] == 1
    assert result.metadata['pass_count'] == 1


@pytest.mark.asyncio
async def test_audit_corrupt_pdf_marks_audit_failed(
    runner: PdfRunner,
    mock_db: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """:class:`CorruptPdf` flips the doc to AUDIT_FAILED and re-raises."""
    doc = _make_doc(status=PdfDocumentStatus.PENDING)
    mock_db.get_pdf_document.return_value = doc

    def _boom(*args: Any, **kwargs: Any) -> AuditResult:
        raise CorruptPdf("/fake/path.pdf", "invalid xref")

    monkeypatch.setattr(
        "auto_a11y.testing.pdf_runner.run_audit", _boom
    )

    with pytest.raises(CorruptPdf):
        await runner.audit_pdf_document(str(doc.mongo_id))

    assert doc.status == PdfDocumentStatus.AUDIT_FAILED
    assert doc.error_reason is not None
    assert "Corrupt PDF" in doc.error_reason
    # No TestResult should have been written.
    mock_db.create_test_result.assert_not_called()


@pytest.mark.asyncio
async def test_audit_ghostscript_missing_marks_audit_failed(
    runner: PdfRunner,
    mock_db: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """:class:`GhostscriptMissing` flips the doc to AUDIT_FAILED and re-raises."""
    doc = _make_doc()
    mock_db.get_pdf_document.return_value = doc

    def _boom(*args: Any, **kwargs: Any) -> AuditResult:
        raise GhostscriptMissing(["/usr/bin/gs"])

    monkeypatch.setattr(
        "auto_a11y.testing.pdf_runner.run_audit", _boom
    )

    with pytest.raises(GhostscriptMissing):
        await runner.audit_pdf_document(str(doc.mongo_id))

    assert doc.status == PdfDocumentStatus.AUDIT_FAILED
    assert doc.error_reason == "Ghostscript not installed"
    mock_db.create_test_result.assert_not_called()


@pytest.mark.asyncio
async def test_audit_generic_exception_marks_audit_failed(
    runner: PdfRunner,
    mock_db: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any other exception still flips status and is re-raised."""
    doc = _make_doc()
    mock_db.get_pdf_document.return_value = doc

    def _boom(*args: Any, **kwargs: Any) -> AuditResult:
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "auto_a11y.testing.pdf_runner.run_audit", _boom
    )

    with pytest.raises(RuntimeError):
        await runner.audit_pdf_document(str(doc.mongo_id))

    assert doc.status == PdfDocumentStatus.AUDIT_FAILED
    assert doc.error_reason is not None
    assert "RuntimeError: boom" in doc.error_reason


@pytest.mark.asyncio
async def test_audit_passes_correct_paths_to_run_audit(
    runner: PdfRunner,
    mock_db: MagicMock,
    storage: PdfStorage,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify ``run_audit`` is called with the storage paths from the doc."""
    oid = ObjectId()
    doc = _make_doc(mongo_id=oid)
    mock_db.get_pdf_document.return_value = doc
    mock_db.create_test_result.return_value = "tr"

    captured: dict[str, Any] = {}

    def _capture(pdf_path: Path, **kwargs: Any) -> AuditResult:
        captured['pdf_path'] = pdf_path
        captured['images_out_dir'] = kwargs.get('images_out_dir')
        captured['gs_path_override'] = kwargs.get('gs_path_override')
        return _make_audit_result()

    monkeypatch.setattr(
        "auto_a11y.testing.pdf_runner.run_audit", _capture
    )

    await runner.audit_pdf_document(str(oid))

    assert captured['pdf_path'] == tmp_path / "w1" / str(oid) / "pdf.pdf"
    assert captured['images_out_dir'] == tmp_path / "w1" / str(oid) / "images"
    # gs override defaults to None.
    assert captured['gs_path_override'] is None


@pytest.mark.asyncio
async def test_audit_propagates_ghostscript_override(
    mock_db: MagicMock,
    storage: PdfStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A constructor-supplied gs override reaches ``run_audit``."""
    r = PdfRunner(
        mock_db, storage, max_parallel=1, ghostscript_path_override="/opt/gs/bin/gs"
    )
    try:
        doc = _make_doc()
        mock_db.get_pdf_document.return_value = doc
        mock_db.create_test_result.return_value = "tr"

        captured: dict[str, Any] = {}

        def _capture(pdf_path: Path, **kwargs: Any) -> AuditResult:
            captured['gs_path_override'] = kwargs.get('gs_path_override')
            return _make_audit_result()

        monkeypatch.setattr(
            "auto_a11y.testing.pdf_runner.run_audit", _capture
        )

        await r.audit_pdf_document(str(doc.mongo_id))
        assert captured['gs_path_override'] == "/opt/gs/bin/gs"
    finally:
        r.shutdown()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_shutdown_is_idempotent(
    mock_db: MagicMock, storage: PdfStorage
) -> None:
    """Calling :meth:`shutdown` more than once is safe."""
    r = PdfRunner(mock_db, storage, max_parallel=1)
    r.shutdown()
    r.shutdown()  # no exception
