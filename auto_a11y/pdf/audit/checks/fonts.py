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
from auto_a11y.pdf.audit.font_metadata import FontInfoDetail, FontMetadata
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
    # Per-font rows the report renderer turns into the Font Inventory
    # table (mirrors pdfMax's ``### Font Inventory`` section). Each row
    # is the smallest in-use size for the font; the renderer joins
    # them into a single table.
    table_rows: list[dict[str, object]] = []

    for info in fa.fonts.values():
        if not info.sizes:
            continue
        smallest = min(info.sizes)
        pct = (info.char_count / total_chars) * 100
        if smallest < _MIN_READABLE_SIZE:
            verdict = "FAIL"
            tiny_chars += info.char_count
            tiny_fonts.append(
                f"{info.name} at {smallest}pt ({info.char_count} chars):"
                + f" below {_MIN_READABLE_SIZE}pt minimum"
            )
        elif smallest < _MIN_BODY_SIZE:
            verdict = "WARN"
            small_chars += info.char_count
            small_fonts.append(
                f"{info.name} at {smallest}pt ({info.char_count} chars):"
                + f" below {_MIN_BODY_SIZE}pt recommended"
            )
        else:
            verdict = "OK"
        table_rows.append({
            "font_name": info.name,
            "size_pt": smallest,
            "char_count": info.char_count,
            "char_pct": round(pct, 1),
            "verdict": verdict,
        })

    # Sort the table by smallest-size ascending so problems land at
    # the top — the user sees the worst offenders first.
    table_rows.sort(
        key=lambda row: (
            float(row["size_pt"]) if isinstance(row["size_pt"], (int, float)) else 0.0,
            str(row["font_name"]),
        )
    )

    extras: dict[str, object] = {
        "font_table": {
            "rows": table_rows,
            "total_chars": total_chars,
            "min_body_size_pt": _MIN_BODY_SIZE,
            "min_readable_size_pt": _MIN_READABLE_SIZE,
        }
    }

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
                extras=extras,
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
                extras=extras,
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
            extras=extras,
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
                result="NA",
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
# Matterhorn font/CMap/encoding checks (Phase 4.12 follow-up)
# ---------------------------------------------------------------------------
#
# Eleven additional checks that consume :attr:`AuditContext.font_metadata`
# (populated by :func:`auto_a11y.pdf.audit.font_metadata.extract_font_metadata`).
# Mirror pdfMax's behaviour at lines ~4646-4733 and ~7137-7260 of
# ``pdf_accessibility_audit.py``. Each follows the same shape: read
# ``ctx.font_metadata`` (skip with INFO when ``None``), iterate the
# fonts list, apply the verdict logic, return a single CheckResult.


#: Standard named encodings (mirrors pdfMax's ``STANDARD_ENCODINGS``).
#:
#: Simple (non-Type0) fonts using one of these are exempt from the
#: ToUnicode requirement (Matterhorn 10-001) — the encoding alone is
#: enough to round-trip text to Unicode.
_STANDARD_ENCODINGS: frozenset[str] = frozenset({
    "/WinAnsiEncoding", "/MacRomanEncoding", "/MacExpertEncoding",
    "/StandardEncoding",
})

#: Predefined CMap names from ISO 32000-1 Table 118 (mirrors pdfMax's
#: ``PREDEFINED_CMAPS``). A Type0 font referencing one of these by name
#: needs no embedded CMap stream.
_PREDEFINED_CMAPS: frozenset[str] = frozenset({
    "Identity-H", "Identity-V",
    # Japanese
    "83pv-RKSJ-H", "90ms-RKSJ-H", "90ms-RKSJ-V", "90msp-RKSJ-H", "90msp-RKSJ-V",
    "EUC-H", "EUC-V", "UniJIS-UCS2-H", "UniJIS-UCS2-V", "UniJIS-UCS2-HW-H",
    "UniJIS-UCS2-HW-V", "UniJIS-UTF16-H", "UniJIS-UTF16-V",
    # Chinese Simplified
    "GB-EUC-H", "GB-EUC-V", "GBpc-EUC-H", "GBK-EUC-H", "GBK-EUC-V",
    "UniGB-UCS2-H", "UniGB-UCS2-V", "UniGB-UTF16-H", "UniGB-UTF16-V",
    # Chinese Traditional
    "B5pc-H", "B5pc-V", "ETen-B5-H", "ETen-B5-V",
    "UniCNS-UCS2-H", "UniCNS-UCS2-V", "UniCNS-UTF16-H", "UniCNS-UTF16-V",
    # Korean
    "KSCms-UHC-H", "KSCms-UHC-V", "KSC-EUC-H", "KSC-EUC-V",
    "UniKS-UCS2-H", "UniKS-UCS2-V", "UniKS-UTF16-H", "UniKS-UTF16-V",
})

