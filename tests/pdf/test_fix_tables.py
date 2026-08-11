"""Tests for the table-structure fixes.

The point of tagging a table properly is that a screen reader can
announce, for any cell, which headers describe it. So these tests care
about which cell got changed and what scope it ended up with — a fix that
retags the neighbouring cell is worse than one that does nothing, because
the table then asserts a structure it does not have.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name

from auto_a11y.pdf.audit.structure import walk_structure_tree
from auto_a11y.pdf.fix.models import FixOptions
from auto_a11y.pdf.fix.tables import (
    fix_table_headers,
    fix_table_scope,
    fix_table_sections,
)


@pytest.fixture
def opts(tmp_path: Path) -> FixOptions:
    return FixOptions(pdf_path=tmp_path / "table.pdf")


def _elem(pdf: pikepdf.Pdf, tag: str, kids: list[pikepdf.Object] | None = None
          ) -> pikepdf.Object:
    """A structure element whose children point back at it.

    A real tagged PDF carries /P throughout; without it a check of
    pointer consistency would fail on nodes the fix never touched.
    """
    node = Dictionary(Type=Name("/StructElem"), S=Name(f"/{tag}"))
    obj = pdf.make_indirect(node)
    if kids:
        obj[Name("/K")] = Array(kids)
        for kid in kids:
            if isinstance(kid, pikepdf.Dictionary):
                kid[Name("/P")] = obj
    return obj


def _table_pdf(rows: list[list[str]], *, group: str | None = None) -> pikepdf.Pdf:
    """A PDF holding one table whose cells carry the given tags."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    row_objs = [
        _elem(pdf, "TR", [_elem(pdf, tag) for tag in row]) for row in rows
    ]
    body = [_elem(pdf, group, row_objs)] if group else row_objs
    table = _elem(pdf, "Table", body)
    struct_root = pdf.make_indirect(Dictionary(Type=Name("/StructTreeRoot")))
    struct_root[Name("/K")] = Array([table])
    pdf.Root[Name("/StructTreeRoot")] = struct_root
    return pdf


def _tags(pdf: pikepdf.Pdf) -> list[str]:
    elements, _ = walk_structure_tree(pdf)
    return [e.resolved_tag for e in elements]


def _scope_of(pdf: pikepdf.Pdf, one_based: int) -> str | None:
    elements, _ = walk_structure_tree(pdf)
    value = elements[one_based - 1].obj.get(Name("/Scope"))
    return str(value) if value is not None else None


# ---------------------------------------------------------------------------
# fix_table_headers
# ---------------------------------------------------------------------------

def test_headers_use_one_based_references(opts: FixOptions) -> None:
    """The off-by-one this port corrects.

    The audit prints element references as index + 1 everywhere, and the
    alt-text fixes read them that way. The original table fix read the
    same references 0-based, so it retagged the cell before the one the
    user picked.
    """
    pdf = _table_pdf([["TD", "TD"]])
    elements, _ = walk_structure_tree(pdf)
    first_cell = next(e for e in elements if e.resolved_tag == "TD")
    reference = first_cell.index + 1

    result = fix_table_headers(
        pdf,
        FixOptions(pdf_path=opts.pdf_path, table_headers_map={str(reference): "TH"}),
    )

    assert result.success
    elements, _ = walk_structure_tree(pdf)
    assert elements[reference - 1].resolved_tag == "TH"


def test_headers_promotes_a_cell_and_gives_it_scope(opts: FixOptions) -> None:
    pdf = _table_pdf([["TD", "TD"], ["TD", "TD"]])
    elements, _ = walk_structure_tree(pdf)
    cells = [e for e in elements if e.resolved_tag == "TD"]

    result = fix_table_headers(
        pdf,
        FixOptions(
            pdf_path=opts.pdf_path,
            table_headers_map={str(cells[0].index + 1): "TH"},
        ),
    )

    assert result.success
    assert _scope_of(pdf, cells[0].index + 1) == "/Column"


