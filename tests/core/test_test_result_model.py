"""Tests for Violation.unique_id assignment and TargetType guarding.

Covers two model-level fixes in auto_a11y/models/test_result.py:

A. ``Violation.to_dict()`` is pure — it does not mutate the instance.
   A ``unique_id`` is assigned at construction time (not lazily on the
   first ``to_dict()`` call), so repeated serialisation is stable and an
   explicitly supplied id survives a to_dict / from_dict round-trip.

B. ``TestResult.from_dict`` tolerates an unrecognised ``target_type``
   value, defaulting to ``TargetType.PAGE`` instead of raising.
"""
from __future__ import annotations

from typing import Any

from auto_a11y.models.test_result import (
    ImpactLevel,
    TargetType,
    TestResult,
    Violation,
)


def _make_violation(unique_id: str | None = None) -> Violation:
    if unique_id is None:
        # Omit unique_id entirely so the construction-time default fires.
        return Violation(
            id='color-contrast',
            impact=ImpactLevel.HIGH,
            touchpoint='ColorAndContrast',
            description='Insufficient contrast',
        )
    return Violation(
        id='color-contrast',
        impact=ImpactLevel.HIGH,
        touchpoint='ColorAndContrast',
        description='Insufficient contrast',
        unique_id=unique_id,
    )


def test_unique_id_assigned_at_construction() -> None:
    """A Violation gets a unique_id at construction, before any to_dict call."""
    v = _make_violation()
    assert v.unique_id is not None
    assert v.unique_id != ''


def test_to_dict_is_pure_and_stable() -> None:
    """Two to_dict() calls return the same unique_id and don't mutate state."""
    v = _make_violation()
    id_before = v.unique_id

    first = v.to_dict()
    after_first = v.unique_id

    second = v.to_dict()
    after_second = v.unique_id

    # to_dict must not change the instance's unique_id.
    assert id_before == after_first == after_second
    # Both serialisations carry the same id.
    assert first['unique_id'] == second['unique_id'] == id_before


def test_explicit_unique_id_preserved_through_round_trip() -> None:
    """An explicitly provided unique_id is kept through to_dict / from_dict."""
    explicit = 'my-explicit-id-123'
    v = _make_violation(unique_id=explicit)
    assert v.unique_id == explicit

    d = v.to_dict()
    assert d['unique_id'] == explicit

    restored = Violation.from_dict(d)
    assert restored.unique_id == explicit


def test_from_dict_preserves_provided_unique_id() -> None:
    """from_dict preserves an existing unique_id rather than regenerating."""
    data: dict[str, Any] = {
        'id': 'color-contrast',
        'impact': 'high',
        'touchpoint': 'ColorAndContrast',
        'description': 'Insufficient contrast',
        'unique_id': 'persisted-id-abc',
    }
    restored = Violation.from_dict(data)
    assert restored.unique_id == 'persisted-id-abc'


def test_from_dict_unknown_target_type_defaults_to_page() -> None:
    """An unrecognised target_type value defaults to PAGE without raising."""
    data: dict[str, Any] = {
        'page_id': 'p1',
        'website_id': 'w1',
        'target_type': 'bogus',
    }
    tr = TestResult.from_dict(data)
    assert tr.target_type is TargetType.PAGE
