"""Issue-card detail parity tests (Part 1 of the 2026-05-01 spec)."""
from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
EN_PDF_FTL = REPO_ROOT / "auto_a11y" / "web" / "translations" / "en" / "pdf.ftl"
FR_PDF_FTL = REPO_ROOT / "auto_a11y" / "web" / "translations" / "fr" / "pdf.ftl"


REQUIRED_NEW_IDS = (
    "pdf-viewer-issue-element",
    "pdf-viewer-issue-document-level",
    "pdf-viewer-issue-group-count",
    "pdf-viewer-issue-view-in-report",
    "pdf-viewer-issue-view-in-report-aria",
    "pdfmax-report-jumped-to-check",
)
REMOVED_IDS = ("pdf-viewer-jump-to-page-template",)


@pytest.mark.parametrize("ftl_path", [EN_PDF_FTL, FR_PDF_FTL])
def test_required_ftl_ids_present(ftl_path: Path) -> None:
    body = ftl_path.read_text(encoding="utf-8")
    for ftl_id in REQUIRED_NEW_IDS:
        assert f"\n{ftl_id}" in f"\n{body}", (
            f"missing Fluent ID {ftl_id!r} in {ftl_path}"
        )


@pytest.mark.parametrize("ftl_path", [EN_PDF_FTL, FR_PDF_FTL])
def test_removed_ftl_ids_absent(ftl_path: Path) -> None:
    body = ftl_path.read_text(encoding="utf-8")
    for ftl_id in REMOVED_IDS:
        assert f"\n{ftl_id} " not in f"\n{body}" and f"\n{ftl_id}\n" not in f"\n{body}", (
            f"dead Fluent ID {ftl_id!r} still present in {ftl_path}"
        )


DETAIL_HTML = REPO_ROOT / "auto_a11y" / "web" / "templates" / "pdf" / "detail.html"


def test_detail_html_i18n_block_has_new_keys() -> None:
    body = DETAIL_HTML.read_text(encoding="utf-8")
    # Each new JS-side key (kebab-case, prefix stripped) must be wired
    # to its Fluent ID via {{ ftl(...) | tojson }} per CLAUDE.md.
    expected_pairs = [
        ('"issue-element"', "pdf-viewer-issue-element"),
        ('"issue-document-level"', "pdf-viewer-issue-document-level"),
        ('"issue-group-count"', "pdf-viewer-issue-group-count"),
        ('"issue-view-in-report"', "pdf-viewer-issue-view-in-report"),
        ('"issue-view-in-report-aria"', "pdf-viewer-issue-view-in-report-aria"),
        ('"jumped-to-check"', "pdfmax-report-jumped-to-check"),
    ]
    for js_key, ftl_id in expected_pairs:
        # The line shape we expect (whitespace-tolerant): <js_key>: ftl('<id>')
        needle = f"{js_key}:"
        assert needle in body, f"missing JS key {js_key} in window.pdfViewerI18n"
        # Same line should mention the Fluent ID.
        line = next(
            (ln for ln in body.splitlines() if needle in ln),
            "",
        )
        assert ftl_id in line, (
            f"JS key {js_key} not wired to ftl('{ftl_id}'); got line: {line!r}"
        )


def test_detail_html_removes_jump_to_page_key() -> None:
    body = DETAIL_HTML.read_text(encoding="utf-8")
    assert '"jump-to-page"' not in body, (
        "dead JS key 'jump-to-page' still present in window.pdfViewerI18n"
    )
    assert "pdf-viewer-jump-to-page-template" not in body, (
        "dead Fluent ID still referenced in detail.html"
    )


def test_detail_html_has_pdfmax_report_url_attr() -> None:
    body = DETAIL_HTML.read_text(encoding="utf-8")
    # The new attribute carries the URL the View-in-report button
    # navigates to. Read by pdf_viewer_app.js when building cards.
    assert "data-pdfmax-report-url" in body, (
        "issue-list container missing data-pdfmax-report-url attribute"
    )


JS_VIEWER = REPO_ROOT / "auto_a11y" / "web" / "static" / "js" / "pdf_viewer_app.js"


def test_pdf_viewer_app_has_no_jump_to_page_button() -> None:
    """The per-card 'Page N' jump button is removed in this pass."""
    body = JS_VIEWER.read_text(encoding="utf-8")
    # The previous implementation had a `pdf-viewer-issue-page-btn`
    # class. Spec calls for its removal in favour of a "View in
    # report" button. Asserts both the class name and the dead
    # i18n key are gone from the JS source.
    assert "pdf-viewer-issue-page-btn" not in body, (
        "dead .pdf-viewer-issue-page-btn class still referenced in JS"
    )
    assert '"jump-to-page"' not in body, (
        "dead 'jump-to-page' i18n key still referenced in JS"
    )


def test_pdf_viewer_app_renders_element_tag_and_doclevel() -> None:
    """The card builder uses the new i18n keys and renders the new
    fields. Pure source-presence — actual DOM rendering would need a
    JSDOM harness which we don't run here.
    """
    body = JS_VIEWER.read_text(encoding="utf-8")
    for needle in (
        '"issue-element"',
        '"issue-document-level"',
        '"issue-view-in-report"',
        '"issue-view-in-report-aria"',
        "_buildIssueGroupCard",
        "data-pdfmax-report-url",
    ):
        assert needle in body, f"pdf_viewer_app.js missing reference to {needle}"


def test_pdf_viewer_app_uses_groupissues() -> None:
    body = JS_VIEWER.read_text(encoding="utf-8")
    assert "function groupIssues" in body, "groupIssues port not found in JS source"
    assert "groupIssues(this.allIssues)" in body, (
        "_renderIssueList not wired to groupIssues(this.allIssues)"
    )
