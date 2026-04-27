"""Tests for ``auto_a11y.pdf.audit.checks.interactive``.

Five interactive-element checks ported from pdfMax's
``pdf_accessibility_audit.py`` (lines ~5429-5615):

* ``check_label_in_name`` — WCAG 2.5.3.
* ``check_interactive_target_size`` — WCAG 2.5.5, 2.5.8.
* ``check_focus_indicator_visibility`` — WCAG 2.4.7.
* ``check_focus_not_obscured`` — WCAG 2.4.11.
* ``check_dragging_movement_alternatives`` — WCAG 2.5.7.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf

from auto_a11y.pdf.audit.checks.interactive import (
    INTERACTIVE_CHECKS,
    check_dragging_movement_alternatives,
    check_focus_indicator_visibility,
    check_focus_not_obscured,
    check_interactive_target_size,
    check_label_in_name,
)
from auto_a11y.pdf.audit.structure import StructElement
from auto_a11y.pdf.models import AuditContext, CheckResult


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _ctx(
    pdf: pikepdf.Pdf,
    elements: list[StructElement] | None = None,
) -> AuditContext:
    """Build an ``AuditContext`` over an in-memory ``pikepdf.Pdf``."""
    return AuditContext(
        pdf=pdf,
        pdf_path=Path("/tmp/test.pdf"),
        elements=elements if elements is not None else [],
        role_map={},
    )


def _new_pdf_with_page() -> pikepdf.Pdf:
    """Return a fresh ``pikepdf.Pdf`` with a single blank Letter-size page."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    return pdf


def _only(results: list[CheckResult]) -> CheckResult:
    """Assert single-element list and return its sole entry."""
    assert len(results) == 1, f"expected one CheckResult, got {len(results)}"
    return results[0]


def _struct(
    index: int,
    tag: str,
    *,
    alt_text: str | None = None,
    text_content: str = "",
) -> StructElement:
    """Build a structure element used by ``check_label_in_name``."""
    elem = StructElement(
        index=index,
        custom_tag=f"/{tag}",
        resolved_tag=tag,
        alt_text=alt_text,
        actual_text=None,
        lang=None,
        children_indices=[],
        mcids=[],
        parent_index=-1,
        obj=pikepdf.Dictionary(),
    )
    elem.text_content = text_content
    return elem


def _add_link_annot(
    pdf: pikepdf.Pdf,
    *,
    rect: tuple[float, float, float, float] | None = (100.0, 100.0, 200.0, 130.0),
    border: tuple[float, float, float] | None = (0.0, 0.0, 1.0),
    uri: str | None = None,
) -> pikepdf.Dictionary:
    """Append one /Link annotation to page 0 and return its dict."""
    page = pdf.pages[0]
    annot_dict: dict[str, pikepdf.Object] = {
        "/Type": pikepdf.Name("/Annot"),
        "/Subtype": pikepdf.Name("/Link"),
    }
    if rect is not None:
        annot_dict["/Rect"] = pikepdf.Array(list(rect))
    if border is not None:
        annot_dict["/Border"] = pikepdf.Array(list(border))
    if uri is not None:
        annot_dict["/A"] = pikepdf.Dictionary({
            "/Type": pikepdf.Name("/Action"),
            "/S": pikepdf.Name("/URI"),
            "/URI": pikepdf.String(uri),
        })
    annot = pikepdf.Dictionary(annot_dict)
    existing = page.obj.get(pikepdf.Name("/Annots"))
    if isinstance(existing, pikepdf.Array):
        existing.append(annot)
    else:
        page.obj["/Annots"] = pikepdf.Array([annot])
    return annot