def test_headers_demoting_removes_scope(opts: FixOptions) -> None:
    # Scope on a data cell is meaningless, and leaves a checker treating
    # the cell as a header.
    pdf = _table_pdf([["TH", "TD"]])
    elements, _ = walk_structure_tree(pdf)
    header = next(e for e in elements if e.resolved_tag == "TH")
    header.obj[Name("/Scope")] = Name("/Column")

    fix_table_headers(
        pdf,
        FixOptions(
            pdf_path=opts.pdf_path,
            table_headers_map={str(header.index + 1): "TD"},
        ),
    )

    assert _scope_of(pdf, header.index + 1) is None


def test_headers_refuses_an_element_that_is_not_a_cell(
    opts: FixOptions,
) -> None:
    pdf = _table_pdf([["TD"]])
    elements, _ = walk_structure_tree(pdf)
    table = next(e for e in elements if e.resolved_tag == "Table")

    result = fix_table_headers(
        pdf,
        FixOptions(
            pdf_path=opts.pdf_path,
            table_headers_map={str(table.index + 1): "TH"},
        ),
    )

    assert not result.success
    assert "not a table cell" in result.description
    assert _tags(pdf).count("Table") == 1, "the table itself must be untouched"


def test_headers_rejects_a_tag_that_is_not_th_or_td(opts: FixOptions) -> None:
    pdf = _table_pdf([["TD"]])

    result = fix_table_headers(
        pdf, FixOptions(pdf_path=opts.pdf_path, table_headers_map={"2": "Figure"})
    )

    assert not result.success


def test_headers_rejects_a_zero_reference(opts: FixOptions) -> None:
    pdf = _table_pdf([["TD"]])

    result = fix_table_headers(
        pdf, FixOptions(pdf_path=opts.pdf_path, table_headers_map={"0": "TH"})
    )

    assert not result.success


# ---------------------------------------------------------------------------
# fix_table_scope
# ---------------------------------------------------------------------------

def test_scope_first_row_headers_describe_their_column(
    opts: FixOptions,
) -> None:
    pdf = _table_pdf([["TH", "TH"], ["TH", "TD"]])
    elements, _ = walk_structure_tree(pdf)
    headers = [e for e in elements if e.resolved_tag == "TH"]

    result = fix_table_scope(pdf, opts)

    assert result.success
    assert _scope_of(pdf, headers[0].index + 1) == "/Column"
    assert _scope_of(pdf, headers[1].index + 1) == "/Column"


def test_scope_a_leading_header_on_a_later_row_describes_that_row(
    opts: FixOptions,
) -> None:
    pdf = _table_pdf([["TH", "TH"], ["TH", "TD"]])
    elements, _ = walk_structure_tree(pdf)
    row_header = [e for e in elements if e.resolved_tag == "TH"][2]

    fix_table_scope(pdf, opts)

    assert _scope_of(pdf, row_header.index + 1) == "/Row"


def test_scope_reaches_rows_inside_thead_and_tbody(opts: FixOptions) -> None:
    """Rows are usually wrapped in a section, not children of the table."""
    pdf = _table_pdf([["TH", "TH"], ["TH", "TD"]], group="TBody")
    elements, _ = walk_structure_tree(pdf)
    headers = [e for e in elements if e.resolved_tag == "TH"]

    result = fix_table_scope(pdf, opts)

    assert result.success
    assert _scope_of(pdf, headers[0].index + 1) == "/Column"
    assert _scope_of(pdf, headers[2].index + 1) == "/Row"


def test_scope_leaves_an_existing_declaration_alone(opts: FixOptions) -> None:
    """An existing scope came from someone who could see the table."""
    pdf = _table_pdf([["TH", "TH"]])
    elements, _ = walk_structure_tree(pdf)
    header = [e for e in elements if e.resolved_tag == "TH"][0]
    header.obj[Name("/Scope")] = Name("/Both")

    fix_table_scope(pdf, opts)

    assert _scope_of(pdf, header.index + 1) == "/Both"


def test_scope_ignores_data_cells(opts: FixOptions) -> None:
    pdf = _table_pdf([["TD", "TD"]])
    elements, _ = walk_structure_tree(pdf)
    cell = [e for e in elements if e.resolved_tag == "TD"][0]

    result = fix_table_scope(pdf, opts)

    assert result.success
    assert _scope_of(pdf, cell.index + 1) is None


