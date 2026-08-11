"""Render an audit as Markdown, in-process.

This replaces the subprocess call into an external pdfMax checkout. That
arrangement had two problems beyond the dependency: the checkout was
absent from every packaged build, so the report simply never appeared
outside Docker; and the report was produced by a second implementation of
the same checks, which could disagree with the one auto_a11y had just
run.

Rendering from :class:`~auto_a11y.pdf.models.AuditResult` means the
report says exactly what the audit found, and it says it in the reader's
own language — the subprocess only ever produced English.
"""
from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache
from typing import TYPE_CHECKING, cast

from auto_a11y.pdf.models import AuditResult, CheckOutcome, CheckResult

if TYPE_CHECKING:
    from markupsafe import Markup


def _ftl(message_id: str, **kwargs: object) -> str | Markup:
    """Resolve a Fluent message.

    Imported here rather than at module scope: ``auto_a11y.web.fluent``
    pulls in the whole web package, including the route that renders this
    report, so a top-level import closes the loop.
    """
    from auto_a11y.web.fluent import ftl

    return ftl(message_id, **kwargs)

#: Verdicts in the order a reader should meet them: what is broken, what
#: needs a look, what was verified, what did not apply.
_VERDICT_ORDER = ("FAIL", "WARN", "INFO", "PASS", "NA")

_VERDICT_HEADING = {
    "FAIL": "pdf-report-section-failures",
    "WARN": "pdf-report-section-warnings",
    "INFO": "pdf-report-section-info",
    "PASS": "pdf-report-section-passed",
    "NA": "pdf-report-section-not-applicable",
}

#: Stored verdict strings to the Literal the model declares.
_OUTCOMES: dict[str, CheckOutcome] = {
    "PASS": "PASS", "FAIL": "FAIL", "WARN": "WARN", "INFO": "INFO", "NA": "NA",
}

@lru_cache(maxsize=1)
def _stable_ids() -> dict[tuple[str, str], str]:
    """(check name, verdict) → stable id, for localised check names.

    Built on first use rather than at import. The catalogue reaches the
    touchpoint registry, which reaches the Flask app, which reaches the
    route that renders this report — importing it at module scope closes
    that loop.
    """
    from auto_a11y.pdf.translation.check_mapper import CHECK_CATALOGUE

    return {
        (row["pdfmax_check_name"], row["pdfmax_result"]): row["stable_id"]
        for row in CHECK_CATALOGUE
    }


def _escape(text: str) -> str:
    """Neutralise Markdown control characters in text we did not author.

    Check details carry document content — a title, a tag name, a
    filename — and a document titled ``**Q3**`` should not render as bold
    in the report.
    """
    for char in ("\\", "`", "*", "_", "[", "]", "<", ">", "|"):
        text = text.replace(char, "\\" + char)
    return text


def _check_title(check: CheckResult) -> str:
    """The check's name in the reader's language, or its own name.

    A check with no catalogue row — a PASS, or one whose row has not
    landed — falls back to the engine's English name rather than showing
    a missing-message id.
    """
    stable_id = _stable_ids().get((check.name, check.result))
    if stable_id is None:
        return check.name
    rendered = str(_ftl(f"pdf-check-{stable_id}-name"))
    if rendered == f"pdf-check-{stable_id}-name":
        return check.name
    return rendered


def _summary_table(result: AuditResult) -> list[str]:
    counts = (
        ("pdf-report-count-failed", result.fail_count),
        ("pdf-report-count-warnings", result.warn_count),
        ("pdf-report-count-passed", result.pass_count),
        ("pdf-report-count-not-applicable", result.na_count),
    )
    lines = [
        f"| {_ftl('pdf-report-column-outcome')} | {_ftl('pdf-report-column-count')} |",
        "| --- | ---: |",
    ]
    lines.extend(f"| {_ftl(label)} | {value} |" for label, value in counts)
    return lines


def _verdict_section(
    verdict: str, checks: Iterable[CheckResult]
) -> list[str]:
    listed = list(checks)
    if not listed:
        return []

    lines = [f"## {_ftl(_VERDICT_HEADING[verdict])} ({len(listed)})", ""]
    for check in listed:
        lines.append(f"### {_escape(_check_title(check))}")
        lines.append("")
        if check.standard:
            lines.append(f"*{_escape(check.standard)}*")
            lines.append("")
        lines.append(_escape(check.details))
        lines.append("")
    return lines


def checks_from_metadata(stored: object) -> list[CheckResult]:
    """Rebuild check verdicts from ``TestResult.metadata['check_results']``.

    Rows that are not shaped as a verdict are skipped rather than
    raising: metadata round-trips through Mongo untyped, and one
    malformed row should cost its own line, not the whole report.
    """
    if not isinstance(stored, list):
        return []
    rows = cast("list[object]", stored)

    verdicts: list[CheckResult] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        fields = cast("dict[str, object]", row)
        name = fields.get("name")
        result = fields.get("result")
        if not isinstance(name, str) or not isinstance(result, str):
            continue
        # A lookup rather than a narrowing construct: mypy, pyright and
        # ty disagree about which of `match`, `in` or `cast` narrows a
        # str to a Literal, and a mapping needs no narrowing at all.
        outcome = _OUTCOMES.get(result)
        if outcome is None:
            continue
        standard = fields.get("standard")
        details = fields.get("details")
        verdicts.append(
            CheckResult(
                name=name,
                standard=standard if isinstance(standard, str) else "",
                result=outcome,
                details=details if isinstance(details, str) else "",
            )
        )
    return verdicts


def render_audit_markdown(result: AuditResult) -> str:
    """Render an audit result as a Markdown report.

    Ordered failures first, then warnings, informational notes, passes
    and finally the checks that did not apply — so the reader meets what
    is wrong before what is fine.

    Must be called inside a Flask application context; the section
    headings and check names are resolved through Fluent.
    """
    lines: list[str] = [
        f"# {_ftl('pdf-report-title', filename=result.pdf_path.name)}",
        "",
    ]

    facts: list[str] = []
    if result.page_count:
        facts.append(f"{_ftl('pdf-report-pages')}: {result.page_count}")
    if result.pdf_version:
        facts.append(f"{_ftl('pdf-report-version')}: {_escape(result.pdf_version)}")
    language = result.declared_lang or result.detected_lang
    if language:
        facts.append(f"{_ftl('pdf-report-language')}: {_escape(language)}")
    if facts:
        lines.extend([" · ".join(facts), ""])

    lines.extend(_summary_table(result))
    lines.append("")

    if result.na_count:
        lines.extend([
            str(_ftl("pdf-report-not-applicable-note", count=result.na_count)),
            "",
        ])

    by_verdict: dict[str, list[CheckResult]] = {v: [] for v in _VERDICT_ORDER}
    for check in result.check_results:
        by_verdict.setdefault(check.result, []).append(check)

    for verdict in _VERDICT_ORDER:
        lines.extend(_verdict_section(verdict, by_verdict.get(verdict, [])))

    return "\n".join(lines).rstrip() + "\n"
