"""View model bundling Page and PdfDocument fields for shared templates.

The detail-view template (test_result.html) needs to render results for
either a Page or a PdfDocument. Rather than branch per attribute access,
both factories produce a TestTargetView that templates can consume
uniformly. Templates branch on `target.kind` only for the few PDF-only
fields (page_count, file_size_bytes, inline_viewer_url).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar, Literal

from auto_a11y.models.page import Page
from auto_a11y.models.pdf_document import PdfDocument


@dataclass(frozen=True)
class Crumb:
    """One breadcrumb segment.

    label: the rendered text (Fluent IDs preferred but raw labels also accepted
           for project/website names that come from user data).
    url: the link target. None for the trailing crumb.
    """

    label: str
    url: str | None


@dataclass(frozen=True)
class TestTargetView:
    """Bundle of Page or PdfDocument fields shared by the detail templates."""

    # Tell pytest not to try to collect this class as a test fixture.
    __test__: ClassVar[bool] = False

    kind: Literal["page", "pdf_document"]
    id: str
    title: str
    source_url: str | None
    breadcrumb: list[Crumb] = field(default_factory=lambda: [])
    last_audited_at: datetime | None = None
    latest_result_id: str | None = None
    # PDF-only fields:
    page_count: int | None = None
    file_size_bytes: int | None = None
    inline_viewer_url: str | None = None


def target_view_from_page(
    page: Page,
    *,
    breadcrumb: list[Crumb] | None = None,
    latest_result_id: str | None = None,
) -> TestTargetView:
    """Build a TestTargetView from a Page.

    The caller supplies ``latest_result_id`` when a TestResult document is
    known; the Page model itself does not store that pointer.
    """
    if page.id is None:
        raise ValueError("Cannot build TestTargetView from a Page without an id")
    return TestTargetView(
        kind="page",
        id=page.id,
        title=page.title or page.url,
        source_url=page.url,
        breadcrumb=breadcrumb or [],
        last_audited_at=page.last_tested,
        latest_result_id=latest_result_id,
        page_count=None,
        file_size_bytes=None,
        inline_viewer_url=None,
    )


def target_view_from_pdf(
    pdf: PdfDocument,
    *,
    breadcrumb: list[Crumb] | None = None,
    inline_viewer_url: str | None = None,
) -> TestTargetView:
    """Build a TestTargetView from a PdfDocument.

    ``inline_viewer_url`` is computed by the caller via ``url_for`` for the
    ``/pdfs/<id>/file`` route; the factory itself has no Flask dependency.
    """
    if pdf.id is None:
        raise ValueError(
            "Cannot build TestTargetView from a PdfDocument without an id"
        )
    # `original_filename` is optional on PdfDocument; fall back to the source URL
    # or the bare id so the template always has a non-empty title.
    title = pdf.original_filename or pdf.source_url or pdf.id
    return TestTargetView(
        kind="pdf_document",
        id=pdf.id,
        title=title,
        source_url=pdf.source_url,
        breadcrumb=breadcrumb or [],
        last_audited_at=pdf.last_audited_at,
        latest_result_id=pdf.last_audit_result_id,
        page_count=pdf.page_count,
        file_size_bytes=pdf.file_size_bytes,
        inline_viewer_url=inline_viewer_url,
    )
