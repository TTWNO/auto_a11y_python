"""Self-contained Markdown and HTML export of a PDF audit result.

Mirrors the Save-as-HTML / Save-as-Markdown export feature of the
original pdfMax ``CheckerReport.tsx`` (handleExportMarkdown +
handleExportHTML). Auto_a11y stores audit data as a structured
:class:`~auto_a11y.models.test_result.TestResult` rather than raw
markdown, so the exporters build their output directly from the
:class:`Violation` list — no detail.html template re-render.

Design constraints:

* Pure functions: take ``pdf`` + ``test_result`` + ``locale`` and
  return a string. No Flask/request context required.
* Self-contained: the HTML output inlines its own minimal stylesheet
  and skip-link so the file works when downloaded or opened offline.
* Translation-aware: every user-visible label resolves through Fluent
  in the requested locale via :func:`force_locale`.
* No remote resources: no external CSS, JS, or images. The PDF's
  extracted images are *not* embedded — they're streamed from the
  app's image route and only render when the file is opened from the
  same origin. The export targets readable text content first.
"""
from __future__ import annotations

from datetime import datetime
from html import escape
from typing import TYPE_CHECKING

from auto_a11y.models.test_result import TestResult, Violation
from auto_a11y.web.fluent import force_locale, ftl, ftl_enum

if TYPE_CHECKING:
    from auto_a11y.models.pdf_document import PdfDocument


# Fixed severity ordering used in tables, summaries, and grouped lists.
_LEVELS: tuple[tuple[str, str], ...] = (
    ("fail", "violations"),
    ("warn", "warnings"),
    ("info", "info"),
)


def _safe_ftl(message_id: str, **kwargs: object) -> str:
    """Resolve a Fluent message and coerce to plain ``str``.

    :func:`ftl` returns ``Markup`` on success and ``str`` on miss; both
    behave like ``str`` for serialisation, but this wrapper guarantees
    the export modules never accidentally emit raw ``Markup`` objects
    into a ``.md`` text file (where the auto-escape is meaningless).
    """
    return str(ftl(message_id, **kwargs))


def _level_label(level: str) -> str:
    return _safe_ftl({
        "fail": "pdf-violation-result-fail",
        "warn": "pdf-violation-result-warn",
        "info": "pdf-violation-result-info",
    }[level])


def _touchpoint_label(touchpoint: str) -> str:
    """Look up the Fluent label for a touchpoint key.

    Falls back to the raw key when the Fluent ID is missing — same
    behaviour as the detail template's ``tp_label != tp_label_id``
    guard.
    """
    slug = touchpoint.replace("_", "-")
    msg_id = f"touchpoint-{slug}-name"
    label = _safe_ftl(msg_id)
    return touchpoint if label == msg_id else label


def _format_meta_value(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value)


# ---------------------------------------------------------------------------
# Markdown export
# ---------------------------------------------------------------------------


def build_markdown_report(
    pdf: PdfDocument,
    test_result: TestResult | None,
    locale: str = "en",
) -> str:
    """Build a self-contained Markdown audit report.

    Output mirrors the structure of the on-screen detail page:

    1. Title + document metadata
    2. Overview counts and conformance verdict
    3. Findings grouped by touchpoint (each as a bullet list)
    4. WCAG criteria mapping (table)
    5. Touchpoint summary (table)
    """
    with force_locale(locale):
        return _build_markdown(pdf, test_result)


