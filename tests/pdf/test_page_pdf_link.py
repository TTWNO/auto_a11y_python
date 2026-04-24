"""Tests for PageStatus.IS_PDF and Page.linked_pdf_document_id."""
from __future__ import annotations

from auto_a11y.models.page import Page, PageStatus


def test_page_linked_pdf_document_id_round_trips() -> None:
    page = Page(
        website_id='w1',
        url='https://example.com/doc',
        status=PageStatus.IS_PDF,
        linked_pdf_document_id='pdf-abc',
    )
    data = page.to_dict()
    assert data['status'] == 'is_pdf'
    assert data['linked_pdf_document_id'] == 'pdf-abc'
    restored = Page.from_dict(data)
    assert restored.status is PageStatus.IS_PDF
    assert restored.linked_pdf_document_id == 'pdf-abc'


def test_page_linked_pdf_document_id_defaults_none_on_old_records() -> None:
    page = Page.from_dict({
        'website_id': 'w1',
        'url': 'https://example.com/',
        'status': 'tested',
    })
    assert page.linked_pdf_document_id is None
