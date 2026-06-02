"""Regression tests for two content-stream audit bugs.

Bug A — MCID text lost when marked-content regions nest. The walker must
keep the outer MCID's accumulated text alive across a nested BDC region:
text drawn in the outer region *before* and *after* the nested region
both belong to the outer MCID, and the nested region gets its own MCID.

Bug B — the /ToUnicode bfchar/bfrange parser only consumed the first
``<src> <dst>`` pair per physical line, so a line carrying multiple pairs
(valid CMap syntax) — including a whole single-line block — dropped every
mapping but the first. The parser must tokenise each block by ``<…>``
groups across the entire block.

These tests build the synthetic content streams and /ToUnicode CMaps the
same way the sibling ``test_content_streams`` module does, so the inputs
match exactly what the production walker / parser consume.
"""
from __future__ import annotations

from typing import Protocol

import pikepdf

from auto_a11y.pdf.audit.content_streams import (
    extract_mcid_text_map_from_content_streams,
    parse_tounicode_mapping,
)


# ---------------------------------------------------------------------------
# Synthetic-PDF / CMap builders
# ---------------------------------------------------------------------------


class _StreamMaker(Protocol):
    """Single-argument view of ``pikepdf.Pdf.make_stream``.

    pikepdf's stub declares ``make_stream(self, d=None, **kwargs)`` with no
    annotations, which leaks ``Unknown`` under pyright strict at the call
    site. We only ever build streams from raw bytes, so we narrow to one
    overload via this Protocol.
    """

    def __call__(self, data: bytes) -> pikepdf.Stream: ...


def _typed_make_stream(pdf: pikepdf.Pdf, data: bytes) -> pikepdf.Stream:
    """Strictly-typed wrapper around ``Pdf.make_stream``."""
    maker: _StreamMaker = getattr(pdf, "make_stream")
    return maker(data)


def _make_pdf_with_content(content: bytes) -> pikepdf.Pdf:
    """Build a one-page PDF with the given content stream bytes."""
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Contents = _typed_make_stream(pdf, content)
    return pdf


def _make_minimal_font(tounicode_stream: pikepdf.Stream) -> pikepdf.Dictionary:
    """Build a minimal /Font dict carrying the given /ToUnicode stream."""
    font = pikepdf.Dictionary(
        Type=pikepdf.Name("/Font"),
        Subtype=pikepdf.Name("/Type1"),
        BaseFont=pikepdf.Name("/Helvetica"),
    )
    font[pikepdf.Name("/ToUnicode")] = tounicode_stream
    return font


def _make_tounicode_stream(pdf: pikepdf.Pdf, body: bytes) -> pikepdf.Stream:
    """Wrap a CMap body into a Stream attached to ``pdf``."""
    return _typed_make_stream(pdf, body)


# ---------------------------------------------------------------------------
# Bug A — nested marked-content text attribution
# ---------------------------------------------------------------------------


def test_nested_bdc_preserves_outer_text_before_and_after() -> None:
    """Outer MCID keeps text drawn before AND after a nested MCID region.

    Layout:
        BDC /MCID 0
            "Before"            <- belongs to MCID 0
            BDC /MCID 1
                "Inner"         <- belongs to MCID 1
            EMC
            "After"             <- belongs to MCID 0 (drawn after inner EMC)
        EMC
    """
    cs = (
        b"/P <</MCID 0>> BDC\n"
        b"BT /F1 12 Tf 50 700 Td (Before) Tj ET\n"
        b"/Span <</MCID 1>> BDC\n"
        b"BT /F1 12 Tf 50 680 Td (Inner) Tj ET\n"
        b"EMC\n"
        b"BT /F1 12 Tf 50 660 Td (After) Tj ET\n"
        b"EMC\n"
    )
    pdf = _make_pdf_with_content(cs)
    result = extract_mcid_text_map_from_content_streams(pdf)
    # Outer MCID accumulates both its before- and after-nesting text.
    assert result[0][0] == "BeforeAfter"
    # Inner MCID gets exactly its own text.
    assert result[0][1] == "Inner"


