"""Pydantic schemas for the /api/v1/drupal/* sync endpoints (§5.12).

Wire-shape preservation
-----------------------
The Drupal sync surface covers nine handlers grouped into three
themes:

- **Discovery / status (GET)** — list audits visible in the configured
  Drupal instance and surface per-project sync counts so a UI can
  render a status banner. The list endpoints return ``{<plural>: [...]}``
  envelopes (``audits``, ``discovered_pages``, ``recordings``,
  ``issues``) rather than the cursor-paginated envelope: the legacy
  shape is preserved byte-for-byte to avoid breaking the existing
  Drupal sync UI.

- **Upload actions (POST)** — push selected pages / recordings /
  issues to Drupal. The action runs synchronously and returns an
  aggregate summary once the loop finishes. The shape includes
  per-item failure entries (``errors: list[{item, error}]``) plus
  three counts: ``success_count``, ``failure_count``,
  ``skipped_count``.

- **Import actions (POST)** — pull pages / issues from Drupal into
  the local database. Counts are ``imported`` (new local row),
  ``updated`` (existing local row keyed on ``drupal_uuid``), and
  ``skipped`` (failed on any row).

Decisions worth flagging
------------------------
- Discovered-page and recording-list payloads include the local
  model's ``drupal_*`` sync fields alongside the structural fields
  the §5.1 / §5.6 endpoints already cover. The duplication is
  intentional — the Drupal sync UI needs both shapes in a single
  round-trip, and cursoring is unnecessary because per-project
  counts are bounded in practice (typically <50 rows).

- The ``errors`` field on action endpoints is typed as
  ``list[dict[str, object]]`` rather than a closed
  ``list[{item: str, error: str}]`` schema because the legacy
  payload occasionally surfaces a non-string ``error`` value from
  upstream Drupal (e.g. a JSON object describing a constraint
  violation). Tightening the schema here would surface as a 500
  on every such failure — preserving the open shape keeps the
  contract honest.

- The audit list returns a fixed projection (``title``, ``uuid``,
  ``nid``); the upstream Drupal payload carries dozens of other
  fields that are out of scope for picking an audit. ``nid`` is
  ``Optional`` because the upstream payload occasionally omits it
  for newly-created audits whose nid hasn't been assigned yet.

- The audit list and several other endpoints have free-form
  ``Optional`` fields. The handler does the actual validation and
  surfaces field-path 400s on bad input; the schemas here document
  the wire shape rather than re-enforcing the validation.
"""
from __future__ import annotations

from typing import Optional

from auto_a11y.web.api.schemas.common import StrictModel


# ---------------------------------------------------------------------------
# Drupal audits — list endpoint
# ---------------------------------------------------------------------------


class DrupalAuditOut(StrictModel):
    """A single Drupal-side audit row.

    Mirrors the projection built inline by
    :func:`drupal_list_audits_rest`: ``title``, ``uuid`` (or ``uuId``
    upstream — the handler normalizes both), and ``nid``. ``nid`` is
    ``Optional`` because the upstream payload occasionally omits it
    for audits whose nid hasn't been assigned yet.
    """

    title: str
    uuid: Optional[str] = None
    nid: Optional[int] = None


class DrupalAuditListOut(StrictModel):
    """Response body for ``GET /drupal/audits``.

    Non-paginated ``{audits: [...]}`` shape. The legacy form mirrors
    this byte-for-byte; cursoring is unnecessary because the audit
    count is bounded by a small constant in practice.
    """

    audits: list[DrupalAuditOut]


# ---------------------------------------------------------------------------
# Per-project sync status
# ---------------------------------------------------------------------------


class DrupalSyncBucketCounts(StrictModel):
    """Per-collection sync counts.

    Mirrors the ``discovered_pages`` and ``recordings`` blocks in the
    sync-status response. ``pending`` aggregates both ``not_synced``
    and ``pending`` statuses since the UI presents them as the same
    state (anything that isn't ``synced`` or ``sync_failed``).
    """

    total: int
    synced: int
    pending: int
    failed: int


class DrupalSyncStatusOut(StrictModel):
    """Response body for ``GET /drupal/projects/<id>/sync-status``.

    Mirrors the inline projection in :func:`drupal_sync_status_rest`
    byte-for-byte: a ``drupal_enabled`` flag (whether the integration
    is configured), the project's display name, per-collection
    buckets, the most-recent sync timestamp across both collections,
    and up to 5 most-recent error messages (capped server-side to
    keep the banner readable).
    """

    drupal_enabled: bool
    project_name: str
    discovered_pages: DrupalSyncBucketCounts
    recordings: DrupalSyncBucketCounts
    last_sync_time: Optional[str] = None
    sync_errors: list[str]


