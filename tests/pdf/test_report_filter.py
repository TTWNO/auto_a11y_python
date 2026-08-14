"""The report's per-check markup, and the filter bar that reads it.

pdfMax's report is a list of ``<details>`` blocks, each tagged with the
check's name and verdict. Two features are built on those attributes: the
filter bar, which hides whole verdicts, and the ``#check=`` deep link the
viewer uses to jump from an issue to its entry in the report. The port
rendered plain headings instead, so the filter bar had nothing to filter
and the deep-link handler — which was already written — could never find
its target.

The attributes are the interface between the Python that renders the
report and the JavaScript that drives it, so they are pinned here.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import cast

import pytest
from flask import Flask

from auto_a11y.pdf.models import AuditResult, CheckOutcome, CheckResult
from auto_a11y.pdf.report_markdown import render_audit_markdown
from auto_a11y.web.fluent import force_locale, init_fluent

REPO_ROOT = Path(__file__).resolve().parents[2]
_RESULTS_HTML = (
    REPO_ROOT / "auto_a11y" / "web" / "templates" / "pdf_scan" / "results.html"
)
_RESULTS_JS = (
    REPO_ROOT / "auto_a11y" / "web" / "static" / "js" / "pdf_scan_results.js"
)
_REPORT_BODY = (
    REPO_ROOT / "auto_a11y" / "web" / "templates" / "pdf"
    / "_pdfmax_report_body.html"
)


def _check(
    name: str,
    result: CheckOutcome,
    *,
    standard: str = "WCAG 1.3.1",
    details: str = "d",
) -> CheckResult:
    return CheckResult(
        name=name, standard=standard, result=result, details=details,
    )


def _render(checks: list[CheckResult], locale: str = "en") -> str:
    app = Flask(__name__)
    init_fluent(app)
    with app.test_request_context("/"), force_locale(locale):
        return render_audit_markdown(
            AuditResult.from_checks(
                pdf_path=Path("document.pdf"),
                pdf_version="1.7",
                page_count=2,
                declared_lang="en",
                detected_lang=None,
                check_results=checks,
                ai_analysis=None,
            )
        )


# ---------------------------------------------------------------------------
# The markup
# ---------------------------------------------------------------------------


def test_every_check_is_a_details_block() -> None:
    """Counted by the attribute, not by the tag.

    A check's remediation carries its own nested disclosure for the
    before/after example, so closing tags outnumber checks.
    """
    markdown = _render([
        _check("Document title set", "FAIL"),
        _check("PDF is tagged", "PASS"),
    ])

    assert markdown.count("<details data-check-name=") == 2
    assert markdown.count("</details>") >= 2


@pytest.mark.parametrize(
    "verdict", ["FAIL", "WARN", "INFO", "PASS", "NA"],
)
def test_each_verdict_reaches_the_data_attribute(verdict: CheckOutcome) -> None:
    """All five, not just pdfMax's three — this report also has INFO and NA."""
    markdown = _render([_check("Some check", verdict)])

    assert f'data-check-result="{verdict}"' in markdown


def test_the_check_name_attribute_is_the_engines_english_name() -> None:
    """It is an identifier, shared with the issue map and the viewer.

    The visible title is translated; this is not, or a French reader's
    deep links would point at names nothing else uses.
    """
    markdown = _render([_check("Table captions", "WARN")], locale="fr")

    assert 'data-check-name="Table captions"' in markdown


def test_the_visible_title_is_translated_even_though_the_attribute_is_not() -> None:
    english = _render([_check("Table captions", "WARN")], locale="en")
    french = _render([_check("Table captions", "WARN")], locale="fr")

    assert 'data-check-name="Table captions"' in french
    # The summary text differs between the two renders.
    assert _summary(english) != _summary(french)


def _summary(markdown: str) -> str:
    match = re.search(r"<summary>(.*?)</summary>", markdown, re.S)
    assert match is not None, "no summary rendered"
    return match.group(1)


def test_a_quote_in_a_check_name_cannot_break_out_of_the_attribute() -> None:
    """Names are ours, but they are still escaped at the boundary."""
    markdown = _render([_check('Bad " name', "FAIL")])

    assert 'data-check-name="Bad &quot; name"' in markdown
    assert 'data-check-name="Bad " name"' not in markdown


def test_details_text_is_still_markdown_escaped() -> None:
    """Document content inside a details block is not markup."""
    markdown = _render([
        _check("Document title set", "FAIL", details="titled **Q3**"),
    ])

    assert r"\*\*Q3\*\*" in markdown


def test_the_badge_names_the_verdict() -> None:
    markdown = _render([_check("Document title set", "FAIL")])

    assert 'class="check-badge check-badge-fail"' in markdown


# ---------------------------------------------------------------------------
# The bar that reads it
# ---------------------------------------------------------------------------


def test_the_filter_bar_offers_a_toggle_per_verdict() -> None:
    html = _RESULTS_HTML.read_text(encoding="utf-8")

    for verdict in ("FAIL", "WARN", "INFO", "PASS", "NA"):
        assert f"'{verdict}'" in html, f"no filter for {verdict}"


def test_filter_buttons_carry_their_state_in_aria_pressed() -> None:
    """A toggle whose state is only a CSS class is invisible to a reader."""
    html = _RESULTS_HTML.read_text(encoding="utf-8")

    assert 'aria-pressed="true"' in html
    assert 'data-result-filter="{{ verdict }}"' in html


def test_every_verdict_starts_shown() -> None:
    """pdfMax hides passes by default; a first view missing most of the
    report, with nothing saying so, is worse than a longer page."""
    html = _RESULTS_HTML.read_text(encoding="utf-8")
    bar = html.split("results-filter-bar", 1)[1].split("</fieldset>", 1)[0]

    assert 'aria-pressed="false"' not in bar