def _add_widget_annot(
    pdf: pikepdf.Pdf,
    *,
    rect: tuple[float, float, float, float] = (100.0, 100.0, 200.0, 150.0),
    field_name: str = "fld",
    aa_actions: dict[str, tuple[str, str]] | None = None,
) -> pikepdf.Dictionary:
    """Append one /Widget annotation to page 0.

    ``aa_actions`` maps an ``/AA`` key (e.g. ``"/D"``) to ``(/S, /JS)``
    where ``/S`` is the action subtype name and ``/JS`` is the script
    string. Used by the dragging-movement check.
    """
    page = pdf.pages[0]
    annot_dict: dict[str, pikepdf.Object] = {
        "/Type": pikepdf.Name("/Annot"),
        "/Subtype": pikepdf.Name("/Widget"),
        "/T": pikepdf.String(field_name),
        "/Rect": pikepdf.Array(list(rect)),
    }
    if aa_actions is not None:
        aa_dict: dict[str, pikepdf.Object] = {}
        for key, (subtype, js) in aa_actions.items():
            aa_dict[key] = pikepdf.Dictionary({
                "/S": pikepdf.Name(subtype),
                "/JS": pikepdf.String(js),
            })
        annot_dict["/AA"] = pikepdf.Dictionary(aa_dict)
    annot = pikepdf.Dictionary(annot_dict)
    existing = page.obj.get(pikepdf.Name("/Annots"))
    if isinstance(existing, pikepdf.Array):
        existing.append(annot)
    else:
        page.obj["/Annots"] = pikepdf.Array([annot])
    return annot


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------


def test_interactive_checks_registry_lists_all_five_functions() -> None:
    """``INTERACTIVE_CHECKS`` is the phase-5 entry point — must list every check."""
    assert INTERACTIVE_CHECKS == [
        check_label_in_name,
        check_interactive_target_size,
        check_focus_indicator_visibility,
        check_focus_not_obscured,
        check_dragging_movement_alternatives,
    ]


# ---------------------------------------------------------------------------
# check_label_in_name
# ---------------------------------------------------------------------------


def test_label_in_name_passes_when_no_eligible_elements() -> None:
    pdf = _new_pdf_with_page()
    res = _only(check_label_in_name(_ctx(pdf)))
    assert res.name == "Label in Name"
    assert res.standard == "WCAG 2.5.3"
    assert res.result == "PASS"


def test_label_in_name_passes_when_visible_text_in_accessible_name() -> None:
    pdf = _new_pdf_with_page()
    elems = [
        _struct(
            0, "Link",
            alt_text="Read more about widgets",
            text_content="Read more",
        ),
    ]
    res = _only(check_label_in_name(_ctx(pdf, elems)))
    assert res.result == "PASS"


def test_label_in_name_fails_when_visible_text_missing_from_alt() -> None:
    pdf = _new_pdf_with_page()
    elems = [
        _struct(
            0, "Link",
            alt_text="Click here for our latest blog",
            text_content="Read more about widgets",
        ),
    ]
    res = _only(check_label_in_name(_ctx(pdf, elems)))
    assert res.result == "FAIL"
    assert "[1] Link" in res.details
    assert "widgets" in res.details or "read more" in res.details


def test_label_in_name_skips_non_interactive_tags() -> None:
    """Only Link/H1-H6/Art/Figure/Span are checked."""
    pdf = _new_pdf_with_page()
    # P is not in the eligible set.
    elems = [
        _struct(
            0, "P",
            alt_text="completely different",
            text_content="Read more about widgets",
        ),
    ]
    res = _only(check_label_in_name(_ctx(pdf, elems)))
    assert res.result == "PASS"


def test_label_in_name_skips_short_visible_text() -> None:
    """Visible text under 3 chars is too short to evaluate."""
    pdf = _new_pdf_with_page()
    elems = [
        _struct(0, "Link", alt_text="totally different", text_content="OK"),
    ]
    res = _only(check_label_in_name(_ctx(pdf, elems)))
    assert res.result == "PASS"


def test_label_in_name_skips_when_no_alt_or_no_text() -> None:
    pdf = _new_pdf_with_page()
    elems = [
        _struct(0, "Link", alt_text=None, text_content="Read more"),
        _struct(1, "Link", alt_text="Read more", text_content=""),
    ]
    res = _only(check_label_in_name(_ctx(pdf, elems)))
    assert res.result == "PASS"


def test_label_in_name_partial_overlap_warns_via_fail() -> None:
    """4+-word visible text with 50-80% overlap reports missing words."""
    pdf = _new_pdf_with_page()
    elems = [
        _struct(
            0, "H2",
            alt_text="Annual report widgets summary",
            text_content="Annual report widgets summary financial results",
        ),
    ]
    res = _only(check_label_in_name(_ctx(pdf, elems)))
    assert res.result == "FAIL"
    assert "missing words" in res.details


