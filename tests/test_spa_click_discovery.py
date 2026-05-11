"""Tests for SPA click-based discovery feature.

The helper-under-test ``_extract_links_via_clicking`` is private by
naming convention (leading underscore) but is invoked here as direct
unit-testing of the implementation. To avoid ``reportPrivateUsage``
errors from pyright, we call it through a small ``getattr`` shim
(``_run_helper``) that returns a typed ``Awaitable[set[str]]``.

The integration tests at the bottom exercise ``_extract_links`` (the
public-facing method) through a similar ``getattr``-based shim so that
pyright's ``reportPrivateUsage`` rule is not triggered there either.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable
from typing import Protocol, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

from auto_a11y.core.scraper import (
    CLICK_TIMEOUT_MS,
    DESTRUCTIVE_ANCHOR_PATTERN,
    MAX_CLICK_CANDIDATES_PER_PAGE,
    POST_CLICK_SETTLE_MS,
    SPA_INITIAL_SETTLE_MS,
    ScrapingEngine,
    ClickablePage,
)
from auto_a11y.models.discovery_run import DiscoveryRun
from auto_a11y.models.website import ScrapingConfig, Website


class _ExtractLinksViaClicking(Protocol):
    """Typed signature of ``ScrapingEngine._extract_links_via_clicking``."""

    def __call__(
        self,
        *,
        page: object,
        current_url: str,
        website: Website,
        base_domain: str,
        base_path: str,
    ) -> Awaitable[set[str]]: ...


def _run_helper(
    engine: ScrapingEngine,
    *,
    page: ClickablePage,
    current_url: str,
    website: Website,
    base_domain: str,
    base_path: str = "",
) -> Awaitable[set[str]]:
    """Call the private SPA-click helper without tripping reportPrivateUsage.

    The leading underscore on ``_extract_links_via_clicking`` is naming
    convention only — these tests are unit tests of that exact method,
    so the access is legitimate. Going through ``getattr`` makes the
    intent explicit and keeps the call site fully typed.

    ``page`` is typed as ``ClickablePage`` so that pyright enforces the
    structural Protocol check on whatever fake is passed in — if ``_FakePage``
    drifts from the Protocol, a type error appears here rather than silently.
    """
    fn = cast(_ExtractLinksViaClicking, getattr(engine, "_extract_links_via_clicking"))
    return fn(
        page=page,
        current_url=current_url,
        website=website,
        base_domain=base_domain,
        base_path=base_path,
    )


class TestScrapingConfigSpaClickDiscovery:
    def test_default_is_false(self) -> None:
        cfg = ScrapingConfig()
        assert cfg.spa_click_discovery is False

    def test_round_trip_preserves_true(self) -> None:
        cfg = ScrapingConfig(spa_click_discovery=True)
        restored = ScrapingConfig.from_dict(cfg.to_dict())
        assert restored.spa_click_discovery is True

    def test_from_dict_missing_key_defaults_false(self) -> None:
        restored = ScrapingConfig.from_dict({"max_pages": 100})
        assert restored.spa_click_discovery is False


class TestDiscoveryRunSpaClickDiscovery:
    def test_default_is_false(self) -> None:
        run = DiscoveryRun(website_id="wid-1")
        assert run.spa_click_discovery is False

    def test_round_trip_preserves_true(self) -> None:
        run = DiscoveryRun(website_id="wid-1", spa_click_discovery=True)
        restored = DiscoveryRun.from_dict(run.to_dict())
        assert restored.spa_click_discovery is True

    def test_from_dict_missing_key_defaults_false(self) -> None:
        restored = DiscoveryRun.from_dict({"website_id": "wid-1"})
        assert restored.spa_click_discovery is False


class TestSpaClickModuleConstants:
    def test_constants_exist_and_have_sane_values(self) -> None:
        assert MAX_CLICK_CANDIDATES_PER_PAGE == 50
        assert CLICK_TIMEOUT_MS == 5000
        assert POST_CLICK_SETTLE_MS == 1500
        assert SPA_INITIAL_SETTLE_MS == 2500

    def test_destructive_pattern_matches_common_actions(self) -> None:
        for word in ["Logout", "log out", "Sign Out", "sign out",
                     "Delete account", "Remove user", "Submit form",
                     "Unsubscribe"]:
            assert DESTRUCTIVE_ANCHOR_PATTERN.search(word) is not None

    def test_destructive_pattern_does_not_match_benign(self) -> None:
        for word in ["Logbook", "Sign in", "Login", "Item details",
                     "Home", "Dashboard"]:
            assert DESTRUCTIVE_ANCHOR_PATTERN.search(word) is None


def _make_engine() -> ScrapingEngine:
    """ScrapingEngine with mocked database + browser manager.

    Uses setattr instead of attribute assignment to avoid mypy/pyright
    complaints about replacing a typed BrowserManager with a MagicMock.
    """
    engine = ScrapingEngine(database=MagicMock(), browser_config={})
    setattr(engine, "browser_manager", MagicMock())
    setattr(engine.browser_manager, "goto", AsyncMock())
    setattr(engine.browser_manager, "config", {})
    return engine


def _make_website(*, spa_click: bool = True,
                  follow_external: bool = False,
                  excluded_paths: list[str] | None = None) -> Website:
    cfg = ScrapingConfig(
        spa_click_discovery=spa_click,
        follow_external=follow_external,
        excluded_paths=excluded_paths or [],
    )
    w = Website(project_id="pid", url="https://example.com/",
                name="ex", scraping_config=cfg)
    w.mongo_id = ObjectId()
    return w


def _engine_with_mocked_page(candidates: list[dict[str, object]]) -> tuple[
    ScrapingEngine, MagicMock
]:
    """Build an engine + mock PlaywrightPage that returns ``candidates`` from evaluate."""
    engine = _make_engine()
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=candidates)
    page.click = AsyncMock()
    page.url = "https://example.com/parent"
    return engine, page


@pytest.mark.asyncio
async def test_candidate_collection_filters_anchors_with_usable_href() -> None:
    engine, page = _engine_with_mocked_page([
        {"hasUsableHref": True,  "text": "Home",      "ariaLabel": "", "selector": "/html/body/a[1]"},
        {"hasUsableHref": False, "text": "Dashboard", "ariaLabel": "", "selector": "/html/body/a[2]"},
    ])
    website = _make_website()

    # At this stage the helper returns an empty set (no click loop yet).
    # We assert that the evaluate JS was run exactly once.
    result = await _run_helper(
        engine,
        page=page, current_url="https://example.com/parent",
        website=website, base_domain="example.com", base_path="",
    )
    assert result == set()
    page.evaluate.assert_called_once()


@pytest.mark.asyncio
async def test_candidate_collection_filters_destructive_anchors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine, page = _engine_with_mocked_page([
        {"hasUsableHref": False, "text": "Logout", "ariaLabel": "", "selector": "/html/body/a[1]"},
        {"hasUsableHref": False, "text": "Settings", "ariaLabel": "", "selector": "/html/body/a[2]"},
    ])
    website = _make_website()

    with caplog.at_level(logging.INFO, logger="auto_a11y.core.scraper"):
        await _run_helper(
            engine,
            page=page, current_url="https://example.com/parent",
            website=website, base_domain="example.com", base_path="",
        )
    # The destructive filter should log one info skip for "Logout".
    assert any("destructive" in r.message.lower() and "logout" in r.message.lower()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_candidate_collection_caps_at_max(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine, page = _engine_with_mocked_page([
        {"hasUsableHref": False, "text": f"Link {i}", "ariaLabel": "",
         "selector": f"/html/body/a[{i}]"}
        for i in range(MAX_CLICK_CANDIDATES_PER_PAGE + 10)
    ])
    website = _make_website()

    with caplog.at_level(logging.INFO, logger="auto_a11y.core.scraper"):
        await _run_helper(
            engine,
            page=page, current_url="https://example.com/parent",
            website=website, base_domain="example.com", base_path="",
        )
    # The "Starting click-based discovery" info log line should report exactly the cap.
    starting_lines = [r.message for r in caplog.records
                      if "Starting click-based discovery" in r.message]
    assert len(starting_lines) == 1
    assert f"{MAX_CLICK_CANDIDATES_PER_PAGE} candidates" in starting_lines[0]


class _FakePage:
    """Minimal Playwright-like page for click-loop tests.

    Satisfies the ``ClickablePage`` protocol defined in scraper.py. Tracks
    navigation calls and lets the test script the URL each click ends at.
    """
    def __init__(self, candidates_payload: list[dict[str, object]]) -> None:
        self._candidates = candidates_payload
        self.url: str = "https://example.com/parent"
        self.click_calls: list[tuple[str, float | None]] = []
        self.eval_calls: list[str] = []
        # Sequence of URLs to return after each click; popped left-to-right.
        self.url_after_click: list[str] = []
        # Selectors for which query_selector should report "not found".
        self.missing_selectors: set[str] = set()

    async def evaluate(self, expression: str, arg: object = None) -> object:
        self.eval_calls.append(expression)
        # First call: return the candidate list.
        if "querySelectorAll('a')" in expression:
            return self._candidates
        # Subsequent calls: target-strip helper. Returns None.
        return None

    async def click(self, selector: str, *, timeout: float | None = None) -> None:
        self.click_calls.append((selector, timeout))
        # Simulate the SPA navigation: update self.url to the next scripted URL.
        if self.url_after_click:
            self.url = self.url_after_click.pop(0)

    async def query_selector(self, selector: str) -> object:
        if selector in self.missing_selectors:
            return None
        return MagicMock()


@pytest.mark.asyncio
async def test_click_loop_captures_url_after_pushstate() -> None:
    engine = _make_engine()
    page = _FakePage([
        {"hasUsableHref": False, "text": "Dashboard",
         "ariaLabel": "", "selector": "/html/body/a[1]"},
    ])
    page.url_after_click = ["https://example.com/parent/dashboard"]
    website = _make_website()

    result = await _run_helper(
        engine, page=page,
        current_url="https://example.com/parent",
        website=website, base_domain="example.com", base_path="",
    )
    assert result == {"https://example.com/parent/dashboard"}
    # Re-navigated to parent once (one click → one renavigation). The
    # ``browser_manager`` attribute is a MagicMock for tests (see
    # ``_make_engine``); cast its ``goto`` to AsyncMock for typed access
    # to ``await_count``.
    goto_mock = cast(AsyncMock, getattr(engine.browser_manager, "goto"))
    assert goto_mock.await_count == 1
    assert page.click_calls and page.click_calls[0][0] == "/html/body/a[1]"


@pytest.mark.asyncio
async def test_click_loop_skips_when_url_unchanged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = _make_engine()
    page = _FakePage([
        {"hasUsableHref": False, "text": "Open menu",
         "ariaLabel": "", "selector": "/html/body/a[1]"},
    ])
    # No URL change after click: page.url stays at parent.
    page.url_after_click = ["https://example.com/parent"]
    website = _make_website()

    with caplog.at_level(logging.WARNING, logger="auto_a11y.core.scraper"):
        result = await _run_helper(
            engine, page=page,
            current_url="https://example.com/parent",
            website=website, base_domain="example.com", base_path="",
        )
    assert result == set()
    assert any("no URL change" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_click_loop_skips_anchor_not_found_after_renavigation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = _make_engine()
    page = _FakePage([
        {"hasUsableHref": False, "text": "Phantom",
         "ariaLabel": "", "selector": "/html/body/a[99]"},
    ])
    page.missing_selectors = {"/html/body/a[99]"}
    website = _make_website()

    with caplog.at_level(logging.WARNING, logger="auto_a11y.core.scraper"):
        result = await _run_helper(
            engine, page=page,
            current_url="https://example.com/parent",
            website=website, base_domain="example.com", base_path="",
        )
    assert result == set()
    assert any("could not locate anchor" in r.message.lower() for r in caplog.records)


@pytest.mark.asyncio
async def test_click_loop_excludes_off_domain_post_click_urls() -> None:
    engine = _make_engine()
    page = _FakePage([
        {"hasUsableHref": False, "text": "External",
         "ariaLabel": "", "selector": "/html/body/a[1]"},
    ])
    page.url_after_click = ["https://evil.example.org/something"]
    website = _make_website(follow_external=False)

    result = await _run_helper(
        engine, page=page,
        current_url="https://example.com/parent",
        website=website, base_domain="example.com", base_path="",
    )
    assert result == set()  # Off-domain URL filtered out.


@pytest.mark.asyncio
async def test_click_loop_respects_excluded_paths() -> None:
    engine = _make_engine()
    page = _FakePage([
        {"hasUsableHref": False, "text": "Admin panel",
         "ariaLabel": "", "selector": "/html/body/a[1]"},
    ])
    page.url_after_click = ["https://example.com/admin/dashboard"]
    website = _make_website(excluded_paths=["/admin"])

    result = await _run_helper(
        engine, page=page,
        current_url="https://example.com/parent",
        website=website, base_domain="example.com", base_path="",
    )
    assert result == set()


@pytest.mark.asyncio
async def test_click_loop_strips_target_blank_before_click() -> None:
    engine = _make_engine()
    page = _FakePage([
        {"hasUsableHref": False, "text": "Pop",
         "ariaLabel": "", "selector": "/html/body/a[1]"},
    ])
    page.url_after_click = ["https://example.com/popped"]
    website = _make_website()

    result = await _run_helper(
        engine, page=page,
        current_url="https://example.com/parent",
        website=website, base_domain="example.com", base_path="",
    )
    assert result == {"https://example.com/popped"}
    # Confirm a target-stripping evaluate call ran *before* the click.
    target_strip_calls = [s for s in page.eval_calls
                          if "removeAttribute" in s and "target" in s]
    assert target_strip_calls, "Expected a target-strip JS call before clicking"


@pytest.mark.asyncio
async def test_click_loop_continues_after_per_click_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = _make_engine()
    page = _FakePage([
        {"hasUsableHref": False, "text": "Breaks",
         "ariaLabel": "", "selector": "/html/body/a[1]"},
        {"hasUsableHref": False, "text": "Works",
         "ariaLabel": "", "selector": "/html/body/a[2]"},
    ])
    # Make the first click raise; second click navigates normally.
    original_click = page.click

    async def click_side_effect(selector: str, *, timeout: float | None = None) -> None:
        if selector == "/html/body/a[1]":
            raise RuntimeError("simulated click failure")
        await original_click(selector, timeout=timeout)

    setattr(page, "click", click_side_effect)
    page.url_after_click = ["https://example.com/works"]
    website = _make_website()

    with caplog.at_level(logging.WARNING, logger="auto_a11y.core.scraper"):
        result = await _run_helper(
            engine, page=page,
            current_url="https://example.com/parent",
            website=website, base_domain="example.com", base_path="",
        )
    assert result == {"https://example.com/works"}
    assert any("simulated click failure" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Integration tests: _extract_links wires the helper in when flag is on
# ---------------------------------------------------------------------------


class _ExtractLinks(Protocol):
    """Typed signature of ``ScrapingEngine._extract_links``."""

    def __call__(
        self,
        *,
        page: object,
        current_url: str,
        website: Website,
        base_domain: str,
        base_path: str,
    ) -> Awaitable[set[str]]: ...


def _run_extract_links(
    engine: ScrapingEngine,
    *,
    page: object,
    current_url: str,
    website: Website,
    base_domain: str,
    base_path: str = "",
) -> Awaitable[set[str]]:
    """Call the private ``_extract_links`` method without tripping reportPrivateUsage."""
    fn = cast(_ExtractLinks, getattr(engine, "_extract_links"))
    return fn(
        page=page,
        current_url=current_url,
        website=website,
        base_domain=base_domain,
        base_path=base_path,
    )


@pytest.mark.asyncio
async def test_extract_links_does_not_call_helper_when_flag_off() -> None:
    engine = _make_engine()
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=[])  # No href anchors.
    website = _make_website(spa_click=False)

    # Spy on the helper via setattr to avoid mypy method-assign warning.
    spy = AsyncMock(return_value={"unused"})
    setattr(engine, "_extract_links_via_clicking", spy)

    result = await _run_extract_links(
        engine,
        page=page, current_url="https://example.com/",
        website=website, base_domain="example.com", base_path="",
    )
    spy.assert_not_called()
    assert result == set()


@pytest.mark.asyncio
async def test_extract_links_unions_helper_result_when_flag_on() -> None:
    engine = _make_engine()
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=[])  # No href anchors.
    website = _make_website(spa_click=True)

    spy = AsyncMock(return_value={"https://example.com/spa-route"})
    setattr(engine, "_extract_links_via_clicking", spy)

    result = await _run_extract_links(
        engine,
        page=page, current_url="https://example.com/",
        website=website, base_domain="example.com", base_path="",
    )
    spy.assert_awaited_once()
    assert "https://example.com/spa-route" in result


@pytest.mark.asyncio
async def test_extract_links_swallows_helper_exception_and_returns_href_links(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = _make_engine()
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=[])  # No href anchors found.
    website = _make_website(spa_click=True)

    boom = AsyncMock(side_effect=RuntimeError("simulated helper failure"))
    setattr(engine, "_extract_links_via_clicking", boom)

    with caplog.at_level(logging.WARNING, logger="auto_a11y.core.scraper"):
        result = await _run_extract_links(
            engine, page=page, current_url="https://example.com/",
            website=website, base_domain="example.com", base_path="",
        )
    # The helper raised; the outer method must still return what it had.
    assert result == set()
    assert any("Click-based discovery failed" in r.message
               and "simulated helper failure" in r.message
               for r in caplog.records)


@pytest.mark.asyncio
async def test_discover_website_records_click_discovery_mode_on_run() -> None:
    """Verify the DiscoveryRun created at the start carries the flag through."""
    captured: list[DiscoveryRun] = []
    engine = _make_engine()

    def fake_create_discovery_run(run: DiscoveryRun) -> str:
        captured.append(run)
        return "run-1"

    # Use setattr to monkey-patch db methods without triggering
    # mypy/pyright complaints about MagicMock attribute assignment.
    setattr(engine.db, "create_discovery_run", fake_create_discovery_run)
    # Make the next db call after create_discovery_run raise, to short-circuit.
    setattr(
        engine.db,
        "get_discovery_runs",
        MagicMock(side_effect=RuntimeError("stop here")),
    )

    website = _make_website(spa_click=True)

    with pytest.raises(Exception):
        await engine.discover_website(website=website)

    assert captured, "DiscoveryRun should have been created before the raise"
    assert captured[0].spa_click_discovery is True


@pytest.mark.asyncio
async def test_extract_links_settles_before_reading_dom_when_spa_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When spa_click_discovery is on, sleep before reading the DOM."""
    import asyncio as _asyncio

    sleep_durations: list[float] = []

    async def fake_sleep(duration: float) -> None:
        sleep_durations.append(duration)

    # Patch asyncio.sleep on the real asyncio module — this is what
    # scraper.py calls (it imports asyncio, not ``from asyncio import sleep``).
    monkeypatch.setattr(_asyncio, "sleep", fake_sleep)

    engine = _make_engine()
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=[])
    website = _make_website(spa_click=True)

    await _run_extract_links(
        engine, page=page, current_url="https://example.com/",
        website=website, base_domain="example.com", base_path="",
    )
    expected = SPA_INITIAL_SETTLE_MS / 1000
    assert expected in sleep_durations, (
        f"expected initial-settle sleep of {expected}s; got {sleep_durations}"
    )