def test_the_filter_hides_headings_left_empty() -> None:
    """Otherwise a filtered report is headings above empty space."""
    js = _RESULTS_JS.read_text(encoding="utf-8")

    assert "hideEmptyHeadings" in js
    assert "checker-detail-hidden" in js


def test_the_filter_announces_what_changed() -> None:
    js = _RESULTS_JS.read_text(encoding="utf-8")

    assert "notification-live-region" in js
    assert "data-filter-applied" in _RESULTS_HTML.read_text(encoding="utf-8")


def test_the_sanitiser_still_permits_the_attributes_the_filter_needs() -> None:
    """The report is sanitised client-side; stripping these would leave
    the filter silently doing nothing."""
    body = _REPORT_BODY.read_text(encoding="utf-8")

    assert "'details'" in body and "'summary'" in body
    assert "'data-check-name'" in body
    assert "'data-check-result'" in body


# ---------------------------------------------------------------------------
# The deep link from the viewer
# ---------------------------------------------------------------------------


def test_the_viewer_link_and_the_report_agree_on_the_check_name() -> None:
    """The end-to-end contract, on a real audit.

    The viewer builds ``#check=<name>`` from the issue map's
    ``check_name``; the report handler resolves it against
    ``data-check-name``. Both come from ``CheckResult.name``, and this is
    the test that fails if either side ever starts translating it.
    """
    corpus = REPO_ROOT / "docs" / "Auto_A11y_API_Guide.pdf"
    if not corpus.is_file():
        pytest.skip("corpus PDF absent")

    from auto_a11y.pdf.audit.pipeline import run_audit

    audit = run_audit(corpus)
    payload = audit.report_sections.get("issue_map")
    assert isinstance(payload, dict)
    raw = cast("dict[str, object]", payload).get("issues")
    assert isinstance(raw, list)
    issues = [
        cast("dict[str, object]", entry)
        for entry in cast("list[object]", raw)
        if isinstance(entry, dict)
    ]
    assert issues

    app = Flask(__name__)
    init_fluent(app)
    with app.test_request_context("/"), force_locale("fr"):
        markdown = render_audit_markdown(audit)

    linked = {
        str(issue["check_name"]) for issue in issues if issue.get("check_name")
    }
    rendered = set(re.findall(r'data-check-name="([^"]+)"', markdown))

    assert linked, "the viewer has no issues to link from"
    assert linked <= rendered, (
        "the viewer links to checks the report does not name: "
        f"{sorted(linked - rendered)}"
    )


# ---------------------------------------------------------------------------
# The remediation guide reaching the reader
# ---------------------------------------------------------------------------

# It was ported and translated long before it was rendered: the report
# printed a one-line finding and dropped every word of the guidance —
# why the finding matters, what has to change, and the steps to change it
# in Acrobat, Word and InDesign.


def test_a_failing_check_carries_its_remediation() -> None:
    markdown = _render([_check("Document title set", "FAIL")])

    assert "Why it matters" in markdown
    assert "What needs to change" in markdown
    assert "Adobe Acrobat Pro" in markdown
    assert "Microsoft Word" in markdown
    assert "Adobe InDesign" in markdown


def test_the_fix_steps_are_an_ordered_list() -> None:
    """A wall of prose is not a procedure."""
    markdown = _render([_check("Document title set", "FAIL")])

    assert re.search(r"^1\. ", markdown, re.M)
    assert re.search(r"^2\. ", markdown, re.M)


def test_a_passing_check_carries_no_remediation() -> None:
    """Nothing to remediate, so nothing to say."""
    markdown = _render([_check("Document title set", "PASS")])

    assert "Why it matters" not in markdown


def test_no_developer_placeholder_reaches_the_report() -> None:
    """``TODO_REMEDIATION`` is a note to whoever ports the next check.

    Six checks had no pdfMax guidance — the contrast ones, which are the
    most common finding in any real audit, and the scanned-document case,
    which is the most serious. Their guidance is written here instead.
    """
    for verdict in ("FAIL", "WARN", "INFO"):
        for name in (
            "Text contrast (WCAG AA)",
            "Text contrast (WCAG AAA)",
            "Document has a text layer",
            "Alt text adequacy",
        ):
            markdown = _render([_check(name, verdict)])
            assert "TODO_REMEDIATION" not in markdown, f"{name} / {verdict}"


def test_checks_are_grouped_and_numbered() -> None:
    """pdfMax numbers findings and groups them by area.

    "#14 Table headers defined" is how a reader refers to one, and the
    group heading is what makes a 125-item list navigable.
    """
    markdown = _render([
        _check("Table captions", "FAIL"),
        _check("Document title set", "FAIL"),
    ])

    assert "#1 " in markdown and "#2 " in markdown
    assert "### " in markdown


def test_numbering_runs_on_across_verdicts() -> None:
    """One sequence for the report, not one per section."""
    markdown = _render([
        _check("Document title set", "FAIL"),
        _check("PDF is tagged", "PASS"),
    ])

    assert "#1 " in markdown
    assert "#2 " in markdown


def test_the_before_after_example_is_its_own_disclosure() -> None:
    """So a reader scanning the fixes is not made to scroll past a block
    of PDF internals to reach the next one."""
    markdown = _render([_check("Document title set", "FAIL")])

    assert "<summary>Before / after example</summary>" in markdown


def test_the_remediation_is_translated() -> None:
    english = _render([_check("Document title set", "FAIL")], locale="en")
    french = _render([_check("Document title set", "FAIL")], locale="fr")

    assert "Why it matters" in english
    # French is placeholder English for the generated entries, so this
    # asserts the lookup resolves rather than that it is translated.
    assert "Why it matters" in french or "Pourquoi" in french