def _build_markdown(
    pdf: PdfDocument,
    test_result: TestResult | None,
) -> str:
    lines: list[str] = []
    title = pdf.original_filename or pdf.id or "PDF"
    lines.append(f"# {title} — {_safe_ftl('pdf-detail-results-heading')}")
    lines.append("")

    # ----- metadata -----
    meta_pairs: list[tuple[str, str]] = [
        (_safe_ftl("pdf-meta-page-count"),
         _format_meta_value(pdf.page_count)),
        (_safe_ftl("pdf-meta-pdf-version"),
         _format_meta_value(pdf.pdf_version)),
        (_safe_ftl("pdf-meta-declared-language"),
         _format_meta_value(pdf.declared_lang)),
        (_safe_ftl("pdf-meta-detected-language"),
         _format_meta_value(pdf.detected_lang)),
        (_safe_ftl("pdf-meta-last-audited-at"),
         _format_meta_value(pdf.last_audited_at)),
    ]
    for key, value in meta_pairs:
        lines.append(f"- **{key}:** {value}")
    lines.append("")

    if test_result is None:
        lines.append(_safe_ftl("pdf-result-summary-not-audited"))
        lines.append("")
        return "\n".join(lines)

    fail_count = len(test_result.violations)
    warn_count = len(test_result.warnings)
    info_count = len(test_result.info)
    pass_count = len(test_result.passes)

    # ----- overview -----
    lines.append(f"## {_safe_ftl('pdf-report-overview-heading')}")
    lines.append("")
    lines.append(f"- {_safe_ftl('pdf-report-overview-errors')}: **{fail_count}**")
    lines.append(f"- {_safe_ftl('pdf-report-overview-warnings')}: **{warn_count}**")
    lines.append(f"- {_safe_ftl('pdf-report-overview-info')}: **{info_count}**")
    lines.append(f"- {_safe_ftl('pdf-report-overview-passes')}: **{pass_count}**")
    lines.append("")

    if fail_count > 0:
        verdict = _safe_ftl(
            "pdf-report-overview-conformance-fail", count=fail_count
        )
    elif warn_count > 0:
        verdict = _safe_ftl(
            "pdf-report-overview-conformance-warn", count=warn_count
        )
    else:
        verdict = _safe_ftl("pdf-report-overview-conformance-pass")
    lines.append(f"> {verdict}")
    lines.append("")

    # ----- findings grouped by touchpoint -----
    levelled_findings = _collect_findings(test_result)
    if levelled_findings:
        groups = _group_by_touchpoint(levelled_findings)
        lines.append(f"## {_safe_ftl('pdf-report-issues-heading')}")
        lines.append("")
        anchor = 0
        for tp, items in groups:
            lines.append(f"### {_touchpoint_label(tp)}")
            lines.append("")
            for level, violation in items:
                anchor += 1
                lines.extend(_render_violation_md(level, violation, anchor))
                lines.append("")
    else:
        lines.append(f"## {_safe_ftl('pdf-report-issues-heading')}")
        lines.append("")
        lines.append(_safe_ftl("pdf-report-issues-empty"))
        lines.append("")

    # ----- WCAG mapping appendix -----
    wcag_rows = _wcag_index(levelled_findings)
    lines.append(f"## {_safe_ftl('pdf-report-appendix-wcag-heading')}")
    lines.append("")
    if wcag_rows:
        lines.append(
            f"| {_safe_ftl('pdf-report-appendix-wcag-col-criterion')} "
            + f"| {_safe_ftl('pdf-report-appendix-wcag-col-count')} "
            + f"| {_safe_ftl('pdf-report-appendix-wcag-col-issues')} |"
        )
        lines.append("|---|---:|---|")
        for criterion, entries in wcag_rows:
            issues_md = ", ".join(
                f"[{label}](#issue-{anchor})" for anchor, label in entries
            )
            lines.append(f"| {criterion} | {len(entries)} | {issues_md} |")
    else:
        lines.append(_safe_ftl("pdf-report-appendix-wcag-empty"))
    lines.append("")

    # ----- touchpoint summary appendix -----
    lines.append(
        f"## {_safe_ftl('pdf-report-appendix-touchpoints-heading')}"
    )
    lines.append("")
    lines.append(
        f"| {_safe_ftl('pdf-report-appendix-touchpoints-col-name')} "
        + f"| {_safe_ftl('pdf-report-appendix-touchpoints-col-errors')} "
        + f"| {_safe_ftl('pdf-report-appendix-touchpoints-col-warnings')} "
        + f"| {_safe_ftl('pdf-report-appendix-touchpoints-col-info')} "
        + f"| {_safe_ftl('pdf-report-appendix-touchpoints-col-total')} |"
    )
    lines.append("|---|---:|---:|---:|---:|")
    for tp, items in _group_by_touchpoint(levelled_findings):
        tp_fail = sum(1 for level, _ in items if level == "fail")
        tp_warn = sum(1 for level, _ in items if level == "warn")
        tp_info = sum(1 for level, _ in items if level == "info")
        lines.append(
            f"| {_touchpoint_label(tp)} | {tp_fail} | {tp_warn} "
            + f"| {tp_info} | **{len(items)}** |"
        )
    lines.append("")

    return "\n".join(lines)


