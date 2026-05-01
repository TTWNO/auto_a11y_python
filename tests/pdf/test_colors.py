"""Tests for the PDF text + form-field colour extractors.

Strategy A: build small synthetic PDFs in-process via pikepdf
(``Pdf.new()`` + ``add_blank_page`` + a hand-rolled content stream that
references a standard Type1 font). pdfminer.six reads these back and
emits ``LTChar`` instances whose ``graphicstate.ncolor`` carries the
fill colour set by ``rg`` / ``g`` / ``k`` operators — sufficient to
exercise every branch of ``extract_text_colors``.

Form-field tests build minimal AcroForm widgets (also via pikepdf) with
explicit ``/DA`` / ``/MK`` / ``/Rect`` entries.

Ghostscript is required for the background-sampling integration tests
(see :func:`test_extract_text_colors_simple_black_text`); they
``pytest.skip`` when ``gs`` isn't on PATH.
"""
from __future__ import annotations

import io
import shutil
from pathlib import Path
from typing import Protocol

import pikepdf
import pytest
from PIL import Image

from auto_a11y.pdf.audit.colors import (
    ApTextColorEntry,
    CharEntry,
    ColorPairInfo,
    FgOnlyColorInfo,
    FormColorPairInfo,
    RgbColor,
    cmyk_to_rgb,
    collect_chars_with_positions,
    extract_form_field_colors,
    extract_text_colors,
    ncolor_to_rgb,
    parse_ap_stream_colors,
    parse_color_array,
    parse_da_string,
    quantize_rgb,
    sample_background,
)


# ---------------------------------------------------------------------------
# Synthetic PDF helpers
# ---------------------------------------------------------------------------


class _StreamMaker(Protocol):
    """Typed view of ``pikepdf.Pdf.make_stream`` for single-arg calls."""

    def __call__(self, data: bytes) -> pikepdf.Stream: ...


def _typed_make_stream(pdf: pikepdf.Pdf, data: bytes) -> pikepdf.Stream:
    """Strictly-typed wrapper around ``Pdf.make_stream``.

    Mirrors the helper in ``test_content_streams.py``: the pikepdf stub
    declares ``Pdf.make_stream(d=None, **kwargs)`` without param types,
    so we route through a Protocol to narrow.
    """
    maker: _StreamMaker = getattr(pdf, "make_stream")
    return maker(data)


def _helvetica_font() -> pikepdf.Dictionary:
    """Standard Helvetica Type1 font — pdfminer renders this without a CMap."""
    return pikepdf.Dictionary(
        Type=pikepdf.Name("/Font"),
        Subtype=pikepdf.Name("/Type1"),
        BaseFont=pikepdf.Name("/Helvetica"),
        Encoding=pikepdf.Name("/WinAnsiEncoding"),
    )


def _make_text_pdf(content: bytes, page_size: tuple[float, float] = (612, 792)) -> bytes:
    """Build an in-memory PDF whose only page draws ``content`` with /F1 = Helvetica."""
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=page_size)
    page.Resources = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(F1=_helvetica_font())
    )
    page.Contents = _typed_make_stream(pdf, content)
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def _write_pdf(tmp_path: Path, content: bytes, name: str = "doc.pdf") -> Path:
    """Materialise a synthetic PDF to disk so pdfminer can open it by path."""
    out = tmp_path / name
    out.write_bytes(_make_text_pdf(content))
    return out


# ---------------------------------------------------------------------------
# Pure-helper tests (no I/O, no Ghostscript)
# ---------------------------------------------------------------------------


def test_quantize_rgb_snaps_to_fiftieths() -> None:
    # 0.234 * 50 = 11.7 → round = 12 → 12/50 = 0.24
    assert quantize_rgb((0.234, 0.0, 1.0)) == (0.24, 0.0, 1.0)


def test_cmyk_to_rgb_zero_k_pure_cyan() -> None:
    # C=1, M=0, Y=0, K=0 → R=0, G=1, B=1 (pure cyan)
    assert cmyk_to_rgb(1.0, 0.0, 0.0, 0.0) == (0.0, 1.0, 1.0)


def test_cmyk_to_rgb_full_k_black() -> None:
    assert cmyk_to_rgb(0.0, 0.0, 0.0, 1.0) == (0.0, 0.0, 0.0)


def test_ncolor_to_rgb_grayscale() -> None:
    assert ncolor_to_rgb(0.5) == (0.5, 0.5, 0.5)


def test_ncolor_to_rgb_rgb_tuple() -> None:
    assert ncolor_to_rgb((0.1, 0.2, 0.3)) == (0.1, 0.2, 0.3)


