"""Audit-driven regression tests for auto_a11y.web.fluent.

Covers three bugs found in a code audit:

A. ``_resolve`` swallowed every exception, turning a genuine Fluent
   format-time error (a programming bug surfaced inside ``bundle.format``)
   into a silent "message missing" (None).  A genuinely missing message
   must still resolve to None (so callers fall back), but a message that
   EXISTS and raises during format must surface, not be hidden.

B. ``ftl_enum`` short-circuited on any *falsy* value, so a valid enum
   member backed by ``0`` or ``''`` bypassed the ``enum-`` lookup.

C. ``_datetimeformat_filter`` only guarded ``None`` and crashed (500)
   on any non-datetime input.
"""
from __future__ import annotations

import datetime
from enum import Enum, IntEnum
from typing import Any
from collections.abc import Iterator

import pytest
from flask import Flask, session

from auto_a11y.web.fluent import (
    ftl,
    ftl_attr,
    lazy_ftl,
    ftl_enum,
)


# ---------------------------------------------------------------------------
# FTL test content
# ---------------------------------------------------------------------------

EN_FTL = """\
hello = Hello
greeting = Hello, { $name }!
enum-zero = Zero State
enum-empty = Empty State
"""

FR_FTL = """\
hello = Bonjour
greeting = Bonjour, { $name } !
enum-zero = État zéro
enum-empty = État vide
"""


@pytest.fixture()
def fluent_app(tmp_path: Any) -> Iterator[Any]:
    """Minimal Flask app with Fluent initialized from temp FTL files."""
    en_dir = tmp_path / "en"
    en_dir.mkdir()
    (en_dir / "messages.ftl").write_text(EN_FTL, encoding="utf-8")

    fr_dir = tmp_path / "fr"
    fr_dir.mkdir()
    (fr_dir / "messages.ftl").write_text(FR_FTL, encoding="utf-8")

    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-secret"
    app.config["TESTING"] = True

    import auto_a11y.web.fluent as fluent_mod
    load_bundles = getattr(fluent_mod, '_load_bundles')
    load_bundles(str(tmp_path))

    getattr(app.jinja_env, 'globals')["ftl"] = ftl
    getattr(app.jinja_env, 'globals')["ftl_attr"] = ftl_attr
    getattr(app.jinja_env, 'globals')["lazy_ftl"] = lazy_ftl

    yield app

    bundles: dict[str, object] = getattr(fluent_mod, '_bundles')
    bundles.clear()


# ---------------------------------------------------------------------------
# Bug A — _resolve must distinguish missing from format-error
# ---------------------------------------------------------------------------

