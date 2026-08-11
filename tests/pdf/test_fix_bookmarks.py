"""Tests for outline (bookmark) generation.

Bookmarks are judged on whether the panel a reader opens is navigable:
entries labelled with the words that appear on the page, arranged in the
document's hierarchy so sections can be collapsed. A flat list of a
hundred entries called "Heading 47" is technically an outline and no help
to anyone, so both properties are tested directly.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, String

from auto_a11y.pdf.fix.bookmarks import fix_bookmarks
from auto_a11y.pdf.fix.models import FixOptions


@pytest.fixture
def opts(tmp_path: Path) -> FixOptions:
    return FixOptions(pdf_path=tmp_path / "doc.pdf")


def _doc(*headings: tuple[str, str]) -> pikepdf.Pdf:
    """A tagged PDF whose headings carry ``(tag, text)`` as /ActualText."""
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    kids = [
        pdf.make_indirect(
            Dictionary(
                Type=Name("/StructElem"),
                S=Name(f"/{tag}"),
                ActualText=String(text),
                Pg=page.obj,
            )
        )
        for tag, text in headings
    ]
    struct_root = pdf.make_indirect(Dictionary(Type=Name("/StructTreeRoot")))
    struct_root[Name("/K")] = Array(kids)
    pdf.Root[Name("/StructTreeRoot")] = struct_root
    return pdf


def _outline_tree(pdf: pikepdf.Pdf) -> list[tuple[int, str]]:
    """(depth, title) for every bookmark, in panel order."""
    outlines = pdf.Root.get(Name("/Outlines"))
    if outlines is None:
        return []
    rows: list[tuple[int, str]] = []

    def walk(node: pikepdf.Object, depth: int) -> None:
        current = node.get(Name("/First"))
        while current is not None:
            rows.append((depth, str(current[Name("/Title")])))
            walk(current, depth + 1)
            current = current.get(Name("/Next"))

    walk(outlines, 0)
    return rows


def test_bookmarks_are_nested_by_heading_level(opts: FixOptions) -> None:
    """The original collected each level and then never used it.

    A flat outline gives a hundred-heading report a hundred top-level
    bookmarks with nothing to collapse.
    """
    pdf = _doc(
        ("H1", "Introduction"),
        ("H2", "Background"),
        ("H2", "Scope"),
        ("H1", "Findings"),
    )

    result = fix_bookmarks(pdf, opts)

    assert result.success
    assert _outline_tree(pdf) == [
        (0, "Introduction"),
        (1, "Background"),
        (1, "Scope"),
        (0, "Findings"),
    ]


def test_a_skipped_level_still_nests_under_the_nearest_ancestor(
    opts: FixOptions,
) -> None:
    # Real documents jump H1 -> H3. Assuming a well-formed sequence would
    # either lose the entry or bury it at the wrong depth.
    pdf = _doc(("H1", "Top"), ("H3", "Buried"), ("H2", "Sibling"))

    fix_bookmarks(pdf, opts)

    assert _outline_tree(pdf) == [(0, "Top"), (1, "Buried"), (1, "Sibling")]


def test_a_document_opening_below_h1_still_nests(opts: FixOptions) -> None:
    pdf = _doc(("H2", "First"), ("H3", "Under it"), ("H2", "Second"))

    fix_bookmarks(pdf, opts)

    assert _outline_tree(pdf) == [
        (0, "First"), (1, "Under it"), (0, "Second"),
    ]


def test_titles_use_the_heading_text(opts: FixOptions) -> None:
    pdf = _doc(("H1", "Annual Report 2025"))

    fix_bookmarks(pdf, opts)

    assert _outline_tree(pdf) == [(0, "Annual Report 2025")]


def test_a_long_title_is_truncated_not_clipped(opts: FixOptions) -> None:
    pdf = _doc(("H1", "A" * 200))

    fix_bookmarks(pdf, opts)

    (_, title), = _outline_tree(pdf)
    assert len(title) <= 80
    assert title.endswith("…")


def test_headings_without_text_get_a_positional_placeholder(
    opts: FixOptions,
) -> None:
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    kids = [
        pdf.make_indirect(
            Dictionary(Type=Name("/StructElem"), S=Name("/H1"), Pg=page.obj)
        )
    ]
    struct_root = pdf.make_indirect(Dictionary(Type=Name("/StructTreeRoot")))
    struct_root[Name("/K")] = Array(kids)
    pdf.Root[Name("/StructTreeRoot")] = struct_root

    result = fix_bookmarks(pdf, opts)

    assert result.success
    assert _outline_tree(pdf) == [(0, "Heading 1")]


def test_sibling_links_are_chained_both_ways(opts: FixOptions) -> None:
    # Viewers walk /Next forwards and /Prev backwards; a one-way chain
    # makes the panel navigable in one direction only.
    pdf = _doc(("H1", "One"), ("H1", "Two"), ("H1", "Three"))

    fix_bookmarks(pdf, opts)

    first = pdf.Root[Name("/Outlines")][Name("/First")]
    second = first[Name("/Next")]
    third = second[Name("/Next")]
    assert str(second[Name("/Prev")][Name("/Title")]) == "One"
    assert str(third[Name("/Prev")][Name("/Title")]) == "Two"
    assert Name("/Next") not in third


def test_the_outline_count_covers_every_entry(opts: FixOptions) -> None:
    pdf = _doc(("H1", "One"), ("H2", "Under"), ("H1", "Two"))

    fix_bookmarks(pdf, opts)

    assert int(pdf.Root[Name("/Outlines")][Name("/Count")]) == 3


def test_the_bookmarks_panel_is_set_to_open(opts: FixOptions) -> None:
    pdf = _doc(("H1", "One"))

    fix_bookmarks(pdf, opts)

    assert str(pdf.Root[Name("/PageMode")]) == "/UseOutlines"


def test_existing_bookmarks_are_left_alone(opts: FixOptions) -> None:
    """An existing outline was authored deliberately.

    It may deliberately not follow the headings, so regenerating it would
    discard someone's decisions.
    """
    pdf = _doc(("H1", "Generated"))
    existing = pdf.make_indirect(
        Dictionary(Title=String("Hand written"))
    )
    pdf.Root[Name("/Outlines")] = pdf.make_indirect(
        Dictionary(Type=Name("/Outlines"), First=existing, Last=existing, Count=1)
    )

    result = fix_bookmarks(pdf, opts)

    assert result.success
    assert "already exist" in result.description
    assert _outline_tree(pdf) == [(0, "Hand written")]


def test_declines_when_there_are_no_headings(opts: FixOptions) -> None:
    pdf = _doc(("P", "Just a paragraph"))

    result = fix_bookmarks(pdf, opts)

    assert not result.success
    assert Name("/Outlines") not in pdf.Root


def test_declines_without_a_structure_tree(opts: FixOptions) -> None:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))

    result = fix_bookmarks(pdf, opts)

    assert not result.success