def test_ncolor_to_rgb_cmyk_tuple() -> None:
    # M=1, all else 0 → green channel = 0, R = B = 1
    assert ncolor_to_rgb((0.0, 1.0, 0.0, 0.0)) == (1.0, 0.0, 1.0)


def test_ncolor_to_rgb_none_passes_through() -> None:
    assert ncolor_to_rgb(None) is None


def test_parse_da_string_rgb_and_size() -> None:
    fg, size = parse_da_string("0.2 0.4 0.6 rg /Helv 14 Tf")
    assert fg == (0.2, 0.4, 0.6)
    assert size == 14.0


def test_parse_da_string_grayscale() -> None:
    fg, size = parse_da_string("0.5 g /Helv 10 Tf")
    assert fg == (0.5, 0.5, 0.5)
    assert size == 10.0


def test_parse_da_string_cmyk() -> None:
    # C=M=Y=0, K=1 → black
    fg, _ = parse_da_string("0 0 0 1 k")
    assert fg == (0.0, 0.0, 0.0)


def test_parse_da_string_empty() -> None:
    assert parse_da_string("") == (None, None)


def test_parse_color_array_grayscale() -> None:
    arr = pikepdf.Array([0.4])
    assert parse_color_array(arr) == (0.4, 0.4, 0.4)


def test_parse_color_array_rgb() -> None:
    arr = pikepdf.Array([0.1, 0.2, 0.3])
    assert parse_color_array(arr) == (0.1, 0.2, 0.3)


def test_parse_color_array_cmyk_passthrough() -> None:
    arr = pikepdf.Array([0.0, 0.0, 0.0, 1.0])
    assert parse_color_array(arr) == (0.0, 0.0, 0.0)


def test_parse_color_array_none() -> None:
    assert parse_color_array(None) is None


def test_parse_color_array_wrong_length() -> None:
    assert parse_color_array(pikepdf.Array([0.1, 0.2])) is None


# ---------------------------------------------------------------------------
# sample_background
# ---------------------------------------------------------------------------


def _rgb_close(a: tuple[float, float, float], b: tuple[float, float, float], tol: float = 1e-6) -> bool:
    """Tuple-of-floats comparison with absolute tolerance."""
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def test_sample_background_uniform_color() -> None:
    """Uniform-coloured image → median sample matches the fill colour."""
    img = Image.new("RGB", (100, 100), (200, 100, 50))
    rgb = sample_background(
        img, img.width, img.height, page_height=100.0,
        x0=10.0, y0=20.0, x1=20.0, y1=30.0,
    )
    # Expected: median of 3x3 sample of (200/255, 100/255, 50/255).
    expected = (200 / 255.0, 100 / 255.0, 50 / 255.0)
    assert _rgb_close(rgb, expected)


def test_sample_background_clamps_at_image_bounds() -> None:
    """Sampling near the image edge is clamped, never raises."""
    img = Image.new("RGB", (10, 10), (255, 255, 255))
    rgb = sample_background(
        img, img.width, img.height, page_height=10.0,
        x0=9.0, y0=9.0, x1=10.0, y1=10.0,
    )
    assert _rgb_close(rgb, (1.0, 1.0, 1.0))


# ---------------------------------------------------------------------------
# collect_chars_with_positions on an LT tree
# ---------------------------------------------------------------------------


def test_collect_chars_picks_up_ltchar_with_grayscale_color(tmp_path: Path) -> None:
    """A black-text PDF → one CharEntry per glyph."""
    # 0 0 0 rg → black RGB; "Hi" → 2 chars
    content = b"BT /F1 12 Tf 0 0 0 rg 100 700 Td (Hi) Tj ET\n"
    pdf_path = _write_pdf(tmp_path, content)

    from pdfminer.high_level import extract_pages
    chars: list[CharEntry] = []
    for page_layout in extract_pages(pdf_path):
        collect_chars_with_positions(page_layout, chars)

    assert len(chars) == 2
    assert {c.char for c in chars} == {"H", "i"}
    assert all(c.fg == (0.0, 0.0, 0.0) for c in chars)
    assert all(c.size == 12.0 for c in chars)


# ---------------------------------------------------------------------------
# extract_text_colors integration tests
# ---------------------------------------------------------------------------


_GS_AVAILABLE = shutil.which("gs") is not None