def _render_violation_md(
    level: str, violation: Violation, anchor: int
) -> list[str]:
    """Render a single Violation as a Markdown block."""
    out: list[str] = []
    short_title = (
        _safe_ftl(violation.short_title)
        if violation.short_title
        else violation.id
    )
    description = (
        _safe_ftl(violation.description)
        if violation.description
        else ""
    )
    impact = ftl_enum(violation.impact.value)

    # The anchor target is added as an inline HTML span so anchors keep
    # working when the markdown is rendered to HTML by GitHub or pandoc.
    out.append(
        f'<span id="issue-{anchor}"></span>**[{_level_label(level)}]** '
        + f"{short_title} — *{impact}*"
    )
    if description:
        out.append("")
        out.append(description)

    page = (
        violation.metadata.get("pdf_page") if violation.metadata else None
    )
    if page is not None:
        out.append("")
        out.append(f"_{_safe_ftl('pdf-jump-to-page', page=page)}_")

    sections = [
        ("pdf-violation-section-what", violation.what),
        ("pdf-violation-section-why", violation.why),
        ("pdf-violation-section-who", violation.who),
    ]
    for label_id, body in sections:
        if not body:
            continue
        translated = _safe_ftl(body)
        out.append("")
        out.append(f"**{_safe_ftl(label_id)}:** {translated}")

    if violation.wcag_criteria:
        out.append("")
        wcag_list = ", ".join(violation.wcag_criteria)
        out.append(f"**{_safe_ftl('pdf-violation-section-wcag')}:** {wcag_list}")

    if violation.remediation:
        out.append("")
        out.append(f"**{_safe_ftl('pdf-violation-section-remediation')}:**")
        out.append("")
        out.append("```")
        out.append(_safe_ftl(violation.remediation))
        out.append("```")

    technical = (
        violation.metadata.get("pdfmax_original_details")
        if violation.metadata
        else None
    )
    if technical:
        out.append("")
        out.append(f"**{_safe_ftl('pdf-violation-section-technical')}:**")
        out.append("")
        out.append("```")
        out.append(str(technical))
        out.append("```")

    return out


# ---------------------------------------------------------------------------
# HTML export
# ---------------------------------------------------------------------------


def build_html_report(
    pdf: PdfDocument,
    test_result: TestResult | None,
    locale: str = "en",
) -> str:
    """Build a self-contained HTML audit report.

    Returns a complete ``<!DOCTYPE html>`` document with no external
    dependencies — inlined CSS, no JS, no images. Mirrors the visual
    structure of the on-screen detail page but flattens the
    interactive filters into a static dump (every finding is shown).
    """
    with force_locale(locale):
        return _build_html(pdf, test_result)


def _build_html(
    pdf: PdfDocument,
    test_result: TestResult | None,
) -> str:
    lang_attr = pdf.declared_lang or pdf.detected_lang or "en"
    title = pdf.original_filename or pdf.id or "PDF"

    body_parts: list[str] = []
    body_parts.append(
        '<a class="skip-link" href="#report-content">'
        + f"{escape(_safe_ftl('common-skip-to-main-content'))}</a>"
    )
    body_parts.append('<main id="report-content">')
    body_parts.append("<header>")
    body_parts.append(f"<h1>{escape(title)}</h1>")
    body_parts.append(
        f"<p class='subtitle'>{escape(_safe_ftl('pdf-detail-results-heading'))}</p>"
    )
    body_parts.append(_html_metadata_block(pdf))
    body_parts.append("</header>")

    if test_result is None:
        body_parts.append(
            "<p class='not-audited'>"
            + f"{escape(_safe_ftl('pdf-result-summary-not-audited'))}</p>"
        )
    else:
        body_parts.append(_html_overview(test_result))
        levelled = _collect_findings(test_result)
        body_parts.append(_html_findings(levelled))
        body_parts.append(_html_wcag_appendix(_wcag_index(levelled)))
        body_parts.append(_html_touchpoint_appendix(levelled))

    body_parts.append("<footer>")
    body_parts.append(
        f"<p>{escape(_safe_ftl('pdf-report-export-footer'))}</p>"
    )
    body_parts.append("</footer>")
    body_parts.append("</main>")

    return (
        "<!DOCTYPE html>\n"
        + f"<html lang=\"{escape(lang_attr, quote=True)}\">\n"
        + "<head>\n"
        + "<meta charset=\"UTF-8\">\n"
        + "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
        + f"<title>{escape(title)} — "
        + f"{escape(_safe_ftl('pdf-detail-results-heading'))}</title>\n"
        + f"<style>{_EXPORT_STYLES}</style>\n"
        + "</head>\n"
        + "<body>\n"
        + "\n".join(body_parts)
        + "\n</body>\n</html>\n"
    )


