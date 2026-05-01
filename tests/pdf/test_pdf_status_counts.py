"""Tests for the project/website detail PDF status counter."""
from __future__ import annotations

from datetime import datetime

from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus
from auto_a11y.web.routes.projects import summarise_pdf_status


def _doc(status: PdfDocumentStatus, sha_seed: str) -> PdfDocument:
    return PdfDocument(
        website_id="w1",
        project_id="p1",
        source_url=None,
        source_type="uploaded",
        discovered_from_page_id=None,
        discovered_from_user_id="u1",
        sha256=(sha_seed * 64)[:64],
        file_size_bytes=1024,
        storage_relpath=f"w1/{sha_seed}/pdf.pdf",
        images_relpath=f"w1/{sha_seed}/images/",
        original_filename="x.pdf",
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


def test_summarise_empty_list_returns_zeros() -> None:
    counts = summarise_pdf_status([])
    assert counts["total"] == 0
    for status in PdfDocumentStatus:
        assert counts[status.value] == 0


def test_summarise_buckets_each_status() -> None:
    pdfs = [
        _doc(PdfDocumentStatus.AUDITED, "a"),
        _doc(PdfDocumentStatus.AUDITED, "b"),
        _doc(PdfDocumentStatus.AUDITING, "c"),
        _doc(PdfDocumentStatus.AUDIT_FAILED, "d"),
        _doc(PdfDocumentStatus.PENDING, "e"),
    ]
    counts = summarise_pdf_status(pdfs)
    assert counts["total"] == 5
    assert counts["audited"] == 2
    assert counts["auditing"] == 1
    assert counts["audit_failed"] == 1
    assert counts["pending"] == 1
    assert counts["fetching"] == 0
    assert counts["fetch_failed"] == 0


def test_summarise_keys_cover_every_status_enum_value() -> None:
    """Every PdfDocumentStatus value gets a key (zero or otherwise)."""
    counts = summarise_pdf_status([_doc(PdfDocumentStatus.AUDITED, "a")])
    for status in PdfDocumentStatus:
        assert status.value in counts