def test_extract_text_colors_empty_pdf(tmp_path: Path) -> None:
    """No-text PDF → returns ({}, {})."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    out = tmp_path / "blank.pdf"
    pdf.save(out)
    color_pairs, fg_only = extract_text_colors(out)
    assert color_pairs == {}
    assert fg_only == {}


@pytest.mark.skipif(not _GS_AVAILABLE, reason="Ghostscript not on PATH")
def test_extract_text_colors_simple_black_text(tmp_path: Path) -> None:
    """Black text on white page → one fg-only entry, one pair."""
    content = b"BT /F1 12 Tf 0 0 0 rg 100 700 Td (Hello) Tj ET\n"
    pdf_path = _write_pdf(tmp_path, content)

    color_pairs, fg_only = extract_text_colors(pdf_path)

    # Foreground-only: black, 5 chars.
    assert (0.0, 0.0, 0.0) in fg_only
    info = fg_only[(0.0, 0.0, 0.0)]
    assert info.count == 5
    assert "Hello" in info.sample
    assert info.size_min == 12.0
    assert info.size_max == 12.0

    # Color pairs: black on near-white. Tolerate quantisation noise from
    # the Ghostscript raster (snap to 0.02 means anything in [0.98, 1.0]
    # round to 1.0).
    assert len(color_pairs) >= 1
    pair_keys = list(color_pairs.keys())
    fg, bg = pair_keys[0]
    assert fg == (0.0, 0.0, 0.0)
    # bg should be near-white
    assert all(c >= 0.96 for c in bg)


@pytest.mark.skipif(not _GS_AVAILABLE, reason="Ghostscript not on PATH")
def test_extract_text_colors_two_colors(tmp_path: Path) -> None:
    """Two distinct fg colours → two fg-only entries."""
    # First 'A' in pure red, then 'B' in pure blue.
    content = (
        b"BT /F1 12 Tf 1 0 0 rg 100 700 Td (A) Tj ET\n"
        b"BT /F1 12 Tf 0 0 1 rg 200 700 Td (B) Tj ET\n"
    )
    pdf_path = _write_pdf(tmp_path, content)
    color_pairs, fg_only = extract_text_colors(pdf_path)

    assert (1.0, 0.0, 0.0) in fg_only
    assert (0.0, 0.0, 1.0) in fg_only
    assert fg_only[(1.0, 0.0, 0.0)].count == 1
    assert fg_only[(0.0, 0.0, 1.0)].count == 1
    # At least one pair per fg color.
    fgs_seen = {fg for fg, _bg in color_pairs.keys()}
    assert (1.0, 0.0, 0.0) in fgs_seen
    assert (0.0, 0.0, 1.0) in fgs_seen


def test_extract_text_colors_corrupt_pdf_returns_empty(tmp_path: Path) -> None:
    """Garbage-byte input → ({}, {}) without raising."""
    bad = tmp_path / "junk.pdf"
    bad.write_bytes(b"not a pdf")
    color_pairs, fg_only = extract_text_colors(bad)
    assert color_pairs == {}
    assert fg_only == {}


# ---------------------------------------------------------------------------
# parse_ap_stream_colors
# ---------------------------------------------------------------------------


def test_parse_ap_stream_colors_records_distinct_colours() -> None:
    """An AP stream with two text-show ops in different colours → two entries."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(100, 100))
    # Two text-show operations with two different fill colours.
    content = (
        b"BT 1 0 0 rg /F1 14 Tf (red) Tj ET\n"
        b"BT 0 1 0 rg /F1 10 Tf (green) Tj ET\n"
    )
    stream = _typed_make_stream(pdf, content)
    entries = parse_ap_stream_colors(stream)

    fgs = {e.fg for e in entries}
    assert (1.0, 0.0, 0.0) in fgs
    assert (0.0, 1.0, 0.0) in fgs
    sizes = {e.font_size for e in entries}
    assert 14.0 in sizes
    assert 10.0 in sizes


