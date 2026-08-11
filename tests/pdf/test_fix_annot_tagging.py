"""Tests for putting annotations into the structure tree.

The costly case here is not a missed widget — it is damage to
``/ParentTree``. That number tree maps every marked-content sequence in
the document back to its structure element, so mangling it untags the
whole file, and nothing about the resulting PDF looks obviously wrong.
The branching-tree test is the reason this file exists.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name

from auto_a11y.pdf.fix.annot_tagging import (
    fix_annot_tagged,
    fix_link_annotations,
    fix_widget_form_tags,
)
from auto_a11y.pdf.fix.models import FixOptions


@pytest.fixture
def opts(tmp_path: Path) -> FixOptions:
    return FixOptions(pdf_path=tmp_path / "form.pdf")


def _elem(
    pdf: pikepdf.Pdf, tag: str, kids: list[pikepdf.Object] | None = None
) -> pikepdf.Object:
    obj = pdf.make_indirect(
        Dictionary(Type=Name("/StructElem"), S=Name(f"/{tag}"))
    )
    if kids is not None:
        obj[Name("/K")] = Array(kids)
        for kid in kids:
            if isinstance(kid, pikepdf.Dictionary):
                kid[Name("/P")] = obj
    return obj


def _widget(pdf: pikepdf.Pdf) -> pikepdf.Object:
    return pdf.make_indirect(
        Dictionary(Type=Name("/Annot"), Subtype=Name("/Widget"))
    )


def _objr(pdf: pikepdf.Pdf, annot: pikepdf.Object) -> pikepdf.Object:
    return pdf.make_indirect(Dictionary(Type=Name("/OBJR"), Obj=annot))


def _document(
    pdf: pikepdf.Pdf, root: pikepdf.Object, *, parent_tree: pikepdf.Object | None
) -> None:
    struct_root = pdf.make_indirect(Dictionary(Type=Name("/StructTreeRoot")))
    struct_root[Name("/K")] = Array([root])
    if parent_tree is not None:
        struct_root[Name("/ParentTree")] = parent_tree
    pdf.Root[Name("/StructTreeRoot")] = struct_root


def _base() -> pikepdf.Pdf:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    return pdf


def _tags(pdf: pikepdf.Pdf) -> list[str]:
    from auto_a11y.pdf.audit.structure import walk_structure_tree

    elements, _ = walk_structure_tree(pdf)
    return [e.resolved_tag for e in elements]


# ---------------------------------------------------------------------------
# The ParentTree hazard
# ---------------------------------------------------------------------------

def test_a_branching_parent_tree_is_never_flattened(opts: FixOptions) -> None:
    """The most damaging bug in the original, and why this fix declines.

    A large document's /ParentTree is a multi-level number tree: the root
    holds /Kids and no /Nums. The original read /Nums from the root (found
    nothing), wrote back a /Nums containing only its own new entries, and
    deleted /Kids — throwing away every marked-content mapping in the
    document and untagging the entire file.
    """
    pdf = _base()
    widget = _widget(pdf)
    pdf.pages[0].obj[Name("/Annots")] = Array([widget])

    leaf = pdf.make_indirect(
        Dictionary(Nums=Array([0, pdf.make_indirect(Dictionary())]))
    )
    branching = pdf.make_indirect(Dictionary(Kids=Array([leaf])))
    _document(pdf, _elem(pdf, "Document"), parent_tree=branching)

    result = fix_widget_form_tags(pdf, opts)

    tree = pdf.Root[Name("/StructTreeRoot")][Name("/ParentTree")]
    assert Name("/Kids") in tree, "existing mappings must survive"
    assert Name("/Nums") not in tree, "must not flatten the tree"
    assert "branching number tree" in result.description


def test_a_flat_parent_tree_gains_the_new_entries(opts: FixOptions) -> None:
    pdf = _base()
    widget = _widget(pdf)
    pdf.pages[0].obj[Name("/Annots")] = Array([widget])
    flat = pdf.make_indirect(Dictionary(Nums=Array([])))
    _document(pdf, _elem(pdf, "Document"), parent_tree=flat)

    result = fix_widget_form_tags(pdf, opts)

    assert result.success
    nums = pdf.Root[Name("/StructTreeRoot")][Name("/ParentTree")][Name("/Nums")]
    assert len(nums) == 2, "one (key, element) pair added"
    assert int(widget[Name("/StructParent")]) == int(nums[0])


def test_existing_parent_tree_entries_are_preserved(opts: FixOptions) -> None:
    pdf = _base()
    widget = _widget(pdf)
    pdf.pages[0].obj[Name("/Annots")] = Array([widget])
    existing = pdf.make_indirect(Dictionary(S=Name("/P")))
    flat = pdf.make_indirect(Dictionary(Nums=Array([0, existing])))
    _document(pdf, _elem(pdf, "Document"), parent_tree=flat)

    fix_widget_form_tags(pdf, opts)

    nums = pdf.Root[Name("/StructTreeRoot")][Name("/ParentTree")][Name("/Nums")]
    assert len(nums) == 4, "the original pair is still there"
    assert int(nums[0]) == 0


# ---------------------------------------------------------------------------
# Wrapping a misparented widget
# ---------------------------------------------------------------------------

def test_a_widget_under_the_wrong_parent_gains_a_form_wrapper(
    opts: FixOptions,
) -> None:
    pdf = _base()
    widget = _widget(pdf)
    pdf.pages[0].obj[Name("/Annots")] = Array([widget])
    paragraph = _elem(pdf, "P", [_objr(pdf, widget)])
    _document(pdf, _elem(pdf, "Document", [paragraph]), parent_tree=None)

    result = fix_widget_form_tags(pdf, opts)

    assert result.success
    assert "Form" in _tags(pdf)
    assert "wrapped 1 misparented" in result.description


def test_a_widget_already_inside_a_form_is_left_alone(
    opts: FixOptions,
) -> None:
    pdf = _base()
    widget = _widget(pdf)
    pdf.pages[0].obj[Name("/Annots")] = Array([widget])
    form = _elem(pdf, "Form", [_objr(pdf, widget)])
    _document(pdf, _elem(pdf, "Document", [form]), parent_tree=None)
    before = _tags(pdf)

    result = fix_widget_form_tags(pdf, opts)

    assert result.success
    assert "already inside <Form>" in result.description
    assert _tags(pdf) == before


def test_wrapping_keeps_the_annotation_in_reading_order(
    opts: FixOptions,
) -> None:
    """The wrapper goes in place, so the widget keeps its position."""
    pdf = _base()
    widget = _widget(pdf)
    pdf.pages[0].obj[Name("/Annots")] = Array([widget])
    document = _elem(pdf, "Document", [
        _elem(pdf, "P"),
        _elem(pdf, "Div", [_objr(pdf, widget)]),
        _elem(pdf, "P"),
    ])
    _document(pdf, document, parent_tree=None)

    fix_widget_form_tags(pdf, opts)

    tags = _tags(pdf)
    assert tags.index("Form") > tags.index("Div")
    assert tags.count("P") == 2


# ---------------------------------------------------------------------------
# Creating a tag for an unlinked widget
# ---------------------------------------------------------------------------

def test_a_widget_absent_from_the_tree_gains_a_form_element(
    opts: FixOptions,
) -> None:
    pdf = _base()
    widget = _widget(pdf)
    pdf.pages[0].obj[Name("/Annots")] = Array([widget])
    _document(pdf, _elem(pdf, "Document"), parent_tree=None)

    result = fix_widget_form_tags(pdf, opts)

    assert result.success
    assert "created 1 new <Form> tag" in result.description
    assert "Form" in _tags(pdf)


def test_non_widget_annotations_are_ignored(opts: FixOptions) -> None:
    pdf = _base()
    link = pdf.make_indirect(
        Dictionary(Type=Name("/Annot"), Subtype=Name("/Link"))
    )
    pdf.pages[0].obj[Name("/Annots")] = Array([link])
    _document(pdf, _elem(pdf, "Document"), parent_tree=None)

    result = fix_widget_form_tags(pdf, opts)

    assert result.success
    assert "No widget annotation(s) in document" in result.description


def test_declines_without_a_structure_tree(opts: FixOptions) -> None:
    pdf = _base()
    pdf.pages[0].obj[Name("/Annots")] = Array([_widget(pdf)])

    result = fix_widget_form_tags(pdf, opts)

    assert not result.success


# ---------------------------------------------------------------------------
# fix_annot_tagged — the same machinery, a different selection
# ---------------------------------------------------------------------------

def _annot(pdf: pikepdf.Pdf, subtype: str) -> pikepdf.Object:
    return pdf.make_indirect(
        Dictionary(Type=Name("/Annot"), Subtype=Name(f"/{subtype}"))
    )


def test_a_stray_note_gains_an_annot_element(opts: FixOptions) -> None:
    pdf = _base()
    note = _annot(pdf, "Text")
    pdf.pages[0].obj[Name("/Annots")] = Array([note])
    _document(pdf, _elem(pdf, "Document"), parent_tree=None)

    result = fix_annot_tagged(pdf, opts)

    assert result.success
    assert "Annot" in _tags(pdf)


def test_a_misparented_note_is_wrapped_in_place(opts: FixOptions) -> None:
    pdf = _base()
    note = _annot(pdf, "Stamp")
    pdf.pages[0].obj[Name("/Annots")] = Array([note])
    document = _elem(pdf, "Document", [
        _elem(pdf, "P"),
        _elem(pdf, "Div", [_objr(pdf, note)]),
    ])
    _document(pdf, document, parent_tree=None)

    result = fix_annot_tagged(pdf, opts)

    assert result.success
    tags = _tags(pdf)
    assert tags.index("Annot") > tags.index("Div"), "kept its reading position"


@pytest.mark.parametrize("subtype", ["Link", "Widget", "Popup", "PrinterMark"])
def test_annotations_with_their_own_home_are_not_claimed(
    subtype: str, opts: FixOptions
) -> None:
    """Each of these belongs somewhere else, or nowhere.

    Links have <Link> and their own fix, widgets belong in <Form>, a Popup
    is attached to another annotation rather than being content, and a
    PrinterMark is a press artefact required to stay out of the tree.
    """
    pdf = _base()
    pdf.pages[0].obj[Name("/Annots")] = Array([_annot(pdf, subtype)])
    _document(pdf, _elem(pdf, "Document"), parent_tree=None)

    result = fix_annot_tagged(pdf, opts)

    assert result.success
    assert "Annot" not in _tags(pdf)


def test_annot_tagged_also_refuses_to_flatten_a_branching_parent_tree(
    opts: FixOptions,
) -> None:
    pdf = _base()
    pdf.pages[0].obj[Name("/Annots")] = Array([_annot(pdf, "Text")])
    leaf = pdf.make_indirect(
        Dictionary(Nums=Array([0, pdf.make_indirect(Dictionary())]))
    )
    branching = pdf.make_indirect(Dictionary(Kids=Array([leaf])))
    _document(pdf, _elem(pdf, "Document"), parent_tree=branching)

    result = fix_annot_tagged(pdf, opts)

    tree = pdf.Root[Name("/StructTreeRoot")][Name("/ParentTree")]
    assert Name("/Kids") in tree
    assert Name("/Nums") not in tree
    assert "branching number tree" in result.description


# ---------------------------------------------------------------------------
# fix_link_annotations — the third caller of the same machinery
# ---------------------------------------------------------------------------

def test_a_stray_link_gains_a_link_element(opts: FixOptions) -> None:
    pdf = _base()
    link = _annot(pdf, "Link")
    pdf.pages[0].obj[Name("/Annots")] = Array([link])
    _document(pdf, _elem(pdf, "Document"), parent_tree=None)

    result = fix_link_annotations(pdf, opts)

    assert result.success
    assert "Link" in _tags(pdf)


def test_a_misparented_link_is_wrapped_in_place(opts: FixOptions) -> None:
    """A link must stay where the sentence put it.

    Moving it to the end of its parent would announce the link after the
    text that introduces it, which is the problem the fix exists to solve.
    """
    pdf = _base()
    link = _annot(pdf, "Link")
    pdf.pages[0].obj[Name("/Annots")] = Array([link])
    document = _elem(pdf, "Document", [
        _elem(pdf, "P"),
        _elem(pdf, "Span", [_objr(pdf, link)]),
        _elem(pdf, "P"),
    ])
    _document(pdf, document, parent_tree=None)

    fix_link_annotations(pdf, opts)

    tags = _tags(pdf)
    assert tags.index("Link") > tags.index("Span")
    assert tags.count("P") == 2


def test_a_link_already_inside_a_link_element_is_left_alone(
    opts: FixOptions,
) -> None:
    pdf = _base()
    link = _annot(pdf, "Link")
    pdf.pages[0].obj[Name("/Annots")] = Array([link])
    wrapper = _elem(pdf, "Link", [_objr(pdf, link)])
    _document(pdf, _elem(pdf, "Document", [wrapper]), parent_tree=None)
    before = _tags(pdf)

    result = fix_link_annotations(pdf, opts)

    assert result.success
    assert _tags(pdf) == before


def test_link_fix_also_refuses_to_flatten_a_branching_parent_tree(
    opts: FixOptions,
) -> None:
    """The same guard, reached through the third caller.

    The original carried this bug in all three copies of the algorithm.
    """
    pdf = _base()
    pdf.pages[0].obj[Name("/Annots")] = Array([_annot(pdf, "Link")])
    leaf = pdf.make_indirect(
        Dictionary(Nums=Array([0, pdf.make_indirect(Dictionary())]))
    )
    branching = pdf.make_indirect(Dictionary(Kids=Array([leaf])))
    _document(pdf, _elem(pdf, "Document"), parent_tree=branching)

    result = fix_link_annotations(pdf, opts)

    tree = pdf.Root[Name("/StructTreeRoot")][Name("/ParentTree")]
    assert Name("/Kids") in tree
    assert Name("/Nums") not in tree
    assert "branching number tree" in result.description
