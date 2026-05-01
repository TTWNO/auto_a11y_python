"""Tests for :mod:`auto_a11y.pdf.report_export`.

The exporters mirror the Save-as-HTML / Save-as-Markdown buttons in
the original pdfMax ``CheckerReport.tsx``. They build a self-contained
report from the persisted :class:`~auto_a11y.models.test_result.TestResult`
without any Flask request state, so the tests here are pure unit
tests — they don't spin up a test client.

The :func:`build_*_report` functions still call :func:`ftl` underneath,
which expects ``init_fluent`` to have been called. We initialise
Fluent against the real ``auto_a11y/web/translations`` tree once per
session and unload bundles afterwards.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest
from bson import ObjectId
from flask import Flask

# Match other PDF tests: ``auto_a11y.core`` must be imported before
# any module that imports from ``auto_a11y.testing``.
import auto_a11y.core as _core_preload
del _core_preload

from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus
from auto_a11y.models.test_result import (
    ImpactLevel,
    TargetType,
    TestResult,
    Violation,
)
from auto_a11y.pdf.report_export import (
    build_html_report,
    build_markdown_report,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope='module', autouse=True)
def _init_fluent() -> Iterator[None]:
    """Load every .ftl bundle once for this module's tests.

    ``ftl()`` is the static-text resolver inside the export module. It
    walks the module-global Fluent bundles populated by
    ``init_fluent``. We register them here so :func:`build_html_report`
    and :func:`build_markdown_report` can resolve message IDs without
    a Flask request context.
    """
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'test'
    app.config['TESTING'] = True
    from auto_a11y.web.fluent import init_fluent
    init_fluent(app)
    yield
    # Don't tear down — other test modules may have already loaded
    # the bundles too. Bundle dict is safe to leave populated.


# Pyright sees autouse fixtures as unused by default; this reference
# pacifies the lint without affecting fixture registration.
_ = _init_fluent


def _make_pdf(
    *,
    last_audit_result_id: str | None = "r-1",
    last_audited_at: datetime | None = datetime(2026, 4, 28, 14, 30),
    original_filename: str | None = "annual_report.pdf",
) -> PdfDocument:
    oid = ObjectId()
    pdf = PdfDocument(
        website_id="w-1",
        project_id="p-1",
        source_url=None,
        source_type="uploaded",
        discovered_from_page_id=None,
        discovered_from_user_id=None,
        sha256="a" * 64,
        file_size_bytes=1024,
        storage_relpath=f"w-1/{oid}/pdf.pdf",
        images_relpath=f"w-1/{oid}/images/",
        original_filename=original_filename,
        pdf_version="1.7",
        page_count=12,
        declared_lang="en",
        detected_lang="en",
        lang_confidence=0.99,
        status=PdfDocumentStatus.AUDITED,
        error_reason=None,
        last_audit_result_id=last_audit_result_id,
        discovered_at=datetime(2026, 1, 1, 12, 0),
        last_audited_at=last_audited_at,
    )
    pdf.mongo_id = oid
    return pdf


def _make_violation(
    *,
    impact: ImpactLevel = ImpactLevel.HIGH,
    touchpoint: str = "DocumentProperties",
    description: str = "The PDF is missing a Title in /Info.",
    short_title: str | None = None,
    wcag_criteria: list[str] | None = None,
    page: int | None = None,
    remediation: str | None = None,
) -> Violation:
    return Violation(
        id="pdf-no-document-title",
        impact=impact,
        touchpoint=touchpoint,
        description=description,
        short_title=short_title,
        wcag_criteria=wcag_criteria or [],
        remediation=remediation,
        metadata={"pdf_page": page} if page is not None else {},
    )


def _make_result(
    *,
    pdf_id: str,
    violations: list[Violation] | None = None,
    warnings: list[Violation] | None = None,
    info: list[Violation] | None = None,
    passes: list[dict[str, Any]] | None = None,
) -> TestResult:
    return TestResult(
        page_id=None,
        target_type=TargetType.PDF_DOCUMENT,
        target_id=pdf_id,
        violations=violations or [],
        warnings=warnings or [],
        info=info or [],
        passes=passes or [],
    )


# ---------------------------------------------------------------------------
# Markdown export
# ---------------------------------------------------------------------------


class TestBuildMarkdownReport:

    def test_includes_title_and_metadata(self) -> None:
        pdf = _make_pdf()
        out = build_markdown_report(pdf, None)
        assert "annual_report.pdf" in out
        # The "not audited yet" banner — there's no test_result.
        assert "not been audited" in out

    def test_overview_counts_when_audited(self) -> None:
        pdf = _make_pdf()
        assert pdf.id is not None
        result = _make_result(
            pdf_id=pdf.id,
            violations=[_make_violation()],
            warnings=[_make_violation(impact=ImpactLevel.MEDIUM)],
            info=[_make_violation(impact=ImpactLevel.LOW)],
            passes=[{"id": "x"}],
        )
        out = build_markdown_report(pdf, result)
        # All four counts appear with their bold-1 markup.
        assert "**1**" in out
        # The conformance-fail verdict must be visible (1 error → fail).
        assert "does not conform" in out

    def test_findings_grouped_by_touchpoint(self) -> None:
        pdf = _make_pdf()
        assert pdf.id is not None
        result = _make_result(
            pdf_id=pdf.id,
            violations=[
                _make_violation(touchpoint="DocumentProperties"),
                _make_violation(touchpoint="Annotations",
                                description="Missing /Contents on annotation"),
            ],
        )
        out = build_markdown_report(pdf, result)
        # Each violation is rendered with an inline anchor span so
        # WCAG-appendix links work post-render to HTML.
        assert 'id="issue-1"' in out
        assert 'id="issue-2"' in out
        # Both descriptions land in the body.
        assert "missing a Title" in out
        assert "Missing /Contents" in out

    def test_wcag_appendix_lists_criteria(self) -> None:
        pdf = _make_pdf()
        assert pdf.id is not None
        result = _make_result(
            pdf_id=pdf.id,
            violations=[
                _make_violation(wcag_criteria=["1.3.1", "2.4.6"]),
            ],
        )
        out = build_markdown_report(pdf, result)
        # Both criteria appear as table-row identifiers.
        assert "1.3.1" in out
        assert "2.4.6" in out

    def test_jump_to_page_emitted_when_page_metadata_present(self) -> None:
        pdf = _make_pdf()
        assert pdf.id is not None
        result = _make_result(
            pdf_id=pdf.id,
            violations=[_make_violation(page=7)],
        )
        out = build_markdown_report(pdf, result)
        assert "7" in out
        # Italic emphasis around the jump-to-page label.
        assert "*" in out

    def test_passes_only_yields_pass_verdict(self) -> None:
        pdf = _make_pdf()
        out = build_markdown_report(pdf, _make_result(pdf_id="x"))
        # No errors, no warnings → the pass verdict copy.
        assert "appears to conform" in out

    def test_warnings_only_yields_warn_verdict(self) -> None:
        pdf = _make_pdf()
        result = _make_result(
            pdf_id="x",
            warnings=[_make_violation(impact=ImpactLevel.MEDIUM)],
        )
        out = build_markdown_report(pdf, result)
        assert "no errors" in out

    def test_remediation_emitted_in_fenced_block(self) -> None:
        # Use a real Fluent ID so the resolver finds it; if absent, the
        # function returns the message_id unchanged — still works.
        pdf = _make_pdf()
        result = _make_result(
            pdf_id="x",
            violations=[
                _make_violation(remediation="pdf-remediation-PdfErrPdfMissingTitle")
            ],
        )
        out = build_markdown_report(pdf, result)
        assert "```" in out  # fenced code block delimiter

    def test_french_locale_localises_section_headings(self) -> None:
        # The fr/ files are TODO_FR placeholder English in this repo,
        # so the localised text equals the English text — but the fact
        # that build_markdown_report(locale='fr') does not crash means
        # force_locale wired up correctly.
        pdf = _make_pdf()
        out = build_markdown_report(pdf, None, locale='fr')
        assert "annual_report" in out


# ---------------------------------------------------------------------------
# HTML export
# ---------------------------------------------------------------------------


class TestBuildHtmlReport:

    def test_returns_complete_html_document(self) -> None:
        pdf = _make_pdf()
        out = build_html_report(pdf, None)
        assert out.startswith("<!DOCTYPE html>")
        assert "<html" in out
        assert "</html>" in out

    def test_inlines_stylesheet_no_external_resources(self) -> None:
        pdf = _make_pdf()
        out = build_html_report(pdf, None)
        # No <link rel="stylesheet">, no external <script>, no <img>.
        assert '<link ' not in out
        assert '<script' not in out
        assert '<img ' not in out
        # Stylesheet IS inlined in <style>.
        assert '<style>' in out

    def test_skip_link_present_for_keyboard_users(self) -> None:
        pdf = _make_pdf()
        out = build_html_report(pdf, None)
        assert 'skip-link' in out
        assert '#report-content' in out

    def test_lang_attribute_uses_pdf_declared_lang(self) -> None:
        pdf = _make_pdf()
        out = build_html_report(pdf, None)
        assert 'lang="en"' in out

    def test_findings_rendered_with_anchors(self) -> None:
        pdf = _make_pdf()
        assert pdf.id is not None
        result = _make_result(
            pdf_id=pdf.id,
            violations=[_make_violation(), _make_violation()],
            warnings=[_make_violation(impact=ImpactLevel.MEDIUM)],
        )
        out = build_html_report(pdf, result)
        assert "issue-1" in out
        assert "issue-2" in out
        assert "issue-3" in out

    def test_overview_stats_visible(self) -> None:
        pdf = _make_pdf()
        result = _make_result(
            pdf_id="x",
            violations=[_make_violation()],
            warnings=[_make_violation(impact=ImpactLevel.MEDIUM)],
            info=[_make_violation(impact=ImpactLevel.LOW)],
        )
        out = build_html_report(pdf, result)
        assert "class='stats'" in out
        # Both fail- and warn-coloured stat tiles.
        assert 'stat-fail' in out
        assert 'stat-warn' in out

    def test_wcag_appendix_links_to_findings(self) -> None:
        pdf = _make_pdf()
        result = _make_result(
            pdf_id="x",
            violations=[_make_violation(wcag_criteria=["2.4.6"])],
        )
        out = build_html_report(pdf, result)
        assert "2.4.6" in out
        assert '#issue-1' in out

    def test_xss_safe_escaping_of_violation_description(self) -> None:
        """Violation description must be HTML-escaped, not raw."""
        pdf = _make_pdf()
        result = _make_result(
            pdf_id="x",
            violations=[
                _make_violation(
                    description="<script>alert(1)</script>",
                ),
            ],
        )
        out = build_html_report(pdf, result)
        assert "<script>alert(1)</script>" not in out
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out

    def test_french_locale_does_not_crash(self) -> None:
        pdf = _make_pdf()
        out = build_html_report(pdf, None, locale='fr')
        assert "<!DOCTYPE html>" in out

    def test_print_styles_present(self) -> None:
        pdf = _make_pdf()
        out = build_html_report(pdf, None)
        # The inline stylesheet ends with @media print rules so the
        # downloaded report prints cleanly.
        assert '@media print' in out
