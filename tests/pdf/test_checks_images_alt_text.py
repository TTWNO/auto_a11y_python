"""Tests for ``auto_a11y.pdf.audit.checks.images_alt_text``.

Four image alt-text checks ported from pdfMax's
``pdf_accessibility_audit.py``:

* ``check_alt_text_on_figure_art`` (PDF/UA, WCAG 1.1.1) — line ~4761.
* ``check_alt_text_free_of_redundant_role_text``
  (WCAG 1.1.1 best practice) — line ~5206.
* ``check_alt_text_does_not_hide_interactive_elements``
  (PDF/UA, WCAG 4.1.2) — line ~5259.
* ``check_figure_elements_have_bbox`` (PDF/UA PAC 2024) — line ~7012.

Two AI-dependent checks are deferred to Phase 5.1 (semantic AI module):

* ``Alt text adequacy`` (WCAG 1.1.1) — pdfMax line ~4068.
* ``Images of text have matching alt text`` (WCAG 1.4.5) — pdfMax line
  ~3878. Requires Claude vision on extracted-image bytes plus full-page
  rendering.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf

from auto_a11y.pdf.audit.checks.images_alt_text import (
    IMAGES_ALT_TEXT_CHECKS,
    check_alt_text_does_not_hide_interactive_elements,
    check_alt_text_free_of_redundant_role_text,
    check_alt_text_on_figure_art,
    check_figure_elements_have_bbox,
)
from auto_a11y.pdf.audit.structure import StructElement
from auto_a11y.pdf.models import AuditContext, CheckResult


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _ctx_with_elements(elems: list[StructElement]) -> AuditContext:
    """Build a minimal ``AuditContext`` with just the structure elements.

    These checks read only ``ctx.elements`` so the other context fields
    can take throwaway values.
    """
    pdf = pikepdf.Pdf.new()
    return AuditContext(
        pdf=pdf,
        pdf_path=Path("/tmp/test.pdf"),
        elements=elems,
        role_map={},
    )


def _struct(
    index: int,
    tag: str,
    *,
    alt_text: str | None = None,
    actual_text: str | None = None,
    parent_index: int = -1,
    children_indices: list[int] | None = None,
    obj: pikepdf.Dictionary | None = None,
) -> StructElement:
    """Build a ``StructElement`` for any tag with optional alt-text/children.

    ``obj`` defaults to an empty Dictionary; callers pass a dictionary
    pre-populated with ``/BBox`` or ``/A`` entries when testing the
    BBox-attribute check.
    """
    return StructElement(
        index=index,
        custom_tag=f"/{tag}",
        resolved_tag=tag,
        alt_text=alt_text,
        actual_text=actual_text,
        lang=None,
        children_indices=children_indices if children_indices is not None else [],
        mcids=[],
        parent_index=parent_index,
        obj=obj if obj is not None else pikepdf.Dictionary(),
    )


def _only(results: list[CheckResult]) -> CheckResult:
    """Assert single-element list and return its sole entry."""
    assert len(results) == 1, f"expected one CheckResult, got {len(results)}"
    return results[0]


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------


def test_images_alt_text_checks_registry_lists_all_four_functions() -> None:
    """``IMAGES_ALT_TEXT_CHECKS`` is the phase-5 entry point."""
    assert IMAGES_ALT_TEXT_CHECKS == [
        check_alt_text_on_figure_art,
        check_alt_text_free_of_redundant_role_text,
        check_alt_text_does_not_hide_interactive_elements,
        check_figure_elements_have_bbox,
    ]


# ---------------------------------------------------------------------------
# check_alt_text_on_figure_art
# ---------------------------------------------------------------------------


def test_alt_text_on_figure_art_passes_with_no_figures() -> None:
    elems = [_struct(0, "Document"), _struct(1, "P", parent_index=0)]
    res = _only(check_alt_text_on_figure_art(_ctx_with_elements(elems)))
    assert res.name == "Alt text on all Figure/Art tags"
    assert res.standard == "PDF/UA, WCAG 1.1.1"
    assert res.result == "PASS"


def test_alt_text_on_figure_art_passes_when_all_figures_have_alt() -> None:
    elems = [
        _struct(0, "Figure", alt_text="A diagram"),
        _struct(1, "Art", alt_text="A photo"),
    ]
    res = _only(check_alt_text_on_figure_art(_ctx_with_elements(elems)))
    assert res.result == "PASS"
    assert "2 Figure/Art elements with alt text" in res.details


def test_alt_text_on_figure_art_fails_for_figure_missing_alt() -> None:
    elems = [_struct(0, "Figure")]
    res = _only(check_alt_text_on_figure_art(_ctx_with_elements(elems)))
    assert res.result == "FAIL"
    assert "1 Figure/Art elements missing alt text" in res.details


def test_alt_text_on_figure_art_excuses_art_with_text_children() -> None:
    """Art elements with P/Span/etc. children are structural containers."""
    elems = [
        _struct(0, "Art", children_indices=[1]),
        _struct(1, "P", parent_index=0),
    ]
    res = _only(check_alt_text_on_figure_art(_ctx_with_elements(elems)))
    assert res.result == "PASS"
    assert "1 Art containers with text children" in res.details


def test_alt_text_on_figure_art_blank_alt_text_treated_as_missing() -> None:
    """Whitespace-only alt text is not real alt text — pdfMax behaviour."""
    elems = [_struct(0, "Figure", alt_text="   ")]
    res = _only(check_alt_text_on_figure_art(_ctx_with_elements(elems)))
    assert res.result == "FAIL"


def test_alt_text_on_figure_art_mixed_pass_fail_includes_excluded_count() -> None:
    """When some figures fail, structural Art is still mentioned."""
    elems = [
        _struct(0, "Figure"),  # missing alt -> FAIL
        _struct(1, "Art", children_indices=[2]),  # structural -> excluded
        _struct(2, "P", parent_index=1),
    ]
    res = _only(check_alt_text_on_figure_art(_ctx_with_elements(elems)))
    assert res.result == "FAIL"
    assert "1 Art containers with text children excluded" in res.details


# ---------------------------------------------------------------------------
# check_alt_text_free_of_redundant_role_text
# ---------------------------------------------------------------------------


def test_redundant_role_text_passes_when_no_role_words_in_alt() -> None:
    elems = [
        _struct(0, "Figure", alt_text="A diagram of the solar system"),
        _struct(1, "Link", alt_text="Annual report PDF"),
    ]
    res = _only(
        check_alt_text_free_of_redundant_role_text(_ctx_with_elements(elems))
    )
    assert res.name == "Alt text free of redundant role text"
    assert res.standard == "WCAG 1.1.1 (best practice)"
    assert res.result == "PASS"


def test_redundant_role_text_warns_on_image_word() -> None:
    elems = [_struct(0, "Figure", alt_text="Image of a cat")]
    res = _only(
        check_alt_text_free_of_redundant_role_text(_ctx_with_elements(elems))
    )
    assert res.result == "WARN"
    assert "image of" in res.details
    assert "[1]" in res.details  # 1-based per pdfMax


def test_redundant_role_text_warns_on_link_word() -> None:
    elems = [_struct(0, "Link", alt_text="Click here link")]
    res = _only(
        check_alt_text_free_of_redundant_role_text(_ctx_with_elements(elems))
    )
    assert res.result == "WARN"


def test_redundant_role_text_warns_on_french_lien() -> None:
    elems = [_struct(0, "Link", alt_text="Cliquez ici lien")]
    res = _only(
        check_alt_text_free_of_redundant_role_text(_ctx_with_elements(elems))
    )
    assert res.result == "WARN"
    assert "lien" in res.details


def test_redundant_role_text_warns_on_button() -> None:
    elems = [_struct(0, "Figure", alt_text="Submit button")]
    res = _only(
        check_alt_text_free_of_redundant_role_text(_ctx_with_elements(elems))
    )
    assert res.result == "WARN"


def test_redundant_role_text_only_one_label_per_element() -> None:
    """pdfMax breaks after the first match per element — verify same."""
    elems = [_struct(0, "Figure", alt_text="link image button")]
    res = _only(
        check_alt_text_free_of_redundant_role_text(_ctx_with_elements(elems))
    )
    assert res.result == "WARN"
    # Only one issue line for this element.
    assert res.details.count("[1]") == 1


def test_redundant_role_text_skips_elements_without_alt() -> None:
    elems = [_struct(0, "P"), _struct(1, "Figure")]
    res = _only(
        check_alt_text_free_of_redundant_role_text(_ctx_with_elements(elems))
    )
    assert res.result == "PASS"


# ---------------------------------------------------------------------------
# check_alt_text_does_not_hide_interactive_elements
# ---------------------------------------------------------------------------


def test_alt_hides_interactive_passes_when_no_alt_parents_have_links() -> None:
    elems = [
        _struct(0, "Figure", alt_text="A photo"),
        _struct(1, "P", children_indices=[2]),
        _struct(2, "Link", parent_index=1),  # not under an alt-text parent
    ]
    res = _only(
        check_alt_text_does_not_hide_interactive_elements(
            _ctx_with_elements(elems)
        )
    )
    assert res.name == "Alt text does not hide interactive elements"
    assert res.standard == "PDF/UA, WCAG 4.1.2"
    assert res.result == "PASS"


def test_alt_hides_interactive_fails_when_link_under_alt_parent() -> None:
    elems = [
        _struct(0, "Figure", alt_text="Photo of a logo", children_indices=[1]),
        _struct(1, "Link", parent_index=0),
    ]
    res = _only(
        check_alt_text_does_not_hide_interactive_elements(
            _ctx_with_elements(elems)
        )
    )
    assert res.result == "FAIL"
    assert "[1] Figure" in res.details
    assert "Link" in res.details


def test_alt_hides_interactive_fails_for_deeply_nested_descendant() -> None:
    """Descendant search is recursive — a Link grandchild also counts."""
    elems = [
        _struct(0, "Figure", alt_text="Banner", children_indices=[1]),
        _struct(1, "Span", parent_index=0, children_indices=[2]),
        _struct(2, "Link", parent_index=1),
    ]
    res = _only(
        check_alt_text_does_not_hide_interactive_elements(
            _ctx_with_elements(elems)
        )
    )
    assert res.result == "FAIL"


def test_alt_hides_interactive_passes_when_alt_parent_has_no_children() -> None:
    elems = [_struct(0, "Figure", alt_text="A photo")]
    res = _only(
        check_alt_text_does_not_hide_interactive_elements(
            _ctx_with_elements(elems)
        )
    )
    assert res.result == "PASS"


def test_alt_hides_interactive_form_and_annot_also_count() -> None:
    """pdfMax flags Form and Annot tags as interactive too."""
    elems = [
        _struct(0, "Figure", alt_text="Form area", children_indices=[1]),
        _struct(1, "Form", parent_index=0),
    ]
    res = _only(
        check_alt_text_does_not_hide_interactive_elements(
            _ctx_with_elements(elems)
        )
    )
    assert res.result == "FAIL"


# ---------------------------------------------------------------------------
# check_figure_elements_have_bbox
# ---------------------------------------------------------------------------


def test_figure_bbox_is_not_applicable_with_no_figures() -> None:
    elems = [_struct(0, "P")]
    res = _only(check_figure_elements_have_bbox(_ctx_with_elements(elems)))
    assert res.name == "Figure elements have BBox attribute"
    assert res.standard == "PDF/UA (PAC 2024)"
    assert res.result == "NA"
    assert "No Figure elements" in res.details


def test_figure_bbox_passes_with_direct_bbox() -> None:
    obj = pikepdf.Dictionary({"/BBox": pikepdf.Array([0.0, 0.0, 100.0, 100.0])})
    elems = [_struct(0, "Figure", obj=obj)]
    res = _only(check_figure_elements_have_bbox(_ctx_with_elements(elems)))
    assert res.result == "PASS"


def test_figure_bbox_passes_with_bbox_inside_a_dict() -> None:
    """``/A`` may be a single attribute Dictionary holding ``/BBox``."""
    a_dict = pikepdf.Dictionary(
        {"/BBox": pikepdf.Array([0.0, 0.0, 50.0, 50.0])}
    )
    obj = pikepdf.Dictionary({"/A": a_dict})
    elems = [_struct(0, "Figure", obj=obj)]
    res = _only(check_figure_elements_have_bbox(_ctx_with_elements(elems)))
    assert res.result == "PASS"


def test_figure_bbox_passes_with_bbox_inside_a_array_of_dicts() -> None:
    """``/A`` may be an Array of attribute dicts; any one entry suffices."""
    a_arr = pikepdf.Array([
        pikepdf.Dictionary({"/O": pikepdf.Name("/Layout")}),
        pikepdf.Dictionary({"/BBox": pikepdf.Array([0.0, 0.0, 10.0, 10.0])}),
    ])
    obj = pikepdf.Dictionary({"/A": a_arr})
    elems = [_struct(0, "Figure", obj=obj)]
    res = _only(check_figure_elements_have_bbox(_ctx_with_elements(elems)))
    assert res.result == "PASS"


def test_figure_bbox_fails_when_no_bbox_anywhere() -> None:
    elems = [_struct(0, "Figure")]  # empty obj, no /BBox, no /A
    res = _only(check_figure_elements_have_bbox(_ctx_with_elements(elems)))
    assert res.result == "FAIL"
    assert "1 of 1 Figure element(s) missing /BBox" in res.details


def test_figure_bbox_fail_lists_indices() -> None:
    obj_with = pikepdf.Dictionary(
        {"/BBox": pikepdf.Array([0.0, 0.0, 1.0, 1.0])}
    )
    elems = [
        _struct(0, "Figure", obj=obj_with),  # OK
        _struct(1, "Figure"),                # missing
        _struct(2, "Figure"),                # missing
    ]
    res = _only(check_figure_elements_have_bbox(_ctx_with_elements(elems)))
    assert res.result == "FAIL"
    # Elements 1 and 2 internally; the report prints them 1-based.
    assert "[2]" in res.details and "[3]" in res.details
    assert "2 of 3" in res.details


def test_figure_bbox_ignores_non_figure_elements() -> None:
    """Only Figure tags are inspected — Art, P, etc. are ignored."""
    elems = [_struct(0, "Art")]  # no BBox but not a Figure
    res = _only(check_figure_elements_have_bbox(_ctx_with_elements(elems)))
    assert res.result == "NA"
    assert "No Figure elements" in res.details
