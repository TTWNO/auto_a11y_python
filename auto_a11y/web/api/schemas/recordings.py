"""Pydantic schemas for the /api/v1/recordings endpoints (§5.6).

Wire-shape preservation
-----------------------
Recordings are the only resource in the /api/v1 surface that requires
a multipart upload (the Dictaphone JSON files for English and optional
French). The wire shapes therefore split across:

- :class:`RecordingUploadIn` — non-file form fields for ``POST /recordings``,
  consumed via ``@document(request_form=..., request_files=[...])`` so
  the spec emits a single ``multipart/form-data`` body whose ``properties``
  inline the form fields + the two file parts as binary strings.
- :class:`RecordingPatch` — JSON body for partial metadata updates.
- :class:`RecordingContentPatch` — empty model marker for the supplementary
  content upload (multipart with up to six optional file parts and no
  required non-file fields).
- :class:`RecordingOut`, :class:`RecordingListOut`,
  :class:`RecordingContentOut`, :class:`RecordingIssueOut`,
  :class:`RecordingIssueListOut` — response envelopes that mirror the
  legacy ``_serialize_recording`` / ``_serialize_recording_issue`` shapes
  byte-for-byte (ISO 8601 timestamps, enum values stringified,
  ``ObjectId`` rendered as ``str`` via ``Recording.id``).

Decisions worth flagging
------------------------
- ``recording_type`` is accepted as an arbitrary string in
  :class:`RecordingUploadIn`. The legacy handler runs
  ``RecordingType(value)`` itself to surface a 400 with a useful
  ``invalid_value`` field path; tightening the schema to a Literal
  would emit the same 400 but with Pydantic's default error code
  (``literal_error``), which the existing frontend doesn't recognise.
  Same logic for the testing-scope ``scope_*`` checkboxes: the browser
  idiom sends literal ``"on"``, so we accept ``Optional[str]`` and let
  the handler interpret it. Wire-shape preservation prevails over
  schema purity here.
- ``RecordingContentPatch`` is intentionally empty: the supplementary
  content PATCH carries only file parts and no form fields, but the
  decorator requires a ``request_form`` model for any multipart shape.
  An empty :class:`StrictModel` is the smallest valid choice.
- Recording issues use the same legacy ``{items, next_cursor}`` shape
  as the other v1 list endpoints. We expose the ``timecodes`` and
  ``wcag`` sub-arrays as ``list[dict[str, object]]`` because their
  legacy ``to_dict()`` output isn't yet modelled here (and remodelling
  would inflate this surface materially).
"""
from __future__ import annotations

from typing import Optional, Union

from pydantic import RootModel

from auto_a11y.web.api.schemas.common import StrictModel


# ---------------------------------------------------------------------------
# POST /recordings (multipart upload)
# ---------------------------------------------------------------------------


