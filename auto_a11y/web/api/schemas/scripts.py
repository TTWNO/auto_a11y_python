"""Pydantic schemas for the /api/v1/scripts endpoints (§5.8).

Wire-shape preservation
-----------------------
Setup scripts are split into PAGE-scoped and WEBSITE-scoped variants
that share a single REST surface. The collection-style routes live
under ``/pages/<id>/scripts`` and ``/websites/<id>/scripts``; once a
script exists, the per-resource routes are at ``/scripts/<id>`` and
``/scripts/<id>/test-runs``. TEST_RUN-scoped scripts are runtime-
internal and surface as 404 from every REST endpoint.

The schemas in this module mirror the legacy helpers in
``auto_a11y/web/routes/api.py`` (``_serialize_script``,
``_build_script_from_body``, ``_apply_patch_to_script``) byte-for-byte
so the refactor is a documentation pass, not a behavioural change.

Body shapes
~~~~~~~~~~~

- :class:`ScriptIn` — POST body for ``/pages/<id>/scripts`` and
  ``/websites/<id>/scripts``. The handler injects ``scope`` and
  ``scope_id`` from the URL; the body must not carry them. The
  ``trigger`` field is accepted as an arbitrary string and validated
  by the handler against :class:`ExecutionTrigger`, matching the
  legacy 400 shape.
- :class:`ScriptPut` — PUT body for ``/scripts/<id>``. Full replacement
  of editable fields; same wire shape as :class:`ScriptIn` but reused
  separately so the spec emits two distinct request bodies (POST and
  PUT have different ``operationId`` values).
- :class:`ScriptPatch` — PATCH body for ``/scripts/<id>``. Every
  field is ``Optional[...]`` so the handler can use
  ``model_fields_set`` to distinguish "absent" from "explicit ``null``".

Nested body shapes
~~~~~~~~~~~~~~~~~~

- :class:`ScriptStepIn` — single step entry accepted in request bodies;
  the legacy parser used ``index + 1`` for the step's ``step_number``,
  so the wire body's step entries don't carry an explicit number.
- :class:`ScriptValidationIn` — optional validation block. The legacy
  parser accepted ``null`` to clear validation; Pydantic mirrors that
  by typing the parent field as ``Optional[ScriptValidationIn]``.

Response shapes
~~~~~~~~~~~~~~~

- :class:`ScriptOut` — single-resource response mirroring
  ``_serialize_script`` byte-for-byte. ISO 8601 timestamps,
  stringified enum values, ``_id`` dropped in favor of the ``id``
  string property.
- :class:`ScriptListOut` — cursor-paginated list envelope with the
  legacy ``{items, next_cursor}`` shape shared by every v1 list endpoint.
- :class:`ScriptTestRunOut` — return shape for
  ``POST /scripts/<id>/test-runs``. Distinct from every other
  ``/test-runs`` endpoint on this surface: the legacy handler is
  synchronous, blocking the request thread until the browser executes
  the script, and clients rely on the inline result. The 200 status
  code (rather than the usual 202) signals "done synchronously".
- :class:`ExecutionStatsOut` — execution stats block in responses,
  always fully populated because :class:`ExecutionStats` is a dataclass
  with default values.
- :class:`ScriptStepOut` and :class:`ScriptValidationOut` — sub-blocks
  in :class:`ScriptOut`; mirror ``_serialize_script_step`` and
  ``_serialize_script_validation``.

Decisions worth flagging
------------------------
- ``trigger`` and ``action_type`` are typed as ``str`` rather than
  narrowed to a Literal. The legacy handlers run the enum constructor
  (``ExecutionTrigger(value)`` / ``ActionType(value)``) so an invalid
  value surfaces as a 400 with the field path the frontend expects.
  Tightening to a Literal would change the error code from
  ``invalid_value`` to Pydantic's generic ``literal_error`` and break
  frontend error handling that hasn't been migrated yet.
- ``scope`` is part of :class:`ScriptOut` (always present in responses)
  but not part of any request body — the URL determines scope on
  create, and scope cannot be changed by PUT/PATCH (the handler
  preserves the existing scope across replacement).
- The ``test-runs`` request body is currently a no-op — the legacy
  handler parses it for the validation 400 shape but ignores its
  contents (the script's persisted ``steps`` are the authoritative
  input). We accept an empty :class:`StrictModel` so future per-run
  overrides (e.g. ``environment_vars``) can be added without an API
  version bump.
"""
from __future__ import annotations

from typing import Optional

