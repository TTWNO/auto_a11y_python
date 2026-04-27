"""Tests for ``auto_a11y.pdf.audit.checks.lists``.

Four list-structure checks ported from pdfMax's
``pdf_accessibility_audit.py`` (lines ~6607-6731): list structure,
empty lists, list nesting, and list-item labels.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf

from auto_a11y.pdf.audit.checks.lists import (
    LIST_CHECKS,
    check_list_item_labels,
    check_list_nesting_valid,
    check_list_structure_valid,
    check_no_empty_lists,
)
from auto_a11y.pdf.audit.structure import StructElement
from auto_a11y.pdf.models import AuditContext, CheckResult


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _ctx(elems: list[StructElement]) -> AuditContext:
    """Build a minimal ``AuditContext`` carrying just the structure elements."""
    return AuditContext(
        pdf=pikepdf.Pdf.new(),
        pdf_path=Path("/tmp/test.pdf"),
        elements=elems,
        role_map={},
    )


def _elem(
    index: int,
    tag: str,
    *,
    parent_index: int = -1,
    children_indices: list[int] | None = None,
) -> StructElement:
    """Build a structure element with sensible defaults."""
    return StructElement(
        index=index,
        custom_tag=f"/{tag}",
        resolved_tag=tag,
        alt_text=None,
        actual_text=None,
        lang=None,
        children_indices=children_indices if children_indices is not None else [],
        mcids=[],
        parent_index=parent_index,
        obj=pikepdf.Dictionary(),
    )


def _only(results: list[CheckResult]) -> CheckResult:
    """Assert single-element list and return its sole entry."""
    assert len(results) == 1, f"expected one CheckResult, got {len(results)}"
    return results[0]


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------


def test_list_checks_registry_lists_all_four() -> None:
    assert LIST_CHECKS == [
        check_list_structure_valid,
        check_no_empty_lists,
        check_list_nesting_valid,
        check_list_item_labels,
    ]


# ---------------------------------------------------------------------------
# check_list_structure_valid
# ---------------------------------------------------------------------------


def test_list_structure_pass_when_no_lists() -> None:
    res = _only(check_list_structure_valid(_ctx([_elem(0, "Document")])))
    assert res.result == "PASS"
    assert "No lists" in res.details


def test_list_structure_pass_for_well_formed_l_li_lbody() -> None:
    elems = [
        _elem(0, "L", children_indices=[1]),
        _elem(1, "LI", parent_index=0, children_indices=[2, 3]),
        _elem(2, "Lbl", parent_index=1),
        _elem(3, "LBody", parent_index=1),
    ]
    res = _only(check_list_structure_valid(_ctx(elems)))
    assert res.result == "PASS"
    assert res.standard == "PDF/UA, WCAG 1.3.1"


def test_list_structure_fail_for_non_li_under_l() -> None:
    # L has a P child instead of LI
    elems = [
        _elem(0, "L", children_indices=[1]),
        _elem(1, "P", parent_index=0),
    ]
    res = _only(check_list_structure_valid(_ctx(elems)))
    assert res.result == "FAIL"
    assert "non-LI" in res.details


def test_list_structure_fail_for_li_missing_lbl_and_lbody() -> None:
    elems = [
        _elem(0, "L", children_indices=[1]),
        _elem(1, "LI", parent_index=0, children_indices=[2]),
        _elem(2, "P", parent_index=1),
    ]
    res = _only(check_list_structure_valid(_ctx(elems)))
    assert res.result == "FAIL"
    assert "missing Lbl/LBody" in res.details


def test_list_structure_skips_truly_empty_list() -> None:
    # Truly empty L (no children) — handled by check_no_empty_lists, not this one
    elems = [_elem(0, "L")]
    res = _only(check_list_structure_valid(_ctx(elems)))
    assert res.result == "PASS"


# ---------------------------------------------------------------------------
# check_no_empty_lists
# ---------------------------------------------------------------------------


def test_no_empty_lists_pass_when_no_lists() -> None:
    res = _only(check_no_empty_lists(_ctx([])))
    assert res.result == "PASS"
    assert "No lists" in res.details


def test_no_empty_lists_pass_when_lists_have_items() -> None:
    elems = [
        _elem(0, "L", children_indices=[1]),
        _elem(1, "LI", parent_index=0),
    ]
    res = _only(check_no_empty_lists(_ctx(elems)))
    assert res.result == "PASS"


def test_no_empty_lists_fail_when_list_has_no_children() -> None:
    elems = [_elem(0, "L")]
    res = _only(check_no_empty_lists(_ctx(elems)))
    assert res.result == "FAIL"
    assert res.standard == "Matterhorn 09-006"
    assert "no list items" in res.details


# ---------------------------------------------------------------------------
# check_list_nesting_valid
# ---------------------------------------------------------------------------


def test_list_nesting_pass_for_correctly_nested_list() -> None:
    # L > LI > LBody > L > LI ... is the correct shape.
    elems = [
        _elem(0, "L", children_indices=[1]),
        _elem(1, "LI", parent_index=0, children_indices=[2]),
        _elem(2, "LBody", parent_index=1, children_indices=[3]),
        _elem(3, "L", parent_index=2),
    ]
    res = _only(check_list_nesting_valid(_ctx(elems)))
    assert res.result == "PASS"


def test_list_nesting_warn_when_l_is_direct_child_of_l() -> None:
    elems = [
        _elem(0, "L", children_indices=[1]),
        _elem(1, "L", parent_index=0),  # nested L direct under L
    ]
    res = _only(check_list_nesting_valid(_ctx(elems)))
    assert res.result == "WARN"
    assert "direct child of L" in res.details


def test_list_nesting_warn_when_l_is_direct_child_of_li() -> None:
    elems = [
        _elem(0, "L", children_indices=[1]),
        _elem(1, "LI", parent_index=0, children_indices=[2]),
        _elem(2, "L", parent_index=1),  # L direct under LI (no LBody wrapper)
    ]
    res = _only(check_list_nesting_valid(_ctx(elems)))
    assert res.result == "WARN"
    assert "direct child of LI" in res.details


# ---------------------------------------------------------------------------
# check_list_item_labels
# ---------------------------------------------------------------------------


def test_list_item_labels_pass_when_no_lists() -> None:
    res = _only(check_list_item_labels(_ctx([])))
    assert res.result == "PASS"


def test_list_item_labels_pass_when_every_li_has_lbl() -> None:
    elems = [
        _elem(0, "L", children_indices=[1]),
        _elem(1, "LI", parent_index=0, children_indices=[2, 3]),
        _elem(2, "Lbl", parent_index=1),
        _elem(3, "LBody", parent_index=1),
    ]
    res = _only(check_list_item_labels(_ctx(elems)))
    assert res.result == "PASS"


def test_list_item_labels_warn_when_li_missing_lbl() -> None:
    elems = [
        _elem(0, "L", children_indices=[1]),
        _elem(1, "LI", parent_index=0, children_indices=[2]),
        _elem(2, "LBody", parent_index=1),  # LBody but no Lbl
    ]
    res = _only(check_list_item_labels(_ctx(elems)))
    assert res.result == "WARN"
    assert "no Lbl" in res.details


def test_list_item_labels_skips_empty_li() -> None:
    # Empty LIs aren't this check's concern.
    elems = [
        _elem(0, "L", children_indices=[1]),
        _elem(1, "LI", parent_index=0),
    ]
    res = _only(check_list_item_labels(_ctx(elems)))
    assert res.result == "PASS"
