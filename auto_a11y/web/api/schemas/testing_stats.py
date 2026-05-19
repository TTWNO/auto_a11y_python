"""Pydantic schemas for the /api/v1 testing-stats and trends endpoints.

Three endpoints under §5.4 of the roadmap surface aggregate metrics that
the legacy ``/testing/api/...`` dashboard helpers already compute. The
underlying calculations live in ``auto_a11y/web/routes/testing.py`` and
return free-form ``dict[str, Any]`` whose key set varies per scope
(global vs project-scoped vs website-scoped vs progress-window) — modeling
each shape precisely here would duplicate dashboard logic without buying
typed clients anything they can actually rely on.

We follow the admin.py precedent and wrap dynamic-keyed bodies in
``RootModel[dict[str, object]]``. Where the legacy helper *does* fix
keys (the trends envelope's ``days`` / ``trend_data`` and the per-day
trend points), we model them precisely.
"""
from __future__ import annotations

from pydantic import Field, RootModel

from auto_a11y.web.api.schemas.common import StrictModel


class TestingStatsOut(RootModel[dict[str, object]]):
    """Response body for ``GET /api/v1/testing/stats``.

    The shape varies per scope: website-scoped responses always carry
    ``website_count``, ``total_pages``, ``tested_pages``,
    ``untested_pages``, ``total_violations``, ``total_warnings``,
    ``test_coverage``; project- and global-scoped responses surface
    whatever :func:`get_project_stats` / :func:`calculate_aggregate_stats`
    returns. ``completed_today`` is appended unconditionally by the
    handler. Wrapping as a RootModel keeps the wire body the bare object
    the legacy clients expect while preserving the @document contract.
    """


class TrendPointOut(StrictModel):
    """One day of trend data in the ``trend_data`` array.

    Shape mirrors :func:`get_trend_data`'s entries: a date string plus
    integer counters. The legacy helper always emits ``violations``,
    ``warnings``, ``tests``; ``tests`` defaults to 0 so older fixtures
    that never received traffic round-trip cleanly.
    """

    date: str
    violations: int
    warnings: int
    tests: int = 0


class TrendsOut(StrictModel):
    """Response body for ``GET /api/v1/test-runs/trends``."""

    days: int = Field(ge=1, le=365)
    trend_data: list[TrendPointOut]


class TrendsProgressOut(RootModel[dict[str, object]]):
    """Response body for ``GET /api/v1/test-runs/trends/progress``.

    :func:`calculate_progress_metrics` returns a free-form dict whose
    keys depend on whether the scope yields tested-page snapshots,
    historical comparisons, or both. RootModel passes the body through
    as a bare object (no ``data`` envelope), matching the legacy
    handler.
    """
