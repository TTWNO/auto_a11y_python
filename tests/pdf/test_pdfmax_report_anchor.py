"""Hash-handler tests for the pdfMax report body (Part 1, 2026-05-01 spec).

The report body — Markdown embedding, marked + DOMPurify rendering, and the
``#check=<name>`` deep-link handler asserted here — moved out of
``pdf/pdfmax_report.html`` into ``pdf/_pdfmax_report_body.html`` so the
standalone scan's Report tab renders through the same code. These
assertions follow it; the behaviour under test is unchanged.
"""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = REPO_ROOT / "auto_a11y" / "web" / "templates" / "pdf"
PDFMAX_REPORT_BODY_HTML = TEMPLATES / "_pdfmax_report_body.html"
PDFMAX_REPORT_HTML = TEMPLATES / "pdfmax_report.html"


def test_pdfmax_report_embeds_hash_handler_script() -> None:
    body = PDFMAX_REPORT_BODY_HTML.read_text(encoding="utf-8")
    # The hash-handler is identifiable by:
    #   - a unique marker comment we add
    #   - the hash-parse keyword 'check='
    #   - the data-check-name attribute selector
    #   - the i18n message id 'pdfmax-report-jumped-to-check'
    assert "pdfmax-report-hash-handler" in body, (
        "hash-handler script marker missing from _pdfmax_report_body.html"
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
    assert "applyCheckHash()" in body, (
        "applyCheckHash must be called from render()"
    )


def test_report_page_includes_the_shared_body() -> None:
    """The project-scoped page must render through the shared partial.

    Guards the reason the extraction happened: if this page ever grows its
    own copy of the body again, the standalone scan and the project report
    start drifting apart silently.
    """
    page = PDFMAX_REPORT_HTML.read_text(encoding="utf-8")
    assert "pdf/_pdfmax_report_body.html" in page, (
        "pdfmax_report.html must include the shared report body partial"
    )
    assert "image_prefix" in page, (
        "the page must pass image_prefix so <img> srcs resolve to its own route"
    )


def test_report_styles_are_linked_not_orphaned() -> None:
    """The stylesheet must land in a block base.html actually defines.

    This CSS previously sat in `{% block extra_head %}`, which base.html does
    not define — Jinja discarded it and the report rendered unstyled.
    """
    page = PDFMAX_REPORT_HTML.read_text(encoding="utf-8")
    assert "{% block extra_css %}" in page, (
        "report styles must be declared in extra_css, the block base.html defines"
    )
    assert "css/pdfmax_report.css" in page
    assert "{% block extra_head %}" not in page, (
        "extra_head is not a block base.html defines; its contents are dropped"
    )
