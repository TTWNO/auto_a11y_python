"""
Website management routes
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session
from werkzeug.wrappers import Response
from auto_a11y.web.api.deprecation import deprecated
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
@deprecated(sunset="2026-09-01")  # successor TBD — pending #54 (/api/v1/websites?project_id=)
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
        website.scraping_config.spa_click_discovery = (
            request.form.get('spa_click_discovery') == 'on'
        )
        website.scraping_config.spa_ready_selector = (
            request.form.get('spa_ready_selector', '').strip()
        )

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
        # Snapshot the website's PDFs before the DB reset so we can wipe
        # their pdfMax cache directories. The viewer routes
        # (/pdfs/<id>/pdfmax-report, /issue-map) read those files directly
        # from disk without consulting PdfDocument.status, so a DB-only
        # reset would leave the user seeing stale issues.
        website_pdfs = get_db().get_pdf_documents(website_id=website_id, limit=10000)
        result = get_db().clear_website_test_results(website_id)

        storage = PdfStorage(base_dir=Path(get_app_config().PDF_STORAGE_DIR))
        for pdf in website_pdfs:
            storage.delete_audit_cache(pdf)

        flash(
            ftl(
                'websites-test-results-cleared',
                test_results=result['test_results_deleted'],
                pages=result['pages_reset'],
                pdfs=result['pdf_documents_reset'],
            ),
            'success',
        )
    except Exception as e:
        logger.error(f"Failed to clear test results for website {website_id}: {e}")
        flash(ftl('websites-failed-to-clear-test-results'), 'error')

    return redirect(url_for('websites.edit_website', website_id=website_id))


@websites_bp.route('/<website_id>/discover', methods=['POST'])
def discover_pages(website_id: str) -> Response | tuple[Response, int]:
    """Start page discovery for a website (with optional max-pages cap)."""
    from auto_a11y.core.test_run_service import (
        WebsiteNotFoundError,
        start_website_discovery,
    )

    data: dict[str, Any] = request.get_json() if request.is_json else {}
    max_pages_raw: str | int | None = (
        data.get('max_pages') if request.is_json
        else request.form.get('max_pages')
    )

    # Still accept the legacy `website_user_ids` form key from the
    # in-flight admin frontend; both are normalised inside the service.
    user_ids_raw: list[str] | str = (
        data.get('project_user_ids') or data.get('website_user_ids', [])
    )

    max_pages: int | None = None
    if max_pages_raw is not None and max_pages_raw != '':
        try:
            max_pages = int(max_pages_raw)
            if max_pages <= 0:
                max_pages = None
        except (ValueError, TypeError):
            max_pages = None

    session_user_id = session.get('user_id') if session else None
    session_id_value = session.get('session_id') if session else None

    try:
        handle = start_website_discovery(
            get_db(),
            get_app_config(),
            website_id,
            max_pages=max_pages,
            project_user_ids=user_ids_raw,
            user_id=session_user_id,
            session_id=session_id_value,
            pdf_runner=get_pdf_runner(),
        )
    except WebsiteNotFoundError:
        return jsonify({'error': ftl('common-website-not-found')}), 404
    except Exception as e:
        logger.error(f"Failed to start discovery: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'message': ftl('common-failed-to-start-page-discovery'),
        }), 500

    # Re-derive the user-facing message from the handle so the legacy
    # response shape stays byte-identical for the in-flight frontend.
    normalized_user_ids = (
        [user_ids_raw] if isinstance(user_ids_raw, str)
        else (user_ids_raw or [''])
    )
    if handle.user_count == 1:
        first_id = normalized_user_ids[0] if normalized_user_ids else ''
        if first_id:
            user_info = get_db().get_project_user(first_id)
            message = (
                f'Page discovery started as '
                f'{user_info.name_display if user_info else "user"}'
            )
        else:
            message = 'Page discovery started as guest'
    else:
        message = f'Page discovery started with {handle.user_count} users'
    if handle.max_pages:
        message += f' (limited to {handle.max_pages} pages)'

    return jsonify({
        'success': True,
        'message': message,
        'job_id': handle.job_id,
        'max_pages': handle.max_pages,
        'user_count': handle.user_count,
        'status_url': url_for(
            'websites.discovery_status',
            website_id=website_id,
            job_id=handle.job_id,
        ),
    })


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
    """Start testing all pages in website using database-backed job management."""
    from auto_a11y.core.test_run_service import (
        NoPagesToTestError,
        WebsiteNotFoundError,
        start_website_test_run,
    )

    data: dict[str, Any] = request.get_json() if request.is_json else {}

    # Get project_user_ids (project-level test users); legacy
    # ``website_user_ids`` form key still accepted by the service.
    uids_raw: list[str] | str = (
        data.get('project_user_ids') or data.get('website_user_ids', [])
    )

    untested_only: bool = data.get('untested_only', False)
    max_pages: int | None = data.get('max_pages')

    session_user_id = session.get('user_id') if session else None
    session_id_value = session.get('session_id') if session else None

    try:
        handle = start_website_test_run(
            get_db(),
            get_app_config(),
            website_id,
            project_user_ids=uids_raw,
            max_pages=max_pages,
            untested_only=untested_only,
            user_id=session_user_id,
            session_id=session_id_value,
            pdf_runner=get_pdf_runner(),
        )
    except WebsiteNotFoundError:
        return jsonify({'error': ftl('common-website-not-found')}), 404
    except NoPagesToTestError:
        return jsonify({
            'success': False,
            'message': ftl('websites-no-pages-available-for-testing-some-may-be'),
        })

    # Re-derive the per-user message envelope so the in-flight admin
    # frontend keeps seeing the same response shape.
    normalized_user_ids = (
        [uids_raw] if isinstance(uids_raw, str)
        else (uids_raw or [''])
    )
    if handle.user_count == 1:
        first_id = normalized_user_ids[0] if normalized_user_ids else ''
        if first_id:
            user_info = get_db().get_project_user(first_id)
            message = (
                f'Testing {handle.pages_queued} pages as '
                f'{user_info.name_display if user_info else "user"}'
            )
        else:
            message = f'Testing {handle.pages_queued} pages as guest'
    else:
        message = (
            f'Testing {handle.pages_queued} pages with '
            f'{handle.user_count} users ({handle.total_tests} total tests)'
        )

    return jsonify({
        'success': True,
        'message': message,
        'job_id': handle.job_id,
        'job_ids': [handle.job_id],
        'pages_queued': handle.pages_queued,
        'user_count': handle.user_count,
        'total_tests': handle.total_tests,
        'status_url': url_for('websites.test_status', website_id=website_id),
    })


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


@websites_bp.route('/<website_id>/manual/start', methods=['POST'])
def manual_session_start(
    website_id: str,
) -> Response | tuple[Response, int]:
    """Start (or restart) a visible browser session for manual discovery/testing.

    Opens a Chromium window driven by the user; the same window backs both
    "capture current URL" (manual discovery) and "test this page" (manual
    testing) until the user explicitly stops the session.
    """
    from auto_a11y.core import manual_session

    website = get_db().get_website(website_id)
    if not website:
        return jsonify({'error': ftl('common-website-not-found')}), 404

    payload: dict[str, Any] = request.get_json(silent=True) or {}
    initial_url: str = str(payload.get('url') or website.url)

    try:
        project = get_db().get_project(website.project_id)
        browser_config: dict[str, Any] = get_app_config().__dict__.copy()
        if project and project.config:
            browser_config['stealth_mode'] = project.config.get(
                'stealth_mode', False
            )
        info = manual_session.start_session(
            website_id=website_id,
            initial_url=initial_url,
            browser_config=browser_config,
        )
        return jsonify({
            'success': True,
            'session': {
                'website_id': info.website_id,
                'current_url': info.current_url,
                'current_title': info.current_title,
                'is_running': info.is_running,
                'started_at': info.started_at,
            },
        })
    except Exception as e:
        logger.error(
            f"Failed to start manual session for {website_id}: {e}",
            exc_info=True,
        )
        return jsonify({
            'success': False,
            'error': str(e),
            'message': ftl('websites-manual-session-start-failed'),
        }), 500


@websites_bp.route('/<website_id>/manual/status')
def manual_session_status(
    website_id: str,
) -> Response | tuple[Response, int]:
    """Return the current state of the manual session, if any."""
    from auto_a11y.core import manual_session

    if not get_db().get_website(website_id):
        return jsonify({'error': ftl('common-website-not-found')}), 404

    session_obj = manual_session.get_session(website_id)
    if session_obj is None:
        return jsonify({
            'success': True,
            'session': None,
        })
    info = session_obj.info()
    return jsonify({
        'success': True,
        'session': {
            'website_id': info.website_id,
            'current_url': info.current_url,
            'current_title': info.current_title,
            'is_running': info.is_running,
            'started_at': info.started_at,
        },
    })


@websites_bp.route('/<website_id>/manual/capture', methods=['POST'])
def manual_session_capture(
    website_id: str,
) -> Response | tuple[Response, int]:
    """Add the manual session's current URL to the website's page list."""
    from auto_a11y.core import manual_session

    website = get_db().get_website(website_id)
    if not website:
        return jsonify({'error': ftl('common-website-not-found')}), 404

    session_obj = manual_session.get_session(website_id)
    if session_obj is None or not session_obj.is_alive():
        return jsonify({
            'success': False,
            'message': ftl('websites-manual-session-not-running'),
        }), 400

    info = session_obj.info()
    if not info.current_url:
        return jsonify({
            'success': False,
            'message': ftl('websites-manual-session-no-url'),
        }), 400

    existing = get_db().get_page_by_url(website_id, info.current_url)
    if existing is not None:
        return jsonify({
            'success': True,
            'already_existed': True,
            'page_id': existing.id,
            'url': info.current_url,
            'title': info.current_title,
            'message': ftl('websites-manual-page-already-exists'),
        })

    page = Page(
        website_id=website_id,
        url=info.current_url,
        title=info.current_title or None,
        discovered_from='manual',
        status=PageStatus.DISCOVERED,
    )
    page_id = get_db().create_page(page)
    return jsonify({
        'success': True,
        'already_existed': False,
        'page_id': page_id,
        'url': info.current_url,
        'title': info.current_title,
        'message': ftl('websites-manual-page-captured'),
    })


@websites_bp.route('/<website_id>/manual/test', methods=['POST'])
def manual_session_test(
    website_id: str,
) -> Response | tuple[Response, int]:
    """Run the test suite against the manual session's currently-loaded page."""
    from auto_a11y.core import manual_session
    from auto_a11y.testing.test_runner import TestRunner
    from playwright.async_api import Page as PlaywrightPage

    website = get_db().get_website(website_id)
    if not website:
        return jsonify({'error': ftl('common-website-not-found')}), 404

    session_obj = manual_session.get_session(website_id)
    if session_obj is None or not session_obj.is_alive():
        return jsonify({
            'success': False,
            'message': ftl('websites-manual-session-not-running'),
        }), 400

    info = session_obj.info()
    if not info.current_url:
        return jsonify({
            'success': False,
            'message': ftl('websites-manual-session-no-url'),
        }), 400

    existing = get_db().get_page_by_url(website_id, info.current_url)
    if existing is None:
        page = Page(
            website_id=website_id,
            url=info.current_url,
            title=info.current_title or None,
            discovered_from='manual',
            status=PageStatus.DISCOVERED,
        )
        get_db().create_page(page)
    else:
        page = existing

    try:
        project = get_db().get_project(website.project_id)
        browser_config: dict[str, Any] = get_app_config().__dict__.copy()
        if project and project.config:
            browser_config['stealth_mode'] = project.config.get(
                'stealth_mode', False
            )

        test_runner = TestRunner(
            get_db(), browser_config, pdf_runner=get_pdf_runner()
        )
        ai_api_key = getattr(get_app_config(), 'CLAUDE_API_KEY', None)

        async def _run(live_page: PlaywrightPage) -> Any:
            return await test_runner.test_loaded_browser_page(
                page,
                live_page,
                take_screenshot=True,
                run_ai_analysis=False,
                ai_api_key=ai_api_key,
            )

        result = session_obj.run_with_page(_run, timeout=600.0)
        return jsonify({
            'success': True,
            'page_id': page.id,
            'url': info.current_url,
            'violations': result.violation_count,
            'warnings': result.warning_count,
            'info': result.info_count,
            'discoveries': result.discovery_count,
            'passes': result.pass_count,
            'message': ftl('websites-manual-test-complete'),
        })
    except Exception as e:
        logger.error(
            f"Manual test failed for website {website_id}: {e}",
            exc_info=True,
        )
        return jsonify({
            'success': False,
            'error': str(e),
            'message': ftl('websites-manual-test-failed'),
        }), 500


@websites_bp.route('/<website_id>/manual/stop', methods=['POST'])
def manual_session_stop(
    website_id: str,
) -> Response | tuple[Response, int]:
    """Close the manual session's browser window."""
    from auto_a11y.core import manual_session

    if not get_db().get_website(website_id):
        return jsonify({'error': ftl('common-website-not-found')}), 404

    stopped = manual_session.stop_session(website_id)
    return jsonify({
        'success': True,
        'stopped': stopped,
        'message': ftl(
            'websites-manual-session-stopped'
            if stopped
            else 'websites-manual-session-not-running'
        ),
    })


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