def test_parse_ap_stream_colors_dedupes_same_colour() -> None:
    """Two Tj ops with the same fill colour → one entry only."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(100, 100))
    content = (
        b"BT 0 0 0 rg /F1 12 Tf (one) Tj ET\n"
        b"BT 0 0 0 rg /F1 12 Tf (two) Tj ET\n"
    )
    stream = _typed_make_stream(pdf, content)
    entries = parse_ap_stream_colors(stream)
    assert len(entries) == 1
    assert entries[0].fg == (0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# extract_form_field_colors
# ---------------------------------------------------------------------------


def _build_form_pdf(
    *,
    da: str | None = "0 0 0 rg /Helv 12 Tf",
    mk_bg: pikepdf.Array | None = None,
) -> pikepdf.Pdf:
    """Build a single-page PDF with one AcroForm /Tx widget."""
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))

    # pikepdf.Dictionary accepts a Mapping; we build the field dict via
    # a plain str→Object mapping with leading-slash keys (the pikepdf
    # constructor accepts that shape directly).
    field_dict = pikepdf.Dictionary({
        "/Type": pikepdf.Name("/Annot"),
        "/Subtype": pikepdf.Name("/Widget"),
        "/FT": pikepdf.Name("/Tx"),
        "/T": pikepdf.String("widget1"),
        "/Rect": pikepdf.Array([100, 700, 300, 720]),
        "/P": page.obj,
    })
    if da is not None:
        field_dict["/DA"] = pikepdf.String(da)
    if mk_bg is not None:
        field_dict["/MK"] = pikepdf.Dictionary({"/BG": mk_bg})

    field_ref = pdf.make_indirect(field_dict)
    page.obj["/Annots"] = pikepdf.Array([field_ref])
    pdf.Root["/AcroForm"] = pdf.make_indirect(
        pikepdf.Dictionary({"/Fields": pikepdf.Array([field_ref])})
    )
    return pdf


def test_extract_form_field_colors_no_acroform(tmp_path: Path) -> None:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    out = tmp_path / "noform.pdf"
    pdf.save(out)
    pdf2 = pikepdf.Pdf.open(out)
    assert extract_form_field_colors(pdf2, out) == {}


def test_extract_form_field_colors_da_with_explicit_bg(tmp_path: Path) -> None:
    """/DA red + /MK /BG yellow → one pair (red, yellow)."""
    pdf = _build_form_pdf(
        da="1 0 0 rg /Helv 14 Tf",
        mk_bg=pikepdf.Array([1.0, 1.0, 0.0]),
    )
    out = tmp_path / "form.pdf"
    pdf.save(out)

    pdf2 = pikepdf.Pdf.open(out)
    pairs = extract_form_field_colors(pdf2, out)
    assert len(pairs) == 1
    pair_key = next(iter(pairs.keys()))
    fg, bg = pair_key
    assert fg == (1.0, 0.0, 0.0)
    assert bg == (1.0, 1.0, 0.0)
    info = pairs[pair_key]
    assert info.count == 1
    assert info.size_min == 14.0
    assert info.size_max == 14.0
    assert info.is_form is True
    assert info.pages == {1}


def test_extract_form_field_colors_no_da_no_ap_skipped(tmp_path: Path) -> None:
    """Field with neither /DA nor parseable /AP → skipped, returns empty."""
    pdf = _build_form_pdf(da=None, mk_bg=None)
    out = tmp_path / "noda.pdf"
    pdf.save(out)
    pdf2 = pikepdf.Pdf.open(out)
    assert extract_form_field_colors(pdf2, out) == {}


def test_extract_form_field_colors_acroform_da_fallback(tmp_path: Path) -> None:
    """Field has no /DA but AcroForm-level /DA supplies one → that colour wins."""
    pdf = _build_form_pdf(
        da=None,
        mk_bg=pikepdf.Array([1.0, 1.0, 1.0]),
    )
    # Inject AcroForm-level /DA
    acroform = pdf.Root["/AcroForm"]
    acroform["/DA"] = pikepdf.String("0 0.5 0 rg /Helv 11 Tf")
    out = tmp_path / "acroda.pdf"
    pdf.save(out)
    pdf2 = pikepdf.Pdf.open(out)
    pairs = extract_form_field_colors(pdf2, out)

    assert len(pairs) == 1
    fg, _bg = next(iter(pairs.keys()))
    assert fg == (0.0, 0.5, 0.0)


def test_extract_form_field_colors_dataclass_shape() -> None:
    """Sanity-check the dataclass surface for downstream consumers."""
    info = FormColorPairInfo()
    assert info.count == 0
    assert info.is_form is True
    info2 = ColorPairInfo()
    assert isinstance(info2.pages, set)
    info3 = FgOnlyColorInfo()
    assert info3.size_min == 999.0


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


def test_rgb_color_alias_accepts_three_floats() -> None:
    """Quick sanity check that the public type alias is the expected shape."""
    rgb: RgbColor = (0.1, 0.2, 0.3)
    assert rgb == (0.1, 0.2, 0.3)


def test_ap_text_color_entry_dataclass_shape() -> None:
    """Internal dataclass exposed for tests; verify default-less construction."""
    entry = ApTextColorEntry(fg=(0.0, 0.0, 0.0), font_size=12.0, snippet="hi")
    assert entry.snippet == "hi"
