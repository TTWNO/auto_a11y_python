"""Tests for the annotation fixes.

The reference format is the thing to guard here. Descriptions are written
by a person against what the report showed them, so a fix that counts
pages differently from the report puts every description on the wrong
page — and a wrong description is worse than a missing one.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, String

from auto_a11y.pdf.fix.annotations import (
    fix_annot_contents_lang,
    fix_annot_descriptions,
    fix_ref_xobjects,
    fix_trapnet,
)
from auto_a11y.pdf.fix.models import FixOptions


@pytest.fixture
def opts(tmp_path: Path) -> FixOptions:
    return FixOptions(pdf_path=tmp_path / "doc.pdf")


def _pdf(pages: int = 1) -> pikepdf.Pdf:
    pdf = pikepdf.Pdf.new()
    for _ in range(pages):
        pdf.add_blank_page(page_size=(200, 200))
    return pdf


def _annot(pdf: pikepdf.Pdf, subtype: str, **entries: object) -> pikepdf.Object:
    fields: dict[str, object] = {"/Type": Name("/Annot"), "/Subtype": Name(f"/{subtype}")}
    fields.update({f"/{k}": v for k, v in entries.items()})
    return pdf.make_indirect(Dictionary(fields))


def _attach(pdf: pikepdf.Pdf, page_index: int, *annots: pikepdf.Object) -> None:
    pdf.pages[page_index].obj[Name("/Annots")] = Array(list(annots))


def _annots_on(pdf: pikepdf.Pdf, page_index: int) -> list[pikepdf.Object]:
    value = pdf.pages[page_index].obj.get(Name("/Annots"))
    return [] if value is None else [value[i] for i in range(len(value))]


# ---------------------------------------------------------------------------
# fix_annot_descriptions — reference format
# ---------------------------------------------------------------------------

def test_descriptions_use_one_based_page_and_position(opts: FixOptions) -> None:
    """The off-by-one corrected here.

    The report prints pages as "p.2"; the original read the same number
    as a 0-based index, so a description written for page 2 landed on
    page 3's annotations — or was silently dropped on the last page.
    """
    pdf = _pdf(pages=2)
    _attach(pdf, 0, _annot(pdf, "Square"))
    _attach(pdf, 1, _annot(pdf, "Square"))

    result = fix_annot_descriptions(
        pdf,
        FixOptions(
            pdf_path=opts.pdf_path,
            annot_descriptions_map={"2:1": "Diagram on the second page"},
        ),
    )

    assert result.success
    assert Name("/Contents") not in _annots_on(pdf, 0)[0]
    assert str(_annots_on(pdf, 1)[0][Name("/Contents")]) == (
        "Diagram on the second page"
    )


def test_descriptions_pick_the_right_annotation_on_a_page(
    opts: FixOptions,
) -> None:
    pdf = _pdf()
    _attach(pdf, 0, _annot(pdf, "Square"), _annot(pdf, "Circle"))

    fix_annot_descriptions(
        pdf,
        FixOptions(
            pdf_path=opts.pdf_path, annot_descriptions_map={"1:2": "The circle"}
        ),
    )

    annots = _annots_on(pdf, 0)
    assert Name("/Contents") not in annots[0]
    assert str(annots[1][Name("/Contents")]) == "The circle"


def test_descriptions_reject_a_zero_reference(opts: FixOptions) -> None:
    pdf = _pdf()
    _attach(pdf, 0, _annot(pdf, "Square"))

    result = fix_annot_descriptions(
        pdf,
        FixOptions(pdf_path=opts.pdf_path, annot_descriptions_map={"0:1": "x"}),
    )

    assert not result.success
    assert Name("/Contents") not in _annots_on(pdf, 0)[0]


def test_descriptions_report_a_page_that_does_not_exist(
    opts: FixOptions,
) -> None:
    pdf = _pdf()
    _attach(pdf, 0, _annot(pdf, "Square"))

    result = fix_annot_descriptions(
        pdf,
        FixOptions(pdf_path=opts.pdf_path, annot_descriptions_map={"9:1": "x"}),
    )

    assert not result.success
    assert "page 9 does not exist" in result.description


def test_descriptions_report_partial_application(opts: FixOptions) -> None:
    pdf = _pdf()
    _attach(pdf, 0, _annot(pdf, "Square"))

    result = fix_annot_descriptions(
        pdf,
        FixOptions(
            pdf_path=opts.pdf_path,
            annot_descriptions_map={"1:1": "good", "1:5": "missing"},
        ),
    )

    assert result.success
    assert "1 of 2" in result.description


def test_descriptions_reject_a_malformed_key(opts: FixOptions) -> None:
    pdf = _pdf()
    _attach(pdf, 0, _annot(pdf, "Square"))

    result = fix_annot_descriptions(
        pdf,
        FixOptions(
            pdf_path=opts.pdf_path, annot_descriptions_map={"page-one": "x"}
        ),
    )

    assert not result.success


# ---------------------------------------------------------------------------
# fix_trapnet
# ---------------------------------------------------------------------------

def test_trapnet_annotations_are_removed(opts: FixOptions) -> None:
    pdf = _pdf()
    _attach(pdf, 0, _annot(pdf, "TrapNet"), _annot(pdf, "Link"))

    result = fix_trapnet(pdf, opts)

    assert result.success
    remaining = _annots_on(pdf, 0)
    assert len(remaining) == 1
    assert str(remaining[0][Name("/Subtype")]) == "/Link"


def test_annots_key_is_dropped_when_nothing_remains(opts: FixOptions) -> None:
    # An empty /Annots array is legal but pointless; removing the key is
    # what the original did and keeps the page clean.
    pdf = _pdf()
    _attach(pdf, 0, _annot(pdf, "TrapNet"))

    fix_trapnet(pdf, opts)

    assert Name("/Annots") not in pdf.pages[0].obj


def test_trapnet_reports_when_there_are_none(opts: FixOptions) -> None:
    pdf = _pdf()
    _attach(pdf, 0, _annot(pdf, "Link"))

    result = fix_trapnet(pdf, opts)

    assert result.success
    assert "No TrapNet" in result.description


# ---------------------------------------------------------------------------
# fix_ref_xobjects
# ---------------------------------------------------------------------------

def test_reference_xobjects_are_removed(opts: FixOptions) -> None:
    pdf = _pdf()
    referencing = pdf.make_indirect(
        Dictionary({"/Type": Name("/XObject"), "/Ref": Dictionary()})
    )
    ordinary = pdf.make_indirect(Dictionary({"/Type": Name("/XObject")}))
    pdf.pages[0].obj[Name("/Resources")] = Dictionary(
        XObject=Dictionary(Ref0=referencing, Im0=ordinary)
    )

    result = fix_ref_xobjects(pdf, opts)

    assert result.success
    xobjects = pdf.pages[0].obj[Name("/Resources")][Name("/XObject")]
    assert Name("/Ref0") not in xobjects
    assert Name("/Im0") in xobjects, "ordinary XObjects must survive"


def test_reference_xobject_removal_states_the_dangling_invocation(
    opts: FixOptions,
) -> None:
    """The caveat must reach the user, not just the docstring.

    Only the resource entry is removed; the Do operator that invoked it
    stays in the content stream and now names nothing.
    """
    pdf = _pdf()
    referencing = pdf.make_indirect(
        Dictionary({"/Type": Name("/XObject"), "/Ref": Dictionary()})
    )
    pdf.pages[0].obj[Name("/Resources")] = Dictionary(
        XObject=Dictionary(Ref0=referencing)
    )

    result = fix_ref_xobjects(pdf, opts)

    assert "Do operators" in result.description


# ---------------------------------------------------------------------------
# fix_annot_contents_lang
# ---------------------------------------------------------------------------

def test_annot_lang_is_a_no_op_when_the_document_declares_one(
    opts: FixOptions,
) -> None:
    pdf = _pdf()
    pdf.Root[Name("/Lang")] = String("en")
    _attach(pdf, 0, _annot(pdf, "Text", Contents=String("A note")))

    result = fix_annot_contents_lang(pdf, opts)

    assert result.success
    assert "inherit" in result.description


def test_annot_lang_declines_without_a_language(opts: FixOptions) -> None:
    pdf = _pdf()
    _attach(pdf, 0, _annot(pdf, "Text", Contents=String("A note")))

    result = fix_annot_contents_lang(pdf, opts)

    assert not result.success
    assert "fix_language" in result.description


def test_annot_lang_tags_only_annotations_with_text(tmp_path: Path) -> None:
    pdf = _pdf()
    with_text = _annot(pdf, "Text", Contents=String("A note"))
    without = _annot(pdf, "Link")
    _attach(pdf, 0, with_text, without)

    result = fix_annot_contents_lang(
        pdf, FixOptions(pdf_path=tmp_path / "d.pdf", lang="fr")
    )

    assert result.success
    annots = _annots_on(pdf, 0)
    assert str(annots[0][Name("/Lang")]) == "fr"
    assert Name("/Lang") not in annots[1], "nothing to announce, nothing to tag"


def test_annot_lang_skips_a_page_that_declares_its_own(
    tmp_path: Path,
) -> None:
    # Annotations inherit the page's language; overriding would be noise.
    pdf = _pdf()
    pdf.pages[0].obj[Name("/Lang")] = String("es")
    _attach(pdf, 0, _annot(pdf, "Text", Contents=String("Una nota")))

    fix_annot_contents_lang(
        pdf, FixOptions(pdf_path=tmp_path / "d.pdf", lang="fr")
    )

    assert Name("/Lang") not in _annots_on(pdf, 0)[0]