def _html_metadata_block(pdf: PdfDocument) -> str:
    rows: list[tuple[str, str]] = [
        (_safe_ftl("pdf-meta-page-count"),
         _format_meta_value(pdf.page_count)),
        (_safe_ftl("pdf-meta-pdf-version"),
         _format_meta_value(pdf.pdf_version)),
        (_safe_ftl("pdf-meta-declared-language"),
         _format_meta_value(pdf.declared_lang)),
        (_safe_ftl("pdf-meta-detected-language"),
         _format_meta_value(pdf.detected_lang)),
        (_safe_ftl("pdf-meta-last-audited-at"),
         _format_meta_value(pdf.last_audited_at)),
    ]
    items = "\n".join(
        f"<dt>{escape(k)}</dt><dd>{escape(v)}</dd>"
        for k, v in rows
    )
    return f"<dl class='metadata'>\n{items}\n</dl>"


def _html_overview(test_result: TestResult) -> str:
    fail_count = len(test_result.violations)
    warn_count = len(test_result.warnings)
    info_count = len(test_result.info)
    pass_count = len(test_result.passes)

    if fail_count > 0:
        verdict_label = "pdf-report-overview-conformance-fail"
        verdict_class = "verdict-fail"
        verdict = _safe_ftl(verdict_label, count=fail_count)
    elif warn_count > 0:
        verdict_label = "pdf-report-overview-conformance-warn"
        verdict_class = "verdict-warn"
        verdict = _safe_ftl(verdict_label, count=warn_count)
    else:
        verdict_class = "verdict-pass"
        verdict = _safe_ftl("pdf-report-overview-conformance-pass")

    return (
        "<section aria-labelledby='overview-heading'>"
        + "<h2 id='overview-heading'>"
        + f"{escape(_safe_ftl('pdf-report-overview-heading'))}</h2>"
        + "<ul class='stats'>"
        + f"<li class='stat-fail'><span class='value'>{fail_count}</span>"
        + "<span class='label'>"
        + f"{escape(_safe_ftl('pdf-report-overview-errors'))}</span></li>"
        + f"<li class='stat-warn'><span class='value'>{warn_count}</span>"
        + "<span class='label'>"
        + f"{escape(_safe_ftl('pdf-report-overview-warnings'))}</span></li>"
        + f"<li class='stat-info'><span class='value'>{info_count}</span>"
        + "<span class='label'>"
        + f"{escape(_safe_ftl('pdf-report-overview-info'))}</span></li>"
        + f"<li class='stat-pass'><span class='value'>{pass_count}</span>"
        + "<span class='label'>"
        + f"{escape(_safe_ftl('pdf-report-overview-passes'))}</span></li>"
        + "</ul>"
        + f"<p class='verdict {verdict_class}'>{escape(verdict)}</p>"
        + "</section>"
    )


def _html_findings(levelled: list[tuple[str, Violation]]) -> str:
    if not levelled:
        return (
            "<section aria-labelledby='findings-heading'>"
            + "<h2 id='findings-heading'>"
            + f"{escape(_safe_ftl('pdf-report-issues-heading'))}</h2>"
            + f"<p>{escape(_safe_ftl('pdf-report-issues-empty'))}</p>"
            + "</section>"
        )

    groups = _group_by_touchpoint(levelled)
    parts: list[str] = []
    parts.append("<section aria-labelledby='findings-heading'>")
    parts.append(
        "<h2 id='findings-heading'>"
        + f"{escape(_safe_ftl('pdf-report-issues-heading'))}</h2>"
    )
    anchor = 0
    for tp, items in groups:
        slug = tp.replace("_", "-")
        parts.append(f"<section aria-labelledby='group-{slug}'>")
        parts.append(
            f"<h3 id='group-{slug}'>"
            + f"{escape(_touchpoint_label(tp))}"
            + " <span class='count'>"
            + f"({len(items)})</span></h3>"
        )
        parts.append("<ol class='findings'>")
        for level, violation in items:
            anchor += 1
            parts.append(_html_violation(level, violation, anchor))
        parts.append("</ol>")
        parts.append("</section>")
    parts.append("</section>")
    return "\n".join(parts)


