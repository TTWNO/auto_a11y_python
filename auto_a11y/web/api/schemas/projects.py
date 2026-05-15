"""Pydantic schemas for the /api/v1/projects/* endpoints (§5.1).

Wire-shape preservation
-----------------------
The existing handlers in ``auto_a11y/web/routes/api.py`` return ad-hoc dict
shapes that don't match the idealized ``ProjectOut`` / ``ListEnvelope[T]``
patterns described in the OpenAPI rollout design doc. To honor the
project rule "do NOT change wire shapes", these models mirror what each
handler currently returns:

- ``POST /projects`` returns ``{"id": ..., "message": ...}``  -> ``ProjectCreatedOut``
- ``PUT  /projects/<id>`` returns ``{"message": ...}``         -> ``MessageOut``
- ``DELETE /projects/<id>`` returns no body (204)              -> ``Empty`` (from common)
- ``GET  /projects`` returns ``{"projects": [...], "pagination": {page, limit, total}}``
                                                                  -> ``ProjectListOut``
- ``GET  /projects/<id>`` returns ``Project.to_dict()`` merged with a
  ``"statistics"`` field at the top level                       -> ``ProjectDictOut``

The two ``*Out`` shapes that carry the full project payload model the
top-level structure with ``dict[str, object]``-typed nested fields rather
than fully-typed nested Pydantic models. Modeling
``LivedExperienceTester`` / ``TestSupervisor`` / ``ProjectMember`` /
statistics with full type rigour would balloon the schema package and
duplicate the dataclasses in ``auto_a11y.models``. The §5.11 Members
work (Task 21) will introduce proper member schemas; until then the
member / tester / supervisor payloads round-trip through
``dict[str, object]``. ``additionalProperties: true`` on those fields is
correct: the existing handlers really do return arbitrarily-shaped dicts
to clients via ``Project.to_dict()``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import Field

from auto_a11y.web.api.schemas.common import StrictModel


class ProjectIn(StrictModel):
    """Request body for ``POST /api/v1/projects``.

    Mirrors the keys the existing handler reads from ``request.get_json()``:
    ``name`` (required), ``description`` (optional), ``config`` (optional).
    Unknown keys are rejected by ``StrictModel`` (``extra='forbid'``); the
    legacy handler silently dropped them, so this is a small input-side
    tightening that the v1 surface deliberately commits to.
    """

    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default="", max_length=2000)
    config: Optional[dict[str, object]] = None


class ProjectPatch(StrictModel):
    """Request body for ``PUT /api/v1/projects/<id>``.

    The existing handler treats every field as optional and applies only
    the fields present in the payload -- closer to PATCH semantics. The
    name reflects that; the route still uses ``PUT`` for wire-compat.
    """

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    status: Optional[str] = None
    config: Optional[dict[str, object]] = None


class ProjectCreatedOut(StrictModel):
    """Response body for ``POST /api/v1/projects`` (current wire shape).

    The legacy handler returns ``{"id": <new_id>, "message": "..."}`` --
    NOT the full project resource. Preserved here byte-for-byte.
    """

    id: str
    message: str


class MessageOut(StrictModel):
    """Generic single-message response (used by the PUT handler)."""

    message: str


class ProjectDictOut(StrictModel):
    """Response body for ``GET /api/v1/projects/<id>``.

    The legacy handler returns ``Project.to_dict()`` merged with a
    ``statistics`` top-level key. We model the top-level structure here
    so the OpenAPI spec lists the keys clients should expect; the deeply
    nested fields (testers, supervisors, members, config, statistics)
    use ``dict[str, object]`` because their shapes are tracked in the
    dataclasses in ``auto_a11y.models`` and will be properly typed when
    §5.11 Members lands.
    """

    # Identity & display
    name: str
    description: Optional[str] = None
    status: str
    project_type: str

    # Type-specific identifiers
    website_ids: list[str] = Field(default_factory=lambda: [])
    app_identifier: Optional[str] = None
    device_model: Optional[str] = None
    location: Optional[str] = None

    # Common to all types
    recording_ids: list[str] = Field(default_factory=lambda: [])

    # Nested collections — modeled as free-form dicts pending §5.11.
    lived_experience_testers: list[dict[str, object]] = Field(default_factory=lambda: [])
    test_supervisors: list[dict[str, object]] = Field(default_factory=lambda: [])
    members: list[dict[str, object]] = Field(default_factory=lambda: [])

    # Drupal integration
    drupal_audit_name: Optional[str] = None

    # Timestamps. Pydantic ``model_dump(mode='json')`` emits ISO 8601
    # strings; the legacy handler relied on Flask's default JSON provider
    # to do the same -- so this preserves the wire shape.
    created_at: datetime
    updated_at: datetime

    # Free-form configuration and tags.
    config: dict[str, object] = Field(default_factory=lambda: {})
    tags: list[str] = Field(default_factory=lambda: [])

    # Optional Mongo ``_id`` -- included by ``to_dict()`` when persisted.
    # ``to_dict()`` returns the raw ``ObjectId``; the handler stringifies
    # it before constructing this model so it can be serialised through
    # ``model_dump(mode='json')``. The wire key remains ``_id`` for
    # frontend compatibility (the field name is ``id_`` because ``_id``
    # is not a valid Python identifier prefix in pydantic field names).
    id_: Optional[str] = Field(default=None, alias="_id")

    # ``statistics`` is merged in by the GET handler. The shape is the
    # return type of ``Database.get_project_stats`` -- a flat numeric dict
    # documented in that method. Round-tripped as ``dict[str, object]``.
    statistics: dict[str, object]


class _PaginationOldShape(StrictModel):
    """Pagination block for ``GET /projects`` -- legacy shape.

    Differs from ``PaginationMeta`` in ``common.py``: keys are
    ``page``/``limit``/``total`` (no ``per_page``, no ``total_pages``).
    """

    page: int
    limit: int
    total: int


class ProjectListOut(StrictModel):
    """Response body for ``GET /api/v1/projects`` (current wire shape).

    Deviates from the generic ``ListEnvelope[T]`` in ``common.py``: the
    legacy handler uses ``"projects"`` (not ``"data"``) and a flat
    ``page/limit/total`` pagination block (not the four-key
    ``PaginationMeta``). The frontend reads this exact shape today.
    """

    projects: list[dict[str, object]]
    pagination: _PaginationOldShape