class TestResolveMissingVsFormatError:

    def test_missing_message_resolves_to_none(self, fluent_app: Any) -> None:
        """A genuinely absent message id -> _resolve returns None."""
        import auto_a11y.web.fluent as fluent_mod
        resolve = getattr(fluent_mod, '_resolve')
        assert resolve("en", "does-not-exist", {}) is None

    def test_present_message_returns_value_and_errors(self, fluent_app: Any) -> None:
        """A present, well-formed message -> (value, [])."""
        import auto_a11y.web.fluent as fluent_mod
        resolve = getattr(fluent_mod, '_resolve')
        res = resolve("en", "hello", {})
        assert res is not None
        value, errors = res
        assert value == "Hello"
        assert list(errors) == []

    def test_format_error_is_reported_not_swallowed(self, fluent_app: Any) -> None:
        """A present message whose format produces a Fluent error must
        surface as (value, [errors]) — NOT be swallowed into None.

        'greeting' references { $name }; calling without it produces a
        FluentReferenceError in the errors list.  Pre-fix, the blanket
        ``except Exception`` could mask this; this asserts the error list
        is populated and the message is still found (non-None)."""
        import auto_a11y.web.fluent as fluent_mod
        resolve = getattr(fluent_mod, '_resolve')
        res = resolve("en", "greeting", {})
        assert res is not None, "format error must NOT become a missing-message None"
        _value, errors = res
        assert list(errors), "format-time errors must be surfaced to the caller"

    def test_genuine_format_exception_propagates(
        self, fluent_app: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If bundle.format() raises a NON-lookup exception (a real
        programming/format-time error), _resolve must NOT swallow it into
        None — it should propagate so strict mode / callers can see it.

        We simulate this by replacing the 'en' bundle with a stub whose
        format() raises a ValueError for a known id while raising KeyError
        (the genuine-miss signal) for unknown ids."""
        import auto_a11y.web.fluent as fluent_mod
        resolve = getattr(fluent_mod, '_resolve')

        class BoomBundle:
            def format(
                self, message_id: str, args: object = None
            ) -> tuple[str, list[object]]:
                if message_id == "boom":
                    raise ValueError("format-time programming error")
                raise KeyError(message_id)

        stub_bundles: dict[str, object] = {"en": BoomBundle()}
        monkeypatch.setattr(fluent_mod, "_bundles", stub_bundles)

        # Genuine miss still -> None
        assert resolve("en", "nope", {}) is None
        # Real format-time error must propagate
        with pytest.raises(ValueError, match="format-time programming error"):
            resolve("en", "boom", {})

    def test_strict_mode_reports_format_error(self, fluent_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """End-to-end: in strict mode a present message with a format
        error must raise MissingTranslationError mentioning formatting,
        not be silently treated as missing."""
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import MissingTranslationError
        monkeypatch.setattr(fluent_mod, "_strict_mode", True)
        with fluent_app.test_request_context():
            session["language"] = "en"
            with pytest.raises(MissingTranslationError, match="formatting errors"):
                ftl("greeting")  # missing required { $name }


# ---------------------------------------------------------------------------
# Bug B — ftl_enum must not short-circuit on falsy-but-valid values
# ---------------------------------------------------------------------------

class TestFtlEnumFalsy:

    def test_intenum_zero_attempts_lookup(self, fluent_app: Any) -> None:
        """An IntEnum member whose value is 0 must reach the enum- lookup
        and resolve, not return '' due to falsiness."""
        class Severity(IntEnum):
            ZERO = 0
            ONE = 1

        with fluent_app.test_request_context():
            session["language"] = "en"
            # str(Severity.ZERO) -> 'Severity.ZERO'; .value is 0 (falsy).
            # ftl_enum stringifies the value; normalized id for 0 is 'enum-0'.
            result = ftl_enum(Severity.ZERO.value)
            assert str(result) != "", "falsy enum value must not short-circuit to empty string"

    def test_integer_zero_does_not_return_empty(self, fluent_app: Any) -> None:
        """The bare integer 0 must not collapse to '' — it should attempt
        the enum- lookup and fall back to a non-empty representation."""
        with fluent_app.test_request_context():
            session["language"] = "en"
            result = ftl_enum(0)
            assert str(result) != ""

    def test_string_enum_value_zero_translates(self, fluent_app: Any) -> None:
        """A string-valued enum 'zero' resolves via enum-zero."""
        class State(Enum):
            ZERO = "zero"

        with fluent_app.test_request_context():
            session["language"] = "en"
            assert str(ftl_enum(State.ZERO.value)) == "Zero State"

    def test_empty_string_does_not_attempt_bogus_lookup(self, fluent_app: Any) -> None:
        """Empty string is not a meaningful enum value: ftl_enum('')
        should return '' (no enum- lookup), matching prior behavior."""
        with fluent_app.test_request_context():
            session["language"] = "en"
            assert str(ftl_enum("")) == ""

    def test_none_returns_empty(self, fluent_app: Any) -> None:
        """None returns '' (unchanged)."""
        with fluent_app.test_request_context():
            session["language"] = "en"
            assert ftl_enum(None) == ""

    def test_normal_value_still_translates(self, fluent_app: Any) -> None:
        """Sanity: a normal truthy value still resolves through enum-."""
        with fluent_app.test_request_context():
            session["language"] = "fr"
            assert str(ftl_enum("empty")) == "État vide"


# ---------------------------------------------------------------------------
# Bug C — _datetimeformat_filter must degrade gracefully on bad input
# ---------------------------------------------------------------------------

class TestDatetimeformatFilter:

    def test_none_returns_empty(self, fluent_app: Any) -> None:
        import auto_a11y.web.fluent as fluent_mod
        dtf = getattr(fluent_mod, '_datetimeformat_filter')
        with fluent_app.app_context():
            assert dtf(None) == ""

    def test_real_datetime_formats(self, fluent_app: Any) -> None:
        import auto_a11y.web.fluent as fluent_mod
        dtf = getattr(fluent_mod, '_datetimeformat_filter')
        dt = datetime.datetime(2026, 6, 1, 12, 30, 0)
        with fluent_app.app_context():
            out = dtf(dt)
            assert isinstance(out, str)
            assert "2026" in out

    def test_string_input_does_not_raise(self, fluent_app: Any) -> None:
        """A non-datetime string must not raise a 500 — it degrades to
        a string fallback (the original value's str)."""
        import auto_a11y.web.fluent as fluent_mod
        dtf = getattr(fluent_mod, '_datetimeformat_filter')
        with fluent_app.app_context():
            out = dtf("not a date")
            assert out == "not a date"

    def test_int_input_does_not_raise(self, fluent_app: Any) -> None:
        import auto_a11y.web.fluent as fluent_mod
        dtf = getattr(fluent_mod, '_datetimeformat_filter')
        with fluent_app.app_context():
            out = dtf(12345)
            assert isinstance(out, str)
            assert out == "12345"

    def test_date_object_formats(self, fluent_app: Any) -> None:
        """A plain date (subclass-free) should still format gracefully."""
        import auto_a11y.web.fluent as fluent_mod
        dtf = getattr(fluent_mod, '_datetimeformat_filter')
        d = datetime.date(2026, 6, 1)
        with fluent_app.app_context():
            out = dtf(d)
            assert isinstance(out, str)
            assert out != ""