def _html_violation(
    level: str, violation: Violation, anchor: int
) -> str:
    short_title = (
        _safe_ftl(violation.short_title)
        if violation.short_title
        else violation.id
    )
    description = (
        _safe_ftl(violation.description)
        if violation.description
        else ""
    )
    parts: list[str] = []
    parts.append(
        f"<li id='issue-{anchor}' class='finding finding-{level}'>"
    )
    parts.append(
        "<p class='finding-meta'>"
        + f"<span class='badge badge-{level}'>"
        + f"{escape(_level_label(level))}</span> "
        + "<span class='badge badge-impact'>"
        + f"{escape(str(ftl_enum(violation.impact.value)))}</span>"
        + "</p>"
    )
    parts.append(f"<h4>{escape(short_title)}</h4>")
    if description:
        parts.append(f"<p>{escape(description)}</p>")

    page = (
        violation.metadata.get("pdf_page") if violation.metadata else None
    )
    if page is not None:
        parts.append(
            "<p class='page-ref'>"
            + f"{escape(_safe_ftl('pdf-jump-to-page', page=page))}</p>"
        )

    sections = [
        ("pdf-violation-section-what", violation.what),
        ("pdf-violation-section-why", violation.why),
        ("pdf-violation-section-who", violation.who),
    ]
    for label_id, body in sections:
        if not body:
            continue
        parts.append(
            f"<p><strong>{escape(_safe_ftl(label_id))}:</strong> "
            + f"{escape(_safe_ftl(body))}</p>"
        )

    if violation.wcag_criteria:
        items = "".join(
            f"<li>{escape(c)}</li>" for c in violation.wcag_criteria
        )
        parts.append(
            "<p class='wcag-label'><strong>"
            + f"{escape(_safe_ftl('pdf-violation-section-wcag'))}:"
            + f"</strong></p><ul class='wcag-list'>{items}</ul>"
        )

    if violation.remediation:
        parts.append(
            "<details class='remediation'><summary>"
            + f"{escape(_safe_ftl('pdf-violation-section-remediation'))}"
            + "</summary><pre>"
            + f"{escape(_safe_ftl(violation.remediation))}</pre></details>"
        )

    technical = (
        violation.metadata.get("pdfmax_original_details")
        if violation.metadata
        else None
    )
    if technical:
        parts.append(
            "<details class='technical'><summary>"
            + f"{escape(_safe_ftl('pdf-violation-section-technical'))}"
            + f"</summary><pre>{escape(str(technical))}</pre></details>"
        )

    parts.append("</li>")
    return "\n".join(parts)


def _html_wcag_appendix(
    rows: list[tuple[str, list[tuple[int, str]]]],
) -> str:
    parts: list[str] = []
    parts.append("<section aria-labelledby='wcag-heading'>")
    parts.append(
        "<h2 id='wcag-heading'>"
        + f"{escape(_safe_ftl('pdf-report-appendix-wcag-heading'))}</h2>"
    )
    if not rows:
        parts.append(
            f"<p>{escape(_safe_ftl('pdf-report-appendix-wcag-empty'))}</p>"
        )
        parts.append("</section>")
        return "\n".join(parts)
    parts.append("<table>")
    parts.append("<thead><tr>")
    parts.append(
        "<th scope='col'>"
        + f"{escape(_safe_ftl('pdf-report-appendix-wcag-col-criterion'))}</th>"
    )
    parts.append(
        "<th scope='col'>"
        + f"{escape(_safe_ftl('pdf-report-appendix-wcag-col-count'))}</th>"
    )
    parts.append(
        "<th scope='col'>"
        + f"{escape(_safe_ftl('pdf-report-appendix-wcag-col-issues'))}</th>"
    )
    parts.append("</tr></thead><tbody>")
    for criterion, entries in rows:
        issue_links = ", ".join(
            f'<a href="#issue-{anchor}">{escape(label)}</a>'
            for anchor, label in entries
        )
        parts.append(
            f"<tr><th scope='row'>{escape(criterion)}</th>"
            + f"<td>{len(entries)}</td><td>{issue_links}</td></tr>"
        )
    parts.append("</tbody></table>")
    parts.append("</section>")
    return "\n".join(parts)


