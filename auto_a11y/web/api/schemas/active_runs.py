"""Pydantic schemas for the §5.2 / §5.4 leftover REST endpoints.

Covers four read-only endpoints that don't fit neatly into the existing
schema files:

- ``GET /api/v1/test-runs?status=active`` — active TESTING jobs.
- ``GET /api/v1/websites?project_id=<id>`` — top-level websites listing.
- ``GET /api/v1/test-runs/trends/detailed`` — rich-filter trend data.
- ``GET /api/v1/test-runs/trends/compare`` — period-vs-period comparison.

The detailed/compare endpoints surface free-form payloads
(:func:`get_detailed_trend_data` / :func:`compare_periods` return
dynamic-keyed dicts that vary per granularity, filter combination, and
period scope), so we wrap them in ``RootModel[dict[str, object]]``
following the admin.py precedent — the wire body matches the legacy
clients' expectations and the OpenAPI doc remains accurate without
over-claiming a structure the helpers don't guarantee.
"""
from __future__ import annotations

from typing import Optional

from pydantic import RootModel

from auto_a11y.web.api.schemas.common import StrictModel
from auto_a11y.web.api.schemas.websites import WebsiteOut


class ActiveTestRunOut(StrictModel):
    """One row in the active-test-runs listing.

    Mirrors the legacy ``/testing/api/active-tests`` shape. ``progress``
    is a percentage-ish integer the worker emits, not a nested dict (in
    contrast to the JobManager top-level ``progress`` field).
    """

    job_id: Optional[str] = None
    status: Optional[str] = None
    website_id: Optional[str] = None
    website_name: Optional[str] = None
    progress: int = 0
    pages_completed: int = 0
    pages_total: int = 0
    current_page: str = ""
    violations_found: int = 0
    started_at: Optional[str] = None
    created_at: Optional[str] = None


class ActiveTestRunsOut(StrictModel):
    """Response body for ``GET /api/v1/test-runs?status=active``.

    Bare ``{items, count}`` envelope — no cursor pagination because the
    active set is bounded by the worker pool size (single-digit rows in
    every realistic deployment).
    """

    items: list[ActiveTestRunOut]
    count: int


class WebsitesListBareOut(StrictModel):
    """Response body for ``GET /api/v1/websites``.

    Bare ``{items}`` envelope (no pagination). The top-level listing
    requires ``?project_id=`` for non-superadmin callers, so the
    expected row count is the number of websites in a single project —
    a few dozen at most. Adding cursor pagination here is deferred
    until somebody actually has that many.
    """

    items: list[WebsiteOut]


class TrendsDetailedOut(RootModel[dict[str, object]]):
    """Response body for ``GET /api/v1/test-runs/trends/detailed``.

    The legacy :func:`get_detailed_trend_data` returns a dict whose key
    set varies with ``granularity``, ``include_breakdown``, and the
    filter arrays. RootModel keeps the bare wire body the legacy
    clients expect.
    """


class TrendsCompareOut(RootModel[dict[str, object]]):
    """Response body for ``GET /api/v1/test-runs/trends/compare``.

    Period-vs-period comparison output from :func:`compare_periods`. The
    nested period blocks carry the same shape as the detailed endpoint;
    the outer envelope adds the summary deltas. RootModel preserves the
    bare object on the wire.
    """
