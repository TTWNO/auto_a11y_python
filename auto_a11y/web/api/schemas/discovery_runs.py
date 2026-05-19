"""Pydantic schemas for the /api/v1 discovery-run endpoints.

DiscoveryRun documents record a single page-discovery crawl for a
website. The write endpoint (POST /websites/<id>/discoveries) queues a
job through :func:`start_website_discovery`; the read endpoints in §5.2
of the roadmap expose stored runs and the cancel endpoints in §5.2/§5.4
flip them through the JobManager.

The wire shape mirrors ``_serialize_discovery_run`` in ``api.py``
field-for-field: stringified Mongo id, ISO 8601 timestamps, plain enum
string for ``status``, list of failed-page dicts kept as
``list[dict[str, object]]`` because the failure reason / url shapes
diverge per-error-class and modeling them precisely would inflate this
file without buying typed clients anything.
"""
from __future__ import annotations

from typing import Optional

from auto_a11y.web.api.schemas.common import StrictModel


class DiscoveryRunOut(StrictModel):
    """Response body for read endpoints on a single DiscoveryRun.

    Mirrors :func:`_serialize_discovery_run` byte-for-byte. ``id`` is
    the stringified Mongo ``_id``; timestamps are ISO 8601 strings;
    ``status`` is the stringified :class:`DiscoveryStatus` enum value.
    ``failed_pages_details`` is kept as ``list[dict[str, object]]``
    because the failure-reason shape varies per crawler error class.
    """

    id: Optional[str] = None
    website_id: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    status: str
    max_pages: int
    max_depth: int
    follow_external: bool
    respect_robots: bool
    pages_discovered: int
    pages_failed: int
    documents_found: int
    external_links_found: int
    failed_pages_details: list[dict[str, object]]
    pages_added: int
    pages_removed: int
    pages_unchanged: int
    triggered_by: Optional[str] = None
    job_id: Optional[str] = None
    error_message: Optional[str] = None
    duration_seconds: Optional[int] = None
    is_latest: bool


class DiscoveryRunListOut(StrictModel):
    """Response body for ``GET /api/v1/websites/<id>/discoveries``.

    Cursor-paginated; same ``{items, next_cursor}`` shape as the other
    v1 list endpoints.
    """

    items: list[DiscoveryRunOut]
    next_cursor: Optional[str] = None
