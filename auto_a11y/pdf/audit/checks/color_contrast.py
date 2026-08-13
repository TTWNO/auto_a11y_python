"""Color contrast checks.

Two checks computed from
:attr:`auto_a11y.pdf.models.AuditContext.color_pairs` (populated by
:func:`auto_a11y.pdf.audit.colors.extract_text_colors`):

* :func:`check_text_contrast_aa` — WCAG 1.4.3 AA. Normal text needs
  >= 4.5:1; large text (>= 18pt) needs >= 3:1.
* :func:`check_text_contrast_aaa` — WCAG 1.4.6 AAA. Normal text needs
  >= 7:1; large text (>= 18pt) needs >= 4.5:1.

pdfMax never wraps its contrast computation in ``CheckResult`` calls —
its report renders contrast as a per-pair markdown table (see
``build_color_contrast_report`` at line ~8467 of pdfMax's
``pdf_accessibility_audit.py``). Phase 4 of the auto_a11y port
*introduces* deterministic CheckResults so the orchestrator can surface
contrast as a check rather than only a report section. The thresholds
match pdfMax's table verbatim; the per-pair ratio uses the same
:mod:`wcag_contrast_ratio` library pdfMax already depends on.

Bold-text detection is *not* available — pdfMax's collector tracks font
size but not weight. The "large text" threshold therefore uses size
alone (pt >= 18). When bold-aware data lands in
:mod:`auto_a11y.pdf.audit.fonts`, the >= 14 bold path can be added here
without changing the check signatures.

Mirrors the convention established in
:mod:`auto_a11y.pdf.audit.checks.headings`: each check is a plain
function ``(ctx) -> list[CheckResult]`` and the module exposes a
:data:`COLOR_CONTRAST_CHECKS` registry list.
"""
from __future__ import annotations

from collections.abc import Callable

import wcag_contrast_ratio as wcr

from auto_a11y.pdf.audit.colors import ColorPairInfo, RgbColor
from auto_a11y.pdf.models import AuditContext, CheckResult


# ---------------------------------------------------------------------------
# Module-private constants
# ---------------------------------------------------------------------------


#: Minimum font size (pt) at which "large text" thresholds apply.
#: Bold text >= 14pt is also "large" per WCAG, but our font collector
#: doesn't yet expose bold-ness; using the size-only threshold keeps the
#: check sound (it's strictly more conservative).
_LARGE_TEXT_PT: float = 18.0

#: WCAG 1.4.3 AA thresholds.
_AA_NORMAL: float = 4.5
_AA_LARGE: float = 3.0

#: WCAG 1.4.6 AAA thresholds.
_AAA_NORMAL: float = 7.0
_AAA_LARGE: float = 4.5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hex(c: RgbColor) -> str:
    """Render an (R, G, B) triple of floats in [0,1] as ``#RRGGBB``."""
    r, g, b = c
    return "#{:02X}{:02X}{:02X}".format(
        max(0, min(255, round(r * 255))),
        max(0, min(255, round(g * 255))),
        max(0, min(255, round(b * 255))),
    )


def _clamp_rgb(c: RgbColor) -> RgbColor:
    """Clamp every channel to [0.0, 1.0] for safe :func:`wcr.rgb` calls."""
    return (
        max(0.0, min(1.0, c[0])),
        max(0.0, min(1.0, c[1])),
        max(0.0, min(1.0, c[2])),
    )


def _is_large(info: ColorPairInfo) -> bool:
    """Return True when this color pair's text qualifies as "large" under WCAG."""
    return info.size_min >= _LARGE_TEXT_PT


def _ratio(fg: RgbColor, bg: RgbColor) -> float | None:
    """Compute the WCAG 2.x contrast ratio, returning ``None`` on bad input."""
    try:
        return wcr.rgb(_clamp_rgb(fg), _clamp_rgb(bg))
    except (ValueError, TypeError):
        return None