def test_doubly_nested_bdc_attribution() -> None:
    """Three levels of nesting each keep their own pre/post text."""
    cs = (
        b"/P <</MCID 0>> BDC\n"
        b"BT /F1 12 Tf 50 700 Td (A) Tj ET\n"
        b"/Span <</MCID 1>> BDC\n"
        b"BT /F1 12 Tf 50 690 Td (B) Tj ET\n"
        b"/Span <</MCID 2>> BDC\n"
        b"BT /F1 12 Tf 50 680 Td (C) Tj ET\n"
        b"EMC\n"
        b"BT /F1 12 Tf 50 670 Td (D) Tj ET\n"
        b"EMC\n"
        b"BT /F1 12 Tf 50 660 Td (E) Tj ET\n"
        b"EMC\n"
    )
    pdf = _make_pdf_with_content(cs)
    result = extract_mcid_text_map_from_content_streams(pdf)
    # MCID 0: "A" before, "E" after the level-1 region.
    assert result[0][0] == "AE"
    # MCID 1: "B" before, "D" after the level-2 region.
    assert result[0][1] == "BD"
    # MCID 2: just "C".
    assert result[0][2] == "C"


def test_bmc_inside_mcid_does_not_double_count() -> None:
    """A property-less BMC inside an MCID keeps accumulating into it once.

    Text drawn inside a /Artifact BMC nested in an MCID region belongs to
    the enclosing MCID, recorded exactly once.
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
    assert result == {0: {0: "OneArtTwo"}}


# ---------------------------------------------------------------------------
# Bug B — ToUnicode parser must read every entry, not just the first per line
# ---------------------------------------------------------------------------


def test_bfchar_multiple_pairs_on_one_line() -> None:
    """Two <src> <dst> pairs on a single bfchar line are both parsed."""
    body = b"""/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
1 begincodespacerange
<00> <FF>
endcodespacerange
2 beginbfchar
<41> <0048> <42> <0069>
endbfchar
endcmap
end
end
"""
    pdf = pikepdf.Pdf.new()
    cmap = _make_tounicode_stream(pdf, body)
    font = _make_minimal_font(cmap)
    result = parse_tounicode_mapping(font)
    assert result is not None
    mapping, _byte_width = result
    assert mapping[0x41] == "H"
    assert mapping[0x42] == "i"


def test_bfchar_single_line_whole_block() -> None:
    """A bfchar block written entirely on one line yields all mappings."""
    body = b"""/CIDInit /ProcSet findresource begin
begincmap
1 begincodespacerange <00> <FF> endcodespacerange
3 beginbfchar <41> <0048> <42> <0069> <43> <0021> endbfchar
endcmap
end
"""
    pdf = pikepdf.Pdf.new()
    cmap = _make_tounicode_stream(pdf, body)
    font = _make_minimal_font(cmap)
    result = parse_tounicode_mapping(font)
    assert result is not None
    mapping, _byte_width = result
    assert mapping[0x41] == "H"
    assert mapping[0x42] == "i"
    assert mapping[0x43] == "!"


def test_bfrange_multiple_triples_on_one_line() -> None:
    """Two simple-form bfrange triples on one line are both expanded."""
    body = b"""/CIDInit /ProcSet findresource begin
begincmap
1 begincodespacerange
<00> <FF>
endcodespacerange
2 beginbfrange
<41> <43> <0048> <61> <63> <0078>
endbfrange
endcmap
end
"""
    pdf = pikepdf.Pdf.new()
    cmap = _make_tounicode_stream(pdf, body)
    font = _make_minimal_font(cmap)
    result = parse_tounicode_mapping(font)
    assert result is not None
    mapping, _byte_width = result
    # First triple: 41->H, 42->I, 43->J.
    assert mapping[0x41] == "H"
    assert mapping[0x42] == "I"
    assert mapping[0x43] == "J"
    # Second triple: 61->x, 62->y, 63->z.
    assert mapping[0x61] == "x"
    assert mapping[0x62] == "y"
    assert mapping[0x63] == "z"


def test_bfrange_array_and_simple_mixed_one_block() -> None:
    """An array-form and a simple-form bfrange on separate lines coexist."""
    body = b"""/CIDInit /ProcSet findresource begin
begincmap
1 begincodespacerange
<00> <FF>
endcodespacerange
2 beginbfrange
<41> <42> [<0048> <0065>]
<61> <62> <0078>
endbfrange
endcmap
end
"""
    pdf = pikepdf.Pdf.new()
    cmap = _make_tounicode_stream(pdf, body)
    font = _make_minimal_font(cmap)
    result = parse_tounicode_mapping(font)
    assert result is not None
    mapping, _byte_width = result
    assert mapping[0x41] == "H"
    assert mapping[0x42] == "e"
    assert mapping[0x61] == "x"
    assert mapping[0x62] == "y"
