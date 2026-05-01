"""Tests for PdfStorage filesystem operations."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus
from auto_a11y.pdf.storage import PdfStorage


@pytest.fixture
def storage(tmp_path: Path) -> PdfStorage:
    return PdfStorage(base_dir=tmp_path)


def _make_doc(website_id: str = 'w1', doc_id: str = '507f1f77bcf86cd799439011') -> PdfDocument:
    return PdfDocument(
        website_id=website_id, project_id='p1',
        source_url=None, source_type='uploaded',
        discovered_from_page_id=None, discovered_from_user_id='u1',
        sha256='a' * 64, file_size_bytes=1024,
        storage_relpath=f'{website_id}/{doc_id}/pdf.pdf',
        images_relpath=f'{website_id}/{doc_id}/images/',
        original_filename='x.pdf',
        pdf_version=None, page_count=None,
        declared_lang=None, detected_lang=None, lang_confidence=None,
        status=PdfDocumentStatus.PENDING, error_reason=None, last_audit_result_id=None,
        discovered_at=datetime.now(), last_audited_at=None,
    )


def test_allocate_and_write(storage: PdfStorage, tmp_path: Path) -> None:
    slot = storage.allocate_pdf(website_id='w1', pdf_document_id='abc')
    storage.write_pdf_bytes(slot, b'%PDF-1.7\ndata')
    assert (tmp_path / 'w1' / 'abc' / 'pdf.pdf').read_bytes().startswith(b'%PDF-')
    assert (tmp_path / 'w1' / 'abc' / 'images').is_dir()


def test_local_path(storage: PdfStorage, tmp_path: Path) -> None:
    doc = _make_doc()
    expected = tmp_path / doc.storage_relpath
    assert storage.local_path(doc) == expected


def test_delete_removes_whole_dir(storage: PdfStorage, tmp_path: Path) -> None:
    slot = storage.allocate_pdf(website_id='w1', pdf_document_id='xyz')
    storage.write_pdf_bytes(slot, b'%PDF-data')
    doc = _make_doc(doc_id='xyz')
    storage.delete(doc)
    assert not (tmp_path / 'w1' / 'xyz').exists()


def test_delete_website_cascades(storage: PdfStorage, tmp_path: Path) -> None:
    slot1 = storage.allocate_pdf(website_id='w2', pdf_document_id='doc1')
    slot2 = storage.allocate_pdf(website_id='w2', pdf_document_id='doc2')
    storage.write_pdf_bytes(slot1, b'%PDF-a')
    storage.write_pdf_bytes(slot2, b'%PDF-b')
    storage.delete_website('w2')
    assert not (tmp_path / 'w2').exists()


def test_write_is_atomic(storage: PdfStorage, tmp_path: Path) -> None:
    """The final file should only exist after a complete write."""
    slot = storage.allocate_pdf(website_id='w3', pdf_document_id='atomic')
    storage.write_pdf_bytes(slot, b'%PDF-1.7\nabc')
    final = tmp_path / 'w3' / 'atomic' / 'pdf.pdf'
    assert final.exists()
    # No lingering .tmp files
    leftover = list((tmp_path / 'w3' / 'atomic').glob('*.tmp'))
    assert leftover == []
