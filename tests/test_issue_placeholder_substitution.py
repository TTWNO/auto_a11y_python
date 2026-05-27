#!/usr/bin/env python3
"""Tests for placeholder substitution in issue descriptions (GH #48).

Reports were leaking unsubstituted ``{placeholder}`` tokens and rendering
nonsensical fragments such as ``<h>`` or ``Heading ""`` when metadata was
missing, empty, or supplied under a different naming style (camelCase vs
snake_case) than the description template expected.

These tests pin the resolver's contract:

* Real metadata values are substituted.
* A value supplied in the *other* naming style still resolves
  (``currentLevel`` fills ``{current_level}`` and vice versa).
* Empty / ``None`` / ``"null"`` values are treated as missing and replaced
  with a readable fallback, never left as ``<h>`` or ``""``.
* No literal ``{identifier}`` token ever survives into a rendered field.
"""
from __future__ import annotations

import importlib
import re
from typing import Any

# Initialise the web side first so its package loads before the reporting
# package, side-stepping the reporting <-> web.routes circular import that
# trips when ``auto_a11y.reporting`` is imported as the entry point.
importlib.import_module('auto_a11y.web.fluent')

from auto_a11y.reporting.issue_descriptions_translated import (  # noqa: E402
    get_detailed_issue_description,
)

_BRACE_TOKEN = re.compile(r'\{[A-Za-z_][A-Za-z0-9_.]*\}')
_TEXT_FIELDS = ('title', 'what', 'what_generic', 'why', 'who', 'remediation')


def _fields(desc: dict[str, Any]) -> list[str]:
    return [desc[f] for f in _TEXT_FIELDS if isinstance(desc.get(f), str)]


def _assert_no_leaks(desc: dict[str, Any]) -> None:
    for value in _fields(desc):
        leaked = _BRACE_TOKEN.findall(value)
        assert not leaked, f"leaked placeholder(s) {leaked} in: {value!r}"
        # An empty heading tag means a level placeholder resolved to nothing.
        assert '<h>' not in value, f"empty <h> tag in: {value!r}"
        assert 'Heading ""' not in value, f'empty heading text in: {value!r}'


def test_heading_mismatch_with_real_levels() -> None:
    """When the AI provides the levels, they appear verbatim in the text."""
    desc = get_detailed_issue_description(
        'AI_ErrHeadingLevelMismatch',
        {'heading_text': 'Our Team', 'current_level': 3, 'suggested_level': 2},
    )
    title = desc['title']
    assert 'Our Team' in title
    assert 'h3' in title and 'h2' in title
    _assert_no_leaks(desc)


def test_heading_mismatch_camelcase_metadata_resolves() -> None:
    """camelCase metadata must satisfy a snake_case placeholder (and vice versa)."""
    desc = get_detailed_issue_description(
        'AI_ErrHeadingLevelMismatch',
        {'headingText': 'Pricing', 'currentLevel': 4, 'suggestedLevel': 2},
    )
    title = desc['title']
    assert 'h4' in title and 'h2' in title
    assert 'Pricing' in title
    _assert_no_leaks(desc)


def test_heading_mismatch_empty_values_use_fallback() -> None:
    """Empty strings (the analyzer's default) must fall back, not render <h>/""."""
    desc = get_detailed_issue_description(
        'AI_ErrHeadingLevelMismatch',
        {'heading_text': '', 'current_level': '', 'suggested_level': ''},
    )
    _assert_no_leaks(desc)


def test_skipped_heading_missing_next_level() -> None:
    """AI_ErrSkippedHeading never emits next_level — it must not leak/blank out."""
    desc = get_detailed_issue_description(
        'AI_ErrSkippedHeading',
        {'current_level': 2},
    )
    _assert_no_leaks(desc)


def test_no_placeholder_leaks_across_heading_codes_with_no_metadata() -> None:
    """With zero metadata, no description field may contain a literal {token}."""
    for code in (
        'AI_ErrHeadingLevelMismatch',
        'AI_ErrSkippedHeading',
        'headings_ErrSkippedHeadingLevel',
        'ErrModalMissingHeading',
    ):
        desc = get_detailed_issue_description(code, {})
        if desc:
            _assert_no_leaks(desc)


if __name__ == '__main__':
    import pytest

    raise SystemExit(pytest.main([__file__, '-v']))
