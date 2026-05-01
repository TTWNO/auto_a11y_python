"""Tests for the content-stream MCID → text extractor.

Strategy A: hand-build content streams and ToUnicode CMaps via pikepdf's
``Pdf.new()`` + ``make_stream()``. ``parse_content_stream`` happily reads
back our hand-rolled byte sequences — no /Font resource is required for
the parser itself, only for unicode decoding. We attach minimal /Font
dictionaries (with optional /ToUnicode streams) when the test exercises
font-dependent decoding.
"""
from __future__ import annotations

from typing import Protocol

import pikepdf

from auto_a11y.pdf.audit.content_streams import (
    McidTextMap,
    decode_pdf_string,
    decode_with_font,
    parse_tounicode_mapping,
    extract_mcid_text_map_from_content_streams,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StreamMaker(Protocol):
    """Typed view of ``pikepdf.Pdf.make_stream`` restricted to one argument.

    The pikepdf stub for ``Pdf.make_stream`` declares ``d=None, **kwargs``
    without annotations, which leaks ``Unknown`` under pyright strict at
    every call site. We only ever construct streams from raw bytes, so
    we narrow the surface to a single overload via this Protocol — the
    call site assigns the bound method to a Protocol variable and
    pyright then sees a fully-typed callable.
    """

    def __call__(self, data: bytes) -> pikepdf.Stream: ...


def _typed_make_stream(pdf: pikepdf.Pdf, data: bytes) -> pikepdf.Stream:
    """Strictly-typed wrapper around ``Pdf.make_stream``.

    See :class:`_StreamMaker` for why we route through a Protocol. The
    ``getattr`` indirection forces pyright to read the attribute via
    ``object``-typed lookup rather than through the partially-Unknown
    method signature, then the Protocol assignment narrows back to the
    single-argument shape we actually want.
    """
    maker: _StreamMaker = getattr(pdf, "make_stream")
    return maker(data)


def _make_pdf_with_content(
    content: bytes,
    *,
    font_resource: pikepdf.Dictionary | None = None,
) -> pikepdf.Pdf:
    """Build a one-page PDF with the given content stream bytes.

    If ``font_resource`` is supplied it's attached to the page's
    ``/Resources/Font/F1`` so text-show operators that name ``/F1`` can
    resolve a /ToUnicode mapping.
    """
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Contents = _typed_make_stream(pdf, content)
    if font_resource is not None:
        page.Resources = pikepdf.Dictionary(
            Font=pikepdf.Dictionary(F1=font_resource),
        )
    return pdf


def _make_minimal_font(tounicode_stream: pikepdf.Stream | None = None) -> pikepdf.Dictionary:
    """Build a minimal /Font dict; attach /ToUnicode if provided."""
    font = pikepdf.Dictionary(
        Type=pikepdf.Name("/Font"),
        Subtype=pikepdf.Name("/Type1"),
        BaseFont=pikepdf.Name("/Helvetica"),
    )
    if tounicode_stream is not None:
        font[pikepdf.Name("/ToUnicode")] = tounicode_stream
    return font


def _make_tounicode_stream(pdf: pikepdf.Pdf, body: bytes) -> pikepdf.Stream:
    """Wrap a CMap body into a Stream attached to ``pdf``."""
    return _typed_make_stream(pdf, body)


# Hand-rolled CMap body: 1-byte codespace, two bfchar entries.
_CMAP_BFCHAR_1BYTE = b"""/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CMapName /Adobe-Identity-UCS def
/CMapType 2 def
1 begincodespacerange
<00> <FF>
endcodespacerange
2 beginbfchar
<41> <0048>
<42> <0069>
endbfchar
endcmap
CMapName currentdict /CMap defineresource pop
end
end
"""

# Hand-rolled CMap body: 2-byte codespace, one bfchar.
_CMAP_BFCHAR_2BYTE = b"""/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CMapName /Adobe-Identity-UCS def
/CMapType 2 def
1 begincodespacerange
<0000> <FFFF>
endcodespacerange
1 beginbfchar
<0041> <0048>
endbfchar
endcmap
CMapName currentdict /CMap defineresource pop
end
end
"""

# Hand-rolled CMap body: bfrange simple form.
_CMAP_BFRANGE_SIMPLE = b"""/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
1 begincodespacerange
<00> <FF>
endcodespacerange
1 beginbfrange
<41> <43> <0048>
endbfrange
endcmap
end
end
"""

# Hand-rolled CMap body: bfrange array form.
_CMAP_BFRANGE_ARRAY = b"""/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
1 begincodespacerange
<00> <FF>
endcodespacerange
1 beginbfrange
<41> <43> [<0048> <0065> <006C>]
endbfrange
endcmap
end
end
"""


# ---------------------------------------------------------------------------
# extract_mcid_text_map_from_content_streams — basic shapes
# ---------------------------------------------------------------------------


def test_empty_pdf_returns_empty_map() -> None:
    pdf = pikepdf.Pdf.new()
    # No pages added.
    result = extract_mcid_text_map_from_content_streams(pdf)
    assert result == {}


def test_single_page_single_mcid() -> None:
    cs = (
        b"/P <</MCID 0>> BDC\n"
        b"BT\n"
        b"/F1 12 Tf\n"
        b"50 700 Td\n"
        b"(Hello) Tj\n"
        b"ET\n"
        b"EMC\n"
    )
    pdf = _make_pdf_with_content(cs)
    result = extract_mcid_text_map_from_content_streams(pdf)
    assert result == {0: {0: "Hello"}}


def test_multiple_mcids_on_one_page() -> None:
    cs = (
        b"/P <</MCID 0>> BDC\n"
        b"BT /F1 12 Tf 50 700 Td (Alpha) Tj ET\n"
        b"EMC\n"
        b"/P <</MCID 1>> BDC\n"
        b"BT /F1 12 Tf 50 680 Td (Beta) Tj ET\n"
        b"EMC\n"
    )
    pdf = _make_pdf_with_content(cs)
    result = extract_mcid_text_map_from_content_streams(pdf)
    assert result == {0: {0: "Alpha", 1: "Beta"}}


def test_corrupt_content_stream_is_graceful() -> None:
    """A page whose content stream fails to parse yields {} for that page."""
    # An open BT with no matching ET, intermixed garbage, etc., is one
    # way to provoke parse failure — but pikepdf's parser is lenient.
    # Inject a stream that's pure junk binary data so parse_content_stream
    # raises PdfError. We bypass make_stream's tokenisation by writing
    # raw bytes that are syntactically invalid PDF content stream.
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Contents = _typed_make_stream(pdf, b"\x00\x01\x02\xff\xfe Q Q Q Q Q Q Q Q")
    result = extract_mcid_text_map_from_content_streams(pdf)
    # Either empty (parse failed) or contains page 0 (parse succeeded
    # but no MCID was found). Either is acceptable; what matters is
    # that no exception propagates.
    assert 0 in result
    assert result[0] == {}


def test_nested_bdc_emc_attribution() -> None:
    """Nested BDC inside another BDC: stack-tracked attribution.

    pdfMax's stack handling has a documented quirk: when an outer BDC
    fires its EMC, the inner ``current_text_parts`` was already flushed
    to ``page_mcids`` (at the inner EMC). The outer mcid only ever gets
    text drawn between the inner EMC and the outer EMC — pdfMax wipes
    ``current_text_parts`` to ``[]`` on every BDC that introduces a new
    MCID. So "Outer" (drawn before the inner BDC) is *never* recorded:
    it's wiped when the inner BDC pushes mcid 1.

    This test pins that upstream behaviour so we don't drift from it.
    """
    cs = (
        b"/P <</MCID 0>> BDC\n"
        b"BT /F1 12 Tf 50 700 Td (Outer) Tj ET\n"
        b"/Span <</MCID 1>> BDC\n"
        b"BT /F1 12 Tf 50 680 Td (Inner) Tj ET\n"
        b"EMC\n"
        b"BT /F1 12 Tf 50 660 Td (More) Tj ET\n"
        b"EMC\n"
    )
    pdf = _make_pdf_with_content(cs)
    result = extract_mcid_text_map_from_content_streams(pdf)
    # Inner mcid captures its own text.
    assert result[0][1] == "Inner"
    # Outer mcid only gets text drawn after the inner EMC; "Outer" is
    # lost by the upstream wipe-on-BDC behaviour.
    assert result[0][0] == "More"


def test_text_outside_marked_content_is_ignored() -> None:
    cs = (
        b"BT /F1 12 Tf 50 700 Td (Outside) Tj ET\n"
        b"/P <</MCID 0>> BDC\n"
        b"BT /F1 12 Tf 50 680 Td (Inside) Tj ET\n"
        b"EMC\n"
    )
    pdf = _make_pdf_with_content(cs)
    result = extract_mcid_text_map_from_content_streams(pdf)
    assert result == {0: {0: "Inside"}}


def test_tj_array_with_strings_and_spacing() -> None:
    cs = (
        b"/P <</MCID 0>> BDC\n"
        b"BT /F1 12 Tf 50 700 Td "
        b"[(Hello) -200 (World)] TJ "
        b"ET\n"
        b"EMC\n"
    )
    pdf = _make_pdf_with_content(cs)
    result = extract_mcid_text_map_from_content_streams(pdf)
    # -200 < -100 → spacer space inserted between Hello and World.
    assert result == {0: {0: "Hello World"}}


def test_tj_array_small_negative_no_space() -> None:
    cs = (
        b"/P <</MCID 0>> BDC\n"
        b"BT /F1 12 Tf 50 700 Td "
        b"[(Hello) -50 (World)] TJ "
        b"ET\n"
        b"EMC\n"
    )
    pdf = _make_pdf_with_content(cs)
    result = extract_mcid_text_map_from_content_streams(pdf)
    # -50 not < -100 → no spacer.
    assert result == {0: {0: "HelloWorld"}}


def test_bmc_pushes_mcid_stack_without_changing_current() -> None:
    """A BMC (no /MCID) inside a BDC preserves the outer MCID.

    pdfMax flushes ``current_text_parts`` on every EMC where
    ``current_mcid is not None``, then *doesn't* clear the parts list
    when the popped value equals the current mcid (the BMC case). The
    result is that the same parts get flushed twice — at the inner EMC
    and again at the outer EMC. We pin this exact upstream behaviour.
    """
    cs = (
        b"/P <</MCID 0>> BDC\n"
        b"BT /F1 12 Tf 50 700 Td (One) Tj ET\n"
        b"/Artifact BMC\n"
        b"BT /F1 12 Tf 50 680 Td (Art) Tj ET\n"
        b"EMC\n"
        b"BT /F1 12 Tf 50 660 Td (Two) Tj ET\n"
        b"EMC\n"
    )
    pdf = _make_pdf_with_content(cs)
    result = extract_mcid_text_map_from_content_streams(pdf)
    # Inner-EMC flush: page_mcids[0] = "OneArt".
    # Then "Two" appended to the still-uncleared parts → ["One","Art","Two"].
    # Outer-EMC flush: append "OneArtTwo" → "OneArt" + "OneArtTwo".
    assert result == {0: {0: "OneArtOneArtTwo"}}


# ---------------------------------------------------------------------------
# extract_mcid_text_map_from_content_streams — decoding
# ---------------------------------------------------------------------------


def test_text_decoded_via_tounicode_cmap() -> None:
    """A font with /ToUnicode mapping decodes raw bytes through the CMap.

    The CMap maps byte 0x41 → "H" and 0x42 → "i". The text-show operand
    contains the raw bytes ``(\x41\x42)``; the decoder should yield "Hi".
    """
    # Build the PDF first so we can attach the CMap stream as an
    # indirect object.
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))

    cmap_stream = _make_tounicode_stream(pdf, _CMAP_BFCHAR_1BYTE)
    font = _make_minimal_font(cmap_stream)
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))

    # Use a hex string operand <4142> so the bytes survive into the
    # parser as-is. Parens-form (\x41\x42) would be interpreted by the
    # PDF lexer as control characters in some cases.
    cs = (
        b"/P <</MCID 0>> BDC\n"
        b"BT /F1 12 Tf 50 700 Td <4142> Tj ET\n"
        b"EMC\n"
    )
    page.Contents = _typed_make_stream(pdf, cs)

    result = extract_mcid_text_map_from_content_streams(pdf)
    assert result == {0: {0: "Hi"}}


