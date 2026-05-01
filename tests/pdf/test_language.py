"""Tests for the shared PDF language helper."""
from __future__ import annotations

from pathlib import Path

import pikepdf

from auto_a11y.pdf.language import detect_pdf_language


def test_detect_declared_language_from_catalog(tmp_path: Path) -> None:
    """A PDF with /Lang set in the catalog reports method='catalog'."""
    pdf_path = tmp_path / "en.pdf"
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.Root["/Lang"] = pikepdf.String("en-US")
    pdf.save(pdf_path)
    pdf.close()

    result = detect_pdf_language(pdf_path)

    assert result.declared_lang == "en-US"
    assert result.detected_lang is None
    assert result.method == "catalog"
    assert result.error is None


def test_detect_inferred_language(tmp_path: Path) -> None:
    """A PDF without /Lang and no inferable text yields method='none'."""
    pdf_path = tmp_path / "nolang.pdf"
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.save(pdf_path)
    pdf.close()

    result = detect_pdf_language(pdf_path)

    assert result.declared_lang is None
    assert result.detected_lang is None
    assert result.method == "none"
    assert result.error is None


def test_detect_handles_corrupt_pdf(tmp_path: Path) -> None:
    """A corrupt PDF returns method='none' with an error message."""
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")

    result = detect_pdf_language(bad)

    assert result.declared_lang is None
    assert result.detected_lang is None
    assert result.method == "none"
    assert result.error is not None


def test_language_detection_method_literal_contract(tmp_path: Path) -> None:
    """LanguageDetection.method must be one of the documented Literal values."""
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    result = detect_pdf_language(bad)
    assert result.method in {'catalog', 'info_dict', 'word_frequency', 'ai', 'none'}
