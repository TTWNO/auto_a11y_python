"""Pydantic schemas for the /api/v1/websites/* endpoints (§5.2).

Wire-shape preservation
-----------------------
The existing 6 CRUD handlers in ``auto_a11y/web/routes/api.py`` return the
shape produced by ``_serialize_website()`` and accept input bodies
processed by ``_build_website_from_body()`` / ``_apply_patch_to_website()``.
This module mirrors those shapes exactly:

- ``GET /projects/<id>/websites`` returns ``{"items": [...], "next_cursor": ...}``
  (cursor pagination, NOT the legacy page/limit/total block used by projects).
- ``POST /projects/<id>/websites`` returns the **full** ``WebsiteOut`` resource
  with status 201 plus a ``Location`` header (NOT ``{id, message}`` like
  the projects POST handler).
- ``GET /websites/<id>`` returns ``WebsiteOut``.
- ``PUT /websites/<id>``, ``PATCH /websites/<id>`` return ``WebsiteOut``.
- ``DELETE /websites/<id>`` returns 204 with no body.

Deliberate v1 deviations
------------------------
- ``WebsiteIn`` / ``WebsitePut`` / ``WebsitePatch`` reject unknown JSON keys
  (``StrictModel`` uses ``extra='forbid'``). The legacy handlers silently
  dropped unknown keys; tightening this is the v1 surface's commitment.
- Pydantic emits ISO 8601 timestamps via ``model_dump(mode='json')``; the
  legacy handler already emitted ISO 8601 via ``datetime.isoformat()`` for
  websites (unlike projects which used Flask's RFC-822), so the wire is
  byte-identical here.
- ``ObjectId`` from ``Website._id`` is stringified by ``Website.id`` (the
  property), so no extra coercion is needed for the website resource
  itself.
"""
from __future__ import annotations

from typing import Optional

from pydantic import Field

from auto_a11y.web.api.schemas.common import StrictModel


# ---------------------------------------------------------------------------
# Nested config model (input + output share the same shape).
# ---------------------------------------------------------------------------


class ScrapingConfigModel(StrictModel):
    """Schema for ``ScrapingConfig`` — used in both request and response bodies.

    Mirrors the dataclass in ``auto_a11y.models.website.ScrapingConfig``.
    All fields are optional on input (defaults fill in missing values via
    ``ScrapingConfig()`` in the handler); on output every field is always
    populated because ``ScrapingConfig.to_dict()`` emits all of them.
    """

    max_pages: Optional[int] = Field(default=None, ge=0)
    max_depth: Optional[int] = Field(default=None, ge=0)
    follow_external: Optional[bool] = None
    include_subdomains: Optional[bool] = None
    respect_robots: Optional[bool] = None
    request_delay: Optional[float] = Field(default=None, ge=0)
    allowed_paths: Optional[list[str]] = None
    excluded_paths: Optional[list[str]] = None
    auto_fetch_pdfs: Optional[bool] = None


# ---------------------------------------------------------------------------
# Request models.
# ---------------------------------------------------------------------------


class WebsiteIn(StrictModel):
    """Request body for ``POST /api/v1/projects/<id>/websites``.

    ``url`` is required; ``name`` and ``scraping_config`` are optional.
    URL is validated for ``http(s)://`` prefix in the handler (Pydantic's
    built-in ``HttpUrl`` is not used here to keep the validation rule
    byte-identical to the legacy ``_validate_url`` helper).
    """

    url: str = Field(min_length=1, max_length=2048)
    name: Optional[str] = Field(default=None, max_length=200)
    scraping_config: Optional[ScrapingConfigModel] = None


class WebsitePut(StrictModel):
    """Request body for ``PUT /api/v1/websites/<id>``.

    PUT is a full replace of editable fields. Server-managed fields
    (``project_id``, ``created_at``, ``last_scraped``, ``last_tested``,
    ``page_count``, ``members``, ``discovery_history``) are preserved
    from the existing record and cannot be set via this endpoint.
    """

    url: str = Field(min_length=1, max_length=2048)
    name: Optional[str] = Field(default=None, max_length=200)
    scraping_config: Optional[ScrapingConfigModel] = None


class WebsitePatch(StrictModel):
    """Request body for ``PATCH /api/v1/websites/<id>``.

    Every field is optional; only fields present in the body are applied.
    ``url`` validation runs only when ``url`` is provided.
    """

    url: Optional[str] = Field(default=None, min_length=1, max_length=2048)
    name: Optional[str] = Field(default=None, max_length=200)
    scraping_config: Optional[ScrapingConfigModel] = None


# ---------------------------------------------------------------------------
# Response models.
# ---------------------------------------------------------------------------


class WebsiteOut(StrictModel):
    """Response body for single-website GET / POST / PUT / PATCH.

    Mirrors ``_serialize_website()`` byte-for-byte. ``id`` is the
    stringified Mongo ``_id``; ``last_scraped`` and ``last_tested`` may be
    null. ``scraping_config`` is the nested dict produced by
    ``ScrapingConfig.to_dict()``.

    Timestamps are emitted as ISO 8601 strings (Pydantic default), which
    matches the legacy handler's behaviour for this resource.
    """

    id: Optional[str] = None
    project_id: str
    url: str
    name: Optional[str] = None
    display_name: str
    page_count: int
    scraping_config: ScrapingConfigModel
    created_at: Optional[str] = None
    last_scraped: Optional[str] = None
    last_tested: Optional[str] = None


class WebsiteListOut(StrictModel):
    """Response body for ``GET /api/v1/projects/<id>/websites``.

    Uses cursor pagination with the legacy ``items`` / ``next_cursor``
    shape — NOT the generic ``ListEnvelope[T]`` (which uses ``data`` and
    a page/limit pagination block). This matches what the existing
    handler returns and what the frontend reads today.
    """

    items: list[WebsiteOut]
    next_cursor: Optional[str] = None


# ---------------------------------------------------------------------------
# Generic 1-key envelopes (re-defined here to avoid cross-module imports).
# ---------------------------------------------------------------------------


class MessageOut(StrictModel):
    """Generic single-message response.

    Currently unused by the 6 CRUD handlers (POST returns the full
    resource, PUT/PATCH return the full resource, DELETE returns 204).
    Kept here for symmetry with ``schemas/projects.py`` and in
    anticipation of any future endpoints in this module that return
    only ``{message}``.
    """

    message: str
