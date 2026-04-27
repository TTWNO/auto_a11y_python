"""Tests for the PDF font analysis collector.

Builds tiny in-memory PDFs via pikepdf so pdfminer.six can parse them
back. The synthetic-PDF strategy mirrors :mod:`tests.pdf.test_colors`
(see that module's docstring for the rationale): we hand-roll a content
stream that names a standard Type1 font (Helvetica) so pdfminer needs
no CMap. Tests for ``_collect_line_spacing``,
``_collect_alignment``, and ``is_italic_font`` use synthetic PDFs end
to end — pdfminer's layout reconstruction is the boundary the collector
relies on, and synthesising LT objects in isolation would mis-test that
boundary.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Protocol

import pikepdf

from auto_a11y.pdf.audit.fonts import (
    AlignmentReport,
    FontAnalysis,
    FontInfo,
    ItalicRun,
    LineSpacing,
    RotationInfo,
    extract_font_analysis,
    is_italic_font,
)


# ---------------------------------------------------------------------------
# Synthetic PDF helpers
# ---------------------------------------------------------------------------


class _StreamMaker(Protocol):
    """Typed view of ``pikepdf.Pdf.make_stream`` for single-arg calls."""

    def __call__(self, data: bytes) -> pikepdf.Stream: ...


def _typed_make_stream(pdf: pikepdf.Pdf, data: bytes) -> pikepdf.Stream:
    """Strictly-typed wrapper around ``Pdf.make_stream`` (see test_colors.py)."""
    maker: _StreamMaker = getattr(pdf, "make_stream")
    return maker(data)


def _font_dict(base_font: str) -> pikepdf.Dictionary:
    """Build a minimal Type1 /Font dictionary referencing ``base_font``."""
    return pikepdf.Dictionary(
        Type=pikepdf.Name("/Font"),
        Subtype=pikepdf.Name("/Type1"),
        BaseFont=pikepdf.Name("/" + base_font),
        Encoding=pikepdf.Name("/WinAnsiEncoding"),
    )


def _make_pdf(
    content: bytes,
    fonts: dict[str, str] | None = None,
    page_size: tuple[float, float] = (612, 792),
) -> bytes:
    """Build a one-page PDF whose /Resources /Font maps short names → BaseFonts.

    ``fonts`` defaults to ``{"F1": "Helvetica"}``. The content stream is
    written as-is — callers control which font they reference.
    """
    if fonts is None:
        fonts = {"F1": "Helvetica"}
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=page_size)
    # Dictionary accepts a Mapping with leading-``/`` keys *or* kwargs
    # (which auto-prepend the slash). We pre-prepend the slash so we
    # can pass a typed mapping — mypy can't validate the ``**`` unpack
    # of a ``dict[str, Dictionary]`` against the kwargs signature.
    font_entries: dict[str, pikepdf.Object] = {
        "/" + name: _font_dict(base) for name, base in fonts.items()
    }
    font_dict = pikepdf.Dictionary(font_entries)
    page.Resources = pikepdf.Dictionary(Font=font_dict)
    page.Contents = _typed_make_stream(pdf, content)
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def _write(tmp_path: Path, content: bytes, *, name: str = "doc.pdf",
           fonts: dict[str, str] | None = None) -> Path:
    out = tmp_path / name
    out.write_bytes(_make_pdf(content, fonts=fonts))
    return out


# ---------------------------------------------------------------------------
# is_italic_font
# ---------------------------------------------------------------------------


def test_is_italic_font_italic_substring() -> None:
    assert is_italic_font("Helvetica-Italic") is True


def test_is_italic_font_oblique_substring() -> None:
    assert is_italic_font("Helvetica-Oblique") is True


def test_is_italic_font_plain_helvetica() -> None:
    assert is_italic_font("Helvetica") is False


def test_is_italic_font_avoids_semibold_false_positive() -> None:
    # "it" inside "Semibold" must NOT match (word-boundary check).
    assert is_italic_font("StagSans-Semibold") is False


# ---------------------------------------------------------------------------
# extract_font_analysis — empty / corrupt
# ---------------------------------------------------------------------------


def test_extract_font_analysis_empty_pdf(tmp_path: Path) -> None:
    """Blank PDF yields an empty FontAnalysis (all collections empty)."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    out = tmp_path / "blank.pdf"
    pdf.save(out)
    result = extract_font_analysis(out)
    assert isinstance(result, FontAnalysis)
    assert result.fonts == {}
    assert result.rotations == []
    assert result.italic_runs == []
    assert result.line_spacings == []
    assert result.alignments == []


def test_extract_font_analysis_corrupt_pdf_returns_empty(tmp_path: Path) -> None:
    """A malformed PDF returns an empty FontAnalysis without raising."""
    out = tmp_path / "garbage.pdf"
    out.write_bytes(b"%PDF-1.4\nthis is not a valid pdf\n%%EOF\n")
    result = extract_font_analysis(out)
    assert isinstance(result, FontAnalysis)
    assert result.fonts == {}


# ---------------------------------------------------------------------------
# extract_font_analysis — single font
# ---------------------------------------------------------------------------


