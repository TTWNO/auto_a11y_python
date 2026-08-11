"""Tests for link descriptions.

A link with no /Contents is announced as "link" and nothing else, so the
value of these fixes is entirely in whether the description that lands is
the author's own wording. A description copied from the wrong link, or an
empty one written over a good one, is worse than the original omission.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, String

from auto_a11y.pdf.fix.links import fix_link_annot_contents, fix_link_content
from auto_a11y.pdf.fix.models import FixOptions


@pytest.fixture
def opts(tmp_path: Path) -> FixOptions:
    return FixOptions(pdf_path=tmp_path / "doc.pdf")


def _pdf(pages: int = 1) -> pikepdf.Pdf:
    pdf = pikepdf.Pdf.new()
    for _ in range(pages):
        pdf.add_blank_page(page_size=(200, 200))
    return pdf


def _link_annot(pdf: pikepdf.Pdf) -> pikepdf.Dictionary:
    return pdf.make_indirect(
        Dictionary(Type=Name("/Annot"), Subtype=Name("/Link"))
    )


def _link_element(
    pdf: pikepdf.Pdf, annot: pikepdf.Object, **entries: object
) -> pikepdf.Dictionary:
    fields: dict[str, object] = {
        "/Type": Name("/StructElem"), "/S": Name("/Link"),
    }
    fields.update({f"/{k}": v for k, v in entries.items()})
    element = pdf.make_indirect(Dictionary(fields))
    objr = pdf.make_indirect(Dictionary(Type=Name("/OBJR"), Obj=annot))
    element[Name("/K")] = Array([objr])
    return element


def _rooted(pdf: pikepdf.Pdf, *elements: pikepdf.Object) -> None:
    struct_root = pdf.make_indirect(Dictionary(Type=Name("/StructTreeRoot")))
    struct_root[Name("/K")] = Array(list(elements))
    pdf.Root[Name("/StructTreeRoot")] = struct_root


def _contents(annot: pikepdf.Object) -> str:
    value = annot.get(Name("/Contents"))
    return str(value) if value is not None else ""


# ---------------------------------------------------------------------------
# Describing from the document's own text
# ---------------------------------------------------------------------------

def test_alt_text_on_the_link_becomes_the_description(
    opts: FixOptions,
) -> None:
    pdf = _pdf()
    annot = _link_annot(pdf)
    pdf.pages[0].obj[Name("/Annots")] = Array([annot])
    _rooted(pdf, _link_element(pdf, annot, Alt=String("Annual report 2025")))

    result = fix_link_annot_contents(pdf, opts)

    assert result.success
    assert _contents(annot) == "Annual report 2025"


def test_an_existing_description_is_never_overwritten(
    opts: FixOptions,
) -> None:
    """Whatever is there was written by someone who could see the link."""
    pdf = _pdf()
    annot = _link_annot(pdf)
    annot[Name("/Contents")] = String("Hand written")
    pdf.pages[0].obj[Name("/Annots")] = Array([annot])
    _rooted(pdf, _link_element(pdf, annot, Alt=String("Generated")))

    fix_link_annot_contents(pdf, opts)

    assert _contents(annot) == "Hand written"


def test_a_link_with_no_usable_text_is_left_alone(opts: FixOptions) -> None:
    # Writing an empty description would satisfy the check while telling
    # the reader nothing, which is worse than the honest omission.
    pdf = _pdf()
    annot = _link_annot(pdf)
    pdf.pages[0].obj[Name("/Annots")] = Array([annot])
    _rooted(pdf, _link_element(pdf, annot))

    result = fix_link_annot_contents(pdf, opts)

    assert result.success
    assert _contents(annot) == ""


def test_each_link_gets_its_own_text(opts: FixOptions) -> None:
    pdf = _pdf()
    first, second = _link_annot(pdf), _link_annot(pdf)
    pdf.pages[0].obj[Name("/Annots")] = Array([first, second])
    _rooted(
        pdf,
        _link_element(pdf, first, Alt=String("Contact us")),
        _link_element(pdf, second, Alt=String("Privacy policy")),
    )

    fix_link_annot_contents(pdf, opts)

    assert _contents(first) == "Contact us"
    assert _contents(second) == "Privacy policy"


def test_declines_without_a_structure_tree(opts: FixOptions) -> None:
    pdf = _pdf()
    pdf.pages[0].obj[Name("/Annots")] = Array([_link_annot(pdf)])

    result = fix_link_annot_contents(pdf, opts)

    assert not result.success


# ---------------------------------------------------------------------------
# fix_link_content — supplied descriptions
# ---------------------------------------------------------------------------

def test_supplied_descriptions_use_one_based_page_and_position(
    opts: FixOptions,
) -> None:
    """The off-by-one corrected here.

    The report prints pages as "p.2"; the original read that as a 0-based
    index, so a description written for page 2 landed on page 3.
    """
    pdf = _pdf(pages=2)
    first, second = _link_annot(pdf), _link_annot(pdf)
    pdf.pages[0].obj[Name("/Annots")] = Array([first])
    pdf.pages[1].obj[Name("/Annots")] = Array([second])
    _rooted(pdf)

    result = fix_link_content(
        pdf,
        FixOptions(
            pdf_path=opts.pdf_path,
            link_content_map={"2:1": "Link on the second page"},
        ),
    )

    assert result.success
    assert _contents(first) == ""
    assert _contents(second) == "Link on the second page"


def test_the_document_text_wins_over_a_supplied_description(
    opts: FixOptions,
) -> None:
    """A link with usable text needed no help.

    The report offering it for input was working from before the first
    pass ran, so the supplied value is stale by the time it arrives.
    """
    pdf = _pdf()
    annot = _link_annot(pdf)
    pdf.pages[0].obj[Name("/Annots")] = Array([annot])
    _rooted(pdf, _link_element(pdf, annot, Alt=String("From the document")))

    fix_link_content(
        pdf,
        FixOptions(
            pdf_path=opts.pdf_path, link_content_map={"1:1": "From the user"}
        ),
    )

    assert _contents(annot) == "From the document"


def test_both_sources_are_reported_separately(opts: FixOptions) -> None:
    pdf = _pdf()
    described, bare = _link_annot(pdf), _link_annot(pdf)
    pdf.pages[0].obj[Name("/Annots")] = Array([described, bare])
    _rooted(pdf, _link_element(pdf, described, Alt=String("From the document")))

    result = fix_link_content(
        pdf,
        FixOptions(
            pdf_path=opts.pdf_path, link_content_map={"1:2": "From the user"}
        ),
    )

    assert result.success
    assert "1 from the document's own text" in result.description
    assert "1 from supplied descriptions" in result.description


def test_a_page_that_does_not_exist_is_reported(opts: FixOptions) -> None:
    pdf = _pdf()
    _rooted(pdf)

    result = fix_link_content(
        pdf, FixOptions(pdf_path=opts.pdf_path, link_content_map={"9:1": "x"})
    )

    assert not result.success
    assert "page 9 does not exist" in result.description
