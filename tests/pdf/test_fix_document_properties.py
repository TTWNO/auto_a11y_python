"""Tests for the document-property fixes.

Two things are worth guarding here beyond "the entry gets written".

First, several of these fixes are allowed to *decline*. A fix that refuses
and says why is a correct outcome, not a failure, and the cases where it
must refuse are exactly the cases where guessing would put a false
accessibility claim into the file.

Second, the fixes must never mutate the original on disk — the runner
opens read-only and saves elsewhere — because a wrong fix that overwrote
the source would be unrecoverable.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest
from pikepdf import Dictionary, Name, String

from auto_a11y.pdf.fix import FixOptions, apply_fixes
from auto_a11y.pdf.fix.document_properties import (
    fix_language,
    fix_mark_info,
    fix_metadata,
    fix_title,
)


def _blank_pdf() -> pikepdf.Pdf:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    return pdf


@pytest.fixture
def opts(tmp_path: Path) -> FixOptions:
    return FixOptions(pdf_path=tmp_path / "annual_report-2025.pdf")


# ---------------------------------------------------------------------------
# fix_title
# ---------------------------------------------------------------------------

def test_title_uses_the_supplied_value(opts: FixOptions) -> None:
    pdf = _blank_pdf()
    result = fix_title(pdf, FixOptions(pdf_path=opts.pdf_path, title="Q3 Results"))

    assert result.success
    assert str(pdf.trailer[Name("/Info")][Name("/Title")]) == "Q3 Results"


def test_title_is_derived_from_the_filename_when_absent(opts: FixOptions) -> None:
    pdf = _blank_pdf()
    result = fix_title(pdf, opts)

    assert result.success
    # "annual_report-2025" → "Annual Report 2025"
    assert str(pdf.trailer[Name("/Info")][Name("/Title")]) == "Annual Report 2025"


def test_title_also_enables_display_doc_title(opts: FixOptions) -> None:
    # A title the viewer never shows still leaves the filename in the
    # window title, which is what a screen reader announces on open.
    pdf = _blank_pdf()
    fix_title(pdf, opts)

    assert bool(pdf.Root[Name("/ViewerPreferences")][Name("/DisplayDocTitle")]) is True


def test_title_does_not_overwrite_an_existing_one_without_input(
    opts: FixOptions,
) -> None:
    pdf = _blank_pdf()
    info = pdf.make_indirect(Dictionary())
    info[Name("/Title")] = String("Already Correct")
    pdf.trailer[Name("/Info")] = info

    fix_title(pdf, opts)

    assert str(pdf.trailer[Name("/Info")][Name("/Title")]) == "Already Correct"


# ---------------------------------------------------------------------------
# fix_language — including the deliberate divergence from pdfMax
# ---------------------------------------------------------------------------

def test_language_uses_the_supplied_code(opts: FixOptions) -> None:
    pdf = _blank_pdf()
    result = fix_language(pdf, FixOptions(pdf_path=opts.pdf_path, lang="fr-CA"))

    assert result.success
    assert str(pdf.Root[Name("/Lang")]) == "fr-CA"


def test_language_leaves_an_existing_declaration_alone(opts: FixOptions) -> None:
    pdf = _blank_pdf()
    pdf.Root[Name("/Lang")] = String("fr")

    result = fix_language(pdf, FixOptions(pdf_path=opts.pdf_path, lang="en"))

    assert result.success
    assert str(pdf.Root[Name("/Lang")]) == "fr", "must not relabel a declared language"


def test_language_declines_rather_than_guessing_english(opts: FixOptions) -> None:
    """The divergence from pdfMax, which defaults an unknown document to "en".

    A blank page yields no text to sample. Writing "en" here would make a
    screen reader read a French document in an English voice, and nothing
    in the UI would reveal that the value was a guess.
    """
    pdf = _blank_pdf()
    result = fix_language(pdf, opts)

    assert not result.success
    assert Name("/Lang") not in pdf.Root
    assert "language" in result.description.lower()


# ---------------------------------------------------------------------------
# fix_mark_info
# ---------------------------------------------------------------------------

def test_mark_info_declines_when_there_is_no_structure_tree(
    opts: FixOptions,
) -> None:
    # Setting /Marked on an untagged file makes it claim an accessibility
    # it does not have — worse than the honest failure.
    pdf = _blank_pdf()
    result = fix_mark_info(pdf, opts)

    assert not result.success
    assert Name("/MarkInfo") not in pdf.Root


def test_mark_info_sets_the_flag_when_tags_exist(opts: FixOptions) -> None:
    pdf = _blank_pdf()
    struct_root = pdf.make_indirect(Dictionary())
    struct_root[Name("/K")] = pdf.make_indirect(Dictionary())
    pdf.Root[Name("/StructTreeRoot")] = struct_root

    result = fix_mark_info(pdf, opts)

    assert result.success
    assert bool(pdf.Root[Name("/MarkInfo")][Name("/Marked")]) is True


# ---------------------------------------------------------------------------
# fix_metadata
# ---------------------------------------------------------------------------

def test_metadata_fills_defaults_and_a_creation_date(opts: FixOptions) -> None:
    pdf = _blank_pdf()
    result = fix_metadata(pdf, opts)

    assert result.success
    info = pdf.trailer[Name("/Info")]
    assert str(info[Name("/Author")]) == "Unknown"
    assert str(info[Name("/CreationDate")]).startswith("D:")


def test_metadata_prefers_supplied_values(opts: FixOptions) -> None:
    pdf = _blank_pdf()
    result = fix_metadata(
        pdf, FixOptions(pdf_path=opts.pdf_path, author="CNIB", creator="InDesign")
    )

    assert result.success
    info = pdf.trailer[Name("/Info")]
    assert str(info[Name("/Author")]) == "CNIB"
    assert str(info[Name("/Creator")]) == "InDesign"


# ---------------------------------------------------------------------------
# Runner integration
# ---------------------------------------------------------------------------

def test_apply_fixes_writes_a_copy_and_leaves_the_original_untouched(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.pdf"
    _blank_pdf().save(source)
    before = source.read_bytes()

    run = apply_fixes(
        pdf_path=source,
        fix_ids=["fix_title"],
        options=FixOptions(pdf_path=source, title="Fixed"),
    )

    assert run.output_path == tmp_path / "source_fixed.pdf"
    assert run.output_path.is_file()
    assert source.read_bytes() == before, "the original must never be rewritten"

    with pikepdf.open(run.output_path) as fixed:
        assert str(fixed.trailer[Name("/Info")][Name("/Title")]) == "Fixed"


def test_apply_fixes_reports_per_fix_outcomes(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    _blank_pdf().save(source)

    # fix_mark_info must decline (no structure tree) without preventing
    # fix_title from applying or the file from being saved.
    run = apply_fixes(
        pdf_path=source,
        fix_ids=["fix_title", "fix_mark_info"],
        options=FixOptions(pdf_path=source, title="Fixed"),
    )

    assert run.success_count == 1
    assert run.failure_count == 1
    assert [r.fix_id for r in run.results] == ["fix_title", "fix_mark_info"]
    assert run.output_path.is_file()


def test_apply_fixes_rejects_an_unknown_fix_id(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    _blank_pdf().save(source)

    with pytest.raises(Exception, match="Unknown fix IDs"):
        apply_fixes(pdf_path=source, fix_ids=["fix_nonexistent"])


def test_apply_fixes_reports_progress(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    _blank_pdf().save(source)
    seen: list[tuple[str, int]] = []

    apply_fixes(
        pdf_path=source,
        fix_ids=["fix_title"],
        options=FixOptions(pdf_path=source, title="Fixed"),
        on_progress=lambda message, percent: seen.append((message, percent)),
    )

    assert seen[-1] == ("Complete", 100)
    assert [percent for _, percent in seen] == sorted(percent for _, percent in seen)
