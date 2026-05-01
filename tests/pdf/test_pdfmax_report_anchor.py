"""Hash-handler tests for /pdfs/<id>/pdfmax-report (Part 1, 2026-05-01 spec)."""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PDFMAX_REPORT_HTML = (
    REPO_ROOT / "auto_a11y" / "web" / "templates" / "pdf" / "pdfmax_report.html"
)


def test_pdfmax_report_embeds_hash_handler_script() -> None:
    body = PDFMAX_REPORT_HTML.read_text(encoding="utf-8")
    # The hash-handler is identifiable by:
    #   - a unique marker comment we add
    #   - the hash-parse keyword 'check='
    #   - the data-check-name attribute selector
    #   - the i18n message id 'pdfmax-report-jumped-to-check'
    assert "pdfmax-report-hash-handler" in body, (
        "hash-handler script marker missing from pdfmax_report.html"
    )
    assert "data-check-name" in body, (
        "hash-handler must querySelector by data-check-name"
    )
    assert "pdfmax-report-jumped-to-check" in body, (
        "hash-handler must wire the live-region message"
    )
    assert "check=" in body, (
        "hash-handler must parse a check= hash fragment"
    )
