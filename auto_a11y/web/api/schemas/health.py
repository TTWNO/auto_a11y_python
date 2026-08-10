"""Pydantic schemas for the ``/api/v1/health/*`` and ``/api/v1/jobs/*`` endpoints (§5.15).

Wire-shape preservation
-----------------------
This module covers two sub-surfaces:

- **Health** — ``GET /health`` and ``GET /health/pdf``. Both are
  unauthenticated probes intended for load balancers, container
  orchestrators, and operator scripts. ``/health`` reports overall
  app+database liveness (``"healthy"`` ⇒ DB ping succeeded,
  ``"degraded"`` ⇒ DB ping failed but the Flask app is still up).
  ``/health/pdf`` reports the PDF audit subsystem (page renderer +
  storage dir) and returns 503 when either check fails.

- **Jobs** — the legacy administrative tools (``stats``, ``clear-all``,
  ``clear-stale``, ``active``, ``cleanup-page-counts``) plus the
  per-job REST endpoints (``GET /jobs/<id>``, ``POST
  /jobs/<id>/cancel``). The legacy tools return ad-hoc
  ``success`` / ``cleared_count`` envelopes; the per-job REST
  endpoints return the full job projection from ``_serialize_job``.

Decisions worth flagging
------------------------
- **``/health`` endpoints are documented as ``security="public"``.**
  They have no auth gate — load balancers and Kubernetes probes must
  be able to call them without credentials. The OpenAPI spec
  surfaces this explicitly so consumers don't generate clients that
  attach Bearer tokens for no reason.

- **``HealthOut.status`` is a free-form ``str``.** The legacy
  handler emits exactly two values today (``"healthy"`` /
  ``"degraded"``) but a future check (e.g. degraded-because-cache-down)
  could grow new values. Modelling it as ``Literal[...]`` would force
  a schema bump on every new health signal. The same reasoning
  applies to ``HealthOut.database``.

- **``JobStatsOut.by_type`` is ``dict[str, dict[str, int]]``.** The
  aggregation pipeline keys the outer dict by ``job_type`` (a free-
  form enum that grows with new background workers) and the inner
  dict by ``status`` (six values today, but the enum can grow with
  new lifecycle states). Modelling them as ``Literal`` unions would
  duplicate the live ``JobType`` / ``JobStatus`` enums in two places.
  The OpenAPI surface is a free-form object — consumers should treat
  the keys as opaque labels and render whatever the aggregation
  emits. ``by_status`` follows the same pattern (``dict[str, int]``).

- **``JobOut.progress`` and ``JobOut.metadata`` are
  ``dict[str, object]``.** Each ``job_type`` records its own shape
  here (a discovery job tracks ``pages_found``; a report job tracks
  ``stage`` + ``percent``; etc.). Surfacing every per-job-type shape
  as a typed union would duplicate the runtime job-type registry in
  the schema layer. Callers are expected to switch on ``job_type``
  before reading these blocks.

- **``JobOut.result`` and ``JobOut.error`` are ``Optional[object]``.**
  ``result`` is whatever the job worker stored on success (a string,
  a dict, a list of IDs — depends on ``job_type``); ``error`` is a
  string in practice but the Mongo doc has no type discipline so we
  surface it loosely.

- **``JobOut`` mirrors ``_serialize_job`` field-for-field.** Every
  field uses the helper's projection key. Datetimes are ISO 8601
  strings (the helper calls ``_iso(...)`` on them). The Mongo
  ``_id`` is not surfaced — ``job_id`` is the public identifier.

- **``JobListOut`` is ``{jobs: [JobOut], count: int}``** rather than
  the standard ``ListEnvelope`` from ``common.py``. The legacy
  handler shape predates the cursor-pagination convention and the
  list is bounded server-side at 100 entries; preserving the
  existing wire shape is more important than aligning with the
  newer convention.

- **The cleanup/clear-all envelopes preserve their ad-hoc fields.**
  ``cleared_count``, ``running_cleared``, ``pending_cleared``,
  ``pages_reset``, ``pages_cleaned`` are all distinct field names
  from the legacy handlers. Modelling them as a uniform "operation
  result" envelope would change the wire shape; the goal of §5.15
  is documentation, not refactor.
"""
from __future__ import annotations

from typing import Optional

from auto_a11y.web.api.schemas.common import StrictModel


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthOut(StrictModel):
    """Response body for ``GET /health``.

    Mirrors the legacy ``health_check`` projection byte-for-byte:

    - ``status`` — overall app health. ``"healthy"`` when the DB
      ping succeeded, ``"degraded"`` when it failed. Typed loosely
      as ``str`` so a future check can grow new values without a
      schema bump.
    - ``database`` — ``"healthy"`` / ``"unhealthy"``. Same loose
      typing rationale.
    - ``timestamp`` — ISO 8601 string from ``datetime.now()``. Not
      timezone-aware (the legacy call is naive); consumers should
      treat it as server local time.
    """

    status: str
    database: str
    timestamp: str


class RendererHealthOut(StrictModel):
    """Page-rasterisation sub-block of :class:`HealthPdfOut`.

    Mirrors the dataclass ``RendererHealth`` from
    ``auto_a11y.pdf.health``: ``available`` reports whether pages can be
    rasterised for the colour and contrast checks, and ``engine`` names the
    renderer.

    Replaced the former ``ghostscript`` block when rasterisation moved from a
    Ghostscript subprocess to in-process PDFium. There is no longer a binary to
    locate, so the block no longer carries a path.
    """

    available: bool
    engine: str