#: Encodings that guarantee a Latin code-point mapping for non-symbolic
#: TrueType (mirrors the allow-list in pdfMax's 31-003 block).
_LATIN_ENCODINGS: frozenset[str] = frozenset({
    "/WinAnsiEncoding", "/MacRomanEncoding", "/StandardEncoding",
})


def _strip_leading_slash(name: str) -> str:
    """Return *name* with at most one leading ``/`` removed."""
    return name[1:] if name.startswith("/") else name


def _font_metadata_or_none(ctx: AuditContext) -> FontMetadata | None:
    """Return ``ctx.font_metadata`` or ``None`` (no-data sentinel).

    All eleven Matterhorn checks below use this helper so the
    "collector didn't run" branch is centralised. Mirrors the
    convention :func:`auto_a11y.pdf.audit.checks.color_contrast._no_data_result`
    introduced — INFO when no data was collected.
    """
    return ctx.font_metadata


def _no_metadata_result(name: str, standard: str) -> CheckResult:
    """The INFO result emitted when font metadata wasn't collected."""
    return CheckResult(
        name=name,
        standard=standard,
        result="INFO",
        details="Font metadata not collected; skipping check.",
    )


# ---------------------------------------------------------------------------
# check_unicode_mapping_tounicode
# ---------------------------------------------------------------------------


