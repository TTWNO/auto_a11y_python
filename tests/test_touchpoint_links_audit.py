"""Audit-driven tests for auto_a11y/testing/touchpoint_tests/test_links.py.

Covers three low-severity bugs found in a code audit:

A. Space-key handler detection regex must match the common real-world form
   ``e.key === ' '`` (a single space inside ONE quote pair), not only the
   malformed ``key === ' ' ' '`` form (a literal space between two separate
   quote pairs).

B. A ``target="_blank"`` link that lacks a new-window warning must be counted
   as a *failed* element and must NOT simultaneously be counted as a *passed*
   element by the later focus-indicator logic.

C. ``parse_color`` must expand 3-digit hex (``#abc`` -> ``#aabbcc``) instead of
   falling back to opaque black, and the numeric-parse helpers must narrow
   their ``except`` clauses so a KeyboardInterrupt is not swallowed.

A and B are exercised end-to-end through the real ``test_links`` pipeline using
a headless Playwright browser (no CSS capture registered, so the inline
:focus-rule scan in page context is used). C is a pure unit test.

Requires Playwright: python -m playwright install chromium
"""
from __future__ import annotations

import os
os.environ.setdefault('RUN_AI_ANALYSIS', 'false')

import importlib
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright, Page

# Import the core package first to fully initialize the auto_a11y.testing
# import chain before reaching into the touchpoint submodule (otherwise the
# submodule import races a partially-initialized auto_a11y.testing package).
importlib.import_module("auto_a11y.core")
from auto_a11y.testing.touchpoint_tests.test_links import (
    parse_color,
    parse_px,
    test_links as run_links_test,
)


# ---------------------------------------------------------------------------
# Playwright fixture: builds a page from inline HTML supplied per-test
# ---------------------------------------------------------------------------

PageBuilder = Callable[[str], Awaitable[Page]]


@pytest_asyncio.fixture
async def make_page() -> AsyncIterator[PageBuilder]:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context()

        async def _build(html: str) -> Page:
            pg = await ctx.new_page()
            await pg.set_content(html, wait_until="domcontentloaded")
            return pg

        yield _build
        await browser.close()


# ---------------------------------------------------------------------------
# Bug A — Space-key handler detection
# ---------------------------------------------------------------------------

# A button-like anchor: solid background + substantial padding => looks_like_button.
# When it HAS a Space-key handler, ErrLinkButtonMissingSpaceHandler must NOT fire.
_BUTTON_STYLE = "background-color: rgb(0, 90, 156); color: rgb(255,255,255); padding: 12px 20px;"


def _button_link_with_handler(handler_attr: str) -> str:
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
        <a href="#" style="{_BUTTON_STYLE}" {handler_attr}>Submit</a>
    </body></html>"""


@pytest.mark.asyncio
async def test_space_handler_single_quoted_space_is_detected(make_page: PageBuilder) -> None:
    """e.key === ' ' (single quoted space) must be recognized as a Space handler."""
    page = await _build_helper(make_page, _button_link_with_handler(
        "onkeydown=\"if (e.key === ' ') { doThing(); }\""
    ))
    results = await run_links_test(page)
    codes = [e['err'] for e in results['errors']]
    assert 'ErrLinkButtonMissingSpaceHandler' not in codes, (
        "Space handler written as e.key === ' ' should be detected; "
        f"got errors: {codes}"
    )


@pytest.mark.asyncio
async def test_space_handler_double_quoted_space_is_detected(make_page: PageBuilder) -> None:
    """e.key === " " (double-quoted space) must also be recognized."""
    page = await _build_helper(make_page, _button_link_with_handler(
        'onkeydown="if (e.key === &quot; &quot;) { doThing(); }"'
    ))
    results = await run_links_test(page)
    codes = [e['err'] for e in results['errors']]
    assert 'ErrLinkButtonMissingSpaceHandler' not in codes, (
        'Space handler written as e.key === " " should be detected; '
        f"got errors: {codes}"
    )


@pytest.mark.asyncio
async def test_space_handler_absent_still_flagged(make_page: PageBuilder) -> None:
    """Control: a button-like link with NO space handler is still flagged."""
    page = await _build_helper(make_page, _button_link_with_handler(""))
    results = await run_links_test(page)
    codes = [e['err'] for e in results['errors']]
    assert 'ErrLinkButtonMissingSpaceHandler' in codes, (
        f"button-like link without a Space handler should be flagged; got: {codes}"
    )


# ---------------------------------------------------------------------------
# Bug B — target="_blank" without warning counts as failed, not passed
# ---------------------------------------------------------------------------

_BLANK_NO_WARNING = """<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
    <a href="https://example.com" target="_blank">Example</a>