from auto_a11y.web.api.schemas.common import StrictModel


# ---------------------------------------------------------------------------
# Nested body shapes
# ---------------------------------------------------------------------------


class ScriptStepIn(StrictModel):
    """Single step entry in a request body.

    The legacy parser assigns ``step_number = index + 1`` based on the
    position in the ``steps`` array, so the wire body's entries don't
    carry an explicit number. ``action_type`` is accepted as an
    arbitrary string and validated by the handler against
    :class:`ActionType`; integer fields default to the legacy values
    (``timeout=5000``, ``wait_after=0``).
    """

    action_type: str
    description: str
    selector: Optional[str] = None
    value: Optional[str] = None
    timeout: Optional[int] = None
    wait_after: Optional[int] = None
    screenshot_after: Optional[bool] = None


class ScriptValidationIn(StrictModel):
    """Optional validation block in a request body.

    All fields are optional; the legacy parser used ``data.get(...)``
    for every key. ``failure_selectors`` defaults to an empty list
    server-side.
    """

    success_selector: Optional[str] = None
    success_text: Optional[str] = None
    failure_selectors: Optional[list[str]] = None


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class ScriptIn(StrictModel):
    """POST body for creating a setup script.

    Wire shape is the same for the page-scoped and website-scoped
    collection endpoints; ``scope`` and the matching id are injected
    by the handler from the URL path.

    ``name`` is required at the semantic level (the handler rejects
    empty/whitespace-only values) but typed as ``Optional[str]`` here
    because the legacy parser permitted ``request.json`` to omit it
    (it then surfaces a 400 with field path ``name``).
    """

    name: Optional[str] = None
    description: Optional[str] = None
    trigger: Optional[str] = None
    condition_selector: Optional[str] = None
    report_violation_if_condition_met: Optional[bool] = None
    violation_message: Optional[str] = None
    violation_code: Optional[str] = None
    test_before_execution: Optional[bool] = None
    test_after_execution: Optional[bool] = None
    expect_visible_after: Optional[list[str]] = None
    expect_hidden_after: Optional[list[str]] = None
    clear_cookies_before: Optional[bool] = None
    clear_local_storage_before: Optional[bool] = None
    wait_for_selector: Optional[bool] = None
    wait_timeout: Optional[int] = None
    enabled: Optional[bool] = None
    steps: Optional[list[ScriptStepIn]] = None
    validation: Optional[ScriptValidationIn] = None


class ScriptPut(StrictModel):
    """PUT body for full replacement of a setup script.

    Same wire shape as :class:`ScriptIn` but declared separately so
    the spec emits distinct request bodies for the POST and PUT
    operations. Scope and scope id are preserved from the existing
    resource — the body must not attempt to change them.
    """

    name: Optional[str] = None
    description: Optional[str] = None
    trigger: Optional[str] = None
    condition_selector: Optional[str] = None
    report_violation_if_condition_met: Optional[bool] = None
    violation_message: Optional[str] = None
    violation_code: Optional[str] = None
    test_before_execution: Optional[bool] = None
    test_after_execution: Optional[bool] = None
    expect_visible_after: Optional[list[str]] = None
    expect_hidden_after: Optional[list[str]] = None
    clear_cookies_before: Optional[bool] = None
    clear_local_storage_before: Optional[bool] = None
    wait_for_selector: Optional[bool] = None
    wait_timeout: Optional[int] = None
    enabled: Optional[bool] = None
    steps: Optional[list[ScriptStepIn]] = None
    validation: Optional[ScriptValidationIn] = None


class ScriptPatch(StrictModel):
    """PATCH body for partial setup-script updates.

    Every field is ``Optional[...]``; the handler uses
    ``model_fields_set`` to apply only fields the client sent. This is
    how the legacy ``_apply_patch_to_script`` distinguished "absent"
    from "explicit ``null``".
    """

    name: Optional[str] = None
    description: Optional[str] = None
    trigger: Optional[str] = None
    condition_selector: Optional[str] = None
    report_violation_if_condition_met: Optional[bool] = None
    violation_message: Optional[str] = None
    violation_code: Optional[str] = None
    test_before_execution: Optional[bool] = None
    test_after_execution: Optional[bool] = None
    expect_visible_after: Optional[list[str]] = None
    expect_hidden_after: Optional[list[str]] = None
    clear_cookies_before: Optional[bool] = None
    clear_local_storage_before: Optional[bool] = None
    wait_for_selector: Optional[bool] = None
    wait_timeout: Optional[int] = None
    enabled: Optional[bool] = None
    steps: Optional[list[ScriptStepIn]] = None
    validation: Optional[ScriptValidationIn] = None


