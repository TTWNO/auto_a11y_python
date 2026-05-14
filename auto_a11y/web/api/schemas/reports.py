"""Pydantic schemas for the /api/v1/reports endpoints (§5.5).

Wire-shape preservation
-----------------------
The report-generation surface is a single "queue a background job that
emits a file" pipeline, exposed through four POST entry points (page /
website / project / generic) plus three operations on the resulting
job record (file download, delete, restart).

Decisions worth flagging:

- The four POSTs all return 202 with a uniform handle envelope
  (``{job_id, scope, display_name, status}``). All routes call the
  same :class:`auto_a11y.core.report_run_service.ReportRunHandle` so we
  share one :class:`ReportCreatedOut` schema across them.
- The job-restart endpoint adds an ``old_job_id`` field to the same
  envelope, so :class:`JobRestartOut` extends the create shape rather
  than duplicating it.
- The four bodies have nested differences: ``ReportIn`` (page) takes
  only ``format`` + ``include_ai``; the website variant adds a ``type``
  discriminator over three values; the project variant changes the
  ``type`` discriminator to four different values; and the generic
  ``/reports`` endpoint takes all of (type, format, include_ai,
  project_id, website_id, page_id). Because the ``type`` literals
  differ between routes, we use four distinct input schemas rather
  than one with a permissive ``str`` field — the OpenAPI consumer
  benefits from per-route enum validation, and the wire is unchanged
  (each handler validates exactly the keys it accepts and ignores
  unknown extras by raising via ``extra='forbid'``).
- ``GET /reports/<report_id>/file`` returns raw binary bytes — Flask's
  :func:`send_file` Response is passed through ``@document`` unchanged.
  We document the operation with no ``response_200=`` model; the spec
  shows the operation under its tag but with only error responses, and
  the binary body is implicit. This matches the §5.5 plan's "pick (a):
  no 200 model" recommendation for binary downloads.
"""
from __future__ import annotations

from typing import Literal, Optional

from auto_a11y.web.api.schemas.common import StrictModel


# ---------------------------------------------------------------------------
# Shared format literal
# ---------------------------------------------------------------------------


# Mirrors ``_VALID_REPORT_FORMATS`` in routes/api.py. ``excel`` is an
# alias for ``xlsx`` accepted by the legacy generator and kept here for
# wire-compat with in-flight admin frontends.
ReportFormat = Literal["xlsx", "html", "csv", "pdf", "excel"]


# ---------------------------------------------------------------------------
# POST /pages/<id>/reports
# ---------------------------------------------------------------------------


class PageReportIn(StrictModel):
    """Request body for queueing a single-page report.

    Both fields are optional. ``format`` defaults to ``xlsx`` on the
    server; ``include_ai`` defaults to ``true``. Unknown keys are
    rejected by ``extra='forbid'`` to keep the surface tight.
    """

    format: Optional[ReportFormat] = None
    include_ai: Optional[bool] = None


# ---------------------------------------------------------------------------
# POST /websites/<id>/reports
# ---------------------------------------------------------------------------


# Mirrors ``_WEBSITE_REPORT_TYPES`` in routes/api.py.
WebsiteReportType = Literal["accessibility", "page-structure", "discovery"]


class WebsiteReportIn(StrictModel):
    """Request body for queueing a website-scoped report.

    The ``type`` discriminator picks the legacy generator (page-
    structure and discovery are sibling generators of the main
    accessibility report). Only the ``accessibility`` type reads
    ``include_ai`` — the others ignore it.
    """

    format: Optional[ReportFormat] = None
    type: Optional[WebsiteReportType] = None
    include_ai: Optional[bool] = None


# ---------------------------------------------------------------------------
# POST /projects/<id>/reports
# ---------------------------------------------------------------------------


# Mirrors ``_PROJECT_REPORT_TYPES`` in routes/api.py.
ProjectReportType = Literal[
    "accessibility", "discovery", "recordings", "deduplicated"
]


