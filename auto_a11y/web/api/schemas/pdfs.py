"""Pydantic schemas for the /api/v1/pdf-documents and /pdfs endpoints (§5.9).

Wire-shape preservation
-----------------------
The PDF cluster splits across three response families:

- **JSON metadata** — list endpoints (project- and website-scoped),
  individual GET, and the multipart/JSON upload POST. Mirrored by
  :class:`PdfOut`, :class:`PdfListOut`, :class:`PdfUploadIn`.
- **Audit lifecycle JSON** — start (202), latest (200), cancel (202).
  Mirrored by :class:`PdfAuditStartOut`, :class:`PdfAuditLatestOut`,
  :class:`PdfAuditCancelOut`, and :class:`PdfAuditJobOut`.
- **Raw bytes** — the five artifact-serving endpoints (file bytes,
  per-page image, derived export, issue-map JSON, pdfMax markdown).
  These are not modelled as JSON; the handlers return a Flask
  ``Response`` straight through ``@document`` (matching the §5.5 reports
  pattern for binary downloads).

Decisions worth flagging
------------------------
- ``POST /projects/<id>/pdfs`` accepts two body shapes — multipart with
  ``pdf_file``/``website_id``, or JSON ``{website_id, source_url, ...}``.
  The ``@document`` decorator forces a choice between ``request=`` and
  ``request_form=`` (you can't have both). We model the multipart shape
  via :class:`PdfUploadIn` (since the OpenAPI spec can only describe one
  body media type per operation) and let the handler dispatch internally
  via ``request.content_type``. Every field in :class:`PdfUploadIn` is
  Optional because the JSON path emits an empty form dict that still has
  to pass ``model_validate({})``. The handler enforces the actual
  per-content-type requireds in its own logic.
- ``PdfAuditLatestOut.job`` is ``Optional`` — the document may exist
  without any audit job (PENDING / FETCHING etc.). The nested
  ``PdfAuditJobOut`` mirrors the legacy ``_serialize_pdf_audit_job``
  shape: a flat top section plus a ``progress`` sub-object with five
  fields, all of which can be ``None`` mid-flight.
- ``PdfAuditStartOut.status`` is the literal ``"queued"`` (the start
  endpoint always responds with the same job-was-enqueued sentinel; the
  real status lives on the polled ``/audits/latest`` response).
- ``source_type`` is a free-form string in :class:`PdfOut` (rather than
  a ``Literal``) because the spec generator surfaces the four-value
  ``SourceType`` set as the model's documentation; tightening to a
  ``Literal`` would force the client to know all four values at compile
  time, which we don't want for forward-compat with new source kinds.
"""
from __future__ import annotations

from typing import Literal, Optional

from auto_a11y.web.api.schemas.common import StrictModel


# ---------------------------------------------------------------------------
# POST /projects/<id>/pdfs (multipart upload OR JSON URL fetch)
# ---------------------------------------------------------------------------


class PdfUploadIn(StrictModel):
    """Form fields for the multipart upload of a PDF document.

    The file part ``pdf_file`` is declared via
    ``@document(request_files=["pdf_file"])`` and arrives in the handler
    as a :class:`werkzeug.datastructures.FileStorage` kwarg.

    Two request shapes share the underlying endpoint:

    1. **multipart/form-data** — ``pdf_file`` part with the raw PDF
       bytes plus ``website_id`` selecting which website in the project
       to attach the document to. ``original_filename`` is optional and
       overrides the file part's ``filename`` attribute.
    2. **application/json** — ``{website_id, source_url[, website_user_id]}``.
       The server fetches the URL and creates a PDF document from the
       response body.

    Because OpenAPI describes one request body media type per operation
    and ``@document`` only takes one of ``request=``/``request_form=``,
    we model the multipart shape here and let the handler dispatch by
    ``Content-Type`` at runtime. Every field is ``Optional`` so the JSON
    request path — which posts no form fields at all — still passes the
    decorator's ``request_form`` validation against an empty form dict.
    The handler enforces the real per-content-type requireds in its own
    logic and surfaces them as 400 with a structured ``field`` errors
    list.
    """

    website_id: Optional[str] = None
    original_filename: Optional[str] = None

    # JSON-path fields — present in the legacy JSON body but accepted
    # here so the schema documents the full set of recognized inputs.
    # They're ignored on the multipart path (the handler reads from
    # ``request.json`` directly there).
    source_url: Optional[str] = None
    website_user_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Response models — list / single-document
# ---------------------------------------------------------------------------


