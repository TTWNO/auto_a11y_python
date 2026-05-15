"""Pydantic schemas for the ``/api/v1/fixture-tests/*`` endpoints (§5.16).

Wire-shape preservation
-----------------------
This module covers the two fixture-test status probes that the SPA
and the ``/testing/fixture-status`` developer page consume to decide
whether a given accessibility test code is production-enabled:

- **``GET /fixture-tests/status``** — bulk view. Returns a global
  envelope with debug-mode state, the latest fixture-run summary,
  the set of error codes that have all-fixtures-passing, the
  per-error-code status map, and pre-computed category counts so
  the front-end avoids re-aggregating.

- **``GET /fixture-tests/check/<error_code>``** — per-code probe.
  Returns whether a single error code is available, with the source
  signals (passed-fixture flag, debug override, fixture path,
  last-tested timestamp) that drove the decision.

Decisions worth flagging
------------------------
- **Datetimes are surfaced as ISO 8601 strings.** ``test_config``
  and ``fixture_validator`` hand back raw ``datetime`` objects
  (``tested_at``, ``completed_at``). Pydantic's ``mode='json'``
  emits these as ISO 8601 (``"2026-05-14T11:48:15"``) rather than
  Flask's default RFC 822 (``"Fri, 14 May 2026 11:48:15 GMT"``).
  The handler explicitly calls ``.isoformat()`` so the schema sees
  a ``str`` and the wire shape is the stable ISO form. This matches
  the §5.15 ``JobOut`` precedent.

- **``FixtureRunSummaryOut`` is nullable at the envelope level, not
  inside.** ``get_fixture_run_summary()`` returns either a fully-
  populated dict or ``None`` (no fixture-runs collection rows). The
  envelope models the nullability via ``Optional[FixtureRunSummaryOut]``
  on the parent so consumers can switch on presence cleanly.

- **``test_statuses`` is ``dict[str, FixtureTestStatusEntry]``.** The
  keys are error codes — there are hundreds and they grow with the
  test catalog. The inner shape is stable enough to model
  explicitly (it's the exact projection ``FixtureValidator.get_test_status``
  emits). Modelling the entry as a typed class is a step up from
  ``dict[str, object]`` and gives downstream consumers (TypeScript
  client generators) a real type.

- **``FixtureTestStatusEntry.tested_at`` is ``Optional[str]``.** The
  validator emits a ``datetime`` (or ``None`` when no fixtures were
  ever run for that code). The handler calls ``.isoformat()`` to
  match the rest of the surface.

- **``FixtureTestStatusEntry.found_codes`` is ``list[object]``.** The
  validator hard-codes this to ``[]`` today with a comment saying it
  "could aggregate these if needed". Typing it as ``list[object]``
  keeps the door open without committing to a specific element shape.

- **``FixtureRunSummaryOut.success_rate`` is ``float``.** The
  computation in ``test_fixtures.save_test_run_summary`` is
  ``(passed / total * 100) if total > 0 else 0`` — that produces a
  float on the success path and the literal int ``0`` on the empty
  path. Surfacing it as ``float`` is the looser type that accepts
  both; the wire emits ``0`` or ``0.0`` depending on which branch
  ran.

- **The 500 error envelope is unchanged.** Both handlers return
  ``{'success': False, 'error': str(e)}`` via ``jsonify`` on
  exception. That's a ``Response`` return — the decorator passes
  it through unchanged. The ``errors=[500]`` parameter on
  ``@document`` is documentation-only; the Problem envelope is not
  in use here because the legacy handler shape pre-dates RFC 7807
  adoption in this codebase.
"""
from __future__ import annotations

from typing import Optional

from auto_a11y.web.api.schemas.common import StrictModel


# ---------------------------------------------------------------------------
# Status — bulk view
# ---------------------------------------------------------------------------


class FixtureRunSummaryOut(StrictModel):
    """Latest fixture-run summary block in :class:`FixtureTestStatusOut`.

    Mirrors the projection from
    :meth:`FixtureValidator.get_fixture_run_summary` field-for-field:

    - ``run_id`` — UUID string stamped on the run (server-side
      ``str(uuid.uuid4())`` from ``test_fixtures.py``).
    - ``completed_at`` — ISO 8601 timestamp; the handler converts
      from the raw ``datetime`` stored on the document.
    - ``total`` / ``passed`` / ``failed`` — fixture counts for the
      run.
    - ``success_rate`` — percentage (0-100) as a float; the
      computation in ``save_test_run_summary`` is
      ``(passed / total * 100) if total > 0 else 0``.
    """

    run_id: str
    completed_at: Optional[str] = None
    total: int
    passed: int
    failed: int
    success_rate: float


