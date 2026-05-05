"""Aggregate accessibility issue counts across all test sources.

The application has multiple, independent sources of FAIL/WARN counts:

* HTML page tests — stored in the ``test_results`` collection, populated
  by the Playwright/Claude testing pipeline.
* PDF audits — stored in ``pdfmax-report/*_issue_map.json`` filesystem
  caches, populated by the pdfMax accessibility audit.

This module owns composing those sources into single totals for a
website or a project. **Adding a new test source** in the future
(e.g. a video-caption audit) means writing one small contributor
function and adding a call to it in :func:`count_website_issues` /
:func:`count_project_issues`. Every UI surface that uses the
aggregator — project overview, per-website badges, public client view,
JSON API — then reflects the new source automatically.

Without this central composer the previous code path counted PDFs in
the per-website rollup but not in the project-level overview, so the
top "issues" number on the project page disagreed with the badges
shown directly below it (issues-counts branch fix).
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from auto_a11y.models import PageStatus
from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus
from auto_a11y.pdf.issue_map_counts import count_issues
from auto_a11y.pdf.storage import PdfStorage

if TYPE_CHECKING:
    from auto_a11y.core.database import Database


@dataclass(frozen=True)
class IssueCounts:
    """Aggregate FAIL (violations) and WARN (warnings) counts.

    Frozen so callers can sum a list without aliasing concerns;
    ``__add__`` returns a new instance.
    """

    violations: int
    warnings: int

    def __add__(self, other: "IssueCounts") -> "IssueCounts":
        return IssueCounts(
            violations=self.violations + other.violations,
            warnings=self.warnings + other.warnings,
        )


ZERO_ISSUE_COUNTS: IssueCounts = IssueCounts(0, 0)


def count_html_page_issues(
    db: "Database", page_ids: Sequence[str | None]
) -> IssueCounts:
    """Aggregate violations/warnings from the latest test_result per page.

    ``$first`` after a ``$sort: test_date desc`` selects the most recent
    test result per page, matching how individual page detail views
    display counts. ``None`` ids are filtered out so callers can pass a
    raw ``[p.id for p in pages]`` list without pre-filtering.
    """
    valid_ids: list[str] = [pid for pid in page_ids if pid is not None]
    if not valid_ids:
        return ZERO_ISSUE_COUNTS

    pipeline: list[dict[str, Any]] = [
        {"$match": {"page_id": {"$in": valid_ids}}},
        {"$sort": {"test_date": -1}},
        {
            "$group": {
                "_id": "$page_id",
                "violation_count": {"$first": {"$ifNull": ["$violation_count", 0]}},
                "warning_count": {"$first": {"$ifNull": ["$warning_count", 0]}},
            }
        },
    ]

    violations = 0
    warnings = 0
    for result in db.test_results.aggregate(pipeline):
        v = result.get("violation_count", 0)
        w = result.get("warning_count", 0)
        if isinstance(v, int):
            violations += v
        if isinstance(w, int):
            warnings += w
    return IssueCounts(violations=violations, warnings=warnings)


def count_pdf_issues(
    pdfs: Iterable[PdfDocument], storage: PdfStorage
) -> IssueCounts:
    """Aggregate FAIL/WARN counts across AUDITED PDFs.

    Non-AUDITED PDFs and AUDITED PDFs with missing/malformed caches
    contribute zero — :func:`count_issues` already collapses those
    cases to ``(0, 0)`` and logs a WARNING for the inconsistency.
    """
    total = ZERO_ISSUE_COUNTS
    for pdf in pdfs:
        if pdf.status is PdfDocumentStatus.AUDITED:
            tally = count_issues(pdf, storage)
            total = total + IssueCounts(
                violations=tally.violations,
                warnings=tally.warnings,
            )
    return total


def count_website_issues(
    db: "Database",
    storage: PdfStorage,
    website_id: str,
    *,
    tested_page_ids: Sequence[str | None] | None = None,
    pdfs: Iterable[PdfDocument] | None = None,
) -> IssueCounts:
    """Sum issue counts for one website across all sources.

    ``tested_page_ids`` and ``pdfs`` may be passed in by callers that
    have already loaded the data in bulk (e.g. the project overview,
    which fetches every PDF in the project once and partitions by
    website). When omitted, both are fetched on demand.
    """
    if tested_page_ids is None:
        pages = db.get_pages(website_id)
        tested_page_ids = [p.id for p in pages if p.status == PageStatus.TESTED]
    html = count_html_page_issues(db, tested_page_ids)

    if pdfs is None:
        pdfs = db.get_pdf_documents(website_id=website_id, limit=10000)
    pdf = count_pdf_issues(pdfs, storage)

    return html + pdf


def count_project_issues(
    db: "Database",
    storage: PdfStorage,
    project_id: str,
) -> IssueCounts:
    """Sum issue counts for an entire project across all sources.

    Issues a single ``get_pdf_documents(project_id=...)`` call rather
    than one per website, then dispatches each website's slice to the
    per-website aggregator. Future sources should follow the same
    pattern: prefer one project-scoped query, partition by website,
    pass the slice in to keep per-website math consistent.
    """
    websites = db.get_websites(project_id)
    project_pdfs = db.get_pdf_documents(project_id=project_id, limit=10000)
    pdfs_by_website: dict[str, list[PdfDocument]] = {}
    for pdf_doc in project_pdfs:
        pdfs_by_website.setdefault(pdf_doc.website_id, []).append(pdf_doc)

    total = ZERO_ISSUE_COUNTS
    for ws in websites:
        if not ws.id:
            continue
        total = total + count_website_issues(
            db,
            storage,
            ws.id,
            pdfs=pdfs_by_website.get(ws.id, []),
        )
    return total
