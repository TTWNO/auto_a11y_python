"""
Report generation routes
"""

from flask import Blueprint, render_template, request, jsonify, send_file, current_app, url_for, flash, redirect, session, g
from flask_babel import get_locale, gettext as _
from auto_a11y.models import PageStatus
from auto_a11y.reporting import ReportGenerator, PageStructureReport
from auto_a11y.reporting.discovery_report import DiscoveryReportGenerator
from auto_a11y.reporting.static_html_generator import StaticHTMLReportGenerator
from auto_a11y.core.job_manager import JobManager, JobType, JobStatus
from auto_a11y.core.task_runner import task_runner
from auto_a11y.core.report_job import ReportJob
from datetime import datetime, timedelta
from uuid import uuid4
import logging
import json
from pathlib import Path

logger = logging.getLogger(__name__)
reports_bp = Blueprint('reports', __name__)


@reports_bp.route('/dashboard')
def reports_dashboard():
    """Reports dashboard"""
    # Get available reports
    reports_dir = current_app.app_config.REPORTS_DIR
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
    projects = current_app.db.get_projects()
    
    # Get all websites for the dropdown
    websites = []
    for project in projects:
        project_websites = current_app.db.get_websites(project.id)
        for website in project_websites:
            websites.append({
                'id': str(website.id),
                'name': website.name,
                'project_id': str(project.id),
                'project_name': project.name
            })
    
    # Get active report generation jobs
    job_manager = JobManager(current_app.db)
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
def generate_report():
    """Generate accessibility report (background job)"""
    data = request.get_json()
    project_id = data.get('project_id')
    website_id = data.get('website_id')
    report_type = data.get('type', 'xlsx')

    scope = 'all'
    scope_id = None
    display_name = 'All Projects Report'

    if project_id:
        project = current_app.db.get_project(project_id)
        if not project:
            return jsonify({'error': 'Project not found'}), 404
        scope = 'project'
        scope_id = project_id
        display_name = f'Accessibility Report - {project.name}'
    elif website_id:
        website = current_app.db.get_website(website_id)
        if not website:
            return jsonify({'error': 'Website not found'}), 404
        scope = 'website'
        scope_id = website_id
        display_name = f'Accessibility Report - {website.name}'

    # Capture Flask context into local variables
    db = current_app.db
    config = current_app.app_config.__dict__.copy()
    language = str(get_locale()) if get_locale() else 'en'
    app = current_app._get_current_object()

    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        project_id=str(scope_id) if scope == 'project' else None,
        website_id=str(scope_id) if scope == 'website' else None,
        metadata={'report_type': report_type, 'scope': scope, 'display_name': display_name}
    )

    def wrapper():
        try:
            with app.app_context():
                from auto_a11y.reporting import ReportGenerator
                generator = ReportGenerator(db, config, language=language)
                format_map = {'excel': 'xlsx'}
                fmt = format_map.get(report_type, report_type)
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
def job_status(job_id):
    """Get report job status"""
    job_manager = JobManager(current_app.db)
    job = job_manager.get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    response = {
        'job_id': job['job_id'],
        'status': job['status'],
        'progress': job.get('progress', {}),
        'result': job.get('result'),
        'error': job.get('error'),
        'metadata': job.get('metadata', {})
    }
    return jsonify(response)


@reports_bp.route('/download/<filename>')
def download_report(filename):
    """Download generated report"""
    reports_dir = current_app.app_config.REPORTS_DIR
    file_path = reports_dir / filename
    
    if not file_path.exists():
        return jsonify({'error': 'Report not found'}), 404
    
    # Security check - ensure file is in reports directory
    if not file_path.resolve().parent == reports_dir.resolve():
        return jsonify({'error': 'Invalid file path'}), 403
    
    return send_file(
        file_path,
        as_attachment=True,
        download_name=filename
    )


