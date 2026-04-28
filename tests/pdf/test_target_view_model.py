"""Tests for TestTargetView and its factories."""
from __future__ import annotations

import dataclasses
from datetime import datetime

import pytest
from bson import ObjectId

from auto_a11y.models.page import Page, PageStatus
from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus
from auto_a11y.web.view_models.target import (
    Crumb,
    TestTargetView,
    target_view_from_page,
    target_view_from_pdf,
)


def _make_pdf() -> PdfDocument:
    """Construct a valid PdfDocument for tests."""
    return PdfDocument(
        website_id="w1",
        project_id="p1",
        source_url="https://example.com/doc.pdf",
        source_type="manual_url",
        discovered_from_page_id=None,
        discovered_from_user_id="u1",
        sha256="a" * 64,
        file_size_bytes=12345,
        storage_relpath="w1/abc/pdf.pdf",
        images_relpath="w1/abc/images/",
        original_filename="report.pdf",
        pdf_version="1.7",
        page_count=42,
        declared_lang="en-US",
        detected_lang=None,
        lang_confidence=None,
        status=PdfDocumentStatus.AUDITED,
        error_reason=None,
        last_audit_result_id="result-1",
        discovered_at=datetime(2026, 1, 1),
        last_audited_at=datetime(2026, 4, 1),
    )


def _make_page() -> Page:
    """Construct a valid Page for tests."""
    page = Page(
        website_id="w1",
        url="https://example.com/about",
        title="About Us",
        last_tested=datetime(2026, 4, 2),
        status=PageStatus.TESTED,
    )
    page.mongo_id = ObjectId()
    return page


def test_target_view_from_pdf_carries_pdf_specific_fields() -> None:
    pdf = _make_pdf()
    pdf.mongo_id = ObjectId()

    view = target_view_from_pdf(pdf, inline_viewer_url="/pdfs/abc/file")

    assert view.kind == "pdf_document"
    assert view.id == pdf.id
    assert view.title == "report.pdf"
    assert view.source_url == "https://example.com/doc.pdf"
    assert view.page_count == 42
    assert view.file_size_bytes == 12345
    assert view.inline_viewer_url == "/pdfs/abc/file"
    assert view.last_audited_at == datetime(2026, 4, 1)
    assert view.latest_result_id == "result-1"


def test_target_view_from_pdf_with_no_id_raises() -> None:
    pdf = _make_pdf()
    pdf.mongo_id = None
    with pytest.raises(ValueError, match="without an id"):
        target_view_from_pdf(pdf)


def test_target_view_from_pdf_falls_back_to_source_url_when_filename_missing() -> None:
    pdf = _make_pdf()
    pdf.mongo_id = ObjectId()
    pdf.original_filename = None

    view = target_view_from_pdf(pdf)

    assert view.title == "https://example.com/doc.pdf"
    assert view.inline_viewer_url is None


def test_target_view_from_page_carries_page_fields() -> None:
    page = _make_page()

    view = target_view_from_page(page)

    assert view.kind == "page"
    assert view.id == page.id
    assert view.title == "About Us"
    assert view.source_url == "https://example.com/about"
    assert view.last_audited_at == datetime(2026, 4, 2)
    # PDF-only fields stay None for page targets.
    assert view.page_count is None
    assert view.file_size_bytes is None
    assert view.inline_viewer_url is None
    assert view.latest_result_id is None


def test_target_view_from_page_falls_back_to_url_when_title_missing() -> None:
    page = _make_page()
    page.title = None

    view = target_view_from_page(page)

    assert view.title == "https://example.com/about"


def test_target_view_from_page_with_no_id_raises() -> None:
    page = _make_page()
    page.mongo_id = None
    with pytest.raises(ValueError, match="without an id"):
        target_view_from_page(page)


def test_target_view_from_page_accepts_latest_result_id() -> None:
    page = _make_page()

    view = target_view_from_page(page, latest_result_id="result-page-1")

    assert view.latest_result_id == "result-page-1"


def test_target_view_is_frozen() -> None:
    pdf = _make_pdf()
    pdf.mongo_id = ObjectId()
    view = target_view_from_pdf(pdf)
    with pytest.raises(dataclasses.FrozenInstanceError):
        # use setattr to avoid pyright's static error on frozen-attr assign
        setattr(view, "title", "new")


def test_breadcrumb_passed_through_for_pdf() -> None:
    pdf = _make_pdf()
    pdf.mongo_id = ObjectId()
    crumbs = [
        Crumb(label="Home", url="/"),
        Crumb(label="Project X", url="/projects/p1"),
        Crumb(label="report.pdf", url=None),
    ]
    view = target_view_from_pdf(pdf, breadcrumb=crumbs)
    assert view.breadcrumb == crumbs


def test_breadcrumb_passed_through_for_page() -> None:
    page = _make_page()
    crumbs = [
        Crumb(label="Home", url="/"),
        Crumb(label="About Us", url=None),
    ]
    view = target_view_from_page(page, breadcrumb=crumbs)
    assert view.breadcrumb == crumbs


def test_breadcrumb_defaults_to_empty_list() -> None:
    pdf = _make_pdf()
    pdf.mongo_id = ObjectId()
    view = target_view_from_pdf(pdf)
    assert view.breadcrumb == []


def test_crumb_is_frozen() -> None:
    crumb = Crumb(label="Home", url="/")
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(crumb, "label", "Other")


def test_test_target_view_can_be_constructed_directly() -> None:
    """The dataclass itself is part of the public surface; a hand-built
    instance should be valid (used in templates that pass mocks in tests).
    """
    view = TestTargetView(
        kind="page",
        id="abc",
        title="My Page",
        source_url="https://example.com/",
    )
    assert view.kind == "page"
    assert view.breadcrumb == []
    assert view.last_audited_at is None
    assert view.latest_result_id is None
