"""Tests for the DiscoveryRun model's from_dict deserialization."""
from __future__ import annotations

from auto_a11y.models.discovery_run import DiscoveryRun, DiscoveryStatus


def _minimal_data() -> dict[str, object]:
    """Build a minimal valid MongoDB document for DiscoveryRun."""
    return {'website_id': 'w1'}


def test_from_dict_defaults_is_latest_to_true_when_missing() -> None:
    """A legacy document without is_latest should default to True (Bug A)."""
    data = _minimal_data()
    assert 'is_latest' not in data
    run = DiscoveryRun.from_dict(data)
    assert run.is_latest is True


def test_from_dict_with_bogus_status_does_not_raise() -> None:
    """An unknown/legacy status string must not raise (Bug B)."""
    data = _minimal_data()
    data['status'] = 'bogus_value'
    run = DiscoveryRun.from_dict(data)
    assert run.status == DiscoveryStatus.RUNNING


def test_from_dict_preserves_valid_status() -> None:
    """A valid status string should still deserialize correctly."""
    data = _minimal_data()
    data['status'] = 'completed'
    run = DiscoveryRun.from_dict(data)
    assert run.status == DiscoveryStatus.COMPLETED


def test_from_dict_defaults_robots_blocked_count_to_zero_when_missing() -> None:
    """A legacy document without robots_blocked_count should default to 0."""
    data = _minimal_data()
    assert 'robots_blocked_count' not in data
    run = DiscoveryRun.from_dict(data)
    assert run.robots_blocked_count == 0


def test_robots_blocked_count_survives_round_trip() -> None:
    """robots_blocked_count must serialize to and deserialize from MongoDB."""
    run = DiscoveryRun(website_id='w1', robots_blocked_count=7)
    restored = DiscoveryRun.from_dict(run.to_dict())
    assert restored.robots_blocked_count == 7
