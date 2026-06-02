"""Audit regression tests for auto_a11y.testing.touchpoint_tests.test_buttons.

Covers two low-severity audit findings:

* Bug A: bare ``except:`` clauses in the numeric/colour parse helpers (caught
  ``KeyboardInterrupt``/``SystemExit``) were narrowed to concrete exception types.
* Bug B: ``parse_color`` only understood ``rgb()``/``rgba()`` and 6-digit hex,
  silently mapping 3-digit hex and named colours to opaque black (which can flip
  a contrast calculation). 3-digit hex expansion + common named colours added.

The parse helpers were closures local to ``test_buttons`` and are now exposed at
module scope so they can be unit-tested.
"""

from __future__ import annotations

import importlib.util
import pathlib
from types import ModuleType
from typing import Callable


def _load_module() -> ModuleType:
    """Load test_buttons directly from its file.

    Importing ``auto_a11y.testing.touchpoint_tests.test_buttons`` through the
    package triggers a pre-existing circular import via
    ``auto_a11y.testing.__init__`` -> ``test_runner`` -> web app. The module
    itself only depends on stdlib + playwright, so we load it straight from
    its file location to exercise the parse helpers in isolation.
    """
    here = pathlib.Path(__file__).resolve().parent.parent
    path = here / "auto_a11y" / "testing" / "touchpoint_tests" / "test_buttons.py"
    spec = importlib.util.spec_from_file_location("_test_buttons_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load_module()


def _get(name: str) -> Callable[..., object]:
    fn: object = getattr(mod, name)
    assert callable(fn), f"{name} is not callable"
    typed_fn: Callable[..., object] = fn
    return typed_fn


def test_parse_color_three_digit_hex_expands() -> None:
    parse_color = _get("parse_color")
    # #abc -> #aabbcc -> (170, 187, 204), fully opaque
    result = parse_color("#abc")
    assert result == {"r": 170, "g": 187, "b": 204, "a": 1.0}


def test_parse_color_three_digit_hex_white_is_not_black() -> None:
    parse_color = _get("parse_color")
    # #fff must be white, NOT the old opaque-black fallback.
    result = parse_color("#fff")
    assert result == {"r": 255, "g": 255, "b": 255, "a": 1.0}


def test_parse_color_six_digit_hex_unchanged() -> None:
    parse_color = _get("parse_color")
    assert parse_color("#ff8000") == {"r": 255, "g": 128, "b": 0, "a": 1.0}


def test_parse_color_rgb_unchanged() -> None:
    parse_color = _get("parse_color")
    assert parse_color("rgb(12, 34, 56)") == {"r": 12, "g": 34, "b": 56, "a": 1.0}


def test_parse_color_rgba_unchanged() -> None:
    parse_color = _get("parse_color")
    result = parse_color("rgba(0, 0, 0, 0.5)")
    assert result == {"r": 0, "g": 0, "b": 0, "a": 0.5}


def test_parse_color_named_colour_white_not_black() -> None:
    parse_color = _get("parse_color")
    # A named colour must not collapse to opaque black.
    assert parse_color("white") == {"r": 255, "g": 255, "b": 255, "a": 1.0}


def test_parse_color_transparent() -> None:
    parse_color = _get("parse_color")
    assert parse_color("transparent") == {"r": 0, "g": 0, "b": 0, "a": 0}


def test_parse_color_unknown_keeps_fallback() -> None:
    parse_color = _get("parse_color")
    # Truly unknown input still returns the documented opaque-black fallback.
    assert parse_color("not-a-real-color") == {"r": 0, "g": 0, "b": 0, "a": 1.0}


def test_parse_px_rem_is_numeric() -> None:
    parse_px = _get("parse_px")
    # rem-before-em ordering: 2rem * 16 = 32, must NOT be 0.
    assert parse_px("2rem") == 32.0


def test_parse_px_em_is_numeric() -> None:
    parse_px = _get("parse_px")
    assert parse_px("1.5em") == 24.0


def test_parse_px_px_value() -> None:
    parse_px = _get("parse_px")
    assert parse_px("4px") == 4.0


def test_narrowed_except_does_not_swallow_keyboardinterrupt() -> None:
    """The narrowed excepts must let KeyboardInterrupt propagate.

    A value whose float() conversion raises is handled and returns 0.0, but a
    KeyboardInterrupt raised inside must NOT be swallowed by the helper.
    """
    parse_px = _get("parse_px")

    # Garbage numeric input is handled gracefully (ValueError path).
    assert parse_px("garbage") == 0.0

    # Verify no bare 'except:' remains in the source.
    import inspect

    src = inspect.getsource(mod)
    assert "except:" not in src, "bare 'except:' clause still present in module"