# ---------------------------------------------------------------------------
# Discovered-pages list — Drupal sync view
# ---------------------------------------------------------------------------


class DrupalDiscoveredPageOut(StrictModel):
    """Discovered-page row carrying its Drupal-sync state.

    Distinct from :class:`DiscoveredPageOut` in the §5.1 / §5.3
    schemas — that one returns the full structural surface; this one
    surfaces only the fields the Drupal sync UI needs (title, url,
    interested_because, page_elements, plus all ``drupal_*`` fields).

    ``interested_because`` and ``page_elements`` are arrays of strings
    even when empty (matching the local model's defaults).
    """

    id: Optional[str] = None
    title: str
    url: str
    interested_because: list[str]
    page_elements: list[str]
    drupal_uuid: Optional[str] = None
    drupal_sync_status: str
    drupal_last_synced: Optional[str] = None
    is_synced: bool
    needs_sync: bool


class DrupalDiscoveredPageListOut(StrictModel):
    """Response body for
    ``GET /drupal/projects/<id>/discovered-pages``.

    Non-paginated ``{discovered_pages: [...]}`` envelope. Bounded by
    project-scope.
    """

    discovered_pages: list[DrupalDiscoveredPageOut]


# ---------------------------------------------------------------------------
# Recordings list — Drupal sync view
# ---------------------------------------------------------------------------


class DrupalRecordingOut(StrictModel):
    """Recording row carrying its Drupal-sync state.

    Mirrors the inline projection in
    :func:`drupal_list_recordings_rest`. ``duration`` is the recording
    length as an ``HH:MM:SS`` string (``Optional`` because recordings
    imported from Drupal may not carry a duration). ``component_names``
    is the list of UI components covered by the recording (empty
    list if none).
    """

    id: Optional[str] = None
    title: str
    duration: Optional[str] = None
    auditor_name: Optional[str] = None
    recording_type: str
    total_issues: int
    component_names: list[str]
    drupal_video_uuid: Optional[str] = None
    drupal_video_nid: Optional[int] = None
    drupal_sync_status: str
    drupal_last_synced: Optional[str] = None
    is_synced: bool
    needs_sync: bool


class DrupalRecordingListOut(StrictModel):
    """Response body for ``GET /drupal/projects/<id>/recordings``.

    Non-paginated ``{recordings: [...]}`` envelope.
    """

    recordings: list[DrupalRecordingOut]


# ---------------------------------------------------------------------------
# Issues list — Drupal sync view
# ---------------------------------------------------------------------------


class DrupalIssueOut(StrictModel):
    """Issue row carrying its Drupal-sync state.

    Mirrors the inline projection in
    :func:`drupal_list_issues_rest`. Note this is the standalone
    :class:`Issue` model, not :class:`RecordingIssue` — the latter
    cascades through the parent recording's sync state.

    ``wcag_criteria`` is the list of associated WCAG criterion ids
    (e.g. ``["1.1.1", "2.4.6"]``); empty list if the issue carries
    no WCAG references.
    """

    id: Optional[str] = None
    title: str
    impact: str
    issue_type: Optional[str] = None
    location_on_page: Optional[str] = None
    wcag_criteria: list[str]
    source_type: Optional[str] = None
    detection_method: Optional[str] = None
    status: Optional[str] = None
    drupal_uuid: Optional[str] = None
    drupal_nid: Optional[int] = None
    drupal_sync_status: str
    drupal_last_synced: Optional[str] = None
    is_synced: bool
    needs_sync: bool


class DrupalIssueListOut(StrictModel):
    """Response body for ``GET /drupal/projects/<id>/issues``.

    Non-paginated ``{issues: [...]}`` envelope.
    """

    issues: list[DrupalIssueOut]


# ---------------------------------------------------------------------------
# Upload action — POST /drupal/projects/<id>/upload
# ---------------------------------------------------------------------------


class DrupalUploadOptions(StrictModel):
    """Upload-options sub-object on the upload action body.

    ``include_french`` controls whether the recording exporter pushes
    the French translation of issue descriptions alongside English.
    Defaults to ``False`` server-side when absent.
    """

    include_french: Optional[bool] = None


