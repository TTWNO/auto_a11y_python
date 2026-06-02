"""Audit regression tests for the forms touchpoint parse helpers.

Covers fixes from a code audit:
- Bug A: ``rem`` CSS values previously parsed to 0 in ``parse_px``.
- Bug B: 3-digit hex / named colours previously fell back to opaque black
  in ``parse_color``.
- Bug C: bare ``except:`` clauses narrowed to ``(ValueError, TypeError)``.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from pathlib import Path
from typing import cast

# ``auto_a11y.testing`` and ``auto_a11y.testing.touchpoint_tests`` package
# __init__ files eagerly import a heavy (circular) chain (TestRunner -> web ->
# core -> ...). The forms module itself only depends on stdlib + playwright, so
# we load it directly by file path to test the pure parse helpers in isolation.
_MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "auto_a11y"
    / "testing"
    / "touchpoint_tests"
    / "test_forms.py"
)
_spec = importlib.util.spec_from_file_location("_forms_under_test", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
_forms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_forms)

parse_px = cast(Callable[[str | None], float], _forms.parse_px)
parse_color = cast(
    Callable[[str | None], dict[str, float]], _forms.parse_color
)


class TestParsePx:
    def test_rem_is_not_zero(self) -> None:
        # Bug A: '2rem' must be 32px (16 * 2), not 0.
        assert parse_px('2rem') == 32.0

    def test_rem_fractional(self) -> None:
        assert parse_px('1.5rem') == 24.0

    def test_em(self) -> None:
        assert parse_px('1.5em') == 24.0

    def test_px(self) -> None:
        assert parse_px('16px') == 16.0

    def test_bare_number(self) -> None:
        assert parse_px('4') == 4.0

    def test_fractional_px(self) -> None:
        assert parse_px('0.5px') == 0.5

    def test_empty_and_auto(self) -> None:
        assert parse_px(None) == 0
        assert parse_px('') == 0
        assert parse_px('auto') == 0

    def test_unparseable(self) -> None:
        assert parse_px('not-a-length') == 0


class TestParseColor:
    def test_three_digit_hex_expands(self) -> None:
        # Bug B: '#abc' must expand to #aabbcc, not fall back to black.
        assert parse_color('#abc') == {'r': 0xAA, 'g': 0xBB, 'b': 0xCC, 'a': 1.0}

    def test_six_digit_hex(self) -> None:
        assert parse_color('#ff8000') == {'r': 255, 'g': 128, 'b': 0, 'a': 1.0}

    def test_eight_digit_hex_alpha(self) -> None:
        result = parse_color('#ff000080')
        assert result['r'] == 255
        assert result['g'] == 0
        assert result['b'] == 0
        assert abs(result['a'] - (0x80 / 255.0)) < 1e-9

    def test_rgb_unchanged(self) -> None:
        assert parse_color('rgb(12, 34, 56)') == {'r': 12, 'g': 34, 'b': 56, 'a': 1.0}

    def test_rgba_alpha(self) -> None:
        assert parse_color('rgba(10, 20, 30, 0.5)') == {'r': 10, 'g': 20, 'b': 30, 'a': 0.5}

    def test_named_white(self) -> None:
        assert parse_color('white') == {'r': 255, 'g': 255, 'b': 255, 'a': 1.0}

    def test_named_black(self) -> None:
        assert parse_color('black') == {'r': 0, 'g': 0, 'b': 0, 'a': 1.0}

    def test_named_red_case_insensitive(self) -> None:
        assert parse_color('Red') == {'r': 255, 'g': 0, 'b': 0, 'a': 1.0}

    def test_none_defaults_black(self) -> None:
        assert parse_color(None) == {'r': 0, 'g': 0, 'b': 0, 'a': 1}

    def test_unknown_defaults_black(self) -> None:
        # Truly unparseable input documents-as-black fallback (dict contract).
        assert parse_color('chartreuse-ish-not-real') == {'r': 0, 'g': 0, 'b': 0, 'a': 1}
