"""Per-page MCID → text-content extractor.

Ports the content-stream walker from pdfMax's
``pdf_accessibility_audit.py`` (``extract_mcid_text_map_from_content_streams``
plus its ``parse_tounicode_mapping`` helper) into the auto_a11y codebase.

The walker iterates every page's content stream, tracks BDC/EMC marked-
content nesting along with the active /MCID, and accumulates whatever
text-show operators draw inside each marker. Per-font /ToUnicode CMaps
are pre-parsed so the byte-level operands of ``Tj``/``TJ``/``'``/``"``
can be decoded into Python strings even when the font has no standard
encoding — this is the common case for tagged PDFs produced by Word,
InDesign and friends.

The original pdfMax module typed every PDF object as ``pikepdf.Object``
and leaned on scattered ``isinstance`` checks. Here we narrow at each
boundary using :mod:`auto_a11y.pdf.audit.pikepdf_helpers` plus explicit
``isinstance`` chains, so callers (and the type-checkers) see concrete
types throughout — no escape hatches.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import TypeAlias

import pikepdf

from auto_a11y.pdf.audit import pikepdf_helpers


# ---------------------------------------------------------------------------
# Public type alias
# ---------------------------------------------------------------------------


#: Per-page MCID → text content map.
#:
#: ``McidTextMap[page_index][mcid] = text``. Used by
#: :func:`auto_a11y.pdf.audit.structure.populate_element_text` to fill in
#: ``StructElement.text_content`` once the structure tree has been walked.
McidTextMap: TypeAlias = dict[int, dict[int, str]]


# ---------------------------------------------------------------------------
# Internal helpers — text decoding
# ---------------------------------------------------------------------------


# Operand of a text-show operator after pikepdf has unpacked the
# instruction. Concretely we see ``pikepdf.String`` for ordinary
# parenthesised literals and hex strings, and ``bytes`` if some producer
# wrote a raw byte string into the content stream. ``str`` is included
# defensively — pikepdf doesn't currently produce it from content stream
# parsing, but neighbouring code paths in pdfMax handled it.
_TextOperand: TypeAlias = pikepdf.String | bytes | str


def decode_pdf_string(s: _TextOperand) -> str:
    """Decode a PDF text operand without any font context.

    Used as the fallback when a font has no /ToUnicode CMap (i.e. when
    the PDF relies on a standard encoding). Mirrors pdfMax's
    ``decode_pdf_string`` in
    ``python/checker/pdf_accessibility_audit.py``.

    Public so tests can exercise the fallback path independently of the
    full content-stream walker.
    """
    if isinstance(s, pikepdf.String):
        try:
            return str(s)
        except (UnicodeDecodeError, ValueError, TypeError):
            return bytes(s).decode("latin-1", errors="replace")
    if isinstance(s, bytes):
        try:
            return s.decode("utf-8")
        except UnicodeDecodeError:
            return s.decode("latin-1", errors="replace")
    # str case: nothing to decode.
    return s


def decode_with_font(
    s: _TextOperand,
    font_info: tuple[dict[int, str], int] | None,
) -> str:
    """Decode a text operand using the font's /ToUnicode mapping.

    If ``font_info`` is ``None`` (font has no /ToUnicode), falls back to
    :func:`decode_pdf_string`. Otherwise consumes the operand byte by
    byte (or as 16-bit codes when the codespace range is two bytes wide)
    and looks each code up in the unicode map.

    Public so tests can exercise the decoder independently.
    """
    if font_info is None:
        return decode_pdf_string(s)

    unicode_map, byte_width = font_info

    raw: bytes
    if isinstance(s, pikepdf.String):
        raw = bytes(s)
    elif isinstance(s, bytes):
        raw = s
    else:
        # str: nothing to decode. Return as-is.
        return s

    parts: list[str] = []
    i = 0
    n = len(raw)
    while i < n:
        if byte_width >= 2 and i + 1 < n:
            code2 = (raw[i] << 8) | raw[i + 1]
            if code2 in unicode_map:
                parts.append(unicode_map[code2])
                i += 2
                continue
        code1 = raw[i]
        if code1 in unicode_map:
            parts.append(unicode_map[code1])
        else:
            parts.append(chr(code1))
        i += 1
    return "".join(parts)


# ---------------------------------------------------------------------------
# /ToUnicode CMap parser
# ---------------------------------------------------------------------------


def parse_tounicode_mapping(
    font_obj: pikepdf.Object,
) -> tuple[dict[int, str], int] | None:
    """Parse a font's /ToUnicode CMap stream.

    The /ToUnicode CMap maps raw character codes (as they appear in
    text-show operands) to Unicode strings. We only need the
    ``beginbfchar``/``endbfchar`` and ``beginbfrange``/``endbfrange``
    sections — everything else (CIDSystemInfo, codespace beyond
    determining byte width, …) is ignored.

    Returns:
        ``(mapping, byte_width)`` where ``mapping`` is
        ``char_code -> unicode_str`` and ``byte_width`` is the number of
        bytes per code (1 or 2) determined from the CMap's
        ``codespacerange``. Callers must use ``byte_width`` to decide
        whether to consume 1 or 2 bytes per code from text-show operands.
        Returns ``None`` if the font has no /ToUnicode stream or if the
        stream couldn't be parsed into any usable mappings.
    """
    try:
        tounicode = font_obj[pikepdf.Name("/ToUnicode")]
    except KeyError:
        return None
    if not isinstance(tounicode, pikepdf.Stream):
        return None

    try:
        data = tounicode.read_bytes()
    except (pikepdf.PdfError, AttributeError, ValueError, TypeError):
        return None
    try:
        # CMap streams are conventionally ASCII / latin-1; a strict UTF-8
        # decode would reject the very byte values we need to read out.
        text = data.decode("latin-1")
    except (UnicodeDecodeError, ValueError):
        return None

    mapping: dict[int, str] = {}

    # Determine byte width from the first codespacerange entry, e.g.
    #   1 begincodespacerange <00> <FF> endcodespacerange  → 1
    #   1 begincodespacerange <0000> <FFFF> endcodespacerange → 2
    byte_width = 1
    cs_match = re.search(r"begincodespacerange\s*<([0-9a-fA-F]+)>", text)
    if cs_match is not None:
        byte_width = max(1, len(cs_match.group(1)) // 2)

    # beginbfchar / endbfchar: a sequence of <srcCode> <dstUnicode>
    # pairs. The pairs may be laid out one-per-line, several-per-line, or
    # the entire block may be a single line — the CMap grammar imposes no
    # line discipline. So we tokenise the *whole block* into consecutive
    # <…> hex groups and consume them two at a time.
    for block in re.finditer(r"beginbfchar\s*(.*?)\s*endbfchar", text, re.DOTALL):
        tokens = re.findall(r"<([0-9a-fA-F]+)>", block.group(1))
        for idx in range(0, len(tokens) - 1, 2):
            try:
                src = int(tokens[idx], 16)
                dst_bytes = bytes.fromhex(tokens[idx + 1])
                mapping[src] = dst_bytes.decode("utf-16-be")
            except (ValueError, UnicodeDecodeError):
                pass

    # beginbfrange / endbfrange:
    #   array form:  <start> <end> [<dst1> <dst2> ...]
    #   simple form: <start> <end> <dstStart>
    #
    # Like bfchar, a block may carry several ranges with no line
    # discipline. We scan the block left-to-right, matching either an
    # array-form entry (which greedily consumes its bracketed destination
    # list) or a simple-form triple, advancing past each match so every
    # entry in the block is parsed — not just the first on each line.
    bfrange_entry = re.compile(
        r"<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>\s*(?:\[([^\]]*)\]|<([0-9a-fA-F]+)>)"
    )
    for block in re.finditer(r"beginbfrange\s*(.*?)\s*endbfrange", text, re.DOTALL):
        for entry in bfrange_entry.finditer(block.group(1)):
            array_body = entry.group(3)
            simple_dst = entry.group(4)
            try:
                start = int(entry.group(1), 16)
                end = int(entry.group(2), 16)
                if array_body is not None:
                    items = re.findall(r"<([0-9a-fA-F]+)>", array_body)
                    for off, item_hex in enumerate(items):
                        if start + off > end:
                            break
                        dst_bytes = bytes.fromhex(item_hex)
                        mapping[start + off] = dst_bytes.decode("utf-16-be")
                elif simple_dst is not None:
                    dst_bytes = bytes.fromhex(simple_dst)
                    dst_start = int.from_bytes(dst_bytes, "big")
                    byte_len = len(dst_bytes)
                    for off in range(end - start + 1):
                        code = dst_start + off
                        mapping[start + off] = code.to_bytes(
                            byte_len, "big"
                        ).decode("utf-16-be")
            except (ValueError, UnicodeDecodeError):
                pass

    if not mapping:
        return None
    return (mapping, byte_width)


# ---------------------------------------------------------------------------
# Per-page font /ToUnicode pre-loader
# ---------------------------------------------------------------------------


def _preload_font_unicode_maps(
    page: pikepdf.Page,
) -> dict[str, tuple[dict[int, str], int] | None]:
    """Pre-parse /ToUnicode CMaps for every font in the page's /Resources.

    Returns a dict keyed by the font's resource name (e.g. ``"/F1"``)
    whose values match :func:`parse_tounicode_mapping`. Fonts that
    lack /ToUnicode, or whose CMap fails to parse, map to ``None`` —
    callers then fall back to :func:`decode_pdf_string`.
    """
    out: dict[str, tuple[dict[int, str], int] | None] = {}

    resources_obj = page.get(pikepdf.Name("/Resources"))
    if resources_obj is None or not isinstance(resources_obj, pikepdf.Dictionary):
        return out

    font_dict = pikepdf_helpers.get_dict(resources_obj, "/Font")
    if font_dict is None:
        return out

    for font_key in font_dict.keys():
        # font_key is a pikepdf.Name; str() gives "/F1" form, matching
        # what content-stream Tf operands emit.
        key_str = str(font_key)
        try:
            font_obj = font_dict[font_key]
        except KeyError:
            out[key_str] = None
            continue
        try:
            out[key_str] = parse_tounicode_mapping(font_obj)
        except (pikepdf.PdfError, KeyError, AttributeError, ValueError, TypeError):
            out[key_str] = None

    return out


# ---------------------------------------------------------------------------
# BDC operand → MCID
# ---------------------------------------------------------------------------


def _bdc_mcid(
    inst: pikepdf.ContentStreamInstruction | pikepdf.ContentStreamInlineImage,
) -> int | None:
    """Pull the /MCID off a BDC instruction's operands, if present.

    A BDC instruction has two operands: the tag name (e.g. ``/P``) and a
    properties Dictionary. The MCID — when present — lives at
    ``properties["/MCID"]``. If the properties operand isn't a
    Dictionary, or there's no /MCID key, returns ``None``.
    """
    operands = inst.operands
    if len(operands) < 2:
        return None
    props = operands[1]
    if not isinstance(props, pikepdf.Dictionary):
        return None
    return pikepdf_helpers.get_int(props, "/MCID")


# ---------------------------------------------------------------------------
# Public API: extract_mcid_text_map_from_content_streams
# ---------------------------------------------------------------------------


def extract_mcid_text_map_from_content_streams(pdf: pikepdf.Pdf) -> McidTextMap:
    """Parse PDF page content streams to map ``(page_index, mcid) -> text``.

    Walks every page in document order, parses its content stream, and
    accumulates the text drawn between matching BDC/EMC pairs that carry
    an /MCID property. The /Font resources on each page are pre-parsed
    so text-show operands (``Tj``, ``TJ``, ``'``, ``"``) can be decoded
    via the active font's /ToUnicode CMap when one is present.

    Pages whose content streams fail to parse (corrupt, encrypted with a
    cipher pikepdf can't read, etc.) are recorded as having an empty
    inner dict — they don't poison the map for the rest of the document.

    Returns:
        ``{page_index: {mcid: text}}`` keyed by 0-based page index. A
        document with no pages returns ``{}``.
    """
    mcid_map: McidTextMap = {}

    for page_num, page in enumerate(pdf.pages):
        page_mcids: dict[int, str] = {}

        try:
            content_stream = pikepdf.parse_content_stream(page)
        except (pikepdf.PdfError, ValueError, TypeError, UnicodeDecodeError):
            mcid_map[page_num] = page_mcids
            continue

        # Pre-load font ToUnicode mappings for this page's resources.
        font_unicode_maps = _preload_font_unicode_maps(page)

        current_mcid: int | None = None
        # Marked-content nesting stack. One frame is pushed for every
        # BDC/BMC and popped on the matching EMC, mirroring the nesting.
        #
        # Each frame is ``(opened_mcid, saved_mcid, saved_parts)``:
        #   * ``opened_mcid`` — True when this marker introduced a *new*
        #     /MCID (a BDC carrying /MCID). Only such markers switch the
        #     active accumulator; their EMC flushes and restores.
        #   * ``saved_mcid`` / ``saved_parts`` — the enclosing region's
        #     MCID and its accumulated text-parts list, captured at the
        #     moment this marker opened so they can be restored on EMC.
        #
        # Preserving ``saved_parts`` is what keeps an outer MCID's text
        # alive across a nested marked-content region: text drawn before
        # the nested BDC and text drawn after the nested EMC both land in
        # the same restored parts list and are flushed together.
        mc_stack: list[tuple[bool, int | None, list[str]]] = []
        current_text_parts: list[str] = []
        current_font: str | None = None

        for inst in content_stream:
            # ContentStreamInlineImage instances also appear in the list
            # but only carry the synthetic "INLINE IMAGE" operator; they
            # never affect MCID state, so we let the dispatch fall
            # through harmlessly via the catch-all "else".
            try:
                op = str(inst.operator)
            except (UnicodeDecodeError, ValueError):
                # Garbage operator bytes — skip the instruction.
                continue
            operands = inst.operands

            if op == "BDC":
                # Begin marked content with properties.
                mcid_val = _bdc_mcid(inst)
                if mcid_val is not None:
                    # A new MCID region: stash the enclosing region's
                    # MCID and its accumulated parts, then start a fresh
                    # accumulator for this region.
                    mc_stack.append((True, current_mcid, current_text_parts))
                    current_mcid = mcid_val
                    current_text_parts = []
                else:
                    # BDC without /MCID: no MCID switch. Text drawn here
                    # belongs to the enclosing MCID, so keep accumulating
                    # into the current parts list.
                    mc_stack.append((False, current_mcid, current_text_parts))

            elif op == "BMC":
                # Begin marked content (no properties, hence no MCID).
                # Text drawn here belongs to the enclosing MCID.
                mc_stack.append((False, current_mcid, current_text_parts))

            elif op == "EMC":
                if mc_stack:
                    opened_mcid, saved_mcid, saved_parts = mc_stack.pop()
                    if opened_mcid:
                        # Closing an MCID region: commit its text, then
                        # resume the enclosing region's accumulator so any
                        # text drawn after this EMC continues to belong to
                        # the outer MCID.
                        if current_mcid is not None and current_text_parts:
                            text = "".join(current_text_parts)
                            if current_mcid in page_mcids:
                                page_mcids[current_mcid] += text
                            else:
                                page_mcids[current_mcid] = text
                        current_mcid = saved_mcid
                        current_text_parts = saved_parts
                    # else: a BMC / property-less BDC closing — the active
                    # MCID and its accumulator are unchanged.
                else:
                    # Unbalanced EMC (no matching open): reset to the
                    # outside-marked-content state.
                    current_mcid = None
                    current_text_parts = []

            elif op == "Tf":
                # Set font: first operand is the resource name (a Name).
                if len(operands) >= 1:
                    current_font = str(operands[0])

            elif op in ("Tj", "'", '"'):
                # Show a single text string. ``'`` and ``"`` also move
                # to a new line and (for ``"``) set spacing — we treat
                # them as plain text shows for MCID accounting.
                if current_mcid is not None and len(operands) >= 1:
                    text_op = _coerce_text_operand(operands[0])
                    if text_op is not None:
                        umap = font_unicode_maps.get(current_font) if current_font else None
                        current_text_parts.append(decode_with_font(text_op, umap))

            elif op == "TJ":
                # Show text with positioning: operand is an Array of
                # alternating strings and numerics. Large negative
                # numerics in the middle of a TJ array indicate inter-
                # word spacing; pdfMax inserts an explicit space when
                # the value is below -100 (a heuristic threshold from
                # the original implementation; see comment there).
                if current_mcid is not None and len(operands) >= 1:
                    arr = operands[0]
                    if isinstance(arr, pikepdf.Array):
                        umap = font_unicode_maps.get(current_font) if current_font else None
                        for arr_idx in range(len(arr)):
                            item = arr[arr_idx]
                            text_op = _coerce_text_operand(item)
                            if text_op is not None:
                                current_text_parts.append(
                                    decode_with_font(text_op, umap)
                                )
                                continue
                            spacing = _coerce_numeric(item)
                            if spacing is not None and spacing < -100:
                                current_text_parts.append(" ")

        mcid_map[page_num] = page_mcids

    return mcid_map


# ---------------------------------------------------------------------------
# Operand coercion helpers
# ---------------------------------------------------------------------------


def _coerce_text_operand(item: object) -> _TextOperand | None:
    """Return ``item`` if it is a usable text operand, else ``None``.

    Text-show operators emit pikepdf.String operands; iterating an Array
    inside TJ may also yield bytes or (defensively) plain str. Anything
    else — Names, ints, Decimals — is not a text operand.
    """
    if isinstance(item, pikepdf.String):
        return item
    if isinstance(item, (bytes, str)):
        return item
    return None


def _coerce_numeric(item: object) -> float | None:
    """Return ``item`` as float if it is a TJ-array spacing number.

    Inside TJ arrays, numerics may arrive as plain ``int``, ``float``,
    or ``Decimal`` (pikepdf unwraps Number objects at the array
    boundary). We coerce all three to ``float`` for the spacing
    threshold comparison; anything else returns ``None``.
    """
    if isinstance(item, bool):
        # bool is a subclass of int in Python; spacing should never be a
        # boolean, so reject it explicitly so we don't read True/False
        # as 1/0 spacing values.
        return None
    if isinstance(item, (int, float, Decimal)):
        return float(item)
    return None
