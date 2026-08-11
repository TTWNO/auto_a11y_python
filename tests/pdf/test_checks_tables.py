"""Tests for ``auto_a11y.pdf.audit.checks.tables``.

Six table-structure checks ported from pdfMax's
``pdf_accessibility_audit.py`` (lines ~6256-6526): table headers
defined, /Scope on TH, section wrappers, row regularity, no empty
tables, and table captions.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf

from auto_a11y.pdf.audit.checks.tables import (
    TABLE_CHECKS,
    check_no_empty_tables,
    check_table_captions,
    check_table_header_scope_defined,
    check_table_headers_defined,
    check_table_regularity,
    check_table_structure_sections,
)
from auto_a11y.pdf.audit.structure import StructElement
from auto_a11y.pdf.models import AuditContext, CheckResult


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _ctx(elems: list[StructElement]) -> AuditContext:
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
    obj: pikepdf.Dictionary | None = None,
    text_content: str = "",
) -> StructElement:
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
        obj=obj if obj is not None else pikepdf.Dictionary(),
        text_content=text_content,
    )


def _only(results: list[CheckResult]) -> CheckResult:
    assert len(results) == 1, f"expected one CheckResult, got {len(results)}"
    return results[0]


def _th_with_scope(index: int, parent_index: int, scope: str) -> StructElement:
    obj = pikepdf.Dictionary({"/Scope": pikepdf.Name(f"/{scope}")})
    return _elem(index, "TH", parent_index=parent_index, obj=obj)


def _td_with_colspan(
    index: int, parent_index: int, colspan: int
) -> StructElement:
    obj = pikepdf.Dictionary({"/ColSpan": colspan})
    return _elem(index, "TD", parent_index=parent_index, obj=obj)


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------


def test_table_checks_registry_lists_all_six() -> None:
    assert TABLE_CHECKS == [
        check_table_headers_defined,
        check_table_header_scope_defined,
        check_table_structure_sections,
        check_table_regularity,
        check_no_empty_tables,
        check_table_captions,
    ]


# ---------------------------------------------------------------------------
# check_table_headers_defined
# ---------------------------------------------------------------------------


def test_table_headers_is_not_applicable_when_no_tables() -> None:
    res = _only(check_table_headers_defined(_ctx([])))
    assert res.result == "NA"


def test_table_headers_pass_when_table_has_th() -> None:
    elems = [
        _elem(0, "Table", children_indices=[1]),
        _elem(1, "TR", parent_index=0, children_indices=[2, 3]),
        _th_with_scope(2, 1, "Column"),
        _elem(3, "TD", parent_index=1),
    ]
    res = _only(check_table_headers_defined(_ctx(elems)))
    assert res.result == "PASS"
    assert res.standard == "PDF/UA, WCAG 1.3.1"


def test_table_headers_fail_when_table_has_no_th() -> None:
    elems = [
        _elem(0, "Table", children_indices=[1]),
        _elem(1, "TR", parent_index=0, children_indices=[2]),
        _elem(2, "TD", parent_index=1, text_content="cell"),
    ]
    res = _only(check_table_headers_defined(_ctx(elems)))
    assert res.result == "FAIL"
    assert "missing TH" in res.details


def test_table_headers_skips_empty_table() -> None:
    elems = [_elem(0, "Table")]
    res = _only(check_table_headers_defined(_ctx(elems)))
    # Empty tables are caught by check_no_empty_tables; this one PASSes.
    assert res.result == "PASS"


# ---------------------------------------------------------------------------
# check_table_header_scope_defined
# ---------------------------------------------------------------------------


def test_table_header_scope_pass_when_all_th_have_valid_scope() -> None:
    elems = [
        _elem(0, "Table", children_indices=[1]),
        _elem(1, "TR", parent_index=0, children_indices=[2]),
        _th_with_scope(2, 1, "Column"),
    ]
    res = _only(check_table_header_scope_defined(_ctx(elems)))
    assert res.result == "PASS"


def test_table_header_scope_fail_when_th_missing_scope() -> None:
    elems = [
        _elem(0, "Table", children_indices=[1]),
        _elem(1, "TR", parent_index=0, children_indices=[2]),
        _elem(2, "TH", parent_index=1, text_content="Header"),
    ]
    res = _only(check_table_header_scope_defined(_ctx(elems)))
    assert res.result == "FAIL"
    assert "missing /Scope" in res.details


def test_table_header_scope_fail_when_th_invalid_scope() -> None:
    elems = [
        _elem(0, "Table", children_indices=[1]),
        _elem(1, "TR", parent_index=0, children_indices=[2]),
        _th_with_scope(2, 1, "Diagonal"),  # not Column/Row/Both
    ]
    res = _only(check_table_header_scope_defined(_ctx(elems)))
    assert res.result == "FAIL"
    assert "invalid scope" in res.details


def test_table_header_scope_through_thead_wrapper() -> None:
    # TH lives inside Table > THead > TR > TH; the BFS should still find it.
    elems = [
        _elem(0, "Table", children_indices=[1]),
        _elem(1, "THead", parent_index=0, children_indices=[2]),
        _elem(2, "TR", parent_index=1, children_indices=[3]),
        _th_with_scope(3, 2, "Column"),
    ]
    res = _only(check_table_header_scope_defined(_ctx(elems)))
    assert res.result == "PASS"


# ---------------------------------------------------------------------------
# check_table_structure_sections
# ---------------------------------------------------------------------------


def test_table_structure_sections_is_not_applicable_when_no_tables() -> None:
    res = _only(check_table_structure_sections(_ctx([])))
    assert res.result == "NA"


def test_table_structure_sections_pass_with_thead_tbody() -> None:
    elems = [
        _elem(0, "Table", children_indices=[1, 2]),
        _elem(1, "THead", parent_index=0),
        _elem(2, "TBody", parent_index=0),
    ]
    res = _only(check_table_structure_sections(_ctx(elems)))
    assert res.result == "PASS"


def test_table_structure_sections_warn_when_direct_tr_only() -> None:
    elems = [
        _elem(0, "Table", children_indices=[1]),
        _elem(1, "TR", parent_index=0),
    ]
    res = _only(check_table_structure_sections(_ctx(elems)))
    assert res.result == "WARN"
    assert "missing THead/TBody" in res.details


# ---------------------------------------------------------------------------
# check_table_regularity
# ---------------------------------------------------------------------------


def test_table_regularity_is_not_applicable_when_no_tables() -> None:
    res = _only(check_table_regularity(_ctx([])))
    assert res.result == "NA"


def test_table_regularity_pass_when_rows_consistent() -> None:
    # Two rows, three cells each.
    elems = [
        _elem(0, "Table", children_indices=[1, 5]),
        _elem(1, "TR", parent_index=0, children_indices=[2, 3, 4]),
        _elem(2, "TD", parent_index=1),
        _elem(3, "TD", parent_index=1),
        _elem(4, "TD", parent_index=1),
        _elem(5, "TR", parent_index=0, children_indices=[6, 7, 8]),
        _elem(6, "TD", parent_index=5),
        _elem(7, "TD", parent_index=5),
        _elem(8, "TD", parent_index=5),
    ]
    res = _only(check_table_regularity(_ctx(elems)))
    assert res.result == "PASS"


def test_table_regularity_warn_when_rows_inconsistent() -> None:
    # Row 1 has 2 cells, row 2 has 3.
    elems = [
        _elem(0, "Table", children_indices=[1, 4]),
        _elem(1, "TR", parent_index=0, children_indices=[2, 3]),
        _elem(2, "TD", parent_index=1),
        _elem(3, "TD", parent_index=1),
        _elem(4, "TR", parent_index=0, children_indices=[5, 6, 7]),
        _elem(5, "TD", parent_index=4),
        _elem(6, "TD", parent_index=4),
        _elem(7, "TD", parent_index=4),
    ]
    res = _only(check_table_regularity(_ctx(elems)))
    assert res.result == "WARN"
    assert "inconsistent cell counts" in res.details


def test_table_regularity_pass_when_colspan_balances_rows() -> None:
    # Row 1: TD with colspan=3. Row 2: 3x TD. Both rows logically have 3 cells.
    elems = [
        _elem(0, "Table", children_indices=[1, 3]),
        _elem(1, "TR", parent_index=0, children_indices=[2]),
        _td_with_colspan(2, 1, 3),
        _elem(3, "TR", parent_index=0, children_indices=[4, 5, 6]),
        _elem(4, "TD", parent_index=3),
        _elem(5, "TD", parent_index=3),
        _elem(6, "TD", parent_index=3),
    ]
    res = _only(check_table_regularity(_ctx(elems)))
    assert res.result == "PASS"


# ---------------------------------------------------------------------------
# check_no_empty_tables
# ---------------------------------------------------------------------------


def test_no_empty_tables_is_not_applicable_when_no_tables() -> None:
    res = _only(check_no_empty_tables(_ctx([])))
    assert res.result == "NA"


def test_no_empty_tables_pass_when_table_has_cells() -> None:
    elems = [
        _elem(0, "Table", children_indices=[1]),
        _elem(1, "TR", parent_index=0, children_indices=[2]),
        _elem(2, "TD", parent_index=1),
    ]
    res = _only(check_no_empty_tables(_ctx(elems)))
    assert res.result == "PASS"


def test_no_empty_tables_fail_when_table_has_no_cells() -> None:
    elems = [_elem(0, "Table")]
    res = _only(check_no_empty_tables(_ctx(elems)))
    assert res.result == "FAIL"
    assert "no data cells" in res.details


# ---------------------------------------------------------------------------
# check_table_captions
# ---------------------------------------------------------------------------


def test_table_captions_is_not_applicable_when_no_tables() -> None:
    res = _only(check_table_captions(_ctx([])))
    assert res.result == "NA"


def test_table_captions_pass_with_caption() -> None:
    elems = [
        _elem(0, "Table", children_indices=[1, 2]),
        _elem(1, "Caption", parent_index=0),
        _elem(2, "TR", parent_index=0, children_indices=[3]),
        _elem(3, "TD", parent_index=2),
    ]
    res = _only(check_table_captions(_ctx(elems)))
    assert res.result == "PASS"


def test_table_captions_warn_without_caption() -> None:
    elems = [
        _elem(0, "Table", children_indices=[1]),
        _elem(1, "TR", parent_index=0, children_indices=[2]),
        _elem(2, "TD", parent_index=1, text_content="data"),
    ]
    res = _only(check_table_captions(_ctx(elems)))
    assert res.result == "WARN"
    assert "missing Caption" in res.details


def test_table_captions_skips_empty_table() -> None:
    elems = [_elem(0, "Table")]
    res = _only(check_table_captions(_ctx(elems)))
    # Empty tables are flagged by check_no_empty_tables, not by Caption.
    assert res.result == "PASS"