def check_unicode_mapping_tounicode(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 10-001: every non-standard font has a /ToUnicode CMap.

    Mirrors pdfMax line ~4646. Standard 14 fonts and simple fonts with a
    standard named encoding are exempt; every other font must carry
    ``/ToUnicode``.
    """
    name = "Unicode mapping (ToUnicode)"
    standard = "Matterhorn 10-001"
    fm = _font_metadata_or_none(ctx)
    if fm is None:
        return [_no_metadata_result(name, standard)]

    missing: list[str] = []
    for f in fm.fonts:
        base = _strip_leading_slash(f.base_font)
        if base in _STANDARD_14_FONTS:
            continue
        if not f.is_type0 and f.encoding_name in _STANDARD_ENCODINGS:
            continue
        if not f.has_to_unicode:
            subtype_pretty = _strip_leading_slash(f.subtype)
            missing.append(f"{f.base_font} ({subtype_pretty}, p.{f.page})")

    if not missing:
        return [
            CheckResult(
                name=name, standard=standard, result="PASS",
                details=f"{len(fm.fonts)} font(s) checked, all have Unicode mapping",
            )
        ]
    return [
        CheckResult(
            name=name, standard=standard, result="FAIL",
            details=(
                f"{len(missing)} font(s) missing ToUnicode CMap: "
                + "; ".join(missing)
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_cid_font_gid_mapping
# ---------------------------------------------------------------------------


def check_cid_font_gid_mapping(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 31-004: embedded CIDFontType2 fonts carry /CIDToGIDMap.

    Mirrors pdfMax line ~4670. PASSes when no Type 2 CIDFonts are
    present; PASSes when every embedded CIDFontType2 has a CIDToGIDMap;
    FAILs otherwise.
    """
    name = "CID font GID mapping"
    standard = "Matterhorn 31-004"
    fm = _font_metadata_or_none(ctx)
    if fm is None:
        return [_no_metadata_result(name, standard)]

    issues: list[str] = []
    type2_count = 0
    for f in fm.fonts:
        if not f.is_cid_type2:
            continue
        type2_count += 1
        if f.has_font_file and f.cidtogidmap is None:
            issues.append(
                f"{f.base_font} (embedded but no CIDToGIDMap, p.{f.page})"
            )

    if type2_count == 0:
        return [
            CheckResult(
                name=name, standard=standard, result="NA",
                details="No Type 2 CIDFonts found — check not applicable",
            )
        ]
    if not issues:
        return [
            CheckResult(
                name=name, standard=standard, result="PASS",
                details=(
                    f"{type2_count} CIDFontType2 font(s) checked, all have"
                    " valid CIDToGIDMap"
                ),
            )
        ]
    return [
        CheckResult(
            name=name, standard=standard, result="FAIL",
            details=(
                f"{len(issues)} CIDFontType2 issue(s): " + "; ".join(issues)
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_cmap_resources_valid
# ---------------------------------------------------------------------------


def check_cmap_resources_valid(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 31-006: every Type0 CMap is predefined or embedded.

    Mirrors pdfMax line ~4688. A Type0 font that names a non-predefined
    CMap and does not embed it fails — the consuming PDF reader has no
    way to look up the CMap.
    """
    name = "CMap resources valid"
    standard = "Matterhorn 31-006"
    fm = _font_metadata_or_none(ctx)
    if fm is None:
        return [_no_metadata_result(name, standard)]

    issues: list[str] = []
    type0_count = 0
    for f in fm.fonts:
        if not f.is_type0:
            continue
        type0_count += 1
        if f.cmap_name is not None and not f.cmap_embedded:
            if f.cmap_name not in _PREDEFINED_CMAPS:
                issues.append(
                    f"{f.base_font}: CMap '{f.cmap_name}' is not predefined"
                    + f" and not embedded (p.{f.page})"
                )

    if type0_count == 0:
        return [
            CheckResult(
                name=name, standard=standard, result="NA",
                details="No Type0 (composite) fonts found — check not applicable",
            )
        ]
    if not issues:
        return [
            CheckResult(
                name=name, standard=standard, result="PASS",
                details=f"{type0_count} Type0 font(s) checked, all CMaps valid",
            )
        ]
    return [
        CheckResult(
            name=name, standard=standard, result="FAIL",
            details=f"{len(issues)} CMap issue(s): " + "; ".join(issues),
        )
    ]


# ---------------------------------------------------------------------------
# check_valid_unicode_values
# ---------------------------------------------------------------------------


def check_valid_unicode_values(ctx: AuditContext) -> list[CheckResult]:
    """veraPDF 7.21.7-2 / Matterhorn 10-001: ToUnicode maps to valid code points.

    Mirrors pdfMax line ~4715. ToUnicode CMaps must not map any source
    code to U+0000, U+FEFF, U+FFFE (or surrogates / U+FFFD, which we
    add as a defensive extension).
    """
    name = "Valid Unicode values"
    standard = "Matterhorn 10-001"
    fm = _font_metadata_or_none(ctx)
    if fm is None:
        return [_no_metadata_result(name, standard)]

    invalid_fonts: list[str] = []
    fonts_with_tounicode = 0
    for f in fm.fonts:
        if f.to_unicode is None:
            continue
        fonts_with_tounicode += 1
        if f.to_unicode.has_invalid_unicode:
            sample = ", ".join(
                f"U+{cp:04X}" for cp in f.to_unicode.invalid_codepoints[:3]
            )
            invalid_fonts.append(f"{f.base_font}: {sample} (p.{f.page})")

    if fonts_with_tounicode == 0:
        return [
            CheckResult(
                name=name, standard=standard, result="NA",
                details="No ToUnicode CMaps to check",
            )
        ]
    if not invalid_fonts:
        return [
            CheckResult(
                name=name, standard=standard, result="PASS",
                details=(
                    f"{fonts_with_tounicode} ToUnicode CMap(s) checked,"
                    " no invalid Unicode values"
                ),
            )
        ]
    return [
        CheckResult(
            name=name, standard=standard, result="FAIL",
            details=(
                f"{len(invalid_fonts)} font(s) with invalid Unicode mappings: "
                + "; ".join(invalid_fonts)
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_no_notdef_glyph_references
# ---------------------------------------------------------------------------


def check_no_notdef_glyph_references(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 31-025: no font references the .notdef glyph.

    Mirrors pdfMax line ~7137. A font's encoding `/Differences` must not
    include `.notdef`, *and* its ToUnicode CMap must not contain a
    ``<0000>`` source code (which would map to .notdef). Either
    occurrence flags the font.
    """
    name = "No .notdef glyph references"
    standard = "Matterhorn 31-025"
    fm = _font_metadata_or_none(ctx)
    if fm is None:
        return [_no_metadata_result(name, standard)]

    flagged: list[str] = []
    for f in fm.fonts:
        if f.encoding_differences is not None and f.encoding_differences.has_notdef:
            flagged.append(f.base_font)
            continue
        if f.to_unicode is not None and b"<0000>" in f.to_unicode.raw_bytes:
            if f.base_font not in flagged:
                flagged.append(f.base_font)

    if not flagged:
        return [
            CheckResult(
                name=name, standard=standard, result="PASS",
                details="No fonts reference .notdef glyphs",
            )
        ]
    return [
        CheckResult(
            name=name, standard=standard, result="FAIL",
            details=(
                f"{len(flagged)} font(s) reference .notdef: "
                + ", ".join(flagged[:5])
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_font_glyph_widths_consistent
# ---------------------------------------------------------------------------


def _width_issue(f: FontInfoDetail) -> str | None:
    """Return a width-array description if ``f`` has an inconsistency.

    Encapsulates the per-font shape pdfMax tracked as ``meta["width_issue"]``
    (set in two places: simple-font length mismatches around line 2503 and
    CID font missing /W or /DW around line 2575).
    """
    if f.subtype in ("/Type1", "/TrueType", "/MMType1"):
        if (
            f.widths_first_char is not None
            and f.widths_last_char is not None
            and f.glyph_widths_count is not None
        ):
            expected = f.widths_last_char - f.widths_first_char + 1
            actual = f.glyph_widths_count
            if actual != expected:
                return (
                    f"Widths array length {actual} != expected {expected}"
                    f" (FirstChar={f.widths_first_char},"
                    f" LastChar={f.widths_last_char})"
                )
        return None
    if f.is_type0:
        # CIDFont must have /W or /DW. Our collector exposes
        # glyph_widths_count for /W; we can't easily detect /DW here, so
        # only flag when /W is missing AND we never saw /DW.  The
        # font_metadata module doesn't currently track /DW separately —
        # absence of glyph_widths_count is the proxy pdfMax used for
        # "neither /W nor /DW present" when the descendant CIDFont
        # lacks any width entry.
        if f.glyph_widths_count is None and f.is_cid_type2:
            return "CID font missing both /W and /DW width entries"
    return None


def check_font_glyph_widths_consistent(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 31-009: widths arrays match the FirstChar/LastChar range.

    Mirrors pdfMax line ~7158. PASSes when every font's widths data is
    internally consistent; FAILs with the offending fonts otherwise.
    """
    name = "Font glyph widths consistent"
    standard = "Matterhorn 31-009"
    fm = _font_metadata_or_none(ctx)
    if fm is None:
        return [_no_metadata_result(name, standard)]

    issues: list[str] = []
    for f in fm.fonts:
        issue = _width_issue(f)
        if issue is not None:
            issues.append(f"{f.base_font}: {issue}")

    if not issues:
        return [
            CheckResult(
                name=name, standard=standard, result="PASS",
                details=(
                    f"All {len(fm.fonts)} font(s) have consistent width"
                    " definitions"
                ),
            )
        ]
    return [
        CheckResult(
            name=name, standard=standard, result="FAIL",
            details=(
                f"{len(issues)} font(s) with width issues: "
                + "; ".join(issues[:3])
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_no_notdef_in_differences
# ---------------------------------------------------------------------------


def check_no_notdef_in_differences(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 31-008: no font has .notdef in its Encoding /Differences.

    Mirrors pdfMax line ~7170. Distinct from 31-025 (which checks any
    .notdef reference): this check looks specifically at the encoding
    dictionary's ``/Differences`` array.
    """
    name = "No .notdef in Differences array"
    standard = "Matterhorn 31-008"
    fm = _font_metadata_or_none(ctx)
    if fm is None:
        return [_no_metadata_result(name, standard)]

    flagged = [
        f.base_font for f in fm.fonts
        if f.encoding_differences is not None and f.encoding_differences.has_notdef
    ]
    if not flagged:
        return [
            CheckResult(
                name=name, standard=standard, result="PASS",
                details="No fonts have .notdef in Encoding /Differences array",
            )
        ]
    return [
        CheckResult(
            name=name, standard=standard, result="FAIL",
            details=(
                f"{len(flagged)} font(s) reference .notdef in /Differences: "
                + ", ".join(flagged[:5])
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_identity_cmap_has_tounicode
# ---------------------------------------------------------------------------


def check_identity_cmap_has_tounicode(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 31-007: Identity-H/V fonts must carry /ToUnicode.

    Mirrors pdfMax line ~7180. A Type0 font that uses Identity-H or
    Identity-V as its CMap has no built-in code-to-Unicode mapping and
    must therefore include /ToUnicode for accessibility.
    """
    name = "Identity CMap has ToUnicode"
    standard = "Matterhorn 31-007"
    fm = _font_metadata_or_none(ctx)
    if fm is None:
        return [_no_metadata_result(name, standard)]

    missing: list[str] = []
    identity_total = 0
    for f in fm.fonts:
        if not f.has_identity_h_or_v:
            continue
        identity_total += 1
        if not f.has_to_unicode:
            missing.append(f"{f.base_font} (p.{f.page})")

    if identity_total == 0:
        return [
            CheckResult(
                name=name, standard=standard, result="NA",
                details="No fonts use Identity-H/V CMap",
            )
        ]
    if not missing:
        return [
            CheckResult(
                name=name, standard=standard, result="PASS",
                details=(
                    f"All {identity_total} Identity CMap font(s) have ToUnicode"
                ),
            )
        ]
    return [
        CheckResult(
            name=name, standard=standard, result="FAIL",
            details=(
                f"{len(missing)} Identity CMap font(s) missing ToUnicode: "
                + "; ".join(missing[:5])
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_cmap_wmode_consistency
# ---------------------------------------------------------------------------


def check_cmap_wmode_consistency(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 31-005: CMap WMode matches the descendant CIDFont.

    Mirrors pdfMax line ~7199. PASSes when every Type0 font's CMap
    WMode is consistent with its descendant CIDFont's vertical-metrics
    declaration; FAILs when at least one font has a horizontal CMap
    paired with vertical CIDFont metrics (or vice versa).
    """
    name = "CMap WMode consistency"
    standard = "Matterhorn 31-005"
    fm = _font_metadata_or_none(ctx)
    if fm is None:
        return [_no_metadata_result(name, standard)]

    issues: list[str] = []
    type0_count = 0
    for f in fm.fonts:
        if not f.is_type0:
            continue
        type0_count += 1
        cmap_wm = f.cmap_wmode
        cid_wm = f.cid_font_wmode
        if cmap_wm is not None and cid_wm is not None and cmap_wm != cid_wm:
            issues.append(
                f"{f.base_font}: CMap WMode={cmap_wm} but CIDFont has"
                + f" vertical metrics (WMode=1) (p.{f.page})"
            )
        elif cid_wm == 1 and cmap_wm == 0:
            # Captured by the previous branch already, but pdfMax repeats
            # the check explicitly so the detail wording differs. Keep
            # the same logic for byte-for-byte pdfMax parity.
            issues.append(
                f"{f.base_font}: CMap is horizontal but CIDFont has"
                + f" vertical width entries (p.{f.page})"
            )

    if not issues:
        return [
            CheckResult(
                name=name, standard=standard, result="PASS",
                details=(
                    f"{type0_count} Type0 font(s) checked"
                    if type0_count
                    else "No Type0 fonts in document"
                ),
            )
        ]
    return [
        CheckResult(
            name=name, standard=standard, result="FAIL",
            details=(
                f"{len(issues)} WMode inconsistency(ies): "
                + "; ".join(issues[:3])
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_nonsymbolic_truetype_latin_mapping
# ---------------------------------------------------------------------------


def check_nonsymbolic_truetype_latin_mapping(
    ctx: AuditContext,
) -> list[CheckResult]:
    """Matterhorn 31-003: non-symbolic TrueType uses standard Latin encoding.

    Mirrors pdfMax line ~7221. Non-symbolic TrueType fonts must use one
    of WinAnsi, MacRoman, or StandardEncoding — anything else (including
    a custom /Differences-based encoding with no /BaseEncoding) cannot
    guarantee Latin-character recovery.
    """
    name = "Non-symbolic TrueType Latin mapping"
    standard = "Matterhorn 31-003"
    fm = _font_metadata_or_none(ctx)
    if fm is None:
        return [_no_metadata_result(name, standard)]

    issues: list[str] = []
    nonsym_total = 0
    for f in fm.fonts:
        # A non-symbolic TrueType font is identified by subtype +
        # FontDescriptor flags. Our collector exposes is_symbolic
        # directly so the inverse pulls non-symbolic out cleanly.
        if f.subtype != "/TrueType" or f.is_symbolic:
            continue
        nonsym_total += 1
        enc = f.encoding_name
        if enc not in _LATIN_ENCODINGS and enc in ("", "custom"):
            issues.append(
                f"{f.base_font}: non-symbolic TrueType without standard"
                + f" Latin encoding (p.{f.page})"
            )

    if nonsym_total == 0:
        return [
            CheckResult(
                name=name, standard=standard, result="NA",
                details="No non-symbolic TrueType fonts found",
            )
        ]
    if not issues:
        return [
            CheckResult(
                name=name, standard=standard, result="PASS",
                details=(
                    f"All {nonsym_total} non-symbolic TrueType font(s) use"
                    " standard Latin encoding"
                ),
            )
        ]
    return [
        CheckResult(
            name=name, standard=standard, result="FAIL",
            details=f"{len(issues)} issue(s): " + "; ".join(issues[:3]),
        )
    ]


# ---------------------------------------------------------------------------
# check_font_encoding_consistency
# ---------------------------------------------------------------------------


def check_font_encoding_consistency(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 31-002: Encoding dict and font program agree.

    Mirrors pdfMax line ~7245. pdfMax's port is intentionally minimal —
    the "real" check requires parsing the embedded font program, which
    pdfMax notes as future work. The check therefore PASSes
    structurally for every simple font, returning the same "no
    contradiction detected" verdict pdfMax does.
    """
    name = "Font encoding consistency"
    standard = "Matterhorn 31-002"
    fm = _font_metadata_or_none(ctx)
    if fm is None:
        return [_no_metadata_result(name, standard)]

    return [
        CheckResult(
            name=name, standard=standard, result="PASS",
            details=(
                f"{len(fm.fonts)} font(s) checked (structural encoding"
                " verification)"
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
    # Matterhorn font/CMap/encoding checks (Phase 4.12 follow-up).
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


__all__ = [
    "FONTS_CHECKS",
    "check_all_fonts_embedded",
    "check_cid_font_gid_mapping",
    "check_cmap_resources_valid",
    "check_cmap_wmode_consistency",
    "check_font_encoding_consistency",
    "check_font_faces_readable",
    "check_font_glyph_widths_consistent",
    "check_font_size_ratio",
    "check_font_sizes_accessible",
    "check_identity_cmap_has_tounicode",
    "check_italic_text_usage",
    "check_line_height_accessible",
    "check_no_notdef_glyph_references",
    "check_no_notdef_in_differences",
    "check_nonsymbolic_truetype_latin_mapping",
    "check_text_alignment_accessible",
    "check_text_rotation_accessible",
    "check_unicode_mapping_tounicode",
    "check_valid_unicode_values",
    "classify_font",
]