def _html_touchpoint_appendix(
    levelled: list[tuple[str, Violation]],
) -> str:
    parts: list[str] = []
    parts.append("<section aria-labelledby='touchpoints-heading'>")
    parts.append(
        "<h2 id='touchpoints-heading'>"
        + f"{escape(_safe_ftl('pdf-report-appendix-touchpoints-heading'))}"
        + "</h2>"
    )
    parts.append("<table>")
    parts.append("<thead><tr>")
    parts.append(
        "<th scope='col'>"
        + f"{escape(_safe_ftl('pdf-report-appendix-touchpoints-col-name'))}</th>"
    )
    parts.append(
        "<th scope='col'>"
        + f"{escape(_safe_ftl('pdf-report-appendix-touchpoints-col-errors'))}</th>"
    )
    parts.append(
        "<th scope='col'>"
        + f"{escape(_safe_ftl('pdf-report-appendix-touchpoints-col-warnings'))}</th>"
    )
    parts.append(
        "<th scope='col'>"
        + f"{escape(_safe_ftl('pdf-report-appendix-touchpoints-col-info'))}</th>"
    )
    parts.append(
        "<th scope='col'>"
        + f"{escape(_safe_ftl('pdf-report-appendix-touchpoints-col-total'))}</th>"
    )
    parts.append("</tr></thead><tbody>")
    for tp, items in _group_by_touchpoint(levelled):
        tp_fail = sum(1 for level, _ in items if level == "fail")
        tp_warn = sum(1 for level, _ in items if level == "warn")
        tp_info = sum(1 for level, _ in items if level == "info")
        parts.append(
            f"<tr><th scope='row'>{escape(_touchpoint_label(tp))}</th>"
            + f"<td>{tp_fail}</td><td>{tp_warn}</td>"
            + f"<td>{tp_info}</td><td><strong>{len(items)}</strong></td></tr>"
        )
    parts.append("</tbody></table>")
    parts.append("</section>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _collect_findings(
    test_result: TestResult,
) -> list[tuple[str, Violation]]:
    """Flatten violations/warnings/info into a single ordered list."""
    out: list[tuple[str, Violation]] = []
    for v in test_result.violations:
        out.append(("fail", v))
    for v in test_result.warnings:
        out.append(("warn", v))
    for v in test_result.info:
        out.append(("info", v))
    return out


def _group_by_touchpoint(
    levelled: list[tuple[str, Violation]],
) -> list[tuple[str, list[tuple[str, Violation]]]]:
    """Group findings by touchpoint, preserving discovery order."""
    keys: list[str] = []
    groups: dict[str, list[tuple[str, Violation]]] = {}
    for level, violation in levelled:
        tp = violation.touchpoint or "other"
        if tp not in groups:
            keys.append(tp)
            groups[tp] = []
        groups[tp].append((level, violation))
    return [(k, groups[k]) for k in keys]


def _wcag_index(
    levelled: list[tuple[str, Violation]],
) -> list[tuple[str, list[tuple[int, str]]]]:
    """Build a sorted ``[(criterion, [(anchor, label), ...]), ...]`` index."""
    index: dict[str, list[tuple[int, str]]] = {}
    anchor = 0
    for _level, violation in levelled:
        anchor += 1
        label = (
            _safe_ftl(violation.short_title)
            if violation.short_title
            else violation.id
        )
        for criterion in (violation.wcag_criteria or []):
            index.setdefault(criterion, []).append((anchor, label))
    return sorted(index.items(), key=lambda item: item[0])


# ---------------------------------------------------------------------------
# Inlined export stylesheet
# ---------------------------------------------------------------------------
#
# The values here intentionally avoid the app's design tokens — the
# export must render correctly when opened from a downloaded file with
# no access to the app's stylesheets. WCAG 2.2 AA is met by all
# foreground/background pairs (verified manually against the listed
# colour values).