class PdfOut(StrictModel):
    """Response body for a single PDF document.

    Mirrors :func:`_serialize_pdf_document` byte-for-byte:

    - ``id`` is the stringified Mongo ``_id``;
    - ``status`` is the enum's string value
      (e.g. ``"audited"``, ``"auditing"``);
    - ``source_type`` is a string (``"uploaded"``, ``"manual_url"``,
      ``"opportunistic"``, or ``"discovered"``); kept as ``str`` rather
      than a Literal so future source kinds don't break older clients;
    - ``discovered_at`` and ``last_audited_at`` are ISO 8601 strings;
    - ``storage_relpath`` / ``images_relpath`` are server-side relative
      paths under the configured PDF storage base directory.
    """

    id: Optional[str] = None
    website_id: str
    project_id: str
    source_url: Optional[str] = None
    source_type: str
    discovered_from_page_id: Optional[str] = None
    discovered_from_user_id: Optional[str] = None
    sha256: str
    file_size_bytes: int
    storage_relpath: str
    images_relpath: str
    original_filename: Optional[str] = None
    pdf_version: Optional[str] = None
    page_count: Optional[int] = None
    declared_lang: Optional[str] = None
    detected_lang: Optional[str] = None
    lang_confidence: Optional[float] = None
    status: str
    error_reason: Optional[str] = None
    last_audit_result_id: Optional[str] = None
    discovered_at: Optional[str] = None
    last_audited_at: Optional[str] = None


class PdfListOut(StrictModel):
    """Response body for ``GET /api/v1/{projects,websites}/<id>/pdfs``.

    Cursor-paginated; same ``{items, next_cursor}`` shape as the other
    v1 list endpoints.
    """

    items: list[PdfOut]
    next_cursor: Optional[str] = None


# ---------------------------------------------------------------------------
# Audit lifecycle
# ---------------------------------------------------------------------------


class PdfAuditStartIn(StrictModel):
    """Request body for ``POST /pdf-documents/<id>/audits``.

    All fields are optional. The legacy handler tolerates a fully
    empty body (no JSON at all, or ``{}``) and falls back to
    ``run_ai=False, wcag_level="AA", locale="en"``. We accept
    ``wcag_level`` as ``str`` rather than ``Literal["AA", "AAA"]`` so
    invalid values surface as a 400 with a structured ``field``/``code``
    rather than Pydantic's generic ``literal_error``. The same handler
    logic produces a 400 with code ``invalid_value`` either way; this
    keeps the existing frontend's error-display contract.
    """

    run_ai: Optional[bool] = None
    wcag_level: Optional[str] = None
    locale: Optional[str] = None


class PdfAuditStartOut(StrictModel):
    """Response body (202) for ``POST /pdf-documents/<id>/audits``.

    Mirrors the legacy start-audit handler's JSON shape exactly. The
    ``status`` field is always the literal ``"queued"`` — the real
    progress comes from polling ``GET /pdf-documents/<id>/audits/latest``.
    """

    job_id: str
    pdf_document_id: str
    run_ai: bool
    wcag_level: str
    locale: str
    status: Literal["queued"]


class PdfAuditProgressOut(StrictModel):
    """Progress sub-object inside :class:`PdfAuditJobOut`.

    Mirrors the legacy ``_serialize_pdf_audit_job`` progress block.
    Every field may be ``None`` mid-flight: the worker reports
    ``current``/``total`` as soon as it sees a unit count, and
    ``stage``/``fraction`` from the audit pipeline's progress hooks.
    The ``message`` is a human-readable string the UI surfaces during
    a long run.
    """

    current: Optional[int] = None
    total: Optional[int] = None
    message: Optional[str] = None
    stage: Optional[str] = None
    fraction: Optional[float] = None


class PdfAuditJobOut(StrictModel):
    """Nested job shape inside :class:`PdfAuditLatestOut`.

    Mirrors :func:`_serialize_pdf_audit_job` byte-for-byte. Surfaces
    only the four flat fields plus the ``progress`` sub-object so
    clients that move from the legacy ``audit_status`` HTML route only
    need to swap the URL, not the parser.
    """

    job_id: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None
    progress: PdfAuditProgressOut


class PdfAuditLatestOut(StrictModel):
    """Response body for ``GET /pdf-documents/<id>/audits/latest``.

    ``job`` is ``null`` only when the document has *never* had an audit
    job recorded. After at least one run the latest job stays in the
    response so clients can render "last audit failed at X" even when
    no fresh job is in flight.
    """

    pdf_document_id: str
    doc_status: str
    error_reason: Optional[str] = None
    last_audit_result_id: Optional[str] = None
    job: Optional[PdfAuditJobOut] = None


class PdfAuditCancelOut(StrictModel):
    """Response body (202) for ``POST /pdf-documents/<id>/audits/latest/cancel``.

    Echoes the new document status and how many active jobs were
    flagged for cancellation. ``cancellation_requested_count`` is 0
    when the document was stuck in ``AUDITING`` with no live worker
    (the escape-hatch path) — in that case the doc status is still
    forced to ``AUDIT_FAILED`` so the user can start a new audit.
    """

    pdf_document_id: str
    cancellation_requested_count: int
    doc_status: str