def test_no_font_resources_falls_back_to_latin1() -> None:
    """Without /Resources/Font, text decodes through decode_pdf_string."""
    cs = (
        b"/P <</MCID 0>> BDC\n"
        b"BT /F1 12 Tf 50 700 Td (Plain) Tj ET\n"
        b"EMC\n"
    )
    # Note: no font_resource attached.
    pdf = _make_pdf_with_content(cs)
    result = extract_mcid_text_map_from_content_streams(pdf)
    assert result == {0: {0: "Plain"}}


# ---------------------------------------------------------------------------
# parse_tounicode_mapping — direct unit tests
# ---------------------------------------------------------------------------


def test_parse_tounicode_returns_none_when_no_tounicode() -> None:
    pdf = pikepdf.Pdf.new()
    font = _make_minimal_font(tounicode_stream=None)
    pdf.Root[pikepdf.Name("/_TestFont")] = font
    assert parse_tounicode_mapping(font) is None


def test_parse_tounicode_bfchar_one_byte() -> None:
    pdf = pikepdf.Pdf.new()
    cmap = _make_tounicode_stream(pdf, _CMAP_BFCHAR_1BYTE)
    font = _make_minimal_font(cmap)
    result = parse_tounicode_mapping(font)
    assert result is not None
    mapping, byte_width = result
    assert byte_width == 1
    assert mapping[0x41] == "H"
    assert mapping[0x42] == "i"


