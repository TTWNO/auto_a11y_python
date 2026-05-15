"""Pydantic schemas for the /api/v1 test-run and test-result endpoints (§5.4).

Wire-shape preservation
-----------------------
This module covers the four POST endpoints that queue background work
(``page-test``, ``website-discovery``, ``website-test``, ``project-test``)
plus the read endpoints that surface stored ``TestResult`` documents.

Decisions worth flagging up front:

- The four POSTs return 202 with a per-handler envelope (``{job_id,
  ..., status, message}``). The shapes diverge by route — the page and
  website-discovery handlers carry different metadata than the website-
  test and project-test handlers — so we use four distinct schemas
  rather than one with optional fields. This matches the legacy wire
  byte-for-byte (the legacy ``jsonify`` calls produced the same keys).
- The single-result and list endpoints proxy
  :meth:`TestResult.to_dict`. Modelling violation / AI-finding /
  ``page_state`` / ``passes`` / ``metadata`` shapes here would duplicate
  a lot of typed surface that §5.x downstream work will revisit; until
  then we keep them as ``dict[str, object]`` with the ``StrictModel``
  ``extra='forbid'`` policy only applied at the envelope level.
- ``compare_test_results`` returns a deeply-nested diff plus a free-form
  summary. We model the outer envelope (the ``success`` and
  ``comparison`` keys) precisely, but the nested ``comparison`` object
  is ``dict[str, object]``. The legacy handler builds it inline from
  three different shapes; refactoring into typed schemas without
  changing the wire would inflate this module significantly.
- ``test-states`` returns a JSON object **keyed by stringified integers**
  (``"0"``, ``"1"``, ...). Pydantic's ``dict[int, X]`` would round-trip
  through JSON correctly, but the legacy handler keeps the dict whole
  rather than emitting a list, so we model the ``states`` field as
  ``dict[str, TestStateEntryOut]`` to match what ``jsonify`` produced
  with integer keys (Flask's encoder coerces int keys to strings).
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import RootModel

from auto_a11y.web.api.schemas.common import StrictModel


# ---------------------------------------------------------------------------
# POST /pages/<id>/test  &  /pages/<id>/test-runs
# ---------------------------------------------------------------------------


class PageTestRunIn(StrictModel):
    """Request body for queueing a single-page test run.

    Both ``POST /api/v1/pages/<id>/test`` (legacy alias) and the
    canonical ``POST /api/v1/pages/<id>/test-runs`` share this body.

    All fields are optional; ``enable_multi_state`` defaults to true on
    the server (mirroring :func:`start_page_test_run`).

    ``take_screenshot`` and ``run_ai`` are per-run overrides for the
    test runner's screenshot capture and Claude-AI visual analysis.
    When ``None``, the runner's defaults apply (``take_screenshot=True``,
    ``run_ai_analysis=False``).
    """

    enable_multi_state: Optional[bool] = None
    website_user_id: Optional[str] = None
    take_screenshot: Optional[bool] = None
    run_ai: Optional[bool] = None


class PageTestRunStartedOut(StrictModel):
    """Response body (202) for queueing a single-page test run.

    Mirrors the legacy handler's ``jsonify`` payload byte-for-byte.
    ``status`` is always ``"queued"``; ``message`` is the human-readable
    fallback the legacy clients display.
    """

    job_id: str
    page_id: str
    multi_state: bool
    status: Literal["queued"]
    message: str


# ---------------------------------------------------------------------------
# POST /websites/<id>/discover  &  /websites/<id>/discoveries
# ---------------------------------------------------------------------------


class WebsiteDiscoveryIn(StrictModel):
    """Request body for queueing a website discovery crawl.

    All fields are optional. ``max_pages`` is coerced to a positive int
    by the handler (non-positive / non-int values silently degrade to
    None). Both ``project_user_ids`` and ``website_user_ids`` are
    accepted for backwards-compat; the handler reads
    ``project_user_ids`` first, falling back to ``website_user_ids``.

    Each ``*_user_ids`` field accepts either a single string or a list
    of strings — the legacy handler tolerates both, and we mirror that
    here via ``str | list[str]``.
    """

    max_pages: Optional[int] = None
    project_user_ids: Optional[str | list[str]] = None
    website_user_ids: Optional[str | list[str]] = None


class WebsiteDiscoveryStartedOut(StrictModel):
    """Response body (202) for queueing a website discovery crawl.

    Mirrors the legacy ``jsonify`` payload. ``max_pages`` is the
    coerced crawl cap (``None`` when unbounded).
    """

    job_id: str
    website_id: str
    max_pages: Optional[int] = None
    user_count: int
    status: Literal["started"]
    message: str


# ---------------------------------------------------------------------------
# POST /websites/<id>/test  &  /websites/<id>/test-runs
# ---------------------------------------------------------------------------


class WebsiteTestRunIn(StrictModel):
    """Request body for queueing a website-wide batch test.

    All fields optional. ``project_user_ids`` and ``website_user_ids``
    accept a single string or list of strings — the handler reads
    ``project_user_ids`` first, falling back to ``website_user_ids``.

    ``untested_only`` filters the queue to pages still in DISCOVERED.

    ``take_screenshot`` and ``run_ai`` are per-run overrides applied
    uniformly across every page queued by this batch. ``None`` keeps
    the runner's defaults.
    """

    max_pages: Optional[int] = None
    untested_only: Optional[bool] = None
    project_user_ids: Optional[str | list[str]] = None
    website_user_ids: Optional[str | list[str]] = None
    take_screenshot: Optional[bool] = None
    run_ai: Optional[bool] = None


class WebsiteTestRunStartedOut(StrictModel):
    """Response body (202) for queueing a website-wide test run.

    Mirrors the legacy ``jsonify`` payload byte-for-byte.
    """

    job_id: str
    website_id: str
    pages_queued: int
    user_count: int
    total_tests: int
    status: Literal["queued"]
    message: str


# ---------------------------------------------------------------------------
# POST /projects/<id>/test-runs
# ---------------------------------------------------------------------------


class ProjectTestRunIn(StrictModel):
    """Request body for queueing a project-wide batch test.

    Each option is applied uniformly to every website in the project.
    Same field semantics as :class:`WebsiteTestRunIn`.
    """

    max_pages: Optional[int] = None
    untested_only: Optional[bool] = None
    project_user_ids: Optional[str | list[str]] = None
    website_user_ids: Optional[str | list[str]] = None
    take_screenshot: Optional[bool] = None
    run_ai: Optional[bool] = None


class ProjectTestRunWebsiteHandleOut(StrictModel):
    """One element of :attr:`ProjectTestRunStartedOut.test_runs`.

    A summary of one website's queued test run; matches the per-website
    summary the legacy handler builds inline.
    """

    job_id: str
    website_id: str
    pages_queued: int
    user_count: int
    total_tests: int


class ProjectTestRunStartedOut(StrictModel):
    """Response body (202) for queueing a project-wide test run.

    Mirrors the legacy ``jsonify`` payload. Websites with no eligible
    pages are silently skipped — they do not appear in ``test_runs``,
    and ``websites_queued`` reflects only the websites that actually
    queued work.
    """

    project_id: str
    websites_queued: int
    pages_queued: int
    total_tests: int
    test_runs: list[ProjectTestRunWebsiteHandleOut]
    status: Literal["queued"]


# ---------------------------------------------------------------------------
# Test results — individual + list.
# ---------------------------------------------------------------------------


# Pydantic v2 has no first-class "free-form dict that round-trips
# unchanged" alias; the legacy handler proxies ``TestResult.to_dict``
# directly, which carries nested ``Violation``/``AIFinding`` dataclass
# dicts plus free-form ``passes``, ``js_test_results``,
# ``ai_analysis_results``, ``metadata``, ``page_state``. Modelling each
# of those here would duplicate a lot of surface area that the §5.x
# downstream work will revisit. We therefore use a :class:`RootModel`
# whose root is ``dict[str, object]`` — the OpenAPI schema for it is
# emitted as a free-form ``object`` and the wire shape is the raw dict
# without any envelope wrapper.
class TestResultOut(RootModel[dict[str, object]]):
    """Response body for ``GET /api/v1/test-results/<id>``.

    The wire is the full :meth:`TestResult.to_dict` payload as-is — no
    envelope. The :class:`RootModel` declaration lets the OpenAPI
    builder emit a free-form ``object`` schema without forcing a
    wrapper key on the wire.
    """


class TestResultListOut(StrictModel):
    """Response body for ``GET /api/v1/pages/<id>/test-results``.

    Mirrors the legacy handler's ``{results: [TestResult.to_dict, ...]}``
    envelope. The list items use the same compromise as
    :class:`TestResultOut`.
    """

    results: list[dict[str, object]]


# ---------------------------------------------------------------------------
# Latest test-run for a page — read + cancel.
# ---------------------------------------------------------------------------


class PageTestRunLatestOut(StrictModel):
    """Response body for ``GET /api/v1/pages/<id>/test-runs/latest``.

    Surfaces *just* the run-level state — separate from the historical
    test-result list — so UIs can drive a "test in flight" indicator
    without paginating the whole history.

    ``status`` is a free-form string (the :class:`PageStatus` enum
    values: ``discovered``, ``queued``, ``testing``, ``tested``,
    ``error``, ``skipped``, ...). We don't use a ``Literal`` here
    because new enum members would silently fail validation; the legacy
    handler is also string-typed.

    ``active_task_id`` is the task-runner id (pattern
    ``test_page_<page_id>_<timestamp>``) when the worker is still
    running, otherwise ``null``.
    """

    page_id: str
    status: str
    last_tested: Optional[str] = None
    last_test_result_id: Optional[str] = None
    active_task_id: Optional[str] = None


class PageTestRunCancelOut(StrictModel):
    """Response body (202) for cancelling a page's in-flight test run.

    Cancellation is asynchronous; the worker polls the task's
    cancellation flag at its next checkpoint. The page status flips
    to ``DISCOVERED`` (never tested) or ``TESTED`` (has prior results)
    immediately so the UI doesn't lock up on a phantom run.

    ``task_id`` may be ``null`` when the page was QUEUED but the worker
    had not yet spawned the task (a recovered-state path).
    """

    page_id: str
    task_id: Optional[str] = None
    cancellation_requested: bool
    status: str


# ---------------------------------------------------------------------------
# Multi-state testing — states + sessions.
# ---------------------------------------------------------------------------


class TestStateEntryOut(StrictModel):
    """A single state's summary inside a multi-state results listing.

    Mirrors the per-state dict built by ``get_test_result_states`` and
    ``get_page_test_states``. ``page_state`` is the captured page-state
    metadata (button toggle name, script name, etc.) — free-form
    because the producer (the multi-state runner) emits a record whose
    keys depend on the trigger that produced the state.
    """

    result_id: Optional[str] = None
    state_sequence: int
    page_state: Optional[dict[str, object]] = None
    session_id: Optional[str] = None
    test_date: Optional[str] = None
    violation_count: int
    warning_count: int
    info_count: int
    pass_count: int
    duration_ms: int


class TestResultStatesOut(StrictModel):
    """Response body for ``GET /api/v1/test-results/<id>/states``.

    Mirrors the legacy ``success``-envelope shape (kept for wire-compat
    with in-flight admin frontends). ``states`` is sorted by
    ``state_sequence`` ascending.
    """

    success: Literal[True]
    result_id: str
    total_states: int
    states: list[TestStateEntryOut]


class PageTestStatesOut(StrictModel):
    """Response body for ``GET /api/v1/pages/<id>/test-states``.

    Mirrors the legacy shape. ``states`` is keyed by the integer state
    sequence, stringified by Flask's JSON encoder (e.g. ``"0"``,
    ``"1"``, ...). We model it as ``dict[str, TestStateEntryOut]`` to
    match the encoded wire shape exactly.
    """

    success: Literal[True]
    page_id: str
    page_url: str
    total_states: int
    states: dict[str, TestStateEntryOut]


class PageTestSessionStateOut(StrictModel):
    """A single state's summary inside a session.

    Slimmer than :class:`TestStateEntryOut` — the sessions endpoint
    only exposes the fields needed to drive a session-level rollup.
    """

    result_id: Optional[str] = None
    state_sequence: int
    state_description: Optional[str] = None
    violation_count: int
    warning_count: int


class PageTestSessionOut(StrictModel):
    """One session entry inside the sessions listing."""

    session_id: str
    test_date: Optional[str] = None
    states: list[PageTestSessionStateOut]
    total_violations: int
    total_warnings: int
    state_count: int


class PageTestSessionsOut(StrictModel):
    """Response body for ``GET /api/v1/pages/<id>/test-sessions``.

    Mirrors the legacy ``success``-envelope shape; sessions are sorted
    by ``test_date`` descending.
    """

    success: Literal[True]
    page_id: str
    page_url: str
    total_sessions: int
    sessions: list[PageTestSessionOut]


# ---------------------------------------------------------------------------
# Compare two test results.
# ---------------------------------------------------------------------------


class TestResultCompareIn(StrictModel):
    """Request body for ``POST /api/v1/test-results/compare``.

    Both fields are required. The legacy handler returns 400 if either
    is missing or empty.
    """

    result_id_1: str
    result_id_2: str


class TestResultCompareOut(StrictModel):
    """Response body for ``POST /api/v1/test-results/compare``.

    Mirrors the legacy ``{success, comparison}`` envelope. The
    ``comparison`` object is a deeply-nested diff that the legacy
    handler builds inline from three different sources (the two raw
    results plus three derived violation lists with their own
    serializer). Modelling it as a typed nested schema would inflate
    this module substantially without changing the wire; we keep it as
    ``dict[str, object]`` and document the compromise inline.
    """

    success: Literal[True]
    comparison: dict[str, object]
