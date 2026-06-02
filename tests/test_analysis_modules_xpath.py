"""
Tests for valid XPath generation in auto_a11y.ai.analysis_modules.

XPath string literals do NOT interpret XML entities such as ``&apos;``. A value
containing an apostrophe must instead be wrapped in double quotes, or — when it
contains both quote characters — be built with ``concat(...)``. These tests pin
that behaviour so we never regress to the broken ``&apos;``-replacement
approach.

The module-private helpers ``_xpath_literal`` and ``_build_xpath_for_element``
are exercised through ``getattr`` so the strict type checkers do not flag
cross-module access to underscore-prefixed names (reportPrivateUsage).
"""

from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Callable

from bs4 import BeautifulSoup
from bs4.element import Tag

import auto_a11y.ai.analysis_modules as am
from auto_a11y.ai.analysis_modules import generate_xpath

HAVE_LXML: bool = importlib.util.find_spec("lxml") is not None

# Resolve the private helpers via getattr (typed as Callable) so strict
# pyright/mypy/ty do not report cross-module private usage.
_xpath_literal: Callable[[str], str] = getattr(am, "_xpath_literal")
_build_xpath_for_element: Callable[[Tag, BeautifulSoup], str | None] = getattr(
    am, "_build_xpath_for_element"
)


def _assert_valid(xpath: str) -> None:
    """Assert the xpath has no XML entity and (if lxml present) compiles."""
    assert "&apos;" not in xpath
    if HAVE_LXML:
        etree = importlib.import_module("lxml.etree")
        # Should not raise XPathSyntaxError.
        compile_xpath = getattr(etree, "XPath")
        compile_xpath(xpath)


# ---------------------------------------------------------------------------
# _xpath_literal
# ---------------------------------------------------------------------------


def test_xpath_literal_plain_value_single_quoted() -> None:
    assert _xpath_literal("hello") == "'hello'"


def test_xpath_literal_apostrophe_not_naively_single_quoted() -> None:
    result = _xpath_literal("it's")
    # Must not produce the broken XML-entity form.
    assert "&apos;" not in result
    # Must not naively wrap an apostrophe-containing value in single quotes,
    # which would terminate the literal early: 'it's'
    assert result != "'it's'"
    # Double-quoting is the correct idiom here since there is no double quote.
    assert result == "\"it's\""
    # And it must be valid in an xpath context.
    _assert_valid(f"//*[text()={result}]")


def test_xpath_literal_double_quote_wrapped_in_single_quotes() -> None:
    result = _xpath_literal('say "hi"')
    assert result == "'say \"hi\"'"
    _assert_valid(f"//*[text()={result}]")


def test_xpath_literal_both_quotes_uses_concat() -> None:
    value = "it's a \"test\""
    result = _xpath_literal(value)
    assert result.startswith("concat(")
    assert "&apos;" not in result
    _assert_valid(f"//*[text()={result}]")


def test_xpath_literal_concat_roundtrips() -> None:
    if not HAVE_LXML:
        return
    etree = importlib.import_module("lxml.etree")
    value = "it's a \"test\""
    literal = _xpath_literal(value)
    html = f'<html><body><p id="x">{value}</p></body></html>'
    tree = getattr(etree, "HTML")(html)
    matches = tree.xpath(f"//p[text()={literal}]")
    assert len(matches) == 1


# ---------------------------------------------------------------------------
# generate_xpath
# ---------------------------------------------------------------------------


def test_generate_xpath_id_with_apostrophe() -> None:
    xpath = generate_xpath("div", element_id="it's")
    assert ("concat(" in xpath) or ('"' in xpath)
    _assert_valid(xpath)


def test_generate_xpath_single_class_with_apostrophe() -> None:
    _assert_valid(generate_xpath("div", element_class="o'clock"))


def test_generate_xpath_multi_class_with_apostrophe() -> None:
    _assert_valid(generate_xpath("div", element_class="foo o'clock"))


def test_generate_xpath_text_with_apostrophe() -> None:
    _assert_valid(generate_xpath("span", element_text="it's a test", use_text=True))


def test_generate_xpath_single_char_with_quote() -> None:
    _assert_valid(generate_xpath("button", element_text="'", use_text=True))


# ---------------------------------------------------------------------------
# _build_xpath_for_element
# ---------------------------------------------------------------------------


def _only_tag(soup: BeautifulSoup, name: str) -> Tag:
    el = soup.find(name)
    assert isinstance(el, Tag)
    return el


def test_build_xpath_id_with_apostrophe() -> None:
    soup = BeautifulSoup("<div id=\"it's\">x</div>", "html.parser")
    xpath = _build_xpath_for_element(_only_tag(soup, "div"), soup)
    assert xpath is not None
    _assert_valid(xpath)


def test_build_xpath_class_with_apostrophe() -> None:
    soup = BeautifulSoup("<div class=\"o'clock\">x</div>", "html.parser")
    xpath = _build_xpath_for_element(_only_tag(soup, "div"), soup)
    assert xpath is not None
    _assert_valid(xpath)


def test_build_xpath_text_with_apostrophe() -> None:
    soup = BeautifulSoup("<p>it's mine</p>", "html.parser")
    xpath = _build_xpath_for_element(_only_tag(soup, "p"), soup)
    assert xpath is not None
    _assert_valid(xpath)
