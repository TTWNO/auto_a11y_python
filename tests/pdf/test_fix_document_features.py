"""Tests for the document-level feature fixes.

The interesting case in this file is :func:`fix_remove_xfa` declining. A
pure XFA form has its fields nowhere else, so "removing the inaccessible
technology" and "deleting the form" are the same operation — and the fix
has to be able to tell the difference.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, String

from auto_a11y.pdf.fix.document_features import (
    fix_embedded_files,
    fix_ocg_as,
    fix_ocg_names,
    fix_page_labels,
    fix_remove_xfa,
)
from auto_a11y.pdf.fix.models import FixOptions


@pytest.fixture
def opts(tmp_path: Path) -> FixOptions:
    return FixOptions(pdf_path=tmp_path / "doc.pdf")


def _pdf() -> pikepdf.Pdf:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    return pdf


# ---------------------------------------------------------------------------
# fix_ocg_names
# ---------------------------------------------------------------------------

def test_unnamed_layers_are_named_by_position(opts: FixOptions) -> None:
    """A blank row in the layers panel is a control with no label.

    Positional naming means the label matches where the row appears,
    rather than counting only the ones that needed fixing.
    """
    pdf = _pdf()
    named = pdf.make_indirect(Dictionary(Name=String("Background")))
    blank = pdf.make_indirect(Dictionary())
    pdf.Root[Name("/OCProperties")] = Dictionary(OCGs=Array([named, blank]))

    result = fix_ocg_names(pdf, opts)

    assert result.success
    groups = pdf.Root[Name("/OCProperties")][Name("/OCGs")]
    assert str(groups[0][Name("/Name")]) == "Background"
    assert str(groups[1][Name("/Name")]) == "Layer 2"


def test_named_layers_are_untouched(opts: FixOptions) -> None:
    pdf = _pdf()
    named = pdf.make_indirect(Dictionary(Name=String("Annotations")))
    pdf.Root[Name("/OCProperties")] = Dictionary(OCGs=Array([named]))

    result = fix_ocg_names(pdf, opts)

    assert result.success
    assert "already have names" in result.description


def test_no_optional_content_is_not_a_failure(opts: FixOptions) -> None:
    result = fix_ocg_names(_pdf(), opts)

    assert result.success


# ---------------------------------------------------------------------------
# fix_ocg_as
# ---------------------------------------------------------------------------

def test_auto_state_is_removed(opts: FixOptions) -> None:
    # Content that switches itself on and off cannot be relied on, and a
    # screen reader has no way to report that it happened.
    pdf = _pdf()
    config = Dictionary(AS=Array([]))
    pdf.Root[Name("/OCProperties")] = Dictionary(D=config, OCGs=Array([]))

    result = fix_ocg_as(pdf, opts)

    assert result.success
    assert Name("/AS") not in pdf.Root[Name("/OCProperties")][Name("/D")]


def test_no_auto_state_needs_no_action(opts: FixOptions) -> None:
    pdf = _pdf()
    pdf.Root[Name("/OCProperties")] = Dictionary(D=Dictionary(), OCGs=Array([]))

    result = fix_ocg_as(pdf, opts)

    assert result.success
    assert "no action needed" in result.description


# ---------------------------------------------------------------------------
# fix_page_labels
# ---------------------------------------------------------------------------

def test_labels_are_prepended_for_uncovered_pages(opts: FixOptions) -> None:
    """A range starting partway leaves earlier pages unlabelled.

    The number printed on the page and the number the viewer shows then
    disagree, which is exactly what page labels exist to prevent.
    """
    pdf = _pdf()
    roman = Dictionary(S=Name("/r"))
    pdf.Root[Name("/PageLabels")] = Dictionary(Nums=Array([4, roman]))

    result = fix_page_labels(pdf, opts)

    assert result.success
    nums = pdf.Root[Name("/PageLabels")][Name("/Nums")]
    assert int(nums[0]) == 0
    assert int(nums[2]) == 4, "the existing range is kept, not replaced"
    assert str(nums[3][Name("/S")]) == "/r", "its numbering style survives"


def test_labels_already_starting_at_the_first_page_are_left_alone(
    opts: FixOptions,
) -> None:
    pdf = _pdf()
    pdf.Root[Name("/PageLabels")] = Dictionary(
        Nums=Array([0, Dictionary(S=Name("/D"))])
    )

    result = fix_page_labels(pdf, opts)

    assert result.success
    assert "already cover the first page" in result.description


def test_a_malformed_label_range_is_replaced(opts: FixOptions) -> None:
    pdf = _pdf()
    pdf.Root[Name("/PageLabels")] = Dictionary(Nums=Array([]))

    result = fix_page_labels(pdf, opts)

    assert result.success
    assert len(pdf.Root[Name("/PageLabels")][Name("/Nums")]) == 2


# ---------------------------------------------------------------------------
# fix_embedded_files
# ---------------------------------------------------------------------------

def _with_attachments(pdf: pikepdf.Pdf, *specs: pikepdf.Object) -> None:
    pairs: list[object] = []
    for index, spec in enumerate(specs):
        pairs.extend([String(f"file{index}.txt"), spec])
    pdf.Root[Name("/Names")] = Dictionary(
        EmbeddedFiles=Dictionary(Names=Array(pairs))
    )


def test_a_missing_unicode_filename_is_copied_from_the_ascii_one(
    opts: FixOptions,
) -> None:
    pdf = _pdf()
    spec = pdf.make_indirect(Dictionary(F=String("report.pdf")))
    _with_attachments(pdf, spec)

    result = fix_embedded_files(pdf, opts)

    assert result.success
    assert str(spec[Name("/UF")]) == "report.pdf"


def test_a_missing_ascii_filename_is_copied_from_the_unicode_one(
    opts: FixOptions,
) -> None:
    pdf = _pdf()
    spec = pdf.make_indirect(Dictionary(UF=String("rapport.pdf")))
    _with_attachments(pdf, spec)

    fix_embedded_files(pdf, opts)

    assert str(spec[Name("/F")]) == "rapport.pdf"


def test_an_attachment_with_no_filename_takes_the_name_tree_key(
    opts: FixOptions,
) -> None:
    # Better a name from the key than an attachment offered with none.
    pdf = _pdf()
    spec = pdf.make_indirect(Dictionary())
    _with_attachments(pdf, spec)

    fix_embedded_files(pdf, opts)

    assert str(spec[Name("/F")]) == "file0.txt"
    assert str(spec[Name("/UF")]) == "file0.txt"


def test_attachments_in_a_branching_name_tree_are_reached(
    opts: FixOptions,
) -> None:
    """Name trees branch, and this one walks /Kids properly.

    Worth stating because the same original mishandles the /ParentTree
    number tree in exactly this situation.
    """
    pdf = _pdf()
    spec = pdf.make_indirect(Dictionary(F=String("deep.txt")))
    leaf = pdf.make_indirect(
        Dictionary(Names=Array([String("deep.txt"), spec]))
    )
    pdf.Root[Name("/Names")] = Dictionary(
        EmbeddedFiles=Dictionary(Kids=Array([leaf]))
    )

    result = fix_embedded_files(pdf, opts)

    assert result.success
    assert str(spec[Name("/UF")]) == "deep.txt"


def test_complete_attachments_are_left_alone(opts: FixOptions) -> None:
    pdf = _pdf()
    spec = pdf.make_indirect(
        Dictionary(F=String("a.txt"), UF=String("a.txt"))
    )
    _with_attachments(pdf, spec)

    result = fix_embedded_files(pdf, opts)

    assert result.success
    assert "already have both filenames" in result.description


# ---------------------------------------------------------------------------
# fix_remove_xfa
# ---------------------------------------------------------------------------

def test_xfa_is_removed_when_acroform_fields_remain(opts: FixOptions) -> None:
    pdf = _pdf()
    field = pdf.make_indirect(Dictionary(FT=Name("/Tx"), T=String("name")))
    pdf.Root[Name("/AcroForm")] = pdf.make_indirect(
        Dictionary(XFA=Array([]), Fields=Array([field]))
    )

    result = fix_remove_xfa(pdf, opts)

    assert result.success
    assert Name("/XFA") not in pdf.Root[Name("/AcroForm")]
    assert len(pdf.Root[Name("/AcroForm")][Name("/Fields")]) == 1


def test_a_pure_xfa_form_is_never_stripped(opts: FixOptions) -> None:
    """The refusal that makes this fix safe.

    With no AcroForm fields, the form exists only as XFA. Removing it
    would not make the form accessible — it would delete the form.
    """
    pdf = _pdf()
    pdf.Root[Name("/AcroForm")] = pdf.make_indirect(
        Dictionary(XFA=Array([]), Fields=Array([]))
    )

    result = fix_remove_xfa(pdf, opts)

    assert not result.success
    assert Name("/XFA") in pdf.Root[Name("/AcroForm")]
    assert "delete the form" in result.description


def test_no_xfa_is_not_a_failure(opts: FixOptions) -> None:
    pdf = _pdf()
    pdf.Root[Name("/AcroForm")] = pdf.make_indirect(Dictionary(Fields=Array([])))

    result = fix_remove_xfa(pdf, opts)

    assert result.success
    assert "No XFA data" in result.description