class ProjectReportIn(StrictModel):
    """Request body for queueing a project-scoped report.

    ``type`` selects one of four project-level generators. Project
    accessibility reports do not currently honour ``include_ai``
    (the legacy handler never read it), so the field is omitted from
    this schema rather than accepted-and-discarded.
    """

    format: Optional[ReportFormat] = None
    type: Optional[ProjectReportType] = None


# ---------------------------------------------------------------------------
# POST /reports  (generic)
# ---------------------------------------------------------------------------


# Mirrors ``_GENERIC_REPORT_TYPES`` in routes/api.py.
GenericReportType = Literal[
    "accessibility",
    "page-structure",
    "discovery",
    "static-html",
    "deduplicated",
    "recordings",
]


class ReportIn(StrictModel):
    """Request body for the generic ``POST /reports`` entry point.

    A single body that accepts every report type plus optional ids;
    the handler picks the scope from ``(type, ids)``. ``project_id``,
    ``website_id``, and ``page_id`` are mutually optional — supplying
    none asks for the "all projects" roll-up (superadmin only).
    """

    type: Optional[GenericReportType] = None
    project_id: Optional[str] = None
    website_id: Optional[str] = None
    page_id: Optional[str] = None
    format: Optional[ReportFormat] = None
    include_ai: Optional[bool] = None


# ---------------------------------------------------------------------------
# Response envelopes
# ---------------------------------------------------------------------------


class ReportCreatedOut(StrictModel):
    """Response body (202) for every report-generation POST.

    Mirrors :func:`_serialize_report_handle` byte-for-byte.
    ``status`` is always ``"queued"``; ``scope`` is the canonical
    scope name (``page``, ``website``, ``project``, ``page_structure``,
    ``discovery_website``, ``discovery_project``, ``static_html``,
    ``deduplicated``, ``recordings``, ``all``).
    """

    job_id: str
    scope: str
    display_name: str
    status: Literal["queued"]


class JobRestartOut(StrictModel):
    """Response body (202) for ``POST /jobs/<id>/restart``.

    Same shape as :class:`ReportCreatedOut` plus ``old_job_id`` so the
    caller can tie the new run to the original. The new ``job_id`` is
    the freshly-queued worker's id — the old job is left in place (its
    cancellation flag is set so its worker exits cleanly).
    """

    old_job_id: str
    job_id: str
    scope: str
    display_name: str
    status: Literal["queued"]


# ---------------------------------------------------------------------------
# GET /projects/<id>/report-summary
# ---------------------------------------------------------------------------


class ProjectReportSummaryItemOut(StrictModel):
    """One entry in :attr:`ProjectReportSummaryOut.recent`.

    Mirrors :func:`_summarize_record` byte-for-byte. Every field is
    ``Optional`` because the underlying job document carries
    ``metadata`` / ``result`` as free-form dicts: the helper reaches
    in via ``.get(...)`` and surfaces ``None`` when a key is missing
    (which legacy job docs from before the metadata schema settled
    can be).

    ``id`` is the public ``job_id``, not the Mongo ``_id``.
    ``filename`` comes from the worker's stored ``result.filename``
    (the generated report file in :data:`Config.REPORTS_DIR`).
    Timestamps are ISO 8601 strings via :func:`_iso_or_none`.
    """

    id: Optional[str] = None
    scope: Optional[str] = None
    report_type: Optional[str] = None
    display_name: Optional[str] = None
    filename: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


class ProjectReportSummaryOut(StrictModel):
    """Response body for ``GET /api/v1/projects/<id>/report-summary``.

    Mirrors the legacy ``jsonify`` payload byte-for-byte. ``by_scope``
    and ``by_report_type`` are free-form ``dict[str, int]`` — the
    keys are :data:`metadata.scope` / :data:`metadata.report_type`
    strings, which are not a closed set (new generators can add new
    values without a schema bump). The summary is "what reports are
    available to download right now": every count and the ``recent``
    list filter to ``status=COMPLETED``, so failed/cancelled jobs are
    intentionally omitted.

    ``recent`` is bounded server-side to the latest 10 records,
    newest first by ``completed_at`` descending.
    """

    project_id: str
    total_completed: int
    by_scope: dict[str, int]
    by_report_type: dict[str, int]
    recent: list[ProjectReportSummaryItemOut]
