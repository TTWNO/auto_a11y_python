"""Tests for the alt-text fixes.

The property these tests exist to protect is that supplied text lands on
the element it was written for. Alt text on the wrong figure describes
something confidently and wrongly, and no later check can tell — so the
failure modes worth covering are the mismatches, not the happy path.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name

from auto_a11y.pdf.audit.structure import walk_structure_tree
from auto_a11y.pdf.fix.alt_text import fix_alt_text, fix_formula_alt
from auto_a11y.pdf.fix.models import FixOptions


@pytest.fixture
def pdf_path(tmp_path: Path) -> Path:
    return tmp_path / "doc.pdf"


def _tagged_pdf(*tags: str) -> pikepdf.Pdf:
    """A PDF whose structure tree holds one element per tag, in order."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    struct_root = pdf.make_indirect(Dictionary(Type=Name("/StructTreeRoot")))
    kids = [
        pdf.make_indirect(Dictionary(Type=Name("/StructElem"), S=Name(f"/{tag}")))
        for tag in tags
    ]
    struct_root[Name("/K")] = Array(kids)
    pdf.Root[Name("/StructTreeRoot")] = struct_root
    return pdf


def _alt_of(pdf: pikepdf.Pdf, one_based: int) -> str | None:
    elements, _ = walk_structure_tree(pdf)
    obj = elements[one_based - 1].obj
    value = obj.get(Name("/Alt"))
    return str(value) if value is not None else None


def test_alt_text_lands_on_the_named_figure(pdf_path: Path) -> None:
    pdf = _tagged_pdf("P", "Figure", "P", "Figure")

    result = fix_alt_text(
        pdf,
        FixOptions(pdf_path=pdf_path, alt_text_map={"2": "Bar chart of uptake"}),
    )

    assert result.success
    assert _alt_of(pdf, 2) == "Bar chart of uptake"
    assert _alt_of(pdf, 4) is None, "only the named element is written"


def test_indices_agree_with_the_audit_walk(pdf_path: Path) -> None:
    """The fix and the audit must number elements identically.

    The report is what the user reads indices off. If the fixer walked the
    tree its own way, every reference would be silently off by however
    much the two walks disagreed.
    """
    pdf = _tagged_pdf("Document", "P", "Figure", "Table", "Figure")
    elements, _ = walk_structure_tree(pdf)
    figure_positions = [
        e.index + 1 for e in elements if e.resolved_tag == "Figure"
    ]

    fix_alt_text(
        pdf,
        FixOptions(
            pdf_path=pdf_path,
            alt_text_map={str(figure_positions[1]): "Second figure"},
        ),
    )

    assert _alt_of(pdf, figure_positions[1]) == "Second figure"
    assert _alt_of(pdf, figure_positions[0]) is None


def test_alt_text_refuses_an_element_of_the_wrong_kind(
    pdf_path: Path,
) -> None:
    """A stale reference must not describe a paragraph as an image."""
    pdf = _tagged_pdf("P", "P")

    result = fix_alt_text(
        pdf, FixOptions(pdf_path=pdf_path, alt_text_map={"1": "A photo"})
    )

    assert not result.success
    assert _alt_of(pdf, 1) is None
    assert "not a figure" in result.description


def test_alt_text_reports_partial_application(pdf_path: Path) -> None:
    pdf = _tagged_pdf("Figure", "P")

    result = fix_alt_text(
        pdf,
        FixOptions(
            pdf_path=pdf_path,
            alt_text_map={"1": "Good", "2": "Wrong kind", "99": "Missing"},
        ),
    )

    # Partial success is still success — the valid one should not be lost
    # because its neighbours were wrong — but the shortfall must be stated.
    assert result.success
    assert _alt_of(pdf, 1) == "Good"
    assert "1 of 3" in result.description
    assert "not found: 99" in result.description


def test_alt_text_rejects_non_numeric_references(pdf_path: Path) -> None:
    pdf = _tagged_pdf("Figure")

    result = fix_alt_text(
        pdf, FixOptions(pdf_path=pdf_path, alt_text_map={"figure-one": "x"})
    )

    assert not result.success
    assert _alt_of(pdf, 1) is None


def test_alt_text_rejects_a_zero_reference(pdf_path: Path) -> None:
    # References are 1-based; 0 would silently become -1 and index from
    # the end of the list.
    pdf = _tagged_pdf("Figure")

    result = fix_alt_text(
        pdf, FixOptions(pdf_path=pdf_path, alt_text_map={"0": "x"})
    )

    assert not result.success
    assert _alt_of(pdf, 1) is None


def test_alt_text_declines_with_nothing_supplied(pdf_path: Path) -> None:
    pdf = _tagged_pdf("Figure")

    result = fix_alt_text(pdf, FixOptions(pdf_path=pdf_path))

    assert not result.success
    assert "No figure alt text values provided" in result.description


def test_alt_text_declines_without_a_structure_tree(pdf_path: Path) -> None:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))

    result = fix_alt_text(
        pdf, FixOptions(pdf_path=pdf_path, alt_text_map={"1": "x"})
    )

    assert not result.success
    assert "No structure tree" in result.description


# ---------------------------------------------------------------------------
# fix_formula_alt
# ---------------------------------------------------------------------------

def test_formula_alt_lands_on_a_formula(pdf_path: Path) -> None:
    pdf = _tagged_pdf("P", "Formula")

    result = fix_formula_alt(
        pdf,
        FixOptions(pdf_path=pdf_path, formula_alt_map={"2": "E equals m c squared"}),
    )

    assert result.success
    assert _alt_of(pdf, 2) == "E equals m c squared"


def test_formula_alt_refuses_a_figure(pdf_path: Path) -> None:
    """The original applied formula text to any element index at all.

    It computed a tag resolver for exactly this purpose and then never
    consulted it, so a stale reference wrote mathematics onto a picture.
    """
    pdf = _tagged_pdf("Figure")

    result = fix_formula_alt(
        pdf, FixOptions(pdf_path=pdf_path, formula_alt_map={"1": "x squared"})
    )

    assert not result.success
    assert _alt_of(pdf, 1) is None
    assert "not a formula" in result.description


def test_formula_alt_and_figure_alt_do_not_share_a_map(
    pdf_path: Path,
) -> None:
    pdf = _tagged_pdf("Figure", "Formula")

    fix_alt_text(
        pdf, FixOptions(pdf_path=pdf_path, alt_text_map={"1": "A picture"})
    )
    fix_formula_alt(
        pdf, FixOptions(pdf_path=pdf_path, formula_alt_map={"2": "A formula"})
    )

    assert _alt_of(pdf, 1) == "A picture"
    assert _alt_of(pdf, 2) == "A formula"
