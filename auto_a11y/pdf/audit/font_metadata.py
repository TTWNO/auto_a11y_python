"""Per-font metadata collector for the Matterhorn font/CMap/encoding checks.

The pdfminer-based :mod:`auto_a11y.pdf.audit.fonts` collector aggregates
text-level statistics (font usage counts, italics-by-name, line spacing,
alignment). It does *not* expose font-resource details such as
``/ToUnicode`` CMap bytes, ``/Encoding`` ``/Differences`` arrays, glyph
widths, ``/CIDToGIDMap`` entries, or the ``/FontDescriptor`` ``/Flags``
bit that tells symbolic from non-symbolic fonts.

This module fills that gap. :func:`extract_font_metadata` walks every
page's ``/Resources/Font`` dictionary, de-duplicates fonts by base-font
name (matching pdfMax's ``collect_font_metadata`` at line ~2408 of
``pdf_accessibility_audit.py``), and returns a typed
:class:`FontMetadata` tree containing one :class:`FontInfoDetail` per
font. Phase 4 font checks consume that tree to evaluate Matterhorn 10-001,
31-002 through 31-009, and 31-025.

ToUnicode parsing reuses :func:`auto_a11y.pdf.audit.content_streams.parse_tounicode_mapping`
so the codespace-range and bfchar/bfrange logic lives in exactly one
place. The Unicode-validity scan is a small additional pass over the
parsed mapping that records any code points in
:data:`_INVALID_UNICODE_CODES` (matching pdfMax's veraPDF 7.21.7-2 set
at line 2405).
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

import pikepdf

from auto_a11y.pdf.audit import pikepdf_helpers
from auto_a11y.pdf.audit.content_streams import parse_tounicode_mapping


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


#: Invalid Unicode code points per veraPDF 7.21.7-2 (and Matterhorn 10-001).
#:
#: ``0x0000`` is the null character (signals an unmapped glyph in
#: practice), ``0xFEFF`` is the byte-order mark, and ``0xFFFE`` is a
#: non-character. A ToUnicode mapping that resolves any character code
#: to one of these values is a hard accessibility failure: the assistive
#: technology pulling text out of the PDF gets nothing useful for that
#: glyph. Mirrors pdfMax's ``INVALID_UNICODE`` constant verbatim.
_INVALID_UNICODE_CODES: frozenset[int] = frozenset({0x0000, 0xFEFF, 0xFFFE})

#: Maximum number of invalid code-point samples to record per font.
#:
#: pdfMax sorts and joins the full set when rendering its FAIL details;
#: capping the in-memory sample at 10 keeps the dataclass size bounded
#: even for extremely broken fonts. The check function still reports the
#: total count, just truncates the printed sample to the first three.
_MAX_INVALID_SAMPLES: int = 10


@dataclass(frozen=True)
class FontUnicodeMapping:
    """Decoded ToUnicode CMap state for a single font.

    Built by :func:`extract_font_metadata` from
    :func:`auto_a11y.pdf.audit.content_streams.parse_tounicode_mapping`
    plus a small additional Unicode-validity pass.

    Attributes:
        mapping: ``char_code -> unicode_str`` from the parsed CMap.
        byte_width: ``1`` or ``2`` — the per-code byte width determined
            from the CMap's ``codespacerange``. Callers that decode raw
            byte streams need this.
        raw_bytes: the original CMap stream bytes. Retained for the
            "Identity CMap" check, which inspects the textual content of
            the CMap (e.g. searches for ``<0000>`` mappings) rather than
            the parsed structure.
        has_invalid_unicode: ``True`` when at least one character code
            maps to a value containing a code point in
            :data:`_INVALID_UNICODE_CODES` (or any lone surrogate, or
            U+FFFD replacement).
        invalid_codepoints: a sample of the invalid code points found
            (capped at :data:`_MAX_INVALID_SAMPLES`). Sorted ascending.
    """

    mapping: dict[int, str]
    byte_width: int
    raw_bytes: bytes
    has_invalid_unicode: bool
    invalid_codepoints: list[int]


@dataclass(frozen=True)
class FontEncodingDifferences:
    """The ``/Differences`` array entries for a font's ``/Encoding`` dict.

    Built by :func:`extract_font_metadata` for every font whose
    ``/Encoding`` is itself a dictionary. Fonts with a Name-typed
    ``/Encoding`` (like ``/WinAnsiEncoding``) have no differences
    dictionary and therefore no :class:`FontEncodingDifferences`.

    Attributes:
        base_encoding: the ``/BaseEncoding`` Name as a string
            (e.g. ``"/WinAnsiEncoding"``), or ``None`` when the
            encoding dict has no ``/BaseEncoding``.
        differences: ``(code, glyph_name)`` pairs in the order they
            appear in the ``/Differences`` array. Each int code is
            absolute (the array re-bases at each int operand; we do
            that re-basing here so consumers see flat tuples).
        has_notdef: ``True`` when at least one ``glyph_name`` is
            ``".notdef"``. Drives Matterhorn 31-008.
    """

    base_encoding: str | None
    differences: list[tuple[int, str]]
    has_notdef: bool


@dataclass(frozen=True)
class FontInfoDetail:
    """Per-font metadata sufficient for the Matterhorn font checks.

    The list of :class:`FontInfoDetail` for an audited PDF lives on
    :class:`FontMetadata`. Built by :func:`extract_font_metadata` once
    per font (de-duplicated by ``/BaseFont`` name, matching pdfMax's
    ``seen_fonts`` dict at line 2413).

    Attributes:
        font_name: the resource key the font was first seen under
            (e.g. ``"/F1"``). Useful for diagnostics.
        base_font: the ``/BaseFont`` value as a string (with the leading
            ``/``). ``"/unknown"`` when the resource has no ``/BaseFont``.
        subtype: the ``/Subtype`` value as a string. ``""`` when absent.
            One of ``"/Type0"``, ``"/Type1"``, ``"/TrueType"``,
            ``"/MMType1"``, ``"/Type3"``, ``"/CIDFontType0"`` or
            ``"/CIDFontType2"``.
        is_symbolic: ``True`` when the font's
            ``/FontDescriptor/Flags`` bit 3 (mask ``0x4``) is set. When
            the descriptor or flags entry is missing, defaults to
            ``False`` — pdfMax's same convention.
        has_to_unicode: ``True`` when the font has a ``/ToUnicode``
            entry (regardless of whether the stream parses).
        to_unicode: parsed ToUnicode state, or ``None`` when the font
            has no ``/ToUnicode`` or the stream couldn't be parsed into
            any mappings.
        encoding_differences: parsed ``/Differences`` state, or ``None``
            when the font's ``/Encoding`` is not a dictionary or has no
            ``/Differences`` array.
        has_identity_h_or_v: ``True`` when the font's ``/Encoding`` is
            the Name ``/Identity-H`` or ``/Identity-V``. Drives Matterhorn
            31-007.
        cmap_wmode: the ``/WMode`` integer from the CMap (0 horizontal,
            1 vertical), or ``None`` when no CMap was inspected. For
            Type0 fonts only.
        cid_font_wmode: vertical-metrics WMode inferred from the
            descendant CIDFont's ``/W2``/``/DW2`` entries — ``1`` when
            either is present, ``None`` otherwise. For Type0 fonts only.
        glyph_widths_count: count of widths in the font's widths array.
            For Type1/TrueType, the ``/Widths`` array length; for
            CIDFontType2, ``len(cid_font.get(/W) or [])``. ``None`` when
            no widths array is present.
        widths_first_char: the ``/FirstChar`` value for simple fonts,
            ``None`` otherwise.
        widths_last_char: the ``/LastChar`` value for simple fonts,
            ``None`` otherwise.
        cidtogidmap: ``"Identity"``, ``"stream"``, or ``None`` —
            descendant CIDFont's ``/CIDToGIDMap`` shape.
        has_cid_font_file: ``True`` when the descendant CIDFont's
            ``/FontDescriptor/FontFile2`` is present (i.e. an embedded
            CIDFontType2 program).
        cmap_name: predefined CMap name (e.g. ``"Identity-H"``) when the
            ``/Encoding`` was a Name; embedded CMap's ``/CMapName`` when
            the ``/Encoding`` was a stream; ``None`` for non-Type0 fonts.
        cmap_embedded: ``True`` when the ``/Encoding`` was a CMap
            stream (i.e. embedded), ``False`` for Name-typed encodings.
        encoding_kind: ``"name"``, ``"dict"``, ``"stream"``, ``"custom"``,
            or ``""`` (no ``/Encoding`` entry). For simple fonts this
            doubles as the ``encoding`` field pdfMax tracked.
        encoding_name: the encoding's name as a string when
            ``encoding_kind`` is ``"name"`` (e.g. ``"/WinAnsiEncoding"``)
            or the ``/BaseEncoding`` from a dict; ``""`` otherwise.
        page: 1-based page number where the font was first seen
            (matches pdfMax's ``page`` field).
        is_cid_type2: ``True`` when the font's descendant CIDFont was
            classified as ``/CIDFontType2`` during collection.
        has_font_file: ``True`` when an embedded font program was
            found — for simple fonts via ``/FontFile``/``/FontFile2``/
            ``/FontFile3`` on the descriptor; for Type0 fonts via the
            descendant CIDFont's ``/FontFile2``.
    """

    font_name: str
    base_font: str
    subtype: str
    is_symbolic: bool
    has_to_unicode: bool
    to_unicode: FontUnicodeMapping | None
    encoding_differences: FontEncodingDifferences | None
    has_identity_h_or_v: bool
    cmap_wmode: int | None
    cid_font_wmode: int | None
    glyph_widths_count: int | None
    widths_first_char: int | None
    widths_last_char: int | None
    cidtogidmap: str | None
    has_cid_font_file: bool
    cmap_name: str | None
    cmap_embedded: bool
    encoding_kind: str
    encoding_name: str
    page: int
    is_cid_type2: bool
    """``True`` when the descendant CIDFont is CIDFontType2."""

    has_font_file: bool
    """``True`` when the font (or its descendant) has an embedded program.

    For simple fonts that's any of ``/FontFile``, ``/FontFile2``,
    ``/FontFile3`` on the descriptor. For Type0 fonts it's the
    descendant CIDFont's ``/FontFile2``."""

    @property
    def is_type0(self) -> bool:
        """``True`` when this is a Type0 (composite) font."""
        return self.subtype == "/Type0"


@dataclass(frozen=True)
class FontMetadata:
    """Document-wide bundle of font-resource metadata.

    Returned by :func:`extract_font_metadata`. Stored on
    :attr:`auto_a11y.pdf.models.AuditContext.font_metadata` for the
    Phase 4 Matterhorn font checks to consume.

    Attributes:
        fonts: one :class:`FontInfoDetail` per unique font (de-duplicated
            by ``/BaseFont`` value), in document order — i.e. ordered by
            the page where each font is first encountered.
    """

    fonts: list[FontInfoDetail]


# ---------------------------------------------------------------------------
# Constants used during collection
# ---------------------------------------------------------------------------


#: Symbolic flag bit in ``/FontDescriptor/Flags``. Bit 3 (mask 0x4).
#:
#: A non-symbolic TrueType font (i.e. one whose Flags has this bit
#: clear) must use a Latin-derived ``/Encoding`` — Matterhorn 31-003
#: enforces that. A symbolic TrueType (bit set) is exempt.
_FLAG_SYMBOLIC: int = 0x4


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_font_metadata(
    pdf: pikepdf.Pdf,
    *,
    progress: Callable[[str, float], None] | None = None,
) -> FontMetadata:
    """Walk every page's ``/Resources/Font`` and emit per-font metadata.

    De-duplicates fonts by their ``/BaseFont`` value: a font referenced
    from multiple pages produces a single :class:`FontInfoDetail` with
    its ``page`` set to the first page it was seen on (mirroring
    pdfMax's ``seen_fonts`` dict at line 2413).

    Each individual font extraction is wrapped in a broad ``try``/
    ``except`` that swallows pikepdf object errors, missing keys, and
    type mismatches — a single malformed font resource cannot abort the
    whole collection.

    Args:
        pdf: the open document.

    Returns:
        :class:`FontMetadata` with one entry per unique base font.
    """
    seen: dict[str, FontInfoDetail] = {}
    total_pages = max(1, len(pdf.pages))

    for page_num, page in enumerate(pdf.pages, start=1):
        if progress is not None:
            progress(
                f"Extracting font metadata: page {page_num} of {total_pages}",
                (page_num - 1) / total_pages,
            )
        resources = pikepdf_helpers.get_dict(page.obj, "/Resources")
        if resources is None:
            continue
        fonts = pikepdf_helpers.get_dict(resources, "/Font")
        if fonts is None:
            continue

        for font_key in fonts.keys():
            try:
                font_obj = fonts[font_key]
            except KeyError:
                continue
            if not isinstance(font_obj, pikepdf.Dictionary):
                continue

            try:
                detail = _extract_one_font(
                    font_obj, font_key=str(font_key), page_num=page_num,
                )
            except (
                pikepdf.PdfError,
                KeyError,
                AttributeError,
                TypeError,
                ValueError,
            ):
                continue

            # De-duplicate by base font name (pdfMax convention). A font
            # referenced from multiple pages keeps the first occurrence.
            if detail.base_font in seen:
                continue
            seen[detail.base_font] = detail

    return FontMetadata(fonts=list(seen.values()))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_one_font(
    font_obj: pikepdf.Dictionary,
    *,
    font_key: str,
    page_num: int,
) -> FontInfoDetail:
    """Build a :class:`FontInfoDetail` for one font resource."""
    base_font_name = pikepdf_helpers.get_name(font_obj, "/BaseFont")
    base_font = str(base_font_name) if base_font_name is not None else "/unknown"

    subtype_name = pikepdf_helpers.get_name(font_obj, "/Subtype")
    subtype = str(subtype_name) if subtype_name is not None else ""

    encoding_kind, encoding_name, cmap_embedded_flag = _classify_encoding(font_obj)

    # /Differences parsing (only when /Encoding is a dict).
    encoding_diffs: FontEncodingDifferences | None = None
    encoding_obj_for_diffs: pikepdf.Object | None
    try:
        encoding_obj_for_diffs = font_obj[pikepdf.Name("/Encoding")]
    except KeyError:
        encoding_obj_for_diffs = None
    if isinstance(encoding_obj_for_diffs, pikepdf.Dictionary):
        encoding_diffs = _parse_encoding_differences(encoding_obj_for_diffs)

    # ToUnicode parsing (re-uses parse_tounicode_mapping for the data,
    # then we layer on the raw bytes + Unicode-validity scan).
    to_unicode = _build_to_unicode(font_obj)
    has_to_unicode = pikepdf.Name("/ToUnicode") in font_obj

    # Symbolic flag from FontDescriptor.
    is_symbolic = _is_symbolic(font_obj)

    # Identity CMap detection (Type0-only meaningful, but harmless on
    # simple fonts — they'll have encoding_kind != "name").
    has_identity = encoding_kind == "name" and encoding_name in (
        "/Identity-H", "/Identity-V",
    )

    cmap_name: str | None = None
    cmap_wmode: int | None = None
    cmap_embedded = cmap_embedded_flag

    cid_font_wmode: int | None = None
    cidtogidmap: str | None = None
    has_cid_font_file = False
    is_cid_type2 = False
    cid_widths_count: int | None = None

    glyph_widths_count: int | None = None
    widths_first_char: int | None = None
    widths_last_char: int | None = None
    simple_has_font_file = False

    if subtype == "/Type0":
        cmap_name, cmap_wmode = _classify_type0_cmap(font_obj)
        descendants = pikepdf_helpers.get_array(font_obj, "/DescendantFonts")
        if descendants is not None and len(descendants) > 0:
            cid_font = descendants[0]
            if isinstance(cid_font, pikepdf.Dictionary):
                cid_subtype_name = pikepdf_helpers.get_name(cid_font, "/Subtype")
                cid_subtype = (
                    str(cid_subtype_name) if cid_subtype_name is not None else ""
                )
                if cid_subtype == "/CIDFontType2":
                    is_cid_type2 = True
                    cidtogidmap = _classify_cidtogidmap(cid_font)
                    cid_descriptor = pikepdf_helpers.get_dict(
                        cid_font, "/FontDescriptor",
                    )
                    if cid_descriptor is not None:
                        has_cid_font_file = (
                            pikepdf.Name("/FontFile2") in cid_descriptor
                        )
                # CIDFont width arrays: /W is the per-CID widths array,
                # /DW is the default width.
                cid_widths_count = _cid_widths_count(cid_font)
                # cid_font_wmode is 1 when /W2 or /DW2 is present.
                cid_font_wmode = _cid_font_wmode(cid_font)
        glyph_widths_count = cid_widths_count
    elif subtype in ("/Type1", "/TrueType", "/MMType1"):
        first_char = pikepdf_helpers.get_int(font_obj, "/FirstChar")
        last_char = pikepdf_helpers.get_int(font_obj, "/LastChar")
        widths = pikepdf_helpers.get_array(font_obj, "/Widths")
        if widths is not None:
            glyph_widths_count = len(widths)
        widths_first_char = first_char
        widths_last_char = last_char
        descriptor = pikepdf_helpers.get_dict(font_obj, "/FontDescriptor")
        if descriptor is not None:
            simple_has_font_file = (
                pikepdf.Name("/FontFile") in descriptor
                or pikepdf.Name("/FontFile2") in descriptor
                or pikepdf.Name("/FontFile3") in descriptor
            )

    has_font_file = (
        has_cid_font_file if subtype == "/Type0" else simple_has_font_file
    )

    return FontInfoDetail(
        font_name=font_key,
        base_font=base_font,
        subtype=subtype,
        is_symbolic=is_symbolic,
        has_to_unicode=has_to_unicode,
        to_unicode=to_unicode,
        encoding_differences=encoding_diffs,
        has_identity_h_or_v=has_identity,
        cmap_wmode=cmap_wmode,
        cid_font_wmode=cid_font_wmode,
        glyph_widths_count=glyph_widths_count,
        widths_first_char=widths_first_char,
        widths_last_char=widths_last_char,
        cidtogidmap=cidtogidmap,
        has_cid_font_file=has_cid_font_file,
        cmap_name=cmap_name,
        cmap_embedded=cmap_embedded,
        encoding_kind=encoding_kind,
        encoding_name=encoding_name,
        page=page_num,
        is_cid_type2=is_cid_type2,
        has_font_file=has_font_file,
    )


def _classify_encoding(
    font_obj: pikepdf.Dictionary,
) -> tuple[str, str, bool]:
    """Classify ``font_obj["/Encoding"]`` into ``(kind, name, embedded)``.

    Returns:
        ``kind`` is one of ``"name"`` (a Name object), ``"dict"`` (a
        Dictionary), ``"stream"`` (a CMap stream), ``"custom"``
        (Dictionary with no ``/BaseEncoding``), or ``""`` (no
        ``/Encoding`` entry at all).
        ``name`` is the encoding's textual handle:
        for ``"name"``, the Name itself (e.g. ``"/WinAnsiEncoding"``);
        for ``"dict"``, the ``/BaseEncoding`` Name as a string or
        ``"custom"`` if absent;
        for ``"stream"``, ``"stream"``;
        otherwise ``""``.
        ``embedded`` is ``True`` iff ``kind == "stream"``.
    """
    try:
        encoding_obj = font_obj[pikepdf.Name("/Encoding")]
    except KeyError:
        return "", "", False

    if isinstance(encoding_obj, pikepdf.Name):
        return "name", str(encoding_obj), False
    if isinstance(encoding_obj, pikepdf.Dictionary):
        base_encoding = pikepdf_helpers.get_name(encoding_obj, "/BaseEncoding")
        if base_encoding is not None:
            return "dict", str(base_encoding), False
        return "dict", "custom", False
    if isinstance(encoding_obj, pikepdf.Stream):
        return "stream", "stream", True
    return "", "", False


def _parse_encoding_differences(
    encoding_dict: pikepdf.Dictionary,
) -> FontEncodingDifferences | None:
    """Extract ``(code, glyph_name)`` pairs from ``encoding_dict``.

    Mirrors the PDF spec: ``/Differences`` is an array alternating ints
    (re-bases the running code counter) and Names (glyph names assigned
    to consecutive codes starting at the most recent int).

    Returns ``None`` when the encoding dict has no ``/Differences``
    array — there's nothing to report.
    """
    base_encoding_name = pikepdf_helpers.get_name(encoding_dict, "/BaseEncoding")
    base_encoding = str(base_encoding_name) if base_encoding_name is not None else None

    diffs_array = pikepdf_helpers.get_array(encoding_dict, "/Differences")
    if diffs_array is None:
        return None

    diffs: list[tuple[int, str]] = []
    has_notdef = False
    code: int | None = None
    for arr_idx in range(len(diffs_array)):
        item = diffs_array[arr_idx]
        if isinstance(item, pikepdf.Name):
            if code is None:
                # /Differences arrays are required to start with a code,
                # but we tolerate names appearing before the first int by
                # skipping them (no rebase yet to attach them to).
                continue
            glyph_name = str(item).lstrip("/")
            diffs.append((code, glyph_name))
            if glyph_name == ".notdef":
                has_notdef = True
            code += 1
            continue
        # Numeric: rebase the running code counter. pikepdf yields
        # plain ints for numeric Array entries (not pikepdf.Object), so
        # we try int() on the operand directly.
        try:
            code = int(item)
        except (TypeError, ValueError):
            # Not a numeric and not a Name — skip the operand.
            continue

    return FontEncodingDifferences(
        base_encoding=base_encoding,
        differences=diffs,
        has_notdef=has_notdef,
    )


def _build_to_unicode(font_obj: pikepdf.Dictionary) -> FontUnicodeMapping | None:
    """Decode the font's ``/ToUnicode`` CMap and scan for invalid mappings."""
    parsed = parse_tounicode_mapping(font_obj)
    if parsed is None:
        return None
    mapping, byte_width = parsed

    raw_bytes = b""
    try:
        tounicode_obj = font_obj[pikepdf.Name("/ToUnicode")]
        if isinstance(tounicode_obj, pikepdf.Stream):
            raw_bytes = tounicode_obj.read_bytes()
    except (KeyError, pikepdf.PdfError, AttributeError, ValueError, TypeError):
        raw_bytes = b""

    invalid: list[int] = []
    for unicode_str in mapping.values():
        for ch in unicode_str:
            cp = ord(ch)
            if cp in _INVALID_UNICODE_CODES or 0xD800 <= cp <= 0xDFFF or cp == 0xFFFD:
                if cp not in invalid:
                    invalid.append(cp)
                if len(invalid) >= _MAX_INVALID_SAMPLES:
                    break
        if len(invalid) >= _MAX_INVALID_SAMPLES:
            break

    invalid.sort()
    return FontUnicodeMapping(
        mapping=mapping,
        byte_width=byte_width,
        raw_bytes=raw_bytes,
        has_invalid_unicode=bool(invalid),
        invalid_codepoints=invalid,
    )


def _is_symbolic(font_obj: pikepdf.Dictionary) -> bool:
    """Return ``True`` when the font's descriptor flags set the symbolic bit."""
    descriptor = pikepdf_helpers.get_dict(font_obj, "/FontDescriptor")
    if descriptor is None:
        return False
    flags = pikepdf_helpers.get_int(descriptor, "/Flags")
    if flags is None:
        return False
    return (flags & _FLAG_SYMBOLIC) != 0


def _classify_type0_cmap(
    font_obj: pikepdf.Dictionary,
) -> tuple[str | None, int | None]:
    """For a Type0 font, return ``(cmap_name, cmap_wmode)``.

    For a Name-typed ``/Encoding`` (predefined CMap), the CMap name is
    the bare name (no leading ``/``), and ``cmap_wmode`` is inferred
    from the standard ``-V`` (vertical, 1) or ``-H`` (horizontal, 0)
    suffix.

    For a stream-typed ``/Encoding`` (embedded CMap), the CMap name is
    its ``/CMapName`` dict entry (with the ``/`` stripped), or
    ``"(embedded)"`` if absent. ``cmap_wmode`` reads the stream's
    ``/WMode`` entry directly.

    Returns ``(None, None)`` when no ``/Encoding`` is present — the
    caller treats that as "not a real Type0 font we can classify".
    """
    try:
        encoding = font_obj[pikepdf.Name("/Encoding")]
    except KeyError:
        return None, None

    if isinstance(encoding, pikepdf.Name):
        cmap_name = str(encoding).lstrip("/")
        wmode: int | None
        if cmap_name.endswith("-V"):
            wmode = 1
        elif cmap_name.endswith("-H"):
            wmode = 0
        else:
            wmode = None
        return cmap_name, wmode

    if isinstance(encoding, pikepdf.Stream):
        cmap_name_obj = pikepdf_helpers.get_name(encoding, "/CMapName")
        cmap_name = (
            str(cmap_name_obj).lstrip("/")
            if cmap_name_obj is not None
            else "(embedded)"
        )
        # /WMode lives on the CMap stream's dictionary.
        wmode = pikepdf_helpers.get_int(encoding, "/WMode")
        if wmode is None:
            # Some embedded CMaps inline /WMode in the stream body.
            try:
                body = encoding.read_bytes()
                match = re.search(rb"/WMode\s+(\d)", body)
                if match is not None:
                    wmode = int(match.group(1))
            except (pikepdf.PdfError, AttributeError, ValueError, TypeError):
                wmode = None
        return cmap_name, wmode

    return None, None


def _classify_cidtogidmap(cid_font: pikepdf.Dictionary) -> str | None:
    """Return ``"Identity"``, ``"stream"``, or ``None`` for the CIDToGIDMap.

    Mirrors pdfMax's ``cidtogidmap_type``: the descendant CIDFont's
    ``/CIDToGIDMap`` entry is either the Name ``/Identity`` (1-to-1
    mapping) or a stream (explicit lookup table).
    """
    try:
        cidtogid = cid_font[pikepdf.Name("/CIDToGIDMap")]
    except KeyError:
        return None
    if isinstance(cidtogid, pikepdf.Name):
        return str(cidtogid).lstrip("/")
    if isinstance(cidtogid, pikepdf.Stream):
        return "stream"
    return None


def _cid_widths_count(cid_font: pikepdf.Dictionary) -> int | None:
    """Return ``len(cid_font["/W"])`` if present, ``None`` otherwise."""
    w = pikepdf_helpers.get_array(cid_font, "/W")
    if w is None:
        return None
    return len(w)


def _cid_font_wmode(cid_font: pikepdf.Dictionary) -> int | None:
    """Return ``1`` if the CIDFont declares vertical metrics, ``None`` otherwise.

    A CIDFont with ``/W2`` (per-CID vertical metrics) or ``/DW2``
    (default vertical metrics) is set up for vertical writing — pdfMax
    surfaces this as ``cid_font_wmode = 1`` so the CMap WMode check can
    flag mismatches.
    """
    has_w2 = pikepdf.Name("/W2") in cid_font
    has_dw2 = pikepdf.Name("/DW2") in cid_font
    if has_w2 or has_dw2:
        return 1
    return None


__all__ = [
    "FontEncodingDifferences",
    "FontInfoDetail",
    "FontMetadata",
    "FontUnicodeMapping",
    "extract_font_metadata",
]
