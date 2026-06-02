"""
Regression tests for four LOW code-audit findings across touchpoint tests.

Bug A (test_page.py): the two page-title length *warnings* were counted in
opposite buckets — ``WarnPageTitleTooShort`` incremented ``elements_failed``
while ``WarnPageTitleTooLong`` incremented ``elements_passed``. Both describe a
present-but-suboptimal single title element and must be counted consistently.
Also a bare ``except: pass`` around the config-read ``page.evaluate`` swallowed
``KeyboardInterrupt``; it must be narrowed to ``except Exception``.

Bug B (test_tabindex.py): ``ErrAnchorTargetTabindex`` failures for in-page
targets (``*[id]``) incremented ``elements_failed`` even though only
``[tabindex]`` elements are counted in ``elements_tested`` — so failed could
exceed tested. Failed must stay <= tested.

Bug C (test_title_attribute.py): a titled iframe matches both ``[title]`` and
``iframe`` selectors, so it was double-counted in ``elements_tested``. Each
element must be counted once.

Bug D (test_language.py): ``validate_language_code`` only checked primary-code
casing when a hyphen was present, so ``lang="EN"`` (no region) passed the
IGNORECASE format pattern and was treated as correctly formatted. An
all-uppercase region-less code must be flagged as mis-formatted.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import AsyncIterator, Awaitable
from types import ModuleType
from typing import Any, Callable, cast

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright, Page

# Import auto_a11y.core before auto_a11y.testing to avoid a pre-existing
# circular import (auto_a11y.testing -> core.testing_job -> auto_a11y.testing)
# that only triggers when the testing package is imported first. The module is
# imported purely for its side effect of populating sys.modules in the right
# order, then deleted so it does not read as an unused binding.
import auto_a11y.core as _core

del _core

# The touchpoint_tests package __init__ re-exports each submodule's primary
# function under the same name as the submodule (e.g. ``test_language`` the
# function shadows the ``test_language`` module), so a plain
# ``import ...test_language`` binds the function. Resolve the actual submodules
# with importlib to reach module-level helpers such as ``validate_language_code``.
_PKG = "auto_a11y.testing.touchpoint_tests"
test_page_mod: ModuleType = importlib.import_module(f"{_PKG}.test_page")
test_tabindex_mod: ModuleType = importlib.import_module(f"{_PKG}.test_tabindex")
test_title_attribute_mod: ModuleType = importlib.import_module(f"{_PKG}.test_title_attribute")
test_language_mod: ModuleType = importlib.import_module(f"{_PKG}.test_language")

_PageTestFn = Callable[[Page], Awaitable[dict[str, Any]]]


def _run_page(page: Page) -> Awaitable[dict[str, Any]]:
    return cast(_PageTestFn, getattr(test_page_mod, "test_page"))(page)


def _run_tabindex(page: Page) -> Awaitable[dict[str, Any]]:
    return cast(_PageTestFn, getattr(test_tabindex_mod, "test_tabindex"))(page)


def _run_title_attribute(page: Page) -> Awaitable[dict[str, Any]]:
    return cast(_PageTestFn, getattr(test_title_attribute_mod, "test_title_attribute"))(page)


@pytest_asyncio.fixture
async def page() -> AsyncIterator[Page]:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context()
        pg = await ctx.new_page()
        yield pg
        await browser.close()


# ---------------------------------------------------------------------------
# Bug A — test_page.py: consistent title-warning accounting + narrowed except
# ---------------------------------------------------------------------------


def _short_title_html() -> str:
    return "<!doctype html><html><head><title>Hi</title></head><body>x</body></html>"


def _long_title_html() -> str:
    long = "A" * 200
    return f"<!doctype html><html><head><title>{long}</title></head><body>x</body></html>"


class TestPageTitleConsistentCounting:
    @pytest.mark.asyncio
    async def test_short_and_long_title_counted_symmetrically(self, page: Page) -> None:
        await page.set_content(_short_title_html())
        short_res = await _run_page(page)

        await page.set_content(_long_title_html())
        long_res = await _run_page(page)

        # Both are a single present-but-suboptimal title => one warning each.
        assert len(short_res["warnings"]) == 1
        assert short_res["warnings"][0]["err"] == "WarnPageTitleTooShort"
        assert len(long_res["warnings"]) == 1
        assert long_res["warnings"][0]["err"] == "WarnPageTitleTooLong"

        # The two branches must land in the SAME bucket as each other.
        assert short_res["elements_passed"] == long_res["elements_passed"], (
            "short/long title warnings must increment elements_passed identically"
        )
        assert short_res["elements_failed"] == long_res["elements_failed"], (
            "short/long title warnings must increment elements_failed identically"
        )

    def test_config_read_except_is_narrowed(self) -> None:
        """A bare ``except:`` swallows KeyboardInterrupt/SystemExit. The
        config-read guard must be ``except Exception`` (structural check)."""
        src = inspect.getsource(cast(_PageTestFn, getattr(test_page_mod, "test_page")))
        assert "except:" not in src, "bare 'except:' must be narrowed to 'except Exception'"


# ---------------------------------------------------------------------------
# Bug B — test_tabindex.py: elements_failed must not exceed elements_tested
# ---------------------------------------------------------------------------


class TestTabindexFailedNotExceedTested:
    @pytest.mark.asyncio
    async def test_in_page_target_failures_bounded_by_tested(self, page: Page) -> None:
        # One [tabindex] element (so the test is applicable / elements_tested>=1)
        # plus several in-page link targets that are NON-interactive containers
        # missing tabindex="-1" -> each produces an ErrAnchorTargetTabindex.
        html = """
        <!doctype html><html><head><title>Tabindex test page</title></head>
        <body>
          <a href="#sec1">go 1</a>
          <a href="#sec2">go 2</a>
          <a href="#sec3">go 3</a>
          <div id="sec1">section 1</div>
          <div id="sec2">section 2</div>
          <div id="sec3">section 3</div>
          <span tabindex="0">focusable span</span>
        </body></html>
        """
        await page.set_content(html)
        res = await _run_tabindex(page)

        # Sanity: the in-page-target failures actually fired.
        anchor_errs = [e for e in res["errors"] if e["err"] == "ErrAnchorTargetTabindex"]
        assert len(anchor_errs) >= 3, "expected in-page-target tabindex failures"

        assert res["elements_failed"] <= res["elements_tested"], (
            f"elements_failed ({res['elements_failed']}) must not exceed "
            f"elements_tested ({res['elements_tested']})"
        )


# ---------------------------------------------------------------------------
# Bug C — test_title_attribute.py: titled iframe counted once in tested
# ---------------------------------------------------------------------------


class TestTitleAttributeIframeCountedOnce:
    @pytest.mark.asyncio
    async def test_titled_iframe_not_double_counted(self, page: Page) -> None:
        # A single iframe WITH a title attribute: it matches both the
        # iframe selector and the [title] selector but is one element.
        html = """
        <!doctype html><html><head><title>iframe title page</title></head>
        <body>
          <iframe title="Embedded content" src="about:blank"></iframe>
        </body></html>
        """
        await page.set_content(html)
        res = await _run_title_attribute(page)

        assert res.get("applicable") is True
        assert res["elements_tested"] == 1, (
            f"a single titled iframe must count once, got {res['elements_tested']}"
        )


# ---------------------------------------------------------------------------
# Bug D — test_language.py: all-uppercase region-less code is mis-formatted
# ---------------------------------------------------------------------------


def _validate() -> Callable[[str], tuple[bool, bool, bool, bool, str, str]]:
    fn = getattr(test_language_mod, "validate_language_code")
    return cast(Callable[[str], tuple[bool, bool, bool, bool, str, str]], fn)


class TestValidateLanguageCodeCase:
    def test_uppercase_regionless_flagged_as_misformatted(self) -> None:
        is_valid_format, is_correct, is_recognized, _region, _primary, _rc = _validate()("EN")
        assert is_valid_format is True, "'EN' should still match the format pattern"
        assert is_correct is False, "'EN' must be flagged as incorrectly formatted"
        # primary language still resolves to a recognized language
        assert is_recognized is True

    def test_lowercase_regionless_is_correct(self) -> None:
        _vf, is_correct, is_recognized, _r, _p, _rc = _validate()("en")
        assert is_correct is True
        assert is_recognized is True

    def test_lowercase_lang_uppercase_region_is_correct(self) -> None:
        _vf, is_correct, _recog, _r, _p, _rc = _validate()("en-US")
        assert is_correct is True

    def test_uppercase_lang_lowercase_region_is_misformatted(self) -> None:
        _vf, is_correct, _recog, _r, _p, _rc = _validate()("EN-us")
        assert is_correct is False
