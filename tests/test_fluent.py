"""Tests for the Flask-Fluent integration layer (auto_a11y.web.fluent)."""
from __future__ import annotations

from typing import Any
from collections.abc import Iterator

import pytest
from flask import Flask, session
from markupsafe import Markup

from auto_a11y.web.fluent import (
    ftl,
    ftl_attr,
    force_locale,
    lazy_ftl,
)

# ---------------------------------------------------------------------------
# FTL test content
# ---------------------------------------------------------------------------

EN_FTL = """\
hello = Hello
greeting = Hello, { $name }!
item-count = { $count ->
    [one] One item
   *[other] { $count } items
}
only-in-english = English only
search-input = Search
    .placeholder = Enter query
"""

FR_FTL = """\
hello = Bonjour
greeting = Bonjour, { $name } !
item-count = { $count ->
    [one] Un élément
   *[other] { $count } éléments
}
apostrophe-test = C'est l'aide
search-input = Rechercher
    .placeholder = Entrez votre requête
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fluent_app(tmp_path: Any) -> Iterator[Any]:
    """Create a minimal Flask app with Fluent initialized from temp FTL files."""
    # Write FTL files
    en_dir = tmp_path / "en"
    en_dir.mkdir()
    (en_dir / "messages.ftl").write_text(EN_FTL, encoding="utf-8")

    fr_dir = tmp_path / "fr"
    fr_dir.mkdir()
    (fr_dir / "messages.ftl").write_text(FR_FTL, encoding="utf-8")

    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-secret"
    app.config["TESTING"] = True

    # Monkey-patch translations dir and initialize
    import auto_a11y.web.fluent as fluent_mod
    load_bundles = getattr(fluent_mod, '_load_bundles')
    load_bundles(str(tmp_path))

    # Register Jinja globals/filters
    getattr(app.jinja_env, 'globals')["ftl"] = ftl
    getattr(app.jinja_env, 'globals')["ftl_attr"] = ftl_attr
    getattr(app.jinja_env, 'globals')["lazy_ftl"] = lazy_ftl

    yield app

    # Clean up module-level bundles
    bundles: dict[str, object] = getattr(fluent_mod, '_bundles')
    bundles.clear()


# ---------------------------------------------------------------------------
# Tests: basic message resolution
# ---------------------------------------------------------------------------

class TestFtlResolution:

    def test_simple_en(self, fluent_app: Any) -> None:
        with fluent_app.test_request_context():
            session["language"] = "en"
            assert str(ftl("hello")) == "Hello"

    def test_simple_fr(self, fluent_app: Any) -> None:
        with fluent_app.test_request_context():
            session["language"] = "fr"
            assert str(ftl("hello")) == "Bonjour"

    def test_returns_markup(self, fluent_app: Any) -> None:
        with fluent_app.test_request_context():
            session["language"] = "en"
            result = ftl("hello")
            assert isinstance(result, Markup)

    def test_variable_substitution(self, fluent_app: Any) -> None:
        with fluent_app.test_request_context():
            session["language"] = "en"
            assert str(ftl("greeting", name="World")) == "Hello, World!"

    def test_variable_substitution_fr(self, fluent_app: Any) -> None:
        with fluent_app.test_request_context():
            session["language"] = "fr"
            result = str(ftl("greeting", name="Monde"))
            assert "Bonjour" in result
            assert "Monde" in result

    def test_plural_one(self, fluent_app: Any) -> None:
        with fluent_app.test_request_context():
            session["language"] = "en"
            assert str(ftl("item-count", count=1)) == "One item"

    def test_plural_other(self, fluent_app: Any) -> None:
        with fluent_app.test_request_context():
            session["language"] = "en"
            assert str(ftl("item-count", count=5)) == "5 items"

    def test_plural_fr(self, fluent_app: Any) -> None:
        with fluent_app.test_request_context():
            session["language"] = "fr"
            assert str(ftl("item-count", count=1)) == "Un élément"
            result = str(ftl("item-count", count=5))
            assert "5" in result
            assert "éléments" in result


# ---------------------------------------------------------------------------
# Tests: missing messages and fallback
# ---------------------------------------------------------------------------

class TestFallback:

    def test_missing_returns_message_id(self, fluent_app: Any) -> None:
        """A completely missing message returns the message ID as a plain string."""
        with fluent_app.test_request_context():
            session["language"] = "en"
            result = ftl("does-not-exist")
            assert result == "does-not-exist"
            assert not isinstance(result, Markup)

    def test_fr_falls_back_to_en(self, fluent_app: Any) -> None:
        """A message present in EN but missing in FR falls back to English."""
        with fluent_app.test_request_context():
            session["language"] = "fr"
            result = ftl("only-in-english")
            assert str(result) == "English only"
            assert isinstance(result, Markup)


# ---------------------------------------------------------------------------
# Tests: apostrophe / HTML escaping
# ---------------------------------------------------------------------------

class TestEscaping:

    def test_french_apostrophe_escaped(self, fluent_app: Any) -> None:
        """French text with apostrophes is HTML-escaped via markupsafe.escape()."""
        with fluent_app.test_request_context():
            session["language"] = "fr"
            result = ftl("apostrophe-test")
            assert isinstance(result, Markup)
            # markupsafe.escape("C'est l'aide") -> "C&#39;est l&#39;aide"
            assert "&#39;" in str(result)
            assert "C&#39;est l&#39;aide" == str(result)


# ---------------------------------------------------------------------------
# Tests: ftl_attr
# ---------------------------------------------------------------------------

class TestFtlAttr:

    def test_attr_en(self, fluent_app: Any) -> None:
        with fluent_app.test_request_context():
            session["language"] = "en"
            assert str(ftl_attr("search-input", "placeholder")) == "Enter query"

    def test_attr_fr(self, fluent_app: Any) -> None:
        with fluent_app.test_request_context():
            session["language"] = "fr"
            result = str(ftl_attr("search-input", "placeholder"))
            assert "Entrez votre requête" in result


# ---------------------------------------------------------------------------
# Tests: force_locale
# ---------------------------------------------------------------------------

class TestForceLocale:

    def test_overrides_session(self, fluent_app: Any) -> None:
        """force_locale overrides the session language."""
        with fluent_app.test_request_context():
            session["language"] = "en"
            with force_locale("fr"):
                assert str(ftl("hello")) == "Bonjour"
            # After exiting, session locale is restored
            assert str(ftl("hello")) == "Hello"

    def test_works_outside_request_context(self, fluent_app: Any) -> None:
        """force_locale works in an app_context (no request)."""
        with fluent_app.app_context():
            with force_locale("fr"):
                assert str(ftl("hello")) == "Bonjour"
            # Without override, falls back to default 'en'
            assert str(ftl("hello")) == "Hello"


# ---------------------------------------------------------------------------
# Tests: lazy_ftl
# ---------------------------------------------------------------------------

class TestLazyFtl:

    def test_resolves_at_str_time(self, fluent_app: Any) -> None:
        """lazy_ftl defers resolution until str() is called."""
        lazy = lazy_ftl("hello")
        # At this point no request context — resolution happens later
        with fluent_app.test_request_context():
            session["language"] = "en"
            assert str(lazy) == "Hello"

    def test_html_returns_markup(self, fluent_app: Any) -> None:
        """__html__() returns Markup for Jinja2 auto-escaping."""
        lazy = lazy_ftl("hello")
        with fluent_app.test_request_context():
            session["language"] = "en"
            html_val = lazy.__html__()
            assert isinstance(html_val, Markup)
            assert str(html_val) == "Hello"

    def test_lazy_with_variable(self, fluent_app: Any) -> None:
        lazy = lazy_ftl("greeting", name="Alice")
        with fluent_app.test_request_context():
            session["language"] = "en"
            assert str(lazy) == "Hello, Alice!"

    def test_lazy_respects_force_locale(self, fluent_app: Any) -> None:
        lazy = lazy_ftl("hello")
        with fluent_app.app_context():
            with force_locale("fr"):
                assert str(lazy) == "Bonjour"


# ---------------------------------------------------------------------------
# Tests: MissingTranslationError
# ---------------------------------------------------------------------------

class TestMissingTranslationError:

    def test_import(self) -> None:
        """MissingTranslationError is importable from auto_a11y.web.fluent."""
        from auto_a11y.web.fluent import MissingTranslationError
        assert MissingTranslationError is not None

    def test_subclasses_keyerror(self) -> None:
        """MissingTranslationError subclasses KeyError so existing
        except-KeyError blocks keep working."""
        from auto_a11y.web.fluent import MissingTranslationError
        assert issubclass(MissingTranslationError, KeyError)

    def test_can_raise_with_message(self) -> None:
        from auto_a11y.web.fluent import MissingTranslationError
        with pytest.raises(MissingTranslationError, match="test msg"):
            raise MissingTranslationError("test msg")


# ---------------------------------------------------------------------------
# Tests: strict-mode flag
# ---------------------------------------------------------------------------

class TestStrictModeFlag:

    def test_is_strict_defaults_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_is_strict() returns False when _strict_mode is False (default)."""
        import auto_a11y.web.fluent as fluent_mod
        monkeypatch.setattr(fluent_mod, "_strict_mode", False)
        is_strict = getattr(fluent_mod, '_is_strict')
        assert is_strict() is False

    def test_is_strict_reflects_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_is_strict() returns True when _strict_mode is True."""
        import auto_a11y.web.fluent as fluent_mod
        monkeypatch.setattr(fluent_mod, "_strict_mode", True)
        is_strict = getattr(fluent_mod, '_is_strict')
        assert is_strict() is True

    def test_init_fluent_sets_flag_from_app_debug(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """init_fluent(app) sets _strict_mode from app.debug."""
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import init_fluent

        monkeypatch.setattr(fluent_mod, "_strict_mode", False)
        bundles: dict[str, object] = getattr(fluent_mod, '_bundles')
        monkeypatch.setattr(fluent_mod, "_bundles", dict(bundles))

        # app.debug=True -> strict
        app = Flask(__name__)
        app.debug = True
        init_fluent(app)
        assert getattr(fluent_mod, '_strict_mode') is True

        # app.debug=False -> non-strict
        app2 = Flask(__name__)
        app2.debug = False
        init_fluent(app2)
        assert getattr(fluent_mod, '_strict_mode') is False


# ---------------------------------------------------------------------------
# Tests: strict mode — missing-locale raises
# ---------------------------------------------------------------------------

class TestStrictModeMissingLocales:

    def test_missing_in_fr_raises(self, fluent_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """When FR is missing a message ID, strict mode raises even if EN has it."""
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import MissingTranslationError

        monkeypatch.setattr(fluent_mod, "_strict_mode", True)

        with fluent_app.test_request_context():
            session["language"] = "en"
            with pytest.raises(MissingTranslationError, match="only-in-english"):
                ftl("only-in-english")

    def test_missing_in_both_raises(self, fluent_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing in both locales raises."""
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import MissingTranslationError

        monkeypatch.setattr(fluent_mod, "_strict_mode", True)

        with fluent_app.test_request_context():
            session["language"] = "en"
            with pytest.raises(MissingTranslationError, match="does-not-exist"):
                ftl("does-not-exist")

    def test_error_message_names_missing_locales(self, fluent_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """The error message lists which locales are missing the ID."""
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import MissingTranslationError

        monkeypatch.setattr(fluent_mod, "_strict_mode", True)

        with fluent_app.test_request_context():
            session["language"] = "en"
            with pytest.raises(MissingTranslationError) as exc_info:
                ftl("only-in-english")
            # Only FR is missing this one
            assert "fr" in str(exc_info.value)
            assert "only-in-english" in str(exc_info.value)

    def test_non_strict_unchanged(self, fluent_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-strict mode (default): same inputs still log + fall back."""
        import auto_a11y.web.fluent as fluent_mod

        monkeypatch.setattr(fluent_mod, "_strict_mode", False)

        with fluent_app.test_request_context():
            session["language"] = "fr"
            # only-in-english: FR missing, EN has it -> falls back to EN
            result = ftl("only-in-english")
            assert str(result) == "English only"

            # does-not-exist: both missing -> returns raw ID
            result2 = ftl("does-not-exist")
            assert result2 == "does-not-exist"

    def test_present_in_both_does_not_raise(self, fluent_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """When the message exists in both locales, strict mode is silent."""
        import auto_a11y.web.fluent as fluent_mod
        monkeypatch.setattr(fluent_mod, "_strict_mode", True)

        with fluent_app.test_request_context():
            session["language"] = "fr"
            # Should not raise
            assert str(ftl("hello")) == "Bonjour"


# ---------------------------------------------------------------------------
# Tests: strict mode — format errors raise
# ---------------------------------------------------------------------------

class TestStrictModeFormatErrors:

    def test_missing_variable_raises_in_strict(self, fluent_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """In strict mode, missing a required variable raises."""
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import MissingTranslationError

        monkeypatch.setattr(fluent_mod, "_strict_mode", True)

        with fluent_app.test_request_context():
            session["language"] = "en"
            # 'greeting' requires { $name }; passing no kwargs -> format error
            with pytest.raises(MissingTranslationError, match="formatting errors"):
                ftl("greeting")

    def test_missing_variable_non_strict_logs_warning(self, fluent_app: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        """Non-strict mode still logs a warning on format errors (unchanged behavior)."""
        import auto_a11y.web.fluent as fluent_mod
        import logging

        monkeypatch.setattr(fluent_mod, "_strict_mode", False)

        with fluent_app.test_request_context():
            session["language"] = "en"
            with caplog.at_level(logging.WARNING, logger="auto_a11y.web.fluent"):
                # Should NOT raise, just log + return whatever fluent gives us
                _result = ftl("greeting")
            # A warning was logged mentioning format errors
            assert any("Fluent errors" in rec.message for rec in caplog.records)

    def test_correct_kwargs_does_not_raise(self, fluent_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """Correct kwargs produce no format errors, strict mode silent."""
        import auto_a11y.web.fluent as fluent_mod
        monkeypatch.setattr(fluent_mod, "_strict_mode", True)

        with fluent_app.test_request_context():
            session["language"] = "en"
            assert str(ftl("greeting", name="World")) == "Hello, World!"


# ---------------------------------------------------------------------------
# Tests: strict mode — ftl_translate_issue
# ---------------------------------------------------------------------------

class TestStrictModeTranslateIssue:
    """ftl_translate_issue never raises — it logs and falls back.

    Translation coverage for inline issue descriptions is enforced by
    the ``TestInlineIssueIdsCoverage`` tests below, not at runtime.
    """

    def test_unmapped_text_returns_original_in_strict(
        self, fluent_app: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """In strict mode, unmapped text logs a warning and returns the
        original English text (no crash)."""
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import ftl_translate_issue

        monkeypatch.setattr(fluent_mod, "_strict_mode", True)
        monkeypatch.setattr(fluent_mod, "_inline_issue_ids", {})

        with fluent_app.test_request_context():
            session["language"] = "en"
            result = ftl_translate_issue("Some untracked inline text")
            assert result == "Some untracked inline text"

    def test_unmapped_text_non_strict_returns_original(self, fluent_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-strict: unmapped text silently falls back (unchanged behavior)."""
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import ftl_translate_issue

        monkeypatch.setattr(fluent_mod, "_strict_mode", False)
        monkeypatch.setattr(fluent_mod, "_inline_issue_ids", {})

        with fluent_app.test_request_context():
            session["language"] = "en"
            assert ftl_translate_issue("Some untracked inline text") == (
                "Some untracked inline text"
            )

    def test_empty_text_does_not_raise(self, fluent_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty text returns empty string without raising, even in strict."""
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import ftl_translate_issue

        monkeypatch.setattr(fluent_mod, "_strict_mode", True)

        assert ftl_translate_issue("") == ""

    @pytest.mark.parametrize("text", [
        # Dynamically interpolated (instance-specific counts/values)
        "Heading is 56 characters, approaching recommended limit of 60",
        (
            "Element contains 5 list-like items using icon font bullets "
            "(Font Awesome, Material Icons, etc.), but does not use proper "
            "<ul>/<ol> and <li> markup"
        ),
        # Auto-generated catalog fallbacks
        "Accessibility issue: lists_WarnListRoleOnList",
        "An accessibility issue of type 'WarnFoo' was detected.",
        "Issue ErrNewCode needs documentation",
    ])
    def test_dynamic_text_never_raises(
        self, fluent_app: Any, monkeypatch: pytest.MonkeyPatch,
        text: str,
    ) -> None:
        """Dynamic, interpolated, and fallback descriptions must never
        crash the page — they return the original English text."""
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import ftl_translate_issue

        monkeypatch.setattr(fluent_mod, "_strict_mode", True)
        monkeypatch.setattr(fluent_mod, "_inline_issue_ids", {})

        with fluent_app.test_request_context():
            session["language"] = "en"
            result = ftl_translate_issue(text)
            assert result == text

    def test_unmapped_text_logs_warning_in_strict(
        self, fluent_app: Any, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Strict mode logs a warning for unmapped text (for dev awareness)."""
        import logging
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import ftl_translate_issue

        monkeypatch.setattr(fluent_mod, "_strict_mode", True)
        monkeypatch.setattr(fluent_mod, "_inline_issue_ids", {})

        with caplog.at_level(logging.WARNING, logger="auto_a11y.web.fluent"):
            with fluent_app.test_request_context():
                session["language"] = "en"
                ftl_translate_issue("Unmapped text for logging test")

        assert any("inline_issue_ids.json" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Tests: inline_issue_ids.json ↔ FTL coverage
# ---------------------------------------------------------------------------

class TestInlineIssueIdsCoverage:
    """Validate that every key in inline_issue_ids.json maps to a real
    FTL message in both EN and FR locale files, and that every static
    issue description from the catalog has an entry in the JSON map."""

    @staticmethod
    def _load_ftl_ids(path: str) -> set[str]:
        """Extract all top-level message IDs from an FTL file."""
        import re
        ids: set[str] = set()
        with open(path, encoding="utf-8") as f:
            for line in f:
                # FTL message definition: id = value (at start of line)
                m = re.match(r'^([a-z][a-z0-9-]*)\s*=', line)
                if m:
                    ids.add(m.group(1))
        return ids

    @staticmethod
    def _load_json_map() -> dict[str, str]:
        import json, os
        path = os.path.join(
            os.path.dirname(__file__), os.pardir,
            "auto_a11y", "web", "translations", "inline_issue_ids.json",
        )
        with open(path, encoding="utf-8") as f:
            result: dict[str, str] = json.load(f)
            return result

    def test_all_json_keys_have_en_ftl(self) -> None:
        """Every FTL ID referenced in inline_issue_ids.json must exist
        in the English inline-issues.ftl file."""
        import os
        json_map = self._load_json_map()
        en_ftl = os.path.join(
            os.path.dirname(__file__), os.pardir,
            "auto_a11y", "web", "translations", "en", "inline-issues.ftl",
        )
        en_ids = self._load_ftl_ids(en_ftl)
        missing = [
            f"{ftl_id!r} (for: {text[:60]}...)"
            for text, ftl_id in sorted(json_map.items())
            if ftl_id not in en_ids
        ]
        assert not missing, "\n  ".join(
            [f"{len(missing)} FTL ID(s) in inline_issue_ids.json missing from en/inline-issues.ftl:"]
            + missing
        )

    def test_all_json_keys_have_fr_ftl(self) -> None:
        """Every FTL ID referenced in inline_issue_ids.json must exist
        in the French inline-issues.ftl file."""
        import os
        json_map = self._load_json_map()
        fr_ftl = os.path.join(
            os.path.dirname(__file__), os.pardir,
            "auto_a11y", "web", "translations", "fr", "inline-issues.ftl",
        )
        fr_ids = self._load_ftl_ids(fr_ftl)
        missing = [
            f"{ftl_id!r} (for: {text[:60]}...)"
            for text, ftl_id in sorted(json_map.items())
            if ftl_id not in fr_ids
        ]
        assert not missing, "\n  ".join(
            [f"{len(missing)} FTL ID(s) in inline_issue_ids.json missing from fr/inline-issues.ftl:"]
            + missing
        )

    def test_static_issue_descriptions_in_json(self) -> None:
        """Every static (no placeholders) ``what`` and ``what_generic``
        value from the issue catalog must have an entry in
        inline_issue_ids.json.  This catches new descriptions added to
        the catalog without a corresponding translation mapping."""
        import re as _re
        from auto_a11y.reporting.issue_descriptions_enhanced import (
            get_detailed_issue_description,
        )

        json_map = self._load_json_map()

        # Collect all error codes from the catalog.
        # The descriptions dict is local to the function, so we extract
        # codes from the source text of the function.
        import auto_a11y.reporting.issue_descriptions_enhanced as eid
        fn_source = __import__('inspect').getsource(eid.get_detailed_issue_description)

        # Match dict keys like 'ErrFoo': { ... } — must be followed by
        # a colon-space-brace to avoid matching enum values etc.
        code_pattern = _re.compile(
            r"'((?:Err|Warn|Disco|AI_)[A-Za-z0-9_]+)'\s*:\s*\{"
        )
        codes = code_pattern.findall(fn_source)
        # Also match Info codes but not the ImpactScale "Informational" value
        info_pattern = _re.compile(
            r"'(Info[A-Z][A-Za-z0-9_]+)'\s*:\s*\{"
        )
        codes.extend(info_pattern.findall(fn_source))

        missing: list[str] = []
        for code in codes:
            desc = get_detailed_issue_description(code, {})
            for field in ("what", "what_generic"):
                text = desc.get(field)
                if not text or not isinstance(text, str):
                    continue
                # Skip parameterised descriptions (contain {placeholder})
                if "{" in text:
                    continue
                # Skip fallback descriptions (not real catalog entries)
                if text.startswith("An accessibility issue of type"):
                    continue
                if text not in json_map:
                    missing.append(f"{code}.{field}: {text[:80]}...")

        assert not missing, "\n  ".join(
            [f"{len(missing)} static issue description(s) missing from inline_issue_ids.json:"]
            + missing
        )

    def test_js_error_codes_have_catalog_entries(self) -> None:
        """Every error code emitted by JavaScript test scripts must have
        a real entry in the issue descriptions catalog (not just the
        auto-generated fallback).  Missing entries cause fallback text
        like ``"Accessibility issue: ..."`` to reach the template, which
        cannot be translated."""
        import os
        import re as _re
        from auto_a11y.reporting.issue_descriptions_enhanced import (
            get_detailed_issue_description,
        )

        # Collect all error codes from JS test scripts
        js_dir = os.path.join(
            os.path.dirname(__file__), os.pardir,
            "auto_a11y", "testing", "touchpoint_tests",
        )
        # Pattern: err: 'ErrFoo' or err: "WarnBar"
        err_pattern = _re.compile(r"""err:\s*['"](\w+)['"]""")
        js_codes: set[str] = set()
        for fname in os.listdir(js_dir):
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(js_dir, fname)
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            js_codes.update(err_pattern.findall(content))

        # Check each code has a real catalog entry (not the fallback)
        missing: list[str] = []
        for code in sorted(js_codes):
            desc = get_detailed_issue_description(code, {})
            title = desc.get("title", "")
            if (
                title.startswith("Accessibility issue:")
                or title.startswith("Issue ")
                and title.endswith(" needs documentation")
            ):
                missing.append(code)

        assert not missing, "\n  ".join(
            [f"{len(missing)} JS error code(s) have no catalog entry in issue_descriptions_enhanced.py:"]
            + missing
        )


# ---------------------------------------------------------------------------
# Tests: strict mode inherited by wrappers
# ---------------------------------------------------------------------------

class TestStrictModeWrappers:

    def test_ftl_attr_inherits_strict(self, fluent_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """ftl_attr raises in strict mode when the composite id.attr is missing."""
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import MissingTranslationError, ftl_attr

        monkeypatch.setattr(fluent_mod, "_strict_mode", True)

        with fluent_app.test_request_context():
            session["language"] = "en"
            # search-input.does-not-exist: attribute missing from both locales
            with pytest.raises(MissingTranslationError):
                ftl_attr("search-input", "does-not-exist")

    def test_lazy_ftl_inherits_strict(self, fluent_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """lazy_ftl raises at stringification time in strict mode."""
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import MissingTranslationError, lazy_ftl

        monkeypatch.setattr(fluent_mod, "_strict_mode", True)

        lazy = lazy_ftl("does-not-exist")
        with fluent_app.test_request_context():
            session["language"] = "en"
            with pytest.raises(MissingTranslationError):
                str(lazy)

    def test_ftl_enum_inherits_strict(self, fluent_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """ftl_enum raises in strict mode when the enum-* id is missing."""
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import MissingTranslationError, ftl_enum

        monkeypatch.setattr(fluent_mod, "_strict_mode", True)

        with fluent_app.test_request_context():
            session["language"] = "en"
            # 'enum-bogus-value' is not in the test bundles
            with pytest.raises(MissingTranslationError):
                ftl_enum("bogus_value")

    def test_ftl_enum_non_strict_still_title_cases(self, fluent_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-strict mode: ftl_enum's title-case fallback still works."""
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import ftl_enum

        monkeypatch.setattr(fluent_mod, "_strict_mode", False)

        with fluent_app.test_request_context():
            session["language"] = "en"
            # Falls back to title-cased 'Bogus Value'
            assert ftl_enum("bogus_value") == "Bogus Value"

    def test_ftl_wcag_inherits_strict(self, fluent_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """ftl_wcag raises in strict mode when the wcag-* id is missing."""
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import MissingTranslationError, ftl_wcag

        monkeypatch.setattr(fluent_mod, "_strict_mode", True)

        with fluent_app.test_request_context():
            session["language"] = "en"
            with pytest.raises(MissingTranslationError):
                ftl_wcag("Some Criterion Not In Bundles")