class ScriptTestRunIn(StrictModel):
    """Request body for ``POST /scripts/<id>/test-runs``.

    Empty in the current surface — the legacy handler parses the body
    for the validation 400 shape but ignores its contents (the script's
    persisted ``steps`` are the authoritative input). Accepting an
    empty :class:`StrictModel` keeps the door open for per-run
    overrides (e.g. ``environment_vars``) without an API version bump,
    and ``extra='forbid'`` still rejects stray fields a misbehaving
    client might send.
    """


# ---------------------------------------------------------------------------
# Response bodies
# ---------------------------------------------------------------------------


class ScriptStepOut(StrictModel):
    """Single step entry in a response body.

    Mirrors ``_serialize_script_step`` byte-for-byte. ``action_type``
    is the enum's string value (e.g. ``"click"``, ``"type"``).
    """

    step_number: int
    action_type: str
    description: str
    selector: Optional[str] = None
    value: Optional[str] = None
    timeout: int
    wait_after: int
    screenshot_after: bool


class ScriptValidationOut(StrictModel):
    """Optional validation block in a response body.

    Mirrors ``_serialize_script_validation``. The parent field on
    :class:`ScriptOut` is ``Optional[ScriptValidationOut]`` because the
    legacy serializer emits ``None`` when no validation is configured.
    """

    success_selector: Optional[str] = None
    success_text: Optional[str] = None
    failure_selectors: list[str]


class ExecutionStatsOut(StrictModel):
    """Execution stats block in a response body.

    Always fully populated because :class:`ExecutionStats` is a
    dataclass with default values. ``last_executed`` is ``None`` for
    scripts that have never run; otherwise it's an ISO 8601 timestamp.
    """

    last_executed: Optional[str] = None
    success_count: int
    failure_count: int
    average_duration_ms: int


class ScriptOut(StrictModel):
    """Response body for single-script GET / POST / PUT / PATCH.

    Mirrors ``_serialize_script`` byte-for-byte. ``id`` is the
    stringified Mongo ``_id`` (``None`` only on transient unsaved
    scripts, which never appear over the wire). ``scope`` and
    ``trigger`` are stringified enum values; timestamps are ISO 8601.

    TEST_RUN-scoped scripts are runtime-internal and never appear in
    this shape over the wire — every REST endpoint surfaces them as
    404 instead.
    """

    id: Optional[str] = None
    name: str
    description: str
    scope: str
    website_id: Optional[str] = None
    page_id: Optional[str] = None
    trigger: str
    condition_selector: Optional[str] = None
    report_violation_if_condition_met: bool
    violation_message: Optional[str] = None
    violation_code: Optional[str] = None
    test_before_execution: bool
    test_after_execution: bool
    expect_visible_after: list[str]
    expect_hidden_after: list[str]
    clear_cookies_before: bool
    clear_local_storage_before: bool
    wait_for_selector: bool
    wait_timeout: int
    enabled: bool
    steps: list[ScriptStepOut]
    validation: Optional[ScriptValidationOut] = None
    created_by: Optional[str] = None
    created_date: Optional[str] = None
    last_modified: Optional[str] = None
    execution_stats: ExecutionStatsOut


class ScriptListOut(StrictModel):
    """Response body for the script-list endpoints.

    Cursor-paginated; same ``{items, next_cursor}`` shape as the other
    v1 list endpoints. Used by both
    ``GET /api/v1/pages/<id>/scripts`` and
    ``GET /api/v1/websites/<id>/scripts``.
    """

    items: list[ScriptOut]
    next_cursor: Optional[str] = None


class ScriptTestRunOut(StrictModel):
    """Response body for ``POST /api/v1/scripts/<id>/test-runs``.

    Returned with status 200 (Synchronous done) rather than the usual
    202 because the legacy handler blocks on the browser execution.
    ``target_url`` is resolved by the handler from scope:

    - PAGE-scoped scripts: the linked page's URL
    - WEBSITE-scoped scripts: the website's root URL

    ``error`` is non-null only when the script raised during
    execution; in that case ``success`` is false and ``steps_executed``
    is zero. The handler returns a 500 in that path but with the same
    JSON envelope, so clients can read the failure inline without
    polling logs.
    """

    script_id: str
    success: bool
    duration_ms: int
    steps_executed: int
    error: Optional[str] = None
    target_url: str
