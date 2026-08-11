"""Tests for the native Markdown report.

The report replaces a subprocess into an external checkout, so what
matters is that it says what the audit found — in the reader's language,
with the faults first, and without letting document content act as
Markdown.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from auto_a11y.pdf.models import AuditResult, CheckResult
from auto_a11y.pdf.report_markdown import (
    checks_from_metadata,
    render_audit_markdown,
)
from auto_a11y.web.fluent import force_locale, init_fluent


@pytest.fixture
def app() -> Flask:
    application = Flask(__name__)
    init_fluent(application)
    return application


def _result(*checks: CheckResult) -> AuditResult:
    return AuditResult.from_checks(
        pdf_path=Path("report.pdf"),
        pdf_version="1.7",
        page_count=3,
        declared_lang="en",
        detected_lang=None,
        check_results=list(checks),
        ai_analysis=None,
    )


def _render(app: Flask, result: AuditResult, locale: str = "en") -> str:
    with app.test_request_context("/"), force_locale(locale):
        return render_audit_markdown(result)


def test_failures_come_before_passes(app: Flask) -> None:
    """A reader should meet what is wrong before what is fine."""
    markdown = _render(app, _result(
        CheckResult("Passing check", "PDF/UA", "PASS", "All good"),
        CheckResult("PDF is tagged", "PDF/UA, WCAG 1.3.1", "FAIL", "No /MarkInfo"),
    ))

    assert markdown.index("No /MarkInfo") < markdown.index("All good")


def test_the_summary_counts_every_outcome(app: Flask) -> None:
    markdown = _render(app, _result(
        CheckResult("A", "s", "FAIL", "d"),
        CheckResult("B", "s", "WARN", "d"),
        CheckResult("C", "s", "PASS", "d"),
        CheckResult("D", "s", "NA", "d"),
    ))

    assert "| Failed | 1 |" in markdown
    assert "| Warnings | 1 |" in markdown
    assert "| Passed | 1 |" in markdown
    assert "| Not applicable | 1 |" in markdown


def test_not_applicable_checks_are_explained_not_hidden(app: Flask) -> None:
    """N/A is counted apart from passes, and the report says why.

    Folding them into passes is exactly what made a scanned document
    read as mostly clean.
    """
    markdown = _render(app, _result(
        CheckResult("A", "s", "NA", "No tables in document"),
    ))

    assert "nothing of that kind to examine" in markdown


def test_document_content_cannot_act_as_markdown(app: Flask) -> None:
    """Check details carry the document's own words.

    A document titled **Q3** must not render the report in bold from
    there on.
    """
    markdown = _render(app, _result(
        CheckResult("Document title set", "PDF/UA", "FAIL", 'Title is "**Q3**"'),
    ))

    assert "\\*\\*Q3\\*\\*" in markdown


def test_the_report_is_rendered_in_the_readers_language(app: Flask) -> None:
    """The subprocess only ever produced English."""
    checks = (CheckResult("PDF is tagged", "PDF/UA", "FAIL", "No /MarkInfo"),)

    english = _render(app, _result(*checks), "en")
    french = _render(app, _result(*checks), "fr")

    assert "Failures" in english
    assert "Échecs" in french
    assert english != french


def test_a_check_with_no_catalogue_row_keeps_its_own_name(app: Flask) -> None:
    # Better the engine's English name than a raw message id on the page.
    markdown = _render(app, _result(
        CheckResult("Some future check", "s", "FAIL", "d"),
    ))

    assert "Some future check" in markdown
    assert "pdf-check-" not in markdown


# ---------------------------------------------------------------------------
# checks_from_metadata
# ---------------------------------------------------------------------------

def test_stored_verdicts_round_trip() -> None:
    stored = [
        {"name": "A", "standard": "s", "result": "FAIL", "details": "d"},
        {"name": "B", "standard": "s", "result": "NA", "details": "d"},
    ]

    checks = checks_from_metadata(stored)

    assert [c.result for c in checks] == ["FAIL", "NA"]


def test_a_malformed_row_costs_only_itself() -> None:
    """Metadata round-trips through Mongo untyped.

    One bad row should not take the whole report with it.
    """
    stored = [
        {"name": "Good", "standard": "s", "result": "FAIL", "details": "d"},
        {"name": "No verdict"},
        "not a row",
        {"name": "Bad verdict", "result": "MAYBE"},
    ]

    checks = checks_from_metadata(stored)

    assert [c.name for c in checks] == ["Good"]


def test_absent_metadata_yields_nothing() -> None:
    assert checks_from_metadata(None) == []
    assert checks_from_metadata("not a list") == []
