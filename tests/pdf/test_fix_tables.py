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
from auto_a11y.pdf.fix.tables import fix_table_headers, fix_table_scope


@pytest.fixture
def opts(tmp_path: Path) -> FixOptions:
    return FixOptions(pdf_path=tmp_path / "table.pdf")


def _elem(pdf: pikepdf.Pdf, tag: str, kids: list[pikepdf.Object] | None = None
          ) -> pikepdf.Object:
    node = Dictionary(Type=Name("/StructElem"), S=Name(f"/{tag}"))
    obj = pdf.make_indirect(node)
    if kids:
        obj[Name("/K")] = Array(kids)
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
