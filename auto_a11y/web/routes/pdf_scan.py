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
* POST ``/pdf-scan``                        — upload, start the audit, redirect
* GET  ``/pdf-scan/<scan_id>``              — results (Report | Viewer tabs)
* GET  ``/pdf-scan/<scan_id>/file``         — the PDF bytes, for the viewer
* GET  ``/pdf-scan/<scan_id>/issue-map``    — overlay payload, for the viewer
* GET  ``/pdf-scan/<scan_id>/images/<name>``— an extracted page image
* GET  ``/pdf-scan/<scan_id>/progress``     — how far the audit has got
* POST ``/pdf-scan/<scan_id>/cancel``       — stop a running audit
* POST ``/pdf-scan/<scan_id>/delete``       — discard a scan

Every route is owner-scoped: :func:`_load_owned_scan` resolves a scan only
within the current user's own directory, so one user's id is never a route
to another user's scan.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Literal, cast

from flask import (
    Blueprint, abort, flash, jsonify, redirect, render_template, request,
    send_file, url_for,
)
from flask_login import current_user, login_required
from werkzeug.wrappers import Response

from auto_a11y.core.task_runner import task_runner
from auto_a11y.pdf.audit.checks import ALL_CHECKS
from auto_a11y.pdf.audit.pipeline import run_audit
from auto_a11y.pdf.errors import CorruptPdf, NotAPdf, PdfTooLarge
from auto_a11y.pdf.fix.registry import FIX_REGISTRY
from auto_a11y.pdf.models import AIAnalysisResult, AIFinding, AuditResult
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
        ai_available=_claude_api_key() is not None,
    )


def _claude_api_key() -> str | None:
    """The Claude API key, or None if none is configured.

    Checked before the scan runs so the AI toggle can be disabled with an
    explanation rather than accepting the request and failing mid-audit.
    """
    cfg = get_app_config()
    key = getattr(cfg, 'CLAUDE_API_KEY', '') or os.environ.get('ANTHROPIC_API_KEY', '')
    return key or None