def test_parse_tounicode_bfchar_two_byte() -> None:
    pdf = pikepdf.Pdf.new()
    cmap = _make_tounicode_stream(pdf, _CMAP_BFCHAR_2BYTE)
    font = _make_minimal_font(cmap)
    result = parse_tounicode_mapping(font)
    assert result is not None
    mapping, byte_width = result
    assert byte_width == 2
    assert mapping[0x0041] == "H"


def test_parse_tounicode_bfrange_simple_form() -> None:
    pdf = pikepdf.Pdf.new()
    cmap = _make_tounicode_stream(pdf, _CMAP_BFRANGE_SIMPLE)
    font = _make_minimal_font(cmap)
    result = parse_tounicode_mapping(font)
    assert result is not None
    mapping, _byte_width = result
    # <41>..<43> mapped starting at <0048> = 'H', so 41→H, 42→I, 43→J.
    assert mapping[0x41] == "H"
    assert mapping[0x42] == "I"
    assert mapping[0x43] == "J"


def test_parse_tounicode_bfrange_array_form() -> None:
    pdf = pikepdf.Pdf.new()
    cmap = _make_tounicode_stream(pdf, _CMAP_BFRANGE_ARRAY)
    font = _make_minimal_font(cmap)
    result = parse_tounicode_mapping(font)
    assert result is not None
    mapping, _byte_width = result
    # Array form: explicit per-source mapping.
    assert mapping[0x41] == "H"
    assert mapping[0x42] == "e"
    assert mapping[0x43] == "l"