# ---------------------------------------------------------------------------
# check_interactive_target_size
# ---------------------------------------------------------------------------


def test_target_size_passes_when_no_links() -> None:
    pdf = _new_pdf_with_page()
    res = _only(check_interactive_target_size(_ctx(pdf)))
    assert res.name == "Interactive element target size"
    assert res.standard == "WCAG 2.5.5, 2.5.8"
    assert res.result == "PASS"
    assert "0" in res.details


def test_target_size_passes_when_link_meets_minimum() -> None:
    pdf = _new_pdf_with_page()
    # 50pt high — meets 24pt minimum
    _add_link_annot(pdf, rect=(100.0, 100.0, 200.0, 150.0))
    res = _only(check_interactive_target_size(_ctx(pdf)))
    assert res.result == "PASS"
    assert "1 link" in res.details


def test_target_size_warns_when_link_below_minimum() -> None:
    pdf = _new_pdf_with_page()
    # 10pt high — below 24pt
    _add_link_annot(
        pdf, rect=(100.0, 100.0, 200.0, 110.0), uri="https://x.test/",
    )
    res = _only(check_interactive_target_size(_ctx(pdf)))
    assert res.result == "WARN"
    assert "1 link" in res.details
    assert "100x10" in res.details or "100x10pt" in res.details


def test_target_size_warns_when_width_is_below_minimum() -> None:
    pdf = _new_pdf_with_page()
    # 10pt wide — below 24pt
    _add_link_annot(pdf, rect=(100.0, 100.0, 110.0, 200.0))
    res = _only(check_interactive_target_size(_ctx(pdf)))
    assert res.result == "WARN"


def test_target_size_ignores_non_link_annots() -> None:
    pdf = _new_pdf_with_page()
    # Widget annotation; should not contribute to the link count.
    _add_widget_annot(pdf, rect=(100.0, 100.0, 110.0, 110.0))
    res = _only(check_interactive_target_size(_ctx(pdf)))
    assert res.result == "PASS"
    assert "0" in res.details  # zero links


# ---------------------------------------------------------------------------
# check_focus_indicator_visibility
# ---------------------------------------------------------------------------


def test_focus_indicator_passes_when_no_links() -> None:
    pdf = _new_pdf_with_page()
    res = _only(check_focus_indicator_visibility(_ctx(pdf)))
    assert res.name == "Focus indicator visibility"
    assert res.standard == "WCAG 2.4.7"
    assert res.result == "PASS"


def test_focus_indicator_passes_when_links_have_visible_borders() -> None:
    pdf = _new_pdf_with_page()
    _add_link_annot(pdf, border=(0.0, 0.0, 1.0))
    _add_link_annot(pdf, border=(0.0, 0.0, 2.0))
    res = _only(check_focus_indicator_visibility(_ctx(pdf)))
    assert res.result == "PASS"


def test_focus_indicator_warns_when_all_borders_zero_width() -> None:
    pdf = _new_pdf_with_page()
    _add_link_annot(pdf, border=(0.0, 0.0, 0.0))
    _add_link_annot(pdf, border=(0.0, 0.0, 0.0))
    res = _only(check_focus_indicator_visibility(_ctx(pdf)))
    assert res.result == "WARN"
    assert "All 2" in res.details
    assert "zero-width" in res.details


def test_focus_indicator_warns_when_some_borders_zero_width() -> None:
    pdf = _new_pdf_with_page()
    _add_link_annot(pdf, border=(0.0, 0.0, 0.0))
    _add_link_annot(pdf, border=(0.0, 0.0, 1.0))
    res = _only(check_focus_indicator_visibility(_ctx(pdf)))
    assert res.result == "WARN"
    assert "1 of 2" in res.details
    assert "suppressed" in res.details


def test_focus_indicator_skips_links_without_border() -> None:
    """A /Link with no /Border isn't counted as suppressed."""
    pdf = _new_pdf_with_page()
    _add_link_annot(pdf, border=None)
    res = _only(check_focus_indicator_visibility(_ctx(pdf)))
    assert res.result == "PASS"


