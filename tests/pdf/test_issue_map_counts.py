"""Unit tests for auto_a11y.pdf.issue_map_counts."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import pytest
from bson import ObjectId

from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus
from auto_a11y.pdf.issue_map_counts import PdfIssueCounts, count_issues
from auto_a11y.pdf.storage import PdfStorage


def _make_doc(*, website_id: str = "w-1", status: PdfDocumentStatus = PdfDocumentStatus.AUDITED) -> PdfDocument:
    oid = ObjectId()
    doc = PdfDocument(
        website_id=website_id,
        project_id="p-1",
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


def _materialise(storage: PdfStorage, doc: PdfDocument) -> Path:
    pdf_path = storage.local_path(doc)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4\n%fake\n%%EOF\n")
    return pdf_path


def test_pdf_issue_counts_zero_default() -> None:
    zero = PdfIssueCounts(0, 0)
    assert zero.violations == 0
    assert zero.warnings == 0


def test_pdf_issue_counts_addition() -> None:
    a = PdfIssueCounts(2, 3)
    b = PdfIssueCounts(4, 5)
    c = a + b
    assert c == PdfIssueCounts(6, 8)
    # Frozen — original unchanged.
    assert a == PdfIssueCounts(2, 3)


def test_count_issues_zero_when_pdf_unaudited(tmp_path: Path) -> None:
    storage = PdfStorage(base_dir=tmp_path)
    doc = _make_doc(status=PdfDocumentStatus.PENDING)
    assert count_issues(doc, storage) == PdfIssueCounts(0, 0)


def test_count_issues_zero_when_cache_dir_missing(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    storage = PdfStorage(base_dir=tmp_path)
    doc = _make_doc()
    _materialise(storage, doc)
    # Status is AUDITED but no pdfmax-report cache exists.
    with caplog.at_level(logging.WARNING, logger="auto_a11y.pdf.issue_map_counts"):
        result = count_issues(doc, storage)
    assert result == PdfIssueCounts(0, 0)
    assert any("missing pdfmax-report cache" in rec.message for rec in caplog.records)


def test_count_issues_zero_when_cache_dir_empty(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    storage = PdfStorage(base_dir=tmp_path)
    doc = _make_doc()
    pdf_path = _materialise(storage, doc)
    (pdf_path.parent / "pdfmax-report").mkdir()
    with caplog.at_level(logging.WARNING, logger="auto_a11y.pdf.issue_map_counts"):
        result = count_issues(doc, storage)
    assert result == PdfIssueCounts(0, 0)
    assert any("no issue_map.json found" in rec.message for rec in caplog.records)


def test_count_issues_zero_on_malformed_json(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    storage = PdfStorage(base_dir=tmp_path)
    doc = _make_doc()
    pdf_path = _materialise(storage, doc)
    cache = pdf_path.parent / "pdfmax-report"
    cache.mkdir()
    (cache / "doc_issue_map.json").write_text("{ not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="auto_a11y.pdf.issue_map_counts"):
        result = count_issues(doc, storage)
    assert result == PdfIssueCounts(0, 0)
    assert any("malformed" in rec.message.lower() for rec in caplog.records)


def test_count_issues_zero_on_missing_issues_key(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    storage = PdfStorage(base_dir=tmp_path)
    doc = _make_doc()
    pdf_path = _materialise(storage, doc)
    cache = pdf_path.parent / "pdfmax-report"
    cache.mkdir()
    (cache / "doc_issue_map.json").write_text(
        json.dumps({"version": 1}), encoding="utf-8"
    )
    with caplog.at_level(logging.WARNING, logger="auto_a11y.pdf.issue_map_counts"):
        result = count_issues(doc, storage)
    assert result == PdfIssueCounts(0, 0)


def test_count_issues_tallies_fail_and_warn(tmp_path: Path) -> None:
    storage = PdfStorage(base_dir=tmp_path)
    doc = _make_doc()
    pdf_path = _materialise(storage, doc)
    cache = pdf_path.parent / "pdfmax-report"
    cache.mkdir()
    payload = {
        "version": 1,
        "issues": [
            {"id": "0", "check_result": "FAIL"},
            {"id": "1", "check_result": "FAIL"},
            {"id": "2", "check_result": "WARN"},
            {"id": "3", "check_result": "FAIL"},
            {"id": "4", "check_result": "WARN"},
        ],
    }
    (cache / "doc_issue_map.json").write_text(json.dumps(payload), encoding="utf-8")
    assert count_issues(doc, storage) == PdfIssueCounts(violations=3, warnings=2)


def test_count_issues_ignores_unknown_severity(tmp_path: Path) -> None:
    storage = PdfStorage(base_dir=tmp_path)
    doc = _make_doc()
    pdf_path = _materialise(storage, doc)
    cache = pdf_path.parent / "pdfmax-report"
    cache.mkdir()
    payload = {
        "version": 1,
        "issues": [
            {"id": "0", "check_result": "FAIL"},
            {"id": "1", "check_result": "INFO"},
            {"id": "2", "check_result": "PASS"},
            {"id": "3", "check_result": "WARN"},
            {"id": "4", "check_result": ""},
            {"id": "5"},  # missing key entirely
        ],
    }
    (cache / "doc_issue_map.json").write_text(json.dumps(payload), encoding="utf-8")
    assert count_issues(doc, storage) == PdfIssueCounts(violations=1, warnings=1)
