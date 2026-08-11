"""Tests for the language-tagging fixes.

The failure these guard against is a document that looks correctly
labelled and reads aloud wrongly. An unrecognised tag makes a screen
reader fall back to its default voice with no indication, and a *wrong*
tag makes it commit to the wrong one — so the tests care as much about
what is left alone as about what is corrected.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, String

from auto_a11y.pdf.fix.language import (
    correct_language_tag,
    fix_lang_bcp47,
    fix_outline_lang,
)
from auto_a11y.pdf.fix.models import FixOptions


@pytest.fixture
def opts(tmp_path: Path) -> FixOptions:
    return FixOptions(pdf_path=tmp_path / "doc.pdf")


def _pdf() -> pikepdf.Pdf:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    return pdf


def _tagged(pdf: pikepdf.Pdf, *langs: str) -> list[pikepdf.Dictionary]:
    """A structure tree of paragraphs, each declaring the given language."""
    nodes: list[pikepdf.Dictionary] = [
        pdf.make_indirect(
            Dictionary(Type=Name("/StructElem"), S=Name("/P"), Lang=String(lang))
        )
        for lang in langs
    ]
    struct_root = pdf.make_indirect(Dictionary(Type=Name("/StructTreeRoot")))
    struct_root[Name("/K")] = Array(nodes)
    pdf.Root[Name("/StructTreeRoot")] = struct_root
    return nodes


# ---------------------------------------------------------------------------
# correct_language_tag
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("eng", "en"),
        ("fre", "fr"),
        ("fra", "fr"),
        ("French", "fr"),
        ("ENGLISH", "en"),
        ("en_US", "en-US"),
        ("fr_CA", "fr-CA"),
    ],
)
def test_unambiguous_values_are_corrected(given: str, expected: str) -> None:
    assert correct_language_tag(given) == expected


@pytest.mark.parametrize("valid", ["en", "fr-CA", "zh-Hant-TW"])
def test_valid_tags_are_left_alone(valid: str) -> None:
    assert correct_language_tag(valid) is None


@pytest.mark.parametrize("ambiguous", ["Canadian", "latin", "qqq", "??"])
def test_values_needing_a_guess_are_left_alone(ambiguous: str) -> None:
    """Writing a guessed region silently changes pronunciation.

    Nobody reviewing the file afterwards would see that a decision had
    been made on their behalf.
    """
    assert correct_language_tag(ambiguous) is None


# ---------------------------------------------------------------------------
# fix_lang_bcp47
# ---------------------------------------------------------------------------

def test_the_document_language_is_corrected(opts: FixOptions) -> None:
    pdf = _pdf()
    pdf.Root[Name("/Lang")] = String("eng")

    result = fix_lang_bcp47(pdf, opts)

    assert result.success
    assert str(pdf.Root[Name("/Lang")]) == "en"


def test_element_languages_are_corrected(opts: FixOptions) -> None:
    pdf = _pdf()
    nodes = _tagged(pdf, "fre", "en", "German")

    result = fix_lang_bcp47(pdf, opts)

    assert result.success
    assert str(nodes[0][Name("/Lang")]) == "fr"
    assert str(nodes[1][Name("/Lang")]) == "en", "already valid, untouched"
    assert str(nodes[2][Name("/Lang")]) == "de"


def test_uncorrectable_values_are_reported_not_guessed(
    opts: FixOptions,
) -> None:
    pdf = _pdf()
    nodes = _tagged(pdf, "Canadian")

    result = fix_lang_bcp47(pdf, opts)

    assert not result.success
    assert str(nodes[0][Name("/Lang")]) == "Canadian", "left as a reported fault"
    assert "without guessing" in result.description


def test_a_mix_reports_both_outcomes(opts: FixOptions) -> None:
    pdf = _pdf()
    _tagged(pdf, "eng", "Canadian")

    result = fix_lang_bcp47(pdf, opts)

    assert result.success, "the correctable one should still be corrected"
    assert "Corrected 1" in result.description
    assert "Canadian" in result.description


def test_all_valid_reports_cleanly(opts: FixOptions) -> None:
    pdf = _pdf()
    pdf.Root[Name("/Lang")] = String("en-CA")
    _tagged(pdf, "fr-CA")

    result = fix_lang_bcp47(pdf, opts)

    assert result.success
    assert "already valid" in result.description


# ---------------------------------------------------------------------------
# fix_outline_lang
# ---------------------------------------------------------------------------

def _with_bookmarks(pdf: pikepdf.Pdf, count: int) -> list[pikepdf.Dictionary]:
    entries: list[pikepdf.Dictionary] = [
        pdf.make_indirect(Dictionary(Title=String(f"Section {n}")))
        for n in range(count)
    ]
    for index, entry in enumerate(entries):
        if index:
            entry[Name("/Prev")] = entries[index - 1]
        if index < len(entries) - 1:
            entry[Name("/Next")] = entries[index + 1]
    pdf.Root[Name("/Outlines")] = pdf.make_indirect(
        Dictionary(Type=Name("/Outlines"), First=entries[0], Last=entries[-1])
    )
    return entries


def test_bookmarks_inherit_the_document_language(opts: FixOptions) -> None:
    pdf = _pdf()
    pdf.Root[Name("/Lang")] = String("fr-CA")
    entries = _with_bookmarks(pdf, 2)

    result = fix_outline_lang(pdf, opts)

    assert result.success
    assert all(str(e[Name("/Lang")]) == "fr-CA" for e in entries)


def test_nested_bookmarks_are_reached(opts: FixOptions) -> None:
    pdf = _pdf()
    pdf.Root[Name("/Lang")] = String("en")
    child = pdf.make_indirect(Dictionary(Title=String("Nested")))
    parent = pdf.make_indirect(Dictionary(Title=String("Top"), First=child))
    pdf.Root[Name("/Outlines")] = pdf.make_indirect(
        Dictionary(Type=Name("/Outlines"), First=parent, Last=parent)
    )

    fix_outline_lang(pdf, opts)

    assert str(child[Name("/Lang")]) == "en"


def test_bookmarks_declining_their_own_language_are_left_alone(
    opts: FixOptions,
) -> None:
    pdf = _pdf()
    pdf.Root[Name("/Lang")] = String("en")
    entries = _with_bookmarks(pdf, 1)
    entries[0][Name("/Lang")] = String("fr")

    fix_outline_lang(pdf, opts)

    assert str(entries[0][Name("/Lang")]) == "fr"


def test_outline_lang_declines_rather_than_stamping_english(
    opts: FixOptions,
) -> None:
    """The divergence from pdfMax, which writes "en" unconditionally.

    Labelling a French document's bookmark panel as English makes it read
    aloud wrongly while appearing correctly tagged to anyone checking.
    """
    pdf = _pdf()
    entries = _with_bookmarks(pdf, 1)

    result = fix_outline_lang(pdf, opts)

    assert not result.success
    assert Name("/Lang") not in entries[0]
    assert "fix_language" in result.description


def test_a_supplied_language_is_used_when_the_document_declares_none(
    tmp_path: Path,
) -> None:
    pdf = _pdf()
    entries = _with_bookmarks(pdf, 1)

    result = fix_outline_lang(
        pdf, FixOptions(pdf_path=tmp_path / "d.pdf", lang="es")
    )

    assert result.success
    assert str(entries[0][Name("/Lang")]) == "es"


def test_no_bookmarks_is_not_a_failure(opts: FixOptions) -> None:
    pdf = _pdf()
    pdf.Root[Name("/Lang")] = String("en")

    result = fix_outline_lang(pdf, opts)

    assert result.success
    assert "No bookmarks" in result.description
