"""Tests for the structure-pruning fixes.

Pruning is destructive and unreviewable — nobody diffs a structure tree —
so these tests are weighted towards what must *survive*. The costly
mistake is not leaving an empty tag behind; it is deleting a node whose
absence changes how its siblings are read.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, String

from auto_a11y.pdf.audit.structure import walk_structure_tree
from auto_a11y.pdf.fix.empty_structure import (
    fix_empty_lists,
    fix_empty_tables,
    fix_empty_tags,
)
from auto_a11y.pdf.fix.models import FixOptions


@pytest.fixture
def opts(tmp_path: Path) -> FixOptions:
    return FixOptions(pdf_path=tmp_path / "doc.pdf")


def _node(
    pdf: pikepdf.Pdf,
    tag: str,
    kids: list[pikepdf.Object] | None = None,
    **entries: object,
) -> pikepdf.Object:
    fields: dict[str, object] = {"/Type": Name("/StructElem"), "/S": Name(f"/{tag}")}
    fields.update({f"/{k}": v for k, v in entries.items()})
    obj = pdf.make_indirect(Dictionary(fields))
    if kids is not None:
        obj[Name("/K")] = Array(kids)
    return obj


def _rooted(pdf: pikepdf.Pdf, *roots: pikepdf.Object) -> pikepdf.Pdf:
    struct_root = pdf.make_indirect(Dictionary(Type=Name("/StructTreeRoot")))
    struct_root[Name("/K")] = Array(list(roots))
    pdf.Root[Name("/StructTreeRoot")] = struct_root
    return pdf


def _blank() -> pikepdf.Pdf:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    return pdf


def _tags(pdf: pikepdf.Pdf) -> list[str]:
    elements, _ = walk_structure_tree(pdf)
    return [e.resolved_tag for e in elements]


# ---------------------------------------------------------------------------
# fix_empty_tags — what must survive
# ---------------------------------------------------------------------------

def test_empty_table_cells_survive(opts: FixOptions) -> None:
    """The divergence from pdfMax, and the costliest bug in the original.

    Its never-remove list covers Table, TR and the row groups but not TD
    or TH. A blank cell is ordinary in a data table — it means "no value"
    — and deleting it shifts every later cell in that row into the wrong
    column, so the surviving cells get the wrong headers announced.
    """
    pdf = _blank()
    row = _node(pdf, "TR", [
        _node(pdf, "TD", [String("a")]),
        _node(pdf, "TD"),            # blank cell, no content at all
        _node(pdf, "TD", [String("c")]),
    ])
    _rooted(pdf, _node(pdf, "Table", [row]))

    result = fix_empty_tags(pdf, opts)

    assert result.success
    assert _tags(pdf).count("TD") == 3, "a blank cell still holds a column"


def test_empty_header_cells_survive(opts: FixOptions) -> None:
    pdf = _blank()
    row = _node(pdf, "TR", [_node(pdf, "TH"), _node(pdf, "TH", [String("Q1")])])
    _rooted(pdf, _node(pdf, "Table", [row]))

    fix_empty_tags(pdf, opts)

    assert _tags(pdf).count("TH") == 2


def test_a_figure_described_only_by_alt_text_survives(
    opts: FixOptions,
) -> None:
    # The description is the content; removing the tag removes the only
    # thing a screen reader had to announce.
    pdf = _blank()
    _rooted(pdf, _node(pdf, "Figure", None, Alt=String("A bar chart")))

    fix_empty_tags(pdf, opts)

    assert "Figure" in _tags(pdf)


def test_an_element_with_actual_text_survives(opts: FixOptions) -> None:
    pdf = _blank()
    _rooted(pdf, _node(pdf, "Span", None, ActualText=String("CNIB")))

    fix_empty_tags(pdf, opts)

    assert "Span" in _tags(pdf)


def test_empty_grouping_containers_survive(opts: FixOptions) -> None:
    # Removing a Sect changes how the document is divided, which is a
    # bigger change than the noise it saves.
    pdf = _blank()
    _rooted(pdf, _node(pdf, "Document", [_node(pdf, "Sect")]))

    fix_empty_tags(pdf, opts)

    assert "Sect" in _tags(pdf)


# ---------------------------------------------------------------------------
# fix_empty_tags — what must go
# ---------------------------------------------------------------------------

def test_an_empty_paragraph_is_removed(opts: FixOptions) -> None:
    pdf = _blank()
    _rooted(pdf, _node(pdf, "Document", [
        _node(pdf, "P", [String("real text")]),
        _node(pdf, "P"),
    ]))

    result = fix_empty_tags(pdf, opts)

    assert result.success
    assert _tags(pdf).count("P") == 1


def test_nested_empty_wrappers_collapse_in_one_pass(opts: FixOptions) -> None:
    """Children are considered before their parent, so a chain goes at once."""
    pdf = _blank()
    inner = _node(pdf, "Span")
    middle = _node(pdf, "Quote", [inner])
    _rooted(pdf, _node(pdf, "Document", [middle]))

    result = fix_empty_tags(pdf, opts)

    assert result.success
    remaining = _tags(pdf)
    assert "Span" not in remaining
    assert "Quote" not in remaining


def test_reports_when_there_is_nothing_to_remove(opts: FixOptions) -> None:
    pdf = _blank()
    _rooted(pdf, _node(pdf, "P", [String("text")]))

    result = fix_empty_tags(pdf, opts)

    assert result.success
    assert "No empty leaf tags" in result.description


def test_declines_without_a_structure_tree(opts: FixOptions) -> None:
    result = fix_empty_tags(_blank(), opts)

    assert not result.success
    assert "No structure tree" in result.description


# ---------------------------------------------------------------------------
# fix_empty_tables
# ---------------------------------------------------------------------------

def test_a_table_with_no_cells_is_removed(opts: FixOptions) -> None:
    pdf = _blank()
    _rooted(pdf, _node(pdf, "Document", [
        _node(pdf, "Table", [_node(pdf, "TR")]),
    ]))

    result = fix_empty_tables(pdf, opts)

    assert result.success
    assert "Table" not in _tags(pdf)


def test_a_table_with_cells_survives(opts: FixOptions) -> None:
    pdf = _blank()
    _rooted(pdf, _node(pdf, "Table", [
        _node(pdf, "TBody", [_node(pdf, "TR", [_node(pdf, "TD")])]),
    ]))

    result = fix_empty_tables(pdf, opts)

    assert result.success
    assert "Table" in _tags(pdf), "cells found through the row group"


# ---------------------------------------------------------------------------
# fix_empty_lists
# ---------------------------------------------------------------------------

def test_a_completely_empty_list_is_removed(opts: FixOptions) -> None:
    pdf = _blank()
    _rooted(pdf, _node(pdf, "Document", [_node(pdf, "L")]))

    result = fix_empty_lists(pdf, opts)

    assert result.success
    assert "L" not in _tags(pdf)


def test_a_malformed_list_holding_content_survives(opts: FixOptions) -> None:
    """Stricter than the table case, deliberately.

    A list whose items were never wrapped in <LI> is malformed, but it
    still holds the author's words. Repairing it belongs to
    fix_list_structure; deleting it here would throw the content away.
    """
    pdf = _blank()
    _rooted(pdf, _node(pdf, "L", [_node(pdf, "P", [String("item one")])]))

    result = fix_empty_lists(pdf, opts)

    assert result.success
    assert "L" in _tags(pdf)
    assert "P" in _tags(pdf)
