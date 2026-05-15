"""Pydantic schemas for the /api/v1/pages/* and /api/v1/discovered-pages/* endpoints (§5.3).

Wire-shape preservation
-----------------------
The existing 15 CRUD handlers in ``auto_a11y/web/routes/api.py`` return
shapes produced by ``_serialize_page()`` / ``_serialize_discovered_page()``
/ ``_serialize_test_state_matrix()`` and accept input bodies processed
by their respective ``_build_*_from_body()`` / ``_apply_patch_to_*()``
helpers. This module mirrors those shapes exactly:

- ``GET /websites/<id>/pages`` returns ``{"items": [...], "next_cursor": ...}``
  (cursor pagination, same shape as the websites list).
- ``POST /websites/<id>/pages`` returns the **full** ``PageOut`` resource
  with status 201 plus a ``Location`` header.
- ``GET /pages/<id>``, ``PUT /pages/<id>``, ``PATCH /pages/<id>`` return ``PageOut``.
- ``DELETE /pages/<id>`` returns 204 with no body.
- ``GET /pages/<id>/violations`` returns a flat dict of issue buckets
  (``PageViolationsOut``). The nested ``Violation`` / ``AIFinding`` items
  are modelled as ``dict[str, object]`` (their fields are tracked in the
  ``test_result`` dataclasses; modelling them here would duplicate a lot
  of typed surface for §5.4 to redo).
- ``GET /pages/<id>/matrix`` and ``PUT /pages/<id>/matrix`` return the
  test-state matrix (``PageMatrixOut``).
- The 6 discovered-pages handlers mirror the pages handlers but operate
  on a separate :class:`DiscoveredPage` resource (different field set,
  different ID space, different parent: project rather than website).

Deliberate v1 deviations
------------------------
- All input models reject unknown JSON keys (``StrictModel`` uses
  ``extra='forbid'``). The legacy handlers silently dropped unknown
  keys; tightening this is the v1 surface's commitment.
- Pydantic emits ISO 8601 timestamps via ``model_dump(mode='json')``;
  the legacy handlers already emitted ISO 8601, so the wire is
  byte-identical here.
- ``ObjectId`` from ``Page._id`` / ``DiscoveredPage._id`` is stringified
  by the ``.id`` property on each dataclass, so no extra coercion is
  needed for the resource itself.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field

from auto_a11y.web.api.schemas.common import LenientPutModel, StrictModel


# ---------------------------------------------------------------------------
# Page CRUD — request models.
# ---------------------------------------------------------------------------


PagePriority = Literal["critical", "high", "normal", "low"]


class PageIn(StrictModel):
    """Request body for ``POST /api/v1/websites/<id>/pages``.

    ``url`` is required; ``title``, ``priority``, and ``setup_script_id``
    are optional. URL is validated for the ``http(s)://`` prefix in the
    handler (matching the legacy ``_validate_url`` rule).
    """

    url: str = Field(min_length=1, max_length=2048)
    title: Optional[str] = Field(default=None, max_length=2048)
    priority: Optional[PagePriority] = None
    setup_script_id: Optional[str] = None


class PagePut(LenientPutModel):
    """Request body for ``PUT /api/v1/pages/<id>``.

    Full replace of editable fields. Server-managed fields (status,
    counts, dates, screenshot, drupal sync, discovery metadata) are
    preserved. ``website_id`` and ``url`` are also locked — the
    (website_id, url) pair is the page identity. A PUT that changes
    ``url`` is rejected with 400.
    """

    url: str = Field(min_length=1, max_length=2048)
    title: Optional[str] = Field(default=None, max_length=2048)
    priority: Optional[PagePriority] = None
    setup_script_id: Optional[str] = None


class PagePatch(StrictModel):
    """Request body for ``PATCH /api/v1/pages/<id>``.

    Only fields present in the body are applied. ``url`` and
    ``website_id`` are not patchable (the page identity is the pair).
    """

    title: Optional[str] = Field(default=None, max_length=2048)
    priority: Optional[PagePriority] = None
    setup_script_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Page CRUD — response models.
# ---------------------------------------------------------------------------


class PageOut(StrictModel):
    """Response body for single-page GET / POST / PUT / PATCH.

    Mirrors ``_serialize_page()`` byte-for-byte. ``id`` is the stringified
    Mongo ``_id``; timestamps are ISO 8601. Drupal-sync fields are
    intentionally excluded from this surface (the legacy serializer
    omitted them too).
    """

    id: Optional[str] = None
    website_id: str
    url: str
    title: Optional[str] = None
    status: str
    priority: str
    depth: int
    discovered_at: Optional[str] = None
    discovered_from: Optional[str] = None
    discovery_run_id: Optional[str] = None
    last_tested: Optional[str] = None
    violation_count: int
    warning_count: int
    info_count: int
    discovery_count: int
    pass_count: int
    test_duration_ms: Optional[int] = None
    error_reason: Optional[str] = None
    is_in_latest_discovery: bool
    screenshot_path: Optional[str] = None
    setup_script_id: Optional[str] = None
    linked_pdf_document_id: Optional[str] = None


class PageListOut(StrictModel):
    """Response body for ``GET /api/v1/websites/<id>/pages``.

    Cursor-paginated; same shape as the websites list.
    """

    items: list[PageOut]
    next_cursor: Optional[str] = None


# ---------------------------------------------------------------------------
# Page violations endpoint — response model.
# ---------------------------------------------------------------------------


class PageViolationsOut(StrictModel):
    """Response body for ``GET /api/v1/pages/<id>/violations``.

    Flattens the latest test-result's issue buckets into a single
    payload so UIs can render a violations table without a second
    request. The nested ``Violation`` / ``AIFinding`` items are
    serialized via their dataclass ``to_dict()`` methods, so we model
    them as ``dict[str, object]`` here. The §5.4 test-runs/test-results
    work will introduce typed schemas for those items; until then this
    surface round-trips through free-form dicts.
    """

    page_id: str
    test_result_id: Optional[str] = None
    tested_at: Optional[str] = None
    violations: list[dict[str, object]]
    warnings: list[dict[str, object]]
    info: list[dict[str, object]]
    discovery: list[dict[str, object]]
    ai_findings: list[dict[str, object]]


# ---------------------------------------------------------------------------
# Page test-state matrix endpoint — request + response models.
# ---------------------------------------------------------------------------


ScriptState = Literal["before", "after", "none"]


class ScriptStateDefinitionOut(StrictModel):
    """Output entry for a single script row in the page test-state matrix.

    Mirrors :meth:`ScriptStateDefinition.to_dict`.
    """

    script_id: str
    script_name: str
    test_before: bool
    test_after: bool
    execution_order: int


class ScriptOrderEntry(StrictModel):
    """One ``{script_id, execution_order}`` row in the PUT body's
    optional ``script_order`` array.

    The legacy handler validates the same two fields with explicit
    type/required checks; the Pydantic constraints capture both.
    """

    script_id: str = Field(min_length=1)
    execution_order: int


class PageMatrixIn(StrictModel):
    """Request body for ``PUT /api/v1/pages/<id>/matrix``.

    ``combinations`` is required: a list of ``{script_id: state}`` dicts
    where each value must be ``"before"``, ``"after"``, or ``"none"``.
    The handler additionally validates the inner state strings (since
    Pydantic cannot constrain values inside a free-form ``dict[str, str]``
    field at schema generation time without losing the arbitrary
    script-id key shape).

    ``script_order`` is optional and only repositions scripts already
    present on the page; entries for script IDs the page does not
    currently have are silently ignored by the handler.
    """

    combinations: list[dict[str, str]]
    script_order: Optional[list[ScriptOrderEntry]] = None


class PageMatrixOut(StrictModel):
    """Response body for matrix GET / PUT.

    Mirrors ``_serialize_test_state_matrix()``. ``id`` is null when the
    matrix has not been persisted yet (the GET handler returns a
    derived in-memory default the first time the endpoint is hit).
    """

    id: Optional[str] = None
    page_id: str
    website_id: str
    scripts: list[ScriptStateDefinitionOut]
    combinations: list[dict[str, str]]
    created_date: Optional[str] = None
    last_modified: Optional[str] = None
    created_by: Optional[str] = None


# ---------------------------------------------------------------------------
# Discovered page CRUD — request models.
# ---------------------------------------------------------------------------


class DiscoveredPageIn(StrictModel):
    """Request body for ``POST /api/v1/projects/<id>/discovered-pages``.

    Mirrors the keys ``_build_discovered_page_from_body`` reads from
    the wire. ``title`` and ``url`` are required; the rest fall back
    to the dataclass defaults. ``document_links`` items are free-form
    objects (e.g. ``{"uri": ..., "title": ...}``) — modelled as
    ``dict[str, object]`` to match the legacy handler's contract.
    """

    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    source_type: Optional[str] = None
    interested_because: Optional[list[str]] = None
    page_elements: Optional[list[str]] = None
    private_notes: Optional[str] = None
    public_notes: Optional[str] = None
    include_in_report: Optional[bool] = None
    audited: Optional[bool] = None
    manual_audit: Optional[bool] = None
    screenshot_paths: Optional[list[str]] = None
    document_links: Optional[list[dict[str, object]]] = None


class DiscoveredPagePut(LenientPutModel):
    """Request body for ``PUT /api/v1/discovered-pages/<id>``.

    Full replace of editable fields. Server-managed fields (project_id,
    source_*, drupal_*, created_at, created_by) are preserved; clients
    cannot reassign a discovered page to a different project, change
    its source, or rewrite Drupal-sync state via this endpoint.
    """

    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    source_type: Optional[str] = None
    interested_because: Optional[list[str]] = None
    page_elements: Optional[list[str]] = None
    private_notes: Optional[str] = None
    public_notes: Optional[str] = None
    include_in_report: Optional[bool] = None
    audited: Optional[bool] = None
    manual_audit: Optional[bool] = None
    screenshot_paths: Optional[list[str]] = None
    document_links: Optional[list[dict[str, object]]] = None


class DiscoveredPagePatch(StrictModel):
    """Request body for ``PATCH /api/v1/discovered-pages/<id>``.

    All fields optional; only present fields are applied. ``title`` and
    ``url`` must be non-empty if provided.
    """

    title: Optional[str] = Field(default=None, min_length=1)
    url: Optional[str] = Field(default=None, min_length=1)
    interested_because: Optional[list[str]] = None
    page_elements: Optional[list[str]] = None
    private_notes: Optional[str] = None
    public_notes: Optional[str] = None
    include_in_report: Optional[bool] = None
    audited: Optional[bool] = None
    manual_audit: Optional[bool] = None
    screenshot_paths: Optional[list[str]] = None
    document_links: Optional[list[dict[str, object]]] = None


# ---------------------------------------------------------------------------
# Discovered page CRUD — response models.
# ---------------------------------------------------------------------------


class DiscoveredPageOut(StrictModel):
    """Response body for single-discovered-page GET / POST / PUT / PATCH.

    Mirrors ``_serialize_discovered_page()`` byte-for-byte. ``id`` is
    the stringified Mongo ``_id``; timestamps are ISO 8601 strings.
    Drupal-sync fields are exposed read-only (managed by the Drupal sync
    subsystem, not by REST clients).
    """

    id: Optional[str] = None
    title: str
    url: str
    project_id: str
    source_type: str
    source_page_id: Optional[str] = None
    source_website_id: Optional[str] = None
    source_component_signature: Optional[str] = None
    source_upload_id: Optional[str] = None
    interested_because: list[str]
    page_elements: list[str]
    private_notes: Optional[str] = None
    public_notes: Optional[str] = None
    include_in_report: bool
    audited: bool
    manual_audit: bool
    screenshot_paths: list[str]
    document_links: list[dict[str, object]]
    drupal_uuid: Optional[str] = None
    drupal_sync_status: str
    drupal_last_synced: Optional[str] = None
    drupal_error_message: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    created_by: Optional[str] = None


class DiscoveredPageListOut(StrictModel):
    """Response body for ``GET /api/v1/projects/<id>/discovered-pages``.

    Cursor-paginated; same shape as the websites/pages lists.
    """

    items: list[DiscoveredPageOut]
    next_cursor: Optional[str] = None
