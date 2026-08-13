"""Tests for ``auto_a11y.pdf.audit.checks.forms``.

Nine form-related checks ported from pdfMax's
``pdf_accessibility_audit.py`` (lines ~5694-6234):

* ``check_widget_annotations_inside_form_tags`` (Matterhorn 28-012).
* ``check_no_xfa_forms_present`` (Matterhorn 25-001).
* ``check_form_fields_labeled`` (PDF/UA, WCAG 1.3.1, 4.1.2).
* ``check_required_fields_flagged`` (PDF/UA, WCAG 1.3.1, 3.3.2).
* ``check_form_fields_tagged_in_structure`` (PDF/UA, WCAG 1.3.1).
* ``check_form_page_tab_order`` (PDF/UA, WCAG 2.1.1, 2.4.3).
* ``check_form_field_names_unique`` (WCAG 4.1.2).
* ``check_redundant_entry_in_forms`` (WCAG 3.3.7).
* ``check_accessible_authentication`` (WCAG 3.3.8).
"""
from __future__ import annotations

from pathlib import Path

import pikepdf

from auto_a11y.pdf.audit.checks.forms import (
    FORMS_CHECKS,
    check_accessible_authentication,
    check_form_field_names_unique,
    check_form_fields_labeled,
    check_form_fields_tagged_in_structure,
    check_form_page_tab_order,
    check_no_xfa_forms_present,
    check_redundant_entry_in_forms,
    check_required_fields_flagged,
    check_required_fields_visually_indicated,
    check_widget_annotations_inside_form_tags,
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


def _new_pdf_with_page(num_pages: int = 1) -> pikepdf.Pdf:
    """Return a fresh ``pikepdf.Pdf`` with ``num_pages`` blank pages."""
    pdf = pikepdf.Pdf.new()
    for _ in range(num_pages):
        pdf.add_blank_page(page_size=(612, 792))
    return pdf


def _only(results: list[CheckResult]) -> CheckResult:
    """Assert single-element list and return its sole entry."""
    assert len(results) == 1, f"expected one CheckResult, got {len(results)}"
    return results[0]


def _add_annot(
    pdf: pikepdf.Pdf,
    *,
    page_index: int = 0,
    fields: dict[str, pikepdf.Object] | None = None,
) -> pikepdf.Dictionary:
    """Append a generic annotation Dictionary to the given page."""
    page = pdf.pages[page_index]
    annot_dict: dict[str, pikepdf.Object] = {
        "/Type": pikepdf.Name("/Annot"),
    }
    if fields is not None:
        annot_dict.update(fields)
    annot = pikepdf.Dictionary(annot_dict)
    existing = page.obj.get(pikepdf.Name("/Annots"))
    if isinstance(existing, pikepdf.Array):
        existing.append(annot)
    else:
        page.obj["/Annots"] = pikepdf.Array([annot])
    return annot


def _set_acroform(
    pdf: pikepdf.Pdf,
    fields: list[pikepdf.Dictionary],
    *,
    extra: dict[str, pikepdf.Object] | None = None,
) -> pikepdf.Dictionary:
    """Install a minimal /AcroForm dict on the catalog with the given fields.

    ``extra`` lets callers add e.g. ``/XFA`` for the XFA test.
    """
    acro_dict: dict[str, pikepdf.Object] = {
        "/Fields": pikepdf.Array(list(fields)),
    }
    if extra is not None:
        acro_dict.update(extra)
    acroform = pikepdf.Dictionary(acro_dict)
    pdf.Root["/AcroForm"] = acroform
    return acroform


def _field(
    *,
    t: str | None = None,
    tu: str | None = None,
    ft: str | None = "/Tx",
    ff: int | None = None,
    extra: dict[str, pikepdf.Object] | None = None,
) -> pikepdf.Dictionary:
    """Build a terminal AcroForm field Dictionary."""
    d: dict[str, pikepdf.Object] = {}
    if ft is not None:
        d["/FT"] = pikepdf.Name(ft)
    if t is not None:
        d["/T"] = pikepdf.String(t)
    if tu is not None:
        d["/TU"] = pikepdf.String(tu)
    if ff is not None:
        d["/Ff"] = pikepdf.Object.parse(str(ff).encode())
    if extra is not None:
        d.update(extra)
    return pikepdf.Dictionary(d)


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------


def test_forms_checks_registry_lists_every_check() -> None:
    """``FORMS_CHECKS`` is the phase-5 entry point."""
    assert FORMS_CHECKS == [
        check_widget_annotations_inside_form_tags,
        check_no_xfa_forms_present,
        check_form_fields_labeled,
        check_required_fields_flagged,
        check_required_fields_visually_indicated,
        check_form_fields_tagged_in_structure,
        check_form_page_tab_order,
        check_form_field_names_unique,
        check_redundant_entry_in_forms,
        check_accessible_authentication,
    ]


# ---------------------------------------------------------------------------
# check_widget_annotations_inside_form_tags
# ---------------------------------------------------------------------------


def test_widgets_in_form_tags_is_not_applicable_when_no_widgets() -> None:
    pdf = _new_pdf_with_page()
    res = _only(check_widget_annotations_inside_form_tags(_ctx(pdf)))
    assert res.name == "Widget annotations inside Form tags"
    assert res.standard == "Matterhorn 28-012"
    assert res.result == "NA"
    assert "No widget" in res.details


def test_widgets_in_form_tags_fails_when_widget_untagged() -> None:
    """A Widget with no parent struct → fails."""
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={"/Subtype": pikepdf.Name("/Widget")})
    res = _only(check_widget_annotations_inside_form_tags(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "1 widget" in res.details
    assert "p.1" in res.details


def test_widgets_in_form_tags_ignores_non_widget_annots() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={"/Subtype": pikepdf.Name("/Link")})
    res = _only(check_widget_annotations_inside_form_tags(_ctx(pdf)))
    assert res.result == "NA"
    assert "No widget" in res.details


# ---------------------------------------------------------------------------
# check_no_xfa_forms_present
# ---------------------------------------------------------------------------


def test_no_xfa_passes_when_no_acroform() -> None:
    pdf = _new_pdf_with_page()
    res = _only(check_no_xfa_forms_present(_ctx(pdf)))
    assert res.name == "No XFA forms present"
    assert res.standard == "Matterhorn 25-001"
    assert res.result == "PASS"


def test_no_xfa_passes_when_acroform_without_xfa() -> None:
    pdf = _new_pdf_with_page()
    _set_acroform(pdf, [_field(t="name", tu="Name")])
    res = _only(check_no_xfa_forms_present(_ctx(pdf)))
    assert res.result == "PASS"
    assert "standard AcroForm" in res.details


def test_no_xfa_fails_when_xfa_present() -> None:
    pdf = _new_pdf_with_page()
    _set_acroform(
        pdf,
        [],
        extra={"/XFA": pikepdf.Array([pikepdf.String("template")])},
    )
    res = _only(check_no_xfa_forms_present(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "XFA" in res.details


# ---------------------------------------------------------------------------
# check_form_fields_labeled
# ---------------------------------------------------------------------------


def test_form_labeled_is_not_applicable_when_no_fields() -> None:
    pdf = _new_pdf_with_page()
    res = _only(check_form_fields_labeled(_ctx(pdf)))
    assert res.name == "Form fields labeled"
    assert res.standard == "PDF/UA, WCAG 1.3.1, 4.1.2"
    assert res.result == "NA"
    assert "No interactive" in res.details


def test_form_labeled_passes_when_all_have_tu() -> None:
    pdf = _new_pdf_with_page()
    _set_acroform(
        pdf,
        [
            _field(t="name", tu="Name"),
            _field(t="email", tu="Email"),
        ],
    )
    res = _only(check_form_fields_labeled(_ctx(pdf)))
    assert res.result == "PASS"
    assert "All 2" in res.details


def test_form_labeled_fails_when_tu_missing() -> None:
    pdf = _new_pdf_with_page()
    _set_acroform(
        pdf,
        [
            _field(t="name", tu="Name"),
            _field(t="email", ft="/Tx"),
        ],
    )
    res = _only(check_form_fields_labeled(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "1 of 2" in res.details
    assert "'email'" in res.details
    assert "(text)" in res.details


def test_form_labeled_fails_when_tu_empty() -> None:
    """An empty /TU is treated as missing."""
    pdf = _new_pdf_with_page()
    _set_acroform(pdf, [_field(t="name", tu="   ")])
    res = _only(check_form_fields_labeled(_ctx(pdf)))
    assert res.result == "FAIL"


def test_form_labeled_flattens_kids_tree() -> None:
    """Intermediate (Kids without /FT) nodes are walked through."""
    pdf = _new_pdf_with_page()
    leaf = _field(t="leaf", tu="Leaf")
    parent = pikepdf.Dictionary({
        "/Kids": pikepdf.Array([leaf]),
    })
    _set_acroform(pdf, [parent])
    res = _only(check_form_fields_labeled(_ctx(pdf)))
    assert res.result == "PASS"
    assert "All 1" in res.details


# ---------------------------------------------------------------------------
# check_required_fields_flagged
# ---------------------------------------------------------------------------


def test_required_passes_when_no_fields() -> None:
    pdf = _new_pdf_with_page()
    res = _only(check_required_fields_flagged(_ctx(pdf)))
    assert res.name == "Required fields flagged"
    assert res.standard == "PDF/UA, WCAG 1.3.1, 3.3.2"
    assert res.result == "PASS"


def test_required_passes_when_no_hints() -> None:
    pdf = _new_pdf_with_page()
    _set_acroform(pdf, [_field(t="name", tu="Name")])
    res = _only(check_required_fields_flagged(_ctx(pdf)))
    assert res.result == "PASS"
    assert "No required fields detected" in res.details


def test_required_passes_when_flag_set() -> None:
    pdf = _new_pdf_with_page()
    _set_acroform(pdf, [_field(t="name", tu="Name (required)", ff=2)])
    res = _only(check_required_fields_flagged(_ctx(pdf)))
    assert res.result == "PASS"
    assert "1 field(s) have Required flag set" in res.details


def test_required_warns_when_hint_without_flag() -> None:
    pdf = _new_pdf_with_page()
    _set_acroform(pdf, [_field(t="name", tu="Name (required)", ff=0)])
    res = _only(check_required_fields_flagged(_ctx(pdf)))
    assert res.result == "WARN"
    assert "1 field(s)" in res.details
    assert "'name'" in res.details


def test_required_warns_for_french_hints() -> None:
    pdf = _new_pdf_with_page()
    _set_acroform(pdf, [_field(t="nom", tu="Nom (obligatoire)")])
    res = _only(check_required_fields_flagged(_ctx(pdf)))
    assert res.result == "WARN"


# ---------------------------------------------------------------------------
# check_form_fields_tagged_in_structure
# ---------------------------------------------------------------------------


def _form_struct_elem(index: int) -> StructElement:
    """Return a Form-tagged structure element."""
    return StructElement(
        index=index,
        custom_tag="/Form",
        resolved_tag="Form",
        alt_text=None,
        actual_text=None,
        lang=None,
        children_indices=[],
        mcids=[],
        parent_index=-1,
        obj=pikepdf.Dictionary(),
    )


def test_fields_tagged_is_not_applicable_when_no_fields() -> None:
    """No form fields → NA, not silence: a missing check reads as unrun."""
    pdf = _new_pdf_with_page()
    res = _only(check_form_fields_tagged_in_structure(_ctx(pdf)))
    assert res.result == "NA"
    assert "No form fields" in res.details


def test_fields_tagged_passes_when_enough_form_tags() -> None:
    pdf = _new_pdf_with_page()
    _set_acroform(pdf, [_field(t="a", tu="A"), _field(t="b", tu="B")])
    elements = [_form_struct_elem(0), _form_struct_elem(1)]
    res = _only(
        check_form_fields_tagged_in_structure(_ctx(pdf, elements))
    )
    assert res.name == "Form fields tagged in structure"
    assert res.standard == "PDF/UA, WCAG 1.3.1"
    assert res.result == "PASS"
    assert "2 Form tags" in res.details


def test_fields_tagged_warns_when_partial() -> None:
    pdf = _new_pdf_with_page()
    _set_acroform(pdf, [_field(t="a", tu="A"), _field(t="b", tu="B")])
    elements = [_form_struct_elem(0)]
    res = _only(
        check_form_fields_tagged_in_structure(_ctx(pdf, elements))
    )
    assert res.result == "WARN"
    assert "Only 1" in res.details


def test_fields_tagged_fails_when_zero_form_tags() -> None:
    pdf = _new_pdf_with_page()
    _set_acroform(pdf, [_field(t="a", tu="A")])
    res = _only(check_form_fields_tagged_in_structure(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "No Form tags" in res.details


# ---------------------------------------------------------------------------
# check_form_page_tab_order
# ---------------------------------------------------------------------------


def test_tab_order_is_not_applicable_when_no_widget_pages() -> None:
    """No Widget annotations → NA rather than silence."""
    pdf = _new_pdf_with_page()
    res = _only(check_form_page_tab_order(_ctx(pdf)))
    assert res.result == "NA"
    assert "Widget" in res.details


def test_tab_order_passes_when_tabs_set_to_S() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={"/Subtype": pikepdf.Name("/Widget")})
    pdf.pages[0].obj["/Tabs"] = pikepdf.Name("/S")
    res = _only(check_form_page_tab_order(_ctx(pdf)))
    assert res.name == "Form page tab order"
    assert res.standard == "PDF/UA, WCAG 2.1.1, 2.4.3"
    assert res.result == "PASS"
    assert "All 1" in res.details


def test_tab_order_fails_when_tabs_missing() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={"/Subtype": pikepdf.Name("/Widget")})
    res = _only(check_form_page_tab_order(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "Page 1" in res.details
    assert "not set" in res.details


def test_tab_order_fails_when_tabs_wrong_value() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={"/Subtype": pikepdf.Name("/Widget")})
    pdf.pages[0].obj["/Tabs"] = pikepdf.Name("/R")
    res = _only(check_form_page_tab_order(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "= /R" in res.details


# ---------------------------------------------------------------------------
# check_form_field_names_unique
# ---------------------------------------------------------------------------


def test_field_names_unique_is_not_applicable_when_no_fields() -> None:
    pdf = _new_pdf_with_page()
    res = _only(check_form_field_names_unique(_ctx(pdf)))
    assert res.result == "NA"
    assert "No form fields" in res.details


def test_field_names_unique_passes_when_distinct() -> None:
    pdf = _new_pdf_with_page()
    _set_acroform(pdf, [_field(t="a", tu="A"), _field(t="b", tu="B")])
    res = _only(check_form_field_names_unique(_ctx(pdf)))
    assert res.name == "Form field names unique"
    assert res.standard == "WCAG 4.1.2"
    assert res.result == "PASS"
    assert "All 2" in res.details


def test_field_names_unique_warns_on_duplicates() -> None:
    pdf = _new_pdf_with_page()
    _set_acroform(
        pdf,
        [
            _field(t="dupe", tu="A"),
            _field(t="dupe", tu="B"),
            _field(t="other", tu="C"),
        ],
    )
    res = _only(check_form_field_names_unique(_ctx(pdf)))
    assert res.result == "WARN"
    assert "'dupe'" in res.details
    assert "x2" in res.details


# ---------------------------------------------------------------------------
# check_redundant_entry_in_forms
# ---------------------------------------------------------------------------


def test_redundant_entry_is_not_applicable_when_no_fields() -> None:
    pdf = _new_pdf_with_page()
    res = _only(check_redundant_entry_in_forms(_ctx(pdf)))
    assert res.name == "Redundant entry in forms"
    assert res.standard == "WCAG 3.3.7"
    assert res.result == "NA"
    assert "No interactive" in res.details


def test_redundant_entry_passes_when_unique_per_page() -> None:
    pdf = _new_pdf_with_page(num_pages=2)
    _set_acroform(pdf, [_field(t="a", tu="Name")])
    _add_annot(
        pdf,
        page_index=0,
        fields={"/Subtype": pikepdf.Name("/Widget"), "/TU": pikepdf.String("Name")},
    )
    _add_annot(
        pdf,
        page_index=1,
        fields={"/Subtype": pikepdf.Name("/Widget"), "/TU": pikepdf.String("Email")},
    )
    res = _only(check_redundant_entry_in_forms(_ctx(pdf)))
    assert res.result == "PASS"


def test_redundant_entry_warns_when_label_repeats() -> None:
    pdf = _new_pdf_with_page(num_pages=2)
    _set_acroform(pdf, [_field(t="a", tu="Name")])
    _add_annot(
        pdf,
        page_index=0,
        fields={"/Subtype": pikepdf.Name("/Widget"), "/TU": pikepdf.String("Name")},
    )
    _add_annot(
        pdf,
        page_index=1,
        fields={"/Subtype": pikepdf.Name("/Widget"), "/TU": pikepdf.String("Name")},
    )
    res = _only(check_redundant_entry_in_forms(_ctx(pdf)))
    assert res.result == "WARN"
    assert "'name'" in res.details
    assert "pages 1, 2" in res.details


# ---------------------------------------------------------------------------
# check_accessible_authentication
# ---------------------------------------------------------------------------


def test_accessible_auth_is_not_applicable_when_no_fields() -> None:
    pdf = _new_pdf_with_page()
    res = _only(check_accessible_authentication(_ctx(pdf)))
    assert res.name == "Accessible authentication"
    assert res.standard == "WCAG 3.3.8"
    assert res.result == "NA"
    assert "No interactive" in res.details


def test_accessible_auth_passes_when_no_password() -> None:
    pdf = _new_pdf_with_page()
    _set_acroform(pdf, [_field(t="name", tu="Name")])
    res = _only(check_accessible_authentication(_ctx(pdf)))
    assert res.result == "PASS"
    assert "No password fields" in res.details


def test_accessible_auth_warns_when_password_present() -> None:
    pdf = _new_pdf_with_page()
    _set_acroform(
        pdf,
        [_field(t="pw", tu="Password", ft="/Tx", ff=0x2000)],
    )
    res = _only(check_accessible_authentication(_ctx(pdf)))
    assert res.result == "WARN"
    assert "1 password" in res.details
    assert "pw" in res.details


def test_accessible_auth_ignores_non_text_fields() -> None:
    """Password flag on a non-/Tx field is ignored."""
    pdf = _new_pdf_with_page()
    _set_acroform(
        pdf,
        [_field(t="btn", tu="Button", ft="/Btn", ff=0x2000)],
    )
    res = _only(check_accessible_authentication(_ctx(pdf)))
    assert res.result == "PASS"