class RecordingUploadIn(StrictModel):
    """Form fields for the multipart upload of a Dictaphone recording.

    The two file parts ``recording_json_en`` (required) and
    ``recording_json_fr`` (optional) are declared via
    ``@document(request_files=[...])`` and arrive in the handler as
    :class:`werkzeug.datastructures.FileStorage` kwargs alongside this
    parsed form model.

    Field semantics mirror the legacy ``request.form.get(...)`` handler:

    - ``project_id`` — required; identifies the target project.
    - ``title`` / ``description`` / ``auditor_name`` / ``auditor_role`` —
      free-text auditor metadata, all optional.
    - ``recording_type`` — optional string; defaults to ``"audit"`` on the
      server. The handler validates against :class:`RecordingType` so
      invalid values surface as 400 with a ``recording_type`` field path
      rather than a generic Pydantic error.
    - ``task_description`` — optional task summary.
    - ``test_user_account``, ``lived_experience_tester_id``,
      ``test_supervisor_id`` — optional auditor-context ids; the handler
      collapses empty strings to ``None``.
    - ``media_file_path`` — optional pointer to the underlying MP4.
    - ``page_urls`` / ``component_names`` / ``app_screens`` /
      ``device_sections`` — optional newline-separated lists parsed by
      ``_parse_multiline_form_field`` in the handler.
    - ``discovered_page_ids`` — optional. Multipart forms send list
      fields as repeated entries with the same key; the handler reads
      that via ``request.form.getlist("discovered_page_ids")``. We
      accept the legacy single-key shape here (the value is the *last*
      submitted entry, which is what ``dict(request.form.items())``
      yields); the handler still reads the full list via ``getlist``
      so multi-entry posts continue to work end-to-end.
    - ``scope_forms`` … ``scope_drag_drop`` — checkbox booleans. The
      browser idiom sends the literal string ``"on"`` for true and
      omits the field entirely for false; we accept ``Optional[str]``
      to preserve that wire and let the handler interpret it.
    """

    project_id: str

    title: Optional[str] = None
    description: Optional[str] = None
    auditor_name: Optional[str] = None
    auditor_role: Optional[str] = None
    recording_type: Optional[str] = None
    task_description: Optional[str] = None
    test_user_account: Optional[str] = None
    lived_experience_tester_id: Optional[str] = None
    test_supervisor_id: Optional[str] = None
    media_file_path: Optional[str] = None

    page_urls: Optional[str] = None
    component_names: Optional[str] = None
    app_screens: Optional[str] = None
    device_sections: Optional[str] = None
    discovered_page_ids: Optional[str] = None

    scope_forms: Optional[str] = None
    scope_video: Optional[str] = None
    scope_live_multimedia: Optional[str] = None
    scope_multilingual: Optional[str] = None
    scope_orientation: Optional[str] = None
    scope_zoom: Optional[str] = None
    scope_timeouts: Optional[str] = None
    scope_motion_actuation: Optional[str] = None
    scope_drag_drop: Optional[str] = None


# ---------------------------------------------------------------------------
# PATCH /recordings/<id>
# ---------------------------------------------------------------------------


class RecordingPatch(StrictModel):
    """Request body for partial recording updates.

    Editable fields cover human-facing metadata plus two scope fields
    that the legacy ``/recordings/<id>/edit`` form has always written:
    ``page_urls`` (free-form URL list) and ``discovered_page_ids``
    (cross-reference to discovered-pages). Server-managed fields
    (counts, project_id, recording_id, recording_type, media_file_path,
    Drupal sync, and the multi-language content arrays) are not patchable
    here.

    ``page_urls`` accepts either a list of strings or a single newline-
    separated string; the handler normalises to a list. Same shape
    flexibility for ``discovered_page_ids`` (list-or-comma-separated).

    The PATCH body is *partial* — only fields the client sends are
    applied. Pydantic's ``model_fields_set`` is the signal the handler
    uses to distinguish "absent" from "explicit ``null``".
    """

    title: Optional[str] = None
    description: Optional[str] = None
    auditor_name: Optional[str] = None
    auditor_role: Optional[str] = None
    tags: Optional[list[str]] = None
    notes: Optional[str] = None
    page_urls: Optional[Union[list[str], str]] = None
    discovered_page_ids: Optional[Union[list[str], str]] = None


# ---------------------------------------------------------------------------
# PATCH /recordings/<id>/content  (multipart, no form fields)
# ---------------------------------------------------------------------------


class RecordingContentPatch(StrictModel):
    """Empty form model for the supplementary content PATCH.

    The endpoint accepts up to six optional file parts (key_takeaways
    / user_painpoints / user_assertions × en/fr) and zero non-file
    fields. ``@document`` requires *some* form model whenever the wire
    is multipart, so we declare an empty :class:`StrictModel` whose
    ``extra='forbid'`` still rejects any stray form fields a misbehaving
    client might include.
    """


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class RecordingOut(StrictModel):
    """Response body for single-recording GET / POST / PATCH.

    Mirrors ``_serialize_recording`` byte-for-byte. ``id`` is the
    stringified Mongo ``_id``; ``recording_type`` is the enum's
    string value (e.g. ``"audit"``). Timestamps are ISO 8601.
    """

    id: Optional[str] = None
    recording_id: str
    title: str
    description: Optional[str] = None
    duration: Optional[str] = None
    recorded_date: Optional[str] = None
    auditor_name: Optional[str] = None
    auditor_role: Optional[str] = None
    recording_type: str
    project_id: Optional[str] = None
    testing_scope: dict[str, bool]
    website_ids: list[str]
    page_urls: list[str]
    page_ids: list[str]
    discovered_page_ids: list[str]
    component_names: list[str]
    app_screens: list[str]
    device_sections: list[str]
    task_description: Optional[str] = None
    total_issues: int
    high_impact_count: int
    medium_impact_count: int
    low_impact_count: int
    tags: list[str]
    notes: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class RecordingListOut(StrictModel):
    """Response body for ``GET /api/v1/recordings``.

    Cursor-paginated using the legacy ``{items, next_cursor}`` shape.
    """

    items: list[RecordingOut]
    next_cursor: Optional[str] = None