def test_parse_tounicode_returns_none_when_mapping_empty() -> None:
    """A CMap stream with no bfchar/bfrange entries → None."""
    pdf = pikepdf.Pdf.new()
    body = b"""/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
1 begincodespacerange
<00> <FF>
endcodespacerange
endcmap
end
end
"""
    cmap = _make_tounicode_stream(pdf, body)
    font = _make_minimal_font(cmap)
    assert parse_tounicode_mapping(font) is None


# ---------------------------------------------------------------------------
# Decoder helpers — isolated tests
# ---------------------------------------------------------------------------


def test_decode_pdf_string_pikepdf_string() -> None:
    s = pikepdf.String("Hello")
    assert decode_pdf_string(s) == "Hello"


def test_decode_pdf_string_bytes_utf8() -> None:
    assert decode_pdf_string(b"caf\xc3\xa9") == "café"


def test_decode_pdf_string_bytes_invalid_utf8_falls_back() -> None:
    # 0xff is invalid UTF-8 → latin-1 fallback yields the replacement.
    out = decode_pdf_string(b"\xff")
    assert isinstance(out, str)
    assert len(out) == 1


def test_decode_pdf_string_str_passthrough() -> None:
    assert decode_pdf_string("already-decoded") == "already-decoded"


def test_decode_with_font_uses_mapping() -> None:
    mapping = {0x41: "H", 0x42: "i"}
    out = decode_with_font(b"\x41\x42", (mapping, 1))
    assert out == "Hi"


def test_decode_with_font_falls_through_to_chr_for_unmapped() -> None:
    mapping = {0x41: "H"}
    out = decode_with_font(b"\x41\x42", (mapping, 1))
    # 0x41 → H (mapped); 0x42 → chr(0x42) = 'B' (unmapped).
    assert out == "HB"


def test_decode_with_font_two_byte_codes() -> None:
    mapping = {0x0041: "H", 0x0042: "i"}
    out = decode_with_font(b"\x00\x41\x00\x42", (mapping, 2))
    assert out == "Hi"


def test_decode_with_font_none_falls_back() -> None:
    out = decode_with_font(pikepdf.String("Plain"), None)
    assert out == "Plain"


# ---------------------------------------------------------------------------
# Type-shape sanity check
# ---------------------------------------------------------------------------


def test_return_type_alias() -> None:
    """Trivial assignment compatibility check for McidTextMap."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    result: McidTextMap = extract_mcid_text_map_from_content_streams(pdf)
    # No content stream attached → either empty page dict or nothing
    # was parsed. Just confirm the shape is the alias.
    assert isinstance(result, dict)
    for k, v in result.items():
        assert isinstance(k, int)
        assert isinstance(v, dict)