@reports_bp.route('/project/<project_id>/summary')
def project_summary(project_id):
    """Generate project summary report"""
    project = current_app.db.get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    
    stats = current_app.db.get_project_stats(project_id)
    websites = current_app.db.get_websites(project_id)
    
    # Get violation breakdown
    violation_summary = {
        'by_touchpoint': {},
        'by_severity': {
            'critical': 0,
            'serious': 0,
            'moderate': 0,
            'minor': 0
        },
        'top_issues': []
    }
    
    # Aggregate data from all test results
    for website in websites:
        pages = current_app.db.get_pages(website.id)
        for page in pages:
            if page.status == PageStatus.TESTED:
                result = current_app.db.get_latest_test_result(page.id)
                if result:
                    for violation in result.violations:
                        # Count by touchpoint
                        if violation.touchpoint not in violation_summary['by_touchpoint']:
                            violation_summary['by_touchpoint'][violation.touchpoint] = 0
                        violation_summary['by_touchpoint'][violation.touchpoint] += 1
                        
                        # Count by severity
                        violation_summary['by_severity'][violation.impact.value] += 1
    
    return render_template('reports/project_summary.html',
                         project=project,
                         stats=stats,
                         websites=websites,
                         violation_summary=violation_summary)


@reports_bp.route('/export-csv', methods=['POST'])
def export_csv():
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
def generate_page_report(page_id):
    """Generate report for a single page (background job)"""
    format = request.form.get('format', request.json.get('format', 'html') if request.is_json else 'html')
    include_ai = request.form.get('include_ai', 'true') == 'true'

    page = current_app.db.get_page(page_id)
    if not page:
        return jsonify({'success': False, 'error': 'Page not found'}), 404

    # Capture Flask context into local variables
    db = current_app.db
    config = current_app.app_config.__dict__.copy()
    language = str(get_locale()) if get_locale() else 'en'
    app = current_app._get_current_object()

    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        metadata={'report_type': format, 'scope': 'page', 'display_name': f'Page Report - {page.title or page.url}'}
    )

    def wrapper():
        try:
            with app.app_context():
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
def generate_website_report(website_id):
    """Generate report for entire website (background job)"""
    format = request.form.get('format', request.json.get('format', 'html') if request.is_json else 'html')
    include_ai = request.form.get('include_ai', 'true') == 'true'

    website = current_app.db.get_website(website_id)
    if not website:
        return jsonify({'success': False, 'error': 'Website not found'}), 404

    # Capture Flask context into local variables
    db = current_app.db
    config = current_app.app_config.__dict__.copy()
    language = str(get_locale()) if get_locale() else 'en'
    app = current_app._get_current_object()

    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        website_id=str(website_id),
        metadata={'report_type': format, 'scope': 'website', 'display_name': f'Website Report - {website.name}'}
    )

    def wrapper():
        try:
            with app.app_context():
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
def generate_project_report(project_id):
    """Generate report for entire project (background job)"""
    format = request.form.get('format', request.json.get('format', 'html') if request.is_json else 'html')

    project = current_app.db.get_project(project_id)
    if not project:
        return jsonify({'success': False, 'error': 'Project not found'}), 404

    # Capture Flask context into local variables
    db = current_app.db
    config = current_app.app_config.__dict__.copy()
    language = str(get_locale()) if get_locale() else 'en'
    app = current_app._get_current_object()

    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        project_id=str(project_id),
        metadata={'report_type': format, 'scope': 'project', 'display_name': f'Project Report - {project.name}'}
    )

    def wrapper():
        try:
            with app.app_context():
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
def generate_page_structure_report_download(website_id):
    """Generate site structure tree report for website (background job)"""
    format = request.form.get('format', request.json.get('format', 'html') if request.is_json else 'html')

    # Validate inputs in route handler
    website = current_app.db.get_website(website_id)
    if not website:
        return jsonify({'error': 'Website not found'}), 404

    project = None
    if website.project_id:
        project = current_app.db.get_project(website.project_id)

    pages = current_app.db.get_pages(website_id)
    if not pages:
        return jsonify({'error': 'No pages found for website'}), 404

    # Capture Flask context into local variables
    db = current_app.db
    language = session.get('language', 'en')
    app = current_app._get_current_object()

    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        website_id=str(website_id),
        metadata={'report_type': format, 'scope': 'page_structure', 'display_name': f'Page Structure - {website.name}'}
    )

    def wrapper():
        try:
            with app.app_context():
                def generate_and_save(progress_callback=None):
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
def generate_page_structure_report():
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
    website = current_app.db.get_website(website_id)
    if not website:
        return jsonify({'success': False, 'error': 'Website not found'}), 404

    project = None
    if website.project_id:
        project = current_app.db.get_project(website.project_id)

    pages = current_app.db.get_pages(website_id)
    if not pages:
        return jsonify({'success': False, 'error': 'No pages found for website'}), 404

    # Capture Flask context into local variables
    db = current_app.db
    language = session.get('language', 'en')
    app = current_app._get_current_object()

    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        website_id=str(website_id),
        metadata={'report_type': format, 'scope': 'page_structure', 'display_name': f'Page Structure - {website.name}'}
    )

    def wrapper():
        try:
            with app.app_context():
                def generate_and_save(progress_callback=None):
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
def generate_discovery_website_report(website_id):
    """Generate discovery report for a website (background job)"""
    format = request.form.get('format', request.json.get('format', 'html') if request.is_json else 'html')

    website = current_app.db.get_website(website_id)
    if not website:
        return jsonify({'success': False, 'error': 'Website not found'}), 404

    # Capture Flask context into local variables
    db = current_app.db
    config = current_app.app_config.__dict__.copy()
    language = session.get('language', 'en')
    app = current_app._get_current_object()

    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        website_id=str(website_id),
        metadata={'report_type': format, 'scope': 'discovery_website', 'display_name': f'Discovery Report - {website.name}'}
    )

    def wrapper():
        try:
            with app.app_context():
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
def generate_discovery_project_report(project_id):
    """Generate discovery report for an entire project (background job)"""
    format = request.form.get('format', request.json.get('format', 'html') if request.is_json else 'html')

    project = current_app.db.get_project(project_id)
    if not project:
        return jsonify({'success': False, 'error': 'Project not found'}), 404

    # Capture Flask context into local variables
    db = current_app.db
    config = current_app.app_config.__dict__.copy()
    language = session.get('language', 'en')
    app = current_app._get_current_object()

    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        project_id=str(project_id),
        metadata={'report_type': format, 'scope': 'discovery_project', 'display_name': f'Discovery Report - {project.name}'}
    )

    def wrapper():
        try:
            with app.app_context():
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
def generate_static_html_report():
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
        project = current_app.db.get_project(project_id)
        if not project:
            return jsonify({'error': 'Project not found'}), 404

        project_name = project.name
        display_name = f'Static HTML Report - {project.name}'
        websites = current_app.db.get_websites(project_id)

        for website in websites:
            pages = current_app.db.get_pages(website.id)
            page_ids.extend([str(p.id) for p in pages if p.status == PageStatus.TESTED])
            if not website_url and website.url:
                website_url = website.url

    elif website_id:
        website = current_app.db.get_website(website_id)
        if not website:
            return jsonify({'error': 'Website not found'}), 404

        if website.project_id:
            project = current_app.db.get_project(website.project_id)
            if project:
                project_name = f"{project.name} - {website.name}"
            else:
                project_name = website.name
        else:
            project_name = website.name

        display_name = f'Static HTML Report - {project_name}'
        website_url = website.url
        pages = current_app.db.get_pages(website_id)
        page_ids = [str(p.id) for p in pages if p.status == PageStatus.TESTED]
    else:
        projects = current_app.db.get_projects()
        project_name = "All Projects Accessibility Report"
        display_name = 'Static HTML Report - All Projects'

        for project in projects:
            websites = current_app.db.get_websites(project.id)
            for website in websites:
                pages = current_app.db.get_pages(website.id)
                page_ids.extend([str(p.id) for p in pages if p.status == PageStatus.TESTED])

    if not page_ids:
        return jsonify({'error': 'No tested pages found to generate report'}), 400

    # Get touchpoints from first page's test result
    if page_ids:
        first_result = current_app.db.get_latest_test_result(page_ids[0])
        if first_result and first_result.violations:
            touchpoints_set = set()
            for violation in first_result.violations:
                if violation.touchpoint:
                    touchpoints_set.add(violation.touchpoint)
            touchpoints_tested = sorted(list(touchpoints_set))

    # Capture Flask context into local variables
    db = current_app.db
    language = session.get('language', 'en')
    output_dir = current_app.app_config.REPORTS_DIR
    app = current_app._get_current_object()

    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        project_id=str(project_id) if project_id else None,
        website_id=str(website_id) if website_id else None,
        metadata={'report_type': 'static_html', 'scope': 'static_html', 'display_name': display_name}
    )

    def wrapper():
        try:
            with app.app_context():
                generator = StaticHTMLReportGenerator(db, output_dir=output_dir, language=language)
                def generate_static(progress_callback=None):
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
def generate_deduplicated_report():
    """Generate deduplicated offline HTML report (background job)"""
    project_id = request.form.get('project_id')
    website_id = request.form.get('website_id')

    # Build display name
    display_name = 'Deduplicated Report'
    if project_id:
        project = current_app.db.get_project(project_id)
        if project:
            display_name = f'Deduplicated Report - {project.name}'
    if website_id:
        website = current_app.db.get_website(website_id)
        if website:
            display_name = f'Deduplicated Report - {website.name}'

    # Capture Flask context into local variables
    db = current_app.db
    language = session.get('language', 'en')
    output_dir = current_app.app_config.REPORTS_DIR
    app = current_app._get_current_object()

    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        project_id=str(project_id) if project_id else None,
        website_id=str(website_id) if website_id else None,
        metadata={'report_type': 'deduplicated', 'scope': 'deduplicated', 'display_name': display_name}
    )

    def wrapper():
        try:
            with app.app_context():
                generator = StaticHTMLReportGenerator(db, output_dir=output_dir, language=language)
                def generate_dedup(progress_callback=None):
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
def generate_recordings_report(project_id):
    """Generate report for recordings in a project (background job)"""
    format = request.form.get('format', request.json.get('format', 'html') if request.is_json else 'html')
    include_summary = request.form.get('include_summary', 'true') in ['true', 'True', '1', 'on']
    include_timecodes = request.form.get('include_timecodes', 'true') in ['true', 'True', '1', 'on']
    include_wcag = request.form.get('include_wcag', 'true') in ['true', 'True', '1', 'on']
    group_by_touchpoint = request.form.get('group_by_touchpoint', 'true') in ['true', 'True', '1', 'on']

    # Validate in route handler
    project = current_app.db.get_project(project_id)
    if not project:
        return jsonify({'success': False, 'error': 'Project not found'}), 404

    recordings = current_app.db.get_recordings(project_id=project_id)
    if not recordings:
        return jsonify({
            'success': False,
            'info': True,
            'title': _('No Recordings Available'),
            'message': _('There are no recordings for this project yet. Once recordings have been added, you can generate a report.')
        }), 200

    # Capture Flask context into local variables
    db = current_app.db
    config = current_app.app_config.__dict__.copy()
    language = session.get('language', 'en')
    app = current_app._get_current_object()

    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        project_id=str(project_id),
        metadata={'report_type': format, 'scope': 'recordings', 'display_name': f'Recordings Report - {project.name}'}
    )

    def wrapper():
        try:
            with app.app_context():
                from auto_a11y.reporting.recordings_report import RecordingsReportGenerator
                generator = RecordingsReportGenerator(db, config)
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
