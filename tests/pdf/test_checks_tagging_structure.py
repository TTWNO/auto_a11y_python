"""Tests for ``auto_a11y.pdf.audit.checks.tagging_structure``.

Twenty tagging-structure checks ported from pdfMax's
``pdf_accessibility_audit.py``: structure-tree presence, RoleMap
correctness, named structure containers, embedded files, optional
content groups, intra-document link destinations, Form XObject reuse,
Reference XObjects, tab-order, the relaxed PDF/UA-2 heading rules, and
the visual-reading-order comparison that consumes Phase 3 collector
output.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

import pikepdf

from auto_a11y.pdf.audit.checks.tagging_structure import (
    check_all_content_tagged,
    check_all_content_tagged_or_artifact,
    check_artifact_classification_subtypes,
    check_artifact_not_inside_tagged,
    check_correct_nesting,
    check_tagged_not_inside_artifact,
    check_no_empty_tags,
    TAGGING_STRUCTURE_CHECKS,
    check_associated_files_on_embedded_content,
    check_embedded_files_have_f_and_uf,
    check_form_xobjects_with_mcids_not_reused,
    check_formula_alt_text,
    check_formula_unicode_mapping_valid,
    check_mathml_associated_with_formula,
    check_no_circular_role_mappings,
    check_no_reference_xobjects,
    check_note_tags_have_unique_ids,
    check_optional_content_groups_have_name,
    check_optional_content_no_as_entry,
    check_pdfua2_heading_hierarchy,
    check_reading_order_matches_visual_layout,
    check_role_mapping_valid,
    check_ruby_structure_valid,
    check_standard_tags_not_remapped,
    check_structure_destinations_for_intra_links,
    check_structure_tree_exists,
    check_tab_order_follows_structure,
    check_toc_structure_valid,
    check_warichu_structure_valid,
)
from auto_a11y.pdf.audit.content_classification import (
    ArtifactMark,
    PageContentClassification,
)
from auto_a11y.pdf.audit.font_metadata import (
    FontInfoDetail,
    FontMetadata,
    FontUnicodeMapping,
)
from auto_a11y.pdf.audit.reading_order import (
    ElementPosition,
    PageDimensions,
    ReadingOrderMismatch,
    VisualBlock,
)
from auto_a11y.pdf.audit.structure import StructElement
from auto_a11y.pdf.models import AuditContext, CheckResult


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _FormStreamMaker(Protocol):
    """Typed view of ``pikepdf.Pdf.make_stream`` for our two-arg usage."""

    def __call__(
        self, data: bytes, d: pikepdf.Dictionary
    ) -> pikepdf.Stream: ...


def _make_form_xobject(
    pdf: pikepdf.Pdf, body: bytes, attrs: pikepdf.Dictionary
) -> pikepdf.Stream:
    """Strictly-typed wrapper around ``Pdf.make_stream(data, dict)``."""
    maker: _FormStreamMaker = getattr(pdf, "make_stream")
    return maker(body, attrs)


def _ctx(
    pdf: pikepdf.Pdf | None = None,
    elements: list[StructElement] | None = None,
    role_map: dict[str, str] | None = None,
) -> AuditContext:
    """Build an ``AuditContext`` for these checks."""
    return AuditContext(
        pdf=pdf if pdf is not None else pikepdf.Pdf.new(),
        pdf_path=Path("/tmp/test.pdf"),
        elements=elements if elements is not None else [],
        role_map=role_map if role_map is not None else {},
    )


def _struct_elem(
    index: int,
    tag: str,
    *,
    parent_index: int = -1,
    children_indices: list[int] | None = None,
    obj: pikepdf.Dictionary | None = None,
    alt_text: str | None = None,
    actual_text: str | None = None,
    mcids: list[int] | None = None,
) -> StructElement:
    """Build a structure element with sensible defaults."""
    return StructElement(
        index=index,
        custom_tag=f"/{tag}",
        resolved_tag=tag,
        alt_text=alt_text,
        actual_text=actual_text,
        lang=None,
        children_indices=children_indices if children_indices is not None else [],
        mcids=mcids if mcids is not None else [],
        parent_index=parent_index,
        obj=obj if obj is not None else pikepdf.Dictionary(),
    )


def _add_blank_page(pdf: pikepdf.Pdf) -> pikepdf.Page:
    """Append a blank page to an in-memory PDF and return it."""
    pdf.add_blank_page(page_size=(612, 792))
    return pdf.pages[-1]


def _only(results: list[CheckResult]) -> CheckResult:
    """Assert single-element list and return its sole entry."""
    assert len(results) == 1, f"expected one CheckResult, got {len(results)}"
    return results[0]


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------


def test_tagging_structure_checks_registry_lists_every_check() -> None:
    assert TAGGING_STRUCTURE_CHECKS == [
        check_structure_tree_exists,
        check_role_mapping_valid,
        check_no_circular_role_mappings,
        check_standard_tags_not_remapped,
        check_tab_order_follows_structure,
        check_toc_structure_valid,
        check_ruby_structure_valid,
        check_warichu_structure_valid,
        check_note_tags_have_unique_ids,
        check_no_empty_tags,
        check_correct_nesting,
        check_artifact_not_inside_tagged,
        check_tagged_not_inside_artifact,
        check_all_content_tagged_or_artifact,
        check_formula_alt_text,
        check_mathml_associated_with_formula,
        check_associated_files_on_embedded_content,
        check_optional_content_groups_have_name,
        check_optional_content_no_as_entry,
        check_embedded_files_have_f_and_uf,
        check_no_reference_xobjects,
        check_form_xobjects_with_mcids_not_reused,
        check_structure_destinations_for_intra_links,
        check_pdfua2_heading_hierarchy,
        check_reading_order_matches_visual_layout,
        check_all_content_tagged,
        check_artifact_classification_subtypes,
        check_formula_unicode_mapping_valid,
    ]


# ---------------------------------------------------------------------------
# check_structure_tree_exists
# ---------------------------------------------------------------------------


def test_structure_tree_exists_pass_when_root_with_k() -> None:
    pdf = pikepdf.Pdf.new()
    pdf.Root["/StructTreeRoot"] = pikepdf.Dictionary(
        {"/Type": pikepdf.Name("/StructTreeRoot"), "/K": pikepdf.Array([pikepdf.Dictionary()])}
    )
    elems = [_struct_elem(0, "Document"), _struct_elem(1, "P")]
    res = _only(check_structure_tree_exists(_ctx(pdf, elements=elems)))
    assert res.name == "Structure tree exists"
    assert res.standard == "Matterhorn 01-006"
    assert res.result == "PASS"
    assert "2 structure elements" in res.details


def test_structure_tree_exists_fail_when_no_root() -> None:
    pdf = pikepdf.Pdf.new()
    res = _only(check_structure_tree_exists(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "/StructTreeRoot" in res.details


def test_structure_tree_exists_fail_when_empty_k() -> None:
    pdf = pikepdf.Pdf.new()
    pdf.Root["/StructTreeRoot"] = pikepdf.Dictionary(
        {"/Type": pikepdf.Name("/StructTreeRoot"), "/K": pikepdf.Array([])}
    )
    res = _only(check_structure_tree_exists(_ctx(pdf)))
    assert res.result == "FAIL"


# ---------------------------------------------------------------------------
# check_role_mapping_valid
# ---------------------------------------------------------------------------


def test_role_mapping_valid_pass_when_targets_are_standard() -> None:
    rm = {"/MyHeading": "/H1", "/Para": "/P"}
    res = _only(check_role_mapping_valid(_ctx(role_map=rm)))
    assert res.name == "Role mapping valid"
    assert res.standard == "Matterhorn 02-001"
    assert res.result == "PASS"


def test_role_mapping_valid_fail_when_target_not_standard() -> None:
    rm = {"/MyTag": "/CustomThing"}
    res = _only(check_role_mapping_valid(_ctx(role_map=rm)))
    assert res.result == "FAIL"
    assert "/MyTag" in res.details
    assert "CustomThing" in res.details


# ---------------------------------------------------------------------------
# check_no_circular_role_mappings
# ---------------------------------------------------------------------------


def test_no_circular_role_mappings_pass_on_acyclic_chain() -> None:
    rm = {"/A": "/B", "/B": "/H1"}
    res = _only(check_no_circular_role_mappings(_ctx(role_map=rm)))
    assert res.name == "No circular role mappings"
    assert res.standard == "Matterhorn 02-003"
    assert res.result == "PASS"


def test_no_circular_role_mappings_fail_on_cycle() -> None:
    rm = {"/A": "/B", "/B": "/A"}
    res = _only(check_no_circular_role_mappings(_ctx(role_map=rm)))
    assert res.result == "FAIL"
    assert "/A" in res.details
    assert "/B" in res.details


# ---------------------------------------------------------------------------
# check_standard_tags_not_remapped
# ---------------------------------------------------------------------------


def test_standard_tags_not_remapped_pass_when_only_custom_keys() -> None:
    rm = {"/MyP": "/P"}
    res = _only(check_standard_tags_not_remapped(_ctx(role_map=rm)))
    assert res.name == "Standard tags not remapped"
    assert res.standard == "Matterhorn 02-004"
    assert res.result == "PASS"


def test_standard_tags_not_remapped_fail_when_h1_remapped() -> None:
    rm = {"/H1": "/P"}
    res = _only(check_standard_tags_not_remapped(_ctx(role_map=rm)))
    assert res.result == "FAIL"
    assert "/H1" in res.details


# ---------------------------------------------------------------------------
# check_tab_order_follows_structure
# ---------------------------------------------------------------------------


def test_tab_order_pass_when_every_page_has_tabs_s() -> None:
    pdf = pikepdf.Pdf.new()
    p1 = _add_blank_page(pdf)
    p1.obj["/Tabs"] = pikepdf.Name("/S")
    res = _only(check_tab_order_follows_structure(_ctx(pdf)))
    assert res.name == "Tab order follows structure"
    assert res.standard == "PDF/UA, WCAG 2.1.1"
    assert res.result == "PASS"
    assert "/Tabs = /S" in res.details


def test_tab_order_fail_when_tabs_not_set() -> None:
    pdf = pikepdf.Pdf.new()
    _add_blank_page(pdf)
    res = _only(check_tab_order_follows_structure(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "not set" in res.details


def test_tab_order_fail_when_tabs_not_s() -> None:
    pdf = pikepdf.Pdf.new()
    p1 = _add_blank_page(pdf)
    p1.obj["/Tabs"] = pikepdf.Name("/R")
    res = _only(check_tab_order_follows_structure(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "/R" in res.details


# ---------------------------------------------------------------------------
# check_toc_structure_valid
# ---------------------------------------------------------------------------


def test_toc_structure_pass_when_valid_nesting() -> None:
    elems = [
        _struct_elem(0, "TOC", children_indices=[1]),
        _struct_elem(1, "TOCI", parent_index=0, children_indices=[2]),
        _struct_elem(2, "P", parent_index=1),
    ]
    res = _only(check_toc_structure_valid(_ctx(elements=elems)))
    assert res.name == "TOC structure valid"
    assert res.standard == "Matterhorn 09-006"
    assert res.result == "PASS"


def test_toc_structure_pass_when_no_toc_elements() -> None:
    elems = [_struct_elem(0, "Document")]
    res = _only(check_toc_structure_valid(_ctx(elements=elems)))
    assert res.result == "PASS"
    assert "No TOC" in res.details


def test_toc_structure_fail_when_toc_has_non_toci_child() -> None:
    elems = [
        _struct_elem(0, "TOC", children_indices=[1]),
        _struct_elem(1, "P", parent_index=0),
    ]
    res = _only(check_toc_structure_valid(_ctx(elements=elems)))
    assert res.result == "FAIL"
    assert "TOC has invalid child" in res.details


def test_toc_structure_fail_when_toci_has_invalid_child() -> None:
    elems = [
        _struct_elem(0, "TOCI", children_indices=[1]),
        _struct_elem(1, "Span", parent_index=0),
    ]
    res = _only(check_toc_structure_valid(_ctx(elements=elems)))
    assert res.result == "FAIL"
    assert "TOCI has invalid child" in res.details


# ---------------------------------------------------------------------------
# check_ruby_structure_valid
# ---------------------------------------------------------------------------


def test_ruby_structure_pass_with_required_children() -> None:
    elems = [
        _struct_elem(0, "Ruby", children_indices=[1, 2]),
        _struct_elem(1, "RB", parent_index=0),
        _struct_elem(2, "RT", parent_index=0),
    ]
    res = _only(check_ruby_structure_valid(_ctx(elements=elems)))
    assert res.name == "Ruby structure valid"
    assert res.standard == "Matterhorn 09-007"
    assert res.result == "PASS"


def test_ruby_structure_fail_when_missing_rb() -> None:
    elems = [
        _struct_elem(0, "Ruby", children_indices=[1]),
        _struct_elem(1, "RT", parent_index=0),
    ]
    res = _only(check_ruby_structure_valid(_ctx(elements=elems)))
    assert res.result == "FAIL"
    assert "RB" in res.details


def test_ruby_structure_fail_when_invalid_child() -> None:
    elems = [
        _struct_elem(0, "Ruby", children_indices=[1, 2, 3]),
        _struct_elem(1, "RB", parent_index=0),
        _struct_elem(2, "RT", parent_index=0),
        _struct_elem(3, "P", parent_index=0),
    ]
    res = _only(check_ruby_structure_valid(_ctx(elements=elems)))
    assert res.result == "FAIL"
    assert "invalid child P" in res.details


# ---------------------------------------------------------------------------
# check_warichu_structure_valid
# ---------------------------------------------------------------------------


def test_warichu_structure_pass_with_wt_wp() -> None:
    elems = [
        _struct_elem(0, "Warichu", children_indices=[1, 2]),
        _struct_elem(1, "WT", parent_index=0),
        _struct_elem(2, "WP", parent_index=0),
    ]
    res = _only(check_warichu_structure_valid(_ctx(elements=elems)))
    assert res.name == "Warichu structure valid"
    assert res.standard == "Matterhorn 09-008"
    assert res.result == "PASS"


def test_warichu_structure_fail_when_missing_wp() -> None:
    elems = [
        _struct_elem(0, "Warichu", children_indices=[1]),
        _struct_elem(1, "WT", parent_index=0),
    ]
    res = _only(check_warichu_structure_valid(_ctx(elements=elems)))
    assert res.result == "FAIL"
    assert "WP" in res.details


# ---------------------------------------------------------------------------
# check_note_tags_have_unique_ids
# ---------------------------------------------------------------------------


def test_note_tags_is_not_applicable_when_no_notes() -> None:
    elems = [_struct_elem(0, "Document")]
    res = _only(check_note_tags_have_unique_ids(_ctx(elements=elems)))
    assert res.name == "Note tags have unique IDs"
    assert res.standard == "Matterhorn 19-003"
    assert res.result == "NA"


def test_note_tags_pass_when_unique_ids() -> None:
    elems = [
        _struct_elem(0, "Note", obj=pikepdf.Dictionary({"/ID": pikepdf.String("n1")})),
        _struct_elem(1, "Note", obj=pikepdf.Dictionary({"/ID": pikepdf.String("n2")})),
    ]
    res = _only(check_note_tags_have_unique_ids(_ctx(elements=elems)))
    assert res.result == "PASS"


def test_note_tags_fail_when_missing_id() -> None:
    elems = [_struct_elem(0, "Note")]
    res = _only(check_note_tags_have_unique_ids(_ctx(elements=elems)))
    assert res.result == "FAIL"
    assert "missing /ID" in res.details


def test_note_tags_fail_when_duplicate_id() -> None:
    elems = [
        _struct_elem(0, "Note", obj=pikepdf.Dictionary({"/ID": pikepdf.String("dup")})),
        _struct_elem(1, "Note", obj=pikepdf.Dictionary({"/ID": pikepdf.String("dup")})),
    ]
    res = _only(check_note_tags_have_unique_ids(_ctx(elements=elems)))
    assert res.result == "FAIL"
    assert "duplicate /ID" in res.details


# ---------------------------------------------------------------------------
# check_formula_alt_text
# ---------------------------------------------------------------------------


def test_formula_alt_text_is_not_applicable_when_no_formulas() -> None:
    elems = [_struct_elem(0, "P")]
    res = _only(check_formula_alt_text(_ctx(elements=elems)))
    assert res.name == "Formula elements have alt text or ActualText"
    assert res.standard == "Matterhorn 17-002"
    assert res.result == "NA"


def test_formula_alt_text_pass_when_alt_set() -> None:
    elems = [_struct_elem(0, "Formula", alt_text="x squared")]
    res = _only(check_formula_alt_text(_ctx(elements=elems)))
    assert res.result == "PASS"


def test_formula_alt_text_pass_when_actual_text_set() -> None:
    elems = [_struct_elem(0, "Formula", actual_text="x²")]
    res = _only(check_formula_alt_text(_ctx(elements=elems)))
    assert res.result == "PASS"


def test_formula_alt_text_fail_when_missing_both() -> None:
    elems = [_struct_elem(0, "Formula")]
    res = _only(check_formula_alt_text(_ctx(elements=elems)))
    assert res.result == "FAIL"
    assert "/Alt" in res.details


# ---------------------------------------------------------------------------
# check_mathml_associated_with_formula
# ---------------------------------------------------------------------------


def test_mathml_is_not_applicable_when_no_formulas() -> None:
    res = _only(check_mathml_associated_with_formula(_ctx(elements=[_struct_elem(0, "P")])))
    assert res.standard == "PDF/UA-2"
    assert res.result == "NA"


def test_mathml_pass_when_af_present() -> None:
    obj = pikepdf.Dictionary({"/AF": pikepdf.Array([])})
    elems = [_struct_elem(0, "Formula", obj=obj)]
    res = _only(check_mathml_associated_with_formula(_ctx(elements=elems)))
    assert res.result == "PASS"


def test_mathml_pass_when_alt_text_carries_mathml() -> None:
    elems = [_struct_elem(0, "Formula", alt_text="<math>...</math>")]
    res = _only(check_mathml_associated_with_formula(_ctx(elements=elems)))
    assert res.result == "PASS"


def test_mathml_warn_when_neither_af_nor_mathml() -> None:
    elems = [_struct_elem(0, "Formula", alt_text="x²")]
    res = _only(check_mathml_associated_with_formula(_ctx(elements=elems)))
    assert res.result == "WARN"


# ---------------------------------------------------------------------------
# check_associated_files_on_embedded_content
# ---------------------------------------------------------------------------


def test_af_embedded_is_not_applicable_when_no_target_tags() -> None:
    elems = [_struct_elem(0, "P")]
    res = _only(check_associated_files_on_embedded_content(_ctx(elements=elems)))
    assert res.name == "Associated Files property on embedded content"
    assert res.standard == "PDF/UA-2"
    assert res.result == "NA"


def test_af_embedded_pass_when_af_present_on_all() -> None:
    obj = pikepdf.Dictionary({"/AF": pikepdf.Array([])})
    elems = [_struct_elem(0, "Figure", obj=obj), _struct_elem(1, "Formula", obj=obj)]
    res = _only(check_associated_files_on_embedded_content(_ctx(elements=elems)))
    assert res.result == "PASS"


def test_af_embedded_warn_when_some_missing() -> None:
    obj = pikepdf.Dictionary({"/AF": pikepdf.Array([])})
    elems = [
        _struct_elem(0, "Figure", obj=obj),
        _struct_elem(1, "Formula"),
    ]
    res = _only(check_associated_files_on_embedded_content(_ctx(elements=elems)))
    assert res.result == "WARN"
    assert "Formula" in res.details


# ---------------------------------------------------------------------------
# check_optional_content_groups_have_name
# ---------------------------------------------------------------------------


def test_ocg_name_is_not_applicable_when_no_ocproperties() -> None:
    pdf = pikepdf.Pdf.new()
    res = _only(check_optional_content_groups_have_name(_ctx(pdf)))
    assert res.name == "Optional content groups have Name"
    assert res.standard == "Matterhorn 20-001"
    assert res.result == "NA"


def test_ocg_name_is_not_applicable_when_no_ocgs() -> None:
    pdf = pikepdf.Pdf.new()
    pdf.Root["/OCProperties"] = pikepdf.Dictionary({"/OCGs": pikepdf.Array([])})
    res = _only(check_optional_content_groups_have_name(_ctx(pdf)))
    assert res.result == "NA"


def test_ocg_name_pass_when_all_named() -> None:
    pdf = pikepdf.Pdf.new()
    ocg = pdf.make_indirect(
        pikepdf.Dictionary({"/Type": pikepdf.Name("/OCG"), "/Name": pikepdf.String("Layer 1")})
    )
    pdf.Root["/OCProperties"] = pikepdf.Dictionary({"/OCGs": pikepdf.Array([ocg])})
    res = _only(check_optional_content_groups_have_name(_ctx(pdf)))
    assert res.result == "PASS"


def test_ocg_name_fail_when_unnamed() -> None:
    pdf = pikepdf.Pdf.new()
    ocg = pdf.make_indirect(pikepdf.Dictionary({"/Type": pikepdf.Name("/OCG")}))
    pdf.Root["/OCProperties"] = pikepdf.Dictionary({"/OCGs": pikepdf.Array([ocg])})
    res = _only(check_optional_content_groups_have_name(_ctx(pdf)))
    assert res.result == "FAIL"


# ---------------------------------------------------------------------------
# check_optional_content_no_as_entry
# ---------------------------------------------------------------------------


def test_ocg_no_as_is_not_applicable_when_no_ocproperties() -> None:
    pdf = pikepdf.Pdf.new()
    res = _only(check_optional_content_no_as_entry(_ctx(pdf)))
    assert res.name == "Optional content has no AS entry"
    assert res.standard == "Matterhorn 20-002"
    assert res.result == "NA"


def test_ocg_no_as_pass_when_no_d_dict() -> None:
    pdf = pikepdf.Pdf.new()
    pdf.Root["/OCProperties"] = pikepdf.Dictionary({"/OCGs": pikepdf.Array([])})
    res = _only(check_optional_content_no_as_entry(_ctx(pdf)))
    assert res.result == "PASS"


def test_ocg_no_as_fail_when_as_present() -> None:
    pdf = pikepdf.Pdf.new()
    pdf.Root["/OCProperties"] = pikepdf.Dictionary(
        {
            "/OCGs": pikepdf.Array([]),
            "/D": pikepdf.Dictionary({"/AS": pikepdf.Array([])}),
        }
    )
    res = _only(check_optional_content_no_as_entry(_ctx(pdf)))
    assert res.result == "FAIL"


# ---------------------------------------------------------------------------
# check_embedded_files_have_f_and_uf
# ---------------------------------------------------------------------------


def test_embedded_files_is_not_applicable_when_no_embedded_files() -> None:
    pdf = pikepdf.Pdf.new()
    res = _only(check_embedded_files_have_f_and_uf(_ctx(pdf)))
    assert res.name == "Embedded files have F and UF keys"
    assert res.standard == "Matterhorn 21-001"
    assert res.result == "NA"


def test_embedded_files_pass_when_all_keys_set() -> None:
    pdf = pikepdf.Pdf.new()
    fs = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Filespec"),
                "/F": pikepdf.String("a.txt"),
                "/UF": pikepdf.String("a.txt"),
            }
        )
    )
    pdf.Root["/Names"] = pikepdf.Dictionary(
        {
            "/EmbeddedFiles": pikepdf.Dictionary(
                {"/Names": pikepdf.Array([pikepdf.String("a.txt"), fs])}
            )
        }
    )
    res = _only(check_embedded_files_have_f_and_uf(_ctx(pdf)))
    assert res.result == "PASS"


def test_embedded_files_fail_when_missing_uf() -> None:
    pdf = pikepdf.Pdf.new()
    fs = pdf.make_indirect(
        pikepdf.Dictionary(
            {"/Type": pikepdf.Name("/Filespec"), "/F": pikepdf.String("a.txt")}
        )
    )
    pdf.Root["/Names"] = pikepdf.Dictionary(
        {
            "/EmbeddedFiles": pikepdf.Dictionary(
                {"/Names": pikepdf.Array([pikepdf.String("a.txt"), fs])}
            )
        }
    )
    res = _only(check_embedded_files_have_f_and_uf(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "/UF" in res.details


# ---------------------------------------------------------------------------
# check_no_reference_xobjects
# ---------------------------------------------------------------------------


def test_no_reference_xobjects_pass_when_no_xobjects() -> None:
    pdf = pikepdf.Pdf.new()
    _add_blank_page(pdf)
    res = _only(check_no_reference_xobjects(_ctx(pdf)))
    assert res.name == "No Reference XObjects"
    assert res.standard == "Matterhorn 30-001"
    assert res.result == "PASS"


def test_no_reference_xobjects_fail_when_ref_present() -> None:
    pdf = pikepdf.Pdf.new()
    page = _add_blank_page(pdf)
    xobj = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/XObject"),
                "/Subtype": pikepdf.Name("/Form"),
                "/Ref": pikepdf.Dictionary({"/F": pikepdf.String("ext.pdf")}),
            }
        )
    )
    page.obj["/Resources"] = pikepdf.Dictionary(
        {"/XObject": pikepdf.Dictionary({"/X1": xobj})}
    )
    res = _only(check_no_reference_xobjects(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "1" in res.details


# ---------------------------------------------------------------------------
# check_form_xobjects_with_mcids_not_reused
# ---------------------------------------------------------------------------


def test_form_xobjects_mcids_pass_when_no_form_xobjects() -> None:
    pdf = pikepdf.Pdf.new()
    _add_blank_page(pdf)
    res = _only(check_form_xobjects_with_mcids_not_reused(_ctx(pdf)))
    assert res.name == "Form XObjects with MCIDs not reused"
    assert res.standard == "Matterhorn 30-002"
    assert res.result == "PASS"


def test_form_xobjects_mcids_pass_when_form_xobject_used_once() -> None:
    pdf = pikepdf.Pdf.new()
    page = _add_blank_page(pdf)
    # Form XObject without MCID — reuse is OK.
    form_stream = _make_form_xobject(
        pdf,
        b"q Q",
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/XObject"),
                "/Subtype": pikepdf.Name("/Form"),
                "/BBox": pikepdf.Array([0, 0, 100, 100]),
            }
        ),
    )
    page.obj["/Resources"] = pikepdf.Dictionary(
        {"/XObject": pikepdf.Dictionary({"/F1": form_stream})}
    )
    res = _only(check_form_xobjects_with_mcids_not_reused(_ctx(pdf)))
    assert res.result == "PASS"


# ---------------------------------------------------------------------------
# check_structure_destinations_for_intra_links
# ---------------------------------------------------------------------------


def test_intra_links_is_not_applicable_when_no_links() -> None:
    pdf = pikepdf.Pdf.new()
    _add_blank_page(pdf)
    res = _only(check_structure_destinations_for_intra_links(_ctx(pdf)))
    assert res.name == "Structure destinations for intra-document links"
    assert res.standard == "PDF/UA-2"
    assert res.result == "NA"


def test_intra_links_pass_when_all_have_sd() -> None:
    pdf = pikepdf.Pdf.new()
    page = _add_blank_page(pdf)
    annot = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Annot"),
                "/Subtype": pikepdf.Name("/Link"),
                "/Dest": pikepdf.Array([0]),
                "/SD": pikepdf.Array([0]),
            }
        )
    )
    page.obj["/Annots"] = pikepdf.Array([annot])
    res = _only(check_structure_destinations_for_intra_links(_ctx(pdf)))
    assert res.result == "PASS"


def test_intra_links_warn_when_missing_sd() -> None:
    pdf = pikepdf.Pdf.new()
    page = _add_blank_page(pdf)
    annot = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Annot"),
                "/Subtype": pikepdf.Name("/Link"),
                "/Dest": pikepdf.Array([0]),
            }
        )
    )
    page.obj["/Annots"] = pikepdf.Array([annot])
    res = _only(check_structure_destinations_for_intra_links(_ctx(pdf)))
    assert res.result == "WARN"
    assert "1" in res.details


# ---------------------------------------------------------------------------
# check_pdfua2_heading_hierarchy
# ---------------------------------------------------------------------------


def test_pdfua2_heading_hierarchy_pass_when_no_skips() -> None:
    elems = [
        _struct_elem(0, "Sect", children_indices=[1, 2]),
        _struct_elem(1, "H1", parent_index=0),
        _struct_elem(2, "H2", parent_index=0),
    ]
    res = _only(check_pdfua2_heading_hierarchy(_ctx(elements=elems)))
    assert res.name == "PDF/UA-2 heading hierarchy"
    assert res.standard == "PDF/UA-2"
    assert res.result == "PASS"


def test_pdfua2_heading_hierarchy_warn_on_skip() -> None:
    elems = [
        _struct_elem(0, "Sect", children_indices=[1, 2]),
        _struct_elem(1, "H1", parent_index=0),
        _struct_elem(2, "H4", parent_index=0),
    ]
    res = _only(check_pdfua2_heading_hierarchy(_ctx(elements=elems)))
    assert res.result == "WARN"
    assert "H1->H4" in res.details


# ---------------------------------------------------------------------------
# check_reading_order_matches_visual_layout
# ---------------------------------------------------------------------------


def test_reading_order_warn_when_no_visual_data() -> None:
    res = _only(check_reading_order_matches_visual_layout(_ctx()))
    assert res.name == "Reading order matches visual layout"
    assert res.standard == "WCAG 1.3.2"
    assert res.result == "WARN"
    assert "Could not extract" in res.details


def test_reading_order_pass_when_no_mismatches() -> None:
    ctx = _ctx(
        elements=[
            _struct_elem(0, "P", mcids=[0]),
            _struct_elem(1, "P", mcids=[1]),
        ]
    )
    ctx.visual_blocks = [
        VisualBlock(page=0, x0=0, y0=0, x1=10, y1=10, y_top=0, text="a"),
    ]
    ctx.page_dimensions = [PageDimensions(width=612, height=792)]
    ctx.columns = []
    ctx.element_positions = {
        0: ElementPosition(page=0, x0=0, y0=0, x1=10, y1=10, y_top=0),
        1: ElementPosition(page=0, x0=0, y0=20, x1=10, y1=30, y_top=20),
    }
    ctx.visual_reading_order = [0, 1]
    ctx.reading_order_mismatches = []
    res = _only(check_reading_order_matches_visual_layout(ctx))
    assert res.result == "PASS"
    assert "100%" in res.details


def test_reading_order_fail_when_many_mismatches() -> None:
    # Build five elements with all five visual ranks inverted so that
    # the formula 1 - (n / (5 * 5 / 2)) = 1 - 10/12.5 = 0.20 < 0.7,
    # producing a FAIL.
    elems = [_struct_elem(i, "P", mcids=[i]) for i in range(5)]
    ctx = _ctx(elements=elems)
    ctx.visual_blocks = [
        VisualBlock(page=0, x0=0, y0=0, x1=10, y1=10, y_top=0, text="x"),
    ]
    ctx.page_dimensions = [PageDimensions(width=612, height=792)]
    ctx.columns = []
    ctx.element_positions = {
        i: ElementPosition(page=0, x0=0, y0=i * 10.0, x1=10, y1=i * 10.0 + 5, y_top=i * 10.0)
        for i in range(5)
    }
    ctx.visual_reading_order = [4, 3, 2, 1, 0]
    # 10 inversions: every pair (i, j) with i < j has visual_rank[i] > visual_rank[j].
    ctx.reading_order_mismatches = [
        ReadingOrderMismatch(struct_first=i, struct_second=j, visual_first=j, visual_second=i)
        for i in range(5)
        for j in range(i + 1, 5)
    ]
    res = _only(check_reading_order_matches_visual_layout(ctx))
    assert res.result == "FAIL"


def test_reading_order_warn_in_middle_band() -> None:
    # Two mismatches across five elements: correlation = 1 - 2/12.5 = 0.84,
    # which is between 0.7 and 0.9 → WARN.
    elems = [_struct_elem(i, "P", mcids=[i]) for i in range(5)]
    ctx = _ctx(elements=elems)
    ctx.visual_blocks = [
        VisualBlock(page=0, x0=0, y0=0, x1=10, y1=10, y_top=0, text="x"),
    ]
    ctx.page_dimensions = [PageDimensions(width=612, height=792)]
    ctx.columns = []
    ctx.element_positions = {
        i: ElementPosition(page=0, x0=0, y0=i * 10.0, x1=10, y1=i * 10.0 + 5, y_top=i * 10.0)
        for i in range(5)
    }
    ctx.visual_reading_order = [1, 0, 2, 4, 3]
    ctx.reading_order_mismatches = [
        ReadingOrderMismatch(struct_first=0, struct_second=1, visual_first=1, visual_second=0),
        ReadingOrderMismatch(struct_first=3, struct_second=4, visual_first=4, visual_second=3),
    ]
    res = _only(check_reading_order_matches_visual_layout(ctx))
    assert res.result == "WARN"


# ---------------------------------------------------------------------------
# check_no_empty_tags
# ---------------------------------------------------------------------------

def test_empty_leaf_tags_are_warned_about() -> None:
    empty = _struct_elem(0, "P")
    filled = _struct_elem(1, "P")
    filled.text_content = "real text"

    result = _only(check_no_empty_tags(_ctx(elements=[empty, filled])))

    assert result.result == "WARN"
    assert "[1] P" in result.details


def test_a_grouping_tag_with_no_content_is_not_a_fault() -> None:
    """An empty Sect groups nothing yet; that is odd, not inaccessible."""
    result = _only(check_no_empty_tags(_ctx(elements=[_struct_elem(0, "Sect")])))

    assert result.result == "PASS"


def test_an_element_described_only_by_alt_text_is_not_empty() -> None:
    figure = _struct_elem(0, "Figure", alt_text="A chart")

    result = _only(check_no_empty_tags(_ctx(elements=[figure])))

    assert result.result == "PASS"


def test_an_element_holding_marked_content_is_not_empty() -> None:
    span = _struct_elem(0, "Span", mcids=[3])

    result = _only(check_no_empty_tags(_ctx(elements=[span])))

    assert result.result == "PASS"


def test_no_structure_makes_empty_tags_not_applicable() -> None:
    result = _only(check_no_empty_tags(_ctx(elements=[])))

    assert result.result == "NA"


# ---------------------------------------------------------------------------
# check_correct_nesting
# ---------------------------------------------------------------------------

def test_a_paragraph_inside_a_paragraph_fails() -> None:
    outer = _struct_elem(0, "P", children_indices=[1])
    inner = _struct_elem(1, "P", parent_index=0)

    result = _only(check_correct_nesting(_ctx(elements=[outer, inner])))

    assert result.result == "FAIL"
    assert "P inside P" in result.details


def test_a_heading_inside_a_heading_fails() -> None:
    outer = _struct_elem(0, "H1", children_indices=[1])
    inner = _struct_elem(1, "H3", parent_index=0)

    result = _only(check_correct_nesting(_ctx(elements=[outer, inner])))

    assert result.result == "FAIL"
    assert "H3 inside H1" in result.details


def test_a_cell_outside_a_row_fails() -> None:
    """The positional half of the check.

    A cell that is not in a row is not in a column either, so every
    header association in that table is unreliable.
    """
    table = _struct_elem(0, "Table", children_indices=[1])
    cell = _struct_elem(1, "TD", parent_index=0)

    result = _only(check_correct_nesting(_ctx(elements=[table, cell])))

    assert result.result == "FAIL"
    assert "TD inside Table" in result.details


def test_an_item_outside_a_list_fails() -> None:
    section = _struct_elem(0, "Sect", children_indices=[1])
    item = _struct_elem(1, "LI", parent_index=0)

    result = _only(check_correct_nesting(_ctx(elements=[section, item])))

    assert result.result == "FAIL"


def test_a_well_formed_table_passes() -> None:
    table = _struct_elem(0, "Table", children_indices=[1])
    row = _struct_elem(1, "TR", parent_index=0, children_indices=[2])
    cell = _struct_elem(2, "TD", parent_index=1)

    result = _only(check_correct_nesting(_ctx(elements=[table, row, cell])))

    assert result.result == "PASS"


def test_a_row_inside_a_row_group_passes() -> None:
    table = _struct_elem(0, "Table", children_indices=[1])
    body = _struct_elem(1, "TBody", parent_index=0, children_indices=[2])
    row = _struct_elem(2, "TR", parent_index=1)

    result = _only(check_correct_nesting(_ctx(elements=[table, body, row])))

    assert result.result == "PASS"


def test_a_heading_inside_a_paragraph_is_not_a_nesting_violation() -> None:
    # Unusual, but not a rule this check enforces — only same-kind
    # nesting and misplaced positional elements are violations.
    paragraph = _struct_elem(0, "P", children_indices=[1])
    heading = _struct_elem(1, "H2", parent_index=0)

    result = _only(check_correct_nesting(_ctx(elements=[paragraph, heading])))

    assert result.result == "PASS"


# ---------------------------------------------------------------------------
# check_all_content_tagged / check_artifact_classification_subtypes
# ---------------------------------------------------------------------------


def _page_class(
    page_number: int,
    *,
    text_operators: int = 0,
    mcid_marks: int = 0,
    artifact_marks: tuple[ArtifactMark, ...] = (),
) -> PageContentClassification:
    return PageContentClassification(
        page_number=page_number,
        artifact_inside_tagged=0,
        tagged_inside_artifact=0,
        untagged_text_operators=0,
        untagged_image_operators=0,
        text_operators=text_operators,
        mcid_marks=mcid_marks,
        artifact_marks=artifact_marks,
    )


def _classified_ctx(
    pages: list[PageContentClassification],
    elements: list[StructElement] | None = None,
) -> AuditContext:
    ctx = _ctx(elements=elements)
    ctx.content_classification = pages
    return ctx


def test_all_content_tagged_is_not_applicable_without_classification() -> None:
    res = _only(check_all_content_tagged(_ctx()))
    assert res.result == "NA"


def test_all_content_tagged_passes_when_every_texted_page_has_mcids() -> None:
    pages = [_page_class(1, text_operators=5, mcid_marks=3)]
    res = _only(check_all_content_tagged(_classified_ctx(pages)))
    assert res.result == "PASS"


def test_all_content_tagged_fails_on_a_page_with_text_and_no_mcids() -> None:
    pages = [
        _page_class(1, text_operators=5, mcid_marks=3),
        _page_class(2, text_operators=4, mcid_marks=0),
    ]
    res = _only(check_all_content_tagged(_classified_ctx(pages)))
    assert res.result == "FAIL"
    assert "page(s) 2" in res.details


def test_all_content_tagged_ignores_a_page_with_no_text() -> None:
    """An image-only page is check_all_content_tagged_or_artifact's business."""
    pages = [_page_class(1, text_operators=0, mcid_marks=0)]
    res = _only(check_all_content_tagged(_classified_ctx(pages)))
    assert res.result == "PASS"


