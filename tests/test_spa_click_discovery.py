"""Tests for SPA click-based discovery feature."""
from __future__ import annotations

import logging  # used by Tasks 8+; kept here to avoid mid-file import later

from auto_a11y.core.scraper import (
    CLICK_TIMEOUT_MS,
    DESTRUCTIVE_ANCHOR_PATTERN,
    MAX_CLICK_CANDIDATES_PER_PAGE,
    POST_CLICK_SETTLE_MS,
)
from auto_a11y.models.discovery_run import DiscoveryRun
from auto_a11y.models.website import ScrapingConfig

_ = logging  # referenced so strict checkers don't flag it as unused


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
