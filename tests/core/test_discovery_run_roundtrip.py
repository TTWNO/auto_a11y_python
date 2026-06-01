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