def test_artifact_subtypes_is_not_applicable_without_artifacts() -> None:
    pages = [_page_class(1, text_operators=2, mcid_marks=1)]
    res = _only(check_artifact_classification_subtypes(_classified_ctx(pages)))
    assert res.result == "NA"
    assert "No artifact markers" in res.details


def test_artifact_subtypes_passes_when_every_artifact_is_classified() -> None:
    marks = (ArtifactMark(has_subtype=True, nearest_mcid=None),)
    pages = [_page_class(1, artifact_marks=marks)]
    res = _only(check_artifact_classification_subtypes(_classified_ctx(pages)))
    assert res.result == "PASS"
    assert "All 1 artifact(s)" in res.details


def test_artifact_subtypes_warns_and_names_the_nearest_element() -> None:
    marks = (
        ArtifactMark(has_subtype=False, nearest_mcid=4),
        ArtifactMark(has_subtype=True, nearest_mcid=4),
    )
    element = _struct_elem(11, "P", mcids=[4])
    element.mcid_page_map = {4: 0}
    ctx = _classified_ctx([_page_class(1, artifact_marks=marks)], [element])

    res = _only(check_artifact_classification_subtypes(ctx))

    assert res.result == "WARN"
    assert "1 of 2 artifact(s)" in res.details
    assert "[12] p.1" in res.details


