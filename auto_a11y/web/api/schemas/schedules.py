"""Pydantic schemas for the /api/v1/scheduled-tests endpoints (§5.7).

Wire-shape preservation
-----------------------
The scheduled-tests surface predates ``@document`` by some margin and was
implemented with hand-rolled ``dict[str, Any]`` body parsing in
``auto_a11y/web/routes/api.py`` (``_build_schedule_from_body``,
``_apply_patch_to_schedule``, ``_serialize_schedule``). The schemas in
this module mirror those helpers' wire shapes byte-for-byte so the
refactor is a documentation pass, not a behavioural change.

Body shapes
~~~~~~~~~~~

- :class:`ScheduleIn` — POST/PUT body for creating or fully replacing a
  schedule. The two operations accept the same wire shape (the PUT
  handler preserves server-managed bookkeeping fields by carrying them
  over from the prior version, not by accepting them in the body).
- :class:`SchedulePatch` — PATCH body for partial updates. Every field
  is ``Optional[...]`` so the handler can use
  ``model_fields_set`` to distinguish "absent" from "explicit ``null``".

Nested config shapes
~~~~~~~~~~~~~~~~~~~~

- :class:`PresetConfigIn` — preset-config block accepted in request
  bodies; mirrors the legacy parser's defaults (``time="02:00"``,
  ``timezone="America/Toronto"``).
- :class:`PresetConfigOut` — preset-config block in responses; always
  fully populated because :class:`PresetConfig` is a dataclass with
  default values.
- :class:`ScheduleTestConfigIn` — test-config block accepted in request
  bodies. ``ai_pages_mode`` is accepted as an arbitrary string and the
  handler runs ``AITestMode(value)`` itself to surface a 400 with a
  useful ``ai_pages_mode`` field path; a Literal would emit the same
  400 but with Pydantic's generic ``literal_error`` code, which the
  existing frontend doesn't recognise.
- :class:`ScheduleTestConfigOut` — test-config block in responses;
  ``ai_pages_mode`` is always a stringified enum value.

Response shapes
~~~~~~~~~~~~~~~

- :class:`ScheduleOut` — single-resource response mirroring
  ``_serialize_schedule`` byte-for-byte. ISO 8601 timestamps,
  stringified enum values, no Mongo ``_id`` in the payload (only the
  ``id`` string property).
- :class:`ScheduleListOut` — cursor-paginated list envelope with the
  legacy ``{items, next_cursor}`` shape shared by every v1 list endpoint.
- :class:`ScheduleRunOut` — return shape for ``POST /scheduled-tests/<id>/runs``;
  carries the ``job_id`` returned by the APScheduler service plus the
  echoed ``schedule_id`` so clients don't have to track that themselves.
- :class:`SchedulePreviewOut` — return shape for
  ``GET /scheduled-tests/<id>/preview``; ``next_runs`` is a list of ISO
  8601 datetime strings (the handler renders ``datetime.isoformat()``
  on each entry produced by the scheduler).

Decisions worth flagging
------------------------
- ``schedule_type`` and ``ai_pages_mode`` are typed as ``str`` rather
  than narrowed to a Literal. The legacy handlers run the enum
  constructor (``ScheduleType(value)`` / ``AITestMode(value)``) so an
  invalid value surfaces as a 400 with the field path the frontend
  expects. Tightening to a Literal would change the error code from
  ``invalid_value`` to ``literal_error`` and break frontend error
  handling that hasn't been migrated yet.
- ``cron_expression`` stays an opaque ``Optional[str]``. The handler
  defers validation to the APScheduler service when a CRON-typed
  schedule registers; we don't try to grammar-check cron in Pydantic.
- Numeric preset fields (``day_of_week``, ``day_of_month``) are typed
  as ``int`` because the legacy parser called ``int(...)`` on whatever
  the body sent. Pydantic v2 accepts numeric strings here by default,
  matching the legacy permissive coercion.
"""
from __future__ import annotations

from typing import Optional

from auto_a11y.web.api.schemas.common import StrictModel


# ---------------------------------------------------------------------------
# Nested config shapes
# ---------------------------------------------------------------------------


class PresetConfigIn(StrictModel):
    """Request-body preset config for daily/weekly/monthly schedules.

    All fields are optional in the wire form because the legacy parser
    supplied defaults for missing keys. ``time`` is an ``HH:MM`` string
    in 24-hour format; ``day_of_week`` is 0=Monday..6=Sunday;
    ``day_of_month`` is 1..31; ``timezone`` is an IANA tz name.
    """

    time: Optional[str] = None
    day_of_week: Optional[int] = None
    day_of_month: Optional[int] = None
    timezone: Optional[str] = None


class PresetConfigOut(StrictModel):
    """Response-body preset config.

    Always fully populated because :class:`PresetConfig` is a dataclass
    with default values for every field.
    """

    time: str
    day_of_week: int
    day_of_month: int
    timezone: str


