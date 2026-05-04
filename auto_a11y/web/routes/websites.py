"""
Website management routes
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session
from werkzeug.wrappers import Response
from auto_a11y.web.fluent import ftl
from auto_a11y.web.typed_app import get_db, get_app_config, get_pdf_runner
from auto_a11y.models import Page, PageStatus
from auto_a11y.models.pdf_document import PdfDocumentStatus
from auto_a11y.pdf.issue_map_counts import PdfIssueCounts, count_issues
from auto_a11y.pdf.storage import PdfStorage
import logging

logger = logging.getLogger(__name__)
websites_bp = Blueprint('websites', __name__)


@websites_bp.route('/api/list')
def api_list_websites() -> Response | tuple[Response, int]:
    """API endpoint to list all websites"""
    try:
        websites = get_db().get_all_websites()
        return jsonify({
            'success': True,
            'websites': [
                {
                    'id': w.id,
                    'name': w.name,
                    'url': w.url,
                    'project_id': w.project_id
                } for w in websites
            ]
        })
    except Exception as e:
        logger.error(f"Error listing websites: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@websites_bp.route('/<website_id>')
def view_website(website_id: str) -> str | Response:
    """View website details"""
    website = get_db().get_website(website_id)
    if not website:
        flash(ftl('common-website-not-found'), 'error')
        return redirect(url_for('projects.list_projects'))

    project = get_db().get_project(website.project_id)

    # Get pagination parameters from request, with config defaults and limits
    page_num = request.args.get('page', 1, type=int)
    # Use getattr with defaults for backward compatibility if config not reloaded
    default_per_page = getattr(get_app_config(), 'PAGES_PER_PAGE', 100)
    max_per_page = getattr(get_app_config(), 'MAX_PAGES_PER_PAGE', 500)
    per_page = request.args.get('per_page', default_per_page, type=int)

    # Enforce max pages per page limit
    if per_page > max_per_page:
        per_page = max_per_page
    elif per_page < 10:
        per_page = 10

    # Get total count of ALL pages for this website (not limited to latest discovery)
    total_page_count = get_db().pages.count_documents({'website_id': website_id})

    # Calculate statistics using database aggregation (efficient for large datasets)
    # This ensures we show stats for ALL discovered pages, not just the limited set
    pipeline: list[Mapping[str, Any]] = [
        {'$match': {'website_id': website_id}},
        {'$group': {
            '_id': None,
            'total_pages': {'$sum': 1},
            'tested_pages': {
                '$sum': {'$cond': [{'$eq': ['$status', 'tested']}, 1, 0]}
            },
            'pages_with_issues': {
                '$sum': {'$cond': [{'$gt': ['$violation_count', 0]}, 1, 0]}
            },
            'total_violations': {'$sum': '$violation_count'},
            'total_warnings': {'$sum': '$warning_count'}
        }}
    ]

    stats_result = list(get_db().pages.aggregate(pipeline))
    if stats_result:
        stats = stats_result[0]
        # Remove MongoDB's _id field
        stats.pop('_id', None)
        # Ensure total_pages matches the count
        stats['total_pages'] = total_page_count
    else:
        # No pages yet
        stats = {
            'total_pages': 0,
            'tested_pages': 0,
            'pages_with_issues': 0,
            'total_violations': 0,
            'total_warnings': 0
        }

    # Get paginated pages for display - show ALL pages, not just latest discovery
    skip = (page_num - 1) * per_page
    pages = get_db().get_pages(website_id, limit=per_page, skip=skip, latest_only=False)

    # Calculate pagination info
    total_pages_pagination = (total_page_count + per_page - 1) // per_page  # Ceiling division
    start_item = ((page_num - 1) * per_page) + 1
    end_item = min(page_num * per_page, total_page_count)

    # Calculate page range for pagination controls (show 5 pages at a time)
    start_page = max(1, page_num - 2)
    end_page = min(total_pages_pagination, page_num + 2)

    # Get available test users for this project
    project_users = get_db().get_project_users(website.project_id, enabled_only=True)

    # PDF nav badge count (Phase 9.7 — additive)
    website_pdfs = get_db().get_pdf_documents(website_id=website_id, limit=10000)
    pdf_count = len(website_pdfs)

    # PDF rollup into the global violation/warning totals (2026-05-01
    # spec Part 2). Reads the cached pdfMax issue_map.json for each
    # AUDITED PDF; the helper degrades to (0, 0) on missing/malformed
    # caches with a WARNING log, so a single bad cache never 500s
    # the page.
    storage = PdfStorage(base_dir=Path(get_app_config().PDF_STORAGE_DIR))
    pdf_totals = PdfIssueCounts(0, 0)
    for pdf in website_pdfs:
        if pdf.status is PdfDocumentStatus.AUDITED:
            pdf_totals = pdf_totals + count_issues(pdf, storage)
    stats['total_violations'] = stats.get('total_violations', 0) + pdf_totals.violations
    stats['total_warnings'] = stats.get('total_warnings', 0) + pdf_totals.warnings

    from auto_a11y.web.routes.projects import summarise_pdf_status
    pdf_status_counts = summarise_pdf_status(website_pdfs)

    # Combined "documents" counts (HTML pages + PDFs). The "Test All
    # Documents" / "Test Untested Documents" buttons act on both. A PDF
    # is "untested" when ``queue_audits_for_website`` would queue it —
    # i.e. anything other than AUDITED, AUDITING, or FETCH_FAILED.
    pdf_audited = pdf_status_counts.get('audited', 0)
    pdf_in_flight = pdf_status_counts.get('auditing', 0)
    pdf_unfetchable = pdf_status_counts.get('fetch_failed', 0)
    pdf_untested_eligible = pdf_count - pdf_audited - pdf_in_flight - pdf_unfetchable
    pages_untested = stats['total_pages'] - stats['tested_pages']
    documents = {
        'total': stats['total_pages'] + pdf_count,
        'tested': stats['tested_pages'] + pdf_audited,
        'untested': max(pages_untested, 0) + max(pdf_untested_eligible, 0),
    }

    return render_template('websites/view.html',
                         website=website,
                         project=project,
                         pages=pages,
                         stats=stats,
                         website_users=project_users,
                         pdf_count=pdf_count,
                         pdf_status_counts=pdf_status_counts,
                         documents=documents,
                         pagination={
                             'page': page_num,
                             'per_page': per_page,
                             'total_pages': total_pages_pagination,
                             'total_items': total_page_count,
                             'start_item': start_item,
                             'end_item': end_item,
                             'start_page': start_page,
                             'end_page': end_page,
                             'has_prev': page_num > 1,
                             'has_next': page_num < total_pages_pagination
                         })


@websites_bp.route('/<website_id>/edit', methods=['GET', 'POST'])
def edit_website(website_id: str) -> str | Response:
    """Edit website configuration"""
    website = get_db().get_website(website_id)
    if not website:
        flash(ftl('common-website-not-found'), 'error')
        return redirect(url_for('projects.list_projects'))

    if request.method == 'POST':
        website.name = request.form.get('name', website.name)
        website.url = request.form.get('url', website.url)
        
        # Update scraping config
        website.scraping_config.max_pages = int(request.form.get('max_pages', 999999))
        website.scraping_config.max_depth = int(request.form.get('max_depth', 10))
        website.scraping_config.follow_external = request.form.get('follow_external') == 'on'
        website.scraping_config.include_subdomains = request.form.get('include_subdomains') == 'on'
        website.scraping_config.respect_robots = request.form.get('respect_robots') == 'on'
        website.scraping_config.request_delay = float(request.form.get('request_delay', 1.0))
        
        if get_db().update_website(website):
            flash(ftl('websites-website-updated-successfully'), 'success')
            return redirect(url_for('websites.view_website', website_id=website_id))
        else:
            flash(ftl('websites-failed-to-update-website'), 'error')
    
    project = get_db().get_project(website.project_id)
    return render_template('websites/edit.html', website=website, project=project)


@websites_bp.route('/<website_id>/delete', methods=['POST'])
def delete_website(website_id: str) -> Response:
    """Delete website"""
    website = get_db().get_website(website_id)
    if not website:
        flash(ftl('common-website-not-found'), 'error')
        return redirect(url_for('projects.list_projects'))

    project_id = website.project_id

    if get_db().delete_website(website_id):
        flash(ftl('websites-website-name-deleted-successfully', name=website.display_name), 'success')
    else:
        flash(ftl('websites-failed-to-delete-website'), 'error')

    return redirect(url_for('projects.view_project', project_id=project_id))


@websites_bp.route('/<website_id>/clear-test-results', methods=['POST'])
def clear_test_results(website_id: str) -> Response:
    """Delete all test results and reset page counters for this website."""
    website = get_db().get_website(website_id)
    if not website:
        flash(ftl('common-website-not-found'), 'error')
        return redirect(url_for('projects.list_projects'))

    try:
        result = get_db().clear_website_test_results(website_id)
        flash(
            ftl(
                'websites-test-results-cleared',
                test_results=result['test_results_deleted'],
                pages=result['pages_reset'],
            ),
            'success',
        )
    except Exception as e:
        logger.error(f"Failed to clear test results for website {website_id}: {e}")
        flash(ftl('websites-failed-to-clear-test-results'), 'error')

    return redirect(url_for('websites.edit_website', website_id=website_id))


@websites_bp.route('/<website_id>/discover', methods=['POST'])
def discover_pages(website_id: str) -> Response | tuple[Response, int]:
    """Start page discovery for website with optional max pages limit"""
    from auto_a11y.core.website_manager import WebsiteManager
    from auto_a11y.core.task_runner import task_runner
    
    website = get_db().get_website(website_id)
    if not website:
        return jsonify({'error': ftl('common-website-not-found')}), 404

    # Get parameters from request
    data: dict[str, Any] = request.get_json() if request.is_json else {}
    max_pages_raw: str | None = data.get('max_pages') if request.is_json else request.form.get('max_pages')

    # Get project_user_ids (project-level test users)
    # Still accept 'website_user_ids' key name for backward compatibility with JavaScript
    user_ids_raw: list[str] | str = data.get('project_user_ids') or data.get('website_user_ids', [])

    # Convert to list if single value provided
    if isinstance(user_ids_raw, str):
        user_ids_list: list[str] = [user_ids_raw]
    else:
        user_ids_list = user_ids_raw

    # Default to guest only if no users specified
    if not user_ids_list:
        user_ids_list = ['']  # empty string represents guest/no login

    # Keep the old variable name for compatibility with existing code paths
    website_user_ids: list[str] = user_ids_list

    max_pages: int | None = None
    if max_pages_raw:
        try:
            max_pages = int(max_pages_raw)
            if max_pages <= 0:
                max_pages = None
            else:
                logger.info(f"Discovery will be limited to {max_pages} pages")
        except (ValueError, TypeError):
            max_pages = None
    
    try:
        # Browser availability is checked at launch time by BrowserManager,
        # which auto-detects the executable and can install it at runtime.

        # Get project to access stealth_mode setting
        project = get_db().get_project(website.project_id)

        # Create browser config with project-specific stealth_mode and headless settings
        browser_config = get_app_config().__dict__.copy()
        if project and project.config:
            browser_config['stealth_mode'] = project.config.get('stealth_mode', False)

            # Apply project-specific headless browser setting
            headless_setting = project.config.get('headless_browser', 'true')
            browser_config['BROWSER_HEADLESS'] = (headless_setting == 'true')
        else:
            browser_config['stealth_mode'] = False

        # Create website manager (with pdf_runner so PDFs found during the
        # crawl are auto-fetched and surfaced in the PDFs UI)
        website_manager = WebsiteManager(
            get_db(), browser_config, pdf_runner=get_pdf_runner()
        )

        # Get user info from session if available
        session_user_id = session.get('user_id') if session else None
        session_id_value = session.get('session_id') if session else None

        # Submit a single combined discovery task that will scrape with all users
        import uuid
        task_id = f'discovery_{website_id}_{uuid.uuid4().hex[:8]}'
        logger.info(f"Submitting discovery task with ID: {task_id} for {len(website_user_ids)} users")

        # Create a wrapper that handles the async execution properly
        def discovery_wrapper() -> object:
            import asyncio
            import nest_asyncio
            nest_asyncio.apply()

            logger.info(f"Discovery wrapper starting for website {website_id}, task_id: {task_id}, users: {len(website_user_ids)}")

            # Try to get the running loop, or create a new one
            try:
                loop = asyncio.get_running_loop()
                logger.info("Using existing event loop for discovery")
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                logger.info("Created new event loop for discovery")

            try:
                result = loop.run_until_complete(
                    website_manager.discover_pages(
                        website_id,
                        max_pages=max_pages,
                        job_id=task_id,
                        user_id=session_user_id,
                        session_id=session_id_value,
                        website_user_ids=website_user_ids
                    )
                )
                logger.info(f"Discovery wrapper completed, result job_id: {result.job_id if result else 'None'}")
                return result
            except Exception as e:
                logger.error(f"Error in discovery wrapper: {e}")
                raise
            finally:
                # Don't close the loop immediately - let it complete tasks
                try:
                    if not loop.is_running():
                        loop.close()
                        logger.info("Closed discovery event loop")
                except:
                    pass

        submitted_id = task_runner.submit_task(
            func=discovery_wrapper,
            args=(),
            task_id=task_id
        )

        logger.info(f"Discovery task submitted successfully with ID: {submitted_id}")

        # Build message
        user_count = len(website_user_ids)
        if user_count == 1:
            if website_user_ids[0]:
                user_info = get_db().get_project_user(website_user_ids[0])
                message = f'Page discovery started as {user_info.name_display if user_info else "user"}'
            else:
                message = f'Page discovery started as guest'
        else:
            message = f'Page discovery started with {user_count} users'

        if max_pages:
            message += f' (limited to {max_pages} pages)'

        return jsonify({
            'success': True,
            'message': message,
            'job_id': submitted_id,
            'max_pages': max_pages,
            'user_count': user_count,
            'status_url': url_for('websites.discovery_status', website_id=website_id, job_id=submitted_id)
        })
        
    except Exception as e:
        logger.error(f"Failed to start discovery: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'message': ftl('common-failed-to-start-page-discovery')
        }), 500


@websites_bp.route('/<website_id>/discovery-status')
def discovery_status(website_id: str) -> Response:
    """Check discovery job status with enhanced progress tracking"""
    from auto_a11y.core.website_manager import WebsiteManager
    
    job_id = request.args.get('job_id')
    
    # Get total page count for website
    total_pages = get_db().pages.count_documents({'website_id': website_id})
    
    if not job_id:
        # No specific job requested, check if any discovery is running
        # This helps with page refreshes where job_id might be lost
        return jsonify({
            'status': 'idle',
            'pages_found': total_pages,
            'message': f'{total_pages} pages in website'
        })
    
    # Get job status from database via WebsiteManager
    website_manager = WebsiteManager(get_db(), get_app_config().__dict__)
    job_status = website_manager.get_job_status(job_id)
    
    if not job_status:
        # Job not found - it might have completed or been cleaned up
        return jsonify({
            'status': 'completed',
            'pages_found': total_pages,
            'message': f'Discovery completed - found {total_pages} pages'
        })
    
    # Extract progress details from database-backed job
    status = job_status.get('status', 'unknown')
    progress = job_status.get('progress', {})
    
    logger.info(f"Job {job_id} status: {status}, progress: {progress}")
    
    # Extract details from progress
    details = progress.get('details', {})

    # Build detailed response
    response = {
        'status': status,
        'pages_found': details.get('pages_found', 0),
        'pages_failed': details.get('pages_failed', 0),
        'current_depth': details.get('current_depth', 0),
        'queue_size': details.get('queue_size', 0),
        'current_url': progress.get('message', ''),  # message contains current URL
        'error': job_status.get('error')
    }

    # Include last failure info if available
    if details.get('last_failed_url'):
        response['last_failed_url'] = details.get('last_failed_url')
        response['last_failed_reason'] = details.get('last_failed_reason', 'Unknown error')

    # Create informative message based on status
    if status == 'running':
        pages_found = details.get('pages_found', 0)
        pages_failed = details.get('pages_failed', 0)
        _queue_size = details.get('queue_size', 0)
        current_url = progress.get('message', '')
        if current_url:
            # Don't truncate URLs - users need to see what's being processed
            failed_info = f' ({pages_failed} failed)' if pages_failed > 0 else ''
            response['message'] = f'Found {pages_found} pages{failed_info}. Scanning: {current_url}'
        else:
            failed_info = f' ({pages_failed} failed)' if pages_failed > 0 else ''
            response['message'] = f'Found {pages_found} pages{failed_info}...'
    elif status == 'completed':
        pages_found = details.get("pages_found", 0)
        pages_failed = details.get("pages_failed", 0)
        failed_info = f', {pages_failed} failed' if pages_failed > 0 else ''
        response['message'] = f'Discovery completed - found {pages_found} pages{failed_info}'
    elif status == 'failed':
        response['message'] = f'Discovery failed: {job_status.get("error", "Unknown error")}'
    elif status == 'cancelled':
        response['message'] = 'Discovery was cancelled'
    elif status == 'cancelling':
        response['message'] = 'Cancelling discovery...'
    elif status == 'pending':
        response['message'] = 'Discovery is starting...'
    else:
        response['message'] = f'Discovery {status}'
    
    return jsonify(response)


@websites_bp.route('/<website_id>/cancel-discovery', methods=['POST'])
def cancel_discovery(website_id: str) -> Response | tuple[Response, int]:
    """Cancel an active discovery job"""
    from auto_a11y.core.website_manager import WebsiteManager
    
    # Log all request data for debugging
    logger.info(f"Cancel request received for website {website_id}")
    logger.info(f"  Request method: {request.method}")
    logger.info(f"  Request is_json: {request.is_json}")
    logger.info(f"  Request form data: {dict(request.form)}")
    logger.info(f"  Request json data: {request.json if request.is_json else 'N/A'}")
    logger.info(f"  Request values: {dict(request.values)}")
    
    # Try multiple ways to get job_id
    job_id = None
    if request.form and 'job_id' in request.form:
        job_id = request.form.get('job_id')
        logger.info(f"Got job_id from form: {job_id}")
    elif request.is_json and request.json and 'job_id' in request.json:
        job_id = request.json.get('job_id')
        logger.info(f"Got job_id from json: {job_id}")
    elif 'job_id' in request.values:
        job_id = request.values.get('job_id')
        logger.info(f"Got job_id from values: {job_id}")
    
    logger.info(f"Final job_id extracted: {job_id}")
    
    if not job_id:
        logger.error("No job_id provided in cancel request")
        return jsonify({'error': ftl('websites-job-id-required')}), 400

    try:
        logger.info(f"Attempting to cancel discovery job {job_id} for website {website_id}")
        
        # Cancel using the database-backed job manager
        website_manager = WebsiteManager(get_db(), get_app_config().__dict__)
        
        # Get user info for tracking who cancelled
        try:
            from flask import session
            user_id = session.get('user_id') if 'user_id' in session else None
        except:
            user_id = None
        
        # Request cancellation through the job manager
        cancelled = website_manager.cancel_discovery(job_id, user_id=user_id)
        
        if cancelled:
            logger.info(f"Successfully cancelled discovery job {job_id} for website {website_id}")
            return jsonify({
                'success': True,
                'message': ftl('websites-discovery-cancelled-successfully')
            })
        else:
            logger.warning(f"Could not cancel discovery job {job_id} - job not found or not cancellable")
            return jsonify({
                'success': False,
                'message': ftl('websites-job-not-found-or-already-completed')
            })
    except Exception as e:
        logger.error(f"Error cancelling discovery job: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e),
            'message': ftl('common-failed-to-cancel-discovery')
        }), 500


@websites_bp.route('/<website_id>/add-page', methods=['POST'])
def add_page(website_id: str) -> Response | tuple[Response, int]:
    """Manually add a page to website"""
    website = get_db().get_website(website_id)
    if not website:
        return jsonify({'error': ftl('common-website-not-found')}), 404

    url = request.form.get('url')
    if not url:
        return jsonify({'error': ftl('common-url-is-required')}), 400
    
    # Create page
    page = Page(
        website_id=website_id,
        url=url,
        priority=request.form.get('priority', 'normal'),
        discovered_from='manual'
    )
    
    page_id = get_db().create_page(page)
    
    return jsonify({
        'success': True,
        'page_id': page_id,
        'message': ftl('websites-page-added-successfully')
    })


@websites_bp.route('/<website_id>/test-all', methods=['POST'])
def test_all_pages(website_id: str) -> Response | tuple[Response, int]:
    """Start testing all pages in website using database-backed job management"""
    from auto_a11y.core.website_manager import WebsiteManager
    from auto_a11y.core.task_runner import task_runner
    import uuid

    website = get_db().get_website(website_id)
    if not website:
        return jsonify({'error': ftl('common-website-not-found')}), 404

    # Extract user IDs from request (array of user IDs, empty string for guest)
    data: dict[str, Any] = request.get_json() if request.is_json else {}

    # Get project_user_ids (project-level test users)
    # Still accept 'website_user_ids' key name for backward compatibility with JavaScript
    uids_raw: list[str] | str = data.get('project_user_ids') or data.get('website_user_ids', [])

    # Convert to list if single value provided
    if isinstance(uids_raw, str):
        uids_list: list[str] = [uids_raw]
    else:
        uids_list = uids_raw

    # Default to guest if no users specified
    if not uids_list:
        uids_list = ['']  # empty string represents guest/no login

    # Keep the old variable name for compatibility with existing code paths
    website_user_ids: list[str] = uids_list

    # Filter to untested pages only if requested
    untested_only: bool = data.get('untested_only', False)

    # Use latest_only=False and limit=0 to get all pages (consistent with stats shown in UI)
    pages = get_db().get_pages(website_id, latest_only=False, limit=0)
    # Allow testing of all pages, not just untested ones
    # Users may want to re-test pages to check for improvements
    testable_pages = [p for p in pages if p.status != PageStatus.TESTING]  # Exclude currently testing pages

    if untested_only:
        testable_pages = [p for p in testable_pages if p.status != PageStatus.TESTED]

    # Limit number of pages if max_pages specified
    max_pages: int | None = data.get('max_pages')
    if max_pages and max_pages > 0:
        testable_pages = testable_pages[:max_pages]

    if not testable_pages:
        return jsonify({
            'success': False,
            'message': ftl('websites-no-pages-available-for-testing-some-may-be')
        })

    try:
        # Get project to access stealth_mode setting
        project = get_db().get_project(website.project_id)

        # Create browser config with project-specific stealth_mode and headless settings
        browser_config = get_app_config().__dict__.copy()
        if project and project.config:
            browser_config['stealth_mode'] = project.config.get('stealth_mode', False)

            # Apply project-specific headless browser setting
            headless_setting = project.config.get('headless_browser', 'true')
            browser_config['BROWSER_HEADLESS'] = (headless_setting == 'true')
        else:
            browser_config['stealth_mode'] = False

        # Create website manager
        website_manager = WebsiteManager(get_db(), browser_config)

        # Get AI configuration
        ai_key = getattr(get_app_config(), 'CLAUDE_API_KEY', None)

        # Get user info from session if available
        session_user_id = session.get('user_id') if session else None
        session_id_value = session.get('session_id') if session else None

        # PDF audits are queued AFTER the HTML page-test loop completes
        # (see the testing_wrapper below). Running them concurrently with
        # Playwright fails with "Timeout should be used inside a task"
        # because ``nest_asyncio.apply()`` (called by the testing wrapper)
        # interferes with the per-request timeout context manager that
        # both Playwright and aiohttp use deep in their stacks. The
        # PdfRunner reference is captured here (Flask app context is
        # required to read it) and consumed inside the wrapper.
        pdf_runner = get_pdf_runner()
        db_for_audits = get_db()

        # Submit a SINGLE testing job that processes all users SEQUENTIALLY
        # This prevents browser session corruption from concurrent user testing
        total_tests = len(website_user_ids) * len(testable_pages)
        job_id = f'testing_{website_id}_{uuid.uuid4().hex[:8]}'
        logger.info(f"Submitting testing job with ID: {job_id} for {len(website_user_ids)} user(s)")

        # Create a wrapper that handles the async execution properly
        # Process all users sequentially within this single job
        def testing_wrapper() -> object:
            import asyncio
            import nest_asyncio
            nest_asyncio.apply()

            logger.info(f"Testing wrapper starting for website {website_id}, job_id: {job_id}, users: {len(website_user_ids)}")

            # Try to get the running loop, or create a new one
            try:
                loop = asyncio.get_running_loop()
                logger.info("Using existing event loop")
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                logger.info("Created new event loop")

            try:
                # Get page IDs for testing
                page_ids = [p.id for p in testable_pages if p.id is not None]
                last_result = None
                num_users = len(website_user_ids)

                # Process each user SEQUENTIALLY to avoid browser session corruption
                for idx, current_website_user_id in enumerate(website_user_ids):
                    user_label = current_website_user_id or 'guest'
                    logger.info(f"Testing user {idx + 1}/{num_users}: {user_label}")

                    # Convert empty string to None for guest testing
                    user_id_to_pass = current_website_user_id if current_website_user_id else None

                    # Always use the main job_id for UI tracking
                    # We'll update progress messages to show which user is being tested
                    current_job_id = job_id

                    # Hold off completing the testing job until PDF audits
                    # finish too — combined HTML-page + PDF progress is
                    # what the button shows.
                    try:
                        result = loop.run_until_complete(
                            website_manager.test_website(
                                website_id=website_id,
                                page_ids=page_ids,
                                job_id=current_job_id,
                                user_id=session_user_id,
                                session_id=session_id_value,
                                test_all=False,
                                take_screenshot=True,
                                run_ai_analysis=None,
                                ai_api_key=ai_key,
                                website_user_id=user_id_to_pass,
                                skip_completion=True,
                            )
                        )
                        last_result = result
                        logger.info(f"Completed testing for user: {user_label}")
                    except Exception as user_error:
                        logger.error(f"Error testing user {user_label}: {user_error}")
                        # Continue with next user even if one fails

                logger.info(f"Testing wrapper completed for all {num_users} users")

                # Now that the HTML page tests are done, queue PDF audits.
                # Running them earlier (concurrent with Playwright on this
                # same nest_asyncio'd loop) crashes the page tests.
                pdf_audit_job_ids: list[str] = []
                if pdf_runner is not None:
                    from auto_a11y.core.pdf_audit_job import (
                        queue_audits_for_website,
                    )

                    try:
                        pdf_audit_job_ids = queue_audits_for_website(
                            runner=pdf_runner,
                            db=db_for_audits,
                            website_id=website_id,
                            user_id=str(session_user_id)
                            if session_user_id
                            else 'anonymous',
                            session_id=str(session_id_value)
                            if session_id_value
                            else None,
                        )
                    except Exception as audit_err:
                        logger.warning(
                            "Failed to queue PDF audits for website %s: %s",
                            website_id,
                            audit_err,
                        )

                # Read the post-page-test progress so we can fold PDF
                # progress on top of it.
                from auto_a11y.core.job_manager import JobStatus as _JobStatus
                jm = website_manager.job_manager

                from typing import cast as _cast

                def _read_int(record: dict[str, Any] | None, key: str, default: int) -> int:
                    if record is None:
                        return default
                    progress_obj = record.get('progress')
                    if not isinstance(progress_obj, dict):
                        return default
                    progress_typed = _cast("dict[str, object]", progress_obj)
                    details_obj = progress_typed.get('details')
                    if not isinstance(details_obj, dict):
                        return default
                    details_typed = _cast("dict[str, object]", details_obj)
                    value = details_typed.get(key, default)
                    if isinstance(value, int):
                        return value
                    if isinstance(value, str):
                        try:
                            return int(value)
                        except ValueError:
                            return default
                    return default

                page_record = jm.get_job(job_id)
                pages_tested_final: int = _read_int(
                    page_record, 'pages_tested', len(page_ids)
                )
                pages_total_final: int = _read_int(
                    page_record, 'total_pages', len(page_ids)
                )
                page_details: dict[str, Any] = {
                    'pages_tested': pages_tested_final,
                    'total_pages': pages_total_final,
                }
                pdf_total: int = len(pdf_audit_job_ids)
                combined_total: int = pages_total_final + pdf_total
                terminal_states = {'completed', 'failed', 'cancelled'}

                # Poll until all PDF audit jobs reach a terminal state,
                # streaming combined progress into the testing job's
                # record so the button's existing poller picks it up.
                if pdf_audit_job_ids:
                    import time

                    while True:
                        done = 0
                        for aid in pdf_audit_job_ids:
                            rec = jm.get_job(aid)
                            if rec and rec.get('status') in terminal_states:
                                done += 1
                        combined_done = pages_tested_final + done
                        jm.update_job_status(
                            job_id=job_id,
                            status=_JobStatus.RUNNING,
                            progress={
                                'current': combined_done,
                                'total': combined_total,
                                'message': (
                                    f'Auditing PDFs: {done}/{pdf_total}'
                                ),
                                'details': {
                                    **page_details,
                                    'pages_tested': combined_done,
                                    'total_pages': combined_total,
                                    'pdf_audits_done': done,
                                    'pdf_audits_total': pdf_total,
                                },
                            },
                        )
                        if done >= pdf_total:
                            break
                        time.sleep(2)

                # Mark the testing job complete with combined counts so
                # the UI's "Testing complete" message reflects everything.
                jm.update_job_status(
                    job_id=job_id,
                    status=_JobStatus.COMPLETED,
                    progress={
                        'current': combined_total,
                        'total': combined_total,
                        'message': (
                            f'Testing complete: {pages_total_final} '
                            f'page(s), {pdf_total} PDF(s)'
                        ),
                        'details': {
                            **page_details,
                            'pages_tested': combined_total,
                            'total_pages': combined_total,
                            'pdf_audits_done': pdf_total,
                            'pdf_audits_total': pdf_total,
                        },
                    },
                )

                return last_result
            except Exception as e:
                logger.error(f"Error in testing wrapper: {e}")
                raise
            finally:
                try:
                    if not loop.is_running():
                        loop.close()
                        logger.info("Closed event loop")
                except:
                    pass

        # Submit single testing task for all users
        submitted_id = task_runner.submit_task(
            func=testing_wrapper,
            args=(),
            task_id=job_id
        )

        job_ids = [submitted_id]
        logger.info(f"Testing job submitted successfully with ID: {submitted_id} for {len(website_user_ids)} user(s)")

        # Build response message
        user_count = len(website_user_ids)
        if user_count == 1:
            if website_user_ids[0]:
                user_info = get_db().get_project_user(website_user_ids[0])
                message = f'Testing {len(testable_pages)} pages as {user_info.name_display if user_info else "user"}'
            else:
                message = f'Testing {len(testable_pages)} pages as guest'
        else:
            message = f'Testing {len(testable_pages)} pages with {user_count} users ({total_tests} total tests)'

        return jsonify({
            'success': True,
            'message': message,
            'job_id': job_ids[0],  # Return first job ID for backward compatibility
            'job_ids': job_ids,  # Return all job IDs
            'pages_queued': len(testable_pages),
            'user_count': user_count,
            'total_tests': total_tests,
            'status_url': url_for('websites.test_status', website_id=website_id)
        })
        
    except Exception as e:
        logger.error(f"Failed to start testing: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'message': ftl('websites-failed-to-start-testing')
        }), 500


@websites_bp.route('/<website_id>/cancel-testing', methods=['POST'])
def cancel_testing(website_id: str) -> Response | tuple[Response, int]:
    """Cancel an active testing job"""
    from auto_a11y.core.website_manager import WebsiteManager
    
    # Log all request data for debugging
    logger.info(f"Cancel testing request received for website {website_id}")
    logger.info(f"  Request form data: {dict(request.form)}")
    logger.info(f"  Request json data: {request.json if request.is_json else 'N/A'}")
    
    # Get job_id from request
    job_id = None
    if request.form and 'job_id' in request.form:
        job_id = request.form.get('job_id')
    elif request.is_json and request.json and 'job_id' in request.json:
        job_id = request.json.get('job_id')
    
    logger.info(f"Final job_id extracted: {job_id}")
    
    if not job_id:
        logger.error("No job_id provided in cancel testing request")
        return jsonify({'error': ftl('websites-job-id-required')}), 400

    try:
        logger.info(f"Attempting to cancel testing job {job_id} for website {website_id}")
        
        # Cancel using the database-backed job manager
        website_manager = WebsiteManager(get_db(), get_app_config().__dict__)
        
        # Get user info for tracking who cancelled
        user_id = session.get('user_id') if session else None
        
        # Request cancellation through the job manager
        cancelled = website_manager.cancel_testing(job_id, user_id=user_id)
        
        if cancelled:
            logger.info(f"Successfully cancelled testing job {job_id} for website {website_id}")
            return jsonify({
                'success': True,
                'message': ftl('websites-testing-cancelled-successfully')
            })
        else:
            logger.warning(f"Could not cancel testing job {job_id} - job not found or not cancellable")
            return jsonify({
                'success': False,
                'message': ftl('websites-job-not-found-or-already-completed')
            })
    except Exception as e:
        logger.error(f"Error cancelling testing job: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e),
            'message': ftl('websites-failed-to-cancel-testing')
        }), 500


@websites_bp.route('/<website_id>/documents')
def view_documents(website_id: str) -> str | Response:
    """View document references for a website"""
    website = get_db().get_website(website_id)
    if not website:
        flash(ftl('common-website-not-found'), 'error')
        return redirect(url_for('projects.list_projects'))

    project = get_db().get_project(website.project_id)

    # Get document references
    documents = get_db().get_document_references(website_id)
    
    # Separate internal and external
    internal_docs = [d for d in documents if d.is_internal]
    external_docs = [d for d in documents if not d.is_internal]
    
    return render_template('websites/documents.html',
                         website=website,
                         project=project,
                         internal_docs=internal_docs,
                         external_docs=external_docs,
                         total_docs=len(documents))


@websites_bp.route('/<website_id>/test-status')
def test_status(website_id: str) -> Response | tuple[Response, int]:
    """Check testing status using database-backed job management"""
    from auto_a11y.core.website_manager import WebsiteManager
    logger.warning(f"DEBUG test_status called for website {website_id}")
    
    # Get job_id from request args if provided
    job_id = request.args.get('job_id')
    logger.warning(f"DEBUG test_status: job_id={job_id}")
    
    # Get fresh website data from database
    website = get_db().get_website(website_id)
    if not website:
        logger.warning(f"DEBUG test_status: website not found!")
        return jsonify({'error': ftl('common-website-not-found')}), 404

    logger.warning(f"DEBUG test_status: website found, job_id={job_id}")
    
    # If a specific job_id is provided, get its status
    if job_id:
        website_manager = WebsiteManager(get_db(), get_app_config().__dict__)
        job_status = website_manager.get_job_status(job_id)
        
        if job_status:
            status = job_status.get('status', 'unknown')
            progress = job_status.get('progress', {})
            details = progress.get('details', {})
            message = progress.get('message', '')
            logger.warning(f"DEBUG test_status: job found, message='{message}'")
            
            return jsonify({
                'status': status,
                'job_id': job_id,
                'pages_tested': details.get('pages_tested', 0),
                'pages_passed': details.get('pages_passed', 0),
                'pages_failed': details.get('pages_failed', 0),
                'pages_skipped': details.get('pages_skipped', 0),
                'total_pages': details.get('total_pages', 0),
                'current_page': details.get('current_page', ''),
                'message': message,
                'error': job_status.get('error'),
                'all_complete': status in ['completed', 'failed', 'cancelled']
            })
    
    # Otherwise, get page-level status (for backward compatibility)
    logger.warning(f"DEBUG test_status: using page-level status (no job_id)")
    pages = get_db().get_pages(website_id)
    
    # Count pages by status
    total_pages = len(pages)
    tested_pages = sum(1 for p in pages if p.status == PageStatus.TESTED)
    testing_pages = sum(1 for p in pages if p.status == PageStatus.TESTING)
    queued_pages = sum(1 for p in pages if p.status == PageStatus.QUEUED)
    error_pages = sum(1 for p in pages if p.status == PageStatus.ERROR)
    
    # Check if all pages are complete (tested or error)
    all_complete = (testing_pages == 0 and queued_pages == 0)
    
    # Get last tested time - refresh from database to get latest
    if all_complete:
        # Refresh website data to get the updated last_tested
        website = get_db().get_website(website_id)
    
    last_tested = None
    if website and website.last_tested:
        last_tested = website.last_tested.strftime('%Y-%m-%d %H:%M')
    
    # Try to get user info from any active testing job for this website
    message = f'Testing: {testing_pages}/{total_pages}'
    logger.warning(f"DEBUG test_status: Looking for active jobs for {website_id}")
    try:
        website_manager = WebsiteManager(get_db(), get_app_config().__dict__)
        active_jobs = website_manager.job_manager.get_active_jobs(
            website_id=website_id
        )
        logger.warning(f"DEBUG test_status: Found {len(active_jobs)} active jobs")
        if active_jobs:
            # Get message from first active job (includes user label)
            job_progress = active_jobs[0].get('progress', {})
            job_message = job_progress.get('message', '')
            logger.warning(f"DEBUG test_status: job_message='{job_message}'")
            if job_message:
                message = job_message
    except Exception as e:
        logger.error(f"Could not get active job info: {e}")
    
    return jsonify({
        'total_pages': total_pages,
        'tested_pages': tested_pages,
        'testing_pages': testing_pages,
        'queued_pages': queued_pages,
        'error_pages': error_pages,
        'all_complete': all_complete,
        'last_tested': last_tested,
        'message': message
    })


@websites_bp.route('/<website_id>/discovery-history')
def view_discovery_history(website_id: str) -> str | Response:
    """View discovery history for a website"""
    website = get_db().get_website(website_id)
    if not website:
        flash(ftl('common-website-not-found'), 'error')
        return redirect(url_for('projects.list_projects'))

    project = get_db().get_project(website.project_id)

    # Get all discovery runs for this website
    discovery_runs = get_db().get_discovery_runs(website_id)
    
    return render_template('websites/discovery_history.html',
                         website=website,
                         project=project,
                         discovery_runs=discovery_runs)


@websites_bp.route('/<website_id>/discovery/<discovery_run_id>')
def view_discovery_run(website_id: str, discovery_run_id: str) -> str | Response:
    """View details of a specific discovery run"""
    website = get_db().get_website(website_id)
    if not website:
        flash(ftl('common-website-not-found'), 'error')
        return redirect(url_for('projects.list_projects'))

    project = get_db().get_project(website.project_id)

    # Get the discovery run
    discovery_run = get_db().get_discovery_run(discovery_run_id)
    if not discovery_run:
        flash(ftl('websites-discovery-run-not-found'), 'error')
        return redirect(url_for('websites.view_discovery_history', website_id=website_id))
    
    # Get pages from this discovery run
    pages = get_db().pages.find({
        'website_id': website_id,
        'discovery_run_id': discovery_run_id
    })
    pages_list = list(pages)
    
    # If there's a previous run, get comparison data
    comparison = None
    discovery_runs = get_db().get_discovery_runs(website_id)
    
    # Find the previous run (the one right after this one in the list, since list is sorted descending)
    previous_run = None
    for i, run in enumerate(discovery_runs):
        if run.id == discovery_run_id and i < len(discovery_runs) - 1:
            previous_run = discovery_runs[i + 1]
            break
    
    if previous_run and previous_run.id:
        comparison = get_db().compare_discoveries(
            website_id,
            previous_run.id,
            discovery_run_id
        )
    
    return render_template('websites/discovery_run.html',
                         website=website,
                         project=project,
                         discovery_run=discovery_run,
                         pages=pages_list,
                         comparison=comparison,
                         previous_run=previous_run)