"""Tests for typed PDF exceptions."""
from __future__ import annotations

from auto_a11y.pdf.errors import (
    CannotAuditFetchFailedDocument,
    CorruptPdf,
    FetchFailed,
    GhostscriptMissing,
    NotAPdf,
    PdfDocumentNotFound,
    PdfError,
    PdfTooLarge,
)


def test_all_errors_inherit_pdf_error() -> None:
    assert issubclass(GhostscriptMissing, PdfError)
    assert issubclass(CorruptPdf, PdfError)
    assert issubclass(FetchFailed, PdfError)
    assert issubclass(PdfTooLarge, PdfError)
    assert issubclass(NotAPdf, PdfError)
    assert issubclass(PdfDocumentNotFound, PdfError)
    assert issubclass(CannotAuditFetchFailedDocument, PdfError)


def test_ghostscript_missing_carries_searched_paths() -> None:
    err = GhostscriptMissing(searched=['gs', 'gswin64c'])
    assert 'gs' in str(err)
    assert err.searched == ['gs', 'gswin64c']


def test_fetch_failed_carries_url_and_reason() -> None:
    err = FetchFailed(url='https://example.com/doc.pdf', reason='HTTP 403')
    assert 'https://example.com/doc.pdf' in str(err)
    assert 'HTTP 403' in str(err)


def test_pdf_too_large_carries_size_and_limit() -> None:
    err = PdfTooLarge(size_bytes=200_000_000, limit_bytes=100_000_000)
    assert err.size_bytes == 200_000_000
    assert err.limit_bytes == 100_000_000


def test_corrupt_pdf_carries_path_and_reason() -> None:
    err = CorruptPdf(path='/tmp/x.pdf', reason='EOF inside xref table')
    assert '/tmp/x.pdf' in str(err)
    assert 'EOF' in str(err)


def test_pdf_document_not_found_carries_id() -> None:
    err = PdfDocumentNotFound(pdf_document_id='deadbeef')
    assert 'deadbeef' in str(err)
    assert err.pdf_document_id == 'deadbeef'
