"""Regression tests: project-level totals must include audited PDFs.

Before the fix, the project overview page showed
``total_violations``/``total_warnings`` from the HTML page test_results
aggregation only. PDFs were rolled up correctly into the per-website
badges (``website_stats``) but invisible at the project level — a
project containing only audited PDFs with issues showed "0 issues" at
the top of the page even though each website badge below had non-zero
counts.

These tests pin the fix so the discrepancy can't silently regress.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from bson import ObjectId

from auto_a11y.core.issue_aggregator import (
    IssueCounts,
    ZERO_ISSUE_COUNTS,
    count_pdf_issues,
    count_website_issues,
)
from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus
from auto_a11y.pdf.storage import PdfStorage


def _make_doc(
    *,
    website_id: str = "w-1",
    project_id: str = "p-1",
    status: PdfDocumentStatus = PdfDocumentStatus.AUDITED,
) -> PdfDocument:
    oid = ObjectId()
    doc = PdfDocument(
        website_id=website_id,
        project_id=project_id,
        source_url=None,
        source_type="uploaded",
        discovered_from_page_id=None,
        discovered_from_user_id=None,
        sha256="a" * 64,
        file_size_bytes=1,
        storage_relpath=f"{website_id}/{oid}/pdf.pdf",
        images_relpath=f"{website_id}/{oid}/images/",
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


def _write_cache(storage: PdfStorage, doc: PdfDocument, fail: int, warn: int) -> None:
    pdf_path = storage.local_path(doc)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4\n%fake\n%%EOF\n")
    cache = pdf_path.parent / "pdfmax-report"
    cache.mkdir(parents=True, exist_ok=True)
    issues = (
        [{"id": f"f-{i}", "check_result": "FAIL"} for i in range(fail)]
        + [{"id": f"w-{i}", "check_result": "WARN"} for i in range(warn)]
    )
    (cache / "doc_issue_map.json").write_text(
        json.dumps({"version": 1, "issues": issues}), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Aggregator-level tests (unit, no Mongo).
# ---------------------------------------------------------------------------


def test_zero_issue_counts_is_additive_identity() -> None:
    """ZERO_ISSUE_COUNTS + x == x for any IssueCounts value."""
    x = IssueCounts(violations=3, warnings=4)
    assert ZERO_ISSUE_COUNTS + x == x
    assert x + ZERO_ISSUE_COUNTS == x


def test_issue_counts_addition_is_commutative_and_correct() -> None:
    a = IssueCounts(violations=2, warnings=3)
    b = IssueCounts(violations=5, warnings=1)
    assert a + b == IssueCounts(violations=7, warnings=4)
    assert b + a == IssueCounts(violations=7, warnings=4)


def test_count_pdf_issues_skips_non_audited(tmp_path: Path) -> None:
    """A PENDING PDF must not contribute even if a cache exists."""
    storage = PdfStorage(base_dir=tmp_path)
    pending = _make_doc(status=PdfDocumentStatus.PENDING)
    _write_cache(storage, pending, fail=99, warn=99)
    result = count_pdf_issues([pending], storage)
    assert result == ZERO_ISSUE_COUNTS


def test_count_pdf_issues_sums_audited_only(tmp_path: Path) -> None:
    """Only AUDITED PDFs contribute; multiple audited PDFs sum."""
    storage = PdfStorage(base_dir=tmp_path)
    audited_a = _make_doc(status=PdfDocumentStatus.AUDITED)
    audited_b = _make_doc(status=PdfDocumentStatus.AUDITED)
    pending = _make_doc(status=PdfDocumentStatus.PENDING)
    _write_cache(storage, audited_a, fail=2, warn=3)
    _write_cache(storage, audited_b, fail=1, warn=4)
    _write_cache(storage, pending, fail=99, warn=99)
    result = count_pdf_issues([audited_a, audited_b, pending], storage)
    assert result == IssueCounts(violations=3, warnings=7)


def test_count_website_issues_uses_provided_pdfs_and_pages(tmp_path: Path) -> None:
    """When tested_page_ids/pdfs are passed, no extra DB calls happen."""
    storage = PdfStorage(base_dir=tmp_path)
    audited = _make_doc()
    _write_cache(storage, audited, fail=2, warn=3)

    db = MagicMock()
    # If tested_page_ids is empty the HTML branch must short-circuit
    # without hitting Mongo.
    db.test_results = MagicMock()
    db.test_results.aggregate = MagicMock(return_value=[])

    result = count_website_issues(
        db, storage, "w-1",
        tested_page_ids=[],
        pdfs=[audited],
    )
    assert result == IssueCounts(violations=2, warnings=3)
    # Empty page list means the aggregate pipeline isn't even built.
    db.test_results.aggregate.assert_not_called()
    # Caller supplied pdfs => no get_pdf_documents call either.
    db.get_pdf_documents.assert_not_called()


def test_count_website_issues_combines_html_and_pdf(tmp_path: Path) -> None:
    """HTML test_results and PDF audits sum into one website total."""
    storage = PdfStorage(base_dir=tmp_path)
    audited = _make_doc()
    _write_cache(storage, audited, fail=1, warn=2)

    db = MagicMock()
    db.test_results = MagicMock()
    # Two pages each with one test result.
    db.test_results.aggregate = MagicMock(return_value=[
        {"_id": "p1", "violation_count": 4, "warning_count": 5},
        {"_id": "p2", "violation_count": 0, "warning_count": 1},
    ])

    result = count_website_issues(
        db, storage, "w-1",
        tested_page_ids=["p1", "p2"],
        pdfs=[audited],
    )
    assert result == IssueCounts(violations=5, warnings=8)


# ---------------------------------------------------------------------------
# Route-level test: project view forwards combined totals to template.
# ---------------------------------------------------------------------------


def test_view_project_stats_include_audited_pdf_counts(tmp_path: Path) -> None:
    """The bug: project overview shows 0/0 even when PDFs have issues.

    With the fix, the ``stats`` dict passed to ``render_template``
    must reflect the audited-PDF FAIL/WARN counts, matching the
    per-website badge shown directly below it on the same page.
    """
    from auto_a11y.web.routes import projects as projects_module

    pdf_a = _make_doc(website_id="w-1", project_id="p-1")
    pdf_b = _make_doc(website_id="w-1", project_id="p-1")

    # Set up storage at a path the route will construct from app config.
    cfg = MagicMock()
    cfg.PDF_STORAGE_DIR = str(tmp_path)
    storage = PdfStorage(base_dir=tmp_path)
    _write_cache(storage, pdf_a, fail=3, warn=2)
    _write_cache(storage, pdf_b, fail=1, warn=4)

    # Mock website with id matching the PDFs' website_id.
    website = MagicMock()
    website.id = "w-1"
    website.page_count = 0

    db = MagicMock()
    project = MagicMock()
    project.id = "p-1"
    db.get_project.return_value = project
    db.get_websites.return_value = [website]
    db.get_pages.return_value = []  # No HTML pages — so the bug is purely PDF
    db.get_pdf_documents.return_value = [pdf_a, pdf_b]
    db.get_project_users.return_value = []
    db.get_recordings.return_value = []
    db.discovered_pages = MagicMock()
    db.discovered_pages.find.return_value.sort.return_value = []
    db.get_all_groups.return_value = []
    db.test_results = MagicMock()
    db.test_results.aggregate = MagicMock(return_value=[])
    # Real call path: get_project_stats is invoked on the mock, so we
    # delegate it to the real implementation operating on the same mock
    # — that exercises the production code path including the new
    # PDF rollup branch. We do this by binding the unbound method.
    from auto_a11y.core.database import Database

    def _delegate_stats(pid: str, pdf_storage: PdfStorage | None = None) -> dict[str, Any]:
        return Database.get_project_stats(db, pid, pdf_storage=pdf_storage)

    db.get_project_stats.side_effect = _delegate_stats

    captured: dict[str, Any] = {}

    def fake_render_template(name: str, **ctx: Any) -> str:
        captured["name"] = name
        captured.update(ctx)
        return "RENDERED"

    fake_user = MagicMock()
    fake_user.is_superadmin = True

    with patch.object(projects_module, "get_db", return_value=db), \
         patch.object(projects_module, "get_app_config", return_value=cfg), \
         patch.object(projects_module, "render_template", side_effect=fake_render_template), \
         patch.object(projects_module, "current_user", fake_user), \
         patch.object(projects_module, "g", MagicMock()):
        fn: Any = projects_module.view_project
        while hasattr(fn, "__wrapped__"):
            fn = fn.__wrapped__
        result = fn("p-1")

    assert result == "RENDERED"
    stats = captured.get("stats")
    assert stats is not None, "stats was not passed to render_template"
    # Combined: 3+1 violations from PDFs, 2+4 warnings from PDFs.
    assert stats["total_violations"] == 4
    assert stats["total_warnings"] == 6
    # Coverage rolls PDFs into both numerator and denominator: a project
    # whose only "document" is an audited PDF reads 1/1 (100%), not 0/0.
    assert stats["total_pages"] == 2
    assert stats["tested_pages"] == 2
    assert stats["test_coverage"] == 100.0
    # And the per-website badge should match (pinned in the same loop).
    website_stats = captured.get("website_stats")
    assert website_stats is not None
    assert website_stats["w-1"] == {"violations": 4, "warnings": 6}


def test_get_project_stats_combines_html_and_pdf_documents(tmp_path: Path) -> None:
    """Combined coverage math: HTML pages + PDFs feed one document total.

    Scenario: 2 HTML pages (1 tested), 3 PDFs (1 AUDITED, 1 PENDING,
    1 FETCH_FAILED). FETCH_FAILED is excluded from the denominator
    because the file never reached an auditable state. Expected:
    total_pages = 2 + 2 = 4, tested_pages = 1 + 1 = 2, coverage = 50%.
    """
    from auto_a11y.core.database import Database
    from auto_a11y.models.page import Page

    storage = PdfStorage(base_dir=tmp_path)
    audited = _make_doc(status=PdfDocumentStatus.AUDITED)
    pending = _make_doc(status=PdfDocumentStatus.PENDING)
    failed = _make_doc(status=PdfDocumentStatus.FETCH_FAILED)
    _write_cache(storage, audited, fail=0, warn=0)

    website = MagicMock()
    website.id = "w-1"

    page_tested = MagicMock(spec=Page)
    page_tested.id = "p-tested"
    page_tested.status = MagicMock()
    page_tested.status = __import__(
        "auto_a11y.models.page", fromlist=["PageStatus"]
    ).PageStatus.TESTED
    page_untested = MagicMock(spec=Page)
    page_untested.id = "p-untested"
    page_untested.status = __import__(
        "auto_a11y.models.page", fromlist=["PageStatus"]
    ).PageStatus.DISCOVERED

    db = MagicMock()
    db.get_websites.return_value = [website]
    db.get_pages.return_value = [page_tested, page_untested]
    db.get_pdf_documents.return_value = [audited, pending, failed]
    db.test_results = MagicMock()
    db.test_results.aggregate = MagicMock(return_value=[])

    stats = Database.get_project_stats(db, "p-1", pdf_storage=storage)

    assert stats["html_page_count"] == 2
    assert stats["tested_html_pages"] == 1
    assert stats["pdf_count"] == 2  # audited + pending; failed excluded
    assert stats["tested_pdfs"] == 1
    assert stats["total_pages"] == 4
    assert stats["tested_pages"] == 2
    assert stats["untested_pages"] == 2
    assert stats["test_coverage"] == 50.0