class StorageHealthOut(StrictModel):
    """Storage-writability sub-block of :class:`HealthPdfOut`.

    Mirrors the dataclass ``StorageHealth`` from
    ``auto_a11y.pdf.health``: ``dir`` is the configured PDF storage
    directory and ``writable`` is the result of the probe-file
    create/delete test.
    """

    dir: str
    writable: bool


class HealthPdfOut(StrictModel):
    """Response body for ``GET /health/pdf``.

    Nested ``renderer`` and ``storage`` sub-blocks. The HTTP
    status code (200 vs 503) reflects ``PdfHealth.ok`` — the body
    itself is the same shape regardless of status so a 503 still
    surfaces actionable diagnostics.
    """

    renderer: RendererHealthOut
    storage: StorageHealthOut


# ---------------------------------------------------------------------------
# Jobs — administrative tools (legacy envelopes)
# ---------------------------------------------------------------------------


class JobStatsOut(StrictModel):
    """Response body for ``GET /jobs/stats``.

    Mirrors the legacy ``get_job_statistics`` shape byte-for-byte:

    - ``total_jobs`` — count of all jobs in the window.
    - ``by_type`` — ``{<job_type>: {<status>: count}}``. The outer
      key is a ``JobType`` enum value (string); the inner key is a
      ``JobStatus`` enum value. Both are typed loosely as ``str``
      because the runtime enums grow without coordinating with the
      schema layer.
    - ``by_status`` — ``{<status>: count}``. Aggregates the per-type
      counts up to a single bucket per status.
    """

    total_jobs: int
    by_type: dict[str, dict[str, int]]
    by_status: dict[str, int]


class JobsClearAllOut(StrictModel):
    """Response body for ``POST /jobs/clear-all``.

    Mirrors the legacy ``clear_all_jobs`` envelope byte-for-byte.
    Fields:

    - ``success`` — always ``true`` on the success path.
    - ``cleared_count`` — total jobs transitioned to CANCELLED
      (running + pending).
    - ``running_cleared`` / ``pending_cleared`` — per-bucket counts.
    - ``pages_reset`` — pages whose status was reset from
      QUEUED/TESTING back to DISCOVERED.
    - ``message`` — human-readable summary string.
    """

    success: bool
    cleared_count: int
    running_cleared: int
    pending_cleared: int
    pages_reset: int
    message: str


class JobsClearStaleOut(StrictModel):
    """Response body for ``POST /jobs/clear-stale``.

    Mirrors the legacy ``clear_stale_jobs`` envelope byte-for-byte.
    ``cleared_count`` is the number of stale jobs the JobManager
    transitioned out of RUNNING/PENDING; ``pages_reset`` is the
    parallel page-status reset count.
    """

    success: bool
    cleared_count: int
    pages_reset: int
    message: str


class JobsCleanupOut(StrictModel):
    """Response body for ``POST /jobs/cleanup-page-counts``.

    Mirrors the legacy ``cleanup_page_counts`` envelope byte-for-byte.
    ``pages_cleaned`` is the number of non-TESTED pages whose
    violation/warning/info/discovery/pass counts were zeroed.
    """

    success: bool
    pages_cleaned: int
    message: str


# ---------------------------------------------------------------------------
# Jobs — per-job REST projection
# ---------------------------------------------------------------------------


class JobOut(StrictModel):
    """Projection of a single job document.

    Mirrors the legacy ``_serialize_job`` helper field-for-field.
    Datetimes are ISO 8601 strings (the helper calls ``_iso(...)``
    on every datetime); the Mongo ``_id`` is not surfaced — the
    public identifier is ``job_id``.

    ``progress`` / ``metadata`` are free-form ``dict[str, object]``
    because each ``job_type`` records its own shape there. Callers
    should switch on ``job_type`` before interpreting them.
    ``result`` / ``error`` are ``Optional[object]`` for the same
    reason (worker-specific payloads on success; loose error
    strings/dicts on failure).
    """

    job_id: Optional[str] = None
    job_type: Optional[str] = None
    status: Optional[str] = None
    website_id: Optional[str] = None
    project_id: Optional[str] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    progress: dict[str, object]
    metadata: dict[str, object]
    error: Optional[object] = None
    result: Optional[object] = None
    cancellation_requested: bool
    cancellation_requested_at: Optional[str] = None
    cancellation_requested_by: Optional[str] = None


class JobListOut(StrictModel):
    """Response body for ``GET /jobs/active``.

    Mirrors the legacy ``get_active_jobs`` envelope: ``{jobs: [...],
    count: int}``. Not paginated — the server bounds the list at
    100 entries via the ``limit(100)`` call in the handler. The
    legacy shape predates the cursor-pagination convention so we
    keep it as-is rather than aligning with :class:`ListEnvelope`.
    """

    jobs: list[JobOut]
    count: int


class JobCancelOut(JobOut):
    """Response body for ``POST /jobs/<job_id>/cancel``.

    The legacy handler returns the refreshed :class:`JobOut`
    projection (so the SPA can immediately observe the new
    ``CANCELLING`` status) with HTTP 202 to signal that the
    cancellation is asynchronous — the worker thread still has to
    notice the flag and transition to ``CANCELLED`` on its next
    checkpoint.

    Subclassing :class:`JobOut` keeps the wire shape identical while
    giving the OpenAPI spec a distinct operation-response name. Two
    schemas for the same shape sounds redundant but means future
    divergence (e.g. adding a ``cancel_acknowledged_at`` field that
    only the cancel response needs) doesn't force a schema bump on
    the read path.
    """
