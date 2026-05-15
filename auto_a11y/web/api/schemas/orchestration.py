"""Pydantic schemas for the test-orchestration endpoints (§5.x F1a follow-up).

Wire-shape preservation
-----------------------
This module covers the four top-level test-orchestration routes that
sit alongside (but distinct from) the per-resource ``/pages/<id>/test``,
``/websites/<id>/test``, ``/projects/<id>/test`` triggers in §5.4:

- **POST /test-runs** — queue a single-page run by body-supplied
  ``page_id``. Mirrors the per-page POST but takes the page id in the
  body so a queue worker / bookmark service that already has the id
  doesn't have to splice it into a URL.
- **POST /test-runs/batch** — queue several single-page runs in one
  request. The handler validates every page id up front so the
  operation is all-or-nothing; on success returns 202 with a per-page
  handle list in the same order as the input.
- **GET /testing/config** / **PUT /testing/config** — read/write the
  runtime testing config (parallel tests, viewport size, AI analysis
  toggle, etc.). PUT is partial: missing keys are left untouched.
  Unknown keys raise 400 so typos surface immediately. The PUT body
  schema is structurally the same as the GET response (every field
  optional) but kept distinct so the OpenAPI consumer doesn't conflate
  "read the current config" with "patch some keys".

Decisions worth flagging
------------------------
- **``TopLevelTestRunIn`` keeps ``enable_multi_state`` and
  ``website_user_id`` as ``Optional[...]``.** The handler treats a
  missing ``enable_multi_state`` as ``True`` (mirrors
  ``start_page_test_run``); modelling it as a default-True field on
  the schema would change the wire (a client that omits the key gets
  the same behaviour, but the OpenAPI consumer would think the field
  is always present after validation). The legacy wire byte-for-byte
  is "missing → server-side default", not "always-present after
  validation".
- **``TestRunBatchIn.page_ids`` is ``list[str]``** rather than
  ``conlist(min_length=1, ...)`` because the legacy handler raises a
  custom :class:`ValidationError` with field path ``page_ids`` and
  code ``too_short`` for the empty-list case. Re-implementing that
  via Pydantic's min-length constraint would produce a different
  error code on the wire. We keep the runtime check in the handler.
- **``TestRunBatchOut.runs`` items omit ``status``.** The legacy
  handler builds the per-handle dict without a ``status`` key (the
  outer envelope's ``status="queued"`` covers the whole batch).
  Adding it to each item would change the wire.
- **``TestingConfigOut`` and ``TestingConfigPatchIn`` are kept
  separate.** The GET response has every field set to a concrete
  value (``_read_testing_config_field`` substitutes typed defaults
  for missing config attributes). The PUT body is partial — any
  subset is allowed. Modelling both with the same schema would force
  a choice between "every field optional" (wrong for the response)
  and "every field required" (wrong for the request).
- **No ``Literal`` constraints on the bool/int testing-config
  fields.** The handler enforces ``isinstance(value, bool)`` and
  ``isinstance(value, int)`` (excluding ``bool``-as-int) by hand,
  raising a custom ``ValidationError`` with field-specific error
  codes. Letting Pydantic's coercion handle this would change the
  error envelope. We keep the runtime checks in the handler and use
  ``Optional[bool]`` / ``Optional[int]`` on the schema so the OpenAPI
  consumer sees the right types without behaviour drift.
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional

from pydantic import Field, RootModel

from auto_a11y.web.api.schemas.common import StrictModel


# ---------------------------------------------------------------------------
# POST /test-runs (top-level single-page)
# ---------------------------------------------------------------------------


class TopLevelTestRunIn(StrictModel):
    """Request body for ``POST /api/v1/test-runs``.

    Top-level form of the per-page ``POST /api/v1/pages/<id>/test-runs``
    that takes the page id in the body instead of the URL. ``page_id``
    is required; the other fields mirror :class:`PageTestRunIn` from
    ``test_runs.py``.

    ``enable_multi_state`` uses strict bool validation: Pydantic's lax
    default would coerce the string ``"yes"`` (and other truthy strings)
    to ``True``, but the test orchestration surface treats those as a
    typo and 400s them so the caller fixes the wire shape.
    """

    page_id: str
    enable_multi_state: Annotated[Optional[bool], Field(strict=True)] = None
    website_user_id: Optional[str] = None


class TopLevelTestRunOut(StrictModel):
    """Response body (202) for ``POST /api/v1/test-runs``.

    Mirrors the legacy ``jsonify`` payload byte-for-byte: same shape
    as :class:`PageTestRunStartedOut` from ``test_runs.py`` but
    duplicated rather than re-imported so the two endpoints can
    diverge independently (e.g. the top-level form could grow a
    ``submitted_by`` echo without forcing it onto the per-page route).
    """

    job_id: str
    page_id: str
    multi_state: bool
    status: Literal["queued"]


# ---------------------------------------------------------------------------
# POST /test-runs/batch
# ---------------------------------------------------------------------------


class TestRunBatchIn(StrictModel):
    """Request body for ``POST /api/v1/test-runs/batch``.

    Queues a single-page run for each of ``page_ids``; all entries
    must resolve to a real page before any run is queued (the legacy
    handler validates up front so the operation is all-or-nothing).

    ``page_ids`` is typed as ``list[str]`` without a min-length
    constraint — the handler raises a custom :class:`ValidationError`
    with field path ``page_ids`` and code ``too_short`` for the empty
    case, and Pydantic's min-length validator would emit a different
    error code on the wire.
    """

    page_ids: list[str]
    enable_multi_state: Optional[bool] = None
    website_user_id: Optional[str] = None


class TestRunBatchHandleOut(StrictModel):
    """One per-page handle inside :attr:`TestRunBatchOut.runs`.

    Mirrors the legacy handle dict the batch handler builds inline.
    Slimmer than :class:`TopLevelTestRunOut` because the outer
    envelope's ``status`` covers the whole batch.
    """

    job_id: str
    page_id: str
    multi_state: bool


class TestRunBatchOut(StrictModel):
    """Response body (202) for ``POST /api/v1/test-runs/batch``.

    Mirrors the legacy ``jsonify`` payload byte-for-byte. ``runs`` is
    in the same order as the input ``page_ids``.
    """

    status: Literal["queued"]
    pages_queued: int
    runs: list[TestRunBatchHandleOut]


# ---------------------------------------------------------------------------
# GET /testing/config  &  PUT /testing/config
# ---------------------------------------------------------------------------


class TestingConfigOut(StrictModel):
    """Response body for ``GET /api/v1/testing/config``.

    Mirrors :func:`_serialize_testing_config` byte-for-byte. Every
    field is concrete on the wire because the helper substitutes
    typed defaults (``0`` / ``False``) for unset config attributes.

    Field semantics:

    - ``parallel_tests`` — number of concurrent browser workers.
    - ``test_timeout`` — per-page browser timeout in milliseconds.
    - ``run_ai_analysis`` — global toggle for Claude visual analysis.
    - ``browser_headless`` — whether Playwright runs headless.
    - ``viewport_width`` / ``viewport_height`` — browser viewport
      dimensions in pixels.
    - ``pages_per_page`` / ``max_pages_per_page`` — admin-UI
      pagination defaults / upper bound.
    - ``show_error_codes`` — developer/debug toggle that surfaces
      internal error codes in the admin UI.
    """

    parallel_tests: int
    test_timeout: int
    run_ai_analysis: bool
    browser_headless: bool
    viewport_width: int
    viewport_height: int
    pages_per_page: int
    max_pages_per_page: int
    show_error_codes: bool


class TestingConfigPatchIn(RootModel[dict[str, object]]):
    """Request body for ``PUT /api/v1/testing/config``.

    Free-form ``dict[str, object]`` rather than a typed model so the
    handler can preserve the legacy 400 wire shape on unknown keys
    (``{field, code: "unknown_field", message: "unknown config key"}``
    in the ``errors`` array). Pydantic's ``extra='forbid'`` would
    emit a different error envelope (``extra_forbidden`` on a ``loc``
    path) and break in-flight admin frontends.

    The same RootModel pattern is used by
    :class:`auto_a11y.web.api.schemas.admin.GenericSectionPatchIn`
    for the admin-settings PATCH surface, which has the same
    "valid-keys list lives in a runtime registry" constraint. The
    OpenAPI surface for this endpoint is therefore a free-form
    object; consumers should consult :class:`TestingConfigOut` for
    the documented field list.

    PUT here is *partial*: any subset of the documented keys is
    accepted; missing keys are left untouched (the legacy POST
    handler was already partial-update; we preserve that semantics).
    Per-key type validation (``isinstance(value, bool)`` and
    ``isinstance(value, int)`` excluding ``bool``-as-int) happens in
    the handler so the error wire shape stays consistent with the
    legacy ``invalid_type`` field-error code.
    """
