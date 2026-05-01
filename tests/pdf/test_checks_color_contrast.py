"""Tests for ``auto_a11y.pdf.audit.checks.color_contrast``.

Two contrast checks computed off
:attr:`AuditContext.color_pairs` (populated by
:func:`auto_a11y.pdf.audit.colors.extract_text_colors`):

* ``check_text_contrast_aa`` — WCAG 1.4.3 (4.5:1 / 3:1).
* ``check_text_contrast_aaa`` — WCAG 1.4.6 (7:1 / 4.5:1).
"""
from __future__ import annotations

from pathlib import Path

import pikepdf

from auto_a11y.pdf.audit.checks.color_contrast import (
    COLOR_CONTRAST_CHECKS,
    check_text_contrast_aa,
    check_text_contrast_aaa,
)
from auto_a11y.pdf.audit.colors import ColorPairInfo, RgbColor
from auto_a11y.pdf.models import AuditContext, CheckResult


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _ctx(
    color_pairs: dict[tuple[RgbColor, RgbColor], ColorPairInfo] | None,
) -> AuditContext:
    """Build an ``AuditContext`` with only ``color_pairs`` populated."""
    return AuditContext(
        pdf=pikepdf.Pdf.new(),
        pdf_path=Path("/tmp/test.pdf"),
        elements=[],
        role_map={},
        color_pairs=color_pairs,
    )


def _info(*, size_min: float = 12.0, size_max: float = 12.0) -> ColorPairInfo:
    """Build a ``ColorPairInfo`` carrying the given size range."""
    info = ColorPairInfo(count=1, sample="x")
    info.size_min = size_min
    info.size_max = size_max
    return info


def _only(results: list[CheckResult]) -> CheckResult:
    assert len(results) == 1, f"expected one CheckResult, got {len(results)}"
    return results[0]


_BLACK: RgbColor = (0.0, 0.0, 0.0)
_WHITE: RgbColor = (1.0, 1.0, 1.0)
# A mid-gray that's well below both AA and AAA thresholds against white.
_LIGHT_GRAY: RgbColor = (0.8, 0.8, 0.8)
# A darker gray ~5.7:1 against white — passes AA normal, fails AAA normal.
_DARK_GRAY: RgbColor = (0.4, 0.4, 0.4)


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------


def test_color_contrast_checks_registry_lists_both() -> None:
    assert COLOR_CONTRAST_CHECKS == [
        check_text_contrast_aa,
        check_text_contrast_aaa,
    ]


# ---------------------------------------------------------------------------
# check_text_contrast_aa
# ---------------------------------------------------------------------------


def test_aa_info_when_color_pairs_unset() -> None:
    res = _only(check_text_contrast_aa(_ctx(None)))
    assert res.result == "INFO"
    assert res.standard == "WCAG 1.4.3"
    assert "not collected" in res.details


def test_aa_pass_when_color_pairs_empty() -> None:
    res = _only(check_text_contrast_aa(_ctx({})))
    assert res.result == "PASS"


def test_aa_pass_when_black_on_white() -> None:
    pairs = {(_BLACK, _WHITE): _info()}
    res = _only(check_text_contrast_aa(_ctx(pairs)))
    assert res.result == "PASS"
    assert "1 color pair(s) meet AA thresholds" in res.details


def test_aa_fail_when_pair_below_threshold() -> None:
    # Light gray on white -> ~1.25:1, well under 4.5:1.
    pairs = {(_LIGHT_GRAY, _WHITE): _info()}
    res = _only(check_text_contrast_aa(_ctx(pairs)))
    assert res.result == "FAIL"
    assert "below AA threshold" in res.details


def test_aa_uses_large_text_threshold_at_18pt() -> None:
    # A pair between the large-text (3:1) and normal-text (4.5:1)
    # AA thresholds: with size_min >= 18 it should PASS, otherwise FAIL.
    # (0.5, 0.5, 0.5) on white is ~3.98:1.
    grayish: RgbColor = (0.5, 0.5, 0.5)
    pairs_large = {(grayish, _WHITE): _info(size_min=18.0, size_max=18.0)}
    pairs_small = {(grayish, _WHITE): _info(size_min=12.0, size_max=12.0)}
    assert _only(check_text_contrast_aa(_ctx(pairs_large))).result == "PASS"
    assert _only(check_text_contrast_aa(_ctx(pairs_small))).result == "FAIL"


# ---------------------------------------------------------------------------
# check_text_contrast_aaa
# ---------------------------------------------------------------------------


def test_aaa_info_when_color_pairs_unset() -> None:
    res = _only(check_text_contrast_aaa(_ctx(None)))
    assert res.result == "INFO"
    assert res.standard == "WCAG 1.4.6"


def test_aaa_pass_when_black_on_white() -> None:
    pairs = {(_BLACK, _WHITE): _info()}
    res = _only(check_text_contrast_aaa(_ctx(pairs)))
    assert res.result == "PASS"


def test_aaa_warn_when_pair_below_threshold() -> None:
    # Dark gray on white is ~5.7:1 — passes AA, fails AAA normal (>=7).
    pairs = {(_DARK_GRAY, _WHITE): _info()}
    res = _only(check_text_contrast_aaa(_ctx(pairs)))
    assert res.result == "WARN"
    assert "below AAA threshold" in res.details


def test_aaa_pass_when_large_text_meets_relaxed_threshold() -> None:
    # Dark gray at 18pt+ — large-text AAA threshold is 4.5:1, dark gray
    # meets that.
    pairs = {(_DARK_GRAY, _WHITE): _info(size_min=18.0, size_max=24.0)}
    res = _only(check_text_contrast_aaa(_ctx(pairs)))
    assert res.result == "PASS"