class RecordingContentOut(StrictModel):
    """Response body for the supplementary-content GET and PATCH.

    Each field is a per-language dict (``{en: [...], fr: [...]}``) of
    structured content items. The inner item shapes vary by content
    type and are produced by the parser modules — we expose them as
    ``list[dict[str, object]]`` rather than fully typing every variant
    here.
    """

    key_takeaways: dict[str, list[dict[str, object]]]
    user_painpoints: dict[str, list[dict[str, object]]]
    user_assertions: dict[str, list[dict[str, object]]]


class RecordingIssueOut(StrictModel):
    """Response body for a single recording-issue resource.

    Mirrors ``_serialize_recording_issue`` byte-for-byte. ``impact``
    is the enum's string value (e.g. ``"high"``). ``timecodes`` and
    ``wcag`` carry per-item dicts produced by their model's
    ``to_dict()``.
    """

    id: Optional[str] = None
    recording_id: str
    title: str
    short_title: Optional[str] = None
    language: str
    what: Optional[str] = None
    why: Optional[str] = None
    who: Optional[str] = None
    remediation: Optional[str] = None
    impact: str
    touchpoint: Optional[str] = None
    timecodes: list[dict[str, object]]
    wcag: list[dict[str, object]]
    xpath: Optional[str] = None
    element: Optional[str] = None
    html: Optional[str] = None
    project_id: Optional[str] = None
    website_ids: list[str]
    page_urls: list[str]
    page_ids: list[str]
    component_names: list[str]
    app_screens: list[str]
    device_sections: list[str]
    task_description: Optional[str] = None
    status: str
    assigned_to: Optional[str] = None
    resolution_notes: Optional[str] = None
    tags: list[str]
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class RecordingIssueListOut(StrictModel):
    """Response body for ``GET /api/v1/recordings/<id>/issues``.

    Cursor-paginated; same ``{items, next_cursor}`` shape as the other
    list endpoints.
    """

    items: list[RecordingIssueOut]
    next_cursor: Optional[str] = None


# ---------------------------------------------------------------------------
# PATCH /api/v1/recording-issues/<id>
# ---------------------------------------------------------------------------


class RecordingIssuePatchIn(RootModel[dict[str, object]]):
    """Request body for ``PATCH /api/v1/recording-issues/<issue_id>``.

    Free-form ``dict[str, object]`` rather than a typed schema so the
    handler can preserve the legacy 400 wire shape on validation
    failures. The editable fields are:

    - ``status`` — one of ``open``, ``in_progress``, ``resolved``,
      ``verified``. Invalid values emit
      ``{field: status, code: invalid_value, ...}`` (Pydantic's
      literal validator would emit ``literal_error``).
    - ``assigned_to`` — string or null. The handler distinguishes
      "absent" (leave untouched) from "null" (clear). A typed
      ``Optional[str]`` would collapse both into ``None``.
    - ``resolution_notes`` — same null vs absent semantics as
      ``assigned_to``.
    - ``tags`` — list of strings. The handler validates the
      list-of-strings shape and emits ``{field: tags, code:
      invalid_type, ...}``; Pydantic's list-of-str validator would
      emit a different envelope.

    The bulk content fields (what/why/who/remediation, timecodes,
    WCAG references) are populated from the source JSON at upload
    time and are intentionally not patchable here.

    Mirrors :class:`auto_a11y.web.api.schemas.admin.GenericSectionPatchIn`:
    same RootModel pattern, same "valid keys / value rules live in
    the handler, not the schema" rationale. The OpenAPI surface for
    this endpoint is therefore a free-form object; consumers should
    consult :class:`RecordingIssueOut` for the documented field
    semantics.
    """
