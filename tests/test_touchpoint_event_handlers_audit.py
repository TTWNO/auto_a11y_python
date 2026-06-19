"""Audit-driven tests for parse helpers in the event_handlers touchpoint.

Covers two bugs found during a code audit:

- Bug A: `rem` CSS values parsed to 0 because `em` was stripped before `rem`.
- Bug B: `parse_color` fell back to opaque black for 3-digit hex / named colors.

The helpers are private module-level functions; we fetch them via a typed
``getattr`` to avoid pyright ``reportPrivateUsage`` warnings.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Callable

# Load the module directly by file path. Importing it through the package
# (``auto_a11y.testing.touchpoint_tests``) drags in the package ``__init__``
# chain, which has a circular import unrelated to these pure helpers.
_MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "auto_a11y"
    / "testing"
    / "touchpoint_tests"
    / "test_event_handlers.py"
)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_eh_audit_under_test", _MODULE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


eh = _load_module()

parse_px: Callable[[str | None], float] = getattr(eh, "_parse_px")
parse_color: Callable[[str | None], dict[str, float]] = getattr(eh, "_parse_color")


# --- Bug A: parse_px -------------------------------------------------------


def test_parse_px_rem_is_not_zero() -> None:
    assert parse_px("2rem") == 2.0


def test_parse_px_px_value() -> None:
    assert parse_px("3px") == 3.0


def test_parse_px_em_value() -> None:
    assert parse_px("1.5em") == 1.5


def test_parse_px_bare_number() -> None:
    assert parse_px("4") == 4.0


def test_parse_px_none_and_empty() -> None:
    assert parse_px(None) == 0
    assert parse_px("") == 0


def test_parse_px_garbage() -> None:
    assert parse_px("auto") == 0


# --- Bug B: parse_color ----------------------------------------------------


def test_parse_color_3_digit_hex_expands() -> None:
    # #abc -> #aabbcc
    assert parse_color("#abc") == {"r": 0xAA, "g": 0xBB, "b": 0xCC, "a": 1.0}


def test_parse_color_3_digit_hex_white() -> None:
    assert parse_color("#fff") == {"r": 255, "g": 255, "b": 255, "a": 1.0}


def test_parse_color_6_digit_hex_unchanged() -> None:
    assert parse_color("#ff0000") == {"r": 255, "g": 0, "b": 0, "a": 1.0}


def test_parse_color_rgb_unchanged() -> None:
    assert parse_color("rgb(10, 20, 30)") == {"r": 10, "g": 20, "b": 30, "a": 1.0}


def test_parse_color_rgba_unchanged() -> None:
    assert parse_color("rgba(10, 20, 30, 0.5)") == {
        "r": 10,
        "g": 20,
        "b": 30,
        "a": 0.5,
    }


def test_parse_color_named_color() -> None:
    assert parse_color("white") == {"r": 255, "g": 255, "b": 255, "a": 1.0}


def test_parse_color_transparent() -> None:
    assert parse_color("transparent") == {"r": 0, "g": 0, "b": 0, "a": 0}


def test_parse_color_unknown_falls_back_to_black() -> None:
    # Truly unknown input documents the opaque-black fallback.
    assert parse_color("not-a-color") == {"r": 0, "g": 0, "b": 0, "a": 1}
