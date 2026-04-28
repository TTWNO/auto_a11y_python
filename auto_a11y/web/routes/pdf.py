"""PDF document management routes.

Phase 9.3 of the pdfMax → auto_a11y integration. Nine routes per
:doc:`spec section 6 <../../../../docs/superpowers/specs/2026-04-24-pdf-audit-engine-port-design>`:

* GET  ``/projects/<project_id>/pdfs``                 — list scoped to a project
* GET  ``/websites/<website_id>/pdfs``                 — list scoped to a website
* GET  ``/projects/<project_id>/pdfs/add``             — upload form
* POST ``/projects/<project_id>/pdfs``                 — create (upload; URL deferred)
* GET  ``/pdfs/<pdf_document_id>``                     — detail page
* POST ``/pdfs/<pdf_document_id>/audit``               — re-audit
* GET  ``/pdfs/<pdf_document_id>/file``                — stream stored bytes
* GET  ``/pdfs/<pdf_document_id>/images/<image_name>`` — stream extracted image
* POST ``/pdfs/<pdf_document_id>/delete``              — cascade delete
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from flask import (
    Blueprint, flash, redirect, render_template, request, send_file, url_for,
)
from werkzeug.wrappers import Response

from auto_a11y.models.pdf_document import PdfDocumentStatus
from auto_a11y.pdf.errors import (
    CannotAuditFetchFailedDocument,
    GhostscriptMissing,
    NotAPdf,
    PdfTooLarge,
)
from auto_a11y.pdf.storage import PdfStorage
from auto_a11y.web.fluent import ftl
from auto_a11y.web.typed_app import get_app_config, get_db, get_pdf_runner
from auto_a11y.web.view_models.target import Crumb, target_view_from_pdf

logger = logging.getLogger(__name__)
pdf_bp = Blueprint('pdf', __name__)


def _get_storage() -> PdfStorage:
    """Build a :class:`PdfStorage` configured from app config."""
    cfg = get_app_config()
    return PdfStorage(base_dir=Path(cfg.PDF_STORAGE_DIR))


@pdf_bp.route('/projects/<project_id>/pdfs', methods=['GET'])
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
                discovered_from_user_id=None,
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

    # Path B: manual URL — deferred to a future task (the URL fetch
    # pipeline is in the spec but not wired into the runner yet).
    flash(ftl('pdf-error-url-fetch-not-implemented'), 'error')
    return redirect(url_for('pdf.add_form', project_id=project_id))


@pdf_bp.route('/pdfs/<pdf_document_id>', methods=['GET'])
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

    return render_template(
        'pdf/detail.html',
        pdf=pdf,
        target=target,
        test_result=test_result,
        project=project,
        website=website,
    )


@pdf_bp.route('/pdfs/<pdf_document_id>/audit', methods=['POST'])
def audit(pdf_document_id: str) -> Response:
    """Re-audit an existing PDF document."""
    db = get_db()
    pdf = db.get_pdf_document(pdf_document_id)
    if pdf is None:
        flash(
            ftl('pdf-error-pdf-document-not-found', doc_id=pdf_document_id),
            'error',
        )
        return redirect(url_for('projects.list_projects'))

    if pdf.status == PdfDocumentStatus.AUDITING:
        flash(ftl('pdf-audit-already-running'), 'warning')
        return redirect(url_for('pdf.detail', pdf_document_id=pdf_document_id))

    runner = get_pdf_runner()
    if runner is None:
        flash(ftl('pdf-error-pdf-runner-not-configured'), 'error')
        return redirect(url_for('pdf.detail', pdf_document_id=pdf_document_id))

    try:
        asyncio.run(runner.audit_pdf_document(
            pdf_document_id,
            run_ai=False,
            ai_api_key=None,
            wcag_level='AA',
            locale='en',
        ))
    except CannotAuditFetchFailedDocument:
        flash(ftl('pdf-error-cannot-audit-fetch-failed-document'), 'error')
        return redirect(url_for('pdf.detail', pdf_document_id=pdf_document_id))
    except GhostscriptMissing as exc:
        flash(
            ftl('pdf-error-ghostscript-missing', searched=', '.join(exc.searched)),
            'error',
        )
        return redirect(url_for('pdf.detail', pdf_document_id=pdf_document_id))
    except Exception as exc:  # noqa: BLE001 — surface any other failure as a flash
        logger.error(f"PDF audit failed: {exc}")
        flash(ftl('pdf-error-generic', reason=str(exc)), 'error')
        return redirect(url_for('pdf.detail', pdf_document_id=pdf_document_id))

    flash(ftl('pdf-audit-queued'), 'success')
    return redirect(url_for('pdf.detail', pdf_document_id=pdf_document_id))


@pdf_bp.route('/pdfs/<pdf_document_id>/file', methods=['GET'])
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
    response.headers['Content-Disposition'] = (
        f'inline; filename="{download_name}"'
    )
    return response


@pdf_bp.route('/pdfs/<pdf_document_id>/images/<image_name>', methods=['GET'])
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


@pdf_bp.route('/pdfs/<pdf_document_id>/delete', methods=['POST'])
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

    storage = _get_storage()
    storage.delete(pdf)
    db.delete_pdf_document(pdf_document_id)

    flash(ftl('pdf-deleted-success'), 'success')
    if pdf.project_id:
        return redirect(url_for('pdf.list_for_project', project_id=pdf.project_id))
    return redirect(url_for('projects.list_projects'))