_EXPORT_STYLES = """
* { box-sizing: border-box; }
body { font-family: system-ui, -apple-system, sans-serif; margin: 0; padding: 0; line-height: 1.6; color: #1a1a1a; background: #fff; }
.skip-link { position: absolute; left: -9999px; top: 0; z-index: 100; padding: 0.5rem 1rem; background: #0550a0; color: #fff; text-decoration: none; font-weight: 600; }
.skip-link:focus { left: 0.5rem; top: 0.5rem; }
main { max-width: 60rem; margin: 0 auto; padding: 1.5rem; }
header { margin-bottom: 1.5rem; }
header .subtitle { color: #4b5563; margin-top: 0.25rem; }
h1 { font-size: 1.75rem; margin: 0; }
h2 { font-size: 1.35rem; color: #0550a0; margin-top: 2rem; padding-bottom: 0.25rem; border-bottom: 1px solid #e5e7eb; }
h3 { font-size: 1.1rem; margin-top: 1.5rem; }
h4 { font-size: 1rem; margin: 0.5rem 0 0.25rem; }
dl.metadata { display: grid; grid-template-columns: max-content 1fr; gap: 0.25rem 1rem; margin: 0.75rem 0; }
dl.metadata dt { font-weight: 600; color: #4b5563; }
dl.metadata dd { margin: 0; }
ul.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(7rem, 1fr)); gap: 0.75rem; padding: 0; list-style: none; }
ul.stats li { display: flex; flex-direction: column; align-items: center; padding: 0.75rem; border: 1px solid #d1d5db; border-radius: 0.5rem; }
ul.stats .value { font-size: 1.75rem; font-weight: 700; line-height: 1; }
ul.stats .label { margin-top: 0.25rem; font-size: 0.85rem; color: #4b5563; text-transform: uppercase; letter-spacing: 0.04em; }
ul.stats .stat-fail { border-color: #b91c1c; }
ul.stats .stat-warn { border-color: #b45309; }
ul.stats .stat-info { border-color: #1e40af; }
ul.stats .stat-pass { border-color: #166534; }
.verdict { padding: 0.75rem 1rem; border-radius: 0.5rem; margin: 1rem 0; }
.verdict-fail { background: #fef2f2; border: 1px solid #b91c1c; color: #7f1d1d; }
.verdict-warn { background: #fffbeb; border: 1px solid #b45309; color: #7c2d12; }
.verdict-pass { background: #f0fdf4; border: 1px solid #166534; color: #14532d; }
ol.findings { list-style: none; padding: 0; }
li.finding { border: 1px solid #e5e7eb; border-left-width: 4px; border-radius: 0.5rem; padding: 0.75rem 1rem; margin-bottom: 0.75rem; background: #fff; }
li.finding-fail { border-left-color: #b91c1c; }
li.finding-warn { border-left-color: #b45309; }
li.finding-info { border-left-color: #1e40af; }
.finding-meta { margin: 0 0 0.25rem 0; font-size: 0.85rem; }
.badge { display: inline-block; padding: 0.1rem 0.5rem; border-radius: 0.25rem; font-weight: 600; font-size: 0.78rem; margin-right: 0.25rem; }
.badge-fail { background: #fee2e2; color: #7f1d1d; }
.badge-warn { background: #fef3c7; color: #7c2d12; }
.badge-info { background: #dbeafe; color: #1e3a8a; }
.badge-impact { background: #f3f4f6; color: #1f2937; }
.wcag-list { list-style: none; padding: 0; display: flex; flex-wrap: wrap; gap: 0.25rem 0.75rem; }
.wcag-list li { background: #f3f4f6; color: #1f2937; padding: 0.1rem 0.5rem; border-radius: 0.25rem; font-size: 0.8rem; }
.wcag-label { margin-bottom: 0.25rem; }
details { margin-top: 0.5rem; }
details summary { cursor: pointer; font-weight: 600; color: #0550a0; }
details pre { background: #f3f4f6; border: 1px solid #e5e7eb; border-radius: 0.25rem; padding: 0.75rem; font-size: 0.85rem; overflow-x: auto; white-space: pre-wrap; }
table { border-collapse: collapse; width: 100%; margin: 0.5rem 0; }
th, td { border: 1px solid #e5e7eb; padding: 0.5rem 0.75rem; text-align: left; vertical-align: top; }
th { background: #f3f4f6; font-weight: 600; }
.count { color: #4b5563; font-weight: 400; font-size: 0.9em; }
footer { margin-top: 2rem; padding-top: 1rem; border-top: 1px solid #e5e7eb; font-size: 0.85rem; color: #4b5563; }
@media print {
  .skip-link { display: none; }
  main { max-width: none; padding: 0; }
  li.finding, table { break-inside: avoid; }
  h2, h3 { break-after: avoid; }
}
""".strip()