def test_artifact_subtypes_matches_mcids_within_their_own_page() -> None:
    """MCIDs repeat per page, so a same-numbered element elsewhere must not win."""
    marks = (ArtifactMark(has_subtype=False, nearest_mcid=1),)
    other_page = _struct_elem(3, "P", mcids=[1])
    other_page.mcid_page_map = {1: 5}
    ctx = _classified_ctx([_page_class(1, artifact_marks=marks)], [other_page])

    res = _only(check_artifact_classification_subtypes(ctx))

    assert res.result == "WARN"
    assert "[4]" not in res.details
    assert "p.1" in res.details


# ---------------------------------------------------------------------------
# check_formula_unicode_mapping_valid
# ---------------------------------------------------------------------------


def _font_metadata(mapping: dict[int, str]) -> FontMetadata:
    detail = FontInfoDetail(
        font_name="/F1",
        base_font="/ABCDEF+MathFont",
        subtype="/Type1",
        is_symbolic=False,
        has_to_unicode=True,
        to_unicode=FontUnicodeMapping(
            mapping=mapping,
            byte_width=1,
            raw_bytes=b"",
            has_invalid_unicode=False,
            invalid_codepoints=[],
        ),
        encoding_differences=None,
        has_identity_h_or_v=False,
        cmap_wmode=None,
        cid_font_wmode=None,
        glyph_widths_count=None,
        widths_first_char=None,
        widths_last_char=None,
        cidtogidmap=None,
        has_cid_font_file=False,
        cmap_name=None,
        cmap_embedded=False,
        encoding_kind="name",
        encoding_name="/WinAnsiEncoding",
        page=1,
        is_cid_type2=False,
        has_font_file=True,
    )
    return FontMetadata(fonts=[detail])


