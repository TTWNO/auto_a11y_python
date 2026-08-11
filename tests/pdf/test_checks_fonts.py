"""Tests for ``auto_a11y.pdf.audit.checks.fonts``.

Eight font/typography checks ported from pdfMax's
``pdf_accessibility_audit.py`` (lines ~2854-3237 and ~4602-4641):

* ``check_all_fonts_embedded`` (Matterhorn 31-001).
* ``check_font_sizes_accessible`` (WCAG 1.4 best practice).
* ``check_font_faces_readable`` (WCAG 1.4 best practice).
* ``check_font_size_ratio`` (WCAG 1.4 best practice).
* ``check_text_rotation_accessible`` (WCAG 1.4 best practice).
* ``check_italic_text_usage`` (WCAG 1.4 best practice).
* ``check_line_height_accessible`` (WCAG 1.4.12).
* ``check_text_alignment_accessible`` (WCAG 1.4 best practice).

These tests follow the convention established in
:mod:`tests.pdf.test_checks_headings`: a ``ctx: AuditContext`` is
built with whichever fields the check reads (most consume
``ctx.font_analysis``; ``check_all_fonts_embedded`` is the lone
exception, which reads ``ctx.pdf`` directly).
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

import pikepdf

from auto_a11y.pdf.audit.checks.fonts import (
    FONTS_CHECKS,
    check_all_fonts_embedded,
    check_cid_font_gid_mapping,
    check_cmap_resources_valid,
    check_cmap_wmode_consistency,
    check_font_encoding_consistency,
    check_font_faces_readable,
    check_font_glyph_widths_consistent,
    check_font_size_ratio,
    check_font_sizes_accessible,
    check_identity_cmap_has_tounicode,
    check_italic_text_usage,
    check_line_height_accessible,
    check_no_notdef_glyph_references,
    check_no_notdef_in_differences,
    check_nonsymbolic_truetype_latin_mapping,
    check_text_alignment_accessible,
    check_text_rotation_accessible,
    check_unicode_mapping_tounicode,
    check_valid_unicode_values,
    classify_font,
)
from auto_a11y.pdf.audit.font_metadata import (
    FontEncodingDifferences,
    FontInfoDetail,
    FontMetadata,
    FontUnicodeMapping,
)
from auto_a11y.pdf.audit.fonts import (
    AlignmentReport,
    FontAnalysis,
    FontInfo,
    ItalicRun,
    LineSpacing,
    RotationInfo,
)
from auto_a11y.pdf.models import AuditContext, CheckResult


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _StreamMaker(Protocol):
    """Typed view of ``pikepdf.Pdf.make_stream`` for single-arg calls."""

    def __call__(self, data: bytes) -> pikepdf.Stream: ...


def _typed_make_stream(pdf: pikepdf.Pdf, data: bytes) -> pikepdf.Stream:
    """Strictly-typed wrapper around ``Pdf.make_stream`` (see test_fonts.py)."""
    maker: _StreamMaker = getattr(pdf, "make_stream")
    return maker(data)


def _ctx(
    *,
    font_analysis: FontAnalysis | None = None,
    pdf: pikepdf.Pdf | None = None,
    font_metadata: FontMetadata | None = None,
) -> AuditContext:
    """Build a minimal ``AuditContext``.

    ``font_analysis`` populates :attr:`AuditContext.font_analysis`;
    ``font_metadata`` populates :attr:`AuditContext.font_metadata`;
    ``pdf`` overrides the open-document field. All three are optional.
    """
    return AuditContext(
        pdf=pdf if pdf is not None else pikepdf.Pdf.new(),
        pdf_path=Path("/tmp/test.pdf"),
        elements=[],
        role_map={},
        font_analysis=font_analysis,
        font_metadata=font_metadata,
    )


def _detail(
    *,
    base_font: str = "/MyFont",
    subtype: str = "/TrueType",
    is_symbolic: bool = False,
    has_to_unicode: bool = False,
    to_unicode: FontUnicodeMapping | None = None,
    encoding_differences: FontEncodingDifferences | None = None,
    has_identity_h_or_v: bool = False,
    cmap_wmode: int | None = None,
    cid_font_wmode: int | None = None,
    glyph_widths_count: int | None = None,
    widths_first_char: int | None = None,
    widths_last_char: int | None = None,
    cidtogidmap: str | None = None,
    has_cid_font_file: bool = False,
    cmap_name: str | None = None,
    cmap_embedded: bool = False,
    encoding_kind: str = "name",
    encoding_name: str = "/WinAnsiEncoding",
    page: int = 1,
    is_cid_type2: bool = False,
    has_font_file: bool = False,
    font_name: str = "/F1",
) -> FontInfoDetail:
    """Build a :class:`FontInfoDetail` with the given attributes.

    Defaults give a non-symbolic TrueType with WinAnsiEncoding — the
    "boring" font shape that should pass every Matterhorn check.
    """
    return FontInfoDetail(
        font_name=font_name,
        base_font=base_font,
        subtype=subtype,
        is_symbolic=is_symbolic,
        has_to_unicode=has_to_unicode,
        to_unicode=to_unicode,
        encoding_differences=encoding_differences,
        has_identity_h_or_v=has_identity_h_or_v,
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
        page=page,
        is_cid_type2=is_cid_type2,
        has_font_file=has_font_file,
    )


def _empty_analysis() -> FontAnalysis:
    """Return an empty :class:`FontAnalysis` (every collection empty)."""
    return FontAnalysis(
        fonts={}, rotations=[], italic_runs=[], line_spacings=[], alignments=[],
    )


def _font(
    name: str,
    *,
    sizes: set[float] | None = None,
    char_count: int = 100,
    is_italic: bool = False,
    is_bold: bool = False,
) -> FontInfo:
    """Build a :class:`FontInfo` with the given name and stats.

    Defaults: 100 chars at size 12pt, not italic, not bold.
    """
    return FontInfo(
        name=name,
        sample="",
        sizes=sizes if sizes is not None else {12.0},
        pages={0},
        char_count=char_count,
        is_italic=is_italic,
        is_bold=is_bold,
    )


def _only(results: list[CheckResult]) -> CheckResult:
    """Assert single-element list and return its sole entry."""
    assert len(results) == 1, f"expected one CheckResult, got {len(results)}"
    return results[0]


def _new_pdf_with_pages(num_pages: int = 1) -> pikepdf.Pdf:
    """Return a fresh ``pikepdf.Pdf`` with ``num_pages`` blank pages."""
    pdf = pikepdf.Pdf.new()
    for _ in range(num_pages):
        pdf.add_blank_page(page_size=(612, 792))
    return pdf


def _attach_font_resource(
    pdf: pikepdf.Pdf,
    page_index: int,
    *,
    font_key: str = "/F1",
    base_font: str = "Helvetica",
    subtype: str = "/Type1",
    has_descriptor: bool = False,
    has_font_file: bool = False,
    font_file_key: str = "/FontFile",
) -> None:
    """Install a /Resources/Font entry on a blank page.

    ``has_descriptor`` controls whether a ``/FontDescriptor`` is added;
    ``has_font_file`` controls whether the descriptor carries the
    embedded program (only meaningful when ``has_descriptor`` is true).
    """
    page = pdf.pages[page_index]
    font_dict: dict[str, pikepdf.Object] = {
        "/Type": pikepdf.Name("/Font"),
        "/Subtype": pikepdf.Name(subtype),
        "/BaseFont": pikepdf.Name("/" + base_font),
    }
    if has_descriptor:
        descriptor_dict: dict[str, pikepdf.Object] = {
            "/Type": pikepdf.Name("/FontDescriptor"),
            "/FontName": pikepdf.Name("/" + base_font),
        }
        if has_font_file:
            stream_obj = _typed_make_stream(pdf, b"\x00")
            descriptor_dict[font_file_key] = stream_obj
        font_dict["/FontDescriptor"] = pikepdf.Dictionary(descriptor_dict)
    fonts = pikepdf.Dictionary({font_key: pikepdf.Dictionary(font_dict)})
    page.Resources = pikepdf.Dictionary(Font=fonts)


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------


def test_fonts_checks_registry_lists_all_functions() -> None:
    """``FONTS_CHECKS`` is the phase-5 entry point — must list everything.

    The original eight pdfminer-driven checks come first (preserving the
    order from Phase 4.12), followed by the eleven Matterhorn
    font/CMap/encoding checks added in the Phase 4.12 follow-up.
    """
    assert FONTS_CHECKS == [
        check_all_fonts_embedded,
        check_font_sizes_accessible,
        check_font_faces_readable,
        check_font_size_ratio,
        check_text_rotation_accessible,
        check_italic_text_usage,
        check_line_height_accessible,
        check_text_alignment_accessible,
        check_unicode_mapping_tounicode,
        check_cid_font_gid_mapping,
        check_cmap_resources_valid,
        check_valid_unicode_values,
        check_no_notdef_glyph_references,
        check_font_glyph_widths_consistent,
        check_no_notdef_in_differences,
        check_identity_cmap_has_tounicode,
        check_cmap_wmode_consistency,
        check_nonsymbolic_truetype_latin_mapping,
        check_font_encoding_consistency,
    ]


# ---------------------------------------------------------------------------
# classify_font
# ---------------------------------------------------------------------------


def test_classify_font_script_match() -> None:
    assert classify_font("BrushScriptMT") == ("script", "brushscript")


def test_classify_font_narrow_match() -> None:
    assert classify_font("Arial-Narrow") == ("narrow", "narrow")


def test_classify_font_decorative_match() -> None:
    assert classify_font("Papyrus-Regular") == ("decorative", "papyrus")


def test_classify_font_blackletter_match() -> None:
    assert classify_font("Fraktur-Regular") == ("blackletter", "fraktur")


def test_classify_font_plain_helvetica_no_match() -> None:
    assert classify_font("Helvetica") is None


# ---------------------------------------------------------------------------
# check_all_fonts_embedded
# ---------------------------------------------------------------------------


def test_all_fonts_embedded_passes_when_no_pages() -> None:
    pdf = pikepdf.Pdf.new()
    res = _only(check_all_fonts_embedded(_ctx(pdf=pdf)))
    assert res.name == "All fonts embedded"
    assert res.standard == "Matterhorn 31-001"
    assert res.result == "PASS"


def test_all_fonts_embedded_passes_for_standard_type1() -> None:
    """Helvetica (a standard 14 font) needs no /FontDescriptor."""
    pdf = _new_pdf_with_pages(1)
    _attach_font_resource(
        pdf, 0, base_font="Helvetica", subtype="/Type1",
        has_descriptor=False,
    )
    res = _only(check_all_fonts_embedded(_ctx(pdf=pdf)))
    assert res.result == "PASS"
    assert "1 fonts checked" in res.details


def test_all_fonts_embedded_passes_for_embedded_truetype() -> None:
    pdf = _new_pdf_with_pages(1)
    _attach_font_resource(
        pdf, 0, base_font="MyFont", subtype="/TrueType",
        has_descriptor=True, has_font_file=True, font_file_key="/FontFile2",
    )
    res = _only(check_all_fonts_embedded(_ctx(pdf=pdf)))
    assert res.result == "PASS"


def test_all_fonts_embedded_fails_for_unembedded_descriptor() -> None:
    """A font with /FontDescriptor but no /FontFile* is unembedded."""
    pdf = _new_pdf_with_pages(1)
    _attach_font_resource(
        pdf, 0, base_font="MyFont", subtype="/TrueType",
        has_descriptor=True, has_font_file=False,
    )
    res = _only(check_all_fonts_embedded(_ctx(pdf=pdf)))
    assert res.result == "FAIL"
    assert "MyFont" in res.details
    assert "Unembedded fonts" in res.details


def test_all_fonts_embedded_skips_pages_without_resources() -> None:
    """A blank page without /Resources is silently skipped."""
    pdf = _new_pdf_with_pages(1)
    # No font installed at all.
    res = _only(check_all_fonts_embedded(_ctx(pdf=pdf)))
    assert res.result == "PASS"


# ---------------------------------------------------------------------------
# check_font_sizes_accessible
# ---------------------------------------------------------------------------


def test_font_sizes_accessible_warns_on_no_data() -> None:
    res = _only(check_font_sizes_accessible(_ctx(font_analysis=_empty_analysis())))
    assert res.result == "WARN"
    assert "Could not extract font information" in res.details


def test_font_sizes_accessible_warns_when_collector_did_not_run() -> None:
    """``ctx.font_analysis is None`` is treated as no data, same as empty."""
    res = _only(check_font_sizes_accessible(_ctx()))
    assert res.result == "WARN"


def test_font_sizes_accessible_passes_at_or_above_body_size() -> None:
    fa = FontAnalysis(
        fonts={"Helvetica": _font("Helvetica", sizes={12.0, 18.0}, char_count=200)},
        rotations=[], italic_runs=[], line_spacings=[], alignments=[],
    )
    res = _only(check_font_sizes_accessible(_ctx(font_analysis=fa)))
    assert res.name == "Font sizes accessible"
    assert res.standard == "WCAG 1.4 (best practice)"
    assert res.result == "PASS"
    assert "12.0pt" in res.details


def test_font_sizes_accessible_warns_when_below_body_min() -> None:
    fa = FontAnalysis(
        fonts={
            "Helvetica": _font("Helvetica", sizes={11.0}, char_count=50),
            "Times": _font("Times", sizes={14.0}, char_count=150),
        },
        rotations=[], italic_runs=[], line_spacings=[], alignments=[],
    )
    res = _only(check_font_sizes_accessible(_ctx(font_analysis=fa)))
    assert res.result == "WARN"
    assert "11.0pt" not in res.details  # WARN message is aggregate, not per-font
    assert "below 12.0pt" in res.details


def test_font_sizes_accessible_fails_when_below_absolute_min() -> None:
    fa = FontAnalysis(
        fonts={
            "TinyFont": _font("TinyFont", sizes={6.0}, char_count=50),
            "Helvetica": _font("Helvetica", sizes={12.0}, char_count=150),
        },
        rotations=[], italic_runs=[], line_spacings=[], alignments=[],
    )
    res = _only(check_font_sizes_accessible(_ctx(font_analysis=fa)))
    assert res.result == "FAIL"
    assert "TinyFont" in res.details
    assert "below 9.0pt" in res.details


# ---------------------------------------------------------------------------
# check_font_faces_readable
# ---------------------------------------------------------------------------


def test_font_faces_readable_passes_on_plain_fonts() -> None:
    fa = FontAnalysis(
        fonts={"Helvetica": _font("Helvetica"), "Times-Roman": _font("Times-Roman")},
        rotations=[], italic_runs=[], line_spacings=[], alignments=[],
    )
    res = _only(check_font_faces_readable(_ctx(font_analysis=fa)))
    assert res.name == "Font faces readable"
    assert res.standard == "WCAG 1.4 (best practice)"
    assert res.result == "PASS"


def test_font_faces_readable_warns_on_problematic_font() -> None:
    fa = FontAnalysis(
        fonts={"BrushScriptMT": _font("BrushScriptMT")},
        rotations=[], italic_runs=[], line_spacings=[], alignments=[],
    )
    res = _only(check_font_faces_readable(_ctx(font_analysis=fa)))
    assert res.result == "WARN"
    assert "script" in res.details
    assert "BrushScriptMT" in res.details


def test_font_faces_readable_warns_on_no_data() -> None:
    res = _only(check_font_faces_readable(_ctx(font_analysis=_empty_analysis())))
    assert res.result == "WARN"


# ---------------------------------------------------------------------------
# check_font_size_ratio
# ---------------------------------------------------------------------------


def test_font_size_ratio_passes_within_3_to_1() -> None:
    fa = FontAnalysis(
        fonts={
            "Helvetica": _font("Helvetica", sizes={10.0, 24.0}, char_count=100),
        },
        rotations=[], italic_runs=[], line_spacings=[], alignments=[],
    )
    res = _only(check_font_size_ratio(_ctx(font_analysis=fa)))
    assert res.name == "Font size ratio (magnification)"
    assert res.result == "PASS"
    assert "2.4:1" in res.details


def test_font_size_ratio_warns_above_3_to_1() -> None:
    fa = FontAnalysis(
        fonts={
            "Helvetica": _font("Helvetica", sizes={6.0, 36.0}, char_count=100),
        },
        rotations=[], italic_runs=[], line_spacings=[], alignments=[],
    )
    res = _only(check_font_size_ratio(_ctx(font_analysis=fa)))
    assert res.result == "WARN"
    assert "6.0:1" in res.details
    assert "exceeds recommended 3:1" in res.details


def test_font_size_ratio_warns_when_no_eligible_fonts() -> None:
    """A font with char_count < 3 is excluded from the size pool."""
    fa = FontAnalysis(
        fonts={"Helvetica": _font("Helvetica", char_count=2)},
        rotations=[], italic_runs=[], line_spacings=[], alignments=[],
    )
    res = _only(check_font_size_ratio(_ctx(font_analysis=fa)))
    assert res.result == "WARN"
    assert "Insufficient font size data" in res.details


def test_font_size_ratio_warns_on_no_data() -> None:
    res = _only(check_font_size_ratio(_ctx(font_analysis=_empty_analysis())))
    assert res.result == "WARN"


# ---------------------------------------------------------------------------
# check_text_rotation_accessible
# ---------------------------------------------------------------------------


def test_text_rotation_passes_when_only_horizontal() -> None:
    fa = FontAnalysis(
        fonts={}, rotations=[RotationInfo(angle=0.0, sample="hi", page=0)],
        italic_runs=[], line_spacings=[], alignments=[],
    )
    res = _only(check_text_rotation_accessible(_ctx(font_analysis=fa)))
    assert res.name == "Text rotation accessible"
    assert res.result == "PASS"


def test_text_rotation_passes_when_no_rotation_data() -> None:
    res = _only(check_text_rotation_accessible(_ctx(font_analysis=_empty_analysis())))
    assert res.result == "PASS"


def test_text_rotation_passes_within_tolerance() -> None:
    """Angles within ±2° of horizontal are still treated as horizontal."""
    fa = FontAnalysis(
        fonts={},
        rotations=[
            RotationInfo(angle=1.0, sample="a", page=0),
            RotationInfo(angle=-2.0, sample="b", page=0),
        ],
        italic_runs=[], line_spacings=[], alignments=[],
    )
    res = _only(check_text_rotation_accessible(_ctx(font_analysis=fa)))
    assert res.result == "PASS"


def test_text_rotation_warns_on_rotated_text() -> None:
    fa = FontAnalysis(
        fonts={},
        rotations=[
            RotationInfo(angle=0.0, sample="ok", page=0),
            RotationInfo(angle=90.0, sample="rot", page=1),
        ],
        italic_runs=[], line_spacings=[], alignments=[],
    )
    res = _only(check_text_rotation_accessible(_ctx(font_analysis=fa)))
    assert res.result == "WARN"
    assert "90" in res.details


# ---------------------------------------------------------------------------
# check_italic_text_usage
# ---------------------------------------------------------------------------


def test_italic_text_is_not_applicable_when_no_italic() -> None:
    fa = FontAnalysis(
        fonts={"Helvetica": _font("Helvetica")},
        rotations=[], italic_runs=[], line_spacings=[], alignments=[],
    )
    res = _only(check_italic_text_usage(_ctx(font_analysis=fa)))
    assert res.name == "Italic text usage"
    assert res.result == "NA"
    assert "No italic text" in res.details


def test_italic_text_warns_on_long_italic_passage() -> None:
    """A run of 7+ words triggers a WARN regardless of overall percentage."""
    long_text = "this is a fairly long italic passage of words"
    fa = FontAnalysis(
        fonts={
            "Helvetica": _font("Helvetica", char_count=900),
            "Helvetica-Italic": _font(
                "Helvetica-Italic", char_count=len(long_text), is_italic=True,
            ),
        },
        rotations=[],
        italic_runs=[
            ItalicRun(text=long_text, font="Helvetica-Italic", size=12.0, page=0),
        ],
        line_spacings=[], alignments=[],
    )
    res = _only(check_italic_text_usage(_ctx(font_analysis=fa)))
    assert res.result == "WARN"
    assert "exceed 6 words" in res.details


def test_italic_text_warns_when_over_10_percent() -> None:
    """No long runs but >10% italic coverage still WARNs."""
    fa = FontAnalysis(
        fonts={
            "Helvetica": _font("Helvetica", char_count=80),
            "Helvetica-Italic": _font(
                "Helvetica-Italic", char_count=20, is_italic=True,
            ),
        },
        rotations=[],
        italic_runs=[
            ItalicRun(text="a b c", font="Helvetica-Italic", size=12.0, page=0),
        ],
        line_spacings=[], alignments=[],
    )
    res = _only(check_italic_text_usage(_ctx(font_analysis=fa)))
    assert res.result == "WARN"
    assert "20%" in res.details


def test_italic_text_passes_when_short_runs_and_low_pct() -> None:
    fa = FontAnalysis(
        fonts={
            "Helvetica": _font("Helvetica", char_count=950),
            "Helvetica-Italic": _font(
                "Helvetica-Italic", char_count=50, is_italic=True,
            ),
        },
        rotations=[],
        italic_runs=[
            ItalicRun(text="a short bit", font="Helvetica-Italic", size=12.0, page=0),
        ],
        line_spacings=[], alignments=[],
    )
    res = _only(check_italic_text_usage(_ctx(font_analysis=fa)))
    assert res.result == "PASS"
    assert "within acceptable limits" in res.details


# ---------------------------------------------------------------------------
# check_line_height_accessible
# ---------------------------------------------------------------------------


def test_line_height_warns_on_no_measurements() -> None:
    res = _only(check_line_height_accessible(_ctx(font_analysis=_empty_analysis())))
    assert res.result == "WARN"
    assert "Could not measure line spacing" in res.details


def test_line_height_passes_when_all_above_minimum() -> None:
    fa = FontAnalysis(
        fonts={}, rotations=[], italic_runs=[],
        line_spacings=[
            LineSpacing(font_size=12.0, leading=18.0, ratio=1.5, text="a"),
            LineSpacing(font_size=12.0, leading=21.6, ratio=1.8, text="b"),
        ],
        alignments=[],
    )
    res = _only(check_line_height_accessible(_ctx(font_analysis=fa)))
    assert res.name == "Line height accessible"
    assert res.standard == "WCAG 1.4.12"
    assert res.result == "PASS"


def test_line_height_warns_when_some_below_minimum() -> None:
    """1-50% below minimum → WARN."""
    fa = FontAnalysis(
        fonts={}, rotations=[], italic_runs=[],
        line_spacings=[
            LineSpacing(font_size=12.0, leading=18.0, ratio=1.5, text="a"),
            LineSpacing(font_size=12.0, leading=18.0, ratio=1.5, text="b"),
            LineSpacing(font_size=12.0, leading=18.0, ratio=1.5, text="c"),
            LineSpacing(font_size=12.0, leading=14.0, ratio=1.2, text="d"),
        ],
        alignments=[],
    )
    res = _only(check_line_height_accessible(_ctx(font_analysis=fa)))
    assert res.result == "WARN"
    assert "1 of 4" in res.details


def test_line_height_fails_when_majority_below_minimum() -> None:
    """>50% below minimum → FAIL."""
    fa = FontAnalysis(
        fonts={}, rotations=[], italic_runs=[],
        line_spacings=[
            LineSpacing(font_size=12.0, leading=14.0, ratio=1.2, text="a"),
            LineSpacing(font_size=12.0, leading=14.0, ratio=1.2, text="b"),
            LineSpacing(font_size=12.0, leading=18.0, ratio=1.5, text="c"),
        ],
        alignments=[],
    )
    res = _only(check_line_height_accessible(_ctx(font_analysis=fa)))
    assert res.result == "FAIL"


# ---------------------------------------------------------------------------
# check_text_alignment_accessible
# ---------------------------------------------------------------------------


def test_text_alignment_passes_when_no_blocks() -> None:
    res = _only(check_text_alignment_accessible(_ctx(font_analysis=_empty_analysis())))
    assert res.name == "Text alignment accessible"
    assert res.result == "PASS"


def test_text_alignment_passes_on_left_aligned_blocks() -> None:
    fa = FontAnalysis(
        fonts={}, rotations=[], italic_runs=[], line_spacings=[],
        alignments=[
            AlignmentReport(page=0, alignment="left", line_count=3),
            AlignmentReport(page=0, alignment="left", line_count=4),
        ],
    )
    res = _only(check_text_alignment_accessible(_ctx(font_analysis=fa)))
    assert res.result == "PASS"


def test_text_alignment_warns_on_justified_block() -> None:
    fa = FontAnalysis(
        fonts={}, rotations=[], italic_runs=[], line_spacings=[],
        alignments=[
            AlignmentReport(page=0, alignment="justified", line_count=5),
            AlignmentReport(page=0, alignment="left", line_count=3),
        ],
    )
    res = _only(check_text_alignment_accessible(_ctx(font_analysis=fa)))
    assert res.result == "WARN"
    assert "1 justified" in res.details


def test_text_alignment_warns_on_centered_block() -> None:
    fa = FontAnalysis(
        fonts={}, rotations=[], italic_runs=[], line_spacings=[],
        alignments=[
            AlignmentReport(page=0, alignment="center", line_count=4),
        ],
    )
    res = _only(check_text_alignment_accessible(_ctx(font_analysis=fa)))
    assert res.result == "WARN"
    assert "1 centered" in res.details


# ---------------------------------------------------------------------------
# check_unicode_mapping_tounicode (Matterhorn 10-001)
# ---------------------------------------------------------------------------


def test_unicode_mapping_skips_when_metadata_missing() -> None:
    res = _only(check_unicode_mapping_tounicode(_ctx()))
    assert res.result == "INFO"


def test_unicode_mapping_passes_for_standard_14_font() -> None:
    fm = FontMetadata(fonts=[
        _detail(base_font="/Helvetica", subtype="/Type1", encoding_name=""),
    ])
    res = _only(check_unicode_mapping_tounicode(_ctx(font_metadata=fm)))
    assert res.result == "PASS"


def test_unicode_mapping_passes_for_standard_encoding() -> None:
    fm = FontMetadata(fonts=[
        _detail(
            base_font="/CustomFont", subtype="/TrueType",
            encoding_kind="name", encoding_name="/WinAnsiEncoding",
        ),
    ])
    res = _only(check_unicode_mapping_tounicode(_ctx(font_metadata=fm)))
    assert res.result == "PASS"


def test_unicode_mapping_fails_when_no_tounicode() -> None:
    fm = FontMetadata(fonts=[
        _detail(
            base_font="/CustomFont", subtype="/Type0",
            encoding_kind="name", encoding_name="/Identity-H",
            has_to_unicode=False,
        ),
    ])
    res = _only(check_unicode_mapping_tounicode(_ctx(font_metadata=fm)))
    assert res.result == "FAIL"
    assert "/CustomFont" in res.details


# ---------------------------------------------------------------------------
# check_cid_font_gid_mapping (Matterhorn 31-004)
# ---------------------------------------------------------------------------


def test_cid_gid_is_not_applicable_when_no_type2_fonts() -> None:
    fm = FontMetadata(fonts=[
        _detail(base_font="/Helvetica", subtype="/Type1"),
    ])
    res = _only(check_cid_font_gid_mapping(_ctx(font_metadata=fm)))
    assert res.result == "NA"
    assert "not applicable" in res.details


def test_cid_gid_passes_when_mapping_present() -> None:
    fm = FontMetadata(fonts=[
        _detail(
            base_font="/CJKFont", subtype="/Type0",
            is_cid_type2=True, has_cid_font_file=True, has_font_file=True,
            cidtogidmap="Identity",
        ),
    ])
    res = _only(check_cid_font_gid_mapping(_ctx(font_metadata=fm)))
    assert res.result == "PASS"


def test_cid_gid_fails_when_embedded_without_mapping() -> None:
    fm = FontMetadata(fonts=[
        _detail(
            base_font="/CJKFont", subtype="/Type0",
            is_cid_type2=True, has_cid_font_file=True, has_font_file=True,
            cidtogidmap=None,
        ),
    ])
    res = _only(check_cid_font_gid_mapping(_ctx(font_metadata=fm)))
    assert res.result == "FAIL"
    assert "/CJKFont" in res.details


# ---------------------------------------------------------------------------
# check_cmap_resources_valid (Matterhorn 31-006)
# ---------------------------------------------------------------------------


def test_cmap_resources_is_not_applicable_with_no_type0() -> None:
    fm = FontMetadata(fonts=[
        _detail(base_font="/Helvetica", subtype="/Type1"),
    ])
    res = _only(check_cmap_resources_valid(_ctx(font_metadata=fm)))
    assert res.result == "NA"


def test_cmap_resources_passes_with_predefined_cmap() -> None:
    fm = FontMetadata(fonts=[
        _detail(
            base_font="/CJKFont", subtype="/Type0",
            cmap_name="Identity-H", cmap_embedded=False,
        ),
    ])
    res = _only(check_cmap_resources_valid(_ctx(font_metadata=fm)))
    assert res.result == "PASS"


def test_cmap_resources_fails_with_unknown_cmap() -> None:
    fm = FontMetadata(fonts=[
        _detail(
            base_font="/CJKFont", subtype="/Type0",
            cmap_name="MyCustomCMap", cmap_embedded=False,
        ),
    ])
    res = _only(check_cmap_resources_valid(_ctx(font_metadata=fm)))
    assert res.result == "FAIL"
    assert "MyCustomCMap" in res.details


# ---------------------------------------------------------------------------
# check_valid_unicode_values (veraPDF 7.21.7-2)
# ---------------------------------------------------------------------------


def test_valid_unicode_is_not_applicable_when_no_tounicode() -> None:
    fm = FontMetadata(fonts=[
        _detail(base_font="/Helvetica", to_unicode=None),
    ])
    res = _only(check_valid_unicode_values(_ctx(font_metadata=fm)))
    assert res.result == "NA"
    assert "No ToUnicode" in res.details


def test_valid_unicode_passes_with_clean_mapping() -> None:
    tu = FontUnicodeMapping(
        mapping={1: "A", 2: "B"},
        byte_width=1, raw_bytes=b"",
        has_invalid_unicode=False, invalid_codepoints=[],
    )
    fm = FontMetadata(fonts=[
        _detail(base_font="/CleanFont", to_unicode=tu, has_to_unicode=True),
    ])
    res = _only(check_valid_unicode_values(_ctx(font_metadata=fm)))
    assert res.result == "PASS"


def test_valid_unicode_fails_with_null_codepoint() -> None:
    tu = FontUnicodeMapping(
        mapping={1: "\x00"},
        byte_width=1, raw_bytes=b"",
        has_invalid_unicode=True, invalid_codepoints=[0x0000],
    )
    fm = FontMetadata(fonts=[
        _detail(base_font="/BadFont", to_unicode=tu, has_to_unicode=True),
    ])
    res = _only(check_valid_unicode_values(_ctx(font_metadata=fm)))
    assert res.result == "FAIL"
    assert "U+0000" in res.details


# ---------------------------------------------------------------------------
# check_no_notdef_glyph_references (Matterhorn 31-025)
# ---------------------------------------------------------------------------


def test_no_notdef_passes_on_clean_fonts() -> None:
    fm = FontMetadata(fonts=[
        _detail(base_font="/Helvetica"),
    ])
    res = _only(check_no_notdef_glyph_references(_ctx(font_metadata=fm)))
    assert res.result == "PASS"


def test_no_notdef_fails_when_in_differences() -> None:
    enc = FontEncodingDifferences(
        base_encoding="/WinAnsiEncoding",
        differences=[(32, ".notdef")],
        has_notdef=True,
    )
    fm = FontMetadata(fonts=[
        _detail(base_font="/BadFont", encoding_differences=enc),
    ])
    res = _only(check_no_notdef_glyph_references(_ctx(font_metadata=fm)))
    assert res.result == "FAIL"
    assert "/BadFont" in res.details


def test_no_notdef_fails_when_tounicode_maps_zero() -> None:
    tu = FontUnicodeMapping(
        mapping={0: "A"},
        byte_width=1,
        raw_bytes=b"beginbfchar <0000> <0041> endbfchar",
        has_invalid_unicode=False, invalid_codepoints=[],
    )
    fm = FontMetadata(fonts=[
        _detail(base_font="/BadFont", to_unicode=tu, has_to_unicode=True),
    ])
    res = _only(check_no_notdef_glyph_references(_ctx(font_metadata=fm)))
    assert res.result == "FAIL"


# ---------------------------------------------------------------------------
# check_font_glyph_widths_consistent (Matterhorn 31-009)
# ---------------------------------------------------------------------------


def test_glyph_widths_passes_for_consistent_simple_font() -> None:
    fm = FontMetadata(fonts=[
        _detail(
            base_font="/MyFont", subtype="/TrueType",
            widths_first_char=32, widths_last_char=126,
            glyph_widths_count=126 - 32 + 1,
        ),
    ])
    res = _only(check_font_glyph_widths_consistent(_ctx(font_metadata=fm)))
    assert res.result == "PASS"


def test_glyph_widths_fails_when_array_too_short() -> None:
    fm = FontMetadata(fonts=[
        _detail(
            base_font="/MyFont", subtype="/TrueType",
            widths_first_char=32, widths_last_char=126,
            glyph_widths_count=10,
        ),
    ])
    res = _only(check_font_glyph_widths_consistent(_ctx(font_metadata=fm)))
    assert res.result == "FAIL"
    assert "/MyFont" in res.details


def test_glyph_widths_fails_for_cid_font_missing_w() -> None:
    fm = FontMetadata(fonts=[
        _detail(
            base_font="/CJKFont", subtype="/Type0",
            is_cid_type2=True, glyph_widths_count=None,
        ),
    ])
    res = _only(check_font_glyph_widths_consistent(_ctx(font_metadata=fm)))
    assert res.result == "FAIL"
    assert "missing both /W and /DW" in res.details


# ---------------------------------------------------------------------------
# check_no_notdef_in_differences (Matterhorn 31-008)
# ---------------------------------------------------------------------------


def test_differences_notdef_passes_on_clean_diffs() -> None:
    enc = FontEncodingDifferences(
        base_encoding="/WinAnsiEncoding",
        differences=[(32, "space"), (33, "exclam")],
        has_notdef=False,
    )
    fm = FontMetadata(fonts=[
        _detail(base_font="/CleanFont", encoding_differences=enc),
    ])
    res = _only(check_no_notdef_in_differences(_ctx(font_metadata=fm)))
    assert res.result == "PASS"


def test_differences_notdef_fails_when_present() -> None:
    enc = FontEncodingDifferences(
        base_encoding="/WinAnsiEncoding",
        differences=[(32, ".notdef")],
        has_notdef=True,
    )
    fm = FontMetadata(fonts=[
        _detail(base_font="/BadFont", encoding_differences=enc),
    ])
    res = _only(check_no_notdef_in_differences(_ctx(font_metadata=fm)))
    assert res.result == "FAIL"
    assert "/BadFont" in res.details


# ---------------------------------------------------------------------------
# check_identity_cmap_has_tounicode (Matterhorn 31-007)
# ---------------------------------------------------------------------------


def test_identity_cmap_is_not_applicable_when_no_identity_fonts() -> None:
    fm = FontMetadata(fonts=[
        _detail(base_font="/Helvetica", has_identity_h_or_v=False),
    ])
    res = _only(check_identity_cmap_has_tounicode(_ctx(font_metadata=fm)))
    assert res.result == "NA"


def test_identity_cmap_passes_with_tounicode() -> None:
    fm = FontMetadata(fonts=[
        _detail(
            base_font="/CJKFont", subtype="/Type0",
            has_identity_h_or_v=True, has_to_unicode=True,
        ),
    ])
    res = _only(check_identity_cmap_has_tounicode(_ctx(font_metadata=fm)))
    assert res.result == "PASS"


def test_identity_cmap_fails_without_tounicode() -> None:
    fm = FontMetadata(fonts=[
        _detail(
            base_font="/CJKFont", subtype="/Type0",
            has_identity_h_or_v=True, has_to_unicode=False,
        ),
    ])
    res = _only(check_identity_cmap_has_tounicode(_ctx(font_metadata=fm)))
    assert res.result == "FAIL"
    assert "/CJKFont" in res.details


# ---------------------------------------------------------------------------
# check_cmap_wmode_consistency (Matterhorn 31-005)
# ---------------------------------------------------------------------------


def test_wmode_passes_when_no_type0_fonts() -> None:
    fm = FontMetadata(fonts=[
        _detail(base_font="/Helvetica", subtype="/Type1"),
    ])
    res = _only(check_cmap_wmode_consistency(_ctx(font_metadata=fm)))
    assert res.result == "PASS"


def test_wmode_passes_when_consistent() -> None:
    fm = FontMetadata(fonts=[
        _detail(
            base_font="/CJKFont", subtype="/Type0",
            cmap_wmode=0, cid_font_wmode=None,
        ),
    ])
    res = _only(check_cmap_wmode_consistency(_ctx(font_metadata=fm)))
    assert res.result == "PASS"


def test_wmode_fails_on_horizontal_cmap_with_vertical_cidfont() -> None:
    fm = FontMetadata(fonts=[
        _detail(
            base_font="/CJKFont", subtype="/Type0",
            cmap_wmode=0, cid_font_wmode=1,
        ),
    ])
    res = _only(check_cmap_wmode_consistency(_ctx(font_metadata=fm)))
    assert res.result == "FAIL"
    assert "/CJKFont" in res.details


# ---------------------------------------------------------------------------
# check_nonsymbolic_truetype_latin_mapping (Matterhorn 31-003)
# ---------------------------------------------------------------------------


def test_nonsymbolic_tt_is_not_applicable_when_no_such_fonts() -> None:
    fm = FontMetadata(fonts=[
        _detail(base_font="/Helvetica", subtype="/Type1"),
    ])
    res = _only(check_nonsymbolic_truetype_latin_mapping(
        _ctx(font_metadata=fm)
    ))
    assert res.result == "NA"


def test_nonsymbolic_tt_passes_with_winansi() -> None:
    fm = FontMetadata(fonts=[
        _detail(
            base_font="/MyFont", subtype="/TrueType", is_symbolic=False,
            encoding_kind="name", encoding_name="/WinAnsiEncoding",
        ),
    ])
    res = _only(check_nonsymbolic_truetype_latin_mapping(
        _ctx(font_metadata=fm)
    ))
    assert res.result == "PASS"


def test_nonsymbolic_tt_fails_with_custom_encoding() -> None:
    fm = FontMetadata(fonts=[
        _detail(
            base_font="/MyFont", subtype="/TrueType", is_symbolic=False,
            encoding_kind="dict", encoding_name="custom",
        ),
    ])
    res = _only(check_nonsymbolic_truetype_latin_mapping(
        _ctx(font_metadata=fm)
    ))
    assert res.result == "FAIL"
    assert "/MyFont" in res.details


# ---------------------------------------------------------------------------
# check_font_encoding_consistency (Matterhorn 31-002)
# ---------------------------------------------------------------------------


def test_encoding_consistency_skips_without_metadata() -> None:
    res = _only(check_font_encoding_consistency(_ctx()))
    assert res.result == "INFO"


def test_encoding_consistency_passes_structurally() -> None:
    fm = FontMetadata(fonts=[
        _detail(base_font="/Helvetica"),
        _detail(base_font="/Times"),
    ])
    res = _only(check_font_encoding_consistency(_ctx(font_metadata=fm)))
    assert res.result == "PASS"
    assert "2 font(s) checked" in res.details