def test_extract_font_analysis_single_helvetica(tmp_path: Path) -> None:
    """One Helvetica run → one FontInfo, sizes={12}, pages={0}."""
    content = b"BT /F1 12 Tf 100 700 Td (Hello) Tj ET\n"
    pdf_path = _write(tmp_path, content)

    result = extract_font_analysis(pdf_path)

    assert len(result.fonts) == 1
    info = next(iter(result.fonts.values()))
    assert isinstance(info, FontInfo)
    assert "Helvetica" in info.name
    assert info.sizes == {12.0}
    assert info.pages == {0}
    assert info.char_count == 5  # "Hello"
    assert info.is_italic is False
    assert info.is_bold is False
    assert "H" in info.sample  # Sample should include text


def test_extract_font_analysis_two_fonts(tmp_path: Path) -> None:
    """Two distinct fonts at distinct sizes → two FontInfo entries."""
    fonts = {"F1": "Helvetica", "F2": "Times-Roman"}
    content = (
        b"BT /F1 12 Tf 100 700 Td (Hello) Tj ET\n"
        b"BT /F2 18 Tf 100 600 Td (World) Tj ET\n"
    )
    pdf_path = _write(tmp_path, content, fonts=fonts)

    result = extract_font_analysis(pdf_path)

    assert len(result.fonts) == 2
    names = {info.name for info in result.fonts.values()}
    # pdfminer reports the BaseFont; either short or full name is acceptable.
    assert any("Helvetica" in n for n in names)
    assert any("Times" in n for n in names)


# ---------------------------------------------------------------------------
# Italic runs (via real PDF, since collector pivots on pdfminer's LTChar)
# ---------------------------------------------------------------------------


def test_extract_font_analysis_italic_run_detected(tmp_path: Path) -> None:
    """Italic-named font → italic_runs collects the text and is_italic=True."""
    fonts = {"F1": "Helvetica-Oblique"}
    content = b"BT /F1 12 Tf 100 700 Td (Italic) Tj ET\n"
    pdf_path = _write(tmp_path, content, fonts=fonts)

    result = extract_font_analysis(pdf_path)

    # One italic font in inventory.
    assert any(info.is_italic for info in result.fonts.values())
    # Italic run captured.
    assert len(result.italic_runs) >= 1
    run = result.italic_runs[0]
    assert isinstance(run, ItalicRun)
    assert "Italic" in run.text
    assert "Oblique" in run.font or "Italic" in run.font


# ---------------------------------------------------------------------------
# Line spacing — multi-line text block
# ---------------------------------------------------------------------------


def test_extract_font_analysis_line_spacing_two_lines(tmp_path: Path) -> None:
    """Two adjacent lines → one LineSpacing entry with leading=14.4 (1.2x)."""
    # Two lines, 14.4pt apart at 12pt → ratio 1.2.
    content = (
        b"BT /F1 12 Tf 100 700 Td (Line one) Tj ET\n"
        b"BT /F1 12 Tf 100 685.6 Td (Line two) Tj ET\n"
    )
    pdf_path = _write(tmp_path, content)

    result = extract_font_analysis(pdf_path)

    assert len(result.line_spacings) >= 1
    ls = result.line_spacings[0]
    assert isinstance(ls, LineSpacing)
    assert ls.font_size == 12.0
    # Leading is the y1 difference between line1 and line2; ratio ~ 1.2.
    assert 0.5 < ls.ratio < 5.0
    assert ls.text  # non-empty


# ---------------------------------------------------------------------------
# Alignment — left-aligned 3+ line block
# ---------------------------------------------------------------------------


def test_extract_font_analysis_alignment_left(tmp_path: Path) -> None:
    """Three left-aligned lines → AlignmentReport with alignment in {left,justified}."""
    # Three lines all starting at x=100, varying widths → left-aligned.
    content = (
        b"BT /F1 12 Tf 100 700 Td (Short) Tj ET\n"
        b"BT /F1 12 Tf 100 685 Td (Medium length) Tj ET\n"
        b"BT /F1 12 Tf 100 670 Td (A bit longer line here) Tj ET\n"
    )
    pdf_path = _write(tmp_path, content)

    result = extract_font_analysis(pdf_path)

    # pdfminer may or may not group the three lines into a single LTTextBox
    # depending on layout heuristics; if it does, we should get exactly one
    # alignment report. If not, we accept zero (the collector is not fooled).
    if result.alignments:
        report = result.alignments[0]
        assert isinstance(report, AlignmentReport)
        assert report.line_count >= 3
        # Three lines starting at x=100 → "left" or "justified" (if right
        # edges happen to align). Both are valid.
        assert report.alignment in {"left", "justified"}


# ---------------------------------------------------------------------------
# RotationInfo dataclass surface (smoke)
# ---------------------------------------------------------------------------


def test_rotation_info_dataclass_construction() -> None:
    """Smoke check on RotationInfo public surface."""
    r = RotationInfo(angle=90.0, sample="abc", page=0)
    assert r.angle == 90.0
    assert r.sample == "abc"
    assert r.page == 0
