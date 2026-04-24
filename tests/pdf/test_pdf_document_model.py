"""Tests for the PdfDocument model."""
from __future__ import annotations

from datetime import datetime

import pytest
from bson import ObjectId

from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus


def test_pdf_document_round_trips_via_dict() -> None:
    now = datetime(2026, 4, 24, 12, 0, 0)
    doc = PdfDocument(
        website_id='w1',
        project_id='p1',
        source_url='https://example.com/doc.pdf',
        source_type='manual_url',
        discovered_from_page_id=None,
        discovered_from_user_id='u1',
        sha256='a' * 64,
        file_size_bytes=1024,
        storage_relpath='w1/d1/pdf.pdf',
        images_relpath='w1/d1/images/',
        original_filename='doc.pdf',
        pdf_version='1.7',
        page_count=10,
        declared_lang='en',
        detected_lang='en',
        lang_confidence=0.95,
        status=PdfDocumentStatus.AUDITED,
        error_reason=None,
        last_audit_result_id='r1',
        discovered_at=now,
        last_audited_at=now,
    )
    as_dict = doc.to_dict()
    assert as_dict['sha256'] == 'a' * 64
    assert as_dict['status'] == 'audited'
    round_tripped = PdfDocument.from_dict(as_dict)
    assert round_tripped.sha256 == doc.sha256
    assert round_tripped.status == PdfDocumentStatus.AUDITED


def test_pdf_document_minimal_fields() -> None:
    now = datetime.now()
    doc = PdfDocument(
        website_id='w1',
        project_id='p1',
        source_url=None,
        source_type='uploaded',
        discovered_from_page_id=None,
        discovered_from_user_id='u1',
        sha256='b' * 64,
        file_size_bytes=1,
        storage_relpath='w1/d1/pdf.pdf',
        images_relpath='w1/d1/images/',
        original_filename='x.pdf',
        pdf_version=None,
        page_count=None,
        declared_lang=None,
        detected_lang=None,
        lang_confidence=None,
        status=PdfDocumentStatus.PENDING,
        error_reason=None,
        last_audit_result_id=None,
        discovered_at=now,
        last_audited_at=None,
    )
    assert doc.id is None


def test_pdf_document_id_property_from_objectid() -> None:
    doc = PdfDocument(
        website_id='w1',
        project_id='p1',
        source_url=None,
        source_type='uploaded',
        discovered_from_page_id=None,
        discovered_from_user_id='u1',
        sha256='c' * 64,
        file_size_bytes=1,
        storage_relpath='w1/d1/pdf.pdf',
        images_relpath='w1/d1/images/',
        original_filename='x.pdf',
        pdf_version=None,
        page_count=None,
        declared_lang=None,
        detected_lang=None,
        lang_confidence=None,
        status=PdfDocumentStatus.PENDING,
        error_reason=None,
        last_audit_result_id=None,
        discovered_at=datetime.now(),
        last_audited_at=None,
        _id=ObjectId(),
    )
    assert isinstance(doc.id, str)
    assert len(doc.id) == 24


@pytest.mark.parametrize("status", list(PdfDocumentStatus))
def test_status_enum_round_trips(status: PdfDocumentStatus) -> None:
    assert PdfDocumentStatus(status.value) is status
