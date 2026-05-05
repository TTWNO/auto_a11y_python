"""Tests for website-/project-detail rollup including audited PDFs.

Part 2 of the 2026-05-01 spec — verifies that PDFs contribute to the
total_violations / total_warnings counts shown on the website-detail
and project-detail pages, without any new persisted field.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from bson import ObjectId

from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus
from auto_a11y.pdf.issue_map_counts import PdfIssueCounts, count_issues
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


def test_count_issues_two_pdfs_summed(tmp_path: Path) -> None:
    """Sanity check that PdfIssueCounts.__add__ + count_issues compose
    the way the rollup loop expects."""
    storage = PdfStorage(base_dir=tmp_path)
    a = _make_doc()
    b = _make_doc()
    _write_cache(storage, a, fail=3, warn=4)
    _write_cache(storage, b, fail=1, warn=2)
    total = count_issues(a, storage) + count_issues(b, storage)
    assert total == PdfIssueCounts(violations=4, warnings=6)


def test_count_issues_skips_when_cache_missing(tmp_path: Path) -> None:
    """An AUDITED PDF with no cache file must not 500 the page or
    poison the running total."""
    storage = PdfStorage(base_dir=tmp_path)
    audited = _make_doc()
    cacheless = _make_doc()
    _write_cache(storage, audited, fail=2, warn=1)
    # cacheless intentionally has no cache dir
    pdf_path = storage.local_path(cacheless)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4\n%fake\n%%EOF\n")

    total = count_issues(audited, storage) + count_issues(cacheless, storage)
    assert total == PdfIssueCounts(violations=2, warnings=1)


def test_websites_route_imports_helper() -> None:
    """Smoke: websites.py imports PdfIssueCounts/count_issues."""
    import auto_a11y.web.routes.websites as websites
    assert any(
        name in dir(websites) for name in ("PdfIssueCounts", "count_issues")
    ), "websites.py does not appear to import the rollup helper"


def test_projects_route_imports_helper() -> None:
    """Smoke: projects.py wires in the cross-source rollup helper.

    Originally projects.py imported the PDF-only ``PdfIssueCounts`` /
    ``count_issues``. After the issues-counts fix it routes through
    ``count_website_issues``, which composes HTML and PDF sources into
    one number — adding new test sources happens there, in one place.
    """
    import auto_a11y.web.routes.projects as projects
    assert "count_website_issues" in dir(projects), (
        "projects.py does not import count_website_issues — the per-website "
        "rollup must go through the aggregator so project totals stay in "
        "sync with website badges."
    )