# ---------------------------------------------------------------------------
# check_focus_not_obscured
# ---------------------------------------------------------------------------


def test_focus_not_obscured_passes_when_no_interactive_annots() -> None:
    pdf = _new_pdf_with_page()
    res = _only(check_focus_not_obscured(_ctx(pdf)))
    assert res.name == "Focus not obscured"
    assert res.standard == "WCAG 2.4.11"
    assert res.result == "PASS"
    assert "0 elements" in res.details


def test_focus_not_obscured_passes_when_annots_dont_overlap() -> None:
    pdf = _new_pdf_with_page()
    _add_link_annot(pdf, rect=(100.0, 100.0, 200.0, 150.0))
    _add_link_annot(pdf, rect=(300.0, 300.0, 400.0, 350.0))
    res = _only(check_focus_not_obscured(_ctx(pdf)))
    assert res.result == "PASS"
    assert "2 elements" in res.details


def test_focus_not_obscured_fails_when_link_inside_widget() -> None:
    pdf = _new_pdf_with_page()
    # Outer widget completely contains the inner link.
    _add_widget_annot(pdf, rect=(100.0, 100.0, 400.0, 400.0))
    _add_link_annot(pdf, rect=(150.0, 150.0, 200.0, 200.0))
    res = _only(check_focus_not_obscured(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "obscured" in res.details


def test_focus_not_obscured_flags_zero_area_annot() -> None:
    pdf = _new_pdf_with_page()
    _add_link_annot(pdf, rect=(100.0, 100.0, 100.0, 100.0))
    res = _only(check_focus_not_obscured(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "zero-area" in res.details


# ---------------------------------------------------------------------------
# check_dragging_movement_alternatives
# ---------------------------------------------------------------------------


def test_dragging_passes_when_no_widgets() -> None:
    pdf = _new_pdf_with_page()
    res = _only(check_dragging_movement_alternatives(_ctx(pdf)))
    assert res.name == "Dragging movement alternatives"
    assert res.standard == "WCAG 2.5.7"
    assert res.result == "PASS"


def test_dragging_passes_when_widget_has_no_aa() -> None:
    pdf = _new_pdf_with_page()
    _add_widget_annot(pdf)
    res = _only(check_dragging_movement_alternatives(_ctx(pdf)))
    assert res.result == "PASS"


def test_dragging_passes_when_aa_js_has_no_drag_terms() -> None:
    pdf = _new_pdf_with_page()
    _add_widget_annot(
        pdf, aa_actions={"/D": ("/JavaScript", "console.log('hi');")},
    )
    res = _only(check_dragging_movement_alternatives(_ctx(pdf)))
    assert res.result == "PASS"


def test_dragging_warns_when_widget_has_drag_js() -> None:
    pdf = _new_pdf_with_page()
    _add_widget_annot(
        pdf,
        field_name="slider1",
        aa_actions={
            "/D": ("/JavaScript", "this.startDrag(event);"),
            "/U": ("/JavaScript", "this.endDrag(event);"),
        },
    )
    res = _only(check_dragging_movement_alternatives(_ctx(pdf)))
    assert res.result == "WARN"
    assert "1 widget" in res.details
    assert "slider1" in res.details
    assert "D" in res.details
    assert "U" in res.details


def test_dragging_warns_for_pointer_or_move_keywords() -> None:
    """All four trigger keywords (drag, mouse, pointer, move) match."""
    pdf = _new_pdf_with_page()
    _add_widget_annot(
        pdf, aa_actions={"/E": ("/JavaScript", "handlePointerEnter();")},
    )
    res = _only(check_dragging_movement_alternatives(_ctx(pdf)))
    assert res.result == "WARN"


def test_dragging_skips_non_javascript_actions() -> None:
    """An /AA entry whose /S is not /JavaScript is ignored."""
    pdf = _new_pdf_with_page()
    _add_widget_annot(
        pdf, aa_actions={"/D": ("/SubmitForm", "drag-this")},
    )
    res = _only(check_dragging_movement_alternatives(_ctx(pdf)))
    assert res.result == "PASS"
