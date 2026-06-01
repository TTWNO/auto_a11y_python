"""
Report generation routes
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from flask import Blueprint, Flask, Response, abort, render_template, request, jsonify, send_file, current_app, url_for, redirect, session
from flask_login import current_user, login_required
from werkzeug.exceptions import HTTPException
from werkzeug.wrappers import Response as WerkzeugResponse
from auto_a11y.web.fluent import ftl, force_locale, get_current_locale as get_locale
from auto_a11y.models import PageStatus
from auto_a11y.models.app_user import UserRole
from auto_a11y.web.routes.auth import get_effective_role, project_role_required
from auto_a11y.reporting import ReportGenerator, PageStructureReport
from auto_a11y.reporting.discovery_report import DiscoveryReportGenerator
from auto_a11y.reporting.static_html_generator import StaticHTMLReportGenerator
from auto_a11y.core.job_manager import JobManager, JobType, JobStatus
from auto_a11y.core.task_runner import task_runner
from auto_a11y.core.report_job import ReportJob
from auto_a11y.web.typed_app import get_db, get_app_config
from datetime import datetime, timedelta
from uuid import uuid4
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_real_app() -> Flask:
    """Get the real Flask app from the current_app proxy (for background threads)."""
    # LocalProxy._get_current_object() isn't in Flask's stubs; use getattr to avoid
    # attr-defined errors while keeping the runtime behaviour intact.
    getter = getattr(current_app, '_get_current_object')
    app: Flask = getter()
    return app
reports_bp = Blueprint('reports', __name__)


def _json_body() -> dict[str, Any]:
    """Return the request's JSON body as a dict, or ``{}`` if absent/invalid.

    ``request.json`` raises (415/400 -> 500 inside a broad except) when the
    request has no JSON body or the body is not valid JSON. ``get_json(silent=
    True)`` returns ``None`` instead; we additionally coerce a non-dict body
    (e.g. a bare JSON list/number) to ``{}`` so callers can ``.get(...)``
    safely. Annotated to a concrete dict so the strict type-checkers don't see
    an ``Any``-tainted ``.get``.
    """
    raw: object = request.get_json(silent=True)
    if not isinstance(raw, dict):
        return {}
    # ``raw`` narrows to ``dict[Unknown, Unknown]``; a parsed JSON object always
    # has str keys, so cast to the concrete element type (not ``Any``) to shed
    # the Unknowns under strict checking. ``api.py`` uses the same pattern.
    return cast("dict[str, Any]", raw)


def _body_format(default: str = 'html') -> str:
    """Read ``format`` from form data or JSON body without exploding on a
    missing/invalid JSON body. Form data wins when present (matches the legacy
    precedence ``request.form.get('format', ...)``).
    """
    body = _json_body()
    fmt: object = request.form.get('format') or body.get('format', default)
    return str(fmt)


def _authorize_body_scope(
    project_id: str | None,
    website_id: str | None,
    *roles: UserRole,
) -> None:
    """In-handler scope authorization for routes that take their scope ids from
    the request BODY (form/JSON) rather than a resolvable URL param.

    Mirrors ``project_role_required`` / ``_authorize_report_record``:

    * superadmin -> always allowed.
    * a project- or website-scoped request -> the effective role on that scope
      must be one of ``roles`` (default ADMIN/AUDITOR/CLIENT).
    * an "all projects" roll-up (no project_id and no website_id) -> superadmin
      only, since it spans every project.

    Aborts 403 otherwise. Raised as a real ``HTTPException`` so callers that
    wrap generation in ``except Exception`` must re-raise it (see the
    ``except HTTPException: raise`` guards) rather than swallowing it into a
    500.
    """
    if getattr(current_user, 'is_superadmin', False):
        return

    if not project_id and not website_id:
        # Cross-project roll-up: only a superadmin may span every project.
        abort(403)

    effective_role = get_effective_role(
        current_user,
        request,
        project_id=project_id,
        website_id=website_id if not project_id else None,
    )
    allowed = roles or (UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT)
    if effective_role not in allowed:
        abort(403)


def _authorize_job_record(job: dict[str, Any], *roles: UserRole) -> None:
    """Authorize access to a report job by the scope on its record.

    The ``<job_id>`` URL param isn't resolvable by the central resolver (the
    job id is not a project/website/page id), so we read the scope the job was
    created with off the record and gate on the effective role there. The scope
    can be carried three ways, mirroring ``api.py``'s ``_authorize_report_record``
    so the two stay consistent:

    * top-level ``project_id`` -> check that project;
    * top-level ``website_id`` -> check that website (resolved to its project);
    * ``metadata.page_id`` (PAGE-scoped jobs, which carry neither top-level id)
      -> resolve the page to its ``website_id`` and check THAT. Without this
      hop a page job would fall through to the "all projects" roll-up branch
      and 403 the ADMIN/AUDITOR/CLIENT who legitimately made it (the bug this
      fixes).

    Jobs with no scope at all are cross-project ("all projects") roll-ups and
    require superadmin, exactly like ``_authorize_report_record`` /
    ``_authorize_body_scope``. Fail-closed: a ``page_id`` that no longer
    resolves (page deleted) falls back to whatever top-level scope remains,
    which for a page job is none -> superadmin only.
    """
    project_id_any: Any = job.get('project_id')
    website_id_any: Any = job.get('website_id')
    metadata_any: Any = job.get('metadata') or {}
    # ``isinstance`` narrows to ``dict[Unknown, Unknown]`` under strict
    # checking; a job's metadata is always a str-keyed JSON object, so cast to
    # the concrete element type (not ``Any`` as a workaround) -- the same
    # pattern ``_json_body`` and api.py's ``_authorize_report_record`` use.
    metadata: dict[str, Any] = (
        cast("dict[str, Any]", metadata_any) if isinstance(metadata_any, dict) else {}
    )
    page_id_any: Any = metadata.get('page_id')

    project_id = project_id_any if isinstance(project_id_any, str) else None
    website_id = website_id_any if isinstance(website_id_any, str) else None
    page_id = page_id_any if isinstance(page_id_any, str) else None

    # PAGE-scoped jobs carry the scope as metadata.page_id. Resolve it to the
    # owning website so the scope check below has something to gate on; if the
    # page is gone, leave website_id as-is (None for a page job -> fail-closed
    # to the superadmin roll-up branch in _authorize_body_scope).
    if page_id and not website_id and not project_id:
        page = get_db().get_page(page_id)
        if page is not None:
            website_id = page.website_id

    _authorize_body_scope(project_id, website_id, *roles)


@reports_bp.route('/dashboard')
@login_required
def reports_dashboard() -> str:
    """Reports dashboard"""
    # Get available reports
    reports_dir = get_app_config().REPORTS_DIR
    report_files = list(reports_dir.glob('*.xlsx')) + list(reports_dir.glob('*.html')) + list(reports_dir.glob('*.json')) + list(reports_dir.glob('*.pdf')) + list(reports_dir.glob('*.zip')) + list(reports_dir.glob('*.csv'))
    
    reports: list[dict[str, Any]] = []
    for file in sorted(report_files, key=lambda x: x.stat().st_mtime, reverse=True)[:20]:
        # Extract project name from filename if available
        filename_parts = file.stem.split('_')
        if len(filename_parts) > 1 and not filename_parts[0].isdigit():
            # Likely a project name, use it
            display_name = ' '.join(filename_parts[:-1]).replace('_', ' ').title()
        else:
            display_name = file.stem.replace('_', ' ').title()
            
        # Try to determine project name from filename
        project_name = None
        if 'all_projects' in file.stem.lower():
            project_name = 'All Projects'
        elif '_' in file.stem:
            # Try to extract project name from filename pattern
            name_parts = file.stem.split('_')
            if len(name_parts) > 2:
                # Skip timestamp parts at the end
                project_name = ' '.join(name_parts[:-2]).replace('_', ' ').title()
        
        reports.append({
            'filename': file.name,
            'name': display_name,
            'size_kb': file.stat().st_size,  # Pass bytes, template uses filesizeformat
            'created_at': datetime.fromtimestamp(file.stat().st_mtime),
            'type': file.suffix[1:].lower(),
            'project_name': project_name
        })
    
    # Get all projects for the dropdown
    projects = get_db().get_projects()
    
    # Get all websites for the dropdown
    websites: list[dict[str, Any]] = []
    for project in projects:
        if not project.id:
            continue
        project_websites = get_db().get_websites(project.id)
        for website in project_websites:
            websites.append({
                'id': str(website.id),
                'name': website.name,
                'project_id': str(project.id),
                'project_name': project.name
            })
    
    # Get active report generation jobs
    job_manager = JobManager(get_db())
    active_jobs = job_manager.get_active_jobs(job_type=JobType.REPORT_GENERATION)

    # Also get recently completed jobs (last 5 minutes)
    recently_completed = list(job_manager.collection.find({
        'job_type': JobType.REPORT_GENERATION.value,
        'status': {'$in': ['completed', 'failed']},
        'completed_at': {'$gte': datetime.now() - timedelta(minutes=5)}
    }).sort('completed_at', -1))

    return render_template('reports/dashboard.html',
                         reports=reports,
                         projects=projects,
                         websites=websites,
                         active_jobs=active_jobs,
                         recently_completed=recently_completed)


@reports_bp.route('/generate', methods=['POST'])
@login_required
def generate_report() -> tuple[Response, int] | Response:
    """Generate accessibility report (background job)"""
    data = _json_body()
    project_id_any: object = data.get('project_id')
    website_id_any: object = data.get('website_id')
    project_id = project_id_any if isinstance(project_id_any, str) else None
    website_id = website_id_any if isinstance(website_id_any, str) else None
    report_type_any: object = data.get('type', 'xlsx')
    report_type = report_type_any if isinstance(report_type_any, str) else 'xlsx'

    # Scope ids come from the JSON body, not a resolvable URL param, so the
    # decorator can't gate this; authorize the resolved scope in-handler.
    # An "all projects" roll-up (no project_id/website_id) requires superadmin.
    _authorize_body_scope(project_id, website_id)

    scope = 'all'
    scope_id = None
    display_name = 'All Projects Report'

    if project_id:
        project = get_db().get_project(project_id)
        if not project:
            return jsonify({'error': 'Project not found'}), 404
        scope = 'project'
        scope_id = project_id
        display_name = f'Accessibility Report - {project.name}'
    elif website_id:
        website = get_db().get_website(website_id)
        if not website:
            return jsonify({'error': 'Website not found'}), 404
        scope = 'website'
        scope_id = website_id
        display_name = f'Accessibility Report - {website.name}'

    # Capture Flask context into local variables
    db = get_db()
    config = get_app_config().__dict__.copy()
    language = str(get_locale()) if get_locale() else 'en'
    app = _get_real_app()

    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        project_id=str(scope_id) if scope == 'project' else None,
        website_id=str(scope_id) if scope == 'website' else None,
        metadata={'report_type': report_type, 'scope': scope, 'display_name': display_name}
    )

    def wrapper() -> None:
        try:
            with app.app_context(), force_locale(language):
                from auto_a11y.reporting import ReportGenerator
                generator = ReportGenerator(db, config, language=language)
                format_map = {'excel': 'xlsx'}
                fmt = format_map.get(report_type, report_type)
                func: Callable[..., Any]
                kwargs: dict[str, Any]
                if scope == 'all':
                    func = generator.generate_all_projects_report
                    kwargs = {'format': fmt}
                elif scope == 'project':
                    func = generator.generate_project_report
                    kwargs = {'project_id': scope_id, 'format': fmt}
                else:
                    func = generator.generate_website_report
                    kwargs = {'website_id': scope_id, 'format': fmt}
                job = ReportJob(job_id, job_manager, func, generator_kwargs=kwargs)
                job.run()
        except Exception as e:
            logger.error(f"Report job {job_id} wrapper failed: {e}", exc_info=True)
            try:
                job_manager.update_job_status(job_id, JobStatus.FAILED, error=str(e))
            except Exception:
                pass

    task_runner.submit_task(func=wrapper, task_id=job_id)
    return jsonify({'success': True, 'job_id': job_id, 'message': 'Report generation started'})


@reports_bp.route('/job/<job_id>/status')
@login_required
def job_status(job_id: str) -> tuple[Response, int] | Response:
    """Get report job status"""
    job_manager = JobManager(get_db())
    job = job_manager.get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    # job_id isn't resolvable by the central resolver; authorize by the scope
    # recorded on the job (read tier: ADMIN/AUDITOR/CLIENT).
    _authorize_job_record(job, UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT)
    response = {
        'job_id': job['job_id'],
        'status': job['status'],
        'progress': job.get('progress', {}),
        'result': job.get('result'),
        'error': job.get('error'),
        'metadata': job.get('metadata', {}),
        'updated_at': job['updated_at'].isoformat() if job.get('updated_at') else None
    }
    return jsonify(response)


@reports_bp.route('/job/<job_id>/drop', methods=['POST'])
@login_required
def drop_job(job_id: str) -> tuple[Response, int] | Response:
    """Drop/cancel an in-progress report job.

    The doc is NOT deleted here: the worker thread polls
    ``is_cancellation_requested(job_id)`` to break out of generation, so
    the doc must outlive this request. Once the worker observes the flag
    it raises ``ReportCancelled`` and writes back ``CANCELLED``; the
    JobManager TTL/cleanup task removes the doc later. Deleting it
    eagerly (the previous behaviour) made the worker keep generating —
    the cancel flag was unreachable.
    """
    try:
        job_manager = JobManager(get_db())
        job = job_manager.get_job(job_id)
        if not job:
            # Idempotent: already gone is success from the user's view.
            return jsonify({'success': True, 'already_gone': True})

        # Dropping a job is a mutation -> ADMIN/AUDITOR on the job's scope.
        _authorize_job_record(job, UserRole.ADMIN, UserRole.AUDITOR)

        if job.get('status') in (JobStatus.PENDING.value, JobStatus.RUNNING.value):
            job_manager.request_cancellation(job_id)

        return jsonify({'success': True})
    except HTTPException:
        # Re-raise authz/HTTP aborts so the generic handler below doesn't
        # swallow a 403/404 into a 500.
        raise
    except Exception as e:
        logger.error(f"Error dropping job {job_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@reports_bp.route('/job/<job_id>/restart', methods=['POST'])
@login_required
def restart_job(job_id: str) -> tuple[Response, int] | Response:
    """Restart a stalled report job from scratch"""
    job_manager = JobManager(get_db())
    old_job = job_manager.get_job(job_id)
    if not old_job:
        return jsonify({'error': 'Job not found'}), 404

    # Restarting re-runs generation -> mutation: ADMIN/AUDITOR on job scope.
    _authorize_job_record(old_job, UserRole.ADMIN, UserRole.AUDITOR)

    metadata = old_job.get('metadata', {})
    scope = metadata.get('scope')
    report_type = metadata.get('report_type', 'xlsx')
    display_name = metadata.get('display_name', 'Report')
    project_id = old_job.get('project_id')
    website_id = old_job.get('website_id')

    # Request cancellation of the old job (don't delete the doc; the
    # worker thread reads the cancellation flag from it). See drop_job.
    if old_job.get('status') in (JobStatus.PENDING.value, JobStatus.RUNNING.value):
        job_manager.request_cancellation(job_id)

    # Capture context for background thread
    db = get_db()
    config = get_app_config().__dict__.copy()
    language = str(get_locale()) if get_locale() else 'en'
    app = _get_real_app()
    output_dir = get_app_config().REPORTS_DIR

    new_job_id = f"report_{uuid4().hex[:8]}"
    job_manager.create_job(
        job_id=new_job_id,
        job_type=JobType.REPORT_GENERATION,
        project_id=project_id,
        website_id=website_id,
        metadata=metadata
    )

    def wrapper() -> None:
        try:
            with app.app_context(), force_locale(language):
                func, kwargs = _build_restart_generator(
                    scope, report_type, project_id, website_id,
                    db, config, language, output_dir
                )
                job = ReportJob(new_job_id, job_manager, func, generator_kwargs=kwargs)
                job.run()
        except Exception as e:
            logger.error(f"Report job {new_job_id} wrapper failed: {e}", exc_info=True)
            try:
                job_manager.update_job_status(new_job_id, JobStatus.FAILED, error=str(e))
            except Exception:
                pass

    task_runner.submit_task(func=wrapper, task_id=new_job_id)
    return jsonify({'success': True, 'job_id': new_job_id, 'display_name': display_name})


def _build_restart_generator(scope: str | None, report_type: str, project_id: str | None, website_id: str | None, db: Any, config: dict[str, Any], language: str, output_dir: Path) -> tuple[Callable[..., Any], dict[str, Any]]:
    """
    Build generator function and kwargs for restarting a report job.
    Must be called inside a Flask app context.

    Returns:
        (func, kwargs) -- func is the generator callable, kwargs are passed
        to it by ReportJob (which also injects progress_callback).
    """
    if scope in ('all', 'project', 'website'):
        from auto_a11y.reporting import ReportGenerator
        generator = ReportGenerator(db, config, language=language)
        fmt_map = {'excel': 'xlsx'}
        fmt = fmt_map.get(report_type, report_type)
        if scope == 'all':
            return generator.generate_all_projects_report, {'format': fmt}
        elif scope == 'project':
            return generator.generate_project_report, {'project_id': project_id, 'format': fmt}
        else:
            return generator.generate_website_report, {'website_id': website_id, 'format': fmt}

    elif scope == 'page_structure':
        website = db.get_website(website_id)
        pages = db.get_pages(website_id)
        project = db.get_project(website.project_id) if website and website.project_id else None

        def generate_and_save(progress_callback: Callable[..., Any] | None = None) -> Any:
            report = PageStructureReport(db, website, pages, project, language=language)
            report.generate(progress_callback=progress_callback)
            return report.save(report_type)
        return generate_and_save, {}

    elif scope == 'discovery_website':
        disco_gen = DiscoveryReportGenerator(db, config, language=language)
        return disco_gen.generate_website_discovery_report, {'website_id': website_id, 'format': report_type}

    elif scope == 'discovery_project':
        disco_gen2 = DiscoveryReportGenerator(db, config, language=language)
        return disco_gen2.generate_project_discovery_report, {'project_id': project_id, 'format': report_type}

    elif scope == 'static_html':
        page_ids: list[str] = []
        project_name = "Accessibility Report"
        website_url = None

        if project_id:
            project = db.get_project(project_id)
            if project:
                project_name = project.name
            websites = db.get_websites(project_id)
            for w in websites:
                pages = db.get_pages(w.id)
                page_ids.extend([str(p.id) for p in pages if p.status == PageStatus.TESTED])
                if not website_url and w.url:
                    website_url = w.url
        elif website_id:
            website = db.get_website(website_id)
            if website:
                if website.project_id:
                    project = db.get_project(website.project_id)
                    project_name = f"{project.name} - {website.name}" if project else website.name
                else:
                    project_name = website.name
                website_url = website.url
            pages = db.get_pages(website_id)
            page_ids = [str(p.id) for p in pages if p.status == PageStatus.TESTED]
        else:
            projects = db.get_projects()
            project_name = "All Projects Accessibility Report"
            for proj in projects:
                websites = db.get_websites(proj.id)
                for w in websites:
                    pages = db.get_pages(w.id)
                    page_ids.extend([str(p.id) for p in pages if p.status == PageStatus.TESTED])

        if not page_ids:
            raise ValueError('No tested pages found to generate report')

        touchpoints_tested = None
        first_result = db.get_latest_test_result(page_ids[0])
        if first_result and first_result.violations:
            touchpoints_tested = sorted(set(
                v.touchpoint for v in first_result.violations if v.touchpoint
            ))

        static_gen = StaticHTMLReportGenerator(db, output_dir=output_dir, language=language)

        def generate_static(progress_callback: Callable[..., Any] | None = None) -> Any:
            return static_gen.generate_report(
                page_ids=page_ids,
                project_name=project_name,
                website_url=website_url,
                wcag_level='AA',
                touchpoints_tested=touchpoints_tested,
                include_screenshots=True,
                include_discovery=True,
                ai_tests_enabled=True,
                progress_callback=progress_callback
            )
        return generate_static, {}

    elif scope == 'deduplicated':
        dedup_gen = StaticHTMLReportGenerator(db, output_dir=output_dir, language=language)

        def generate_dedup(progress_callback: Callable[..., Any] | None = None) -> Any:
            return dedup_gen.generate_project_deduplicated_report(
                project_id=project_id,
                website_id=website_id,
                progress_callback=progress_callback
            )
        return generate_dedup, {}

    elif scope == 'recordings':
        from auto_a11y.reporting.recordings_report import RecordingsReportGenerator
        rec_gen = RecordingsReportGenerator(db, config, language=language)
        return rec_gen.generate_project_recordings_report, {
            'project_id': project_id,
            'format': report_type,
            'language': language
        }

    elif scope == 'page':
        raise ValueError('Single page reports cannot be restarted (page ID not stored in job)')

    else:
        raise ValueError(f'Unknown report scope: {scope}')


def _safe_report_path(filename: str) -> Path | None:
    """Resolve ``filename`` strictly inside ``REPORTS_DIR``.

    THE SECURITY BOUNDARY IS PATH CONTAINMENT, NOT ``secure_filename``: we
    ``resolve()`` the candidate and verify it is relative to the resolved
    reports dir, which by itself rejects ``..`` traversal, absolute paths, and
    embedded separators after resolution. CONTAINMENT IS CHECKED BEFORE
    EXISTENCE so a traversing/absolute filename is refused without ever probing
    the filesystem outside the reports directory (the previous order did
    ``.exists()`` first, which leaks whether an out-of-tree file exists and
    could stream it).

    We deliberately do NOT run the name through ``secure_filename`` and reject
    on inequality: ``secure_filename`` transliterates non-ASCII (e.g. ``Café``
    -> ``Cafe``), but report files are named via
    ``report_generator._sanitize_filename`` which only strips ``<>:"/\\|?*``
    and collapses whitespace -- it preserves accented characters. So a project
    named ``Café Réseau`` yields the on-disk file
    ``website_Café_Réseau_<ts>.xlsx``; a ``secure_filename`` equality gate
    would 403 that legitimate download/delete. This is a bilingual (EN/FR)
    product, so accented names are expected and MUST be allowed.

    As a cheap pre-resolve reject we still refuse any value carrying a path
    separator (``/`` or the OS separator) or that IS a relative-traversal
    segment (``.`` / ``..``) -- a fast-path defence only; the containment check
    below is the real guarantee. We do NOT reject on a ``..`` substring: a
    legitimate report name may contain consecutive dots (``A..B.xlsx``), and
    with separators already excluded such a name cannot traverse out of the
    reports dir -- ``resolve()`` keeps it inside.

    Returns the safe ``Path`` if it is inside the reports dir, else ``None``
    (caller maps ``None`` -> 403/404).
    """
    # Cheap pre-resolve reject for obvious traversal/separators. Non-ASCII is
    # NOT rejected here (see docstring) -- only structural path characters.
    if (
        not filename
        or filename in {'.', '..'}
        or '/' in filename
        or os.sep in filename
        or (os.altsep is not None and os.altsep in filename)
    ):
        return None

    reports_dir = Path(get_app_config().REPORTS_DIR).resolve()

    candidate = (reports_dir / filename).resolve()
    # Containment check (the real security boundary) BEFORE any existence probe.
    if not candidate.is_relative_to(reports_dir):
        return None
    return candidate


@reports_bp.route('/download/<filename>')
@login_required
def download_report(filename: str) -> tuple[Response, int] | Response | WerkzeugResponse:
    """Download generated report.

    AUTHORIZATION GAP (documented): reports on disk are keyed only by
    ``filename`` with no per-file scope record to authorize against (the API
    surface ``/api/.../reports/<report_id>/file`` keys on a JobManager record
    and DOES scope-check via ``_authorize_report_record`` -- this filesystem
    route has no such record). We therefore gate it with ``@login_required``
    plus strict path-traversal containment only; any authenticated user can
    download any report file. Tightening this to per-report scope requires
    persisting a scope alongside each generated file (future work).
    """
    file_path = _safe_report_path(filename)
    if file_path is None:
        # Out-of-tree / traversing name -- refuse WITHOUT touching the FS.
        return jsonify({'error': 'Invalid file path'}), 403

    if not file_path.exists():
        return jsonify({'error': 'Report not found'}), 404

    return send_file(
        file_path,
        as_attachment=True,
        download_name=file_path.name
    )


@reports_bp.route('/<filename>/delete', methods=['POST'])
@login_required
def delete_report(filename: str) -> tuple[Response, int] | Response:
    """Delete a generated report.

    AUTHORIZATION GAP (documented): like ``download_report``, on-disk reports
    carry no per-file scope record, so we cannot mirror the API's
    ``_authorize_report_mutation`` ADMIN/AUDITOR scope check here. Gated with
    ``@login_required`` plus strict path-traversal containment; tightening to
    per-report ADMIN scope requires persisting a scope per file (future work).
    """
    file_path = _safe_report_path(filename)
    if file_path is None:
        # Out-of-tree / traversing name -- refuse WITHOUT touching the FS.
        return jsonify({'error': 'Invalid file path'}), 403

    if not file_path.exists():
        return jsonify({'error': 'Report not found'}), 404

    try:
        file_path.unlink()
        return jsonify({'success': True})
    except OSError as e:
        logger.error(f"Failed to delete report {filename}: {e}")
        return jsonify({'error': 'Failed to delete report'}), 500


@reports_bp.route('/project/<project_id>/summary')
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT)
def project_summary(project_id: str) -> Response | WerkzeugResponse:
    """Project summary -- redirects to the project report page."""
    return redirect(url_for('projects.generate_project_report', project_id=project_id))


@reports_bp.route('/export-csv', methods=['POST'])
@login_required
def export_csv() -> Response:
    """Export data as CSV.

    NOTE: this is currently an unimplemented stub — it returns a canned
    ``download_url`` and does NOT yet read any project/website/page from the
    body, so there is no scope to authorize against. It is gated with
    ``@login_required`` only; when real CSV generation lands here it MUST
    resolve the requested scope and run ``_authorize_body_scope`` (or a
    ``project_role_required`` decorator if the scope moves to a URL param)
    before emitting any data.
    """
    data = _json_body()

    _export_type: object = data.get('type')  # violations, pages, summary
    _filters: object = data.get('filters', {})
    
    # Generate CSV based on type
    # This would be implemented with actual CSV generation
    
    return jsonify({
        'success': True,
        'message': 'CSV export started',
        'download_url': '/reports/download/export.csv'
    })


@reports_bp.route('/generate/page/<page_id>', methods=['POST'])
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT)
def generate_page_report(page_id: str) -> tuple[Response, int] | Response:
    """Generate report for a single page (background job)"""
    format = _body_format()
    include_ai = request.form.get('include_ai', 'true') == 'true'

    page = get_db().get_page(page_id)
    if not page:
        return jsonify({'success': False, 'error': 'Page not found'}), 404

    # Capture Flask context into local variables
    db = get_db()
    config = get_app_config().__dict__.copy()
    language = str(get_locale()) if get_locale() else 'en'
    app = _get_real_app()

    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    # Persist the page's scope onto the job so status/drop/restart can authorize
    # the page's project members (not just superadmin). We store both the
    # resolved website_id (top-level, the scope _authorize_job_record gates on)
    # and the page_id in metadata, mirroring api.py's report-record shape.
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        website_id=str(page.website_id),
        metadata={
            'report_type': format,
            'scope': 'page',
            'page_id': page_id,
            'display_name': f'Page Report - {page.title or page.url}',
        },
    )

    def wrapper() -> None:
        try:
            with app.app_context(), force_locale(language):
                generator = ReportGenerator(db, config, language=language)
                job = ReportJob(job_id, job_manager, generator.generate_page_report,
                               generator_kwargs={'page_id': page_id, 'format': format, 'include_ai': include_ai})
                job.run()
        except Exception as e:
            logger.error(f"Report job {job_id} wrapper failed: {e}", exc_info=True)
            try:
                job_manager.update_job_status(job_id, JobStatus.FAILED, error=str(e))
            except Exception:
                pass

    task_runner.submit_task(func=wrapper, task_id=job_id)
    return jsonify({'success': True, 'job_id': job_id})


@reports_bp.route('/generate/website/<website_id>', methods=['POST'])
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT)
def generate_website_report(website_id: str) -> tuple[Response, int] | Response:
    """Generate report for entire website (background job)"""
    format = _body_format()
    include_ai = request.form.get('include_ai', 'true') == 'true'

    website = get_db().get_website(website_id)
    if not website:
        return jsonify({'success': False, 'error': 'Website not found'}), 404

    # Capture Flask context into local variables
    db = get_db()
    config = get_app_config().__dict__.copy()
    language = str(get_locale()) if get_locale() else 'en'
    app = _get_real_app()

    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        website_id=str(website_id),
        metadata={'report_type': format, 'scope': 'website', 'display_name': f'Website Report - {website.name}'}
    )

    def wrapper() -> None:
        try:
            with app.app_context(), force_locale(language):
                generator = ReportGenerator(db, config, language=language)
                job = ReportJob(job_id, job_manager, generator.generate_website_report,
                               generator_kwargs={'website_id': website_id, 'format': format, 'include_ai': include_ai})
                job.run()
        except Exception as e:
            logger.error(f"Report job {job_id} wrapper failed: {e}", exc_info=True)
            try:
                job_manager.update_job_status(job_id, JobStatus.FAILED, error=str(e))
            except Exception:
                pass

    task_runner.submit_task(func=wrapper, task_id=job_id)
    return jsonify({'success': True, 'job_id': job_id})


@reports_bp.route('/generate/project/<project_id>', methods=['POST'])
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT)
def generate_project_report(project_id: str) -> tuple[Response, int] | Response:
    """Generate report for entire project (background job)"""
    format = _body_format()

    project = get_db().get_project(project_id)
    if not project:
        return jsonify({'success': False, 'error': 'Project not found'}), 404

    # Capture Flask context into local variables
    db = get_db()
    config = get_app_config().__dict__.copy()
    language = str(get_locale()) if get_locale() else 'en'
    app = _get_real_app()

    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        project_id=str(project_id),
        metadata={'report_type': format, 'scope': 'project', 'display_name': f'Project Report - {project.name}'}
    )

    def wrapper() -> None:
        try:
            with app.app_context(), force_locale(language):
                generator = ReportGenerator(db, config, language=language)
                job = ReportJob(job_id, job_manager, generator.generate_project_report,
                               generator_kwargs={'project_id': project_id, 'format': format})
                job.run()
        except Exception as e:
            logger.error(f"Report job {job_id} wrapper failed: {e}", exc_info=True)
            try:
                job_manager.update_job_status(job_id, JobStatus.FAILED, error=str(e))
            except Exception:
                pass

    task_runner.submit_task(func=wrapper, task_id=job_id)
    return jsonify({'success': True, 'job_id': job_id})


@reports_bp.route('/generate/page-structure/<website_id>', methods=['POST'])
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT)
def generate_page_structure_report_download(website_id: str) -> tuple[Response, int] | Response:
    """Generate site structure tree report for website (background job)"""
    format = _body_format()

    # Validate inputs in route handler
    website = get_db().get_website(website_id)
    if not website:
        return jsonify({'error': 'Website not found'}), 404

    project = None
    if website.project_id:
        project = get_db().get_project(website.project_id)

    pages = get_db().get_pages(website_id)
    if not pages:
        return jsonify({'error': 'No pages found for website'}), 404

    # Capture Flask context into local variables
    db = get_db()
    language = session.get('language', 'en')
    app = _get_real_app()

    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        website_id=str(website_id),
        metadata={'report_type': format, 'scope': 'page_structure', 'display_name': f'Page Structure - {website.name}'}
    )

    def wrapper() -> None:
        try:
            with app.app_context(), force_locale(language):
                def generate_and_save(progress_callback: Callable[..., Any] | None = None) -> Any:
                    report = PageStructureReport(db, website, pages, project, language=language)
                    report.generate(progress_callback=progress_callback)
                    return report.save(format)
                job = ReportJob(job_id, job_manager, generate_and_save)
                job.run()
        except Exception as e:
            logger.error(f"Report job {job_id} wrapper failed: {e}", exc_info=True)
            try:
                job_manager.update_job_status(job_id, JobStatus.FAILED, error=str(e))
            except Exception:
                pass

    task_runner.submit_task(func=wrapper, task_id=job_id)
    return jsonify({'success': True, 'job_id': job_id})


@reports_bp.route('/generate/page-structure', methods=['POST'])
@login_required
def generate_page_structure_report() -> tuple[Response, int] | Response:
    """Generate site structure tree report (background job)"""
    # Accept both JSON and form data
    data = _json_body()
    website_id_any: object = request.form.get('website_id') or data.get('website_id')
    website_id = website_id_any if isinstance(website_id_any, str) else None
    format_any: object = request.form.get('format') or data.get('format', 'html')
    format = format_any if isinstance(format_any, str) else 'html'

    # Validate inputs in route handler
    if not website_id:
        return jsonify({'success': False, 'error': 'Website ID required'}), 400

    # website_id comes from the body, not a URL param, so authorize in-handler.
    _authorize_body_scope(None, website_id)
    website = get_db().get_website(website_id)
    if not website:
        return jsonify({'success': False, 'error': 'Website not found'}), 404

    project = None
    if website.project_id:
        project = get_db().get_project(website.project_id)

    pages = get_db().get_pages(website_id)
    if not pages:
        return jsonify({'success': False, 'error': 'No pages found for website'}), 404

    # Capture Flask context into local variables
    db = get_db()
    language = session.get('language', 'en')
    app = _get_real_app()

    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        website_id=str(website_id),
        metadata={'report_type': format, 'scope': 'page_structure', 'display_name': f'Page Structure - {website.name}'}
    )

    def wrapper() -> None:
        try:
            with app.app_context(), force_locale(language):
                def generate_and_save(progress_callback: Callable[..., Any] | None = None) -> Any:
                    report = PageStructureReport(db, website, pages, project, language=language)
                    report.generate(progress_callback=progress_callback)
                    return report.save(format)
                job = ReportJob(job_id, job_manager, generate_and_save)
                job.run()
        except Exception as e:
            logger.error(f"Report job {job_id} wrapper failed: {e}", exc_info=True)
            try:
                job_manager.update_job_status(job_id, JobStatus.FAILED, error=str(e))
            except Exception:
                pass

    task_runner.submit_task(func=wrapper, task_id=job_id)
    return jsonify({'success': True, 'job_id': job_id})


@reports_bp.route('/generate/discovery/website/<website_id>', methods=['POST'])
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT)
def generate_discovery_website_report(website_id: str) -> tuple[Response, int] | Response:
    """Generate discovery report for a website (background job)"""
    format = _body_format()

    website = get_db().get_website(website_id)
    if not website:
        return jsonify({'success': False, 'error': 'Website not found'}), 404

    # Capture Flask context into local variables
    db = get_db()
    config = get_app_config().__dict__.copy()
    language = session.get('language', 'en')
    app = _get_real_app()

    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        website_id=str(website_id),
        metadata={'report_type': format, 'scope': 'discovery_website', 'display_name': f'Discovery Report - {website.name}'}
    )

    def wrapper() -> None:
        try:
            with app.app_context(), force_locale(language):
                generator = DiscoveryReportGenerator(db, config, language=language)
                job = ReportJob(job_id, job_manager, generator.generate_website_discovery_report,
                               generator_kwargs={'website_id': website_id, 'format': format})
                job.run()
        except Exception as e:
            logger.error(f"Report job {job_id} wrapper failed: {e}", exc_info=True)
            try:
                job_manager.update_job_status(job_id, JobStatus.FAILED, error=str(e))
            except Exception:
                pass

    task_runner.submit_task(func=wrapper, task_id=job_id)
    return jsonify({'success': True, 'job_id': job_id})


@reports_bp.route('/generate/discovery/project/<project_id>', methods=['POST'])
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT)
def generate_discovery_project_report(project_id: str) -> tuple[Response, int] | Response:
    """Generate discovery report for an entire project (background job)"""
    format = _body_format()

    project = get_db().get_project(project_id)
    if not project:
        return jsonify({'success': False, 'error': 'Project not found'}), 404

    # Capture Flask context into local variables
    db = get_db()
    config = get_app_config().__dict__.copy()
    language = session.get('language', 'en')
    app = _get_real_app()

    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        project_id=str(project_id),
        metadata={'report_type': format, 'scope': 'discovery_project', 'display_name': f'Discovery Report - {project.name}'}
    )

    def wrapper() -> None:
        try:
            with app.app_context(), force_locale(language):
                generator = DiscoveryReportGenerator(db, config, language=language)
                job = ReportJob(job_id, job_manager, generator.generate_project_discovery_report,
                               generator_kwargs={'project_id': project_id, 'format': format})
                job.run()
        except Exception as e:
            logger.error(f"Report job {job_id} wrapper failed: {e}", exc_info=True)
            try:
                job_manager.update_job_status(job_id, JobStatus.FAILED, error=str(e))
            except Exception:
                pass

    task_runner.submit_task(func=wrapper, task_id=job_id)
    return jsonify({'success': True, 'job_id': job_id})

@reports_bp.route('/generate/static-html', methods=['POST'])
@login_required
def generate_static_html_report() -> tuple[Response, int] | Response:
    """Generate static HTML report (background job)"""
    # Get data from form submission
    project_id = request.form.get('project_id')
    website_id = request.form.get('website_id')

    # Scope ids come from the form body, not a URL param. Authorize the scope
    # in-handler; an "all projects" roll-up (neither id) requires superadmin.
    _authorize_body_scope(project_id, website_id)

    include_screenshots = request.form.get('include_screenshots', 'true') in ['true', 'True', '1', 'on']
    include_discovery = request.form.get('include_discovery', 'true') in ['true', 'True', '1', 'on']
    wcag_level = request.form.get('wcag_level', 'AA')

    # Collect all page IDs based on scope (data collection stays in route handler)
    page_ids: list[str] = []
    project_name = "Accessibility Report"
    website_url = None
    touchpoints_tested: list[str] | None = None
    display_name = 'Static HTML Report'

    if project_id:
        project = get_db().get_project(project_id)
        if not project:
            return jsonify({'error': 'Project not found'}), 404

        project_name = project.name
        display_name = f'Static HTML Report - {project.name}'
        websites = get_db().get_websites(project_id)

        for website in websites:
            if not website.id:
                continue
            pages = get_db().get_pages(website.id)
            page_ids.extend([str(p.id) for p in pages if p.status == PageStatus.TESTED])
            if not website_url and website.url:
                website_url = website.url

    elif website_id:
        ws = get_db().get_website(website_id)
        if not ws:
            return jsonify({'error': 'Website not found'}), 404

        if ws.project_id:
            project = get_db().get_project(ws.project_id)
            if project:
                project_name = f"{project.name} - {ws.name}"
            else:
                project_name = ws.name or 'Unknown'
        else:
            project_name = ws.name or 'Unknown'

        display_name = f'Static HTML Report - {project_name}'
        website_url = ws.url
        pages = get_db().get_pages(website_id)
        page_ids = [str(p.id) for p in pages if p.status == PageStatus.TESTED]
    else:
        projects = get_db().get_projects()
        project_name = "All Projects Accessibility Report"
        display_name = 'Static HTML Report - All Projects'

        for project in projects:
            if not project.id:
                continue
            websites = get_db().get_websites(project.id)
            for website in websites:
                if not website.id:
                    continue
                pages = get_db().get_pages(website.id)
                page_ids.extend([str(p.id) for p in pages if p.status == PageStatus.TESTED])

    if not page_ids:
        return jsonify({'error': 'No tested pages found to generate report'}), 400

    # Get touchpoints from first page's test result
    if page_ids:
        first_result = get_db().get_latest_test_result(page_ids[0])
        if first_result and first_result.violations:
            tp_set: set[str] = set()
            for violation in first_result.violations:
                if violation.touchpoint:
                    tp_set.add(violation.touchpoint)
            touchpoints_tested = sorted(list(tp_set))

    # Capture Flask context into local variables
    db = get_db()
    language = session.get('language', 'en')
    output_dir = get_app_config().REPORTS_DIR
    app = _get_real_app()

    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        project_id=str(project_id) if project_id else None,
        website_id=str(website_id) if website_id else None,
        metadata={'report_type': 'static_html', 'scope': 'static_html', 'display_name': display_name}
    )

    def wrapper() -> None:
        try:
            with app.app_context(), force_locale(language):
                generator = StaticHTMLReportGenerator(db, output_dir=output_dir, language=language)
                def generate_static(progress_callback: Callable[..., Any] | None = None) -> Any:
                    return generator.generate_report(
                        page_ids=page_ids,
                        project_name=project_name,
                        website_url=website_url,
                        wcag_level=wcag_level,
                        touchpoints_tested=touchpoints_tested,
                        include_screenshots=include_screenshots,
                        include_discovery=include_discovery,
                        ai_tests_enabled=True,
                        progress_callback=progress_callback
                    )
                job = ReportJob(job_id, job_manager, generate_static)
                job.run()
        except Exception as e:
            logger.error(f"Report job {job_id} wrapper failed: {e}", exc_info=True)
            try:
                job_manager.update_job_status(job_id, JobStatus.FAILED, error=str(e))
            except Exception:
                pass

    task_runner.submit_task(func=wrapper, task_id=job_id)
    return jsonify({'success': True, 'job_id': job_id})


@reports_bp.route('/generate/deduplicated', methods=['POST'])
@login_required
def generate_deduplicated_report() -> Response:
    """Generate deduplicated offline HTML report (background job)"""
    project_id = request.form.get('project_id')
    website_id = request.form.get('website_id')

    # Scope ids come from the form body, not a URL param. Authorize in-handler;
    # an "all projects" roll-up (neither id) requires superadmin.
    _authorize_body_scope(project_id, website_id)

    # Build display name
    display_name = 'Deduplicated Report'
    if project_id:
        project = get_db().get_project(project_id)
        if project:
            display_name = f'Deduplicated Report - {project.name}'
    if website_id:
        website = get_db().get_website(website_id)
        if website:
            display_name = f'Deduplicated Report - {website.name}'

    # Capture Flask context into local variables
    db = get_db()
    language = session.get('language', 'en')
    output_dir = get_app_config().REPORTS_DIR
    app = _get_real_app()

    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        project_id=str(project_id) if project_id else None,
        website_id=str(website_id) if website_id else None,
        metadata={'report_type': 'deduplicated', 'scope': 'deduplicated', 'display_name': display_name}
    )

    def wrapper() -> None:
        try:
            with app.app_context(), force_locale(language):
                generator = StaticHTMLReportGenerator(db, output_dir=output_dir, language=language)
                def generate_dedup(progress_callback: Callable[..., Any] | None = None) -> Any:
                    return generator.generate_project_deduplicated_report(
                        project_id=project_id,
                        website_id=website_id if website_id else None,
                        progress_callback=progress_callback
                    )
                job = ReportJob(job_id, job_manager, generate_dedup)
                job.run()
        except Exception as e:
            logger.error(f"Report job {job_id} wrapper failed: {e}", exc_info=True)
            try:
                job_manager.update_job_status(job_id, JobStatus.FAILED, error=str(e))
            except Exception:
                pass

    task_runner.submit_task(func=wrapper, task_id=job_id)
    return jsonify({'success': True, 'job_id': job_id})


@reports_bp.route('/generate/recordings/<project_id>', methods=['POST'])
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT)
def generate_recordings_report(project_id: str) -> tuple[Response, int] | Response:
    """Generate report for recordings in a project (background job)"""
    format = _body_format()
    include_summary = request.form.get('include_summary', 'true') in ['true', 'True', '1', 'on']
    include_timecodes = request.form.get('include_timecodes', 'true') in ['true', 'True', '1', 'on']
    include_wcag = request.form.get('include_wcag', 'true') in ['true', 'True', '1', 'on']
    group_by_touchpoint = request.form.get('group_by_touchpoint', 'true') in ['true', 'True', '1', 'on']

    # Validate in route handler
    project = get_db().get_project(project_id)
    if not project:
        return jsonify({'success': False, 'error': 'Project not found'}), 404

    recordings = get_db().get_recordings(project_id=project_id)
    if not recordings:
        return jsonify({
            'success': False,
            'info': True,
            'title': ftl('reports-no-recordings-available'),
            'message': ftl('reports-there-are-no-recordings-for-this-project-yet-once')
        }), 200

    # Capture Flask context into local variables
    db = get_db()
    config = get_app_config().__dict__.copy()
    language = session.get('language', 'en')
    app = _get_real_app()

    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        project_id=str(project_id),
        metadata={'report_type': format, 'scope': 'recordings', 'display_name': f'Recordings Report - {project.name}'}
    )

    def wrapper() -> None:
        try:
            with app.app_context(), force_locale(language):
                from auto_a11y.reporting.recordings_report import RecordingsReportGenerator
                generator = RecordingsReportGenerator(db, config, language=language)
                job = ReportJob(job_id, job_manager, generator.generate_project_recordings_report,
                               generator_kwargs={
                                   'project_id': project_id,
                                   'format': format,
                                   'include_summary': include_summary,
                                   'include_timecodes': include_timecodes,
                                   'include_wcag': include_wcag,
                                   'group_by_touchpoint': group_by_touchpoint,
                                   'language': language
                               })
                job.run()
        except Exception as e:
            logger.error(f"Report job {job_id} wrapper failed: {e}", exc_info=True)
            try:
                job_manager.update_job_status(job_id, JobStatus.FAILED, error=str(e))
            except Exception:
                pass

    task_runner.submit_task(func=wrapper, task_id=job_id)
    return jsonify({'success': True, 'job_id': job_id})