def _formula_ctx(metadata: FontMetadata | None) -> AuditContext:
    ctx = _ctx(elements=[_struct_elem(0, "Formula")])
    ctx.font_metadata = metadata
    return ctx


def test_formula_unicode_is_not_applicable_without_formula_elements() -> None:
    ctx = _ctx(elements=[_struct_elem(0, "P")])
    ctx.font_metadata = _font_metadata({1: ""})
    res = _only(check_formula_unicode_mapping_valid(ctx))
    assert res.result == "NA"
    assert "No Formula elements" in res.details


def test_formula_unicode_is_not_applicable_without_font_metadata() -> None:
    res = _only(check_formula_unicode_mapping_valid(_formula_ctx(None)))
    assert res.result == "NA"
    assert "Font metadata" in res.details


def test_formula_unicode_passes_on_real_code_points() -> None:
    ctx = _formula_ctx(_font_metadata({1: "∑", 2: "α"}))
    res = _only(check_formula_unicode_mapping_valid(ctx))
    assert res.result == "PASS"


def test_formula_unicode_warns_on_private_use_code_points() -> None:
    ctx = _formula_ctx(_font_metadata({1: "", 2: "x"}))
    res = _only(check_formula_unicode_mapping_valid(ctx))
    assert res.result == "WARN"
    assert "U+E001" in res.details
    assert "/ABCDEF+MathFont" in res.details


def test_formula_unicode_warns_on_supplementary_private_use_area() -> None:
    """Planes 15 and 16 are private-use too, not just the BMP block."""
    ctx = _formula_ctx(_font_metadata({1: "\U000f0001"}))
    res = _only(check_formula_unicode_mapping_valid(ctx))
    assert res.result == "WARN"
    assert "U+F0001" in res.details
