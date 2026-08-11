"""Tests for the nesting repairs.

Both fixes rearrange the tree, so the property that matters is
conservation: after the repair the same content is present, in the same
order. A repair that drops a paragraph's text while removing its
redundant wrapper has made the document worse in a way no check reports.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, String

from auto_a11y.pdf.audit.structure import walk_structure_tree
from auto_a11y.pdf.fix.models import FixOptions
from auto_a11y.pdf.fix.nesting import fix_correct_nesting, fix_toc_structure


@pytest.fixture
def opts(tmp_path: Path) -> FixOptions:
    return FixOptions(pdf_path=tmp_path / "doc.pdf")


def _node(
    pdf: pikepdf.Pdf, tag: str, kids: list[pikepdf.Object] | None = None
) -> pikepdf.Dictionary:
    obj = pdf.make_indirect(
        Dictionary(Type=Name("/StructElem"), S=Name(f"/{tag}"))
    )
    if kids is not None:
        obj[Name("/K")] = Array(kids)
        for kid in kids:
            if isinstance(kid, pikepdf.Dictionary):
                kid[Name("/P")] = obj
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


def _shape(pdf: pikepdf.Pdf) -> list[tuple[int, str]]:
    elements, _ = walk_structure_tree(pdf)
    by_index = {e.index: e for e in elements}
    depths: dict[int, int] = {}
    for element in elements:
        parent = by_index.get(element.parent_index)
        depths[element.index] = 0 if parent is None else depths[parent.index] + 1
    return [(depths[e.index], e.resolved_tag) for e in elements]


# ---------------------------------------------------------------------------
# fix_correct_nesting
# ---------------------------------------------------------------------------

def test_a_paragraph_inside_a_paragraph_is_unwrapped(
    opts: FixOptions,
) -> None:
    pdf = _blank()
    inner = _node(pdf, "P", [_node(pdf, "Span")])
    _rooted(pdf, _node(pdf, "P", [inner]))

    result = fix_correct_nesting(pdf, opts)

    assert result.success
    assert _shape(pdf) == [(0, "P"), (1, "Span")]


def test_the_inner_content_is_promoted_not_discarded(
    opts: FixOptions,
) -> None:
    """The conservation property this fix has to hold."""
    pdf = _blank()
    inner = _node(pdf, "P", [_node(pdf, "Span"), _node(pdf, "Quote")])
    _rooted(pdf, _node(pdf, "P", [inner]))

    fix_correct_nesting(pdf, opts)

    tags = [tag for _, tag in _shape(pdf)]
    assert "Span" in tags
    assert "Quote" in tags


def test_promotion_keeps_document_order(opts: FixOptions) -> None:
    pdf = _blank()
    inner = _node(pdf, "P", [_node(pdf, "Quote")])
    _rooted(pdf, _node(pdf, "P", [
        _node(pdf, "Span"), inner, _node(pdf, "Code"),
    ]))

    fix_correct_nesting(pdf, opts)

    tags = [tag for _, tag in _shape(pdf)]
    assert tags == ["P", "Span", "Quote", "Code"]


def test_a_heading_inside_a_heading_is_unwrapped(opts: FixOptions) -> None:
    pdf = _blank()
    _rooted(pdf, _node(pdf, "H2", [_node(pdf, "H2", [_node(pdf, "Span")])]))

    result = fix_correct_nesting(pdf, opts)

    assert result.success
    assert _shape(pdf) == [(0, "H2"), (1, "Span")]


def test_different_heading_levels_still_count_as_the_same_kind(
    opts: FixOptions,
) -> None:
    # An H3 inside an H1 is as meaningless as an H1 inside an H1.
    pdf = _blank()
    _rooted(pdf, _node(pdf, "H1", [_node(pdf, "H3", [_node(pdf, "Span")])]))

    fix_correct_nesting(pdf, opts)

    assert _shape(pdf) == [(0, "H1"), (1, "Span")]


def test_a_paragraph_inside_a_heading_is_left_alone(opts: FixOptions) -> None:
    pdf = _blank()
    _rooted(pdf, _node(pdf, "H1", [_node(pdf, "P")]))
    before = _shape(pdf)

    result = fix_correct_nesting(pdf, opts)

    assert result.success
    assert _shape(pdf) == before


def test_an_empty_inner_paragraph_is_removed(opts: FixOptions) -> None:
    pdf = _blank()
    _rooted(pdf, _node(pdf, "P", [_node(pdf, "P")]))

    result = fix_correct_nesting(pdf, opts)

    assert result.success
    assert _shape(pdf) == [(0, "P")]


def test_a_well_formed_tree_is_untouched(opts: FixOptions) -> None:
    pdf = _blank()
    _rooted(pdf, _node(pdf, "Sect", [_node(pdf, "P"), _node(pdf, "P")]))
    before = _shape(pdf)

    result = fix_correct_nesting(pdf, opts)

    assert result.success
    assert "No element is nested inside its own kind" in result.description
    assert _shape(pdf) == before


# ---------------------------------------------------------------------------
# fix_toc_structure
# ---------------------------------------------------------------------------

def test_loose_contents_entries_gain_a_toci(opts: FixOptions) -> None:
    pdf = _blank()
    _rooted(pdf, _node(pdf, "TOC", [
        _node(pdf, "P", [String("Chapter one")]),
        _node(pdf, "P", [String("Chapter two")]),
    ]))

    result = fix_toc_structure(pdf, opts)

    assert result.success
    assert _shape(pdf) == [
        (0, "TOC"), (1, "TOCI"), (2, "P"), (1, "TOCI"), (2, "P"),
    ]


def test_existing_entries_are_left_alone(opts: FixOptions) -> None:
    pdf = _blank()
    _rooted(pdf, _node(pdf, "TOC", [_node(pdf, "TOCI", [_node(pdf, "P")])]))
    before = _shape(pdf)

    result = fix_toc_structure(pdf, opts)

    assert result.success
    assert _shape(pdf) == before


def test_a_nested_contents_table_is_not_wrapped(opts: FixOptions) -> None:
    """A sub-table of contents is a legitimate direct child, not an entry."""
    pdf = _blank()
    _rooted(pdf, _node(pdf, "TOC", [
        _node(pdf, "TOCI", [_node(pdf, "P")]),
        _node(pdf, "TOC", [_node(pdf, "TOCI")]),
    ]))

    fix_toc_structure(pdf, opts)

    shape = _shape(pdf)
    assert (1, "TOC") in shape, "the nested TOC stays a direct child"


def test_toc_declines_without_a_structure_tree(opts: FixOptions) -> None:
    result = fix_toc_structure(_blank(), opts)

    assert not result.success