def _format_failures(
    failures: list[tuple[RgbColor, RgbColor, float, float]],
    *,
    limit: int = 3,
) -> str:
    """Render the first few failures as a short ``fg=...; bg=...; ratio=...`` list."""
    parts: list[str] = []
    for fg, bg, ratio, threshold in failures[:limit]:
        parts.append(
            f"fg={_hex(fg)} bg={_hex(bg)} ratio={ratio:.2f}"
            + f" (need {threshold:.1f})"
        )
    return "; ".join(parts)


def _no_data_result(name: str, standard: str) -> CheckResult:
    """The INFO result emitted when color data wasn't collected."""
    return CheckResult(
        name=name,
        standard=standard,
        result="INFO",
        details=(
            "Color data not collected; skipping contrast analysis."
        ),
    )


# ---------------------------------------------------------------------------
# check_text_contrast_aa
# ---------------------------------------------------------------------------


def check_text_contrast_aa(ctx: AuditContext) -> list[CheckResult]:
    """WCAG 1.4.3: text contrast >= 4.5:1 (normal) or 3:1 (large, >= 18pt).

    Reads :attr:`AuditContext.color_pairs`. Returns INFO when the
    collector hasn't run, FAIL when at least one pair is below
    threshold, PASS otherwise.
    """
    name = "Text contrast (WCAG AA)"
    standard = "WCAG 1.4.3"
    if ctx.color_pairs is None:
        return [_no_data_result(name, standard)]
    if not ctx.color_pairs:
        return [
            CheckResult(
                name=name,
                standard=standard,
                result="NA",
                details="No text colors detected — nothing to check.",
            )
        ]

    failures: list[tuple[RgbColor, RgbColor, float, float]] = []
    for (fg, bg), info in ctx.color_pairs.items():
        ratio = _ratio(fg, bg)
        if ratio is None:
            continue
        threshold = _AA_LARGE if _is_large(info) else _AA_NORMAL
        if ratio < threshold:
            failures.append((fg, bg, ratio, threshold))

    if failures:
        return [
            CheckResult(
                name=name,
                standard=standard,
                result="FAIL",
                details=(
                    f"{len(failures)} of {len(ctx.color_pairs)} color pair(s)"
                    f" below AA threshold: {_format_failures(failures)}"
                ),
            )
        ]
    return [
        CheckResult(
            name=name,
            standard=standard,
            result="PASS",
            details=(
                f"All {len(ctx.color_pairs)} color pair(s) meet"
                " AA thresholds."
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_text_contrast_aaa
# ---------------------------------------------------------------------------


def check_text_contrast_aaa(ctx: AuditContext) -> list[CheckResult]:
    """WCAG 1.4.6: text contrast >= 7:1 (normal) or 4.5:1 (large, >= 18pt).

    Reads :attr:`AuditContext.color_pairs`. Returns INFO when the
    collector hasn't run, WARN when at least one pair is below
    threshold (AAA is advisory), PASS otherwise.
    """
    name = "Text contrast (WCAG AAA)"
    standard = "WCAG 1.4.6"
    if ctx.color_pairs is None:
        return [_no_data_result(name, standard)]
    if not ctx.color_pairs:
        return [
            CheckResult(
                name=name,
                standard=standard,
                result="NA",
                details="No text colors detected — nothing to check.",
            )
        ]

    failures: list[tuple[RgbColor, RgbColor, float, float]] = []
    for (fg, bg), info in ctx.color_pairs.items():
        ratio = _ratio(fg, bg)
        if ratio is None:
            continue
        threshold = _AAA_LARGE if _is_large(info) else _AAA_NORMAL
        if ratio < threshold:
            failures.append((fg, bg, ratio, threshold))

    if failures:
        return [
            CheckResult(
                name=name,
                standard=standard,
                result="WARN",
                details=(
                    f"{len(failures)} of {len(ctx.color_pairs)} color pair(s)"
                    f" below AAA threshold: {_format_failures(failures)}"
                ),
            )
        ]
    return [
        CheckResult(
            name=name,
            standard=standard,
            result="PASS",
            details=(
                f"All {len(ctx.color_pairs)} color pair(s) meet"
                " AAA thresholds."
            ),
        )
    ]


def check_non_text_contrast(ctx: AuditContext) -> list[CheckResult]:
    """WCAG 1.4.11: form fields and graphics are distinguishable.

    Mirrors pdfMax line ~11200. Two sources feed one verdict:
    :func:`~auto_a11y.pdf.audit.non_text_contrast.extract_field_contrast`
    for form fields and
    :func:`~auto_a11y.pdf.audit.non_text_contrast.extract_graphical_contrast`
    for drawn graphics, because a form and a chart fail this criterion in
    the same way — something you have to see is not visible enough to
    see.

    A field failure counts when its border misses 3:1, or when its fill
    matches the page *and* no border rescues it. A field with no
    author-drawn border at all is neither: the collector exempts it, and
    the count of exemptions is reported rather than hidden.

    When an AI run follows, its visual pass supersedes this verdict —
    see :func:`auto_a11y.pdf.audit.ai.verdicts.derive_check_results`.
    """
    name = "Non-text contrast sufficient"
    standard = "WCAG 1.4.11"
    data = ctx.non_text_contrast
    if data is None:
        return [CheckResult(
            name=name, standard=standard, result="NA",
            details="Non-text contrast could not be measured",
        )]

    graphics = data.graphics.summary
    fields = data.fields.summary if data.fields is not None else None

    if fields is None:
        if graphics.total_fails:
            return [CheckResult(
                name=name, standard=standard, result="FAIL",
                details=(
                    f"{graphics.total_fails} graphical element(s) below 3:1"
                    " contrast"
                ),
            )]
        if graphics.total_elements:
            return [CheckResult(
                name=name, standard=standard, result="PASS",
                details=(
                    f"No form fields. {graphics.total_elements} graphical"
                    " element(s) all meet 3:1 contrast threshold"
                ),
            )]
        return [CheckResult(
            name=name, standard=standard, result="NA",
            details="No form fields or graphical elements to test",
        )]

    non_exempt = fields.total_fields - fields.exempt_count
    failures: list[str] = []
    if fields.border_fails:
        failures.append(f"{fields.border_fails} field border(s) below 3:1")
    if fields.boundary_only_fails:
        failures.append(
            f"{fields.boundary_only_fails} field(s) with no visible boundary"
        )
    if graphics.total_fails:
        failures.append(
            f"{graphics.total_fails} graphical element(s) below 3:1"
        )

    if failures:
        return [CheckResult(
            name=name, standard=standard, result="FAIL",
            details=(
                f"Non-text contrast issues: {'; '.join(failures)}."
                " See Non-text Contrast section for details"
            ),
        )]
    if non_exempt == 0 and graphics.total_elements == 0:
        return [CheckResult(
            name=name, standard=standard, result="NA",
            details=(
                f"All {fields.total_fields} field(s) are exempt (read-only or"
                " no author-drawn border) and no graphical elements were found"
            ),
        )]
    return [CheckResult(
        name=name, standard=standard, result="PASS",
        details=(
            f"All {non_exempt} non-exempt field(s) have borders/boundaries"
            + " meeting 3:1 contrast threshold"
            + (
                f"; {graphics.total_elements} graphical element(s) also pass"
                if graphics.total_elements else ""
            )
        ),
    )]


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------


#: Phase 5.3's pipeline iterates this list in order. Phase 6's check
#: catalogue iterates the same list to enumerate every check name.
COLOR_CONTRAST_CHECKS: list[Callable[[AuditContext], list[CheckResult]]] = [
    check_text_contrast_aa,
    check_text_contrast_aaa,
    check_non_text_contrast,
]


__all__ = [
    "COLOR_CONTRAST_CHECKS",
    "check_non_text_contrast",
    "check_text_contrast_aa",
    "check_text_contrast_aaa",
]
