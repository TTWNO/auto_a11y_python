"""
Report generation routes
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flask import Blueprint, Flask, Response, render_template, request, jsonify, send_file, current_app, url_for, flash, redirect, session, g
from werkzeug.wrappers import Response as WerkzeugResponse
from auto_a11y.web.fluent import ftl, force_locale, _get_current_locale as get_locale
from auto_a11y.models import PageStatus
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
import json
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


@reports_bp.route('/dashboard')
def reports_dashboard() -> str:
    """Reports dashboard"""
    # Get available reports
    reports_dir = get_app_config().REPORTS_DIR
    report_files = list(reports_dir.glob('*.xlsx')) + list(reports_dir.glob('*.html')) + list(reports_dir.glob('*.json')) + list(reports_dir.glob('*.pdf')) + list(reports_dir.glob('*.zip')) + list(reports_dir.glob('*.csv'))
    
    reports = []
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
    websites = []
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
def generate_report() -> tuple[Response, int] | Response:
    """Generate accessibility report (background job)"""
    data = request.get_json()
    project_id = data.get('project_id')
    website_id = data.get('website_id')
    report_type = data.get('type', 'xlsx')

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
def job_status(job_id: str) -> tuple[Response, int] | Response:
    """Get report job status"""
    job_manager = JobManager(get_db())
    job = job_manager.get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
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
def drop_job(job_id: str) -> tuple[Response, int] | Response:
    """Drop/delete a stalled or in-progress report job"""
    job_manager = JobManager(get_db())
    job = job_manager.get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404

    # Request cancellation if still active (so thread stops gracefully)
    if job.get('status') in (JobStatus.PENDING.value, JobStatus.RUNNING.value):
        job_manager.request_cancellation(job_id)

    job_manager.collection.delete_one({'job_id': job_id})
    return jsonify({'success': True})


@reports_bp.route('/job/<job_id>/restart', methods=['POST'])
def restart_job(job_id: str) -> tuple[Response, int] | Response:
    """Restart a stalled report job from scratch"""
    job_manager = JobManager(get_db())
    old_job = job_manager.get_job(job_id)
    if not old_job:
        return jsonify({'error': 'Job not found'}), 404

    metadata = old_job.get('metadata', {})
    scope = metadata.get('scope')
    report_type = metadata.get('report_type', 'xlsx')
    display_name = metadata.get('display_name', 'Report')
    project_id = old_job.get('project_id')
    website_id = old_job.get('website_id')

    # Cancel and remove old job
    if old_job.get('status') in (JobStatus.PENDING.value, JobStatus.RUNNING.value):
        job_manager.request_cancellation(job_id)
    job_manager.collection.delete_one({'job_id': job_id})

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


@reports_bp.route('/download/<filename>')
def download_report(filename: str) -> tuple[Response, int] | Response | WerkzeugResponse:
    """Download generated report"""
    reports_dir = get_app_config().REPORTS_DIR
    file_path = reports_dir / filename
    
    if not file_path.exists():
        return jsonify({'error': 'Report not found'}), 404
    
    # Security check - ensure file is in reports directory
    if not file_path.resolve().is_relative_to(reports_dir.resolve()):
        return jsonify({'error': 'Invalid file path'}), 403
    
    return send_file(
        file_path,
        as_attachment=True,
        download_name=filename
    )


@reports_bp.route('/<filename>/delete', methods=['POST'])
def delete_report(filename: str) -> tuple[Response, int] | Response:
    """Delete a generated report"""
    reports_dir = get_app_config().REPORTS_DIR
    file_path = reports_dir / filename

    if not file_path.exists():
        return jsonify({'error': 'Report not found'}), 404

    # Security check - ensure file is in reports directory
    if not file_path.resolve().is_relative_to(reports_dir.resolve()):
        return jsonify({'error': 'Invalid file path'}), 403

    try:
        file_path.unlink()
        return jsonify({'success': True})
    except OSError as e:
        logger.error(f"Failed to delete report {filename}: {e}")
        return jsonify({'error': 'Failed to delete report'}), 500


@reports_bp.route('/project/<project_id>/summary')
def project_summary(project_id: str) -> Response | WerkzeugResponse:
    """Project summary -- redirects to the project report page."""
    return redirect(url_for('projects.generate_project_report', project_id=project_id))


@reports_bp.route('/export-csv', methods=['POST'])
def export_csv() -> Response:
    """Export data as CSV"""
    data = request.get_json()
    
    export_type = data.get('type')  # violations, pages, summary
    filters = data.get('filters', {})
    
    # Generate CSV based on type
    # This would be implemented with actual CSV generation
    
    return jsonify({
        'success': True,
        'message': 'CSV export started',
        'download_url': '/reports/download/export.csv'
    })


@reports_bp.route('/generate/page/<page_id>', methods=['POST'])
def generate_page_report(page_id: str) -> tuple[Response, int] | Response:
    """Generate report for a single page (background job)"""
    format = request.form.get('format', request.json.get('format', 'html') if request.is_json else 'html')
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
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        metadata={'report_type': format, 'scope': 'page', 'display_name': f'Page Report - {page.title or page.url}'}
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
def generate_website_report(website_id: str) -> tuple[Response, int] | Response:
    """Generate report for entire website (background job)"""
    format = request.form.get('format', request.json.get('format', 'html') if request.is_json else 'html')
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
def generate_project_report(project_id: str) -> tuple[Response, int] | Response:
    """Generate report for entire project (background job)"""
    format = request.form.get('format', request.json.get('format', 'html') if request.is_json else 'html')

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
def generate_page_structure_report_download(website_id: str) -> tuple[Response, int] | Response:
    """Generate site structure tree report for website (background job)"""
    format = request.form.get('format', request.json.get('format', 'html') if request.is_json else 'html')

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
def generate_page_structure_report() -> tuple[Response, int] | Response:
    """Generate site structure tree report (background job)"""
    # Accept both JSON and form data
    if request.is_json:
        data = request.get_json()
        website_id = data.get('website_id')
        format = data.get('format', 'html')
    else:
        website_id = request.form.get('website_id')
        format = request.form.get('format', 'html')

    # Validate inputs in route handler
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
def generate_discovery_website_report(website_id: str) -> tuple[Response, int] | Response:
    """Generate discovery report for a website (background job)"""
    format = request.form.get('format', request.json.get('format', 'html') if request.is_json else 'html')

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
def generate_discovery_project_report(project_id: str) -> tuple[Response, int] | Response:
    """Generate discovery report for an entire project (background job)"""
    format = request.form.get('format', request.json.get('format', 'html') if request.is_json else 'html')

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
def generate_static_html_report() -> tuple[Response, int] | Response:
    """Generate static HTML report (background job)"""
    # Get data from form submission
    project_id = request.form.get('project_id')
    website_id = request.form.get('website_id')
    include_screenshots = request.form.get('include_screenshots', 'true') in ['true', 'True', '1', 'on']
    include_discovery = request.form.get('include_discovery', 'true') in ['true', 'True', '1', 'on']
    wcag_level = request.form.get('wcag_level', 'AA')

    # Collect all page IDs based on scope (data collection stays in route handler)
    page_ids = []
    project_name = "Accessibility Report"
    website_url = None
    touchpoints_tested = None
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
            touchpoints_set = set()
            for violation in first_result.violations:
                if violation.touchpoint:
                    touchpoints_set.add(violation.touchpoint)
            touchpoints_tested = sorted(list(touchpoints_set))

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
def generate_deduplicated_report() -> Response:
    """Generate deduplicated offline HTML report (background job)"""
    project_id = request.form.get('project_id')
    website_id = request.form.get('website_id')

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
def generate_recordings_report(project_id: str) -> tuple[Response, int] | Response:
    """Generate report for recordings in a project (background job)"""
    format = request.form.get('format', request.json.get('format', 'html') if request.is_json else 'html')
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
