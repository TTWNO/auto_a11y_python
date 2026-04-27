"""Font / typography accessibility checks.

Eight checks ported from pdfMax's
``python/checker/pdf_accessibility_audit.py`` (lines ~2854-3237 and
~4602-4641):

* :func:`check_all_fonts_embedded` — every Font dictionary on every
  page either has a /FontDescriptor with a /FontFile* stream or is a
  standard Type1 (Matterhorn 31-001; pdfMax line ~4601).
* :func:`check_font_sizes_accessible` — all text is at or above the
  9pt absolute / 12pt recommended minimum (WCAG 1.4 best practice;
  pdfMax line ~2906).
* :func:`check_font_faces_readable` — no problematic font face
  (script, narrow, decorative, blackletter) is in use (WCAG 1.4 best
  practice; pdfMax line ~2924).
* :func:`check_font_size_ratio` — the largest-to-smallest font-size
  ratio is at most 3:1 across fonts with at least 3 characters (WCAG
  1.4 best practice; pdfMax line ~2980).
* :func:`check_text_rotation_accessible` — no non-horizontal text
  rotation is used (WCAG 1.4 best practice; pdfMax line ~3028).
* :func:`check_italic_text_usage` — italic runs stay short (≤6 words)
  and italic chars are below 10% of the document (WCAG 1.4 best
  practice; pdfMax line ~3081).
* :func:`check_line_height_accessible` — every measured line-pair has
  a leading/font-size ratio at or above 1.5x (WCAG 1.4.12; pdfMax
  line ~3131).
* :func:`check_text_alignment_accessible` — multi-line text blocks
  use left or right alignment, not justified or centered (WCAG 1.4
  best practice; pdfMax line ~3190).

Mirrors the convention established in
:mod:`auto_a11y.pdf.audit.checks.headings`: each check is a plain
function ``(ctx) -> list[CheckResult]`` and the module exposes a
:data:`FONTS_CHECKS` registry list. ``CheckResult`` ``name`` and
``standard`` strings match pdfMax verbatim so Phase 6's check
catalogue can map them.

Deferred from this commit (all eleven require pdfMax's
``collect_font_metadata`` Phase 3 collector, which has not been
ported yet — its output is the per-font dict carrying ToUnicode
bytes, CIDFont/CMap properties, encoding flags, etc.):

* ``Unicode mapping (ToUnicode)`` (Matterhorn 10-001)
* ``CID font GID mapping`` (Matterhorn 31-004)
* ``CMap resources valid`` (Matterhorn 31-006)
* ``Valid Unicode values`` (Matterhorn 10-001 / veraPDF)
* ``No .notdef glyph references`` (Matterhorn 31-025)
* ``No .notdef in Differences array`` (Matterhorn 31-008)
* ``Non-symbolic TrueType Latin mapping`` (Matterhorn 31-003)
* ``Font glyph widths consistent`` (Matterhorn 31-009)
* ``Font encoding consistency`` (Matterhorn 31-002)
* ``Identity CMap has ToUnicode`` (Matterhorn 31-007)
* ``CMap WMode consistency`` (Matterhorn 31-005)

Two modelling notes that make the surfaced detail strings differ
slightly from pdfMax (the verdicts are unchanged):

1. :class:`auto_a11y.pdf.audit.fonts.FontInfo` keys per-font usage by
   font name, with a ``set[float]`` of sizes and a single
   ``char_count`` aggregating across sizes. pdfMax keyed by
   ``(name, size)`` so its size-bucketed FAIL detail can name a
   specific (font, size) pair; here we report per font with the
   smallest in-use size.
2. :class:`auto_a11y.pdf.audit.fonts.RotationInfo` records each
   unique angle once (with the first sample / page seen) but does
   *not* track per-angle character counts. The rotation check
   therefore reports the angles in use and total *number of unique
   angles* rather than pdfMax's "{count} chars ({pct}%) at angles
   …" wording.
"""
from __future__ import annotations

import re
from collections.abc import Callable

import pikepdf

from auto_a11y.pdf.audit import pikepdf_helpers
from auto_a11y.pdf.audit.fonts import FontAnalysis
from auto_a11y.pdf.models import AuditContext, CheckResult


# ---------------------------------------------------------------------------
# Module-private constants
# ---------------------------------------------------------------------------