class FixtureTestStatusEntry(StrictModel):
    """Per-error-code entry in :class:`FixtureTestStatusOut.test_statuses`.

    Mirrors the projection from
    :meth:`FixtureValidator.get_test_status` field-for-field:

    - ``success`` — ``True`` iff every fixture for this code passed.
    - ``status_category`` — ``"all_pass"`` / ``"partial_pass"`` /
      ``"all_fail"``. Typed loosely as ``str`` so a future fourth
      category (e.g. ``"no_fixtures"``) doesn't force a schema bump.
    - ``total_fixtures`` / ``passed_fixtures`` — fixture counts for
      this code.
    - ``fixture_paths`` — every fixture path scoped to this code.
    - ``fixture_path`` — back-compat field; the validator emits a
      summary string like ``"3 fixtures"`` here for old consumers.
    - ``notes`` — per-failure notes appended by the validator when
      the code is not all-pass.
    - ``tested_at`` — ISO 8601 timestamp; the handler converts from
      the raw ``datetime`` returned by the validator.
    - ``found_codes`` — currently always ``[]``; the validator
      reserves this for future aggregation. Typed as
      ``list[object]`` so the door stays open without committing
      to an element shape.
    """

    success: bool
    status_category: str
    total_fixtures: int
    passed_fixtures: int
    fixture_paths: list[str]
    fixture_path: str
    notes: list[str]
    tested_at: Optional[str] = None
    found_codes: list[object]


class FixtureTestStatusOut(StrictModel):
    """Response body for ``GET /fixture-tests/status``.

    Bulk projection of every accessibility test code's fixture
    status, plus pre-computed category counts so the SPA does not
    have to re-aggregate. Fields mirror the legacy handler envelope
    byte-for-byte:

    - ``success`` — always ``true`` on the success path.
    - ``debug_mode`` — mirrors ``test_config.debug_mode``; when true
      every test is forcibly marked available regardless of fixture
      state.
    - ``fixture_run_summary`` — last fixture-run summary, or
      ``null`` when no runs are recorded.
    - ``passing_tests`` — error codes that have every fixture
      passing. The legacy handler returns this as a JSON array even
      though it's a set server-side; ``list[str]`` matches.
    - ``test_statuses`` — per-code status keyed by error code.
    - ``total_tests`` — ``len(test_statuses)``.
    - ``passing_count`` — ``len(passing_tests)``.
    - ``all_pass_count`` / ``partial_pass_count`` / ``all_fail_count``
      — pre-computed category counts.
    """

    success: bool
    debug_mode: bool
    fixture_run_summary: Optional[FixtureRunSummaryOut] = None
    passing_tests: list[str]
    test_statuses: dict[str, FixtureTestStatusEntry]
    total_tests: int
    passing_count: int
    all_pass_count: int
    partial_pass_count: int
    all_fail_count: int


# ---------------------------------------------------------------------------
# Check — per-code probe
# ---------------------------------------------------------------------------


class FixtureTestCheckOut(StrictModel):
    """Response body for ``GET /fixture-tests/check/<error_code>``.

    Per-code fixture probe. Mirrors the legacy handler envelope
    byte-for-byte:

    - ``success`` — always ``true`` on the success path.
    - ``error_code`` — echo of the path parameter.
    - ``available`` — ``True`` when the code is enabled in
      production (all fixtures passing OR ``debug_mode`` is on).
    - ``passed_fixture`` — pure fixture signal, ignoring debug
      mode. ``False`` when fixtures partially or fully failed.
    - ``debug_override`` — ``True`` when ``debug_mode`` is the
      reason ``available`` is true (i.e. fixtures didn't pass but
      we're forcing the test on for debugging).
    - ``fixture_path`` — the validator's back-compat summary field
      (e.g. ``"3 fixtures"``). Empty string when no fixtures were
      ever run for this code.
    - ``tested_at`` — ISO 8601 timestamp of the most recent
      fixture run for this code, or ``null`` when no run is
      recorded.
    """

    success: bool
    error_code: str
    available: bool
    passed_fixture: bool
    debug_override: bool
    fixture_path: str
    tested_at: Optional[str] = None
