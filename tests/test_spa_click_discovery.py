"""Tests for SPA click-based discovery feature.

The helper-under-test ``_extract_links_via_clicking`` is private by
naming convention (leading underscore) but is invoked here as direct
unit-testing of the implementation. To avoid ``reportPrivateUsage``
errors from pyright, we call it through a small ``getattr`` shim
(``_run_helper``) that returns a typed ``Awaitable[set[str]]``.
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
    ScrapingEngine,
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
    page: object,
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