</body></html>"""


@pytest.mark.asyncio
async def test_blank_no_warning_emits_error(make_page: PageBuilder) -> None:
    page = await _build_helper(make_page, _BLANK_NO_WARNING)
    results = await run_links_test(page)
    codes = [e['err'] for e in results['errors']]
    assert 'ErrLinkOpensNewWindowNoWarning' in codes


@pytest.mark.asyncio
async def test_blank_no_warning_counts_as_failed_not_passed(make_page: PageBuilder) -> None:
    page = await _build_helper(make_page, _BLANK_NO_WARNING)
    results = await run_links_test(page)

    assert results['elements_tested'] == 1, "exactly one link on the page"
    assert results['elements_failed'] >= 1, (
        "a _blank link with no new-window warning must be counted as failed"
    )
    # The single link cannot be both passed and failed.
    assert results['elements_passed'] == 0, (
        "the only link emitted a new-window error and must NOT also be counted "
        f"as passed; passed={results['elements_passed']} failed={results['elements_failed']}"
    )
    assert results['elements_passed'] + results['elements_failed'] <= results['elements_tested'], (
        "passed + failed must not exceed tested (no double counting)"
    )


@pytest.mark.asyncio
async def test_blank_with_warning_does_not_emit_newwindow_error(make_page: PageBuilder) -> None:
    """Control: a _blank link that warns the user must not emit the error."""
    html = """<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
        <a href="https://example.com" target="_blank">Example (opens in a new tab)</a>
    </body></html>"""
    page = await _build_helper(make_page, html)
    results = await run_links_test(page)
    codes = [e['err'] for e in results['errors']]
    assert 'ErrLinkOpensNewWindowNoWarning' not in codes


# ---------------------------------------------------------------------------
# Bug C — parse_color 3-digit hex expansion + narrowed excepts
# ---------------------------------------------------------------------------

def test_parse_color_six_digit_hex() -> None:
    assert parse_color('#aabbcc') == {'r': 170, 'g': 187, 'b': 204, 'a': 1.0}


def test_parse_color_three_digit_hex_expands() -> None:
    """#abc must expand to #aabbcc, not fall back to opaque black."""
    assert parse_color('#abc') == {'r': 170, 'g': 187, 'b': 204, 'a': 1.0}


def test_parse_color_three_digit_hex_white() -> None:
    assert parse_color('#fff') == {'r': 255, 'g': 255, 'b': 255, 'a': 1.0}


def test_parse_color_rgb_still_works() -> None:
    assert parse_color('rgb(10, 20, 30)') == {'r': 10, 'g': 20, 'b': 30, 'a': 1.0}


def test_parse_px_does_not_swallow_keyboard_interrupt() -> None:
    """parse_px must use narrowed excepts so KeyboardInterrupt is not swallowed.

    Static guard: the source must contain no bare ``except:`` clauses.
    """
    source = inspect.getsource(parse_px)
    assert 'except:' not in source.replace(' ', ''), (
        "parse_px must not use a bare 'except:' (it would swallow KeyboardInterrupt)"
    )


def test_test_links_module_has_no_bare_except() -> None:
    """No bare ``except:`` anywhere in the links touchpoint module."""
    import auto_a11y.testing.touchpoint_tests.test_links as mod
    source = inspect.getsource(mod)
    # Look for "except:" not preceded by an exception type.
    import re as _re
    bare = _re.findall(r'\bexcept\s*:', source)
    assert not bare, f"found {len(bare)} bare except clause(s) in test_links.py"


# ---------------------------------------------------------------------------
# helper
# ---------------------------------------------------------------------------

async def _build_helper(make_page: PageBuilder, html: str) -> Page:
    return await make_page(html)
