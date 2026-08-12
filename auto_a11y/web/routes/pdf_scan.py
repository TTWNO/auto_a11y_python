"""Standalone PDF scanning — pdfMax's own workflow, inside auto_a11y.

This is the "PDFs → Scan a PDF file" flow. A user drops a PDF in, the
shared audit engine runs, and the result comes back as pdfMax's own
Report / Viewer tabbed results view.

It shares the *engine* with the project-scoped PDF routes in
:mod:`auto_a11y.web.routes.pdf` — the same
:func:`~auto_a11y.pdf.audit.pipeline.run_audit`, the same checks, the same
fixes — and nothing else. A scan is not a
:class:`~auto_a11y.models.pdf_document.PdfDocument`: it has no website, no
project, no ``TestResult``, and no row in any collection. It lives in
:class:`~auto_a11y.pdf.scan_store.ScanStore` on disk, so scanning a file
cannot move any project's numbers.

Routes:

* GET  ``/pdf-scan``                        — the file-select screen
* POST ``/pdf-scan``                        — upload, audit, redirect to results
* GET  ``/pdf-scan/<scan_id>``              — results (Report | Viewer tabs)
* GET  ``/pdf-scan/<scan_id>/file``         — the PDF bytes, for the viewer
* GET  ``/pdf-scan/<scan_id>/issue-map``    — overlay payload, for the viewer
* GET  ``/pdf-scan/<scan_id>/images/<name>``— an extracted page image
* POST ``/pdf-scan/<scan_id>/delete``       — discard a scan

Every route is owner-scoped: :func:`_load_owned_scan` resolves a scan only
within the current user's own directory, so one user's id is never a route
to another user's scan.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from flask import (
    Blueprint, abort, flash, jsonify, redirect, render_template, request,
    send_file, url_for,
)
from flask_login import current_user, login_required
from werkzeug.wrappers import Response

from auto_a11y.pdf.audit.checks import ALL_CHECKS
from auto_a11y.pdf.audit.pipeline import run_audit
from auto_a11y.pdf.errors import CorruptPdf, NotAPdf, PdfTooLarge
from auto_a11y.pdf.fix.registry import FIX_REGISTRY
from auto_a11y.pdf.models import AuditResult
from auto_a11y.pdf.report_markdown import (
    checks_from_metadata,
    render_audit_markdown,
)
from auto_a11y.pdf.scan_store import ScanRecord, ScanStore
from auto_a11y.web.fluent import ftl, get_current_locale
from auto_a11y.web.typed_app import get_app_config

logger = logging.getLogger(__name__)
pdf_scan_bp = Blueprint('pdf_scan', __name__)


def _get_store() -> ScanStore:
    """Build a :class:`ScanStore` rooted under the configured PDF directory."""
    cfg = get_app_config()
    return ScanStore(base_dir=Path(cfg.PDF_STORAGE_DIR))


def _current_user_id() -> str:
    """The signed-in user's id as a string.

    Every route here is ``@login_required``, so an authenticated user is
    guaranteed; the empty fallback exists only to keep the type honest.
    """
    value = current_user.get_id() if current_user.is_authenticated else None
    return str(value) if value is not None else ''


def _load_owned_scan(scan_id: str) -> ScanRecord:
    """Fetch a scan belonging to the current user, or 404.

    Ownership is enforced by lookup, not by a comparison after the fact:
    the store is asked for ``(this user, this scan)``, so another user's
    scan is indistinguishable from one that does not exist. The explicit
    id check below is belt-and-braces against a manifest that was moved
    between directories.
    """
    record = _get_store().get(_current_user_id(), scan_id)
    if record is None or record.owner_user_id != _current_user_id():
        abort(404)
    return record


@pdf_scan_bp.route('/pdf-scan', methods=['GET'])
@login_required
def select_file() -> str:
    """Show the file-select screen — pdfMax's home screen.

    The subtitle's counts come from the registries themselves rather than
    a hardcoded string, so they cannot drift as checks and fixes land.
    """
    return render_template(
        'pdf_scan/select.html',
        recent_scans=_get_store().list_for_user(_current_user_id(), limit=10),
        check_count=len(ALL_CHECKS),
        fix_count=len(FIX_REGISTRY),
    )


@pdf_scan_bp.route('/pdf-scan', methods=['POST'])
@login_required
def scan() -> Response:
    """Accept the upload, run the audit, and redirect to the results.

    The audit runs inline. It is a CPU-bound pass over one file that the
    user is actively waiting on, and there is nothing useful to show
    between "sent" and "done" without the progress plumbing the
    project-scoped path uses — so this holds the request rather than
    pretending to be asynchronous. Wiring pdfMax's ``AuditingScreen`` and
    its progress stream in is a follow-up, not a change of shape.
    """
    uploaded = request.files.get('pdf_file')
    if uploaded is None or not uploaded.filename:
        flash(ftl('pdf-scan-error-no-file'), 'error')
        return redirect(url_for('pdf_scan.select_file'))

    pdf_bytes = uploaded.read()

    cfg = get_app_config()
    max_bytes = cfg.PDF_MAX_SIZE_MB * 1024 * 1024
    try:
        if not pdf_bytes.startswith(b'%PDF-'):
            raise NotAPdf('Bytes do not start with %PDF- magic header')
        if len(pdf_bytes) > max_bytes:
            raise PdfTooLarge(len(pdf_bytes), max_bytes)
    except NotAPdf:
        flash(ftl('pdf-error-not-a-pdf'), 'error')
        return redirect(url_for('pdf_scan.select_file'))
    except PdfTooLarge as exc:
        flash(
            ftl(
                'pdf-error-pdf-too-large',
                size=exc.size_bytes,
                limit=exc.limit_bytes,
            ),
            'error',
        )
        return redirect(url_for('pdf_scan.select_file'))

    wcag_level: Literal["AA", "AAA"] = (
        "AAA" if request.form.get('wcag_level') == 'AAA' else "AA"
    )
    locale = get_current_locale()
    store = _get_store()
    record = store.create(
        owner_user_id=_current_user_id(),
        pdf_bytes=pdf_bytes,
        original_filename=uploaded.filename or 'document.pdf',
        wcag_level=wcag_level,
        locale=locale,
    )

    try:
        audit = run_audit(
            store.pdf_path(record.owner_user_id, record.scan_id),
            wcag_level=wcag_level,
            run_ai=False,
            locale=locale,
            images_out_dir=store.images_dir(record.owner_user_id, record.scan_id),
        )
    except CorruptPdf as exc:
        store.fail(record, f'Corrupt PDF: {exc}')
        flash(ftl('pdf-scan-error-corrupt'), 'error')
        return redirect(url_for('pdf_scan.results', scan_id=record.scan_id))
    except Exception as exc:  # noqa: BLE001 — persist the failure, then surface it
        logger.exception('Standalone scan %s failed', record.scan_id)
        store.fail(record, f'{type(exc).__name__}: {exc}')
        flash(ftl('pdf-scan-error-failed'), 'error')
        return redirect(url_for('pdf_scan.results', scan_id=record.scan_id))

    store.finish(record, audit)
    return redirect(url_for('pdf_scan.results', scan_id=record.scan_id))


@pdf_scan_bp.route('/pdf-scan/<scan_id>', methods=['GET'])
@login_required
def results(scan_id: str) -> str:
    """The results view: pdfMax's Report and Viewer tab panels."""
    record = _load_owned_scan(scan_id)

    report_markdown: str | None = None
    error: str | None = None

    if record.status == 'audit_failed':
        error = record.error_reason or str(ftl('pdf-scan-error-failed'))
    elif record.status != 'audited':
        error = str(ftl('pdfmax-report-not-audited'))
    else:
        checks = checks_from_metadata(record.result.get('check_results'))
        if not checks:
            error = str(ftl('pdfmax-report-no-verdicts'))
        else:
            # Rendered per request, in the reader's current language,
            # rather than cached at scan time in whatever locale the
            # scan happened to run under.
            report_markdown = render_audit_markdown(
                AuditResult.from_checks(
                    pdf_path=Path(record.original_filename),
                    pdf_version=record.pdf_version,
                    page_count=record.page_count or 0,
                    declared_lang=record.declared_lang,
                    detected_lang=record.detected_lang,
                    check_results=checks,
                    ai_analysis=None,
                )
            )

    return render_template(
        'pdf_scan/results.html',
        scan=record,
        report_markdown=report_markdown,
        error=error,
        report_sections=record.report_sections,
        has_issue_map=record.issue_map is not None,
    )


