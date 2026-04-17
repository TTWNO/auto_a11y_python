"""Tests for the Flask-Fluent integration layer (auto_a11y.web.fluent)."""
from __future__ import annotations

from typing import Any
from collections.abc import Iterator

import os
import tempfile

import pytest
from flask import Flask, session
from markupsafe import Markup

from auto_a11y.web.fluent import (
    ftl,
    ftl_attr,
    force_locale,
    lazy_ftl,
    init_fluent,
    _bundles,
    _load_bundles,
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
    _load_bundles(str(tmp_path))

    # Register Jinja globals/filters
    app.jinja_env.globals["ftl"] = ftl
    app.jinja_env.globals["ftl_attr"] = ftl_attr
    app.jinja_env.globals["lazy_ftl"] = lazy_ftl

    yield app

    # Clean up module-level bundles
    fluent_mod._bundles.clear()


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
