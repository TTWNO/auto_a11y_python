"""Tests for ``auto_a11y.pdf.audit.font_metadata``.

Builds synthetic PDF font resources with pikepdf and checks that
:func:`extract_font_metadata` walks them into the expected
:class:`FontInfoDetail` shapes.

Cases exercised:

* No fonts on the page → empty :class:`FontMetadata`.
* Simple Type1 font with ``/WinAnsiEncoding`` → name-typed encoding,
  no symbolic flag, no ToUnicode.
* TrueType font with ``/FontDescriptor`` ``/Flags`` set to symbolic.
* Encoding dictionary with a ``/Differences`` array containing
  ``/.notdef``.
* ToUnicode CMap parsed by
  :func:`auto_a11y.pdf.audit.content_streams.parse_tounicode_mapping`.
* ToUnicode CMap with an invalid Unicode mapping (U+0000 / U+FEFF).
* Type0 font with descendant CIDFontType2 + Identity-H CMap.
* De-duplication: the same font referenced from two pages still yields
  a single :class:`FontInfoDetail`.
"""
from __future__ import annotations

from typing import Protocol

import pikepdf

from auto_a11y.pdf.audit.font_metadata import (
    FontInfoDetail,
    extract_font_metadata,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StreamMaker(Protocol):
    """Typed view of ``pikepdf.Pdf.make_stream`` for single-arg calls."""

    def __call__(self, data: bytes) -> pikepdf.Stream: ...


def _typed_make_stream(pdf: pikepdf.Pdf, data: bytes) -> pikepdf.Stream:
    """Strictly-typed wrapper around ``Pdf.make_stream``."""
    maker: _StreamMaker = getattr(pdf, "make_stream")
    return maker(data)


def _new_pdf_with_pages(num_pages: int = 1) -> pikepdf.Pdf:
    """Return a fresh ``pikepdf.Pdf`` with ``num_pages`` blank pages."""
    pdf = pikepdf.Pdf.new()
    for _ in range(num_pages):
        pdf.add_blank_page(page_size=(612, 792))
    return pdf


def _attach_font(
    pdf: pikepdf.Pdf, page_index: int, *,
    font_key: str, font_dict: dict[str, pikepdf.Object],
) -> None:
    """Attach ``font_dict`` to ``page.Resources.Font[font_key]``."""
    page = pdf.pages[page_index]
    fonts = pikepdf.Dictionary({font_key: pikepdf.Dictionary(font_dict)})
    page.Resources = pikepdf.Dictionary(Font=fonts)


def _font_by(fonts: list[FontInfoDetail], base_font: str) -> FontInfoDetail:
    """Return the FontInfoDetail whose ``base_font`` matches."""
    for f in fonts:
        if f.base_font == base_font:
            return f
    raise AssertionError(f"font {base_font!r} not found in {fonts}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_extract_font_metadata_no_fonts_returns_empty() -> None:
    pdf = _new_pdf_with_pages(1)
    fm = extract_font_metadata(pdf)
    assert fm.fonts == []


def test_extract_font_metadata_simple_type1_font() -> None:
    pdf = _new_pdf_with_pages(1)
    _attach_font(
        pdf, 0,
        font_key="/F1",
        font_dict={
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/Type1"),
            "/BaseFont": pikepdf.Name("/Helvetica"),
            "/Encoding": pikepdf.Name("/WinAnsiEncoding"),
        },
    )
    fm = extract_font_metadata(pdf)
    assert len(fm.fonts) == 1
    f = fm.fonts[0]
    assert f.base_font == "/Helvetica"
    assert f.subtype == "/Type1"
    assert f.encoding_kind == "name"
    assert f.encoding_name == "/WinAnsiEncoding"
    assert f.is_symbolic is False
    assert f.has_to_unicode is False
    assert f.encoding_differences is None
    assert f.is_type0 is False
    assert f.is_cid_type2 is False


def test_extract_font_metadata_symbolic_truetype() -> None:
    pdf = _new_pdf_with_pages(1)
    descriptor = pikepdf.Dictionary({
        "/Type": pikepdf.Name("/FontDescriptor"),
        "/FontName": pikepdf.Name("/SymFont"),
        "/Flags": 4,  # bit 3 set → symbolic
    })
    _attach_font(
        pdf, 0,
        font_key="/F1",
        font_dict={
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/TrueType"),
            "/BaseFont": pikepdf.Name("/SymFont"),
            "/FontDescriptor": descriptor,
        },
    )
    fm = extract_font_metadata(pdf)
    f = _font_by(fm.fonts, "/SymFont")
    assert f.is_symbolic is True


def test_extract_font_metadata_differences_with_notdef() -> None:
    pdf = _new_pdf_with_pages(1)
    encoding = pikepdf.Dictionary({
        "/Type": pikepdf.Name("/Encoding"),
        "/BaseEncoding": pikepdf.Name("/WinAnsiEncoding"),
        "/Differences": pikepdf.Array([
            32, pikepdf.Name("/space"), pikepdf.Name("/.notdef"),
        ]),
    })
    _attach_font(
        pdf, 0,
        font_key="/F1",
        font_dict={
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/Type1"),
            "/BaseFont": pikepdf.Name("/MyFont"),
            "/Encoding": encoding,
        },
    )
    fm = extract_font_metadata(pdf)
    f = _font_by(fm.fonts, "/MyFont")
    assert f.encoding_differences is not None
    assert f.encoding_differences.has_notdef is True
    assert f.encoding_differences.differences == [(32, "space"), (33, ".notdef")]


def test_extract_font_metadata_with_tounicode() -> None:
    pdf = _new_pdf_with_pages(1)
    cmap_text = (
        b"/CIDInit /ProcSet findresource begin\n"
        b"12 dict begin\n"
        b"begincmap\n"
        b"1 begincodespacerange\n"
        b"<00> <FF>\n"
        b"endcodespacerange\n"
        b"1 beginbfchar\n"
        b"<41> <0041>\n"
        b"endbfchar\n"
        b"endcmap\n"
        b"end\n"
    )
    tounicode_stream = _typed_make_stream(pdf, cmap_text)
    _attach_font(
        pdf, 0,
        font_key="/F1",
        font_dict={
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/Type1"),
            "/BaseFont": pikepdf.Name("/CleanFont"),
            "/ToUnicode": tounicode_stream,
        },
    )
    fm = extract_font_metadata(pdf)
    f = _font_by(fm.fonts, "/CleanFont")
    assert f.has_to_unicode is True
    assert f.to_unicode is not None
    assert f.to_unicode.mapping == {0x41: "A"}
    assert f.to_unicode.has_invalid_unicode is False


def test_extract_font_metadata_tounicode_with_invalid_codepoint() -> None:
    pdf = _new_pdf_with_pages(1)
    cmap_text = (
        b"/CIDInit /ProcSet findresource begin\n"
        b"12 dict begin\n"
        b"begincmap\n"
        b"1 begincodespacerange\n"
        b"<00> <FF>\n"
        b"endcodespacerange\n"
        b"1 beginbfchar\n"
        b"<41> <0000>\n"
        b"endbfchar\n"
        b"endcmap\n"
        b"end\n"
    )
    tounicode_stream = _typed_make_stream(pdf, cmap_text)
    _attach_font(
        pdf, 0,
        font_key="/F1",
        font_dict={
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/Type1"),
            "/BaseFont": pikepdf.Name("/BadFont"),
            "/ToUnicode": tounicode_stream,
        },
    )
    fm = extract_font_metadata(pdf)
    f = _font_by(fm.fonts, "/BadFont")
    assert f.to_unicode is not None
    assert f.to_unicode.has_invalid_unicode is True
    assert 0x0000 in f.to_unicode.invalid_codepoints


def test_extract_font_metadata_type0_with_identity_h() -> None:
    pdf = _new_pdf_with_pages(1)
    cid_descriptor = pikepdf.Dictionary({
        "/Type": pikepdf.Name("/FontDescriptor"),
        "/FontName": pikepdf.Name("/CJKFont"),
        "/Flags": 4,
        "/FontFile2": _typed_make_stream(pdf, b"\x00" * 64),
    })
    cid_font = pikepdf.Dictionary({
        "/Type": pikepdf.Name("/Font"),
        "/Subtype": pikepdf.Name("/CIDFontType2"),
        "/BaseFont": pikepdf.Name("/CJKFont"),
        "/CIDSystemInfo": pikepdf.Dictionary({
            "/Registry": pikepdf.String("Adobe"),
            "/Ordering": pikepdf.String("Identity"),
            "/Supplement": 0,
        }),
        "/CIDToGIDMap": pikepdf.Name("/Identity"),
        "/FontDescriptor": cid_descriptor,
        "/W": pikepdf.Array([0, pikepdf.Array([500, 500, 500])]),
    })
    _attach_font(
        pdf, 0,
        font_key="/F1",
        font_dict={
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/Type0"),
            "/BaseFont": pikepdf.Name("/CJKFont"),
            "/Encoding": pikepdf.Name("/Identity-H"),
            "/DescendantFonts": pikepdf.Array([cid_font]),
        },
    )
    fm = extract_font_metadata(pdf)
    f = _font_by(fm.fonts, "/CJKFont")
    assert f.is_type0 is True
    assert f.has_identity_h_or_v is True
    assert f.is_cid_type2 is True
    assert f.cidtogidmap == "Identity"
    assert f.has_cid_font_file is True
    assert f.has_font_file is True
    assert f.cmap_name == "Identity-H"
    assert f.cmap_wmode == 0  # -H suffix → horizontal


def test_extract_font_metadata_dedupes_across_pages() -> None:
    """A font referenced from two pages produces a single FontInfoDetail."""
    pdf = _new_pdf_with_pages(2)
    for page_index in (0, 1):
        _attach_font(
            pdf, page_index,
            font_key="/F1",
            font_dict={
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
                "/Encoding": pikepdf.Name("/WinAnsiEncoding"),
            },
        )
    fm = extract_font_metadata(pdf)
    helvetica = [f for f in fm.fonts if f.base_font == "/Helvetica"]
    assert len(helvetica) == 1
    assert helvetica[0].page == 1
