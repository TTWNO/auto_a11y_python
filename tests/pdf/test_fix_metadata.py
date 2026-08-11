"""Tests for the metadata and conformance-claim fixes.

The load-bearing case in this file is :func:`fix_pdfua_identifier`.
``pdfuaid:part`` is the document asserting it meets PDF/UA-1, and
conforming checkers take it at its word — so writing it onto a document
that cannot possibly conform produces a file that lies about itself.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest
from pikepdf import Dictionary, Name, String

from auto_a11y.pdf.fix.metadata import (
    fix_accessibility_permission,
    fix_display_doc_title,
    fix_metadata_lang,
    fix_pdfua_identifier,
    fix_suspects,
    fix_xmp_title,
)
from auto_a11y.pdf.fix.models import FixOptions


@pytest.fixture
def opts(tmp_path: Path) -> FixOptions:
    return FixOptions(pdf_path=tmp_path / "doc.pdf")


def _pdf(tagged: bool = False) -> pikepdf.Pdf:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    if tagged:
        struct_root = pdf.make_indirect(Dictionary())
        struct_root[Name("/K")] = pdf.make_indirect(Dictionary())
        pdf.Root[Name("/StructTreeRoot")] = struct_root
        mark_info = pdf.make_indirect(Dictionary())
        mark_info[Name("/Marked")] = True
        pdf.Root[Name("/MarkInfo")] = mark_info
    return pdf


def _xmp(pdf: pikepdf.Pdf) -> str:
    meta = pdf.Root.get(Name("/Metadata"))
    if meta is None:
        return ""
    return bytes(meta.read_bytes()).decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# fix_pdfua_identifier — the conformance claim
# ---------------------------------------------------------------------------

def test_pdfua_identifier_refuses_on_an_untagged_document(
    opts: FixOptions,
) -> None:
    """The divergence from pdfMax, which writes the claim unconditionally.

    An untagged document cannot conform to PDF/UA-1 under any reading, so
    the identifier would be a false assertion that checkers and assistive
    technology would then act on.
    """
    pdf = _pdf(tagged=False)

    result = fix_pdfua_identifier(pdf, opts)

    assert not result.success
    assert "pdfuaid" not in _xmp(pdf)
    assert "false" in result.description.lower()


def test_pdfua_identifier_refuses_when_tags_exist_but_marked_is_absent(
    opts: FixOptions,
) -> None:
    pdf = _pdf(tagged=False)
    struct_root = pdf.make_indirect(Dictionary())
    struct_root[Name("/K")] = pdf.make_indirect(Dictionary())
    pdf.Root[Name("/StructTreeRoot")] = struct_root

    result = fix_pdfua_identifier(pdf, opts)

    assert not result.success


def test_pdfua_identifier_is_written_for_a_tagged_document(
    opts: FixOptions,
) -> None:
    pdf = _pdf(tagged=True)

    result = fix_pdfua_identifier(pdf, opts)

    assert result.success
    assert "pdfuaid" in _xmp(pdf)


def test_pdfua_identifier_preserves_existing_metadata(
    opts: FixOptions,
) -> None:
    """Written through pikepdf's metadata API rather than by splicing XML.

    The original edited the packet with a string replace, which loses
    anything the pattern did not anticipate.
    """
    pdf = _pdf(tagged=True)
    with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
        meta["dc:creator"] = ["CNIB"]

    fix_pdfua_identifier(pdf, opts)

    xmp = _xmp(pdf)
    assert "pdfuaid" in xmp
    assert "CNIB" in xmp, "pre-existing metadata must survive"


def test_pdfua_identifier_is_idempotent(opts: FixOptions) -> None:
    pdf = _pdf(tagged=True)
    fix_pdfua_identifier(pdf, opts)

    result = fix_pdfua_identifier(pdf, opts)

    assert result.success
    assert "already present" in result.description


# ---------------------------------------------------------------------------
# fix_display_doc_title
# ---------------------------------------------------------------------------

def test_display_doc_title_is_set(opts: FixOptions) -> None:
    pdf = _pdf()

    result = fix_display_doc_title(pdf, opts)

    assert result.success
    assert bool(pdf.Root[Name("/ViewerPreferences")][Name("/DisplayDocTitle")])


def test_display_doc_title_is_idempotent(opts: FixOptions) -> None:
    pdf = _pdf()
    fix_display_doc_title(pdf, opts)

    result = fix_display_doc_title(pdf, opts)

    assert "already set" in result.description


# ---------------------------------------------------------------------------
# fix_suspects
# ---------------------------------------------------------------------------

def test_suspects_flag_is_cleared(opts: FixOptions) -> None:
    pdf = _pdf(tagged=True)
    pdf.Root[Name("/MarkInfo")][Name("/Suspects")] = True

    result = fix_suspects(pdf, opts)

    assert result.success
    assert Name("/Suspects") not in pdf.Root[Name("/MarkInfo")]


def test_suspects_is_never_set_only_cleared(opts: FixOptions) -> None:
    # Clearing /Suspects is a person vouching for the tags. There is no
    # circumstance in which this fix should assert the opposite.
    pdf = _pdf(tagged=True)

    result = fix_suspects(pdf, opts)

    assert result.success
    assert Name("/Suspects") not in pdf.Root[Name("/MarkInfo")]


def test_suspects_without_markinfo_does_nothing(opts: FixOptions) -> None:
    pdf = _pdf()

    result = fix_suspects(pdf, opts)

    assert result.success
    assert "nothing to clear" in result.description


# ---------------------------------------------------------------------------
# fix_xmp_title
# ---------------------------------------------------------------------------

def test_xmp_title_prefers_the_supplied_value(tmp_path: Path) -> None:
    pdf = _pdf()

    result = fix_xmp_title(
        pdf, FixOptions(pdf_path=tmp_path / "d.pdf", title="Annual Report")
    )

    assert result.success
    assert "Annual Report" in _xmp(pdf)


def test_xmp_title_falls_back_to_the_info_dictionary(opts: FixOptions) -> None:
    pdf = _pdf()
    info = pdf.make_indirect(Dictionary())
    info[Name("/Title")] = String("From Info Dict")
    pdf.trailer[Name("/Info")] = info

    result = fix_xmp_title(pdf, opts)

    assert result.success
    assert "From Info Dict" in _xmp(pdf)


def test_xmp_title_points_at_the_fix_that_would_supply_one(
    opts: FixOptions,
) -> None:
    pdf = _pdf()

    result = fix_xmp_title(pdf, opts)

    assert not result.success
    assert "fix_title" in result.description


# ---------------------------------------------------------------------------
# fix_metadata_lang
# ---------------------------------------------------------------------------

def test_metadata_lang_uses_an_existing_declaration(opts: FixOptions) -> None:
    pdf = _pdf()
    pdf.Root[Name("/Lang")] = String("fr-CA")

    result = fix_metadata_lang(pdf, opts)

    assert result.success
    assert "fr-CA" in result.description


def test_metadata_lang_sets_a_supplied_language(tmp_path: Path) -> None:
    pdf = _pdf()

    result = fix_metadata_lang(
        pdf, FixOptions(pdf_path=tmp_path / "d.pdf", lang="en")
    )

    assert result.success
    assert str(pdf.Root[Name("/Lang")]) == "en"


def test_metadata_lang_points_at_fix_language(opts: FixOptions) -> None:
    pdf = _pdf()

    result = fix_metadata_lang(pdf, opts)

    assert not result.success
    assert "fix_language" in result.description


# ---------------------------------------------------------------------------
# fix_accessibility_permission
# ---------------------------------------------------------------------------

def test_accessibility_permission_reports_an_unencrypted_document(
    opts: FixOptions,
) -> None:
    pdf = _pdf()

    result = fix_accessibility_permission(pdf, opts)

    assert result.success
    assert "already unrestricted" in result.description