@pytest.mark.asyncio
async def test_extract_links_does_not_settle_when_spa_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When spa_click_discovery is off, no settle delay is added."""
    import asyncio as _asyncio

    sleep_durations: list[float] = []

    async def fake_sleep(duration: float) -> None:
        sleep_durations.append(duration)

    monkeypatch.setattr(_asyncio, "sleep", fake_sleep)

    engine = _make_engine()
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=[])
    website = _make_website(spa_click=False)

    await _run_extract_links(
        engine, page=page, current_url="https://example.com/",
        website=website, base_domain="example.com", base_path="",
    )
    expected = SPA_INITIAL_SETTLE_MS / 1000
    assert expected not in sleep_durations, (
        f"unexpected initial-settle sleep when flag off; got {sleep_durations}"
    )


@pytest.mark.asyncio
async def test_click_loop_settles_after_renavigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After re-navigating to the parent, wait for SPA to re-render before locating anchor."""
    import asyncio as _asyncio

    sleep_durations: list[float] = []

    async def fake_sleep(duration: float) -> None:
        sleep_durations.append(duration)

    monkeypatch.setattr(_asyncio, "sleep", fake_sleep)

    engine = _make_engine()
    page = _FakePage([
        {"hasUsableHref": False, "text": "Dashboard",
         "ariaLabel": "", "selector": "/html/body/a[1]"},
    ])
    page.url_after_click = ["https://example.com/parent/dashboard"]
    website = _make_website()

    await _run_helper(
        engine, page=page,
        current_url="https://example.com/parent",
        website=website, base_domain="example.com", base_path="",
    )
    # The helper should have called asyncio.sleep at least twice:
    # once for the re-navigation settle, and once for POST_CLICK_SETTLE_MS.
    expected_initial = SPA_INITIAL_SETTLE_MS / 1000
    expected_post_click = POST_CLICK_SETTLE_MS / 1000
    assert expected_initial in sleep_durations
    assert expected_post_click in sleep_durations
