"""Audit tests for the per-page CSS-focus capture cache.

These guard against two defects in the prior ``id(page)``-keyed implementation:

1. CPython reuses ``id()`` values after an object is garbage collected, so a new
   ``Page`` could alias a closed page's cached ``CSSFocusCapture`` (stale read).
2. Any path that dropped a page without calling ``clear_css_capture_for_page``
   leaked the cache entry permanently.

The fix keys the cache on the page object itself via ``weakref.WeakKeyDictionary``
so entries auto-evict when the page is garbage collected.
"""

from __future__ import annotations

import gc
import weakref

# Import the ``core`` package first to settle a pre-existing circular import
# between ``auto_a11y.testing`` and ``auto_a11y.core.testing_job`` that triggers
# only when ``auto_a11y.testing`` is imported as the very first submodule.
import auto_a11y.core
from playwright.async_api import Page

from auto_a11y.testing.css_focus_capture import (
    CSSFocusCapture,
    clear_css_capture_for_page,
    css_capture_cache_size,
    get_css_capture_for_page,
    set_css_capture_for_page,
)

# Reference the side-effect import so static analysers see it as used.
assert auto_a11y.core is not None


def _make_page() -> Page:
    """Create a bare, weak-referenceable Playwright ``Page`` instance.

    Playwright's ``Page`` is a pure-Python class exposing ``__weakref__`` and
    ``__dict__``. Allocating with ``__new__`` (skipping ``__init__``) yields a
    genuine ``Page`` object — exactly the type the cache is keyed on in
    production — without needing a live browser. This keeps the test exercising
    the real key type rather than a duck-typed stand-in.
    """
    return Page.__new__(Page)


def test_distinct_pages_do_not_alias() -> None:
    """Two distinct page objects must not share a cached capture."""
    page_a = _make_page()
    page_b = _make_page()
    capture_a = CSSFocusCapture()
    capture_b = CSSFocusCapture()

    set_css_capture_for_page(page_a, capture_a)
    set_css_capture_for_page(page_b, capture_b)

    assert get_css_capture_for_page(page_a) is capture_a
    assert get_css_capture_for_page(page_b) is capture_b
    assert get_css_capture_for_page(page_a) is not get_css_capture_for_page(page_b)


def test_clear_removes_entry_no_stale_read() -> None:
    """After clearing a page, its entry must be gone (no stale read)."""
    page = _make_page()
    capture = CSSFocusCapture()
    set_css_capture_for_page(page, capture)
    assert get_css_capture_for_page(page) is capture

    clear_css_capture_for_page(page)
    assert get_css_capture_for_page(page) is None


def test_clear_is_idempotent_for_unknown_page() -> None:
    """Clearing a page that was never stored must not raise."""
    page = _make_page()
    # Must not raise even though nothing is stored.
    clear_css_capture_for_page(page)
    assert get_css_capture_for_page(page) is None


def test_entry_auto_evicts_when_page_gc_collected() -> None:
    """Dropping the strong ref to a page must auto-evict its cache entry.

    This is the core leak/stale-alias guard: the WeakKeyDictionary releases the
    entry once the page is garbage collected, so no later object can read a
    stale capture and the cache cannot leak.
    """
    baseline = css_capture_cache_size()
    page = _make_page()
    capture = CSSFocusCapture()
    set_css_capture_for_page(page, capture)
    assert css_capture_cache_size() == baseline + 1

    # Independent observer to confirm the page is actually collected.
    page_ref = weakref.ref(page)
    assert page_ref() is not None

    del page
    gc.collect()

    assert page_ref() is None, "page should have been garbage collected"
    # Entry must have auto-evicted; cache returns to its baseline size.
    assert css_capture_cache_size() == baseline