@pdf_scan_bp.route('/pdf-scan/<scan_id>/file', methods=['GET'])
@login_required
def file(scan_id: str) -> Response:
    """Stream the scanned PDF for the viewer canvas."""
    record = _load_owned_scan(scan_id)
    path = _get_store().pdf_path(record.owner_user_id, record.scan_id)
    if not path.is_file():
        abort(404)
    return send_file(
        path,
        mimetype='application/pdf',
        as_attachment=False,
        download_name=record.original_filename,
    )


@pdf_scan_bp.route('/pdf-scan/<scan_id>/issue-map', methods=['GET'])
@login_required
def issue_map(scan_id: str) -> Response:
    """Serve the viewer's overlay payload for this scan.

    404s rather than redirecting, so the viewer falls back to its "no
    overlays available" notice instead of navigating the user away.
    """
    record = _load_owned_scan(scan_id)
    payload = record.issue_map
    if payload is None:
        abort(404)
    response = jsonify(payload)
    response.headers['Cache-Control'] = 'private, max-age=60'
    return response


@pdf_scan_bp.route('/pdf-scan/<scan_id>/images/<path:image_name>', methods=['GET'])
@login_required
def image(scan_id: str, image_name: str) -> Response:
    """Serve one extracted page image referenced by the report.

    ``image_name`` arrives from the report's own markup, but it still
    reaches the filesystem, so the resolved path is confined to the
    scan's own images directory before anything is opened.
    """
    record = _load_owned_scan(scan_id)
    images_dir = _get_store().images_dir(
        record.owner_user_id, record.scan_id
    ).resolve()
    candidate = (images_dir / image_name).resolve()
    if not candidate.is_relative_to(images_dir) or not candidate.is_file():
        abort(404)
    return send_file(candidate)


@pdf_scan_bp.route('/pdf-scan/<scan_id>/delete', methods=['POST'])
@login_required
def delete(scan_id: str) -> Response:
    """Discard a scan and its stored bytes."""
    record = _load_owned_scan(scan_id)
    _get_store().delete(record.owner_user_id, record.scan_id)
    flash(ftl('pdf-scan-deleted'), 'success')
    return redirect(url_for('pdf_scan.select_file'))