class ScheduleTestConfigIn(StrictModel):
    """Request-body test-config block.

    ``ai_pages_mode`` is accepted as an arbitrary string; the handler
    runs ``AITestMode(value)`` itself so invalid values surface as 400
    with an ``ai_pages_mode`` field path.
    """

    run_ai_tests: Optional[bool] = None
    run_javascript_tests: Optional[bool] = None
    run_python_tests: Optional[bool] = None
    enabled_touchpoints: Optional[list[str]] = None
    ai_pages_mode: Optional[str] = None
    ai_page_ids: Optional[list[str]] = None
    take_screenshots: Optional[bool] = None


class ScheduleTestConfigOut(StrictModel):
    """Response-body test-config block.

    ``ai_pages_mode`` is always the stringified enum value
    (e.g. ``"all"``, ``"selected"``, ``"none"``).
    """

    run_ai_tests: bool
    run_javascript_tests: bool
    run_python_tests: bool
    enabled_touchpoints: list[str]
    ai_pages_mode: str
    ai_page_ids: list[str]
    take_screenshots: bool


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class ScheduleIn(StrictModel):
    """POST/PUT body for creating or fully replacing a schedule.

    Mirrors the legacy ``_build_schedule_from_body`` parser shape.
    ``name`` is required at the semantic level (the handler rejects
    empty/whitespace-only values) but typed as ``Optional[str]`` here
    because the legacy parser permitted ``request.json`` to omit it
    (it then surfaces a 400 with field path ``name``). Centralising
    presence checks in the handler keeps error codes consistent across
    POST and PUT.
    """

    name: Optional[str] = None
    description: Optional[str] = None
    schedule_type: Optional[str] = None
    scheduled_datetime: Optional[str] = None
    cron_expression: Optional[str] = None
    preset_config: Optional[PresetConfigIn] = None
    test_config: Optional[ScheduleTestConfigIn] = None
    project_user_ids: Optional[list[str]] = None
    enabled: Optional[bool] = None


class SchedulePatch(StrictModel):
    """PATCH body for partial schedule updates.

    Every field is ``Optional[...]``; the handler uses
    ``model_fields_set`` to apply only fields the client sent. This is
    how the legacy ``_apply_patch_to_schedule`` distinguished "absent"
    from "explicit ``null``".
    """

    name: Optional[str] = None
    description: Optional[str] = None
    schedule_type: Optional[str] = None
    scheduled_datetime: Optional[str] = None
    cron_expression: Optional[str] = None
    preset_config: Optional[PresetConfigIn] = None
    test_config: Optional[ScheduleTestConfigIn] = None
    project_user_ids: Optional[list[str]] = None
    enabled: Optional[bool] = None


# ---------------------------------------------------------------------------
# Response bodies
# ---------------------------------------------------------------------------


class ScheduleOut(StrictModel):
    """Response body for single-schedule GET / POST / PUT / PATCH.

    Mirrors ``_serialize_schedule`` byte-for-byte. ``id`` is the
    stringified Mongo ``_id`` (``None`` only on transient unsaved
    schedules, which never appear over the wire). ``schedule_type``
    and ``last_run_status`` are stringified enum values; timestamps
    are ISO 8601.
    """

    id: Optional[str] = None
    website_id: str
    name: str
    description: Optional[str] = None
    schedule_type: str
    scheduled_datetime: Optional[str] = None
    cron_expression: Optional[str] = None
    preset_config: PresetConfigOut
    test_config: ScheduleTestConfigOut
    project_user_ids: list[str]
    enabled: bool
    created_by: Optional[str] = None
    last_run_at: Optional[str] = None
    last_run_job_id: Optional[str] = None
    last_run_status: Optional[str] = None
    next_run_at: Optional[str] = None
    run_count: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ScheduleListOut(StrictModel):
    """Response body for ``GET /api/v1/websites/<id>/scheduled-tests``.

    Cursor-paginated; same ``{items, next_cursor}`` shape as the other
    v1 list endpoints.
    """

    items: list[ScheduleOut]
    next_cursor: Optional[str] = None


class ScheduleRunOut(StrictModel):
    """Response body for ``POST /api/v1/scheduled-tests/<id>/runs``.

    Returned with status 202 (Accepted). ``job_id`` is the APScheduler
    job identifier; ``schedule_id`` is echoed so clients don't have to
    track the request-side schedule id separately.
    """

    job_id: str
    schedule_id: str


class SchedulePreviewOut(StrictModel):
    """Response body for ``GET /api/v1/scheduled-tests/<id>/preview``.

    ``next_runs`` is a list of ISO 8601 datetime strings produced by
    ``datetime.isoformat()``. The handler clamps ``?count`` to
    ``[1, 50]``; the typical default is 5.
    """

    schedule_id: str
    next_runs: list[str]
