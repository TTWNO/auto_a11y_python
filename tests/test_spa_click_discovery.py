"""Tests for SPA click-based discovery feature."""
from __future__ import annotations

from auto_a11y.models.website import ScrapingConfig
from auto_a11y.models.discovery_run import DiscoveryRun


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