class DrupalUploadIn(StrictModel):
    """Request body for ``POST /drupal/projects/<id>/upload``.

    All three ID arrays are optional; missing or empty arrays simply
    skip that resource type. The handler doesn't reject an entirely
    empty body (you get back a result with all counts == 0). The
    ``options`` block carries upload-time toggles
    (currently only ``include_french``).
    """

    discovered_page_ids: Optional[list[str]] = None
    recording_ids: Optional[list[str]] = None
    issue_ids: Optional[list[str]] = None
    options: Optional[DrupalUploadOptions] = None


class DrupalUploadOut(StrictModel):
    """Response body for the upload action.

    Mirrors the aggregate dict :func:`drupal_upload_rest` returns:
    the project id, the resolved Drupal audit UUID the upload
    targeted, and the three counts (``success_count``,
    ``failure_count``, ``skipped_count``) plus a per-item
    ``errors`` list. The ``errors`` list is typed
    ``list[dict[str, object]]`` rather than a closed
    ``list[{item: str, error: str}]`` because the legacy payload
    occasionally surfaces a non-string ``error`` value from upstream
    Drupal — tightening the schema here would surface as a 500 on
    every such failure.
    """

    project_id: str
    audit_uuid: str
    success_count: int
    failure_count: int
    skipped_count: int
    errors: list[dict[str, object]]


# ---------------------------------------------------------------------------
# Import actions — pages and issues
# ---------------------------------------------------------------------------


class DrupalImportPagesIn(StrictModel):
    """Request body for ``POST /drupal/projects/<id>/import-pages``.

    No-op body — the legacy handler accepts an empty body (or no
    body at all) and pulls every discovered page bound to the
    project's resolved audit UUID. Kept as a model rather than
    ``None`` so the OpenAPI spec carries a content type and future
    options (e.g. a filter on Drupal-side status) can be added
    without bumping the handler signature.
    """


class DrupalImportIssuesIn(StrictModel):
    """Request body for ``POST /drupal/projects/<id>/import-issues``.

    Same shape as :class:`DrupalImportPagesIn` — currently no
    fields, but kept as a model for forward-compat.
    """


class DrupalImportSummaryOut(StrictModel):
    """Aggregate response for the import-pages / import-issues actions.

    Mirrors the dict :func:`drupal_import_pages_rest` and
    :func:`drupal_import_issues_rest` return:

    - ``fetched`` — total rows pulled from Drupal
    - ``imported`` — new local rows created
    - ``updated`` — existing local rows updated (keyed on
      ``drupal_uuid``)
    - ``skipped`` — rows that failed mid-import; one entry per
      failure appears in ``errors``

    The ``errors`` list carries the same ``list[dict[str, object]]``
    shape as :class:`DrupalUploadOut.errors` for the same reason.
    """

    project_id: str
    audit_uuid: str
    fetched: int
    imported: int
    updated: int
    skipped: int
    errors: list[dict[str, object]]


# ---------------------------------------------------------------------------
# Automated-results upload action
# ---------------------------------------------------------------------------


class DrupalAutomatedUploadOptions(StrictModel):
    """Options block on the automated-results upload action.

    - ``min_component_pages`` — minimum number of pages a component
      must appear on to be considered "common" by the deduplication
      service. Defaults to 2 server-side; non-int values fall back
      to the default.
    - ``mark_pages_for_inspection`` — when true, URL-only pages
      created during deduplication are flagged for manual inspection
      (defaults to false).
    """

    min_component_pages: Optional[int] = None
    mark_pages_for_inspection: Optional[bool] = None


class DrupalAutomatedUploadIn(StrictModel):
    """Request body for
    ``POST /drupal/projects/<id>/upload-automated-results``.

    Both top-level fields are optional. ``options`` is preserved
    as a nested sub-object so future toggles can be added without
    bumping the wire contract.
    """

    options: Optional[DrupalAutomatedUploadOptions] = None


class DrupalAutomatedUploadOut(StrictModel):
    """Response body for the automated-results upload action.

    Mirrors the aggregate dict
    :func:`drupal_upload_automated_results_rest` returns. The
    ``upload_id`` is the deduplication-service batch identifier
    (``Optional`` because the service may not always assign one).

    The three count fields ``discovered_pages_created``,
    ``common_components``, and ``page_urls`` describe what the
    deduplication step produced before the Drupal upload loop
    started. ``success_count`` and ``failure_count`` then describe
    the upload-loop outcome over those pages.
    """

    project_id: str
    audit_uuid: str
    upload_id: Optional[str] = None
    discovered_pages_created: int
    common_components: int
    page_urls: int
    success_count: int
    failure_count: int
    errors: list[dict[str, object]]
