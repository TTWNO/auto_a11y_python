"""PDF document management routes.

Phase 9.3 of the pdfMax → auto_a11y integration. Nine routes per
:doc:`spec section 6 <../../../../docs/superpowers/specs/2026-04-24-pdf-audit-engine-port-design>`:

* GET  ``/projects/<project_id>/pdfs``                 — list scoped to a project
* GET  ``/websites/<website_id>/pdfs``                 — list scoped to a website
* GET  ``/projects/<project_id>/pdfs/add``             — upload form
* POST ``/projects/<project_id>/pdfs``                 — create (upload or URL)
* GET  ``/pdfs/<pdf_document_id>``                     — detail page
* POST ``/pdfs/<pdf_document_id>/audit``               — re-audit (async)
* GET  ``/pdfs/<pdf_document_id>/file``                — stream stored bytes
* GET  ``/pdfs/<pdf_document_id>/images/<image_name>`` — stream extracted image
* POST ``/pdfs/<pdf_document_id>/delete``              — cascade delete

All routes require an authenticated user. Project- and website-keyed
routes use :func:`project_role_required` for permission checks; routes
keyed only by ``pdf_document_id`` resolve the project via the
:class:`~auto_a11y.models.pdf_document.PdfDocument` and check the
effective role via the local :func:`_require_pdf_role` helper.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from flask import (
    Blueprint, abort, flash, jsonify, redirect, render_template, request,
    send_file, url_for,
)
from flask_login import current_user, login_required
from werkzeug.wrappers import Response

from typing import Any

from auto_a11y.core.job_manager import JobManager, JobStatus, JobType
from auto_a11y.models.app_user import UserRole
from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus
from auto_a11y.pdf.errors import (
    FetchFailed,
    NotAPdf,
    PdfTooLarge,
)
from auto_a11y.pdf.storage import PdfStorage
from auto_a11y.web.fluent import ftl
from auto_a11y.web.routes.auth import get_effective_role, project_role_required
from auto_a11y.web.typed_app import get_app_config, get_db, get_pdf_runner
from auto_a11y.web.view_models.target import Crumb, target_view_from_pdf

logger = logging.getLogger(__name__)
pdf_bp = Blueprint('pdf', __name__)


# Read-side roles allowed to view a PDF document.
_READ_ROLES = (UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT)
# Write-side roles allowed to mutate (create / audit / delete) a PDF.
_WRITE_ROLES = (UserRole.ADMIN, UserRole.AUDITOR)


def _get_storage() -> PdfStorage:
    """Build a :class:`PdfStorage` configured from app config."""
    cfg = get_app_config()
    return PdfStorage(base_dir=Path(cfg.PDF_STORAGE_DIR))


def _require_pdf_role(
    pdf: PdfDocument, *roles: UserRole
) -> Response | None:
    """Authorise the current user against a PDF-keyed route.

    The PDF-keyed routes (``detail``/``audit``/``file``/``image``/
    ``delete``) take only ``pdf_document_id`` in their URL, so
    :func:`project_role_required` (which derives the project from
    ``project_id``/``website_id``/``page_id`` kwargs) cannot do the
    check on its own. This helper fetches the effective role for the
    PDF's project and either:

    * returns ``None`` when access is granted, or
    * returns a :class:`Response` (or aborts with 403 / redirects to
      login) when access is denied.

    Superadmins always pass. Anonymous users get a redirect to login;
    authenticated users without a sufficient role get a 403.
    """
    if not current_user.is_authenticated:
        flash(ftl('common-please-log-in-to-access-this-page'), 'warning')
        return redirect(url_for('auth.login', next=request.url))

    if getattr(current_user, 'is_superadmin', False):
        return None

    effective = get_effective_role(
        current_user, request, pdf.project_id, pdf.website_id, None
    )
    if effective not in roles:
        flash(
            ftl('common-you-do-not-have-permission-to-access-this-resource'),
            'danger',
        )
        abort(403)
    return None


@pdf_bp.route('/projects/<project_id>/pdfs', methods=['GET'])
@login_required
@project_role_required(*_READ_ROLES)
def list_for_project(project_id: str) -> str | Response:
    """List PDF documents in a project (across all websites)."""
    db = get_db()
    project = db.get_project(project_id)
    if project is None:
        flash(ftl('common-project-not-found'), 'error')
        return redirect(url_for('projects.list_projects'))

    status_filter = request.args.get('status')
    has_issues_filter = request.args.get('has_issues') == 'true'

    pdfs = db.get_pdf_documents(project_id=project_id, limit=500)
    if status_filter:
        pdfs = [p for p in pdfs if p.status.value == status_filter]

    return render_template(
        'pdf/list.html',
        project=project,
        website=None,
        pdfs=pdfs,
        status_filter=status_filter,
        has_issues_filter=has_issues_filter,
    )


@pdf_bp.route('/websites/<website_id>/pdfs', methods=['GET'])
@login_required
@project_role_required(*_READ_ROLES)
def list_for_website(website_id: str) -> str | Response:
    """List PDF documents for one website."""
    db = get_db()
    website = db.get_website(website_id)
    if website is None:
        flash(ftl('common-website-not-found'), 'error')
        return redirect(url_for('projects.list_projects'))

    pdfs = db.get_pdf_documents(website_id=website_id, limit=500)
    project = db.get_project(website.project_id)
    return render_template(
        'pdf/list.html',
        project=project,
        website=website,
        pdfs=pdfs,
        status_filter=None,
        has_issues_filter=False,
    )


@pdf_bp.route('/projects/<project_id>/pdfs/add', methods=['GET'])
@login_required
@project_role_required(*_WRITE_ROLES)
def add_form(project_id: str) -> str | Response:
    """Show the upload/URL form."""
    db = get_db()
    project = db.get_project(project_id)
    if project is None:
        flash(ftl('common-project-not-found'), 'error')
        return redirect(url_for('projects.list_projects'))

    websites = db.get_websites(project_id)
    return render_template('pdf/add.html', project=project, websites=websites)


@pdf_bp.route('/projects/<project_id>/pdfs', methods=['POST'])
@login_required
@project_role_required(*_WRITE_ROLES)
def create(project_id: str) -> Response:
    """Handle upload (multipart file) or manual-URL form submission."""
    db = get_db()
    project = db.get_project(project_id)
    if project is None:
        flash(ftl('common-project-not-found'), 'error')
        return redirect(url_for('projects.list_projects'))

    website_id = request.form.get('website_id')
    if not website_id:
        flash(ftl('pdf-error-website-required'), 'error')
        return redirect(url_for('pdf.add_form', project_id=project_id))

    website = db.get_website(website_id)
    if website is None or website.project_id != project_id:
        flash(ftl('pdf-error-website-not-in-project'), 'error')
        return redirect(url_for('pdf.add_form', project_id=project_id))

    runner = get_pdf_runner()
    if runner is None:
        flash(ftl('pdf-error-pdf-runner-not-configured'), 'error')
        return redirect(url_for('pdf.add_form', project_id=project_id))

    user_id_value = (
        current_user.get_id() if current_user.is_authenticated else None
    )
    user_id_str = str(user_id_value) if user_id_value is not None else None

    # Path A: file upload
    uploaded = request.files.get('pdf_file')
    if uploaded is not None and uploaded.filename:
        pdf_bytes = uploaded.read()
        try:
            doc = asyncio.run(runner.create_or_find_pdf_document(
                pdf_bytes,
                website_id=website_id,
                project_id=project_id,
                source_type='uploaded',
                discovered_from_page_id=None,
                discovered_from_user_id=user_id_str,
                original_filename=uploaded.filename or 'document.pdf',
                source_url=None,
            ))
        except NotAPdf:
            flash(ftl('pdf-error-not-a-pdf'), 'error')
            return redirect(url_for('pdf.add_form', project_id=project_id))
        except PdfTooLarge as exc:
            flash(
                ftl(
                    'pdf-error-pdf-too-large',
                    size=exc.size_bytes,
                    limit=exc.limit_bytes,
                ),
                'error',
            )
            return redirect(url_for('pdf.add_form', project_id=project_id))

        flash(ftl('pdf-uploaded-success'), 'success')
        if doc.id is None:
            # Defensive: a successfully-persisted PdfDocument always has an id.
            return redirect(url_for('pdf.list_for_project', project_id=project_id))
        return redirect(url_for('pdf.detail', pdf_document_id=doc.id))

    # Path B: manual URL
    url = (request.form.get('source_url') or '').strip()
    if url:
        website_user_id = request.form.get('website_user_id') or None
        try:
            pdf_bytes = asyncio.run(runner.fetch_pdf_from_url(
                url, website_user_id=website_user_id
            ))
        except FetchFailed as exc:
            flash(
                ftl('pdf-error-fetch-failed', reason=exc.reason),
                'error',
            )
            return redirect(url_for('pdf.add_form', project_id=project_id))
        except PdfTooLarge as exc:
            flash(
                ftl(
                    'pdf-error-pdf-too-large',
                    size=exc.size_bytes,
                    limit=exc.limit_bytes,
                ),
                'error',
            )
            return redirect(url_for('pdf.add_form', project_id=project_id))
        except NotAPdf:
            flash(ftl('pdf-error-not-a-pdf'), 'error')
            return redirect(url_for('pdf.add_form', project_id=project_id))

        try:
            doc = asyncio.run(runner.create_or_find_pdf_document(
                pdf_bytes,
                website_id=website_id,
                project_id=project_id,
                source_type='manual_url',
                discovered_from_page_id=None,
                discovered_from_user_id=user_id_str,
                original_filename=(
                    url.rsplit('/', 1)[-1] or 'document.pdf'
                ),
                source_url=url,
            ))
        except NotAPdf:
            flash(ftl('pdf-error-not-a-pdf'), 'error')
            return redirect(url_for('pdf.add_form', project_id=project_id))
        except PdfTooLarge as exc:
            flash(
                ftl(
                    'pdf-error-pdf-too-large',
                    size=exc.size_bytes,
                    limit=exc.limit_bytes,
                ),
                'error',
            )
            return redirect(url_for('pdf.add_form', project_id=project_id))

        flash(ftl('pdf-uploaded-success'), 'success')
        if doc.id is None:
            return redirect(url_for('pdf.list_for_project', project_id=project_id))
        return redirect(url_for('pdf.detail', pdf_document_id=doc.id))

    # Neither upload nor URL provided.
    flash(ftl('pdf-error-source-required'), 'error')
    return redirect(url_for('pdf.add_form', project_id=project_id))


@pdf_bp.route('/pdfs/<pdf_document_id>', methods=['GET'])
@login_required
def detail(pdf_document_id: str) -> str | Response:
    """Detail view: PDF iframe + latest TestResult."""
    db = get_db()
    pdf = db.get_pdf_document(pdf_document_id)
    if pdf is None:
        flash(
            ftl('pdf-error-pdf-document-not-found', doc_id=pdf_document_id),
            'error',
        )
        return redirect(url_for('projects.list_projects'))

    denied = _require_pdf_role(pdf, *_READ_ROLES)
    if denied is not None:
        return denied

    website = db.get_website(pdf.website_id)
    project = db.get_project(pdf.project_id) if pdf.project_id else None

    breadcrumb: list[Crumb] = []
    if project is not None and project.id is not None:
        breadcrumb.append(Crumb(
            label=project.name,
            url=url_for('projects.view_project', project_id=project.id),
        ))
    if website is not None and website.id is not None:
        breadcrumb.append(Crumb(
            label=website.name or website.url,
            url=url_for('websites.view_website', website_id=website.id),
        ))
    if pdf.project_id:
        breadcrumb.append(Crumb(
            label=ftl('pdf-breadcrumb'),
            url=url_for('pdf.list_for_project', project_id=pdf.project_id),
        ))
    breadcrumb.append(Crumb(
        label=pdf.original_filename or pdf.id or '',
        url=None,
    ))

    inline_url: str | None = None
    if pdf.id is not None:
        inline_url = url_for('pdf.file', pdf_document_id=pdf.id)

    target = target_view_from_pdf(
        pdf,
        breadcrumb=breadcrumb,
        inline_viewer_url=inline_url,
    )

    test_result = None
    if pdf.last_audit_result_id:
        test_result = db.get_test_result(pdf.last_audit_result_id)

    # pdfMax §2-13 inventory data, populated by Phase A of the
    # report port. Falls back to an empty dict so the template's
    # ``{% if report_sections %}`` guard skips the master section
    # cleanly for audits that pre-date the feature.
    # The report_sections payload was emitted by the audit pipeline as
    # ``dict[str, object]``; round-tripping through Mongo + the
    # untyped ``TestResult.metadata`` mapping erases that, so we
    # narrow conservatively here. The cast is justified by the
    # pipeline's contract — see ``build_report_sections``.
    report_sections: dict[str, object]
    if test_result is not None and isinstance(
        test_result.metadata.get('report_sections'), dict
    ):
        from typing import cast
        report_sections = cast(
            'dict[str, object]',
            test_result.metadata['report_sections'],
        )
    else:
        report_sections = {}

    return render_template(
        'pdf/detail.html',
        pdf=pdf,
        target=target,
        test_result=test_result,
        project=project,
        website=website,
        report_sections=report_sections,
    )


@pdf_bp.route('/pdfs/<pdf_document_id>/audit', methods=['POST'])
@login_required
def audit(pdf_document_id: str) -> Response:
    """Enqueue a re-audit. Returns immediately — does not block on the audit."""
    db = get_db()
    pdf = db.get_pdf_document(pdf_document_id)
    if pdf is None:
        flash(
            ftl('pdf-error-pdf-document-not-found', doc_id=pdf_document_id),
            'error',
        )
        return redirect(url_for('projects.list_projects'))

    denied = _require_pdf_role(pdf, *_WRITE_ROLES)
    if denied is not None:
        return denied

    if pdf.status == PdfDocumentStatus.AUDITING:
        flash(ftl('pdf-audit-already-running'), 'warning')
        return redirect(url_for('pdf.detail', pdf_document_id=pdf_document_id))

    runner = get_pdf_runner()
    if runner is None:
        flash(ftl('pdf-error-pdf-runner-not-configured'), 'error')
        return redirect(url_for('pdf.detail', pdf_document_id=pdf_document_id))

    # Local import — pulling in pdf_audit_job at module import time
    # would form a cycle: pdf_audit_job → testing.pdf_runner → ... ↻.
    from auto_a11y.core.pdf_audit_job import PdfAuditJob

    user_id_value = (
        current_user.get_id() if current_user.is_authenticated else None
    )
    user_id_str = (
        str(user_id_value) if user_id_value is not None else 'anonymous'
    )
    job = PdfAuditJob(
        runner=runner,
        db=db,
        pdf_document_id=pdf_document_id,
        run_ai=False,
        ai_api_key=None,
        wcag_level='AA',
        locale='en',
        user_id=user_id_str,
    )
    job.start()

    flash(ftl('pdf-audit-queued'), 'success')
    return redirect(url_for('pdf.detail', pdf_document_id=pdf_document_id))


@pdf_bp.route('/pdfs/<pdf_document_id>/audit-status', methods=['GET'])
@login_required
def audit_status(pdf_document_id: str) -> Response | tuple[Response, int]:
    """JSON poll endpoint for the in-progress audit's status + progress.

    Polled by ``static/js/pdf_audit_progress.js`` every couple of seconds
    while the detail page is open and the document is in AUDITING. The
    response surfaces the most-recent ``progress.message`` and ``current``
    pumped by :class:`PdfAuditJob._on_progress` so the UI can stream
    intra-stage ticks like "Extracting colours: page 7 of 50" without
    waiting for the page to refresh.

    Schema (all fields nullable to keep the consumer simple):

    .. code-block:: json

       {
         "doc_status": "auditing|audited|audit_failed|...",
         "error_reason": null,
         "last_audit_result_id": null,
         "job": {
           "job_id": "...",
           "status": "running|pending|completed|failed|cancelled|cancelling",
           "progress": {
             "current": 42,
             "total": 100,
             "message": "Extracting colours: page 7 of 50",
             "stage": "Extracting colours: page 7 of 50",
             "fraction": 0.42
           }
         } | null
       }
    """
    db = get_db()
    pdf = db.get_pdf_document(pdf_document_id)
    if pdf is None:
        return jsonify({'error': 'pdf_not_found'}), 404

    denied = _require_pdf_role(pdf, *_READ_ROLES)
    if denied is not None:
        # `_require_pdf_role` returns a redirect / abort Response on
        # denial; re-emit as a JSON error so the polling JS doesn't have
        # to follow redirects.
        return jsonify({'error': 'forbidden'}), 403

    job_manager = JobManager.get_instance(db)
    # Prefer the most recent ACTIVE job (PENDING / RUNNING / CANCELLING)
    # for this document. Falling back to the absolute-most-recent record
    # would surface a stale completed job from a prior run, which the
    # polling JS would mis-read as "the current audit just finished" and
    # trigger an immediate reload-loop.
    active_statuses = [
        JobStatus.PENDING.value,
        JobStatus.RUNNING.value,
        JobStatus.CANCELLING.value,
    ]
    job_doc_raw = job_manager.collection.find_one(
        {
            'job_type': JobType.PDF_AUDIT.value,
            'metadata.pdf_document_id': pdf_document_id,
            'status': {'$in': active_statuses},
        },
        sort=[('created_at', -1)],
    )
    if job_doc_raw is None:
        # No active job — fall back to the most recent record so the
        # operator can still see what the last run looked like.
        job_doc_raw = job_manager.collection.find_one(
            {
                'job_type': JobType.PDF_AUDIT.value,
                'metadata.pdf_document_id': pdf_document_id,
            },
            sort=[('created_at', -1)],
        )

    job_payload: dict[str, Any] | None = None
    if job_doc_raw is not None:
        # JobManager.collection is Collection[dict[str, Any]] so
        # find_one's value is dict[str, Any] and every .get() is Any.
        # Read each leaf via Any-typed locals so pyright doesn't widen
        # nested dict types via isinstance narrowing.
        job_doc: Any = job_doc_raw
        progress_obj: Any = job_doc.get('progress') if hasattr(job_doc, 'get') else None
        details_obj: Any = (
            progress_obj.get('details')
            if hasattr(progress_obj, 'get')
            else None
        )
        job_payload = {
            'job_id': job_doc.get('job_id'),
            'status': job_doc.get('status'),
            'progress': {
                'current': progress_obj.get('current') if hasattr(progress_obj, 'get') else None,
                'total': progress_obj.get('total') if hasattr(progress_obj, 'get') else None,
                'message': progress_obj.get('message') if hasattr(progress_obj, 'get') else None,
                'stage': details_obj.get('stage') if hasattr(details_obj, 'get') else None,
                'fraction': details_obj.get('fraction') if hasattr(details_obj, 'get') else None,
            },
        }

    return jsonify({
        'doc_status': pdf.status.value,
        'error_reason': pdf.error_reason,
        'last_audit_result_id': pdf.last_audit_result_id,
        'job': job_payload,
    })


@pdf_bp.route('/pdfs/<pdf_document_id>/cancel', methods=['POST'])
@login_required
def cancel(pdf_document_id: str) -> Response:
    """Cancel an in-progress (or stale) audit and reset the document status.

    Looks up every PDF_AUDIT JobManager record for this document that is
    still PENDING / RUNNING / CANCELLING and requests cancellation. Then
    forcibly resets ``PdfDocument.status`` to AUDIT_FAILED with a "cancelled
    by user" reason — this clears stale AUDITING states left over after a
    process restart or worker crash, which the regular re-audit button
    would otherwise refuse to overwrite.

    If the worker thread is still alive, it may complete and overwrite
    the status to AUDITED — that's fine; the audit succeeded. The point
    of cancel is to unblock the user, not to forcibly stop a healthy run.
    """
    db = get_db()
    pdf = db.get_pdf_document(pdf_document_id)
    if pdf is None:
        flash(
            ftl('pdf-error-pdf-document-not-found', doc_id=pdf_document_id),
            'error',
        )
        return redirect(url_for('projects.list_projects'))

    denied = _require_pdf_role(pdf, *_WRITE_ROLES)
    if denied is not None:
        return denied

    job_manager = JobManager.get_instance(db)
    user_id_value = (
        current_user.get_id() if current_user.is_authenticated else None
    )
    user_id_str = (
        str(user_id_value) if user_id_value is not None else 'anonymous'
    )

    # Find every active PDF_AUDIT job for this document and request
    # cancellation. JobManager.collection.find returns raw Mongo dicts.
    active_statuses = [
        JobStatus.PENDING.value,
        JobStatus.RUNNING.value,
        JobStatus.CANCELLING.value,
    ]
    cancelled_count = 0
    for job_doc in job_manager.collection.find({
        'job_type': JobType.PDF_AUDIT.value,
        'metadata.pdf_document_id': pdf_document_id,
        'status': {'$in': active_statuses},
    }):
        job_id_value = job_doc.get('job_id')
        if not isinstance(job_id_value, str):
            continue
        if job_manager.request_cancellation(job_id_value, requested_by=user_id_str):
            cancelled_count += 1

    # Reset the doc itself so the user can re-trigger an audit. We mark
    # AUDIT_FAILED rather than reverting to PENDING so the failure is
    # visible in the list view and the operator knows nothing was saved.
    pdf.status = PdfDocumentStatus.AUDIT_FAILED
    pdf.error_reason = 'Audit cancelled by user'
    db.update_pdf_document(pdf)

    logger.info(
        'PDF audit cancellation requested for doc %s by %s '
        + '(%d active job record(s) flagged)',
        pdf_document_id, user_id_str, cancelled_count,
    )
    flash(ftl('pdf-audit-cancelled'), 'success')
    return redirect(url_for('pdf.detail', pdf_document_id=pdf_document_id))


@pdf_bp.route('/pdfs/<pdf_document_id>/file', methods=['GET'])
@login_required
def file(pdf_document_id: str) -> Response:
    """Stream the stored PDF bytes for inline iframe display."""
    db = get_db()
    pdf = db.get_pdf_document(pdf_document_id)
    if pdf is None:
        flash(
            ftl('pdf-error-pdf-document-not-found', doc_id=pdf_document_id),
            'error',
        )
        return redirect(url_for('projects.list_projects'))

    denied = _require_pdf_role(pdf, *_READ_ROLES)
    if denied is not None:
        return denied

    storage = _get_storage()
    pdf_path = storage.local_path(pdf)
    if not pdf_path.exists():
        flash(ftl('pdf-error-file-missing-on-disk'), 'error')
        return redirect(url_for('pdf.detail', pdf_document_id=pdf_document_id))

    download_name = pdf.original_filename or 'document.pdf'
    response = send_file(
        pdf_path,
        mimetype='application/pdf',
        as_attachment=False,
        download_name=download_name,
    )
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    # Explicit CSP so the global after_request hook leaves it alone and
    # the same-origin iframe embed is unambiguous to every browser.
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; frame-ancestors 'self'"
    )
    response.headers['Content-Disposition'] = (
        f'inline; filename="{download_name}"'
    )
    return response


@pdf_bp.route('/pdfs/<pdf_document_id>/images/<image_name>', methods=['GET'])
@login_required
def image(pdf_document_id: str, image_name: str) -> Response:
    """Stream an extracted image for the detail view."""
    db = get_db()
    pdf = db.get_pdf_document(pdf_document_id)
    if pdf is None:
        flash(
            ftl('pdf-error-pdf-document-not-found', doc_id=pdf_document_id),
            'error',
        )
        return redirect(url_for('projects.list_projects'))

    denied = _require_pdf_role(pdf, *_READ_ROLES)
    if denied is not None:
        return denied

    # Path safety: image_name must not escape the images directory.
    if '..' in image_name or '/' in image_name or '\\' in image_name:
        flash(ftl('pdf-error-invalid-image-name'), 'error')
        return redirect(url_for('pdf.detail', pdf_document_id=pdf_document_id))

    storage = _get_storage()
    image_path = storage.images_dir_for(pdf) / image_name
    if not image_path.exists():
        flash(ftl('pdf-error-image-missing'), 'error')
        return redirect(url_for('pdf.detail', pdf_document_id=pdf_document_id))

    return send_file(image_path, mimetype='image/png')


# Locale codes accepted by the export route's ``?locale=`` query string.
# Any other value falls back to the default at view-fn time.
_EXPORT_LOCALES = frozenset({'en', 'fr'})


@pdf_bp.route('/pdfs/<pdf_document_id>/export.<fmt>', methods=['GET'])
@login_required
def export(pdf_document_id: str, fmt: str) -> Response:
    """Stream a self-contained Markdown or HTML audit report.

    Mirrors the ``Save as Markdown`` / ``Save as HTML`` buttons in the
    pdfMax ``CheckerReport.tsx``. The generated file is fully
    standalone — no JS, no external CSS, and (for Markdown) no inline
    HTML except ``<span id="...">`` anchors used to make in-document
    links keep working when the file is opened in a browser or
    rendered by GitHub.

    The audit data is built straight from the latest persisted
    :class:`~auto_a11y.models.test_result.TestResult`, so an
    in-progress audit will export the previous result, not the
    partial state.

    ``fmt``: ``"md"`` or ``"html"``. Anything else returns 404.
    """
    if fmt not in ('md', 'html'):
        abort(404)

    db = get_db()
    pdf = db.get_pdf_document(pdf_document_id)
    if pdf is None:
        flash(
            ftl('pdf-error-pdf-document-not-found', doc_id=pdf_document_id),
            'error',
        )
        return redirect(url_for('projects.list_projects'))

    denied = _require_pdf_role(pdf, *_READ_ROLES)
    if denied is not None:
        return denied

    test_result = None
    if pdf.last_audit_result_id:
        test_result = db.get_test_result(pdf.last_audit_result_id)

    locale = request.args.get('locale', 'en')
    if locale not in _EXPORT_LOCALES:
        locale = 'en'

    # Local import — keeps ``report_export`` out of the route module's
    # import graph until an export is actually requested. Avoids
    # paying the Fluent-aware import cost on every PDF list/detail
    # view.
    from auto_a11y.pdf.report_export import (
        build_html_report,
        build_markdown_report,
    )

    base_name = (
        pdf.original_filename.removesuffix('.pdf')
        if pdf.original_filename and pdf.original_filename.lower().endswith('.pdf')
        else (pdf.original_filename or 'audit-report')
    )

    if fmt == 'md':
        body = build_markdown_report(pdf, test_result, locale=locale)
        mimetype = 'text/markdown; charset=utf-8'
        filename = f'{base_name}_accessibility_report.md'
    else:
        body = build_html_report(pdf, test_result, locale=locale)
        mimetype = 'text/html; charset=utf-8'
        filename = f'{base_name}_accessibility_report.html'

    response = Response(body.encode('utf-8'), mimetype=mimetype)
    response.headers['Content-Disposition'] = (
        f'attachment; filename="{filename}"'
    )
    # The export bytes are derived purely from the persisted audit
    # data; they don't depend on cookies or the current user. Marking
    # them ``private`` lets browsers cache locally without leaking the
    # bytes through a shared proxy.
    response.headers['Cache-Control'] = 'private, no-cache'
    return response


@pdf_bp.route('/pdfs/<pdf_document_id>/issue-map', methods=['GET'])
@login_required
def issue_map(pdf_document_id: str) -> Response:
    """Stream the cached pdfMax ``*_issue_map.json`` for the viewer overlays.

    pdfMax's audit subprocess writes one issue-map JSON per run alongside
    the Markdown report (see :meth:`PdfAuditJob._build_pdfmax_report_cache`).
    The Viewer JS fetches this to position issue overlays on each PDF
    page and to know which sidebar card to highlight on click.

    Returns ``404`` (not a flash + redirect) so the viewer JS can fall
    back to a "no overlays available — re-audit to enable" notice without
    crashing the page.
    """
    db = get_db()
    pdf = db.get_pdf_document(pdf_document_id)
    if pdf is None:
        abort(404)

    denied = _require_pdf_role(pdf, *_READ_ROLES)
    if denied is not None:
        return denied

    storage = _get_storage()
    pdf_path = storage.local_path(pdf)
    cache_dir = pdf_path.parent / 'pdfmax-report'

    if not cache_dir.is_dir():
        abort(404)

    candidates = sorted(cache_dir.glob('*_issue_map.json'))
    if not candidates:
        abort(404)

    response = send_file(candidates[0], mimetype='application/json')
    # The issue-map mirrors the audit's persisted state — safe to cache
    # in the user's browser within a session.
    response.headers['Cache-Control'] = 'private, max-age=60'
    return response


@pdf_bp.route('/pdfs/<pdf_document_id>/pdfmax-report', methods=['GET'])
@login_required
def pdfmax_report(pdf_document_id: str) -> Response | str:
    """Render the cached verbatim pdfMax Markdown audit report.

    The pdfMax subprocess is *not* run here. The audit job
    (:class:`PdfAuditJob`) writes a ``*_accessibility_report.md``
    file to disk as part of the normal Audit / Re-audit flow; this
    route is a pure cache lookup against that file.

    If no cached report exists (the PDF has never been audited, or
    was audited before this feature shipped), the page tells the
    user to click Re-audit. The route never blocks the request
    thread on a 10-30s audit.
    """
    db = get_db()
    pdf = db.get_pdf_document(pdf_document_id)
    if pdf is None:
        flash(
            ftl('pdf-error-pdf-document-not-found', doc_id=pdf_document_id),
            'error',
        )
        return redirect(url_for('projects.list_projects'))

    denied = _require_pdf_role(pdf, *_READ_ROLES)
    if denied is not None:
        return denied

    storage = _get_storage()
    pdf_path = storage.local_path(pdf)
    cache_dir = pdf_path.parent / 'pdfmax-report'

    cached_md: str | None = None
    error: str | None = None
    if cache_dir.is_dir():
        candidates = sorted(cache_dir.glob('*_accessibility_report.md'))
        if candidates:
            try:
                cached_md = candidates[0].read_text(encoding='utf-8')
            except OSError as exc:
                error = f"Failed to read cached pdfMax report: {exc}"
        else:
            error = (
                "No pdfMax report has been generated for this document yet. "
                "Click 'Re-audit' on the detail page to produce one."
            )
    else:
        error = (
            "No pdfMax report has been generated for this document yet. "
            "Click 'Re-audit' on the detail page to produce one."
        )

    return render_template(
        'pdf/pdfmax_report.html',
        pdf=pdf,
        target=target_view_from_pdf(pdf, breadcrumb=[]),
        report_markdown=cached_md,
        error=error,
    )


@pdf_bp.route(
    '/pdfs/<pdf_document_id>/pdfmax-report/images/<path:image_name>',
    methods=['GET'],
)
@login_required
def pdfmax_report_image(pdf_document_id: str, image_name: str) -> Response:
    """Stream an image referenced by pdfMax's verbatim Markdown report.

    The pdfMax audit writes ``image_*.png`` files alongside the .md
    and the markdown links to them with ``![](image_3.png)`` style
    relative paths. The viewer page's marked-rendered ``<img src>``
    attributes are rewritten client-side to point at this route so
    every image resolves to a real HTTP URL.
    """
    db = get_db()
    pdf = db.get_pdf_document(pdf_document_id)
    if pdf is None:
        abort(404)

    denied = _require_pdf_role(pdf, *_READ_ROLES)
    if denied is not None:
        return denied

    # Path safety: refuse anything that could traverse out of the
    # cache dir. ``image_name`` is a Werkzeug ``path`` converter so
    # subdirectories are possible; we strip any '..' component
    # entirely.
    if '..' in image_name.split('/') or '..' in image_name.split('\\'):
        abort(404)

    storage = _get_storage()
    pdf_path = storage.local_path(pdf)
    cache_dir = pdf_path.parent / 'pdfmax-report'
    image_path = (cache_dir / image_name).resolve()

    # Resolved path must remain inside cache_dir.
    try:
        image_path.relative_to(cache_dir.resolve())
    except ValueError:
        abort(404)

    if not image_path.is_file():
        abort(404)

    # Pick a mimetype from the suffix; default to octet-stream so a
    # malformed filename can't be interpreted as anything dangerous.
    suffix = image_path.suffix.lower()
    mimetype = {
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.gif': 'image/gif',
        '.svg': 'image/svg+xml',
        '.webp': 'image/webp',
    }.get(suffix, 'application/octet-stream')
    return send_file(image_path, mimetype=mimetype)


@pdf_bp.route('/pdfs/<pdf_document_id>/delete', methods=['POST'])
@login_required
def delete(pdf_document_id: str) -> Response:
    """Cascade-delete a PdfDocument (DB record + filesystem artefacts)."""
    db = get_db()
    pdf = db.get_pdf_document(pdf_document_id)
    if pdf is None:
        flash(
            ftl('pdf-error-pdf-document-not-found', doc_id=pdf_document_id),
            'error',
        )
        return redirect(url_for('projects.list_projects'))

    denied = _require_pdf_role(pdf, *_WRITE_ROLES)
    if denied is not None:
        return denied

    storage = _get_storage()
    storage.delete(pdf)
    db.delete_pdf_document(pdf_document_id)

    flash(ftl('pdf-deleted-success'), 'success')
    if pdf.project_id:
        return redirect(url_for('pdf.list_for_project', project_id=pdf.project_id))
    return redirect(url_for('projects.list_projects'))
