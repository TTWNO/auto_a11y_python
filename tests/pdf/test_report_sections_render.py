"""Rendering guards for ``pdf/_report_sections.html``.

The AI passes are the expensive part of an audit and, until this landed,
the only thing they moved was the pass/fail count: six sections' worth of
findings were produced, stored, and rendered by nothing. These tests
render the partial against a payload shaped like a real audit's and
assert each section reaches the page — in both languages, since a French
reader gets a French report or the section may as well not be there.

They also pin the shape flip that caused the original bug: without AI,
``images_of_text`` is a placeholder saying "AI required"; with AI it is
the vision pass's own payload, which has no ``available`` key at all. A
template testing the wrong key renders "not configured" on top of real
findings.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from flask import Flask, render_template

from auto_a11y.web.fluent import force_locale, init_fluent

REPO_ROOT = Path(__file__).resolve().parents[2]


def _app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(REPO_ROOT / "auto_a11y" / "web" / "templates"),
    )
    init_fluent(app)
    return app


def _render(sections: dict[str, object], locale: str = "en") -> str:
    """Both partials, in the order every page includes them.

    The front matter — metadata, executive summary, charts — is a
    separate include so it can lead the report rather than trail the
    inventories, so a test that renders only the inventories would miss
    half of what these assertions are about.
    """
    app = _app()
    with app.test_request_context("/"), force_locale(locale):
        return render_template(
            "pdf/_report_front_matter.html", report_sections=sections,
        ) + render_template(
            "pdf/_report_sections.html",
            report_sections=sections,
            image_url_prefix="/images/",
        )


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------


def _non_text_contrast() -> dict[str, Any]:
    return {
        "available": True,
        "fields": [{
            "field_name": "surname", "field_type": "Text", "page": 1,
            "border_color": "#cccccc", "border_source": "/MK/BC",
            "field_bg_color": "#ffffff", "page_bg_color": "#ffffff",
            "border_contrast": 1.61, "boundary_contrast": 1.0,
            "border_pass": False, "boundary_pass": False,
            "exempt_reason": "", "status": "border_fail",
        }],
        "graphics": [{
            "category": "divider_line", "page": 2,
            "element_color": "#eeeeee", "page_bg_color": "#ffffff",
            "color_source": "stroke", "contrast_ratio": 1.13,
            "contrast_pass": False, "linewidth": 1.0,
            "description": "Divider Line on page 2",
        }],
        "field_summary": {
            "total_fields": 1, "fields_with_borders": 1, "border_fails": 1,
            "boundary_only_fails": 1, "boundary_info": 1, "exempt_count": 0,
        },
        "graphic_summary": {
            "total_elements": 1, "table_borders": 0, "divider_lines": 1,
            "chart_elements": 0, "other_graphics": 0, "total_fails": 1,
        },
    }


def _required_fields() -> dict[str, Any]:
    return {
        "available": True,
        "has_legend": True,
        "legend_text": "Fields marked with * are required",
        "fields": [{
            "name": "surname", "tooltip": "Surname *", "field_type": "Text",
            "page": 1, "has_indicator": True,
            "indicator_detail": "* in name/tooltip",
        }],
        "missing_count": 0,
        "total": 1,
    }


def _ai_sections() -> dict[str, Any]:
    return {
        "ai_executive_summary": {
            "overall_rating": "needs work",
            "headline": "Most of the structure is sound.",
            "plain_language_summary": "Three fixes would make this usable.",
            "key_strengths": ["Headings are tagged"],
            "priority_actions": ["Add alt text to the cover image"],
            "who_is_affected": "Screen reader users",
            "estimated_effort": "About an hour",
        },
        "ai_semantic": {
            "overall_assessment": "Two paragraphs read as headings.",
            "issues": [{
                "severity": "important",
                "title": "Unmarked heading",
                "elements": "[7]",
                "problem": "Large bold text tagged as a paragraph.",
                "impact": "Nobody can jump to this section.",
                "recommendation": "Tag it as H2.",
            }],
        },
        "alt_text_adequacy": {
            "total": 2, "inadequate": 1, "errors": 0,
            "assessments": [{
                "element_index": 12, "alt_text": "image",
                "adequate": False, "reason": "Says nothing about the image.",
                "suggestion": "Bar chart: complaints fell by half.",
            }],
        },
        "images_of_text": {
            "total_findings": 1,
            "images": [{
                "filename": "img_001.png",
                "text": "Annual Report 2026",
                "in_alt_text": False,
            }],
            "pages": [{"page": 1, "finding": "Title is set as a graphic."}],
        },
        "color_use": {
            "overall_assessment": "Status is shown by colour alone.",
            "findings": [{
                "severity": "fail", "page": 3, "category": "status",
                "description": "Red and green rows carry the status.",
                "what_color_conveys": "Whether a claim was approved.",
                "recommendation": "Add a text label to each row.",
            }],
        },
        "non_text_contrast_ai": {
            "overall_assessment": "Field borders are hard to see.",
            "pass": False,
            "findings": [{
                "category": "form_field_border", "severity": "fail", "page": 1,
                "description": "The surname field has no visible border.",
                "recommendation": "Darken the border to at least 3:1.",
            }],
        },
        "required_indicators": {
            "has_legend": True, "legend_text": "…", "pass": False,
            "field_results": [{
                "field_name": "surname", "has_visual_indicator": True,
                "indicator_type": "asterisk", "indicator_description": "*",
            }],
            "visual_only_required": [
                {"description": "Postal code has an asterisk", "page": 1},
            ],
            "overall_assessment": "One field is marked only visually.",
        },
    }


def _full() -> dict[str, Any]:
    return {
        "non_text_contrast": _non_text_contrast(),
        "required_field_indicators": _required_fields(),
        **_ai_sections(),
    }


# ---------------------------------------------------------------------------
# Every AI section reaches the page
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("anchor", [
    "section-non_text_contrast",
    "section-required_field_indicators",
    "section-alt_text_adequacy",
    "section-color_use",
    "section-ai_semantic",
    "section-images_of_text",
])
def test_each_section_renders_its_anchor(anchor: str) -> None:
    """Anchors are what the sidebar links to; a missing one is a dead link."""
    assert f'id="{anchor}"' in _render(_full())


def test_the_ai_executive_summary_renders() -> None:
    html = _render(_full())

    assert "Most of the structure is sound." in html
    assert "Add alt text to the cover image" in html
    assert "About an hour" in html


def test_measured_contrast_numbers_reach_the_page() -> None:
    """The ratios are the part a remediator acts on."""
    html = _render(_full())

    assert "1.61:1" in html
    assert "#cccccc" in html
    assert "1.13:1" in html


def test_ai_contrast_findings_render_alongside_the_measurements() -> None:
    """Both halves, not one replacing the other."""
    html = _render(_full())

    assert "The surname field has no visible border." in html
    assert "1.61:1" in html


def test_alt_text_suggestions_render() -> None:
    html = _render(_full())

    assert "Bar chart: complaints fell by half." in html
    assert "Says nothing about the image." in html


def test_colour_findings_render_with_their_recommendation() -> None:
    html = _render(_full())

    assert "Red and green rows carry the status." in html
    assert "Add a text label to each row." in html


def test_semantic_issues_render_with_element_references() -> None:
    html = _render(_full())

    assert "Unmarked heading" in html
    assert "[7]" in html
    assert "Tag it as H2." in html


def test_visual_only_required_fields_render() -> None:
    html = _render(_full())

    assert "Postal code has an asterisk" in html


# ---------------------------------------------------------------------------
# The images-of-text shape flip
# ---------------------------------------------------------------------------


def test_images_of_text_placeholder_says_ai_is_required() -> None:
    html = _render({"images_of_text": {"available": False,
                                       "reason": "ai_not_configured"}})

    assert "AI" in html
    assert "img_001.png" not in html


def test_images_of_text_with_results_does_not_say_not_configured() -> None:
    """The bug this pins: the AI payload has no ``available`` key."""
    html = _render({"images_of_text": _ai_sections()["images_of_text"]})

    assert "img_001.png" in html
    assert "Annual Report 2026" in html
    assert "not configured" not in html.lower()


# ---------------------------------------------------------------------------
# Absent data
# ---------------------------------------------------------------------------


def test_unmeasured_contrast_says_so_rather_than_vanishing() -> None:
    html = _render({"non_text_contrast": {"available": False, "fields": [],
                                          "graphics": []}})

    assert 'id="section-non_text_contrast"' in html
    assert "could not be measured" in html


def test_a_form_with_no_required_fields_says_so() -> None:
    html = _render({"required_field_indicators": {"available": False,
                                                  "fields": [],
                                                  "has_legend": False}})

    assert 'id="section-required_field_indicators"' in html
    assert "/Ff Required" in html


def test_ai_sections_are_absent_when_ai_did_not_run() -> None:
    """No empty AI headings on a scan the user did not pay for."""
    html = _render({"non_text_contrast": _non_text_contrast()})

    for anchor in ("section-ai_semantic", "section-alt_text_adequacy",
                   "section-color_use"):
        assert f'id="{anchor}"' not in html


# ---------------------------------------------------------------------------
# French
# ---------------------------------------------------------------------------


def test_every_new_section_renders_in_french() -> None:
    html = _render(_full(), locale="fr")

    assert "Contraste des éléments non textuels" in html
    assert "Repères des champs obligatoires" in html
    assert "Pertinence du texte de remplacement" in html
    assert "Usage de la couleur" in html
    assert "Analyse sémantique par IA" in html
    assert "Résumé de direction par IA" in html


def test_no_message_id_leaks_into_the_french_render() -> None:
    """Fluent returns the id itself when a string is missing.

    The ids share a prefix with the CSS class names, so the text is
    checked with the attributes stripped out — otherwise every section's
    own ``class="pdf-inventory-section"`` reads as a failure.
    """
    html = _render(_full(), locale="fr")
    text = re.sub(r'\s(?:class|id|href|aria-\w+)="[^"]*"', "", html)

    leaked = re.findall(r"pdf-inventory-[a-z0-9-]+", text)

    assert not leaked, f"untranslated message ids rendered: {sorted(set(leaked))}"


# ---------------------------------------------------------------------------
# Front matter: metadata first, and the summary charts
# ---------------------------------------------------------------------------


def _front(sections: dict[str, object], locale: str = "en") -> str:
    app = _app()
    with app.test_request_context("/"), force_locale(locale):
        return render_template(
            "pdf/_report_front_matter.html", report_sections=sections,
        )


def _summary(**counts: object) -> dict[str, object]:
    base: dict[str, object] = {
        "fail_count": 13, "warn_count": 10, "info_count": 0,
        "pass_count": 103, "na_count": 0, "total_checks": 126,
        "top_issues": [], "verdict": "FAIL",
        "areas": [
            {"area": "forms", "fail": 3, "warn": 1, "total": 4},
            {"area": "structure", "fail": 1, "warn": 1, "total": 2},
        ],
    }
    base.update(counts)
    return base


def test_the_metadata_section_is_in_the_front_matter() -> None:
    """It leads the report; it used to render below every inventory,
    while the sidebar listed it first."""
    html = _front({"document_metadata": {"author": "A", "pages": 3}})

    assert 'id="pdf-inventory-doc-metadata-heading"' in html


def test_the_metadata_section_is_not_in_the_inventories() -> None:
    """Rendered once, at the top — not in both partials."""
    app = _app()
    with app.test_request_context("/"), force_locale("en"):
        inventories = render_template(
            "pdf/_report_sections.html",
            report_sections={"document_metadata": {"author": "A"}},
            image_url_prefix="/i/",
        )

    assert "pdf-inventory-doc-metadata-heading" not in inventories


def test_the_donut_shows_the_pass_percentage() -> None:
    html = _front({"executive_summary": _summary()})

    assert 'class="pdf-donut"' in html
    assert ">82%<" in html  # 103 of 126


def test_the_donut_segments_are_proportional() -> None:
    """Each arc's dash length is its share of the circumference.

    Counts only the value arcs — the boundary separators drawn over them
    also carry a dasharray.
    """
    html = _front({"executive_summary": _summary()})

    arcs = re.findall(
        r'class="pdf-donut-(?:pass|warn|fail)"[^>]*?'
        r'stroke-dasharray="([0-9.]+) ([0-9.]+)"',
        html,
        re.S,
    )
    assert len(arcs) == 3
    circumference = float(arcs[0][1])
    assert abs(sum(float(a[0]) for a in arcs) - circumference) < 0.5


def test_the_area_chart_lists_the_worst_area_first() -> None:
    html = _front({"executive_summary": _summary()})

    labels = re.findall(r'pdf-summary-bar-label">\s*([^<]+)', html)
    assert [label.strip() for label in labels] == ["Forms", "Structure"]


def test_the_donut_states_every_figure_in_its_alt_text() -> None:
    """It was aria-hidden, on the reasoning that the prose said the same.

    The prose says the verdict and the counts, so that held for the
    donut — but hiding a chart is only defensible when nothing is lost,
    and it is simpler to be right than to be arguably right. A labelled
    image costs nothing and answers the question outright.
    """
    html = _front({"executive_summary": _summary()})
    donut = html.split('class="pdf-donut"', 1)[1].split("</svg>", 1)[0]

    assert 'role="img"' in donut
    assert 'aria-hidden' not in donut
    label = re.search(r'aria-label="([^"]+)"', donut)
    assert label is not None
    for figure in ("82", "103", "10", "13"):
        assert figure in label.group(1), f"{figure} missing from the alt text"


def test_the_area_chart_is_a_table_not_a_picture() -> None:
    """This breakdown appears nowhere else in the report.

    Hiding it, as the first version did, removed information rather than
    avoiding repetition. Drawing the bars inside a real table means what
    a screen reader reads is the data itself.
    """
    html = _front({"executive_summary": _summary()})

    assert "<table" in html
    assert "<caption" in html
    assert 'scope="col"' in html and 'scope="row"' in html


def test_each_bar_sits_beside_its_number() -> None:
    """Length is a second encoding of the figure, never the only one."""
    html = _front({"executive_summary": _summary()})
    row = html.split('<tbody>', 1)[1].split('</tr>', 1)[0]

    assert 'aria-hidden="true"' in row, "the drawn bar is decoration"
    assert ">4<" in row, "the total is present as text"


def test_the_segment_boundaries_are_separated() -> None:
    """Adjacent severity colours are 1.03:1 to 1.5:1 against each other.

    Each is fine against the page, so where two segments meet there is
    no visible edge at all (SC 1.4.11).
    """
    html = _front({"executive_summary": _summary()})

    assert html.count("pdf-donut-divider") >= 3


def test_no_summary_charts_when_nothing_was_checked() -> None:
    html = _front({"executive_summary": _summary(
        fail_count=0, warn_count=0, pass_count=0, total_checks=0,
        verdict="NOT_TESTED", areas=[],
    )})

    assert "pdf-summary-charts" not in html


def test_an_area_with_no_issues_is_not_charted() -> None:
    """The chart is a list of things to fix; a zero row is noise."""
    html = _front({"executive_summary": _summary(areas=[])})

    assert "pdf-summary-bars" not in html


def test_the_area_labels_are_translated() -> None:
    html = _front({"executive_summary": _summary()}, locale="fr")

    assert "Formulaires" in html
    assert "Problèmes par domaine" in html