#: Recommended minimum body-text size (pdfMax line 2334).
_MIN_BODY_SIZE: float = 12.0
#: Absolute minimum readable size (pdfMax line 2335).
_MIN_READABLE_SIZE: float = 9.0
#: Maximum acceptable largest:smallest ratio for magnification compatibility
#: (pdfMax line 2852).
_MAX_SIZE_RATIO: float = 3.0
#: WCAG 1.4.12 recommended minimum line-height ratio (pdfMax line 2849).
_MIN_LINE_HEIGHT: float = 1.5
#: Maximum italic run length (in words) before pdfMax flags it
#: (pdfMax line 2851).
_MAX_ITALIC_WORDS: int = 6
#: Allowed deviation from horizontal (degrees) before pdfMax considers
#: text "rotated" (pdfMax line 3030).
_ROTATION_TOLERANCE: float = 2.0

#: Font faces known to be problematic for accessibility. Mirrors pdfMax's
#: ``PROBLEMATIC_FONTS`` dict at line 2320 verbatim, including the
#: category names used in failure details.
_PROBLEMATIC_FONTS: dict[str, tuple[str, ...]] = {
    "script": (
        "brushscript", "lucidahandwriting", "zapfino", "segoeprint",
        "segoescript", "mistral", "freestyle", "vivaldi", "edwardian",
        "palace", "kunstler", "script", "cursive", "handwriting",
    ),
    "narrow": (
        "narrow", "condensed", "compressed", "impact", "haettenschweiler",
    ),
    "decorative": (
        "papyrus", "jokerman", "curlz", "chiller", "stencil",
        "copperplate", "engravers", "playbill", "showcard",
    ),
    "blackletter": (
        "blackletter", "oldenglish", "fraktur", "textura",
    ),
}

#: Standard Type 1 fonts (line ~2373). Used by :func:`check_all_fonts_embedded`
#: to exempt fonts that legitimately have no FontDescriptor.
_STANDARD_14_FONTS: frozenset[str] = frozenset({
    "Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique",
    "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique",
    "Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic",
    "Symbol", "ZapfDingbats",
})

#: pdfMax strips a base-font subset prefix like ``ABCDEF+Helvetica`` to
#: ``Helvetica`` before comparing against the standard 14 — match the
#: same regex shape (line ~4650, ``base = fm["name"].lstrip("/")``; the
#: subset stripping happens in :func:`classify_font` style elsewhere).
#: Six uppercase letters followed by ``+`` is the PDF subset prefix
#: convention.
_SUBSET_PREFIX_RE: re.Pattern[str] = re.compile(r"^[A-Z]{6}\+")


# ---------------------------------------------------------------------------
# Helpers shared across the pdfminer-output-consuming checks
# ---------------------------------------------------------------------------


def classify_font(fontname: str) -> tuple[str, str] | None:
    """Classify a font name as problematic, returning ``(category, pattern)``.

    Mirrors pdfMax's ``classify_font`` (line ~2821) byte-for-byte: the
    name is normalised by lowercasing and stripping ``-``/``_``/spaces,
    then each :data:`_PROBLEMATIC_FONTS` category's patterns are tried
    in order. Returns the first matching ``(category, pattern)`` pair,
    or ``None`` when no pattern matches.
    """
    fname_lower = (
        fontname.lower().replace("-", "").replace("_", "").replace(" ", "")
    )
    for category, patterns in _PROBLEMATIC_FONTS.items():
        for pattern in patterns:
            if pattern in fname_lower:
                return category, pattern
    return None


def _no_font_data(check_name: str) -> CheckResult:
    """Return the conventional WARN result when font analysis is empty.

    Mirrors pdfMax's ``no_data = CheckResult(...)`` shortcut at line
    ~2855: when :func:`extract_font_analysis` returned no per-font
    data the seven pdfminer-driven checks all share the same WARN
    detail. Centralising here keeps every check consistent and makes
    the contract visible in one place.
    """
    return CheckResult(
        name=check_name,
        standard="WCAG 1.4 (best practice)",
        result="WARN",
        details="Could not extract font information",
    )


def _font_analysis_or_empty(ctx: AuditContext) -> FontAnalysis:
    """Return ``ctx.font_analysis`` or an empty :class:`FontAnalysis`.

    The orchestrator may not have run the pdfminer collector yet (or
    it may have failed and swallowed the error in
    :func:`auto_a11y.pdf.audit.fonts.extract_font_analysis`). Treat
    "no analysis" identically to "analysis succeeded but found no
    text" — both surface as a WARN through :func:`_no_font_data`
    inside each check.
    """
    if ctx.font_analysis is None:
        return FontAnalysis(
            fonts={}, rotations=[], italic_runs=[],
            line_spacings=[], alignments=[],
        )
    return ctx.font_analysis