@pdf_scan_bp.route('/pdf-scan', methods=['POST'])
@login_required
def scan() -> Response:
    """Accept the upload, start the audit, and show the progress screen.

    The audit runs on a worker thread rather than in this request. It is
    minutes of work on a large PDF, and holding the request meant the
    user watched a bar that could only sweep — it had no way to know how
    far along anything was. Starting it here and answering with
    pdfMax's AuditingScreen lets the same progress callbacks the
    project-scoped path already uses drive a real bar.
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
    # The checkbox only reaches here when ticked; a configured key is still
    # required, so a stale form against a key-less deployment degrades to a
    # deterministic scan rather than erroring.
    api_key = _claude_api_key()
    run_ai = request.form.get('run_ai') == 'true' and api_key is not None

    locale = get_current_locale()
    store = _get_store()
    record = store.create(
        owner_user_id=_current_user_id(),
        pdf_bytes=pdf_bytes,
        original_filename=uploaded.filename or 'document.pdf',
        wcag_level=wcag_level,
        locale=locale,
        run_ai=run_ai,
    )

    task_runner.submit_task(
        func=run_scan_in_thread,
        task_id=f'pdf_scan_{record.scan_id}',
        kwargs={
            'store': store,
            'record': record,
            'api_key': api_key,
            'ai_model': getattr(cfg, 'CLAUDE_MODEL', None) or None,
        },
    )
    return redirect(url_for('pdf_scan.results', scan_id=record.scan_id))


#: Written to the manifest when the user cancels, so the results page can
#: tell "you stopped this" from "this broke".
CANCELLED_REASON = 'cancelled'


class _ScanCancelled(Exception):
    """Raised out of the progress callback to unwind a cancelled audit."""


def run_scan_in_thread(
    *,
    store: ScanStore,
    record: ScanRecord,
    api_key: str | None,
    ai_model: str | None,
) -> None:
    """Run one standalone audit on a worker thread.

    Everything this needs was resolved in the request that queued it —
    there is no application or request context here, so nothing may touch
    ``current_user``, ``request`` or :func:`ftl`. Failures are written to
    the manifest so the results page can explain them; nothing is raised
    into the pool.
    """
    def on_progress(stage: str, fraction: float) -> None:
        if store.cancel_requested(record.owner_user_id, record.scan_id):
            raise _ScanCancelled
        store.set_progress(record, fraction=fraction, step=stage)

    try:
        audit = run_audit(
            store.pdf_path(record.owner_user_id, record.scan_id),
            wcag_level=record.wcag_level,
            run_ai=record.run_ai,
            ai_api_key=api_key,
            ai_model=ai_model,
            locale=record.locale,
            images_out_dir=store.images_dir(record.owner_user_id, record.scan_id),
            progress=on_progress,
        )
    except _ScanCancelled:
        logger.info('Standalone scan %s cancelled', record.scan_id)
        store.fail(record, CANCELLED_REASON)
        return
    except CorruptPdf as exc:
        store.fail(record, f'Corrupt PDF: {exc}')
        return
    except Exception as exc:  # noqa: BLE001 — persist the failure, then stop
        logger.exception('Standalone scan %s failed', record.scan_id)
        store.fail(record, f'{type(exc).__name__}: {exc}')
        return

    store.set_progress(record, fraction=1.0, step='Done')
    store.finish(record, audit)


@pdf_scan_bp.route('/pdf-scan/<scan_id>/progress', methods=['GET'])
@login_required
def progress(scan_id: str) -> Response:
    """How far the audit has got — polled by the auditing screen.

    Returns the manifest status alongside the fraction so one request
    answers both "how far" and "is it over". A separate completion check
    could see a finished manifest and a stale progress file, and send the
    user to a report that is still being written.
    """
    record = _load_owned_scan(scan_id)
    fraction, step = _get_store().get_progress(
        record.owner_user_id, record.scan_id
    )
    return cast("Response", jsonify({
        'status': record.status,
        # Whole percent: the bar and its readout should agree, and the
        # readout is announced.
        'percent': int(round(fraction * 100)),
        'step': step,
        'done': record.status != 'auditing',
    }))


@pdf_scan_bp.route('/pdf-scan/<scan_id>/cancel', methods=['POST'])
@login_required
def cancel(scan_id: str) -> Response:
    """Ask a running audit to stop — pdfMax's Cancel button.

    The audit notices at its next progress tick, so this answers
    immediately and the scan settles a moment later. Answering only once
    the worker had actually stopped would hold the request for as long as
    the current stage takes, which is the thing being cancelled.
    """
    record = _load_owned_scan(scan_id)
    cancelling = record.status == 'auditing'
    if cancelling:
        _get_store().request_cancel(record.owner_user_id, record.scan_id)

    # A plain form post — the no-JavaScript path — needs somewhere to go;
    # answering it with a JSON body would leave the user looking at it.
    if 'application/json' not in (request.headers.get('Accept') or ''):
        return redirect(url_for('pdf_scan.results', scan_id=record.scan_id))
    return cast("Response", jsonify({'cancelling': cancelling}))


@pdf_scan_bp.route('/pdf-scan/<scan_id>', methods=['GET'])
@login_required
def results(scan_id: str) -> str:
    """The results view: pdfMax's Report and Viewer tab panels.

    While the audit is still running this is pdfMax's AuditingScreen
    instead — same URL, so the address the user landed on after
    uploading is the address of their report once it exists.
    """
    record = _load_owned_scan(scan_id)

    if record.status == 'auditing':
        fraction, step = _get_store().get_progress(
            record.owner_user_id, record.scan_id
        )
        return render_template(
            'pdf_scan/auditing.html',
            scan=record,
            percent=int(round(fraction * 100)),
            step=step,
        )

    report_markdown: str | None = None
    error: str | None = None

    if record.status == 'audit_failed':
        # A cancelled scan is not a failed one — the user stopped it, and
        # telling them their file broke something would be a lie.
        error = (
            str(ftl('pdf-scan-error-cancelled'))
            if record.error_reason == CANCELLED_REASON
            else record.error_reason or str(ftl('pdf-scan-error-failed'))
        )
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
                    ai_analysis=_ai_from_record(record),
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


def _ai_from_record(record: ScanRecord) -> AIAnalysisResult | None:
    """Rebuild the stored AI analysis so the report can render it.

    Returns ``None`` when AI was never requested. When it *was* requested
    but could not run, the stored ``model`` is a parenthesised marker and
    the summary carries the reason — both are preserved so the report says
    "AI did not run" instead of quietly omitting the section.
    """
    stored = record.ai_analysis
    if stored is None:
        return None

    findings: list[AIFinding] = []
    raw = stored.get('findings')
    if isinstance(raw, list):
        for item in cast("list[object]", raw):
            if not isinstance(item, dict):
                continue
            entry = cast("dict[str, Any]", item)
            severity = entry.get('severity')
            findings.append(AIFinding(
                category=str(entry.get('category') or ''),
                severity=(
                    severity if severity in ('high', 'medium', 'low', 'info')
                    else 'low'
                ),
                title=str(entry.get('title') or ''),
                description=str(entry.get('description') or ''),
                page=entry.get('page') if isinstance(entry.get('page'), int) else None,
                element_index=(
                    entry.get('element_index')
                    if isinstance(entry.get('element_index'), int) else None
                ),
            ))

    overall = stored.get('overall_severity')
    return AIAnalysisResult(
        findings=findings,
        executive_summary=str(stored.get('executive_summary') or ''),
        overall_severity=(
            overall if overall in ('high', 'medium', 'low', 'none') else 'none'
        ),
        model=str(stored.get('model') or ''),
        cached_input_tokens=_int_or_zero(stored.get('cached_input_tokens')),
        uncached_input_tokens=_int_or_zero(stored.get('uncached_input_tokens')),
        output_tokens=_int_or_zero(stored.get('output_tokens')),
    )


def _int_or_zero(value: object) -> int:
    return value if isinstance(value, int) else 0


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