def test_scope_reports_a_document_with_no_tables(opts: FixOptions) -> None:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    struct_root = pdf.make_indirect(Dictionary(Type=Name("/StructTreeRoot")))
    struct_root[Name("/K")] = Array([_elem(pdf, "P")])
    pdf.Root[Name("/StructTreeRoot")] = struct_root

    result = fix_table_scope(pdf, opts)

    assert result.success
    assert "No tables" in result.description


# ---------------------------------------------------------------------------
# fix_table_sections
# ---------------------------------------------------------------------------

def _depth_shape(pdf: pikepdf.Pdf) -> list[tuple[int, str]]:
    elements, _ = walk_structure_tree(pdf)
    by_index = {e.index: e for e in elements}
    depths: dict[int, int] = {}
    for element in elements:
        parent = by_index.get(element.parent_index)
        depths[element.index] = 0 if parent is None else depths[parent.index] + 1
    return [(depths[e.index], e.resolved_tag) for e in elements]


def test_sections_split_a_header_row_from_the_body(opts: FixOptions) -> None:
    pdf = _table_pdf([["TH", "TH"], ["TD", "TD"], ["TD", "TD"]])

    result = fix_table_sections(pdf, opts)

    assert result.success
    shape = _depth_shape(pdf)
    assert (1, "THead") in shape
    assert (1, "TBody") in shape
    # Rows moved under the sections rather than staying on the table.
    assert all(depth != 1 for depth, tag in shape if tag == "TR")


def test_sections_put_every_row_in_the_body_without_a_header_row(
    opts: FixOptions,
) -> None:
    """No TH in the first row means no evidence of a header.

    Promoting the first row anyway would assert a header that the table
    does not have, and screen readers would then announce its values as
    the headers for every column.
    """
    pdf = _table_pdf([["TD", "TD"], ["TD", "TD"]])

    result = fix_table_sections(pdf, opts)

    assert result.success
    shape = _depth_shape(pdf)
    assert (1, "THead") not in shape
    assert (1, "TBody") in shape


def test_sections_leave_a_table_that_already_has_them(
    opts: FixOptions,
) -> None:
    pdf = _table_pdf([["TH", "TH"], ["TD", "TD"]], group="TBody")
    before = _depth_shape(pdf)

    result = fix_table_sections(pdf, opts)

    assert result.success
    assert _depth_shape(pdf) == before


def test_sections_skip_a_single_row_table(opts: FixOptions) -> None:
    # Nothing to separate; wrapping one row adds a level for no gain.
    pdf = _table_pdf([["TH", "TH"]])
    before = _depth_shape(pdf)

    result = fix_table_sections(pdf, opts)

    assert result.success
    assert _depth_shape(pdf) == before


def test_sections_keep_parent_pointers_consistent(opts: FixOptions) -> None:
    pdf = _table_pdf([["TH", "TH"], ["TD", "TD"]])

    fix_table_sections(pdf, opts)

    elements, _ = walk_structure_tree(pdf)
    by_index = {e.index: e for e in elements}
    for element in elements:
        parent = by_index.get(element.parent_index)
        if parent is None:
            continue
        declared = element.obj.get(Name("/P"))
        assert declared is not None
        assert declared.objgen == parent.obj.objgen


def test_sections_preserve_a_caption_ahead_of_the_rows(
    opts: FixOptions,
) -> None:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    rows = [
        _elem(pdf, "TR", [_elem(pdf, "TH"), _elem(pdf, "TH")]),
        _elem(pdf, "TR", [_elem(pdf, "TD"), _elem(pdf, "TD")]),
    ]
    table = _elem(pdf, "Table", [_elem(pdf, "Caption"), *rows])
    struct_root = pdf.make_indirect(Dictionary(Type=Name("/StructTreeRoot")))
    struct_root[Name("/K")] = Array([table])
    pdf.Root[Name("/StructTreeRoot")] = struct_root

    result = fix_table_sections(pdf, opts)

    assert result.success
    tags = [tag for _, tag in _depth_shape(pdf)]
    assert tags.index("Caption") < tags.index("THead")