# ---------------------------------------------------------------------------
# check_all_fonts_embedded
# ---------------------------------------------------------------------------


def _strip_subset_prefix(font_name: str) -> str:
    """Strip a leading ``ABCDEF+`` subset prefix and any leading ``/``."""
    name = font_name.lstrip("/")
    return _SUBSET_PREFIX_RE.sub("", name)


def check_all_fonts_embedded(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 31-001: every page-resource Font is embedded.

    Mirrors pdfMax line ~4601. Walks every page's ``/Resources/Font``
    dictionary and checks that each font either:

    * carries a ``/FontDescriptor`` with at least one of ``/FontFile``,
      ``/FontFile2``, ``/FontFile3``; or
    * is a standard Type1 font that needs no descriptor.

    PASSes when every font is embedded (or is a standard Type1 with no
    descriptor); FAILs when any font is missing its program. Same data
    flow as pdfMax — we just wrap pikepdf reads through the typed
    :mod:`auto_a11y.pdf.audit.pikepdf_helpers` accessors.
    """
    font_details_count = 0
    unembedded: list[str] = []
    for page in ctx.pdf.pages:
        resources = pikepdf_helpers.get_dict(page.obj, "/Resources")
        if resources is None:
            continue
        fonts = pikepdf_helpers.get_dict(resources, "/Font")
        if fonts is None:
            continue
        for font_key in fonts.keys():
            entry = fonts[font_key]
            if not isinstance(entry, pikepdf.Dictionary):
                continue
            base_font_name = pikepdf_helpers.get_name(entry, "/BaseFont")
            if base_font_name is not None:
                font_name = str(base_font_name)
            else:
                font_name = "unknown"
            font_details_count += 1

            descriptor = pikepdf_helpers.get_dict(entry, "/FontDescriptor")
            if descriptor is not None:
                has_file = (
                    "/FontFile" in descriptor
                    or "/FontFile2" in descriptor
                    or "/FontFile3" in descriptor
                )
                if not has_file:
                    unembedded.append(font_name)
                continue

            # No FontDescriptor: standard Type1 fonts are exempt.
            subtype = pikepdf_helpers.get_name(entry, "/Subtype")
            stripped = _strip_subset_prefix(font_name)
            is_standard_type1 = (
                subtype is not None
                and str(subtype) == "/Type1"
                and stripped in _STANDARD_14_FONTS
            )
            if not is_standard_type1:
                # No descriptor on a non-standard font: pdfMax records
                # this as "no FontDescriptor" but does not flip
                # ``all_embedded`` to false (line ~4634). Mirror that.
                pass

    if not unembedded:
        return [
            CheckResult(
                name="All fonts embedded",
                standard="Matterhorn 31-001",
                result="PASS",
                details=f"{font_details_count} fonts checked, all embedded",
            )
        ]
    return [
        CheckResult(
            name="All fonts embedded",
            standard="Matterhorn 31-001",
            result="FAIL",
            details=f"Unembedded fonts: {', '.join(unembedded)}",
        )
    ]


# ---------------------------------------------------------------------------
# check_font_sizes_accessible
# ---------------------------------------------------------------------------


def check_font_sizes_accessible(ctx: AuditContext) -> list[CheckResult]:
    """WCAG 1.4 best practice: text is at or above 9pt / 12pt recommended.

    Mirrors pdfMax line ~2906. FAILs when any font has its smallest
    in-use size below :data:`_MIN_READABLE_SIZE` (9pt); WARNs when no
    font is below 9pt but at least one is below :data:`_MIN_BODY_SIZE`
    (12pt); PASSes when every font's smallest size is at or above
    12pt.

    Modelling difference from pdfMax (see module docstring): pdfMax
    reports per (font, size) char counts. Our :class:`FontInfo` totals
    chars across sizes, so we report per-font using the font's
    smallest in-use size (the worst-case for accessibility).
    """
    fa = _font_analysis_or_empty(ctx)
    if not fa.fonts:
        return [_no_font_data("Font sizes accessible")]

    total_chars = sum(info.char_count for info in fa.fonts.values())
    if total_chars == 0:
        return [_no_font_data("Font sizes accessible")]

    tiny_fonts: list[str] = []  # below absolute minimum (FAIL)
    small_fonts: list[str] = []  # below recommended minimum (WARN)
    tiny_chars = 0
    small_chars = 0

    for info in fa.fonts.values():
        if not info.sizes:
            continue
        smallest = min(info.sizes)
        if smallest < _MIN_READABLE_SIZE:
            tiny_chars += info.char_count
            tiny_fonts.append(
                f"{info.name} at {smallest}pt ({info.char_count} chars):"
                + f" below {_MIN_READABLE_SIZE}pt minimum"
            )
        elif smallest < _MIN_BODY_SIZE:
            small_chars += info.char_count
            small_fonts.append(
                f"{info.name} at {smallest}pt ({info.char_count} chars):"
                + f" below {_MIN_BODY_SIZE}pt recommended"
            )

    if tiny_fonts:
        tiny_pct = tiny_chars / total_chars * 100
        font_names = "; ".join(tiny_fonts[:3])
        return [
            CheckResult(
                name="Font sizes accessible",
                standard="WCAG 1.4 (best practice)",
                result="FAIL",
                details=(
                    f"{len(tiny_fonts)} font(s) below {_MIN_READABLE_SIZE}pt"
                    f" minimum ({tiny_chars} chars, {tiny_pct:.0f}% of"
                    f" document): {font_names}"
                ),
            )
        ]
    if small_fonts:
        small_pct = small_chars / total_chars * 100
        return [
            CheckResult(
                name="Font sizes accessible",
                standard="WCAG 1.4 (best practice)",
                result="WARN",
                details=(
                    f"Some text below {_MIN_BODY_SIZE}pt recommended"
                    f" minimum ({small_chars} chars, {small_pct:.0f}% of"
                    " document)"
                ),
            )
        ]
    return [
        CheckResult(
            name="Font sizes accessible",
            standard="WCAG 1.4 (best practice)",
            result="PASS",
            details=(
                f"All text at or above {_MIN_BODY_SIZE}pt"
                f" ({len(fa.fonts)} fonts, {total_chars} chars)"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_font_faces_readable
# ---------------------------------------------------------------------------


def check_font_faces_readable(ctx: AuditContext) -> list[CheckResult]:
    """WCAG 1.4 best practice: no script / narrow / decorative / blackletter.

    Mirrors pdfMax line ~2924. PASSes when every font name is plain
    (no match in :data:`_PROBLEMATIC_FONTS`); WARNs when any matches.
    """
    fa = _font_analysis_or_empty(ctx)
    if not fa.fonts:
        return [_no_font_data("Font faces readable")]

    face_issues: list[str] = []
    for info in fa.fonts.values():
        match = classify_font(info.name)
        if match is not None:
            category, pattern = match
            face_issues.append(
                f"{info.name}: classified as {category}"
                + f" (matched '{pattern}') — may be difficult to read"
                + " for users with dyslexia or low vision"
            )

    if face_issues:
        return [
            CheckResult(
                name="Font faces readable",
                standard="WCAG 1.4 (best practice)",
                result="WARN",
                details="; ".join(face_issues),
            )
        ]
    return [
        CheckResult(
            name="Font faces readable",
            standard="WCAG 1.4 (best practice)",
            result="PASS",
            details=(
                f"All {len(fa.fonts)} font(s) appear readable (no script,"
                " narrow, decorative, or blackletter fonts detected)"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_font_size_ratio
# ---------------------------------------------------------------------------


def check_font_size_ratio(ctx: AuditContext) -> list[CheckResult]:
    """WCAG 1.4 best practice: largest:smallest size ratio ≤ 3:1.

    Mirrors pdfMax line ~2980. The size pool is the union of every
    in-use size across fonts whose ``char_count >= 3`` — pdfMax filters
    on per (font, size) char count of 3; ours filters on per-font
    total. WARNs when the ratio exceeds :data:`_MAX_SIZE_RATIO` or
    when the pool is empty (insufficient data); PASSes otherwise.
    """
    fa = _font_analysis_or_empty(ctx)
    if not fa.fonts:
        return [
            CheckResult(
                name="Font size ratio (magnification)",
                standard="WCAG 1.4 (best practice)",
                result="WARN",
                details="Insufficient font size data for ratio analysis",
            )
        ]

    all_sizes: list[float] = []
    for info in fa.fonts.values():
        if info.char_count >= 3:
            all_sizes.extend(info.sizes)

    if not all_sizes:
        return [
            CheckResult(
                name="Font size ratio (magnification)",
                standard="WCAG 1.4 (best practice)",
                result="WARN",
                details="Insufficient font size data for ratio analysis",
            )
        ]

    min_size = min(all_sizes)
    max_size = max(all_sizes)
    ratio = max_size / min_size if min_size > 0 else 0.0

    if ratio > _MAX_SIZE_RATIO:
        return [
            CheckResult(
                name="Font size ratio (magnification)",
                standard="WCAG 1.4 (best practice)",
                result="WARN",
                details=(
                    f"{ratio:.1f}:1 ratio ({max_size}pt max / {min_size}pt"
                    f" min) exceeds recommended {_MAX_SIZE_RATIO:.0f}:1"
                    " maximum — magnifier users may struggle"
                ),
            )
        ]
    return [
        CheckResult(
            name="Font size ratio (magnification)",
            standard="WCAG 1.4 (best practice)",
            result="PASS",
            details=(
                f"{ratio:.1f}:1 ratio ({max_size}pt max / {min_size}pt"
                f" min) within recommended {_MAX_SIZE_RATIO:.0f}:1 maximum"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_text_rotation_accessible
# ---------------------------------------------------------------------------


def check_text_rotation_accessible(ctx: AuditContext) -> list[CheckResult]:
    """WCAG 1.4 best practice: text is horizontal (within 2°).

    Mirrors pdfMax line ~3057. PASSes when every recorded angle is
    within :data:`_ROTATION_TOLERANCE` of horizontal (or no rotation
    data exists); WARNs when any angle is outside that band.

    Modelling difference from pdfMax: our :class:`RotationInfo`
    records each unique angle once but does not track per-angle
    character counts. The detail string therefore lists the angles
    rather than reporting "{count} chars ({pct}%)".
    """
    fa = _font_analysis_or_empty(ctx)
    non_horizontal_angles = sorted(
        {r.angle for r in fa.rotations if abs(r.angle) > _ROTATION_TOLERANCE}
    )
    if not non_horizontal_angles:
        return [
            CheckResult(
                name="Text rotation accessible",
                standard="WCAG 1.4 (best practice)",
                result="PASS",
                details=(
                    "All text is horizontal — no rotated or angled text"
                    " detected"
                ),
            )
        ]
    return [
        CheckResult(
            name="Text rotation accessible",
            standard="WCAG 1.4 (best practice)",
            result="WARN",
            details=(
                f"{len(non_horizontal_angles)} non-horizontal text"
                f" rotation(s) found (angles:"
                f" {', '.join(f'{a}°' for a in non_horizontal_angles)})"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_italic_text_usage
# ---------------------------------------------------------------------------


def check_italic_text_usage(ctx: AuditContext) -> list[CheckResult]:
    """WCAG 1.4 best practice: italic runs short, italic chars ≤ 10%.

    Mirrors pdfMax line ~3081. PASSes when no italic text is detected
    *or* when italic stays under 10% of the document with no run
    exceeding :data:`_MAX_ITALIC_WORDS` words; WARNs when either
    threshold is breached.
    """
    fa = _font_analysis_or_empty(ctx)

    italic_runs = fa.italic_runs
    italic_fonts = {info.name for info in fa.fonts.values() if info.is_italic}
    italic_char_count = sum(
        info.char_count for info in fa.fonts.values() if info.is_italic
    )
    total_italic_chars_in_runs = sum(len(r.text) for r in italic_runs)
    total_chars = sum(info.char_count for info in fa.fonts.values())

    if not italic_fonts and total_italic_chars_in_runs == 0:
        return [
            CheckResult(
                name="Italic text usage",
                standard="WCAG 1.4 (best practice)",
                result="PASS",
                details="No italic text detected",
            )
        ]

    long_runs = [r for r in italic_runs if len(r.text.split()) > _MAX_ITALIC_WORDS]
    italic_pct = (
        italic_char_count / total_chars * 100 if total_chars > 0 else 0.0
    )

    if long_runs:
        longest = max(len(r.text.split()) for r in long_runs)
        return [
            CheckResult(
                name="Italic text usage",
                standard="WCAG 1.4 (best practice)",
                result="WARN",
                details=(
                    f"{len(long_runs)} italic passage(s) exceed"
                    f" {_MAX_ITALIC_WORDS} words (longest: {longest} words)."
                    f" Total italic: {italic_char_count} chars"
                    f" ({italic_pct:.0f}%)"
                ),
            )
        ]
    if italic_pct > 10:
        return [
            CheckResult(
                name="Italic text usage",
                standard="WCAG 1.4 (best practice)",
                result="WARN",
                details=(
                    f"{italic_pct:.0f}% of document is italic"
                    f" ({italic_char_count} chars) — consider reducing"
                    " italic usage for readability"
                ),
            )
        ]
    return [
        CheckResult(
            name="Italic text usage",
            standard="WCAG 1.4 (best practice)",
            result="PASS",
            details=(
                f"Italic text present ({italic_char_count} chars,"
                f" {italic_pct:.0f}%) but within acceptable limits"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_line_height_accessible
# ---------------------------------------------------------------------------


def check_line_height_accessible(ctx: AuditContext) -> list[CheckResult]:
    """WCAG 1.4.12: every measured line-pair has ratio ≥ 1.5x.

    Mirrors pdfMax line ~3175. WARNs when no measurements are
    available, FAILs when more than half the measured pairs are below
    :data:`_MIN_LINE_HEIGHT`, WARNs when 1-50% are below, PASSes when
    all measured pairs meet the minimum.
    """
    fa = _font_analysis_or_empty(ctx)
    spacings = fa.line_spacings
    if not spacings:
        return [
            CheckResult(
                name="Line height accessible",
                standard="WCAG 1.4.12",
                result="WARN",
                details="Could not measure line spacing",
            )
        ]

    below_min = [s for s in spacings if s.ratio < _MIN_LINE_HEIGHT]
    avg_ratio = sum(s.ratio for s in spacings) / len(spacings)

    if not below_min:
        return [
            CheckResult(
                name="Line height accessible",
                standard="WCAG 1.4.12",
                result="PASS",
                details=(
                    f"All {len(spacings)} line pairs meet"
                    f" {_MIN_LINE_HEIGHT}x minimum (avg: {avg_ratio:.2f}x)"
                ),
            )
        ]

    pct_below = len(below_min) / len(spacings) * 100
    min_ratio = min(s.ratio for s in spacings)
    return [
        CheckResult(
            name="Line height accessible",
            standard="WCAG 1.4.12",
            result="FAIL" if pct_below > 50 else "WARN",
            details=(
                f"{len(below_min)} of {len(spacings)} line pairs"
                f" ({pct_below:.0f}%) below {_MIN_LINE_HEIGHT}x minimum"
                f" (avg: {avg_ratio:.2f}x, min: {min_ratio:.2f}x)"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_text_alignment_accessible
# ---------------------------------------------------------------------------


def check_text_alignment_accessible(ctx: AuditContext) -> list[CheckResult]:
    """WCAG 1.4 best practice: no justified or centered body alignment.

    Mirrors pdfMax line ~3226. WARNs when any block is justified or
    centered; PASSes otherwise. The detail string reports the count
    of each problematic alignment (pdfMax's per-block sample text is
    not preserved on :class:`AlignmentReport`, so we surface the
    aggregate counts instead).
    """
    fa = _font_analysis_or_empty(ctx)
    alignments = fa.alignments

    justified = [a for a in alignments if a.alignment == "justified"]
    centered = [a for a in alignments if a.alignment == "center"]
    if justified or centered:
        return [
            CheckResult(
                name="Text alignment accessible",
                standard="WCAG 1.4 (best practice)",
                result="WARN",
                details=(
                    f"{len(justified)} justified and {len(centered)}"
                    " centered text block(s) found — recommend"
                    " left-alignment for body text"
                ),
            )
        ]
    return [
        CheckResult(
            name="Text alignment accessible",
            standard="WCAG 1.4 (best practice)",
            result="PASS",
            details=(
                "All text blocks are left-aligned — no justified or"
                " centered body text detected"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------


#: Phase 5.3's pipeline iterates this list in order. Phase 6's check
#: catalogue iterates the same list to enumerate every check name.
FONTS_CHECKS: list[Callable[[AuditContext], list[CheckResult]]] = [
    check_all_fonts_embedded,
    check_font_sizes_accessible,
    check_font_faces_readable,
    check_font_size_ratio,
    check_text_rotation_accessible,
    check_italic_text_usage,
    check_line_height_accessible,
    check_text_alignment_accessible,
]


__all__ = [
    "FONTS_CHECKS",
    "check_all_fonts_embedded",
    "check_font_faces_readable",
    "check_font_size_ratio",
    "check_font_sizes_accessible",
    "check_italic_text_usage",
    "check_line_height_accessible",
    "check_text_alignment_accessible",
    "check_text_rotation_accessible",
    "classify_font",
]
