"""Tests for the heading-level fix.

Two failure modes matter more than the happy path. Acting on the wrong
element is especially costly here because neighbouring elements are often
both headings, so a mistake produces a plausible-looking result. And
promoting a positional element — a table cell, a list item — takes it out
of the structure it belongs to, damaging more than it relabels.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name

from auto_a11y.pdf.audit.structure import walk_structure_tree
from auto_a11y.pdf.fix.headings import fix_heading_levels
from auto_a11y.pdf.fix.models import FixOptions


@pytest.fixture
def opts(tmp_path: Path) -> FixOptions:
    return FixOptions(pdf_path=tmp_path / "doc.pdf")


def _tagged(*tags: str) -> pikepdf.Pdf:
    """A flat structure tree with one element per tag, in order."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    kids = [
        pdf.make_indirect(Dictionary(Type=Name("/StructElem"), S=Name(f"/{tag}")))
        for tag in tags
    ]
    struct_root = pdf.make_indirect(Dictionary(Type=Name("/StructTreeRoot")))
    struct_root[Name("/K")] = Array(kids)
    pdf.Root[Name("/StructTreeRoot")] = struct_root
    return pdf


def _tags(pdf: pikepdf.Pdf) -> list[str]:
    elements, _ = walk_structure_tree(pdf)
    return [e.resolved_tag for e in elements]


def test_references_are_one_based(opts: FixOptions) -> None:
    """The off-by-one corrected here, and why it hid.

    The original read references 0-based while the report prints them
    1-based. Where a document has consecutive headings — the common case
    — the change landed on the neighbouring heading and still produced a
    document full of headings, so nothing looked wrong.
    """
    pdf = _tagged("H1", "H1", "H1")

    result = fix_heading_levels(
        pdf, FixOptions(pdf_path=opts.pdf_path, heading_levels_map={"2": "2"})
    )

    assert result.success
    assert _tags(pdf) == ["H1", "H2", "H1"]


def test_a_heading_can_be_demoted_to_a_paragraph(opts: FixOptions) -> None:
    # Level 0 is for text that was styled large but is not a heading.
    pdf = _tagged("H1", "H2")

    result = fix_heading_levels(
        pdf, FixOptions(pdf_path=opts.pdf_path, heading_levels_map={"2": "0"})
    )

    assert result.success
    assert _tags(pdf) == ["H1", "P"]


def test_a_paragraph_can_be_promoted_to_a_heading(opts: FixOptions) -> None:
    pdf = _tagged("H1", "P")

    result = fix_heading_levels(
        pdf, FixOptions(pdf_path=opts.pdf_path, heading_levels_map={"2": "2"})
    )

    assert result.success
    assert _tags(pdf) == ["H1", "H2"]


@pytest.mark.parametrize("positional", ["TD", "TH", "LI", "Lbl", "LBody"])
def test_positional_elements_cannot_become_headings(
    positional: str, opts: FixOptions
) -> None:
    """The original promoted whatever it was pointed at.

    A <TD> retagged as <H2> leaves its row a cell short, so every later
    cell shifts column and the table's header associations go with it. A
    stale reference should be refused, not applied.
    """
    pdf = _tagged(positional)

    result = fix_heading_levels(
        pdf, FixOptions(pdf_path=opts.pdf_path, heading_levels_map={"1": "2"})
    )

    assert not result.success
    assert _tags(pdf) == [positional]
    assert "cannot become a heading" in result.description


def test_demoting_something_that_is_not_a_heading_is_refused(
    opts: FixOptions,
) -> None:
    pdf = _tagged("P")

    result = fix_heading_levels(
        pdf, FixOptions(pdf_path=opts.pdf_path, heading_levels_map={"1": "0"})
    )

    assert not result.success
    assert _tags(pdf) == ["P"]


def test_partial_application_is_reported(opts: FixOptions) -> None:
    pdf = _tagged("H1", "TD")

    result = fix_heading_levels(
        pdf,
        FixOptions(
            pdf_path=opts.pdf_path,
            heading_levels_map={"1": "2", "2": "3", "9": "1"},
        ),
    )

    assert result.success
    assert _tags(pdf) == ["H2", "TD"]
    assert "1 of 3" in result.description
    assert "9 not found" in result.description


@pytest.mark.parametrize("bad", ["0", "7", "-1", "two"])
def test_levels_outside_zero_to_six_are_rejected(
    bad: str, opts: FixOptions
) -> None:
    pdf = _tagged("H1")

    result = fix_heading_levels(
        pdf, FixOptions(pdf_path=opts.pdf_path, heading_levels_map={"1": bad})
    )

    if bad == "0":
        # 0 is valid — it means demote to a paragraph.
        assert result.success
        return
    assert not result.success
    assert _tags(pdf) == ["H1"]


def test_a_zero_reference_is_rejected(opts: FixOptions) -> None:
    pdf = _tagged("H1")

    result = fix_heading_levels(
        pdf, FixOptions(pdf_path=opts.pdf_path, heading_levels_map={"0": "2"})
    )

    assert not result.success
    assert _tags(pdf) == ["H1"]


def test_declines_with_nothing_supplied(opts: FixOptions) -> None:
    result = fix_heading_levels(_tagged("H1"), opts)

    assert not result.success


def test_declines_without_a_structure_tree(opts: FixOptions) -> None:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))

    result = fix_heading_levels(
        pdf, FixOptions(pdf_path=opts.pdf_path, heading_levels_map={"1": "2"})
    )

    assert not result.success
    assert "No structure tree" in result.description
