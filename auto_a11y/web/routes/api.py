"""
RESTful API routes
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from flask import Blueprint, Response, jsonify, request
from flask_login import current_user
from auto_a11y.models import (
    Page,
    PageStatus,
    Project,
    ProjectStatus,
    ScriptStateDefinition,
    TestStateMatrix,
)
from auto_a11y.models.app_user import UserRole
from auto_a11y.pdf.storage import PdfStorage
from auto_a11y.web.routes.auth import project_role_required
from auto_a11y.core.job_manager import JobManager, JobStatus, JobType
from auto_a11y.web.typed_app import get_db, get_app_config, get_test_config
from datetime import datetime
import logging

logger = logging.getLogger(__name__)
api_bp = Blueprint('api', __name__)


# Fixture Test Status API

@api_bp.route('/fixture-tests/status', methods=['GET'])
def get_fixture_test_status() -> tuple[Response, int] | Response:
    """Get fixture test status for all tests"""
    try:
        # Get test configuration
        test_config = get_test_config()

        # Get all test statuses
        statuses = test_config.get_all_test_statuses()
        
        # Get fixture run summary
        summary = None
        if test_config.fixture_validator:
            summary = test_config.fixture_validator.get_fixture_run_summary()
        
        # Get passing tests
        passing_tests: set[str] = set()
        if test_config.fixture_validator:
            passing_tests = test_config.fixture_validator.get_passing_tests()

        # Calculate category counts
        all_pass_count = 0
        partial_pass_count = 0
        all_fail_count = 0

        for _error_code, status in statuses.items():
            category = status.get('status_category', 'all_fail')
            if category == 'all_pass':
                all_pass_count += 1
            elif category == 'partial_pass':
                partial_pass_count += 1
            else:
                all_fail_count += 1

        return jsonify({
            'success': True,
            'debug_mode': test_config.debug_mode,
            'fixture_run_summary': summary,
            'passing_tests': list(passing_tests),
            'test_statuses': statuses,
            'total_tests': len(statuses),
            'passing_count': len(passing_tests),
            'all_pass_count': all_pass_count,
            'partial_pass_count': partial_pass_count,
            'all_fail_count': all_fail_count
        })
    except Exception as e:
        logger.error(f"Error getting fixture test status: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@api_bp.route('/fixture-tests/check/<error_code>', methods=['GET'])
def check_test_availability(error_code: str) -> tuple[Response, int] | Response:
    """Check if a specific test is available based on fixture status"""
    try:
        test_config = get_test_config()

        # Get fixture status for this test
        status = test_config.get_test_fixture_status(error_code)
        
        return jsonify({
            'success': True,
            'error_code': error_code,
            'available': status.get('available', False),
            'passed_fixture': status.get('passed_fixture', False),
            'debug_override': status.get('debug_override', False),
            'fixture_path': status.get('fixture_path', ''),
            'tested_at': status.get('tested_at')
        })
    except Exception as e:
        logger.error(f"Error checking test availability for {error_code}: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# Projects API

@api_bp.route('/projects', methods=['GET'])
def get_projects() -> tuple[Response, int] | Response:
    """Get all projects"""
    page = int(request.args.get('page', 1))
    limit = int(request.args.get('limit', 20))
    status = request.args.get('status')

    skip = (page - 1) * limit

    if current_user.is_authenticated and not getattr(current_user, 'is_superadmin', False):
        projects = get_db().get_projects_for_user(str(current_user.get_id()))
        if status:
            try:
                status_enum = ProjectStatus(status)
                projects = [p for p in projects if p.status == status_enum]
            except ValueError:
                return jsonify({'error': 'Invalid status value'}), 400
    elif status:
        try:
            status_enum = ProjectStatus(status)
            projects = get_db().get_projects(status=status_enum, limit=limit, skip=skip)
        except ValueError:
            return jsonify({'error': 'Invalid status value'}), 400
    else:
        projects = get_db().get_projects(limit=limit, skip=skip)
    
    return jsonify({
        'projects': [p.to_dict() for p in projects],
        'pagination': {
            'page': page,
            'limit': limit,
            'total': get_db().projects.count_documents({})
        }
    })


@api_bp.route('/projects', methods=['POST'])
def create_project() -> tuple[Response, int]:
    """Create new project"""
    data = request.get_json()
    
    if not data or 'name' not in data:
        return jsonify({'error': 'Project name is required'}), 400
    
    # Check if project exists
    existing = get_db().projects.find_one({'name': data['name']})
    if existing:
        return jsonify({'error': f'Project {data["name"]} already exists'}), 409
    
    project = Project(
        name=data['name'],
        description=data.get('description', ''),
        status=ProjectStatus.ACTIVE,
        config=data.get('config', {})
    )
    
    project_id = get_db().create_project(project)
    # Auto-add creator as project admin
    if current_user.is_authenticated:
        admin_group = get_db().get_group_by_name('Admin')
        get_db().add_project_member(
            project_id, str(current_user.get_id()), [admin_group.id] if admin_group and admin_group.id else []
        )

    return jsonify({
        'id': project_id,
        'message': f'Project created successfully'
    }), 201


@api_bp.route('/projects/<project_id>', methods=['GET'])
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT)
def get_project(project_id: str) -> tuple[Response, int] | Response:
    """Get project by ID"""
    project = get_db().get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    
    storage = PdfStorage(base_dir=Path(get_app_config().PDF_STORAGE_DIR))
    stats = get_db().get_project_stats(project_id, pdf_storage=storage)

    response = project.to_dict()
    response['statistics'] = stats
    
    return jsonify(response)


@api_bp.route('/projects/<project_id>', methods=['PUT'])
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR)
def update_project(project_id: str) -> tuple[Response, int] | Response:
    """Update project"""
    project = get_db().get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    
    data = request.get_json()
    
    if 'name' in data:
        project.name = data['name']
    if 'description' in data:
        project.description = data['description']
    if 'status' in data:
        try:
            project.status = ProjectStatus(data['status'])
        except ValueError:
            return jsonify({'error': 'Invalid status value'}), 400
    if 'config' in data:
        project.config.update(data['config'])
    
    if get_db().update_project(project):
        return jsonify({'message': 'Project updated successfully'})
    else:
        return jsonify({'error': 'Failed to update project'}), 500


@api_bp.route('/projects/<project_id>', methods=['DELETE'])
@project_role_required(UserRole.ADMIN)
def delete_project(project_id: str) -> tuple[Response, int]:
    """Delete project"""
    project = get_db().get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    
    if get_db().delete_project(project_id):
        return jsonify({'message': 'Project deleted successfully'}), 204
    else:
        return jsonify({'error': 'Failed to delete project'}), 500


# Websites API

# Website CRUD endpoints moved to the REST block at the bottom of this
# file (search for "Websites (REST shape — uses the @api_endpoint
# scaffolding)"). The legacy stubs at this position were never wired
# into the frontend and used a different error envelope from the rest of
# /api/v1; consolidating into the proper RFC 7807 shape keeps the
# /api/v1/websites surface consistent.


# Pages API

# Page CRUD endpoints moved to the REST block at the bottom of this file
# (search for "Pages (REST shape — uses the @api_endpoint scaffolding)").
# The legacy stubs at this position were never wired into the frontend
# and lacked auth guards on list/create — consolidating into the
# `@api_endpoint` shape closes that gap and adds PUT/PATCH/DELETE.


@api_bp.route('/pages/<page_id>/test', methods=['POST'])
@api_bp.route('/pages/<page_id>/test-runs', methods=['POST'])
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR)
def test_page(page_id: str) -> tuple[Response, int]:
    """Queue an accessibility test run for a page.

    Both ``POST /api/v1/pages/<id>/test`` (legacy, kept for the
    in-flight admin frontend) and the canonical
    ``POST /api/v1/pages/<id>/test-runs`` route here. They share a
    handler so the response shape stays identical until the frontend
    migration retires the old URL.

    The actual orchestration lives in
    :mod:`auto_a11y.core.test_run_service` so the legacy HTML route
    and this REST endpoint dispatch through the same code path.
    """
    from auto_a11y.core.test_run_service import (
        BrowserDisabledError,
        BrowserRemoteError,
        PageNotFoundError,
        start_page_test_run,
    )

    data: dict[str, Any] = request.get_json() or {}
    enable_multi_state: bool = bool(data.get('enable_multi_state', True))
    website_user_id_raw = data.get('website_user_id')
    website_user_id: str | None = (
        website_user_id_raw if isinstance(website_user_id_raw, str) else None
    )

    try:
        handle = start_page_test_run(
            get_db(),
            get_app_config(),
            page_id,
            enable_multi_state=enable_multi_state,
            website_user_id=website_user_id,
        )
    except BrowserDisabledError as exc:
        return jsonify({'error': str(exc)}), 503
    except BrowserRemoteError as exc:
        return jsonify({'error': str(exc)}), 503
    except PageNotFoundError:
        return jsonify({'error': 'Page not found'}), 404

    return jsonify({
        'job_id': handle.job_id,
        'page_id': handle.page_id,
        'multi_state': handle.multi_state,
        'status': 'queued',
        'message': 'Test job queued successfully',
    }), 202


# Test Results API

@api_bp.route('/test-results/<result_id>', methods=['GET'])
def get_test_result(result_id: str) -> tuple[Response, int] | Response:
    """Get test result by ID"""
    result = get_db().get_test_result(result_id)
    if not result:
        return jsonify({'error': 'Test result not found'}), 404
    
    return jsonify(result.to_dict())


@api_bp.route('/pages/<page_id>/test-results', methods=['GET'])
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT)
def get_page_test_results(page_id: str) -> tuple[Response, int] | Response:
    """Get test results for page"""
    page = get_db().get_page(page_id)
    if not page:
        return jsonify({'error': 'Page not found'}), 404
    
    results = get_db().get_test_results(page_id=page_id)
    
    return jsonify({
        'results': [r.to_dict() for r in results]
    })


# Batch Operations

@api_bp.route('/websites/<website_id>/discover', methods=['POST'])
@api_bp.route('/websites/<website_id>/discoveries', methods=['POST'])
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR)
def discover_pages(website_id: str) -> tuple[Response, int]:
    """Queue a page-discovery crawl for a website.

    Both the legacy ``/discover`` URL and the canonical
    ``/discoveries`` URL share this handler; they emit the same
    response shape until the frontend migration retires the legacy
    name.

    The actual orchestration (browser-config merge, async wrapping,
    task-runner submission) lives in
    :mod:`auto_a11y.core.test_run_service` so the HTML route in
    ``websites.py`` and this REST endpoint dispatch through the same
    code path.
    """
    from auto_a11y.core.test_run_service import (
        WebsiteNotFoundError,
        start_website_discovery,
    )
    from auto_a11y.web.typed_app import get_pdf_runner as _get_pdf_runner

    data: dict[str, Any] = request.get_json() or {}
    max_pages_raw = data.get('max_pages')
    max_pages: int | None = None
    if max_pages_raw is not None and max_pages_raw != '':
        try:
            max_pages = int(max_pages_raw)
            if max_pages <= 0:
                max_pages = None
        except (ValueError, TypeError):
            max_pages = None

    def _widen_to_str_list(value: Any) -> list[str]:
        # Routing ``value`` through an ``Any``-typed parameter drops
        # pyright's ``list[Unknown]`` narrowing from the outer
        # isinstance check, so iteration yields properly-typed items.
        # Same trick as ``_iter_to_any_list`` further down this file.
        items: list[Any] = []
        for item in value:
            items.append(item)
        return [str(item) for item in items]

    user_ids_raw_value: Any = (
        data.get('project_user_ids') or data.get('website_user_ids')
    )
    user_ids_raw: list[str] | str | None
    if isinstance(user_ids_raw_value, list):
        user_ids_raw = _widen_to_str_list(user_ids_raw_value)
    elif isinstance(user_ids_raw_value, str):
        user_ids_raw = user_ids_raw_value
    else:
        user_ids_raw = None

    try:
        handle = start_website_discovery(
            get_db(),
            get_app_config(),
            website_id,
            max_pages=max_pages,
            project_user_ids=user_ids_raw,
            pdf_runner=_get_pdf_runner(),
        )
    except WebsiteNotFoundError:
        return jsonify({'error': 'Website not found'}), 404

    return jsonify({
        'job_id': handle.job_id,
        'website_id': handle.website_id,
        'max_pages': handle.max_pages,
        'user_count': handle.user_count,
        'status': 'started',
        'message': 'Page discovery started',
    }), 202


@api_bp.route('/websites/<website_id>/test', methods=['POST'])
@api_bp.route('/websites/<website_id>/test-runs', methods=['POST'])
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR)
def test_website(website_id: str) -> tuple[Response, int]:
    """Queue a batch test run for every page on a website.

    Both the legacy ``/test`` URL and the canonical ``/test-runs`` URL
    share this handler so the response shape stays identical until
    the frontend migration retires the legacy name.

    The orchestration (sequential per-user page tests, PDF audit
    queueing, combined progress reporting) lives in
    :mod:`auto_a11y.core.test_run_service` so both the HTML route in
    ``websites.py`` and this REST endpoint dispatch through the same
    code path.
    """
    from auto_a11y.core.test_run_service import (
        NoPagesToTestError,
        WebsiteNotFoundError,
        start_website_test_run,
    )
    from auto_a11y.web.typed_app import get_pdf_runner as _get_pdf_runner

    data: dict[str, Any] = request.get_json() or {}

    max_pages_raw = data.get('max_pages')
    max_pages: int | None = None
    if max_pages_raw is not None and max_pages_raw != '':
        try:
            max_pages = int(max_pages_raw)
            if max_pages <= 0:
                max_pages = None
        except (ValueError, TypeError):
            max_pages = None

    untested_only: bool = bool(data.get('untested_only', False))

    def _widen_to_str_list(value: Any) -> list[str]:
        items: list[Any] = []
        for item in value:
            items.append(item)
        return [str(item) for item in items]

    user_ids_raw_value: Any = (
        data.get('project_user_ids') or data.get('website_user_ids')
    )
    user_ids_raw: list[str] | str | None
    if isinstance(user_ids_raw_value, list):
        user_ids_raw = _widen_to_str_list(user_ids_raw_value)
    elif isinstance(user_ids_raw_value, str):
        user_ids_raw = user_ids_raw_value
    else:
        user_ids_raw = None

    try:
        handle = start_website_test_run(
            get_db(),
            get_app_config(),
            website_id,
            project_user_ids=user_ids_raw,
            max_pages=max_pages,
            untested_only=untested_only,
            pdf_runner=_get_pdf_runner(),
        )
    except WebsiteNotFoundError:
        return jsonify({'error': 'Website not found'}), 404
    except NoPagesToTestError:
        return jsonify({'error': 'No pages to test'}), 400

    return jsonify({
        'job_id': handle.job_id,
        'website_id': handle.website_id,
        'pages_queued': handle.pages_queued,
        'user_count': handle.user_count,
        'total_tests': handle.total_tests,
        'status': 'queued',
        'message': f'Batch testing queued for {handle.pages_queued} pages',
    }), 202




# Health Check

@api_bp.route('/health', methods=['GET'])
def health_check() -> Response:
    """API health check"""
    try:
        # Check database connection
        get_db().client.server_info()
        db_status = 'healthy'
    except:
        db_status = 'unhealthy'
    
    return jsonify({
        'status': 'healthy' if db_status == 'healthy' else 'degraded',
        'database': db_status,
        'timestamp': datetime.now().isoformat()
    })


# Jobs API

@api_bp.route('/jobs/stats', methods=['GET'])
def get_job_stats() -> tuple[Response, int] | Response:
    """Get job statistics"""
    try:
        job_manager = JobManager(get_db())
        
        # Get overall statistics
        stats = job_manager.get_job_statistics(hours=24)
        
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Error getting job stats: {e}")
        return jsonify({'error': 'Failed to get job statistics'}), 500


@api_bp.route('/jobs/clear-all', methods=['POST'])
def clear_all_jobs() -> tuple[Response, int] | Response:
    """Clear all running and pending jobs - emergency reset"""
    try:
        job_manager = JobManager(get_db())
        
        # Clear all running jobs
        running_result = job_manager.collection.update_many(
            {'status': {'$in': [JobStatus.RUNNING.value, JobStatus.CANCELLING.value]}},
            {
                '$set': {
                    'status': JobStatus.CANCELLED.value,
                    'completed_at': datetime.now(),
                    'error': 'Job cleared by administrator'
                }
            }
        )
        
        # Clear all pending jobs
        pending_result = job_manager.collection.update_many(
            {'status': JobStatus.PENDING.value},
            {
                '$set': {
                    'status': JobStatus.CANCELLED.value,
                    'completed_at': datetime.now(),
                    'error': 'Job cleared by administrator'
                }
            }
        )
        
        # Also reset page statuses that are stuck in QUEUED or TESTING states
        pages_result = get_db().pages.update_many(
            {'status': {'$in': [PageStatus.QUEUED.value, PageStatus.TESTING.value]}},
            {
                '$set': {
                    'status': PageStatus.DISCOVERED.value,
                    'error_reason': 'Job cleared by administrator'
                }
            }
        )
        
        total_cleared = running_result.modified_count + pending_result.modified_count
        
        logger.info(f"Cleared {total_cleared} jobs (running: {running_result.modified_count}, pending: {pending_result.modified_count})")
        logger.info(f"Reset {pages_result.modified_count} pages from queued/testing to discovered status")
        
        return jsonify({
            'success': True,
            'cleared_count': total_cleared,
            'running_cleared': running_result.modified_count,
            'pending_cleared': pending_result.modified_count,
            'pages_reset': pages_result.modified_count,
            'message': f'Successfully cleared {total_cleared} jobs and reset {pages_result.modified_count} pages'
        })
        
    except Exception as e:
        logger.error(f"Error clearing all jobs: {e}")
        return jsonify({'error': f'Failed to clear jobs: {str(e)}'}), 500


@api_bp.route('/jobs/clear-stale', methods=['POST'])
def clear_stale_jobs() -> tuple[Response, int] | Response:
    """Clear stale jobs that have been running for too long"""
    try:
        job_manager = JobManager(get_db())
        
        # Clear jobs running for more than 24 hours
        cleared_count = job_manager.cleanup_stale_jobs(stale_after_hours=24)
        
        # Also reset old pages stuck in QUEUED or TESTING states for more than 24 hours
        from datetime import timedelta
        stale_time = datetime.now() - timedelta(hours=24)
        pages_result = get_db().pages.update_many(
            {
                'status': {'$in': [PageStatus.QUEUED.value, PageStatus.TESTING.value]},
                '$or': [
                    {'last_tested': {'$lt': stale_time}},
                    {'last_tested': None, 'discovered_at': {'$lt': stale_time}}
                ]
            },
            {
                '$set': {
                    'status': PageStatus.DISCOVERED.value,
                    'error_reason': 'Stale job cleared by administrator'
                }
            }
        )
        
        logger.info(f"Cleared {cleared_count} stale jobs")
        logger.info(f"Reset {pages_result.modified_count} stale pages")
        
        return jsonify({
            'success': True,
            'cleared_count': cleared_count,
            'pages_reset': pages_result.modified_count,
            'message': f'Successfully cleared {cleared_count} stale jobs and reset {pages_result.modified_count} pages'
        })
        
    except Exception as e:
        logger.error(f"Error clearing stale jobs: {e}")
        return jsonify({'error': f'Failed to clear stale jobs: {str(e)}'}), 500


@api_bp.route('/jobs/active', methods=['GET'])
def get_active_jobs() -> tuple[Response, int] | Response:
    """Get list of active jobs"""
    try:
        job_manager = JobManager(get_db())
        
        # Get active jobs
        active_jobs = list(job_manager.collection.find(
            {'status': {'$in': [JobStatus.RUNNING.value, JobStatus.PENDING.value, JobStatus.CANCELLING.value]}},
            {'_id': 0}  # Exclude MongoDB _id from response
        ).sort('created_at', -1).limit(100))
        
        return jsonify({
            'jobs': active_jobs,
            'count': len(active_jobs)
        })
        
    except Exception as e:
        logger.error(f"Error getting active jobs: {e}")
        return jsonify({'error': 'Failed to get active jobs'}), 500


@api_bp.route('/jobs/cleanup-page-counts', methods=['POST'])
def cleanup_page_counts() -> tuple[Response, int] | Response:
    """Clean up violation counts for pages that haven't been tested"""
    try:
        # Reset violation/warning/info counts for all pages that aren't in TESTED status
        result = get_db().pages.update_many(
            {'status': {'$ne': PageStatus.TESTED.value}},
            {
                '$set': {
                    'violation_count': 0,
                    'warning_count': 0,
                    'info_count': 0,
                    'discovery_count': 0,
                    'pass_count': 0,
                    'test_duration_ms': None
                }
            }
        )
        
        logger.info(f"Cleaned up counts for {result.modified_count} untested pages")
        
        return jsonify({
            'success': True,
            'pages_cleaned': result.modified_count,
            'message': f'Reset counts for {result.modified_count} untested pages'
        })
        
    except Exception as e:
        logger.error(f"Error cleaning up page counts: {e}")
        return jsonify({'error': f'Failed to clean up page counts: {str(e)}'}), 500


# Multi-State Testing API Endpoints

@api_bp.route('/test-results/<result_id>/states', methods=['GET'])
def get_test_result_states(result_id: str) -> tuple[Response, int] | Response:
    """
    Get all related state test results for a given result

    Returns all test results from the same testing session, showing different
    page states (before/after scripts, button states, etc.)
    """
    try:
        # Get the result
        result = get_db().get_test_result(result_id)
        if not result:
            return jsonify({'error': 'Test result not found'}), 404

        # Get related results
        related_results = get_db().get_related_test_results(result_id)

        # Include the original result
        all_results = [result] + related_results

        # Sort by state_sequence
        all_results.sort(key=lambda r: r.state_sequence)

        # Serialize results
        results_data: list[dict[str, Any]] = []
        for r in all_results:
            state_info = {
                'result_id': str(r.mongo_id) if r.mongo_id else None,
                'state_sequence': r.state_sequence,
                'page_state': r.page_state,
                'session_id': r.session_id,
                'test_date': r.test_date.isoformat() if r.test_date else None,
                'violation_count': r.violation_count,
                'warning_count': r.warning_count,
                'info_count': r.info_count,
                'pass_count': r.pass_count,
                'duration_ms': r.duration_ms
            }
            results_data.append(state_info)

        return jsonify({
            'success': True,
            'result_id': result_id,
            'total_states': len(results_data),
            'states': results_data
        })

    except Exception as e:
        logger.error(f"Error getting test result states: {e}")
        return jsonify({'error': f'Failed to get test result states: {str(e)}'}), 500


@api_bp.route('/pages/<page_id>/test-states', methods=['GET'])
def get_page_test_states(page_id: str) -> tuple[Response, int] | Response:
    """
    Get latest test results per state for a page

    Shows the most recent test result for each state (initial, after script, etc.)
    """
    try:
        # Check page exists
        page = get_db().get_page(page_id)
        if not page:
            return jsonify({'error': 'Page not found'}), 404

        # Get latest results per state
        state_results = get_db().get_latest_test_results_per_state(page_id)

        # Serialize results
        states_data: dict[int, dict[str, Any]] = {}
        for state_seq, result in state_results.items():
            states_data[state_seq] = {
                'result_id': str(result.mongo_id) if result.mongo_id else None,
                'state_sequence': state_seq,
                'page_state': result.page_state,
                'session_id': result.session_id,
                'test_date': result.test_date.isoformat() if result.test_date else None,
                'violation_count': result.violation_count,
                'warning_count': result.warning_count,
                'info_count': result.info_count,
                'pass_count': result.pass_count,
                'duration_ms': result.duration_ms
            }

        return jsonify({
            'success': True,
            'page_id': page_id,
            'page_url': page.url,
            'total_states': len(states_data),
            'states': states_data
        })

    except Exception as e:
        logger.error(f"Error getting page test states: {e}")
        return jsonify({'error': f'Failed to get page test states: {str(e)}'}), 500


@api_bp.route('/pages/<page_id>/test-sessions', methods=['GET'])
def get_page_test_sessions(page_id: str) -> tuple[Response, int] | Response:
    """
    Get all test sessions for a page with their state counts

    Shows all testing sessions and how many states were tested in each
    """
    try:
        # Check page exists
        page = get_db().get_page(page_id)
        if not page:
            return jsonify({'error': 'Page not found'}), 404

        # Get all test results for page
        all_results = get_db().get_test_results(page_id=page_id)

        # Group by session
        sessions: dict[str, dict[str, Any]] = {}
        for result in all_results:
            session_id = result.session_id or 'single_state'

            if session_id not in sessions:
                sessions[session_id] = {
                    'session_id': session_id,
                    'test_date': result.test_date,
                    'states': [],
                    'total_violations': 0,
                    'total_warnings': 0
                }

            sessions[session_id]['states'].append({
                'result_id': str(result.mongo_id) if result.mongo_id else None,
                'state_sequence': result.state_sequence,
                'state_description': result.page_state.get('description') if result.page_state else None,
                'violation_count': result.violation_count,
                'warning_count': result.warning_count
            })

            sessions[session_id]['total_violations'] += result.violation_count
            sessions[session_id]['total_warnings'] += result.warning_count

        # Convert to list and sort by date
        sessions_list = list(sessions.values())
        sessions_list.sort(key=lambda s: s['test_date'], reverse=True)

        # Sort states within each session
        for session in sessions_list:
            states_list: list[dict[str, Any]] = session['states']
            states_list.sort(key=lambda s: s['state_sequence'])
            session['state_count'] = len(session['states'])
            session['test_date'] = session['test_date'].isoformat() if session['test_date'] else None

        return jsonify({
            'success': True,
            'page_id': page_id,
            'page_url': page.url,
            'total_sessions': len(sessions_list),
            'sessions': sessions_list
        })

    except Exception as e:
        logger.error(f"Error getting page test sessions: {e}")
        return jsonify({'error': f'Failed to get page test sessions: {str(e)}'}), 500


@api_bp.route('/test-results/compare', methods=['POST'])
def compare_test_results() -> tuple[Response, int] | Response:
    """
    Compare two test results (typically from different states)

    Request body:
    {
        "result_id_1": "...",
        "result_id_2": "..."
    }

    Returns:
    - Violations that appeared in result_2 (new violations)
    - Violations that disappeared from result_1 (fixed violations)
    - Violations that exist in both (persistent violations)
    """
    try:
        data = request.get_json()
        result_id_1 = data.get('result_id_1')
        result_id_2 = data.get('result_id_2')

        if not result_id_1 or not result_id_2:
            return jsonify({'error': 'Both result_id_1 and result_id_2 are required'}), 400

        # Get results
        result1 = get_db().get_test_result(result_id_1)
        result2 = get_db().get_test_result(result_id_2)

        if not result1 or not result2:
            return jsonify({'error': 'One or both test results not found'}), 404

        # Get violation IDs (using issue_id for comparison)
        violations1_ids = {v.id for v in result1.violations}
        violations2_ids = {v.id for v in result2.violations}

        # Calculate differences
        new_violations = [v for v in result2.violations if v.id not in violations1_ids]
        fixed_violations = [v for v in result1.violations if v.id not in violations2_ids]
        persistent_violations = [v for v in result2.violations if v.id in violations1_ids]

        # Serialize violations
        def serialize_violation(v: Any) -> dict[str, Any]:
            return {
                'id': v.id,
                'impact': v.impact.value if hasattr(v.impact, 'value') else v.impact,
                'touchpoint': v.touchpoint,
                'description': v.description,
                'element': v.element
            }

        comparison = {
            'result_1': {
                'id': result_id_1,
                'state_sequence': result1.state_sequence,
                'state_description': result1.page_state.get('description') if result1.page_state else None,
                'violation_count': result1.violation_count,
                'test_date': result1.test_date.isoformat() if result1.test_date else None
            },
            'result_2': {
                'id': result_id_2,
                'state_sequence': result2.state_sequence,
                'state_description': result2.page_state.get('description') if result2.page_state else None,
                'violation_count': result2.violation_count,
                'test_date': result2.test_date.isoformat() if result2.test_date else None
            },
            'new_violations': [serialize_violation(v) for v in new_violations],
            'fixed_violations': [serialize_violation(v) for v in fixed_violations],
            'persistent_violations': [serialize_violation(v) for v in persistent_violations],
            'summary': {
                'new_count': len(new_violations),
                'fixed_count': len(fixed_violations),
                'persistent_count': len(persistent_violations),
                'net_change': result2.violation_count - result1.violation_count
            }
        }

        return jsonify({
            'success': True,
            'comparison': comparison
        })

    except Exception as e:
        logger.error(f"Error comparing test results: {e}")
        return jsonify({'error': f'Failed to compare test results: {str(e)}'}), 500


@api_bp.route('/health/pdf', methods=['GET'])
def pdf_health() -> tuple[Response, int]:
    """Report PDF-audit subsystem health."""
    from pathlib import Path
    from auto_a11y.pdf.health import check_pdf_health
    from auto_a11y.web.typed_app import get_app_config

    cfg = get_app_config()
    health = check_pdf_health(
        gs_override=cfg.GHOSTSCRIPT_PATH,
        storage_dir=Path(cfg.PDF_STORAGE_DIR),
    )
    payload = {
        "ghostscript": {"found": health.ghostscript.found, "path": health.ghostscript.path},
        "storage": {"dir": health.storage.dir, "writable": health.storage.writable},
    }
    status = 200 if health.ok else 503
    return jsonify(payload), status


# ---------------------------------------------------------------------------
# Scheduled tests (REST shape — uses the @api_endpoint scaffolding).
#
# These endpoints follow the conventions in ``docs/REST_API_ROADMAP.md``:
# bare-body JSON responses (no ``success`` envelope), Problem Details on
# error, cursor pagination on list, RFC 7807 ``Idempotency-Key`` support
# on the action endpoint. They live alongside the legacy ``success``-
# wrapped endpoints above; both shapes coexist until the frontend-
# migration phase per the locked-in §4.4 deprecation plan.
# ---------------------------------------------------------------------------

from auto_a11y.models.schedule import (  # noqa: E402
    AITestMode,
    PresetConfig,
    ScheduleTestConfig,
    ScheduleType,
    TestSchedule,
)
from auto_a11y.web.api import (  # noqa: E402
    ConflictError,
    NotFoundError,
    ValidationError,
    api_endpoint,
    paginate,
    require_project_role,
    require_superadmin,
)
from auto_a11y.web.api.errors import FieldError as _FieldError  # noqa: E402
from auto_a11y.web.api.pagination import Cursor as _Cursor  # noqa: E402
from auto_a11y.web.api.pagination import parse_limit  # noqa: E402
from auto_a11y.web.typed_app import get_idempotency_store  # noqa: E402


def _serialize_schedule(schedule: TestSchedule) -> dict[str, Any]:
    """Project a :class:`TestSchedule` to a JSON-safe dict.

    ``TestSchedule.to_dict`` keeps datetimes as ``datetime`` objects for
    Mongo. The REST API surfaces them as ISO 8601 UTC strings and drops
    the Mongo ``_id`` field in favor of the string ``id`` property.
    """

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    return {
        "id": schedule.id,
        "website_id": schedule.website_id,
        "name": schedule.name,
        "description": schedule.description,
        "schedule_type": schedule.schedule_type.value,
        "scheduled_datetime": _iso(schedule.scheduled_datetime),
        "cron_expression": schedule.cron_expression,
        "preset_config": schedule.preset_config.to_dict(),
        "test_config": schedule.test_config.to_dict(),
        "project_user_ids": list(schedule.project_user_ids),
        "enabled": schedule.enabled,
        "created_by": schedule.created_by,
        "last_run_at": _iso(schedule.last_run_at),
        "last_run_job_id": schedule.last_run_job_id,
        "last_run_status": (
            schedule.last_run_status.value
            if schedule.last_run_status is not None
            else None
        ),
        "next_run_at": _iso(schedule.next_run_at),
        "run_count": schedule.run_count,
        "created_at": _iso(schedule.created_at),
        "updated_at": _iso(schedule.updated_at),
    }


def _coerce_str_list(value: Any) -> list[str]:
    """Coerce an arbitrary iterable into a ``list[str]``.

    Centralizes the ``Any → list[str]`` conversion so callers don't have
    to wrestle with pyright's Unknown propagation on every comprehension.
    """
    items: list[Any] = []
    for item in value:
        items.append(item)
    return [str(item) for item in items]


def _require_dict_body() -> dict[str, Any]:
    """Return the parsed JSON body or raise ValidationError."""
    body_any: Any = request.get_json(silent=True)
    if not isinstance(body_any, dict):
        raise ValidationError(
            "request body must be a JSON object",
            errors=(
                _FieldError(field="<root>", code="invalid_type", message="must be object"),
            ),
        )
    return cast(dict[str, Any], body_any)


def _parse_schedule_type(raw: Any, *, field: str) -> ScheduleType:
    if not isinstance(raw, str):
        raise ValidationError(
            f"{field} must be a string",
            errors=(
                _FieldError(field=field, code="invalid_type", message="must be string"),
            ),
        )
    try:
        return ScheduleType(raw)
    except ValueError as exc:
        raise ValidationError(
            f"{field} is not a recognized schedule type",
            errors=(
                _FieldError(field=field, code="invalid_value", message=str(exc)),
            ),
        ) from exc


def _parse_iso_datetime(raw: Any, *, field: str) -> datetime:
    if not isinstance(raw, str):
        raise ValidationError(
            f"{field} must be an ISO 8601 datetime string",
            errors=(_FieldError(field=field, code="invalid_type", message="must be string"),),
        )
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(
            f"{field} is not a valid ISO 8601 datetime",
            errors=(_FieldError(field=field, code="invalid_format", message=str(exc)),),
        ) from exc
    return parsed


def _parse_preset_config(raw: Any, *, field: str) -> PresetConfig:
    if not isinstance(raw, dict):
        raise ValidationError(
            f"{field} must be an object",
            errors=(_FieldError(field=field, code="invalid_type", message="must be object"),),
        )
    raw_dict = cast(dict[str, Any], raw)
    return PresetConfig(
        time=str(raw_dict.get("time", "02:00")),
        day_of_week=int(raw_dict.get("day_of_week", 0)),
        day_of_month=int(raw_dict.get("day_of_month", 1)),
        timezone=str(raw_dict.get("timezone", "America/Toronto")),
    )


def _parse_test_config(raw: Any, *, field: str) -> ScheduleTestConfig:
    if not isinstance(raw, dict):
        raise ValidationError(
            f"{field} must be an object",
            errors=(_FieldError(field=field, code="invalid_type", message="must be object"),),
        )
    raw_dict = cast(dict[str, Any], raw)
    ai_pages_mode_raw: Any = raw_dict.get("ai_pages_mode", "all")
    try:
        ai_pages_mode = (
            AITestMode(ai_pages_mode_raw)
            if isinstance(ai_pages_mode_raw, str)
            else AITestMode.ALL
        )
    except ValueError as exc:
        raise ValidationError(
            "test_config.ai_pages_mode is not recognized",
            errors=(
                _FieldError(
                    field=f"{field}.ai_pages_mode",
                    code="invalid_value",
                    message=str(exc),
                ),
            ),
        ) from exc
    return ScheduleTestConfig(
        run_ai_tests=bool(raw_dict.get("run_ai_tests", False)),
        run_javascript_tests=bool(raw_dict.get("run_javascript_tests", True)),
        run_python_tests=bool(raw_dict.get("run_python_tests", True)),
        enabled_touchpoints=list(raw_dict.get("enabled_touchpoints", [])),
        ai_pages_mode=ai_pages_mode,
        ai_page_ids=list(raw_dict.get("ai_page_ids", [])),
        take_screenshots=bool(raw_dict.get("take_screenshots", True)),
    )


def _validate_schedule_invariants(schedule: TestSchedule) -> None:
    """Cross-field checks not enforceable from a single field's parser."""
    if not schedule.name.strip():
        raise ValidationError(
            "name is required",
            errors=(_FieldError(field="name", code="required", message="required"),),
        )
    if (
        schedule.schedule_type is ScheduleType.CRON
        and not (schedule.cron_expression or "").strip()
    ):
        raise ValidationError(
            "cron_expression is required when schedule_type=cron",
            errors=(
                _FieldError(
                    field="cron_expression",
                    code="required",
                    message="required when schedule_type=cron",
                ),
            ),
        )
    if (
        schedule.schedule_type is ScheduleType.ONE_TIME
        and schedule.scheduled_datetime is None
    ):
        raise ValidationError(
            "scheduled_datetime is required when schedule_type=one_time",
            errors=(
                _FieldError(
                    field="scheduled_datetime",
                    code="required",
                    message="required when schedule_type=one_time",
                ),
            ),
        )


def _build_schedule_from_body(
    website_id: str, body: dict[str, Any]
) -> TestSchedule:
    schedule_type = _parse_schedule_type(
        body.get("schedule_type", "daily"), field="schedule_type"
    )
    scheduled_datetime: datetime | None = None
    if "scheduled_datetime" in body and body["scheduled_datetime"] is not None:
        scheduled_datetime = _parse_iso_datetime(
            body["scheduled_datetime"], field="scheduled_datetime"
        )
    preset_config = _parse_preset_config(
        body.get("preset_config", {}), field="preset_config"
    )
    test_config = _parse_test_config(body.get("test_config", {}), field="test_config")
    cron_raw = body.get("cron_expression")
    cron_expression = cron_raw.strip() if isinstance(cron_raw, str) and cron_raw.strip() else None
    project_user_ids_raw: Any = body.get("project_user_ids", [])
    if not isinstance(project_user_ids_raw, list):
        raise ValidationError(
            "project_user_ids must be an array",
            errors=(
                _FieldError(
                    field="project_user_ids",
                    code="invalid_type",
                    message="must be array",
                ),
            ),
        )
    project_user_ids = _coerce_str_list(project_user_ids_raw)
    schedule = TestSchedule(
        website_id=website_id,
        name=str(body.get("name", "")).strip(),
        description=(
            str(body["description"]).strip()
            if isinstance(body.get("description"), str)
            else None
        ),
        schedule_type=schedule_type,
        scheduled_datetime=scheduled_datetime,
        cron_expression=cron_expression,
        preset_config=preset_config,
        test_config=test_config,
        project_user_ids=project_user_ids,
        enabled=bool(body.get("enabled", True)),
        created_by=(
            str(current_user.get_id()) if current_user.is_authenticated else None
        ),
    )
    _validate_schedule_invariants(schedule)
    return schedule


def _apply_patch_to_schedule(
    schedule: TestSchedule, body: dict[str, Any]
) -> TestSchedule:
    """Apply only the keys present in ``body`` to ``schedule``."""
    if "name" in body:
        if not isinstance(body["name"], str):
            raise ValidationError(
                "name must be a string",
                errors=(
                    _FieldError(field="name", code="invalid_type", message="must be string"),
                ),
            )
        schedule.name = body["name"].strip()
    if "description" in body:
        desc = body["description"]
        schedule.description = desc.strip() if isinstance(desc, str) else None
    if "schedule_type" in body:
        schedule.schedule_type = _parse_schedule_type(
            body["schedule_type"], field="schedule_type"
        )
    if "scheduled_datetime" in body:
        schedule.scheduled_datetime = (
            _parse_iso_datetime(body["scheduled_datetime"], field="scheduled_datetime")
            if body["scheduled_datetime"] is not None
            else None
        )
    if "cron_expression" in body:
        cron_raw = body["cron_expression"]
        schedule.cron_expression = (
            cron_raw.strip() if isinstance(cron_raw, str) and cron_raw.strip() else None
        )
    if "preset_config" in body:
        schedule.preset_config = _parse_preset_config(
            body["preset_config"], field="preset_config"
        )
    if "test_config" in body:
        schedule.test_config = _parse_test_config(
            body["test_config"], field="test_config"
        )
    if "project_user_ids" in body:
        ids_raw: Any = body["project_user_ids"]
        if not isinstance(ids_raw, list):
            raise ValidationError(
                "project_user_ids must be an array",
                errors=(
                    _FieldError(
                        field="project_user_ids",
                        code="invalid_type",
                        message="must be array",
                    ),
                ),
            )
        schedule.project_user_ids = _coerce_str_list(ids_raw)
    if "enabled" in body:
        schedule.enabled = bool(body["enabled"])
    schedule.update_timestamp()
    _validate_schedule_invariants(schedule)
    return schedule


def _resync_with_scheduler(schedule: TestSchedule) -> None:
    """Mirror the legacy register/remove dance against the scheduler.

    Imported lazily so endpoints can run in tests where the APScheduler
    service is not configured.
    """
    from auto_a11y.core.scheduler import get_scheduler_service

    scheduler = get_scheduler_service()
    if scheduler is None:
        return
    if schedule.enabled:
        scheduler.register_schedule_with_apscheduler(schedule)
    elif schedule.id:
        scheduler.remove_from_apscheduler(schedule.id)


@api_bp.route("/websites/<website_id>/scheduled-tests", methods=["GET"])
@api_endpoint
def list_scheduled_tests(website_id: str) -> tuple[Response, int] | Response:
    """List scheduled tests for a website with cursor pagination."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=website_id
    )
    website = get_db().get_website(website_id)
    if website is None:
        raise NotFoundError(f"website {website_id} not found")

    limit = parse_limit(request.args.get("limit"))
    cursor_raw = request.args.get("cursor")
    cursor = _Cursor.decode(cursor_raw) if cursor_raw else None

    query: dict[str, Any] = {"website_id": website_id}
    if cursor is not None:
        from bson import ObjectId
        try:
            query["_id"] = {"$lt": ObjectId(cursor.last_id)}
        except Exception as exc:
            raise ValidationError(
                "cursor.last_id is not a valid ObjectId",
                errors=(
                    _FieldError(
                        field="cursor.last_id",
                        code="invalid_format",
                        message=str(exc),
                    ),
                ),
            ) from exc

    docs = list(
        get_db().test_schedules.find(query).sort("_id", -1).limit(limit + 1)
    )
    schedules = [TestSchedule.from_dict(doc) for doc in docs]
    page = paginate(
        schedules, limit=limit, get_id=lambda s: str(s.mongo_id) if s.mongo_id else ""
    )
    return jsonify(
        {
            "items": [_serialize_schedule(s) for s in page["items"]],
            "next_cursor": page["next_cursor"],
        }
    )


@api_bp.route("/websites/<website_id>/scheduled-tests", methods=["POST"])
@api_endpoint
def create_scheduled_test(website_id: str) -> tuple[Response, int]:
    """Create a scheduled test on a website."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=website_id
    )
    if get_db().get_website(website_id) is None:
        raise NotFoundError(f"website {website_id} not found")

    body = _require_dict_body()
    schedule = _build_schedule_from_body(website_id, body)
    schedule_id = get_db().create_test_schedule(schedule)
    refreshed = get_db().get_test_schedule(schedule_id)
    if refreshed is None:
        raise ConflictError("schedule failed to persist")
    if refreshed.enabled:
        _resync_with_scheduler(refreshed)
    response = jsonify(_serialize_schedule(refreshed))
    response.headers["Location"] = f"/api/v1/scheduled-tests/{schedule_id}"
    return response, 201


@api_bp.route("/scheduled-tests/<schedule_id>", methods=["GET"])
@api_endpoint
def get_scheduled_test(schedule_id: str) -> tuple[Response, int] | Response:
    """Get a scheduled test by id."""
    schedule = get_db().get_test_schedule(schedule_id)
    if schedule is None:
        raise NotFoundError(f"scheduled test {schedule_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=schedule.website_id
    )
    return jsonify(_serialize_schedule(schedule))


@api_bp.route("/scheduled-tests/<schedule_id>", methods=["PUT"])
@api_endpoint
def replace_scheduled_test(schedule_id: str) -> tuple[Response, int] | Response:
    """Full replace of a scheduled test."""
    schedule = get_db().get_test_schedule(schedule_id)
    if schedule is None:
        raise NotFoundError(f"scheduled test {schedule_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=schedule.website_id
    )
    body = _require_dict_body()
    replaced = _build_schedule_from_body(schedule.website_id, body)
    replaced.mongo_id = schedule.mongo_id
    replaced.created_at = schedule.created_at
    replaced.created_by = schedule.created_by
    replaced.last_run_at = schedule.last_run_at
    replaced.last_run_job_id = schedule.last_run_job_id
    replaced.last_run_status = schedule.last_run_status
    replaced.next_run_at = schedule.next_run_at
    replaced.run_count = schedule.run_count
    replaced.apscheduler_job_id = schedule.apscheduler_job_id
    replaced.update_timestamp()
    if not get_db().update_test_schedule(replaced):
        raise ConflictError("schedule could not be updated")
    _resync_with_scheduler(replaced)
    return jsonify(_serialize_schedule(replaced))


@api_bp.route("/scheduled-tests/<schedule_id>", methods=["PATCH"])
@api_endpoint
def patch_scheduled_test(schedule_id: str) -> tuple[Response, int] | Response:
    """Partial update — used for toggle (``{"enabled": true}``) and similar edits."""
    schedule = get_db().get_test_schedule(schedule_id)
    if schedule is None:
        raise NotFoundError(f"scheduled test {schedule_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=schedule.website_id
    )
    body = _require_dict_body()
    patched = _apply_patch_to_schedule(schedule, body)
    if not get_db().update_test_schedule(patched):
        raise ConflictError("schedule could not be updated")
    _resync_with_scheduler(patched)
    return jsonify(_serialize_schedule(patched))


@api_bp.route("/scheduled-tests/<schedule_id>", methods=["DELETE"])
@api_endpoint
def delete_scheduled_test(schedule_id: str) -> tuple[Response, int]:
    """Delete a scheduled test."""
    schedule = get_db().get_test_schedule(schedule_id)
    if schedule is None:
        raise NotFoundError(f"scheduled test {schedule_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=schedule.website_id
    )
    from auto_a11y.core.scheduler import get_scheduler_service
    scheduler = get_scheduler_service()
    if scheduler is not None:
        scheduler.remove_from_apscheduler(schedule_id)
    get_db().delete_test_schedule(schedule_id)
    return Response(status=204), 204


@api_bp.route("/scheduled-tests/<schedule_id>/runs", methods=["POST"])
@api_endpoint
def run_scheduled_test_now(
    schedule_id: str,
) -> tuple[Response, int] | Response:
    """Trigger an immediate run of the schedule.

    Honors the ``Idempotency-Key`` header per §4.9 of the roadmap. A
    repeated POST with the same key returns the recorded ``job_id``
    without enqueuing a second job.
    """
    schedule = get_db().get_test_schedule(schedule_id)
    if schedule is None:
        raise NotFoundError(f"scheduled test {schedule_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=schedule.website_id
    )

    from auto_a11y.core.scheduler import get_scheduler_service
    scheduler = get_scheduler_service()
    if scheduler is None:
        raise ConflictError("scheduler service is not available")

    def _run() -> tuple[int, dict[str, Any]]:
        job_id = scheduler.run_now(schedule_id)
        if not job_id:
            raise ConflictError("scheduler refused to start the run")
        return 202, {"job_id": job_id, "schedule_id": schedule_id}

    idempotency_key = request.headers.get("Idempotency-Key")
    body_any: Any = request.get_json(silent=True)
    request_body: dict[str, Any] | None = (
        cast(dict[str, Any], body_any) if isinstance(body_any, dict) else None
    )

    if idempotency_key:
        status_code, body = get_idempotency_store().get_or_record(
            idempotency_key, request_body=request_body, compute=_run
        )
    else:
        status_code, body = _run()

    return jsonify(body), status_code


@api_bp.route("/scheduled-tests/<schedule_id>/preview", methods=["GET"])
@api_endpoint
def preview_scheduled_test(
    schedule_id: str,
) -> tuple[Response, int] | Response:
    """Return the next N upcoming run times for a schedule."""
    schedule = get_db().get_test_schedule(schedule_id)
    if schedule is None:
        raise NotFoundError(f"scheduled test {schedule_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=schedule.website_id
    )
    from auto_a11y.core.scheduler import get_scheduler_service
    scheduler = get_scheduler_service()
    if scheduler is None:
        raise ConflictError("scheduler service is not available")

    count_raw = request.args.get("count", "5")
    try:
        count = max(1, min(int(count_raw), 50))
    except ValueError as exc:
        raise ValidationError(
            "count must be an integer",
            errors=(_FieldError(field="count", code="invalid_type", message=str(exc)),),
        ) from exc

    next_runs = scheduler.get_next_run_times(schedule_id, count)
    return jsonify(
        {
            "schedule_id": schedule_id,
            "next_runs": [dt.isoformat() for dt in next_runs],
        }
    )


# ---------------------------------------------------------------------------
# Websites (REST shape — uses the @api_endpoint scaffolding).
#
# Mirrors the conventions from the scheduled-tests block above. Action
# endpoints (discoveries, test-runs, cancels, clear-test-results) are
# scoped out of this PR — they involve async-job tracking and idempotency
# concerns that are best handled in a follow-up alongside their respective
# resources, per ``docs/REST_API_ROADMAP.md`` §5.2.
# ---------------------------------------------------------------------------

from auto_a11y.models.website import ScrapingConfig, Website  # noqa: E402


def _serialize_website(website: Website) -> dict[str, Any]:
    """Project a :class:`Website` to a JSON-safe dict.

    Datetimes are emitted as ISO 8601 strings; the Mongo ``_id`` is
    surfaced as the string ``id`` field. ``members`` is intentionally
    omitted — the legacy field is unused and the project members API
    is the authoritative surface for that data.
    """

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    return {
        "id": website.id,
        "project_id": website.project_id,
        "url": website.url,
        "name": website.name,
        "display_name": website.display_name,
        "page_count": website.page_count,
        "scraping_config": website.scraping_config.to_dict(),
        "created_at": _iso(website.created_at),
        "last_scraped": _iso(website.last_scraped),
        "last_tested": _iso(website.last_tested),
    }


def _parse_scraping_config(raw: Any, *, field: str) -> ScrapingConfig:
    """Validate and project a JSON object into a :class:`ScrapingConfig`.

    Unknown keys are dropped (ScrapingConfig.from_dict already does this).
    Type coercion for primitives is intentionally light: callers send
    whatever JSON parses produce, and ScrapingConfig's defaults absorb
    most omissions.
    """
    if not isinstance(raw, dict):
        raise ValidationError(
            f"{field} must be an object",
            errors=(_FieldError(field=field, code="invalid_type", message="must be object"),),
        )
    raw_dict = cast(dict[str, Any], raw)

    def _int(value: Any, *, key: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValidationError(
                f"{field}.{key} must be an integer",
                errors=(
                    _FieldError(
                        field=f"{field}.{key}",
                        code="invalid_type",
                        message="must be integer",
                    ),
                ),
            )
        return int(value)

    def _float(value: Any, *, key: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValidationError(
                f"{field}.{key} must be a number",
                errors=(
                    _FieldError(
                        field=f"{field}.{key}",
                        code="invalid_type",
                        message="must be number",
                    ),
                ),
            )
        return float(value)

    def _bool(value: Any, *, key: str) -> bool:
        if not isinstance(value, bool):
            raise ValidationError(
                f"{field}.{key} must be a boolean",
                errors=(
                    _FieldError(
                        field=f"{field}.{key}",
                        code="invalid_type",
                        message="must be boolean",
                    ),
                ),
            )
        return value

    def _str_list(value: Any, *, key: str) -> list[str]:
        if not isinstance(value, list):
            raise ValidationError(
                f"{field}.{key} must be an array",
                errors=(
                    _FieldError(
                        field=f"{field}.{key}",
                        code="invalid_type",
                        message="must be array",
                    ),
                ),
            )
        return _coerce_str_list(value)

    defaults = ScrapingConfig()
    return ScrapingConfig(
        max_pages=_int(raw_dict["max_pages"], key="max_pages")
            if "max_pages" in raw_dict else defaults.max_pages,
        max_depth=_int(raw_dict["max_depth"], key="max_depth")
            if "max_depth" in raw_dict else defaults.max_depth,
        follow_external=_bool(raw_dict["follow_external"], key="follow_external")
            if "follow_external" in raw_dict else defaults.follow_external,
        include_subdomains=_bool(raw_dict["include_subdomains"], key="include_subdomains")
            if "include_subdomains" in raw_dict else defaults.include_subdomains,
        respect_robots=_bool(raw_dict["respect_robots"], key="respect_robots")
            if "respect_robots" in raw_dict else defaults.respect_robots,
        request_delay=_float(raw_dict["request_delay"], key="request_delay")
            if "request_delay" in raw_dict else defaults.request_delay,
        allowed_paths=_str_list(raw_dict["allowed_paths"], key="allowed_paths")
            if "allowed_paths" in raw_dict else list(defaults.allowed_paths),
        excluded_paths=_str_list(raw_dict["excluded_paths"], key="excluded_paths")
            if "excluded_paths" in raw_dict else list(defaults.excluded_paths),
        auto_fetch_pdfs=_bool(raw_dict["auto_fetch_pdfs"], key="auto_fetch_pdfs")
            if "auto_fetch_pdfs" in raw_dict else defaults.auto_fetch_pdfs,
    )


def _validate_url(raw: Any, *, field: str) -> str:
    """Require ``raw`` to be a non-empty http(s) URL string."""
    if not isinstance(raw, str):
        raise ValidationError(
            f"{field} must be a string",
            errors=(_FieldError(field=field, code="invalid_type", message="must be string"),),
        )
    value = raw.strip()
    if not value:
        raise ValidationError(
            f"{field} is required",
            errors=(_FieldError(field=field, code="required", message="required"),),
        )
    if not (value.startswith("http://") or value.startswith("https://")):
        raise ValidationError(
            f"{field} must be an http(s) URL",
            errors=(_FieldError(field=field, code="invalid_format", message="must start with http:// or https://"),),
        )
    return value


def _build_website_from_body(project_id: str, body: dict[str, Any]) -> Website:
    """Construct a :class:`Website` from a POST/PUT body, validating fields."""
    url = _validate_url(body.get("url"), field="url")
    name_raw = body.get("name")
    if name_raw is not None and not isinstance(name_raw, str):
        raise ValidationError(
            "name must be a string or null",
            errors=(_FieldError(field="name", code="invalid_type", message="must be string"),),
        )
    name = name_raw.strip() if isinstance(name_raw, str) and name_raw.strip() else None
    scraping_config = (
        _parse_scraping_config(body["scraping_config"], field="scraping_config")
        if "scraping_config" in body and body["scraping_config"] is not None
        else ScrapingConfig()
    )
    return Website(
        project_id=project_id,
        url=url,
        name=name,
        scraping_config=scraping_config,
    )


def _apply_patch_to_website(website: Website, body: dict[str, Any]) -> Website:
    """Apply only the keys present in ``body`` to ``website`` in place."""
    if "url" in body:
        website.url = _validate_url(body["url"], field="url")
    if "name" in body:
        name_raw = body["name"]
        if name_raw is not None and not isinstance(name_raw, str):
            raise ValidationError(
                "name must be a string or null",
                errors=(_FieldError(field="name", code="invalid_type", message="must be string"),),
            )
        website.name = (
            name_raw.strip() if isinstance(name_raw, str) and name_raw.strip() else None
        )
    if "scraping_config" in body:
        if body["scraping_config"] is None:
            website.scraping_config = ScrapingConfig()
        else:
            website.scraping_config = _parse_scraping_config(
                body["scraping_config"], field="scraping_config"
            )
    return website


@api_bp.route("/projects/<project_id>/websites", methods=["GET"])
@api_endpoint
def list_websites_for_project(project_id: str) -> tuple[Response, int] | Response:
    """List websites in a project with cursor pagination."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=project_id
    )
    if get_db().get_project(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")

    limit = parse_limit(request.args.get("limit"))
    cursor_raw = request.args.get("cursor")
    cursor = _Cursor.decode(cursor_raw) if cursor_raw else None

    query: dict[str, Any] = {"project_id": project_id}
    if cursor is not None:
        from bson import ObjectId
        try:
            query["_id"] = {"$lt": ObjectId(cursor.last_id)}
        except Exception as exc:
            raise ValidationError(
                "cursor.last_id is not a valid ObjectId",
                errors=(
                    _FieldError(
                        field="cursor.last_id",
                        code="invalid_format",
                        message=str(exc),
                    ),
                ),
            ) from exc

    docs = list(get_db().websites.find(query).sort("_id", -1).limit(limit + 1))
    websites = [Website.from_dict(doc) for doc in docs]
    page = paginate(
        websites, limit=limit, get_id=lambda w: str(w.mongo_id) if w.mongo_id else ""
    )
    return jsonify(
        {
            "items": [_serialize_website(w) for w in page["items"]],
            "next_cursor": page["next_cursor"],
        }
    )


@api_bp.route("/projects/<project_id>/websites", methods=["POST"])
@api_endpoint
def create_website(project_id: str) -> tuple[Response, int]:
    """Create a website inside a project."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    if get_db().get_project(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")

    body = _require_dict_body()
    website = _build_website_from_body(project_id, body)
    website_id = get_db().create_website(website)
    refreshed = get_db().get_website(website_id)
    if refreshed is None:
        raise ConflictError("website failed to persist")
    response = jsonify(_serialize_website(refreshed))
    response.headers["Location"] = f"/api/v1/websites/{website_id}"
    return response, 201


@api_bp.route("/websites/<website_id>", methods=["GET"])
@api_endpoint
def get_website(website_id: str) -> tuple[Response, int] | Response:
    """Get a website by id."""
    website = get_db().get_website(website_id)
    if website is None:
        raise NotFoundError(f"website {website_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, website_id=website_id
    )
    return jsonify(_serialize_website(website))


@api_bp.route("/websites/<website_id>", methods=["PUT"])
@api_endpoint
def replace_website(website_id: str) -> tuple[Response, int] | Response:
    """Full replace of a website's editable fields.

    Server-managed fields (created_at, last_scraped, last_tested,
    page_count, project_id, members) are preserved from the existing
    record — clients cannot reassign a website to a different project
    via PUT.
    """
    existing = get_db().get_website(website_id)
    if existing is None:
        raise NotFoundError(f"website {website_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=website_id
    )
    body = _require_dict_body()
    replaced = _build_website_from_body(existing.project_id, body)
    replaced.mongo_id = existing.mongo_id
    replaced.created_at = existing.created_at
    replaced.last_scraped = existing.last_scraped
    replaced.last_tested = existing.last_tested
    replaced.page_count = existing.page_count
    replaced.discovery_history = list(existing.discovery_history)
    replaced.members = list(existing.members)
    if not get_db().update_website(replaced):
        raise ConflictError("website could not be updated")
    return jsonify(_serialize_website(replaced))


@api_bp.route("/websites/<website_id>", methods=["PATCH"])
@api_endpoint
def patch_website(website_id: str) -> tuple[Response, int] | Response:
    """Partial update — only fields present in the request body are changed."""
    website = get_db().get_website(website_id)
    if website is None:
        raise NotFoundError(f"website {website_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=website_id
    )
    body = _require_dict_body()
    patched = _apply_patch_to_website(website, body)
    if not get_db().update_website(patched):
        raise ConflictError("website could not be updated")
    return jsonify(_serialize_website(patched))


@api_bp.route("/websites/<website_id>", methods=["DELETE"])
@api_endpoint
def delete_website(website_id: str) -> tuple[Response, int]:
    """Delete a website. Cascades to pages, PDFs, and test results."""
    website = get_db().get_website(website_id)
    if website is None:
        raise NotFoundError(f"website {website_id} not found")
    require_project_role(
        UserRole.ADMIN, website_id=website_id
    )
    get_db().delete_website(website_id)
    return Response(status=204), 204


# ---------------------------------------------------------------------------
# Pages (REST shape — uses the @api_endpoint scaffolding).
#
# Mirrors the conventions from the websites and scheduled-tests blocks
# above. Action endpoints (`/test`, `/test-results`, `/test-states`,
# `/test-sessions`) remain on the legacy URL surface for now — they
# share async-job tracking with the schedules `/runs` endpoint and are
# scoped out of this PR per ``docs/REST_API_ROADMAP.md`` §5.3.
# ---------------------------------------------------------------------------


_VALID_PAGE_PRIORITIES: frozenset[str] = frozenset({"high", "normal", "low"})


def _serialize_page(page: Page) -> dict[str, Any]:
    """Project a :class:`Page` to a JSON-safe dict.

    Datetimes emit as ISO 8601 strings; the Mongo ``_id`` becomes a
    string ``id``. Drupal-sync fields are exposed read-only — they are
    set by the Drupal sync subsystem, not by REST clients, but reading
    them is useful for downstream tooling.
    """

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    return {
        "id": page.id,
        "website_id": page.website_id,
        "url": page.url,
        "title": page.title,
        "status": page.status.value,
        "priority": page.priority,
        "depth": page.depth,
        "discovered_at": _iso(page.discovered_at),
        "discovered_from": page.discovered_from,
        "discovery_run_id": page.discovery_run_id,
        "last_tested": _iso(page.last_tested),
        "violation_count": page.violation_count,
        "warning_count": page.warning_count,
        "info_count": page.info_count,
        "discovery_count": page.discovery_count,
        "pass_count": page.pass_count,
        "test_duration_ms": page.test_duration_ms,
        "error_reason": page.error_reason,
        "is_in_latest_discovery": page.is_in_latest_discovery,
        "screenshot_path": page.screenshot_path,
        "setup_script_id": page.setup_script_id,
        "linked_pdf_document_id": page.linked_pdf_document_id,
    }


def _validate_page_priority(raw: Any, *, field: str) -> str:
    if not isinstance(raw, str):
        raise ValidationError(
            f"{field} must be a string",
            errors=(_FieldError(field=field, code="invalid_type", message="must be string"),),
        )
    if raw not in _VALID_PAGE_PRIORITIES:
        raise ValidationError(
            f"{field} must be one of high|normal|low",
            errors=(
                _FieldError(
                    field=field,
                    code="invalid_value",
                    message=f"must be one of {sorted(_VALID_PAGE_PRIORITIES)}",
                ),
            ),
        )
    return raw


def _build_page_from_body(website_id: str, body: dict[str, Any]) -> Page:
    """Construct a :class:`Page` from a POST/PUT body, validating fields."""
    url = _validate_url(body.get("url"), field="url")
    title_raw = body.get("title")
    if title_raw is not None and not isinstance(title_raw, str):
        raise ValidationError(
            "title must be a string or null",
            errors=(_FieldError(field="title", code="invalid_type", message="must be string"),),
        )
    title = title_raw.strip() if isinstance(title_raw, str) and title_raw.strip() else None
    priority = (
        _validate_page_priority(body["priority"], field="priority")
        if "priority" in body
        else "normal"
    )
    setup_script_id_raw = body.get("setup_script_id")
    if setup_script_id_raw is not None and not isinstance(setup_script_id_raw, str):
        raise ValidationError(
            "setup_script_id must be a string or null",
            errors=(
                _FieldError(
                    field="setup_script_id",
                    code="invalid_type",
                    message="must be string",
                ),
            ),
        )
    return Page(
        website_id=website_id,
        url=url,
        title=title,
        priority=priority,
        setup_script_id=setup_script_id_raw if isinstance(setup_script_id_raw, str) else None,
    )


def _apply_patch_to_page(page: Page, body: dict[str, Any]) -> Page:
    """Apply only the keys present in ``body`` to ``page`` in place.

    ``url`` and ``website_id`` are not patchable: the page identity is
    its (website_id, url) pair, and changing either would conflict with
    the upsert semantics in :meth:`Database.create_page`.
    """
    if "title" in body:
        title_raw = body["title"]
        if title_raw is not None and not isinstance(title_raw, str):
            raise ValidationError(
                "title must be a string or null",
                errors=(_FieldError(field="title", code="invalid_type", message="must be string"),),
            )
        page.title = (
            title_raw.strip() if isinstance(title_raw, str) and title_raw.strip() else None
        )
    if "priority" in body:
        page.priority = _validate_page_priority(body["priority"], field="priority")
    if "setup_script_id" in body:
        raw = body["setup_script_id"]
        if raw is not None and not isinstance(raw, str):
            raise ValidationError(
                "setup_script_id must be a string or null",
                errors=(
                    _FieldError(
                        field="setup_script_id",
                        code="invalid_type",
                        message="must be string",
                    ),
                ),
            )
        page.setup_script_id = raw if isinstance(raw, str) else None
    return page


@api_bp.route("/websites/<website_id>/pages", methods=["GET"])
@api_endpoint
def list_pages_for_website(website_id: str) -> tuple[Response, int] | Response:
    """List pages for a website with cursor pagination.

    Optional query params:
    - ``status``: filter to a single PageStatus value (e.g. ``tested``).
      Unknown values produce a 400.
    - ``limit`` / ``cursor``: standard pagination.
    """
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, website_id=website_id
    )
    if get_db().get_website(website_id) is None:
        raise NotFoundError(f"website {website_id} not found")

    limit = parse_limit(request.args.get("limit"))
    cursor_raw = request.args.get("cursor")
    cursor = _Cursor.decode(cursor_raw) if cursor_raw else None

    query: dict[str, Any] = {"website_id": website_id}
    status_raw = request.args.get("status")
    if status_raw is not None:
        try:
            query["status"] = PageStatus(status_raw).value
        except ValueError as exc:
            raise ValidationError(
                "status is not a recognized PageStatus value",
                errors=(_FieldError(field="status", code="invalid_value", message=str(exc)),),
            ) from exc
    if cursor is not None:
        from bson import ObjectId
        try:
            query["_id"] = {"$lt": ObjectId(cursor.last_id)}
        except Exception as exc:
            raise ValidationError(
                "cursor.last_id is not a valid ObjectId",
                errors=(
                    _FieldError(
                        field="cursor.last_id",
                        code="invalid_format",
                        message=str(exc),
                    ),
                ),
            ) from exc

    docs = list(get_db().pages.find(query).sort("_id", -1).limit(limit + 1))
    pages = [Page.from_dict(doc) for doc in docs]
    page = paginate(
        pages, limit=limit, get_id=lambda p: str(p.mongo_id) if p.mongo_id else ""
    )
    return jsonify(
        {
            "items": [_serialize_page(p) for p in page["items"]],
            "next_cursor": page["next_cursor"],
        }
    )


@api_bp.route("/websites/<website_id>/pages", methods=["POST"])
@api_endpoint
def create_page(website_id: str) -> tuple[Response, int]:
    """Create a page on a website.

    Note: :meth:`Database.create_page` is upsert-by-(website_id, url) —
    posting a duplicate URL returns the existing page rather than a new
    one. The 201 response and ``Location`` header reflect the resulting
    resource either way.
    """
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=website_id
    )
    if get_db().get_website(website_id) is None:
        raise NotFoundError(f"website {website_id} not found")

    body = _require_dict_body()
    page = _build_page_from_body(website_id, body)
    page_id = get_db().create_page(page)
    refreshed = get_db().get_page(page_id)
    if refreshed is None:
        raise ConflictError("page failed to persist")
    response = jsonify(_serialize_page(refreshed))
    response.headers["Location"] = f"/api/v1/pages/{page_id}"
    return response, 201


@api_bp.route("/pages/<page_id>", methods=["GET"])
@api_endpoint
def get_page_resource(page_id: str) -> tuple[Response, int] | Response:
    """Get a page by id."""
    page = get_db().get_page(page_id)
    if page is None:
        raise NotFoundError(f"page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, website_id=page.website_id
    )
    return jsonify(_serialize_page(page))


@api_bp.route("/pages/<page_id>", methods=["PUT"])
@api_endpoint
def replace_page(page_id: str) -> tuple[Response, int] | Response:
    """Full replace of a page's editable fields.

    Server-managed fields (status, counts, dates, screenshot, drupal
    sync, discovery metadata) are preserved. ``website_id`` and ``url``
    are also locked — the (website_id, url) pair is the page's identity.
    """
    existing = get_db().get_page(page_id)
    if existing is None:
        raise NotFoundError(f"page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=existing.website_id
    )
    body = _require_dict_body()
    replaced = _build_page_from_body(existing.website_id, body)
    if replaced.url != existing.url:
        raise ValidationError(
            "url cannot be changed; (website_id, url) is the page identity",
            errors=(_FieldError(field="url", code="immutable", message="immutable"),),
        )
    replaced.mongo_id = existing.mongo_id
    replaced.discovered_at = existing.discovered_at
    replaced.discovered_from = existing.discovered_from
    replaced.discovery_run_id = existing.discovery_run_id
    replaced.last_tested = existing.last_tested
    replaced.status = existing.status
    replaced.violation_count = existing.violation_count
    replaced.warning_count = existing.warning_count
    replaced.info_count = existing.info_count
    replaced.discovery_count = existing.discovery_count
    replaced.pass_count = existing.pass_count
    replaced.test_duration_ms = existing.test_duration_ms
    replaced.depth = existing.depth
    replaced.error_reason = existing.error_reason
    replaced.is_in_latest_discovery = existing.is_in_latest_discovery
    replaced.screenshot_path = existing.screenshot_path
    replaced.visible_to_users = list(existing.visible_to_users)
    replaced.is_flagged_for_discovery = existing.is_flagged_for_discovery
    replaced.discovery_reasons = list(existing.discovery_reasons)
    replaced.discovery_areas = list(existing.discovery_areas)
    replaced.discovery_notes_private = existing.discovery_notes_private
    replaced.discovery_notes_public = existing.discovery_notes_public
    replaced.drupal_discovered_page_uuid = existing.drupal_discovered_page_uuid
    replaced.drupal_sync_status = existing.drupal_sync_status
    replaced.drupal_last_synced = existing.drupal_last_synced
    replaced.drupal_error_message = existing.drupal_error_message
    replaced.linked_pdf_document_id = existing.linked_pdf_document_id
    if not get_db().update_page(replaced):
        raise ConflictError("page could not be updated")
    return jsonify(_serialize_page(replaced))


@api_bp.route("/pages/<page_id>", methods=["PATCH"])
@api_endpoint
def patch_page(page_id: str) -> tuple[Response, int] | Response:
    """Partial update — only fields present in the request body are changed."""
    page = get_db().get_page(page_id)
    if page is None:
        raise NotFoundError(f"page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=page.website_id
    )
    body = _require_dict_body()
    patched = _apply_patch_to_page(page, body)
    if not get_db().update_page(patched):
        raise ConflictError("page could not be updated")
    return jsonify(_serialize_page(patched))


@api_bp.route("/pages/<page_id>", methods=["DELETE"])
@api_endpoint
def delete_page_resource(page_id: str) -> tuple[Response, int]:
    """Delete a page and its test results."""
    page = get_db().get_page(page_id)
    if page is None:
        raise NotFoundError(f"page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=page.website_id
    )
    get_db().delete_page(page_id)
    return Response(status=204), 204


# ---------------------------------------------------------------------------
# Page sub-resources (REST shape — §5.3 of docs/REST_API_ROADMAP.md).
#
# Replaces the legacy HTML routes on `pages_bp`:
#   - GET /pages/<id>/violations  → just redirected to the page view
#   - GET/POST /pages/<id>/matrix → server-rendered form
#   - POST /pages/<id>/cancel-test → ad-hoc `success` envelope
#
# The HTML routes stay alive until the issue #21 frontend migration —
# they keep their old shapes; this surface returns RFC 7807 problems.
# ---------------------------------------------------------------------------


@api_bp.route("/pages/<page_id>/violations", methods=["GET"])
@api_endpoint
def get_page_violations(page_id: str) -> tuple[Response, int] | Response:
    """Return the latest test result's issue buckets for a page.

    Unlike :func:`get_page_test_results` (which lists every historical
    result), this endpoint flattens the *latest* result down to just the
    issue arrays a UI needs to render a violations table:

    - ``violations`` — high-severity issues (the things that fail WCAG)
    - ``warnings`` — medium-severity issues
    - ``info`` — informational notes
    - ``discovery`` — discovery items (elements that need a human review)
    - ``ai_findings`` — AI-detected issues, when Claude analysis ran

    Each issue is serialized via :meth:`Violation.to_dict` /
    :meth:`AIFinding.to_dict` so the shape matches what
    ``/test-results/<id>`` already returns inside its ``violations`` etc.
    fields.

    When the page has never been tested, the buckets are all empty and
    ``test_result_id`` / ``tested_at`` are ``null`` — clients can still
    render an empty-state table without a second request.
    """
    page = get_db().get_page(page_id)
    if page is None:
        raise NotFoundError(f"page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, website_id=page.website_id
    )

    result = get_db().get_latest_test_result(page_id)

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    if result is None:
        return jsonify({
            "page_id": page_id,
            "test_result_id": None,
            "tested_at": None,
            "violations": [],
            "warnings": [],
            "info": [],
            "discovery": [],
            "ai_findings": [],
        })

    return jsonify({
        "page_id": page_id,
        "test_result_id": result.id,
        "tested_at": _iso(result.test_date),
        "violations": [v.to_dict() for v in result.violations],
        "warnings": [v.to_dict() for v in result.warnings],
        "info": [v.to_dict() for v in result.info],
        "discovery": [v.to_dict() for v in result.discovery],
        "ai_findings": [f.to_dict() for f in result.ai_findings],
    })


def _serialize_test_state_matrix(
    matrix: TestStateMatrix, *, page_id: str, website_id: str
) -> dict[str, Any]:
    """Project a :class:`TestStateMatrix` to a JSON-safe dict.

    Datetimes emit as ISO 8601; the legacy ``matrix`` (row/column
    boolean grid) is omitted because the canonical storage is
    ``combinations`` — clients reading this endpoint should never need
    to know the legacy shape. ``id`` is ``None`` for an unsaved default
    matrix (when no row existed yet for this page).
    """

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    return {
        "id": matrix.id,
        "page_id": page_id,
        "website_id": website_id,
        "scripts": [s.to_dict() for s in matrix.scripts],
        "combinations": list(matrix.combinations),
        "created_date": _iso(matrix.created_date),
        "last_modified": _iso(matrix.last_modified),
        "created_by": matrix.created_by,
    }


@api_bp.route("/pages/<page_id>/matrix", methods=["GET"])
@api_endpoint
def get_page_matrix(page_id: str) -> tuple[Response, int] | Response:
    """Read the test-state matrix for a page.

    A page can have at most one matrix. If none has been saved yet, the
    handler returns a *default* in-memory matrix derived from the page's
    currently-enabled multi-state scripts, with sequential combinations
    initialised the same way the legacy HTML form would render them.
    ``id`` is ``null`` in that case so clients can tell the matrix has
    not yet been persisted.
    """
    page = get_db().get_page(page_id)
    if page is None:
        raise NotFoundError(f"page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, website_id=page.website_id
    )

    matrix = get_db().get_test_state_matrix_by_page(page_id)
    if matrix is None:
        matrix = TestStateMatrix(page_id=page_id, website_id=page.website_id)
        scripts = get_db().get_scripts_for_page_v2(
            page_id=page_id, website_id=page.website_id, enabled_only=False
        )
        testable_scripts = [
            s for s in scripts
            if s.enabled and (s.test_before_execution or s.test_after_execution)
        ]
        for script in testable_scripts:
            if not script.id:
                continue
            matrix.scripts.append(ScriptStateDefinition(
                script_id=script.id,
                script_name=script.name,
                test_before=script.test_before_execution,
                test_after=script.test_after_execution,
                execution_order=len(matrix.scripts),
            ))
        if matrix.scripts:
            matrix.initialize_matrix()

    return jsonify(_serialize_test_state_matrix(
        matrix, page_id=page_id, website_id=page.website_id
    ))


def _parse_matrix_combinations(raw: Any, *, field: str) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        raise ValidationError(
            f"{field} must be a list of state combinations",
            errors=(
                _FieldError(field=field, code="invalid_type", message="must be list"),
            ),
        )
    raw_list = _iter_to_any_list(raw)
    parsed: list[dict[str, str]] = []
    for idx, item in enumerate(raw_list):
        if not isinstance(item, dict):
            raise ValidationError(
                f"{field}[{idx}] must be an object mapping script_id → state",
                errors=(
                    _FieldError(
                        field=f"{field}[{idx}]",
                        code="invalid_type",
                        message="must be object",
                    ),
                ),
            )
        item_dict = cast(dict[str, Any], item)
        normalized: dict[str, str] = {}
        for sid, state in item_dict.items():
            if not isinstance(state, str):
                raise ValidationError(
                    f"{field}[{idx}].{sid} must be a string",
                    errors=(
                        _FieldError(
                            field=f"{field}[{idx}].{sid}",
                            code="invalid_type",
                            message="must be string",
                        ),
                    ),
                )
            if state not in ("before", "after", "none"):
                raise ValidationError(
                    f"{field}[{idx}].{sid} must be one of before|after|none",
                    errors=(
                        _FieldError(
                            field=f"{field}[{idx}].{sid}",
                            code="invalid_value",
                            message="must be before|after|none",
                        ),
                    ),
                )
            normalized[sid] = state
        parsed.append(normalized)
    return parsed


def _parse_script_order(raw: Any, *, field: str) -> dict[str, int]:
    if not isinstance(raw, list):
        raise ValidationError(
            f"{field} must be a list of {{script_id, execution_order}} objects",
            errors=(
                _FieldError(field=field, code="invalid_type", message="must be list"),
            ),
        )
    raw_list = _iter_to_any_list(raw)
    order_map: dict[str, int] = {}
    for idx, item in enumerate(raw_list):
        if not isinstance(item, dict):
            raise ValidationError(
                f"{field}[{idx}] must be an object",
                errors=(
                    _FieldError(
                        field=f"{field}[{idx}]",
                        code="invalid_type",
                        message="must be object",
                    ),
                ),
            )
        item_dict = cast(dict[str, Any], item)
        sid = item_dict.get("script_id")
        order = item_dict.get("execution_order")
        if not isinstance(sid, str):
            raise ValidationError(
                f"{field}[{idx}].script_id must be a string",
                errors=(
                    _FieldError(
                        field=f"{field}[{idx}].script_id",
                        code="invalid_type",
                        message="must be string",
                    ),
                ),
            )
        if not isinstance(order, int) or isinstance(order, bool):
            raise ValidationError(
                f"{field}[{idx}].execution_order must be an integer",
                errors=(
                    _FieldError(
                        field=f"{field}[{idx}].execution_order",
                        code="invalid_type",
                        message="must be int",
                    ),
                ),
            )
        order_map[sid] = order
    return order_map


@api_bp.route("/pages/<page_id>/matrix", methods=["PUT"])
@api_endpoint
def replace_page_matrix(page_id: str) -> tuple[Response, int] | Response:
    """Full replace of the page's test-state matrix.

    Body shape:

        {
          "combinations": [
            {"script_id_1": "before", "script_id_2": "before"},
            {"script_id_1": "after",  "script_id_2": "after"},
            ...
          ],
          "script_order": [          // optional
            {"script_id": "...", "execution_order": 0},
            ...
          ]
        }

    The ``scripts`` array on the matrix is always rebuilt from the page's
    currently-enabled multi-state scripts at save time — clients don't
    have to (and can't) submit it. This matches the legacy HTML form's
    POST handler, which re-derives ``scripts`` from
    :meth:`Database.get_scripts_for_page_v2` on every save.

    ``combinations`` is **required**; ``script_order`` is optional and
    only repositions scripts already present on the page.

    On success, returns ``200`` with the persisted matrix. The verb is
    PUT because the matrix is an idempotent 1:1 sub-resource of the
    page — there is no PATCH and no DELETE; clearing it means PUTing an
    empty ``combinations`` array.
    """
    page = get_db().get_page(page_id)
    if page is None:
        raise NotFoundError(f"page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=page.website_id
    )

    body = _require_dict_body()
    if "combinations" not in body:
        raise ValidationError(
            "combinations is required",
            errors=(
                _FieldError(
                    field="combinations", code="required", message="required"
                ),
            ),
        )
    combinations = _parse_matrix_combinations(
        body["combinations"], field="combinations"
    )
    order_map = (
        _parse_script_order(body["script_order"], field="script_order")
        if "script_order" in body
        else {}
    )

    scripts = get_db().get_scripts_for_page_v2(
        page_id=page_id, website_id=page.website_id, enabled_only=False
    )
    testable_scripts = [
        s for s in scripts
        if s.enabled and (s.test_before_execution or s.test_after_execution)
    ]

    matrix = get_db().get_test_state_matrix_by_page(page_id)
    if matrix is None:
        matrix = TestStateMatrix(page_id=page_id, website_id=page.website_id)

    matrix.scripts = []
    for idx, script in enumerate(testable_scripts):
        if not script.id:
            continue
        matrix.scripts.append(ScriptStateDefinition(
            script_id=script.id,
            script_name=script.name,
            test_before=script.test_before_execution,
            test_after=script.test_after_execution,
            execution_order=order_map.get(script.id, idx),
        ))
    matrix.scripts.sort(key=lambda s: s.execution_order)
    matrix.combinations = combinations
    matrix.matrix = {}

    if matrix.mongo_id:
        get_db().update_test_state_matrix(matrix)
    else:
        new_id = get_db().create_test_state_matrix(matrix)
        refreshed = get_db().get_test_state_matrix(new_id)
        if refreshed is None:
            raise ConflictError("matrix failed to persist")
        matrix = refreshed

    return jsonify(_serialize_test_state_matrix(
        matrix, page_id=page_id, website_id=page.website_id
    ))


@api_bp.route("/pages/<page_id>/test-runs/latest/cancel", methods=["POST"])
@api_endpoint
def cancel_page_test_run_latest(page_id: str) -> tuple[Response, int] | Response:
    """Request cancellation of the page's in-flight test run.

    Returns 202 because cancellation is asynchronous — the test worker
    polls the task's cancellation flag and exits at its next checkpoint.
    The page status flips to ``DISCOVERED`` (never tested) or ``TESTED``
    (has prior results) immediately so the UI can update without
    waiting for the worker to actually unwind.

    Errors:

    - **404** — page does not exist
    - **409** — page is not in QUEUED/TESTING (legacy /cancel-test
      returned 200 with ``{success: false}``; the REST surface uses 409
      so callers can branch on status_code alone)

    Unlike :func:`cancel_job_rest`, this endpoint targets the task
    runner directly: single-page tests run as bare ``test_page_<id>_*``
    tasks without a JobManager record. The lookup mirrors the legacy
    ``/cancel-test`` handler.
    """
    from auto_a11y.core.task_runner import task_runner

    page = get_db().get_page(page_id)
    if page is None:
        raise NotFoundError(f"page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=page.website_id
    )

    if page.status not in (PageStatus.QUEUED, PageStatus.TESTING):
        raise ConflictError(
            f"page {page_id} is not being tested (current status: {page.status.value})"
        )

    task_pattern = f"test_page_{page_id}_"
    target_task_id: str | None = None
    for active_task_id in task_runner.get_active_tasks():
        if active_task_id.startswith(task_pattern):
            target_task_id = active_task_id
            break

    cancelled = False
    if target_task_id is not None:
        try:
            cancelled = task_runner.cancel_task(target_task_id)
        except Exception as exc:
            logger.error(f"Error cancelling page task {target_task_id}: {exc}")

    # Even if the task was already past the cancel checkpoint or had
    # never been spawned (status set to QUEUED by an earlier failed
    # path), flip the page back to a non-running state so the UI
    # doesn't lock up on a phantom in-flight run.
    if cancelled or page.status == PageStatus.QUEUED:
        test_history = get_db().get_test_results(page_id=page_id, limit=1)
        page.status = PageStatus.TESTED if test_history else PageStatus.DISCOVERED
        get_db().update_page(page)

    refreshed = get_db().get_page(page_id)
    if refreshed is None:
        raise ConflictError(f"page {page_id} disappeared after cancel")

    return jsonify({
        "page_id": page_id,
        "task_id": target_task_id,
        "cancellation_requested": cancelled,
        "status": refreshed.status.value,
    }), 202


# ---------------------------------------------------------------------------
# Top-level test-runs + testing config (REST shape — §5.4 of
# docs/REST_API_ROADMAP.md).
#
# Replaces the hollow legacy routes on `testing_bp`:
#   - POST /testing/run-test    → only updated page.status (no real queue)
#   - POST /testing/batch-test  → only returned a fake batch_id
#   - GET/POST /testing/configure → HTML form
#
# The new routes delegate to :mod:`auto_a11y.core.test_run_service`
# (the same helper the per-page and per-website routes call) so the
# generic top-level URL actually queues work instead of just flipping
# status flags.
# ---------------------------------------------------------------------------


def _parse_optional_bool(raw: Any, *, field: str) -> bool | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    raise ValidationError(
        f"{field} must be a boolean",
        errors=(
            _FieldError(field=field, code="invalid_type", message="must be bool"),
        ),
    )


def _parse_optional_str(raw: Any, *, field: str) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    raise ValidationError(
        f"{field} must be a string",
        errors=(
            _FieldError(field=field, code="invalid_type", message="must be string"),
        ),
    )


def _parse_required_str(raw: Any, *, field: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise ValidationError(
            f"{field} is required",
            errors=(
                _FieldError(field=field, code="required", message="required"),
            ),
        )
    return raw


def _parse_str_list(raw: Any, *, field: str) -> list[str]:
    if not isinstance(raw, list):
        raise ValidationError(
            f"{field} must be a list of strings",
            errors=(
                _FieldError(field=field, code="invalid_type", message="must be list"),
            ),
        )
    raw_list = _iter_to_any_list(raw)
    result: list[str] = []
    for idx, item in enumerate(raw_list):
        if not isinstance(item, str):
            raise ValidationError(
                f"{field}[{idx}] must be a string",
                errors=(
                    _FieldError(
                        field=f"{field}[{idx}]",
                        code="invalid_type",
                        message="must be string",
                    ),
                ),
            )
        result.append(item)
    return result


@api_bp.route("/test-runs", methods=["POST"])
@api_endpoint
def create_test_run(
) -> tuple[Response, int] | Response:
    """Queue a single-page accessibility test run.

    Body:

        {
          "page_id": "...",                // required
          "enable_multi_state": bool,      // optional, default true
          "website_user_id": "..."         // optional
        }

    Returns 202 with the same handle shape as
    ``POST /api/v1/pages/<id>/test-runs`` — both call
    :func:`auto_a11y.core.test_run_service.start_page_test_run`. The
    per-page URL stays alive for clients that already know the page id
    in the path; this top-level form is for callers who already have
    the page id in their request body (e.g. a "test this page"
    bookmark service or a queue worker).

    Use :func:`create_test_runs_batch` for multiple pages — looping
    this endpoint client-side is fine but the batch shape is more
    convenient.
    """
    from auto_a11y.core.test_run_service import (
        BrowserDisabledError,
        BrowserRemoteError,
        PageNotFoundError,
        start_page_test_run,
    )

    body = _require_dict_body()
    page_id = _parse_required_str(body.get("page_id"), field="page_id")
    enable_multi_state = _parse_optional_bool(
        body.get("enable_multi_state"), field="enable_multi_state"
    )
    if enable_multi_state is None:
        enable_multi_state = True
    website_user_id = _parse_optional_str(
        body.get("website_user_id"), field="website_user_id"
    )

    page = get_db().get_page(page_id)
    if page is None:
        raise NotFoundError(f"page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=page.website_id
    )

    try:
        handle = start_page_test_run(
            get_db(),
            get_app_config(),
            page_id,
            enable_multi_state=enable_multi_state,
            website_user_id=website_user_id,
        )
    except BrowserDisabledError as exc:
        raise ConflictError(str(exc)) from exc
    except BrowserRemoteError as exc:
        raise ConflictError(str(exc)) from exc
    except PageNotFoundError as exc:
        # Race: the page existed at the auth check but disappeared
        # between then and the service call. Treat as a fresh 404.
        raise NotFoundError(str(exc)) from exc

    return jsonify({
        "job_id": handle.job_id,
        "page_id": handle.page_id,
        "multi_state": handle.multi_state,
        "status": "queued",
    }), 202


@api_bp.route("/test-runs/batch", methods=["POST"])
@api_endpoint
def create_test_runs_batch(
) -> tuple[Response, int] | Response:
    """Queue accessibility test runs for several pages.

    Body:

        {
          "page_ids": ["...", "..."],      // required, 1..N entries
          "enable_multi_state": bool,      // optional, default true
          "website_user_id": "..."         // optional, applied to every run
        }

    Each page is queued through
    :func:`auto_a11y.core.test_run_service.start_page_test_run`, the
    same helper :func:`create_test_run` and the per-page endpoint use.
    Pages from different websites can be mixed — auth is enforced
    per-page so a non-admin caller will fail on the first page they
    don't have access to.

    On success returns 202 with a list of handles, one per queued
    page, in the same order as the input. If ``page_ids`` contains an
    unknown id, the request fails with 404 *before* anything is queued,
    so the operation is all-or-nothing.

    Use :func:`test_website` (``POST /websites/<id>/test-runs``) when
    you want to test every page on a website — it handles the
    per-user sequential execution and PDF audit folding that this
    endpoint deliberately doesn't.
    """
    from auto_a11y.core.test_run_service import (
        BrowserDisabledError,
        BrowserRemoteError,
        PageNotFoundError,
        start_page_test_run,
    )

    body = _require_dict_body()
    page_ids = _parse_str_list(body.get("page_ids"), field="page_ids")
    if not page_ids:
        raise ValidationError(
            "page_ids must contain at least one entry",
            errors=(
                _FieldError(
                    field="page_ids", code="too_short", message="min 1 entry"
                ),
            ),
        )
    enable_multi_state = _parse_optional_bool(
        body.get("enable_multi_state"), field="enable_multi_state"
    )
    if enable_multi_state is None:
        enable_multi_state = True
    website_user_id = _parse_optional_str(
        body.get("website_user_id"), field="website_user_id"
    )

    # Validate every page up front so we don't half-queue on a typo.
    pages: list[Page] = []
    for page_id in page_ids:
        page = get_db().get_page(page_id)
        if page is None:
            raise NotFoundError(f"page {page_id} not found")
        require_project_role(
            UserRole.ADMIN, UserRole.AUDITOR, website_id=page.website_id
        )
        pages.append(page)

    handles: list[dict[str, Any]] = []
    for page in pages:
        assert page.id is not None
        try:
            handle = start_page_test_run(
                get_db(),
                get_app_config(),
                page.id,
                enable_multi_state=enable_multi_state,
                website_user_id=website_user_id,
            )
        except BrowserDisabledError as exc:
            raise ConflictError(str(exc)) from exc
        except BrowserRemoteError as exc:
            raise ConflictError(str(exc)) from exc
        except PageNotFoundError as exc:
            raise NotFoundError(str(exc)) from exc
        handles.append({
            "job_id": handle.job_id,
            "page_id": handle.page_id,
            "multi_state": handle.multi_state,
        })

    return jsonify({
        "status": "queued",
        "pages_queued": len(handles),
        "runs": handles,
    }), 202


# Runtime config keys the testing config endpoint exposes.
# Tuple form keeps ordering deterministic across GET/PUT.
_TESTING_CONFIG_FIELDS: tuple[tuple[str, str, type[Any]], ...] = (
    # (json_field, Config attribute name, expected python type)
    ("parallel_tests", "PARALLEL_TESTS", int),
    ("test_timeout", "TEST_TIMEOUT", int),
    ("run_ai_analysis", "RUN_AI_ANALYSIS", bool),
    ("browser_headless", "BROWSER_HEADLESS", bool),
    ("viewport_width", "BROWSER_VIEWPORT_WIDTH", int),
    ("viewport_height", "BROWSER_VIEWPORT_HEIGHT", int),
    ("pages_per_page", "PAGES_PER_PAGE", int),
    ("max_pages_per_page", "MAX_PAGES_PER_PAGE", int),
    ("show_error_codes", "SHOW_ERROR_CODES", bool),
)


def _read_testing_config_field(
    cfg: Any, *, attr: str, py_type: type[Any]
) -> Any:
    """Read a single config attribute with a sensible per-type default.

    Several fields are optional on the Config dataclass (``getattr`` in
    the legacy route uses defaults like ``100`` / ``500`` / ``False``).
    Returning a typed default — instead of letting ``None`` leak into
    the JSON — keeps the response schema stable across deployments
    that haven't set every flag in ``.env``.
    """
    if py_type is bool:
        return bool(getattr(cfg, attr, False))
    if py_type is int:
        return int(getattr(cfg, attr, 0))
    return getattr(cfg, attr)


def _serialize_testing_config(cfg: Any) -> dict[str, Any]:
    return {
        field: _read_testing_config_field(cfg, attr=attr, py_type=py_type)
        for field, attr, py_type in _TESTING_CONFIG_FIELDS
    }


@api_bp.route("/testing/config", methods=["GET"])
@api_endpoint
def get_testing_config() -> tuple[Response, int] | Response:
    """Read the runtime testing config.

    Mirrors the legacy GET ``/testing/configure`` form view, but emits
    JSON only (the HTML form has been retained on ``testing_bp`` for
    the admin UI). Fields:

    - ``parallel_tests`` (int)
    - ``test_timeout`` (int, ms)
    - ``run_ai_analysis`` (bool)
    - ``browser_headless`` (bool)
    - ``viewport_width`` / ``viewport_height`` (int)
    - ``pages_per_page`` / ``max_pages_per_page`` (int — pagination defaults)
    - ``show_error_codes`` (bool — developer/debug toggle)

    Superadmin-only because this surface also gates the PUT writer
    and we don't want two role checks to drift.
    """
    require_superadmin()
    return jsonify(_serialize_testing_config(get_app_config()))


@api_bp.route("/testing/config", methods=["PUT"])
@api_endpoint
def replace_testing_config() -> tuple[Response, int] | Response:
    """Update runtime testing config keys.

    Body shape:

        {
          "parallel_tests": 4,
          "browser_headless": false,
          ...
        }

    Any subset of :data:`_TESTING_CONFIG_FIELDS` is accepted; missing
    keys are left untouched (PUT here is a *full-update-or-no-change*,
    matching the legacy POST handler that only wrote the keys present
    in the body). Unknown keys raise 400 so typos surface immediately
    instead of silently dropping.

    Type-validates each present key — pyright/mypy strict mode wants
    real ``bool`` / ``int`` values, not the JSON-coerced ``Any`` the
    legacy route happily passed straight into the Config attributes.

    Returns the full post-update config so callers can confirm the
    effective values without a follow-up GET.

    **In-process only:** writes go to the live :class:`Config` object,
    not to ``.env`` or a database — the changes survive until the
    process restarts. Persisting these settings is out of scope for
    #27; an admin-settings PR (#36) covers the persistent equivalents.
    """
    require_superadmin()
    body = _require_dict_body()

    known_fields = {f for f, _, _ in _TESTING_CONFIG_FIELDS}
    unknown = set(body) - known_fields
    if unknown:
        sorted_unknown = sorted(unknown)
        raise ValidationError(
            f"unknown config keys: {sorted_unknown}",
            errors=tuple(
                _FieldError(
                    field=key, code="unknown_field", message="unknown config key"
                )
                for key in sorted_unknown
            ),
        )

    cfg = get_app_config()
    for field, attr, py_type in _TESTING_CONFIG_FIELDS:
        if field not in body:
            continue
        value: Any = body[field]
        if py_type is bool:
            if not isinstance(value, bool):
                raise ValidationError(
                    f"{field} must be a boolean",
                    errors=(
                        _FieldError(
                            field=field, code="invalid_type", message="must be bool"
                        ),
                    ),
                )
            setattr(cfg, attr, value)
        elif py_type is int:
            # ``isinstance(True, int)`` is True in Python — exclude bools
            # so a stray ``true`` in the JSON isn't coerced to 1.
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValidationError(
                    f"{field} must be an integer",
                    errors=(
                        _FieldError(
                            field=field, code="invalid_type", message="must be int"
                        ),
                    ),
                )
            setattr(cfg, attr, value)
        else:
            setattr(cfg, attr, value)

    return jsonify(_serialize_testing_config(cfg))


# Reports API (REST shape — §5.5 of docs/REST_API_ROADMAP.md).
#
# Replaces the hollow ``POST /projects/<id>/reports`` placeholder that
# used to live here. The new routes delegate to
# :mod:`auto_a11y.core.report_run_service` so the legacy
# ``reports_bp`` blueprint and these endpoints submit jobs through the
# same code path.
#
# Out of scope for this commit (deferred):
#   - GET /api/v1/reports/<id>/file (download)
#   - DELETE /api/v1/reports/<id>   (delete)
#   - GET /api/v1/projects/<id>/report-summary
# These need a separate report-record collection so the opaque report
# id can map to a filename/path independent of the JobManager TTL —
# follow-up PR.


_VALID_REPORT_FORMATS: frozenset[str] = frozenset({
    "xlsx", "html", "csv", "pdf", "excel",
})


def _parse_report_format(raw: Any, *, field: str = "format") -> str:
    if raw is None:
        return "xlsx"
    if not isinstance(raw, str):
        raise ValidationError(
            f"{field} must be a string",
            errors=(
                _FieldError(field=field, code="invalid_type", message="must be string"),
            ),
        )
    if raw not in _VALID_REPORT_FORMATS:
        raise ValidationError(
            f"{field} must be one of {sorted(_VALID_REPORT_FORMATS)}",
            errors=(
                _FieldError(
                    field=field, code="invalid_value",
                    message=f"must be one of {sorted(_VALID_REPORT_FORMATS)}",
                ),
            ),
        )
    return raw


def _capture_report_runtime() -> tuple[Any, dict[str, Any], str, Path]:
    """Snapshot the per-request Flask state the service needs.

    Read once at the start of each report route so the background
    thread sees a consistent view of (app, config, language,
    output_dir) — the request context is gone by the time the worker
    runs.
    """
    from flask import current_app
    from auto_a11y.web.fluent import get_current_locale as _get_locale

    app = getattr(current_app, "_get_current_object")()
    config_snapshot: dict[str, Any] = get_app_config().__dict__.copy()
    locale_obj = _get_locale()
    language = str(locale_obj) if locale_obj else "en"
    output_dir = Path(get_app_config().REPORTS_DIR)
    return app, config_snapshot, language, output_dir


def _handle_report_service_errors(exc: Exception) -> tuple[Response, int]:
    """Map :mod:`report_run_service` exceptions to RFC 7807 responses.

    Called inside the route handlers' ``try`` blocks. The exceptions
    are imported lazily inside the route bodies (matching the test-run
    service routes) so this helper takes ``Exception`` and tests
    ``type(exc).__name__``; this avoids a top-level import that would
    pull report-generator code into the request path.
    """
    name = type(exc).__name__
    if name in ("ProjectNotFoundError", "WebsiteNotFoundError", "PageNotFoundError"):
        raise NotFoundError(str(exc)) from exc
    if name == "JobNotFoundError":
        raise NotFoundError(str(exc)) from exc
    if name in ("ReportScopeError", "NoTestedPagesError"):
        raise ValidationError(str(exc)) from exc
    raise exc


def _serialize_report_handle(handle: Any) -> dict[str, Any]:
    """Shape :class:`ReportRunHandle` into the 202 response body."""
    return {
        "job_id": handle.job_id,
        "scope": handle.scope,
        "display_name": handle.display_name,
        "status": "queued",
    }


@api_bp.route('/pages/<page_id>/reports', methods=['POST'])
@api_endpoint
def generate_page_report(page_id: str) -> tuple[Response, int] | Response:
    """Queue a single-page accessibility report.

    Body:

        {
          "format": "html|xlsx|csv|pdf|excel",  // optional, default xlsx
          "include_ai": bool                    // optional, default true
        }

    The page's existence is validated up front so the response is a
    clean 404 (Problem Details) rather than a 202 followed by a job
    that fails on the first generator call.
    """
    from auto_a11y.core.report_run_service import (
        start_report_generation,
    )

    page = get_db().get_page(page_id)
    if page is None:
        raise NotFoundError(f"page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
        website_id=page.website_id,
    )

    body = _require_dict_body() if request.data else {}
    report_format = _parse_report_format(body.get("format"))
    include_ai_raw = body.get("include_ai")
    include_ai = (
        include_ai_raw if isinstance(include_ai_raw, bool) else True
    )

    app, config_snapshot, language, output_dir = _capture_report_runtime()

    try:
        handle = start_report_generation(
            get_db(),
            scope="page",
            report_format=report_format,
            page_id=page_id,
            include_ai=include_ai,
            config=config_snapshot,
            language=language,
            output_dir=output_dir,
            app=app,
        )
    except Exception as exc:
        return _handle_report_service_errors(exc)

    return jsonify(_serialize_report_handle(handle)), 202


_WEBSITE_REPORT_TYPES: frozenset[str] = frozenset({
    "accessibility", "page-structure", "discovery",
})

_WEBSITE_REPORT_TYPE_TO_SCOPE: dict[str, str] = {
    "accessibility": "website",
    "page-structure": "page_structure",
    "discovery": "discovery_website",
}


def _parse_report_type(
    raw: Any, *, allowed: frozenset[str], field: str = "type", default: str
) -> str:
    if raw is None:
        return default
    if not isinstance(raw, str):
        raise ValidationError(
            f"{field} must be a string",
            errors=(
                _FieldError(field=field, code="invalid_type", message="must be string"),
            ),
        )
    if raw not in allowed:
        raise ValidationError(
            f"{field} must be one of {sorted(allowed)}",
            errors=(
                _FieldError(
                    field=field, code="invalid_value",
                    message=f"must be one of {sorted(allowed)}",
                ),
            ),
        )
    return raw


@api_bp.route('/websites/<website_id>/reports', methods=['POST'])
@api_endpoint
def generate_website_report(
    website_id: str,
) -> tuple[Response, int] | Response:
    """Queue a website-scoped report.

    Body:

        {
          "format": "html|xlsx|csv|pdf|excel",                // optional
          "type":   "accessibility|page-structure|discovery", // default accessibility
          "include_ai": bool                                  // optional, default true
        }

    The ``type`` discriminator collapses the three legacy URLs
    (``/generate/website/<id>``, ``/generate/page-structure/<id>``,
    ``/generate/discovery/website/<id>``) into one endpoint. Only the
    ``accessibility`` type reads ``include_ai`` — the others ignore it.
    """
    from auto_a11y.core.report_run_service import (
        start_report_generation,
    )

    website = get_db().get_website(website_id)
    if website is None:
        raise NotFoundError(f"website {website_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
        website_id=website_id,
    )

    body = _require_dict_body() if request.data else {}
    report_format = _parse_report_format(body.get("format"))
    report_type = _parse_report_type(
        body.get("type"), allowed=_WEBSITE_REPORT_TYPES, default="accessibility",
    )
    include_ai_raw = body.get("include_ai")
    include_ai = (
        include_ai_raw if isinstance(include_ai_raw, bool) else True
    )

    app, config_snapshot, language, output_dir = _capture_report_runtime()
    scope = _WEBSITE_REPORT_TYPE_TO_SCOPE[report_type]

    try:
        handle = start_report_generation(
            get_db(),
            scope=scope,
            report_format=report_format,
            website_id=website_id,
            include_ai=include_ai,
            config=config_snapshot,
            language=language,
            output_dir=output_dir,
            app=app,
        )
    except Exception as exc:
        return _handle_report_service_errors(exc)

    return jsonify(_serialize_report_handle(handle)), 202


_PROJECT_REPORT_TYPES: frozenset[str] = frozenset({
    "accessibility", "discovery", "recordings", "deduplicated",
})

_PROJECT_REPORT_TYPE_TO_SCOPE: dict[str, str] = {
    "accessibility": "project",
    "discovery": "discovery_project",
    "recordings": "recordings",
    "deduplicated": "deduplicated",
}


@api_bp.route('/projects/<project_id>/reports', methods=['POST'])
@api_endpoint
def generate_project_report(
    project_id: str,
) -> tuple[Response, int] | Response:
    """Queue a project-scoped report.

    Body:

        {
          "format": "html|xlsx|csv|pdf|excel",                       // optional
          "type":   "accessibility|discovery|recordings|deduplicated" // default accessibility
        }

    Replaces the previous hollow implementation that returned a fake
    ``report_id`` without actually queueing anything. The ``type``
    discriminator collapses the four legacy URLs:

    - ``/generate/project/<id>``                     → ``type=accessibility``
    - ``/generate/discovery/project/<id>``           → ``type=discovery``
    - ``/generate/recordings/<project_id>``          → ``type=recordings``
    - ``/generate/deduplicated`` (with project_id)   → ``type=deduplicated``
    """
    from auto_a11y.core.report_run_service import (
        start_report_generation,
    )

    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
        project_id=project_id,
    )

    body = _require_dict_body() if request.data else {}
    report_format = _parse_report_format(body.get("format"))
    report_type = _parse_report_type(
        body.get("type"), allowed=_PROJECT_REPORT_TYPES, default="accessibility",
    )

    app, config_snapshot, language, output_dir = _capture_report_runtime()
    scope = _PROJECT_REPORT_TYPE_TO_SCOPE[report_type]

    try:
        handle = start_report_generation(
            get_db(),
            scope=scope,
            report_format=report_format,
            project_id=project_id,
            config=config_snapshot,
            language=language,
            output_dir=output_dir,
            app=app,
        )
    except Exception as exc:
        return _handle_report_service_errors(exc)

    return jsonify(_serialize_report_handle(handle)), 202


_GENERIC_REPORT_TYPES: frozenset[str] = frozenset({
    "accessibility",
    "page-structure",
    "discovery",
    "static-html",
    "deduplicated",
    "recordings",
})


def _resolve_generic_scope(
    *,
    report_type: str,
    project_id: str | None,
    website_id: str | None,
    page_id: str | None,
) -> str:
    """Pick the right scope for ``POST /reports`` from (type, ids).

    The generic top-level endpoint is the catch-all that lets clients
    request any report shape with one body — the legacy
    ``/generate``, ``/generate/static-html``, and
    ``/generate/deduplicated`` routes collapse here. The scope
    inferred from ``type`` plus the supplied ids dictates which
    generator runs:

    - ``accessibility`` with project_id → ``project``
    - ``accessibility`` with website_id → ``website``
    - ``accessibility`` with page_id    → ``page``
    - ``accessibility`` with none       → ``all`` (all-projects roll-up)
    - ``page-structure``                → ``page_structure`` (requires website_id)
    - ``discovery`` with project_id     → ``discovery_project``
    - ``discovery`` with website_id     → ``discovery_website``
    - ``static-html``                   → ``static_html`` (accepts any/none)
    - ``deduplicated``                  → ``deduplicated`` (accepts any/none)
    - ``recordings``                    → ``recordings`` (requires project_id)
    """
    if report_type == "accessibility":
        if page_id:
            return "page"
        if website_id:
            return "website"
        if project_id:
            return "project"
        return "all"
    if report_type == "page-structure":
        return "page_structure"
    if report_type == "discovery":
        return "discovery_website" if website_id else "discovery_project"
    if report_type == "static-html":
        return "static_html"
    if report_type == "deduplicated":
        return "deduplicated"
    if report_type == "recordings":
        return "recordings"
    # Defensive — should be unreachable given _GENERIC_REPORT_TYPES.
    raise ValidationError(
        f"unsupported type: {report_type}",
        errors=(
            _FieldError(
                field="type", code="invalid_value", message="unsupported type"
            ),
        ),
    )


@api_bp.route('/reports', methods=['POST'])
@api_endpoint
def generate_generic_report() -> tuple[Response, int] | Response:
    """Generic top-level report-generation endpoint.

    Body:

        {
          "type": "accessibility|page-structure|discovery|static-html|deduplicated|recordings",
          "project_id": "...",   // optional
          "website_id": "...",   // optional
          "page_id":    "...",   // optional
          "format":     "...",   // optional, default xlsx
          "include_ai": bool     // optional, default true
        }

    The route picks the right scope from ``(type, ids)`` via
    :func:`_resolve_generic_scope`. Most clients should prefer the
    scope-specific routes (``/pages/<id>/reports``,
    ``/websites/<id>/reports``, ``/projects/<id>/reports``) which
    enforce ``required-id`` shape at the URL level — this top-level
    form is for clients that need to switch report type at runtime
    without remapping URLs.

    Authorization is checked at the ``require_project_role`` call
    against whichever scope the ids resolve to; anonymous/unauthorized
    callers get 401/403 from the service layer's auth checks.
    """
    from auto_a11y.core.report_run_service import (
        start_report_generation,
    )

    body = _require_dict_body()
    report_format = _parse_report_format(body.get("format"))
    report_type = _parse_report_type(
        body.get("type"),
        allowed=_GENERIC_REPORT_TYPES,
        default="accessibility",
    )
    project_id = _parse_optional_str(body.get("project_id"), field="project_id")
    website_id = _parse_optional_str(body.get("website_id"), field="website_id")
    page_id = _parse_optional_str(body.get("page_id"), field="page_id")
    include_ai_raw = body.get("include_ai")
    include_ai = (
        include_ai_raw if isinstance(include_ai_raw, bool) else True
    )

    # Validate target existence + enforce role before we read any
    # request-context machinery. This way a 404/403 doesn't waste a
    # config-snapshot copy.
    if page_id:
        page = get_db().get_page(page_id)
        if page is None:
            raise NotFoundError(f"page {page_id} not found")
        require_project_role(
            UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
            website_id=page.website_id,
        )
    elif website_id:
        website = get_db().get_website(website_id)
        if website is None:
            raise NotFoundError(f"website {website_id} not found")
        require_project_role(
            UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
            website_id=website_id,
        )
    elif project_id:
        if get_db().get_project(project_id) is None:
            raise NotFoundError(f"project {project_id} not found")
        require_project_role(
            UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
            project_id=project_id,
        )
    else:
        # No scope id supplied — only superadmins can request a
        # cross-project ("all") roll-up.
        require_superadmin()

    scope = _resolve_generic_scope(
        report_type=report_type,
        project_id=project_id,
        website_id=website_id,
        page_id=page_id,
    )

    app, config_snapshot, language, output_dir = _capture_report_runtime()

    try:
        handle = start_report_generation(
            get_db(),
            scope=scope,
            report_format=report_format,
            project_id=project_id,
            website_id=website_id,
            page_id=page_id,
            include_ai=include_ai,
            config=config_snapshot,
            language=language,
            output_dir=output_dir,
            app=app,
        )
    except Exception as exc:
        return _handle_report_service_errors(exc)

    return jsonify(_serialize_report_handle(handle)), 202


@api_bp.route('/jobs/<job_id>/restart', methods=['POST'])
@api_endpoint
def restart_job_rest(job_id: str) -> tuple[Response, int] | Response:
    """Re-queue a (typically failed/cancelled) report job.

    Reads the old job's metadata, files a cancellation request if it's
    still pending/running (the worker thread reads the flag and exits
    cleanly), and submits a fresh job with the same scope/type/format
    via :func:`auto_a11y.core.report_run_service.restart_report_generation`.

    Currently only ``REPORT_GENERATION`` jobs are restartable — other
    job types either auto-restart (discovery) or have side effects
    that don't make sense to replay (test runs, PDF audits). Restart
    of a non-report job returns 400 rather than silently treating it
    as a report.
    """
    from auto_a11y.core.report_run_service import (
        restart_report_generation,
    )

    job_manager = JobManager(get_db())
    old_job = job_manager.get_job(job_id)
    if old_job is None:
        raise NotFoundError(f"job {job_id} not found")
    if old_job.get("job_type") != JobType.REPORT_GENERATION.value:
        raise ValidationError(
            f"job {job_id} is not a report job; restart only supports report_generation",
            errors=(
                _FieldError(
                    field="job_id", code="invalid_value",
                    message="not a report job",
                ),
            ),
        )

    require_authenticated()
    app, config_snapshot, language, output_dir = _capture_report_runtime()

    try:
        handle = restart_report_generation(
            get_db(),
            old_job_id=job_id,
            config=config_snapshot,
            language=language,
            output_dir=output_dir,
            app=app,
        )
    except Exception as exc:
        return _handle_report_service_errors(exc)

    return jsonify({
        "old_job_id": job_id,
        "job_id": handle.job_id,
        "scope": handle.scope,
        "display_name": handle.display_name,
        "status": "queued",
    }), 202


# ---------------------------------------------------------------------------
# Reports retrieval (§5.5 of REST_API_ROADMAP.md — the three routes
# deferred from the generation slice).
#
# Report id = the JobManager job_id of the completed report job. The
# job record already carries (project_id, website_id, created_at,
# metadata.scope, metadata.report_type, result.filename, result.path),
# so we don't need a separate `reports` collection — the opaque id
# maps back to the on-disk filename via job.result.filename.
#
# - GET    /api/v1/reports/<id>/file          download (200 + Content-Disposition)
# - DELETE /api/v1/reports/<id>               delete file + job record (204)
# - GET    /api/v1/projects/<id>/report-summary
# ---------------------------------------------------------------------------


def _resolve_report_job(job_id: str) -> dict[str, Any]:
    """Return the JobManager record for a report job, or raise 404.

    A job is considered a "report" for this surface iff its
    ``job_type`` is ``REPORT_GENERATION``. Other job types living
    under the same JobManager (testing / discovery / pdf_audit) are
    intentionally hidden from this URL space so callers can't use a
    test-run id with the report endpoints.
    """
    job_manager = JobManager(get_db())
    record = job_manager.get_job(job_id)
    if record is None:
        raise NotFoundError(f"report {job_id} not found")
    if record.get("job_type") != JobType.REPORT_GENERATION.value:
        raise NotFoundError(f"report {job_id} not found")
    return record


def _authorize_report_record(record: dict[str, Any]) -> None:
    """Run the project-role auth check that matches the report's scope.

    Reports inherit their access policy from whichever resource the
    underlying job was scoped to:

    - ``project_id`` set → check the project
    - ``website_id`` set → check the website
    - metadata.``page_id`` set → check the page's website
    - none of the above → "all projects" roll-up → must be superadmin

    The check covers ADMIN, AUDITOR, and CLIENT for reads. Mutations
    (DELETE) raise the bar to ADMIN/AUDITOR — see
    :func:`_authorize_report_mutation`.
    """
    project_id_any: Any = record.get("project_id")
    website_id_any: Any = record.get("website_id")
    metadata_any: Any = record.get("metadata") or {}
    metadata = cast(dict[str, Any], metadata_any) if isinstance(metadata_any, dict) else {}
    page_id_any: Any = metadata.get("page_id")

    project_id = project_id_any if isinstance(project_id_any, str) else None
    website_id = website_id_any if isinstance(website_id_any, str) else None
    page_id = page_id_any if isinstance(page_id_any, str) else None

    if page_id:
        page = get_db().get_page(page_id)
        if page is None:
            # Page was deleted after the report was generated — fall
            # back to the website that's still on the job record.
            if website_id is None:
                require_superadmin()
                return
        else:
            require_project_role(
                UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
                website_id=page.website_id,
            )
            return

    if website_id:
        require_project_role(
            UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
            website_id=website_id,
        )
        return
    if project_id:
        require_project_role(
            UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
            project_id=project_id,
        )
        return

    # "all projects" roll-up — the only scope with no id on the job.
    require_superadmin()


def _authorize_report_mutation(record: dict[str, Any]) -> None:
    """ADMIN/AUDITOR variant of :func:`_authorize_report_record` for DELETE."""
    project_id_any: Any = record.get("project_id")
    website_id_any: Any = record.get("website_id")
    metadata_any: Any = record.get("metadata") or {}
    metadata = cast(dict[str, Any], metadata_any) if isinstance(metadata_any, dict) else {}
    page_id_any: Any = metadata.get("page_id")

    project_id = project_id_any if isinstance(project_id_any, str) else None
    website_id = website_id_any if isinstance(website_id_any, str) else None
    page_id = page_id_any if isinstance(page_id_any, str) else None

    if page_id:
        page = get_db().get_page(page_id)
        if page is not None:
            require_project_role(
                UserRole.ADMIN, UserRole.AUDITOR, website_id=page.website_id,
            )
            return

    if website_id:
        require_project_role(
            UserRole.ADMIN, UserRole.AUDITOR, website_id=website_id,
        )
        return
    if project_id:
        require_project_role(
            UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id,
        )
        return

    require_superadmin()


def _resolve_report_file_path(record: dict[str, Any]) -> Path:
    """Return the on-disk path of a completed report, raising 404 if the
    job isn't completed yet or the file is missing.

    The reports directory is enforced to be the configured
    ``REPORTS_DIR``: even though the job's ``result.path`` is
    server-controlled (not user input), we re-resolve under
    ``REPORTS_DIR`` and check ``is_relative_to`` so a stray absolute
    path can't escape the directory.
    """
    status = record.get("status")
    if status != JobStatus.COMPLETED.value:
        raise NotFoundError(
            f"report not ready (status: {status})"
        )

    result_any: Any = record.get("result") or {}
    if not isinstance(result_any, dict):
        raise NotFoundError("report result missing")
    result = cast(dict[str, Any], result_any)
    filename_any: Any = result.get("filename")
    if not isinstance(filename_any, str) or not filename_any:
        raise NotFoundError("report filename missing")

    reports_dir = Path(get_app_config().REPORTS_DIR).resolve()
    candidate = (reports_dir / filename_any).resolve()
    # Guard against absolute or traversing filenames just in case the
    # generator ever wrote one — the report record is not user input
    # but defence-in-depth is cheap here.
    try:
        candidate.relative_to(reports_dir)
    except ValueError as exc:
        raise NotFoundError("report path outside reports dir") from exc
    if not candidate.exists():
        raise NotFoundError("report file no longer on disk")
    return candidate


@api_bp.route("/reports/<report_id>/file", methods=["GET"])
@api_endpoint
def download_report_file(report_id: str) -> Response | tuple[Response, int]:
    """Stream the completed report file with Content-Disposition.

    The opaque ``report_id`` is the underlying job id. Status 404 is
    returned for any of: job missing, job not a report-generation
    job, job not yet COMPLETED, result/filename missing, file removed
    from disk after generation.

    Authorization mirrors the report's scope:

    - project-scoped → ADMIN/AUDITOR/CLIENT on the project
    - website-scoped → ADMIN/AUDITOR/CLIENT on the website
    - page-scoped    → ADMIN/AUDITOR/CLIENT on the page's website
    - cross-project  → superadmin
    """
    from flask import send_file

    record = _resolve_report_job(report_id)
    _authorize_report_record(record)
    file_path = _resolve_report_file_path(record)

    download_name = file_path.name
    return send_file(
        str(file_path), as_attachment=True, download_name=download_name,
    )


@api_bp.route("/reports/<report_id>", methods=["DELETE"])
@api_endpoint
def delete_report(report_id: str) -> tuple[Response, int]:
    """Delete the report file and the underlying job record.

    Idempotent at the file level — a missing on-disk file still
    yields 204 as long as the job record exists and gets deleted. A
    missing job record raises 404.

    Requires ADMIN or AUDITOR on the report's scope (or superadmin
    for cross-project rollups).
    """
    record = _resolve_report_job(report_id)
    _authorize_report_mutation(record)

    # Best-effort file removal — proceed to the job-record delete
    # even if the file is gone or unreadable. Log so an oncall can
    # spot a leaked file later.
    result_any: Any = record.get("result") or {}
    if isinstance(result_any, dict):
        result_dict = cast(dict[str, Any], result_any)
        filename_any: Any = result_dict.get("filename")
        if isinstance(filename_any, str) and filename_any:
            reports_dir = Path(get_app_config().REPORTS_DIR).resolve()
            try:
                candidate = (reports_dir / filename_any).resolve()
                candidate.relative_to(reports_dir)
                if candidate.exists():
                    candidate.unlink()
            except (ValueError, OSError) as exc:
                logger.warning(
                    "report %s: file removal skipped (%s)", report_id, exc,
                )

    JobManager(get_db()).collection.delete_one({"job_id": report_id})
    return Response(status=204), 204


def _iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return None


def _summarize_record(record: dict[str, Any]) -> dict[str, Any]:
    """Shape a completed-report job into the summary list-item form."""
    metadata_any: Any = record.get("metadata") or {}
    metadata = cast(dict[str, Any], metadata_any) if isinstance(metadata_any, dict) else {}
    result_any: Any = record.get("result") or {}
    result = cast(dict[str, Any], result_any) if isinstance(result_any, dict) else {}
    return {
        "id": record.get("job_id"),
        "scope": metadata.get("scope"),
        "report_type": metadata.get("report_type"),
        "display_name": metadata.get("display_name"),
        "filename": result.get("filename"),
        "created_at": _iso_or_none(record.get("created_at")),
        "completed_at": _iso_or_none(record.get("completed_at")),
    }


@api_bp.route(
    "/projects/<project_id>/report-summary", methods=["GET"]
)
@api_endpoint
def get_project_report_summary(
    project_id: str,
) -> tuple[Response, int] | Response:
    """Aggregate the project's completed report-generation jobs.

    Returns:

        {
          "project_id": "...",
          "total_completed": 7,
          "by_scope":      {"project": 4, "discovery_project": 3},
          "by_report_type": {"html": 2, "xlsx": 4, "csv": 1},
          "recent": [<latest 10, newest first, summary shape>]
        }

    Counts and the ``recent`` list both filter to
    ``status=COMPLETED`` — failed/cancelled jobs are intentionally
    omitted because the summary is a "what reports are available to
    download right now" view. Use ``GET /jobs?status=...`` (and the
    forthcoming :doc:`/jobs` filters from #54) for the broader job
    history.
    """
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
        project_id=project_id,
    )

    job_manager = JobManager(get_db())
    cursor = job_manager.collection.find({
        "job_type": JobType.REPORT_GENERATION.value,
        "project_id": project_id,
        "status": JobStatus.COMPLETED.value,
    }).sort("completed_at", -1)

    records = list(cursor)
    by_scope: dict[str, int] = {}
    by_report_type: dict[str, int] = {}
    for record in records:
        metadata_any: Any = record.get("metadata") or {}
        metadata = (
            cast(dict[str, Any], metadata_any)
            if isinstance(metadata_any, dict) else {}
        )
        scope = metadata.get("scope")
        report_type = metadata.get("report_type")
        if isinstance(scope, str):
            by_scope[scope] = by_scope.get(scope, 0) + 1
        if isinstance(report_type, str):
            by_report_type[report_type] = by_report_type.get(report_type, 0) + 1

    return jsonify({
        "project_id": project_id,
        "total_completed": len(records),
        "by_scope": by_scope,
        "by_report_type": by_report_type,
        "recent": [_summarize_record(r) for r in records[:10]],
    })


# ---------------------------------------------------------------------------
# Share tokens (REST shape — uses the @api_endpoint scaffolding).
#
# These endpoints sit alongside the existing `share_tokens_bp` HTML
# routes at /share-tokens/... — those still serve the admin frontend
# until the issue #21 stage-2 migration. The new REST surface adds:
#   - JSON body input (legacy used multipart form)
#   - RFC 7807 errors
#   - DELETE-as-revoke (idempotent, 204)
#   - Cursor pagination on list
#   - Single-resource GET (legacy never had this)
#
# Per docs/REST_API_ROADMAP.md §5.10.
# ---------------------------------------------------------------------------

import hashlib  # noqa: E402
import uuid  # noqa: E402

from itsdangerous import URLSafeSerializer  # noqa: E402

from auto_a11y.models.share_token import ShareToken, TokenScope  # noqa: E402


_SHARE_TOKEN_SALT = "public-share-token"


def _share_token_serializer() -> URLSafeSerializer:
    return URLSafeSerializer(get_app_config().SECRET_KEY, salt=_SHARE_TOKEN_SALT)


def _share_token_hash(token_string: str) -> str:
    return hashlib.sha256(token_string.encode("utf-8")).hexdigest()


def _build_public_url(token_string: str) -> str:
    """Build the public share URL.

    Uses ``request.host_url`` directly so the result is correct even in
    test contexts where the public blueprint is not registered (and so
    ``url_for('public.token_landing', ...)`` would raise BuildError).
    """
    host = request.host_url.rstrip("/")
    return f"{host}/t/{token_string}/"


def _serialize_share_token(token: ShareToken) -> dict[str, Any]:
    """Project a :class:`ShareToken` to a JSON-safe metadata dict.

    Never includes the raw token or the SHA-256 hash. The raw token is
    only ever returned by the create handler in the same response.
    """

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    return {
        "id": token.id,
        "scope": token.scope.value,
        "scope_id": token.scope_id,
        "label": token.label,
        "created_by": token.created_by,
        "created_at": _iso(token.created_at),
        "expires_at": _iso(token.expires_at),
        "revoked": token.revoked,
        "revoked_at": _iso(token.revoked_at),
        "last_used": _iso(token.last_used),
        "use_count": token.use_count,
        "is_valid": token.is_valid,
    }


def _parse_share_token_body(body: dict[str, Any]) -> tuple[str, datetime | None]:
    """Validate and extract ``label`` / ``expires_at`` from a create body."""
    label_raw = body.get("label")
    if not isinstance(label_raw, str) or not label_raw.strip():
        raise ValidationError(
            "label is required",
            errors=(_FieldError(field="label", code="required", message="required"),),
        )
    label = label_raw.strip()
    expires_at: datetime | None = None
    if "expires_at" in body and body["expires_at"] is not None:
        expires_at = _parse_iso_datetime(body["expires_at"], field="expires_at")
    return label, expires_at


def _create_share_token(scope: TokenScope, scope_id: str) -> tuple[Response, int]:
    """Shared logic for project- and website-scoped token creation."""
    body = _require_dict_body()
    label, expires_at = _parse_share_token_body(body)

    serializer = _share_token_serializer()
    # ``nonce`` ensures every dump produces a distinct signed string —
    # without it, ``URLSafeSerializer.dumps`` is deterministic on
    # (scope, scope_id) and a second token for the same scope collides
    # on the unique ``token_hash`` index. validate_token() looks up by
    # hash, so the nonce is never inspected on the public side.
    token_string = serializer.dumps(
        {"scope": scope.value, "scope_id": scope_id, "nonce": uuid.uuid4().hex}
    )

    token = ShareToken(
        scope=scope,
        scope_id=scope_id,
        created_by=str(current_user.get_id()) if current_user.is_authenticated else "",
        label=label,
        token_hash=_share_token_hash(token_string),
        expires_at=expires_at,
    )
    token_id = get_db().create_share_token(token)
    refreshed = get_db().get_share_token(token_id)
    if refreshed is None:
        raise ConflictError("share token failed to persist")

    payload = _serialize_share_token(refreshed)
    payload["token"] = token_string  # raw token — only ever in the create response
    payload["public_url"] = _build_public_url(token_string)
    response = jsonify(payload)
    response.headers["Location"] = f"/api/v1/share-tokens/{token_id}"
    return response, 201


@api_bp.route("/projects/<project_id>/share-tokens", methods=["POST"])
@api_endpoint
def create_project_share_token(project_id: str) -> tuple[Response, int]:
    """Create a project-scoped share token."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    if get_db().get_project(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")
    return _create_share_token(TokenScope.PROJECT, project_id)


@api_bp.route("/websites/<website_id>/share-tokens", methods=["POST"])
@api_endpoint
def create_website_share_token(website_id: str) -> tuple[Response, int]:
    """Create a website-scoped share token."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=website_id
    )
    if get_db().get_website(website_id) is None:
        raise NotFoundError(f"website {website_id} not found")
    return _create_share_token(TokenScope.WEBSITE, website_id)


def _list_share_tokens(scope: TokenScope, scope_id: str) -> Response:
    limit = parse_limit(request.args.get("limit"))
    cursor_raw = request.args.get("cursor")
    cursor = _Cursor.decode(cursor_raw) if cursor_raw else None

    query: dict[str, Any] = {"scope": scope.value, "scope_id": scope_id}
    if cursor is not None:
        from bson import ObjectId
        try:
            query["_id"] = {"$lt": ObjectId(cursor.last_id)}
        except Exception as exc:
            raise ValidationError(
                "cursor.last_id is not a valid ObjectId",
                errors=(
                    _FieldError(
                        field="cursor.last_id",
                        code="invalid_format",
                        message=str(exc),
                    ),
                ),
            ) from exc

    docs = list(get_db().share_tokens.find(query).sort("_id", -1).limit(limit + 1))
    tokens = [ShareToken.from_dict(doc) for doc in docs]
    page = paginate(
        tokens, limit=limit, get_id=lambda t: str(t.mongo_id) if t.mongo_id else ""
    )
    return jsonify(
        {
            "items": [_serialize_share_token(t) for t in page["items"]],
            "next_cursor": page["next_cursor"],
        }
    )


@api_bp.route("/projects/<project_id>/share-tokens", methods=["GET"])
@api_endpoint
def list_project_share_tokens(project_id: str) -> tuple[Response, int] | Response:
    """List share tokens scoped to a project."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    if get_db().get_project(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")
    return _list_share_tokens(TokenScope.PROJECT, project_id)


@api_bp.route("/websites/<website_id>/share-tokens", methods=["GET"])
@api_endpoint
def list_website_share_tokens(website_id: str) -> tuple[Response, int] | Response:
    """List share tokens scoped to a website."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=website_id
    )
    if get_db().get_website(website_id) is None:
        raise NotFoundError(f"website {website_id} not found")
    return _list_share_tokens(TokenScope.WEBSITE, website_id)


def _resolve_token_project_id(token: ShareToken) -> str | None:
    """Resolve a token's scope to its owning project_id (for auth checks)."""
    if token.scope is TokenScope.WEBSITE:
        website = get_db().get_website(token.scope_id)
        return website.project_id if website is not None else None
    return token.scope_id


@api_bp.route("/share-tokens/<token_id>", methods=["GET"])
@api_endpoint
def get_share_token(token_id: str) -> tuple[Response, int] | Response:
    """Get share token metadata by id."""
    token = get_db().get_share_token(token_id)
    if token is None:
        raise NotFoundError(f"share token {token_id} not found")
    project_id = _resolve_token_project_id(token)
    if project_id is None:
        # Token references a deleted scope — treat as not found rather
        # than expose its existence to anyone with the id.
        raise NotFoundError(f"share token {token_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    return jsonify(_serialize_share_token(token))


@api_bp.route("/share-tokens/<token_id>", methods=["DELETE"])
@api_endpoint
def revoke_share_token(token_id: str) -> tuple[Response, int]:
    """Revoke a share token (idempotent — repeated DELETE returns 204)."""
    token = get_db().get_share_token(token_id)
    if token is None:
        raise NotFoundError(f"share token {token_id} not found")
    project_id = _resolve_token_project_id(token)
    if project_id is None:
        raise NotFoundError(f"share token {token_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    get_db().revoke_share_token(token_id)
    return Response(status=204), 204


# ---------------------------------------------------------------------------
# Page-setup scripts (REST shape — uses the @api_endpoint scaffolding).
#
# Sits alongside the existing scripts_bp HTML routes at /scripts/... —
# those still serve the admin frontend (templates call /scripts/<id>/test,
# /toggle, /delete) until issue #21 stage 2 migrates the frontend. The
# new REST surface adds:
#   - JSON body input (legacy used multipart form)
#   - RFC 7807 errors
#   - PUT replace + PATCH partial update (legacy had only "edit" + "toggle")
#   - DELETE returns 204 (legacy returned JSON)
#   - Cursor pagination on list
#
# Scripts have a ``scope`` discriminator: PAGE-scoped scripts attach to a
# single page, WEBSITE-scoped scripts run for every page on a website.
# The TEST_RUN scope is internal/runtime-injected and is not exposed via
# REST. Per docs/REST_API_ROADMAP.md §5.8.
#
# Action endpoint POST /api/v1/scripts/<id>/test-runs is deferred to a
# follow-up alongside other test-run action endpoints.
# ---------------------------------------------------------------------------

from auto_a11y.models.page_setup_script import (  # noqa: E402
    ActionType,
    ExecutionTrigger,
    PageSetupScript,
    ScriptScope,
    ScriptStep,
    ScriptValidation,
)


def _serialize_script_step(step: ScriptStep) -> dict[str, Any]:
    return {
        "step_number": step.step_number,
        "action_type": step.action_type.value,
        "description": step.description,
        "selector": step.selector,
        "value": step.value,
        "timeout": step.timeout,
        "wait_after": step.wait_after,
        "screenshot_after": step.screenshot_after,
    }


def _serialize_script_validation(validation: ScriptValidation | None) -> dict[str, Any] | None:
    if validation is None:
        return None
    return {
        "success_selector": validation.success_selector,
        "success_text": validation.success_text,
        "failure_selectors": list(validation.failure_selectors),
    }


def _serialize_script(script: PageSetupScript) -> dict[str, Any]:
    """Project a :class:`PageSetupScript` to a JSON-safe dict."""

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    stats = script.execution_stats
    return {
        "id": script.id,
        "name": script.name,
        "description": script.description,
        "scope": script.scope.value,
        "website_id": script.website_id,
        "page_id": script.page_id,
        "trigger": script.trigger.value,
        "condition_selector": script.condition_selector,
        "report_violation_if_condition_met": script.report_violation_if_condition_met,
        "violation_message": script.violation_message,
        "violation_code": script.violation_code,
        "test_before_execution": script.test_before_execution,
        "test_after_execution": script.test_after_execution,
        "expect_visible_after": list(script.expect_visible_after),
        "expect_hidden_after": list(script.expect_hidden_after),
        "clear_cookies_before": script.clear_cookies_before,
        "clear_local_storage_before": script.clear_local_storage_before,
        "wait_for_selector": script.wait_for_selector,
        "wait_timeout": script.wait_timeout,
        "enabled": script.enabled,
        "steps": [_serialize_script_step(s) for s in script.steps],
        "validation": _serialize_script_validation(script.validation),
        "created_by": script.created_by,
        "created_date": _iso(script.created_date),
        "last_modified": _iso(script.last_modified),
        "execution_stats": {
            "last_executed": _iso(stats.last_executed),
            "success_count": stats.success_count,
            "failure_count": stats.failure_count,
            "average_duration_ms": stats.average_duration_ms,
        },
    }


def _parse_action_type(raw: Any, *, field: str) -> ActionType:
    if not isinstance(raw, str):
        raise ValidationError(
            f"{field} must be a string",
            errors=(_FieldError(field=field, code="invalid_type", message="must be string"),),
        )
    try:
        return ActionType(raw)
    except ValueError as exc:
        raise ValidationError(
            f"{field} is not a recognized action type",
            errors=(_FieldError(field=field, code="invalid_value", message=str(exc)),),
        ) from exc


def _parse_execution_trigger(raw: Any, *, field: str) -> ExecutionTrigger:
    if not isinstance(raw, str):
        raise ValidationError(
            f"{field} must be a string",
            errors=(_FieldError(field=field, code="invalid_type", message="must be string"),),
        )
    try:
        return ExecutionTrigger(raw)
    except ValueError as exc:
        raise ValidationError(
            f"{field} is not a recognized trigger",
            errors=(_FieldError(field=field, code="invalid_value", message=str(exc)),),
        ) from exc


def _parse_script_step(raw: Any, *, index: int) -> ScriptStep:
    field_prefix = f"steps[{index}]"
    if not isinstance(raw, dict):
        raise ValidationError(
            f"{field_prefix} must be an object",
            errors=(_FieldError(field=field_prefix, code="invalid_type", message="must be object"),),
        )
    step_dict = cast(dict[str, Any], raw)

    description_raw = step_dict.get("description", "")
    if not isinstance(description_raw, str):
        raise ValidationError(
            f"{field_prefix}.description must be a string",
            errors=(_FieldError(field=f"{field_prefix}.description", code="invalid_type", message="must be string"),),
        )

    selector_raw = step_dict.get("selector")
    if selector_raw is not None and not isinstance(selector_raw, str):
        raise ValidationError(
            f"{field_prefix}.selector must be a string or null",
            errors=(_FieldError(field=f"{field_prefix}.selector", code="invalid_type", message="must be string"),),
        )

    value_raw = step_dict.get("value")
    if value_raw is not None and not isinstance(value_raw, str):
        raise ValidationError(
            f"{field_prefix}.value must be a string or null",
            errors=(_FieldError(field=f"{field_prefix}.value", code="invalid_type", message="must be string"),),
        )

    timeout_raw = step_dict.get("timeout", 5000)
    if isinstance(timeout_raw, bool) or not isinstance(timeout_raw, int):
        raise ValidationError(
            f"{field_prefix}.timeout must be an integer",
            errors=(_FieldError(field=f"{field_prefix}.timeout", code="invalid_type", message="must be integer"),),
        )

    wait_after_raw = step_dict.get("wait_after", 0)
    if isinstance(wait_after_raw, bool) or not isinstance(wait_after_raw, int):
        raise ValidationError(
            f"{field_prefix}.wait_after must be an integer",
            errors=(_FieldError(field=f"{field_prefix}.wait_after", code="invalid_type", message="must be integer"),),
        )

    screenshot_after_raw = step_dict.get("screenshot_after", False)
    if not isinstance(screenshot_after_raw, bool):
        raise ValidationError(
            f"{field_prefix}.screenshot_after must be a boolean",
            errors=(_FieldError(field=f"{field_prefix}.screenshot_after", code="invalid_type", message="must be boolean"),),
        )

    return ScriptStep(
        step_number=index + 1,
        action_type=_parse_action_type(step_dict.get("action_type"), field=f"{field_prefix}.action_type"),
        description=description_raw,
        selector=selector_raw,
        value=value_raw,
        timeout=int(timeout_raw),
        wait_after=int(wait_after_raw),
        screenshot_after=screenshot_after_raw,
    )


def _iter_to_any_list(value: Any) -> list[Any]:
    """Re-widen a narrowed iterable to ``list[Any]``.

    Same trick as :func:`_coerce_str_list`: by routing the value through
    a parameter typed ``Any``, pyright drops the ``list[Unknown]``
    narrowing that an outer ``isinstance(_, list)`` check imposes, and
    iteration inside this body yields properly-typed ``Any`` items.
    """
    items: list[Any] = []
    for item in value:
        items.append(item)
    return items


def _parse_script_steps(raw: Any, *, field: str) -> list[ScriptStep]:
    if not isinstance(raw, list):
        raise ValidationError(
            f"{field} must be an array",
            errors=(_FieldError(field=field, code="invalid_type", message="must be array"),),
        )
    items = _iter_to_any_list(raw)
    return [_parse_script_step(item, index=i) for i, item in enumerate(items)]


def _parse_script_validation(raw: Any, *, field: str) -> ScriptValidation | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValidationError(
            f"{field} must be an object or null",
            errors=(_FieldError(field=field, code="invalid_type", message="must be object"),),
        )
    raw_dict = cast(dict[str, Any], raw)
    success_selector = raw_dict.get("success_selector")
    success_text = raw_dict.get("success_text")
    failure_selectors_raw = raw_dict.get("failure_selectors", [])
    if success_selector is not None and not isinstance(success_selector, str):
        raise ValidationError(
            f"{field}.success_selector must be a string or null",
            errors=(_FieldError(field=f"{field}.success_selector", code="invalid_type", message="must be string"),),
        )
    if success_text is not None and not isinstance(success_text, str):
        raise ValidationError(
            f"{field}.success_text must be a string or null",
            errors=(_FieldError(field=f"{field}.success_text", code="invalid_type", message="must be string"),),
        )
    if not isinstance(failure_selectors_raw, list):
        raise ValidationError(
            f"{field}.failure_selectors must be an array",
            errors=(_FieldError(field=f"{field}.failure_selectors", code="invalid_type", message="must be array"),),
        )
    return ScriptValidation(
        success_selector=success_selector,
        success_text=success_text,
        failure_selectors=_coerce_str_list(failure_selectors_raw),
    )


def _build_script_from_body(
    body: dict[str, Any], *, scope: ScriptScope, scope_id: str
) -> PageSetupScript:
    """Construct a :class:`PageSetupScript` from a POST/PUT body."""
    name_raw = body.get("name")
    if not isinstance(name_raw, str) or not name_raw.strip():
        raise ValidationError(
            "name is required",
            errors=(_FieldError(field="name", code="required", message="required"),),
        )
    description_raw = body.get("description", "")
    if not isinstance(description_raw, str):
        raise ValidationError(
            "description must be a string",
            errors=(_FieldError(field="description", code="invalid_type", message="must be string"),),
        )

    trigger = (
        _parse_execution_trigger(body["trigger"], field="trigger")
        if "trigger" in body
        else ExecutionTrigger.ONCE_PER_PAGE
    )
    steps = _parse_script_steps(body["steps"], field="steps") if "steps" in body else []
    validation = (
        _parse_script_validation(body["validation"], field="validation")
        if "validation" in body
        else None
    )

    script = PageSetupScript(
        name=name_raw.strip(),
        description=description_raw,
        scope=scope,
        page_id=scope_id if scope is ScriptScope.PAGE else None,
        website_id=scope_id if scope is ScriptScope.WEBSITE else None,
        trigger=trigger,
        steps=steps,
        validation=validation,
        enabled=bool(body.get("enabled", True)),
        condition_selector=(
            body["condition_selector"] if isinstance(body.get("condition_selector"), str) else None
        ),
        report_violation_if_condition_met=bool(body.get("report_violation_if_condition_met", False)),
        violation_message=(
            body["violation_message"] if isinstance(body.get("violation_message"), str) else None
        ),
        violation_code=(
            body["violation_code"] if isinstance(body.get("violation_code"), str) else None
        ),
        test_before_execution=bool(body.get("test_before_execution", False)),
        test_after_execution=bool(body.get("test_after_execution", True)),
        expect_visible_after=_coerce_str_list(body.get("expect_visible_after", [])),
        expect_hidden_after=_coerce_str_list(body.get("expect_hidden_after", [])),
        clear_cookies_before=bool(body.get("clear_cookies_before", False)),
        clear_local_storage_before=bool(body.get("clear_local_storage_before", False)),
        wait_for_selector=bool(body.get("wait_for_selector", False)),
        wait_timeout=int(body.get("wait_timeout", 5000)) if not isinstance(body.get("wait_timeout"), bool) else 5000,
        created_by=str(current_user.get_id()) if current_user.is_authenticated else None,
    )
    if script.trigger is ExecutionTrigger.CONDITIONAL and not (script.condition_selector or "").strip():
        raise ValidationError(
            "condition_selector is required when trigger=conditional",
            errors=(_FieldError(field="condition_selector", code="required", message="required when trigger=conditional"),),
        )
    return script


def _apply_patch_to_script(script: PageSetupScript, body: dict[str, Any]) -> PageSetupScript:
    """Apply only the keys present in ``body`` to ``script`` in place."""
    if "name" in body:
        if not isinstance(body["name"], str) or not body["name"].strip():
            raise ValidationError(
                "name must be a non-empty string",
                errors=(_FieldError(field="name", code="invalid_value", message="must be non-empty string"),),
            )
        script.name = body["name"].strip()
    if "description" in body:
        desc = body["description"]
        if not isinstance(desc, str):
            raise ValidationError(
                "description must be a string",
                errors=(_FieldError(field="description", code="invalid_type", message="must be string"),),
            )
        script.description = desc
    if "trigger" in body:
        script.trigger = _parse_execution_trigger(body["trigger"], field="trigger")
    if "condition_selector" in body:
        cs = body["condition_selector"]
        script.condition_selector = cs if isinstance(cs, str) else None
    if "report_violation_if_condition_met" in body:
        script.report_violation_if_condition_met = bool(body["report_violation_if_condition_met"])
    if "violation_message" in body:
        vm = body["violation_message"]
        script.violation_message = vm if isinstance(vm, str) else None
    if "violation_code" in body:
        vc = body["violation_code"]
        script.violation_code = vc if isinstance(vc, str) else None
    if "test_before_execution" in body:
        script.test_before_execution = bool(body["test_before_execution"])
    if "test_after_execution" in body:
        script.test_after_execution = bool(body["test_after_execution"])
    if "expect_visible_after" in body:
        if not isinstance(body["expect_visible_after"], list):
            raise ValidationError(
                "expect_visible_after must be an array",
                errors=(_FieldError(field="expect_visible_after", code="invalid_type", message="must be array"),),
            )
        script.expect_visible_after = _coerce_str_list(body["expect_visible_after"])
    if "expect_hidden_after" in body:
        if not isinstance(body["expect_hidden_after"], list):
            raise ValidationError(
                "expect_hidden_after must be an array",
                errors=(_FieldError(field="expect_hidden_after", code="invalid_type", message="must be array"),),
            )
        script.expect_hidden_after = _coerce_str_list(body["expect_hidden_after"])
    if "clear_cookies_before" in body:
        script.clear_cookies_before = bool(body["clear_cookies_before"])
    if "clear_local_storage_before" in body:
        script.clear_local_storage_before = bool(body["clear_local_storage_before"])
    if "wait_for_selector" in body:
        script.wait_for_selector = bool(body["wait_for_selector"])
    if "wait_timeout" in body:
        wt = body["wait_timeout"]
        if isinstance(wt, bool) or not isinstance(wt, int):
            raise ValidationError(
                "wait_timeout must be an integer",
                errors=(_FieldError(field="wait_timeout", code="invalid_type", message="must be integer"),),
            )
        script.wait_timeout = int(wt)
    if "enabled" in body:
        script.enabled = bool(body["enabled"])
    if "steps" in body:
        script.steps = _parse_script_steps(body["steps"], field="steps")
    if "validation" in body:
        script.validation = _parse_script_validation(body["validation"], field="validation")
    if script.trigger is ExecutionTrigger.CONDITIONAL and not (script.condition_selector or "").strip():
        raise ValidationError(
            "condition_selector is required when trigger=conditional",
            errors=(_FieldError(field="condition_selector", code="required", message="required when trigger=conditional"),),
        )
    script.update_timestamp()
    return script


def _resolve_script_auth_context(script: PageSetupScript) -> tuple[str | None, str | None]:
    """Return ``(website_id, page_id)`` for ``require_project_role`` lookup.

    REST surfaces only PAGE- and WEBSITE-scoped scripts; TEST_RUN-scoped
    scripts are runtime-internal and not exposed.
    """
    if script.scope is ScriptScope.PAGE:
        return None, script.page_id
    if script.scope is ScriptScope.WEBSITE:
        return script.website_id, None
    return None, None


def _list_scripts_for_scope(
    scope: ScriptScope, scope_id: str, *, scope_field: str
) -> Response:
    limit = parse_limit(request.args.get("limit"))
    cursor_raw = request.args.get("cursor")
    cursor = _Cursor.decode(cursor_raw) if cursor_raw else None

    query: dict[str, Any] = {"scope": scope.value, scope_field: scope_id}
    if cursor is not None:
        from bson import ObjectId
        try:
            query["_id"] = {"$lt": ObjectId(cursor.last_id)}
        except Exception as exc:
            raise ValidationError(
                "cursor.last_id is not a valid ObjectId",
                errors=(_FieldError(field="cursor.last_id", code="invalid_format", message=str(exc)),),
            ) from exc

    docs = list(get_db().page_setup_scripts.find(query).sort("_id", -1).limit(limit + 1))
    scripts = [PageSetupScript.from_dict(doc) for doc in docs]
    page = paginate(
        scripts, limit=limit, get_id=lambda s: str(s.mongo_id) if s.mongo_id else ""
    )
    return jsonify(
        {
            "items": [_serialize_script(s) for s in page["items"]],
            "next_cursor": page["next_cursor"],
        }
    )


@api_bp.route("/pages/<page_id>/scripts", methods=["GET"])
@api_endpoint
def list_page_scripts_rest(page_id: str) -> tuple[Response, int] | Response:
    """List page-scoped setup scripts."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, page_id=page_id
    )
    if get_db().get_page(page_id) is None:
        raise NotFoundError(f"page {page_id} not found")
    return _list_scripts_for_scope(ScriptScope.PAGE, page_id, scope_field="page_id")


@api_bp.route("/pages/<page_id>/scripts", methods=["POST"])
@api_endpoint
def create_page_script_rest(page_id: str) -> tuple[Response, int]:
    """Create a page-scoped setup script."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, page_id=page_id
    )
    if get_db().get_page(page_id) is None:
        raise NotFoundError(f"page {page_id} not found")

    body = _require_dict_body()
    script = _build_script_from_body(body, scope=ScriptScope.PAGE, scope_id=page_id)
    script_id = get_db().create_page_setup_script(script)
    refreshed = get_db().get_page_setup_script(script_id)
    if refreshed is None:
        raise ConflictError("script failed to persist")
    response = jsonify(_serialize_script(refreshed))
    response.headers["Location"] = f"/api/v1/scripts/{script_id}"
    return response, 201


@api_bp.route("/websites/<website_id>/scripts", methods=["GET"])
@api_endpoint
def list_website_scripts_rest(website_id: str) -> tuple[Response, int] | Response:
    """List website-scoped setup scripts."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, website_id=website_id
    )
    if get_db().get_website(website_id) is None:
        raise NotFoundError(f"website {website_id} not found")
    return _list_scripts_for_scope(ScriptScope.WEBSITE, website_id, scope_field="website_id")


@api_bp.route("/websites/<website_id>/scripts", methods=["POST"])
@api_endpoint
def create_website_script_rest(website_id: str) -> tuple[Response, int]:
    """Create a website-scoped setup script."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=website_id
    )
    if get_db().get_website(website_id) is None:
        raise NotFoundError(f"website {website_id} not found")

    body = _require_dict_body()
    script = _build_script_from_body(body, scope=ScriptScope.WEBSITE, scope_id=website_id)
    script_id = get_db().create_page_setup_script(script)
    refreshed = get_db().get_page_setup_script(script_id)
    if refreshed is None:
        raise ConflictError("script failed to persist")
    response = jsonify(_serialize_script(refreshed))
    response.headers["Location"] = f"/api/v1/scripts/{script_id}"
    return response, 201


@api_bp.route("/scripts/<script_id>", methods=["GET"])
@api_endpoint
def get_script_rest(script_id: str) -> tuple[Response, int] | Response:
    """Get a setup script by id."""
    script = get_db().get_page_setup_script(script_id)
    if script is None:
        raise NotFoundError(f"script {script_id} not found")
    if script.scope is ScriptScope.TEST_RUN:
        # TEST_RUN-scoped scripts are runtime-internal; they are not part
        # of the REST surface, so return 404 rather than expose them.
        raise NotFoundError(f"script {script_id} not found")
    website_id, page_id = _resolve_script_auth_context(script)
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
        website_id=website_id, page_id=page_id,
    )
    return jsonify(_serialize_script(script))


@api_bp.route("/scripts/<script_id>", methods=["PUT"])
@api_endpoint
def replace_script_rest(script_id: str) -> tuple[Response, int] | Response:
    """Full replace of a setup script's editable fields.

    Server-managed fields (created_date, created_by, execution_stats,
    scope/page_id/website_id) are preserved.
    """
    existing = get_db().get_page_setup_script(script_id)
    if existing is None:
        raise NotFoundError(f"script {script_id} not found")
    if existing.scope is ScriptScope.TEST_RUN:
        raise NotFoundError(f"script {script_id} not found")
    website_id, page_id = _resolve_script_auth_context(existing)
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR,
        website_id=website_id, page_id=page_id,
    )
    body = _require_dict_body()
    scope_id = existing.page_id if existing.scope is ScriptScope.PAGE else existing.website_id
    if scope_id is None:
        raise ConflictError("existing script has no scope id")
    replaced = _build_script_from_body(body, scope=existing.scope, scope_id=scope_id)
    replaced.mongo_id = existing.mongo_id
    replaced.created_by = existing.created_by
    replaced.created_date = existing.created_date
    replaced.execution_stats = existing.execution_stats
    replaced.update_timestamp()
    if not get_db().update_page_setup_script(replaced):
        raise ConflictError("script could not be updated")
    return jsonify(_serialize_script(replaced))


@api_bp.route("/scripts/<script_id>", methods=["PATCH"])
@api_endpoint
def patch_script_rest(script_id: str) -> tuple[Response, int] | Response:
    """Partial update — covers the legacy enable/disable toggle (``{"enabled": false}``)
    plus any other field-level edit."""
    script = get_db().get_page_setup_script(script_id)
    if script is None:
        raise NotFoundError(f"script {script_id} not found")
    if script.scope is ScriptScope.TEST_RUN:
        raise NotFoundError(f"script {script_id} not found")
    website_id, page_id = _resolve_script_auth_context(script)
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR,
        website_id=website_id, page_id=page_id,
    )
    body = _require_dict_body()
    patched = _apply_patch_to_script(script, body)
    if not get_db().update_page_setup_script(patched):
        raise ConflictError("script could not be updated")
    return jsonify(_serialize_script(patched))


@api_bp.route("/scripts/<script_id>", methods=["DELETE"])
@api_endpoint
def delete_script_rest(script_id: str) -> tuple[Response, int]:
    """Delete a setup script."""
    script = get_db().get_page_setup_script(script_id)
    if script is None:
        raise NotFoundError(f"script {script_id} not found")
    if script.scope is ScriptScope.TEST_RUN:
        raise NotFoundError(f"script {script_id} not found")
    website_id, page_id = _resolve_script_auth_context(script)
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR,
        website_id=website_id, page_id=page_id,
    )
    get_db().delete_page_setup_script(script_id)
    return Response(status=204), 204


@api_bp.route("/scripts/<script_id>/test-runs", methods=["POST"])
@api_endpoint
def run_script_test(
    script_id: str,
) -> tuple[Response, int] | Response:
    """Execute a setup script against its target URL and return the result.

    **Synchronous** — distinct from every other ``/test-runs`` endpoint
    on this surface. The legacy ``POST /scripts/<id>/test`` blocks the
    request thread until the browser executes the script, and clients
    rely on the inline result; this REST shape preserves that semantic
    even though the URL implies async by convention. The 200 status
    code (rather than the usual 202) signals "done synchronously" so
    callers don't poll a non-existent job.

    Target URL resolution:

    - PAGE-scoped scripts run against the linked page's URL
    - WEBSITE-scoped scripts run against the website's root URL
    - TEST_RUN-scoped scripts are runtime-internal and surface as 404
      from this endpoint (matching the rest of the scripts REST API)

    Body fields are accepted but currently ignored — the script's
    persisted ``steps`` are the authoritative input. Accepting the
    body shape keeps the door open for per-run overrides (e.g.
    ``environment_vars``) without an API version bump.

    Response shape:

        {
          "script_id":      "...",
          "success":        true|false,
          "duration_ms":    1234,
          "steps_executed": 7,
          "error":          null | "...",
          "target_url":     "https://..."
        }

    Errors:

    - **404** — script doesn't exist, is TEST_RUN-scoped, or its
      target (page / website) can't be resolved
    - **409** — server has no browser configured (BROWSER_MODE='disabled'
      or 'remote' — the browser pipeline is local-only for this endpoint)
    - **500** — script raises during execution; the body carries the
      error message so the operator can debug without polling logs
    """
    import asyncio

    from auto_a11y.core.browser_manager import BrowserManager
    from auto_a11y.testing.script_executor import ScriptExecutor

    script = get_db().get_page_setup_script(script_id)
    if script is None:
        raise NotFoundError(f"script {script_id} not found")
    if script.scope is ScriptScope.TEST_RUN:
        # TEST_RUN-scoped scripts are runtime-internal and not exposed.
        raise NotFoundError(f"script {script_id} not found")

    website_id, page_id = _resolve_script_auth_context(script)
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR,
        website_id=website_id, page_id=page_id,
    )

    if not script.website_id:
        raise NotFoundError(
            f"script {script_id} has no website target"
        )
    website = get_db().get_website(script.website_id)
    if website is None:
        raise NotFoundError(
            f"website {script.website_id} for script {script_id} not found"
        )

    target_url: str
    if script.scope is ScriptScope.PAGE:
        if not script.page_id:
            raise NotFoundError(
                f"page-scoped script {script_id} has no page target"
            )
        target_page = get_db().get_page(script.page_id)
        if target_page is None:
            raise NotFoundError(
                f"page {script.page_id} for script {script_id} not found"
            )
        target_url = target_page.url
    else:
        target_url = website.url

    # Reject deployments that don't run a local browser. The script
    # test endpoint is *not* implemented as a queued job, so it can't
    # transparently fall back to a remote runner the way test-runs
    # can — surface the misconfiguration as 409 instead of pretending
    # the test ran with zero steps.
    browser_mode = getattr(get_app_config(), "BROWSER_MODE", "local")
    if browser_mode in ("disabled", "remote"):
        raise ConflictError(
            f"script test requires local browser; BROWSER_MODE={browser_mode!r}"
        )

    # Body is currently a no-op but parsed for the validation 400 shape.
    _ = _require_dict_body() if request.data else {}

    project = (
        get_db().get_project(website.project_id) if website.project_id else None
    )
    browser_config: dict[str, Any] = get_app_config().__dict__.copy()
    if project is not None and project.config:
        browser_config["stealth_mode"] = project.config.get(
            "stealth_mode", False
        )
        headless_setting = project.config.get("headless_browser", "true")
        browser_config["BROWSER_HEADLESS"] = headless_setting == "true"
    else:
        browser_config["stealth_mode"] = False

    async def _run_test() -> dict[str, Any]:
        # Local async coroutine — instantiates a fresh browser per
        # request. Matches the legacy handler: no pooling, no reuse;
        # the test endpoint is rare enough that the per-request
        # browser-launch cost is acceptable.
        manager = BrowserManager(browser_config)
        try:
            await manager.start()
            context = await manager.create_context()
            page_obj = await context.new_page()
            await page_obj.goto(
                target_url, wait_until="networkidle", timeout=30000,
            )
            executor = ScriptExecutor()
            return await executor.execute_script(page_obj, script)
        finally:
            await manager.stop()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(_run_test())
    except Exception as exc:
        logger.error(
            "Error running script %s against %s: %s",
            script_id, target_url, exc,
        )
        # Mirror the legacy route's 500 shape but emit a stable
        # JSON envelope rather than a raw error string. The
        # @api_endpoint wrapper would intercept ApiError subclasses;
        # here we want the *test result*, not the route, to be the
        # thing reporting failure.
        return jsonify({
            "script_id": script_id,
            "success": False,
            "duration_ms": 0,
            "steps_executed": 0,
            "error": str(exc),
            "target_url": target_url,
        }), 500
    finally:
        loop.close()

    return jsonify({
        "script_id": script_id,
        "success": bool(result.get("success", False)),
        "duration_ms": int(result.get("duration_ms", 0) or 0),
        "steps_executed": int(result.get("steps_executed", 0) or 0),
        "error": result.get("error"),
        "target_url": target_url,
    })


# ---------------------------------------------------------------------------
# Recordings (REST shape — uses the @api_endpoint scaffolding).
#
# Sits alongside the existing recordings_bp HTML routes at /recordings/...
# (still serving the admin frontend) and the legacy ad-hoc JSON endpoints
# at /recordings/api/list, /recordings/api/<id>/issues, and
# /recordings/api/issue/<id>/status. Per docs/REST_API_ROADMAP.md §5.6.
#
# In scope for this PR: read/update/delete on existing recordings + their
# issues. Out of scope (deferred to a follow-up PR): multipart upload
# (`POST /api/v1/recordings`) — that involves file-system handoff,
# DictaphoneImporter parsing, and bulk issue creation, large enough to
# deserve its own review.
# ---------------------------------------------------------------------------

from auto_a11y.models.recording import Recording, RecordingType  # noqa: E402
from auto_a11y.models.recording_issue import RecordingIssue  # noqa: E402


_VALID_ISSUE_STATUSES: frozenset[str] = frozenset(
    {"open", "in_progress", "resolved", "verified"}
)


def _serialize_recording(recording: Recording) -> dict[str, Any]:
    """Project a :class:`Recording` to a JSON-safe dict."""

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    return {
        "id": recording.id,
        "recording_id": recording.recording_id,
        "title": recording.title,
        "description": recording.description,
        "duration": recording.duration,
        "recorded_date": _iso(recording.recorded_date),
        "auditor_name": recording.auditor_name,
        "auditor_role": recording.auditor_role,
        "recording_type": recording.recording_type.value,
        "project_id": recording.project_id,
        "testing_scope": dict(recording.testing_scope),
        "website_ids": list(recording.website_ids),
        "page_urls": list(recording.page_urls),
        "page_ids": list(recording.page_ids),
        "discovered_page_ids": list(recording.discovered_page_ids),
        "component_names": list(recording.component_names),
        "app_screens": list(recording.app_screens),
        "device_sections": list(recording.device_sections),
        "task_description": recording.task_description,
        "total_issues": recording.total_issues,
        "high_impact_count": recording.high_impact_count,
        "medium_impact_count": recording.medium_impact_count,
        "low_impact_count": recording.low_impact_count,
        "tags": list(recording.tags),
        "notes": recording.notes,
        "created_at": _iso(recording.created_at),
        "updated_at": _iso(recording.updated_at),
    }


def _serialize_recording_issue(issue: RecordingIssue) -> dict[str, Any]:
    """Project a :class:`RecordingIssue` to a JSON-safe dict."""

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    return {
        "id": issue.id,
        "recording_id": issue.recording_id,
        "title": issue.title,
        "short_title": issue.short_title,
        "language": issue.language,
        "what": issue.what,
        "why": issue.why,
        "who": issue.who,
        "remediation": issue.remediation,
        "impact": issue.impact.value,
        "touchpoint": issue.touchpoint,
        "timecodes": [tc.to_dict() for tc in issue.timecodes],
        "wcag": [w.to_dict() for w in issue.wcag],
        "xpath": issue.xpath,
        "element": issue.element,
        "html": issue.html,
        "project_id": issue.project_id,
        "website_ids": list(issue.website_ids),
        "page_urls": list(issue.page_urls),
        "page_ids": list(issue.page_ids),
        "component_names": list(issue.component_names),
        "app_screens": list(issue.app_screens),
        "device_sections": list(issue.device_sections),
        "task_description": issue.task_description,
        "status": issue.status,
        "assigned_to": issue.assigned_to,
        "resolution_notes": issue.resolution_notes,
        "tags": list(issue.tags),
        "created_at": _iso(issue.created_at),
        "updated_at": _iso(issue.updated_at),
    }


def _apply_patch_to_recording(recording: Recording, body: dict[str, Any]) -> Recording:
    """Apply a partial-update body to ``recording``.

    Editable fields are intentionally narrow: human-facing metadata only
    (title, description, auditor_name/role, tags, notes). Computed
    counts, project_id, recording_id, recording_type, media_file_path,
    Drupal sync, and multi-language structured content
    (key_takeaways/user_painpoints/user_assertions) are server- or
    upload-managed and not editable here.
    """
    if "title" in body:
        if not isinstance(body["title"], str) or not body["title"].strip():
            raise ValidationError(
                "title must be a non-empty string",
                errors=(_FieldError(field="title", code="invalid_value", message="must be non-empty string"),),
            )
        recording.title = body["title"].strip()
    if "description" in body:
        desc = body["description"]
        if desc is not None and not isinstance(desc, str):
            raise ValidationError(
                "description must be a string or null",
                errors=(_FieldError(field="description", code="invalid_type", message="must be string"),),
            )
        recording.description = desc.strip() if isinstance(desc, str) and desc.strip() else None
    if "auditor_name" in body:
        name = body["auditor_name"]
        if name is not None and not isinstance(name, str):
            raise ValidationError(
                "auditor_name must be a string or null",
                errors=(_FieldError(field="auditor_name", code="invalid_type", message="must be string"),),
            )
        recording.auditor_name = name if isinstance(name, str) else None
    if "auditor_role" in body:
        role = body["auditor_role"]
        if role is not None and not isinstance(role, str):
            raise ValidationError(
                "auditor_role must be a string or null",
                errors=(_FieldError(field="auditor_role", code="invalid_type", message="must be string"),),
            )
        recording.auditor_role = role if isinstance(role, str) else None
    if "tags" in body:
        if not isinstance(body["tags"], list):
            raise ValidationError(
                "tags must be an array",
                errors=(_FieldError(field="tags", code="invalid_type", message="must be array"),),
            )
        recording.tags = _coerce_str_list(body["tags"])
    if "notes" in body:
        notes = body["notes"]
        if notes is not None and not isinstance(notes, str):
            raise ValidationError(
                "notes must be a string or null",
                errors=(_FieldError(field="notes", code="invalid_type", message="must be string"),),
            )
        recording.notes = notes if isinstance(notes, str) else None
    recording.updated_at = datetime.now()
    return recording


def _apply_patch_to_recording_issue(
    issue: RecordingIssue, body: dict[str, Any]
) -> RecordingIssue:
    """Apply a partial-update body to ``issue``.

    Editable fields cover the issue-triage workflow: status (with enum
    validation), assigned_to, resolution_notes, tags. The bulk content
    fields (what/why/who/remediation, timecodes, WCAG references) are
    set during upload from the source JSON and are not patchable here.
    """
    if "status" in body:
        status = body["status"]
        if not isinstance(status, str) or status not in _VALID_ISSUE_STATUSES:
            raise ValidationError(
                f"status must be one of {sorted(_VALID_ISSUE_STATUSES)}",
                errors=(_FieldError(field="status", code="invalid_value", message="not a recognized status"),),
            )
        issue.status = status
    if "assigned_to" in body:
        assigned = body["assigned_to"]
        if assigned is not None and not isinstance(assigned, str):
            raise ValidationError(
                "assigned_to must be a string or null",
                errors=(_FieldError(field="assigned_to", code="invalid_type", message="must be string"),),
            )
        issue.assigned_to = assigned if isinstance(assigned, str) else None
    if "resolution_notes" in body:
        notes = body["resolution_notes"]
        if notes is not None and not isinstance(notes, str):
            raise ValidationError(
                "resolution_notes must be a string or null",
                errors=(_FieldError(field="resolution_notes", code="invalid_type", message="must be string"),),
            )
        issue.resolution_notes = notes if isinstance(notes, str) else None
    if "tags" in body:
        if not isinstance(body["tags"], list):
            raise ValidationError(
                "tags must be an array",
                errors=(_FieldError(field="tags", code="invalid_type", message="must be array"),),
            )
        issue.tags = _coerce_str_list(body["tags"])
    issue.updated_at = datetime.now()
    return issue


def _resolve_recording_project_id(recording: Recording) -> str | None:
    """Recordings are scoped to projects via ``project_id``.

    Some legacy recordings predate that linkage and may have
    ``project_id is None``. Treat those as not-found via REST rather
    than expose an unscopable resource.
    """
    return recording.project_id


def _parse_multiline_form_field(value: str | None) -> list[str]:
    """Split a textarea-style form field (one item per line) to a list.

    Empty input yields an empty list; whitespace-only lines are
    dropped. Used by the recordings upload route for the page_urls /
    component_names / app_screens / device_sections fields the legacy
    multipart form accepts.
    """
    if not value:
        return []
    return [line.strip() for line in value.split("\n") if line.strip()]


@api_bp.route("/recordings", methods=["POST"])
@api_endpoint
def create_recording_rest() -> tuple[Response, int] | Response:
    """Upload a Dictaphone JSON recording.

    Multipart/form-data shape:

    - ``project_id`` (form, required)
    - ``recording_json_en`` (file, required) — Dictaphone JSON
    - ``recording_json_fr`` (file, optional) — second-language file;
      its ``recording`` id must match the English file's
    - Optional form fields: ``title``, ``description``,
      ``auditor_name``, ``auditor_role``, ``recording_type``,
      ``task_description``, ``test_user_account``,
      ``lived_experience_tester_id``, ``test_supervisor_id``,
      ``media_file_path``, ``page_urls``, ``component_names``,
      ``app_screens``, ``device_sections``, ``discovered_page_ids``
      (the last 5 are newline-separated lists)
    - Testing scope checkboxes (``scope_forms`` through
      ``scope_drag_drop``) are accepted with ``"on"`` for true (the
      browser idiom); any other value is false.

    Returns 201 with the new Recording on success.

    Out of scope for this commit (deferred to follow-up PATCH):
      supplementary content files for key-takeaways, painpoints, and
      assertions — those land via separate endpoints once the storage
      shape is settled.

    Errors:

    - **400** — required field/file missing, non-JSON file, language
      mismatch between en/fr files, invalid ``recording_type``
    - **404** — project does not exist
    - **409** — recording id from the file already exists (Dictaphone's
      ``recording`` field is the dedup key)
    """
    import json
    import tempfile
    from pathlib import Path

    from auto_a11y.importers import DictaphoneImporter

    if not request.content_type or not request.content_type.startswith(
        "multipart/form-data"
    ):
        raise ValidationError(
            "request must be multipart/form-data",
            errors=(
                _FieldError(
                    field="<content-type>", code="invalid_type",
                    message="must be multipart/form-data",
                ),
            ),
        )

    project_id_raw = request.form.get("project_id")
    if not project_id_raw:
        raise ValidationError(
            "project_id is required",
            errors=(
                _FieldError(
                    field="project_id", code="required", message="required",
                ),
            ),
        )
    project = get_db().get_project(project_id_raw)
    if project is None:
        raise NotFoundError(f"project {project_id_raw} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id_raw,
    )

    file_en = request.files.get("recording_json_en")
    if file_en is None or not file_en.filename:
        raise ValidationError(
            "recording_json_en part is required",
            errors=(
                _FieldError(
                    field="recording_json_en", code="required",
                    message="required",
                ),
            ),
        )
    if not file_en.filename.endswith(".json"):
        raise ValidationError(
            "recording_json_en must be a .json file",
            errors=(
                _FieldError(
                    field="recording_json_en", code="invalid_value",
                    message="must end with .json",
                ),
            ),
        )

    file_fr = request.files.get("recording_json_fr")
    has_french = file_fr is not None and file_fr.filename and file_fr.filename.endswith(".json")

    recording_type_raw = request.form.get("recording_type", "audit")
    try:
        RecordingType(recording_type_raw)
    except ValueError as exc:
        raise ValidationError(
            f"recording_type {recording_type_raw!r} is not recognized",
            errors=(
                _FieldError(
                    field="recording_type", code="invalid_value",
                    message=str(exc),
                ),
            ),
        ) from exc

    # Read both files now so a parse error reports as 400, not 500.
    content_en = file_en.read().decode("utf-8")
    try:
        data_en = json.loads(content_en)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"recording_json_en is not valid JSON: {exc}",
            errors=(
                _FieldError(
                    field="recording_json_en", code="invalid_format",
                    message=str(exc),
                ),
            ),
        ) from exc

    recording_id_value: str = str(data_en.get("recording", "")).strip()
    if not recording_id_value:
        raise ValidationError(
            "recording_json_en is missing the 'recording' id field",
            errors=(
                _FieldError(
                    field="recording_json_en.recording", code="required",
                    message="required",
                ),
            ),
        )

    content_fr: str | None = None
    if has_french and file_fr is not None:
        # Use a local non-Optional ``content_fr_text`` for the
        # ``json.loads`` call so pyright keeps the narrowing.
        content_fr_text = file_fr.read().decode("utf-8")
        content_fr = content_fr_text
        try:
            data_fr = json.loads(content_fr_text)
        except json.JSONDecodeError as exc:
            raise ValidationError(
                f"recording_json_fr is not valid JSON: {exc}",
                errors=(
                    _FieldError(
                        field="recording_json_fr", code="invalid_format",
                        message=str(exc),
                    ),
                ),
            ) from exc
        recording_id_fr = str(data_fr.get("recording", "")).strip()
        if recording_id_fr != recording_id_value:
            raise ValidationError(
                f"recording id mismatch: en={recording_id_value!r}, fr={recording_id_fr!r}",
                errors=(
                    _FieldError(
                        field="recording_json_fr.recording",
                        code="invalid_value",
                        message="must match recording_json_en.recording",
                    ),
                ),
            )

    existing = get_db().get_recording_by_recording_id(recording_id_value)
    if existing is not None:
        raise ConflictError(
            f"recording {recording_id_value!r} already exists"
        )

    auditor_info: dict[str, Any] = {
        "title": request.form.get("title", ""),
        "description": request.form.get("description", ""),
        "auditor_name": request.form.get("auditor_name", ""),
        "auditor_role": request.form.get("auditor_role", ""),
        "test_user_account": (
            request.form.get("test_user_account", "").strip() or None
        ),
        "lived_experience_tester_id": (
            request.form.get("lived_experience_tester_id", "").strip() or None
        ),
        "test_supervisor_id": (
            request.form.get("test_supervisor_id", "").strip() or None
        ),
        "media_file_path": request.form.get("media_file_path", ""),
    }
    testing_scope: dict[str, bool] = {
        "forms": request.form.get("scope_forms") == "on",
        "video": request.form.get("scope_video") == "on",
        "live_multimedia": request.form.get("scope_live_multimedia") == "on",
        "multilingual": request.form.get("scope_multilingual") == "on",
        "orientation": request.form.get("scope_orientation") == "on",
        "zoom": request.form.get("scope_zoom") == "on",
        "timeouts": request.form.get("scope_timeouts") == "on",
        "motion_actuation": request.form.get("scope_motion_actuation") == "on",
        "drag_drop": request.form.get("scope_drag_drop") == "on",
    }

    page_urls = _parse_multiline_form_field(request.form.get("page_urls"))
    component_names = _parse_multiline_form_field(
        request.form.get("component_names")
    )
    app_screens = _parse_multiline_form_field(
        request.form.get("app_screens")
    )
    device_sections = _parse_multiline_form_field(
        request.form.get("device_sections")
    )
    discovered_page_ids = request.form.getlist("discovered_page_ids")
    task_description = (
        request.form.get("task_description", "").strip() or None
    )

    importer = DictaphoneImporter()
    tmp_paths: list[Path] = []
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix="_en.json", delete=False, encoding="utf-8",
        ) as tmp_en:
            tmp_en.write(content_en)
            tmp_paths.append(Path(tmp_en.name))

        recording, issues_en = importer.import_from_file(
            str(tmp_paths[0]),
            project_id=project_id_raw,
            page_urls=page_urls,
            discovered_page_ids=discovered_page_ids,
            component_names=component_names,
            app_screens=app_screens,
            device_sections=device_sections,
            task_description=task_description,
            auditor_info=auditor_info,
            recording_type=recording_type_raw,
            testing_scope=testing_scope,
            language="en",
        )

        all_issues: list[RecordingIssue] = list(issues_en)
        if content_fr is not None:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix="_fr.json", delete=False, encoding="utf-8",
            ) as tmp_fr:
                tmp_fr.write(content_fr)
                tmp_paths.append(Path(tmp_fr.name))
            _, issues_fr = importer.import_from_file(
                str(tmp_paths[1]),
                project_id=project_id_raw,
                page_urls=page_urls,
                discovered_page_ids=discovered_page_ids,
                component_names=component_names,
                app_screens=app_screens,
                device_sections=device_sections,
                task_description=task_description,
                auditor_info=auditor_info,
                recording_type=recording_type_raw,
                testing_scope=testing_scope,
                language="fr",
            )
            all_issues.extend(issues_fr)

        created_id = get_db().create_recording(recording)
        get_db().create_recording_issues_bulk(all_issues)

        # Mirror the legacy form: append the new recording id to
        # ``project.recording_ids`` so the project view sees it.
        if created_id not in project.recording_ids:
            project.recording_ids.append(created_id)
            get_db().update_project(project)

        refreshed = get_db().get_recording(created_id)
        if refreshed is None:
            raise ConflictError("recording failed to persist")
    finally:
        for path in tmp_paths:
            path.unlink(missing_ok=True)

    response = jsonify(_serialize_recording(refreshed))
    response.headers["Location"] = f"/api/v1/recordings/{created_id}"
    return response, 201


@api_bp.route("/recordings", methods=["GET"])
@api_endpoint
def list_recordings_rest() -> tuple[Response, int] | Response:
    """List recordings.

    Filter via ``?project_id=<id>`` (required for non-superadmins so the
    project-role check has a target). ``recording_type=<type>`` further
    narrows by type.
    """
    project_id = request.args.get("project_id")
    if project_id is None:
        raise ValidationError(
            "project_id query parameter is required",
            errors=(_FieldError(field="project_id", code="required", message="required"),),
        )
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=project_id
    )
    if get_db().get_project(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")

    recording_type_raw = request.args.get("recording_type")
    if recording_type_raw is not None:
        try:
            RecordingType(recording_type_raw)
        except ValueError as exc:
            raise ValidationError(
                "recording_type is not a recognized value",
                errors=(_FieldError(field="recording_type", code="invalid_value", message=str(exc)),),
            ) from exc

    limit = parse_limit(request.args.get("limit"))
    cursor_raw = request.args.get("cursor")
    cursor = _Cursor.decode(cursor_raw) if cursor_raw else None

    query: dict[str, Any] = {"project_id": project_id}
    if recording_type_raw is not None:
        query["recording_type"] = recording_type_raw
    if cursor is not None:
        from bson import ObjectId
        try:
            query["_id"] = {"$lt": ObjectId(cursor.last_id)}
        except Exception as exc:
            raise ValidationError(
                "cursor.last_id is not a valid ObjectId",
                errors=(_FieldError(field="cursor.last_id", code="invalid_format", message=str(exc)),),
            ) from exc

    docs = list(get_db().recordings.find(query).sort("_id", -1).limit(limit + 1))
    recordings = [Recording.from_dict(doc) for doc in docs]
    page = paginate(
        recordings, limit=limit, get_id=lambda r: str(r.mongo_id) if r.mongo_id else ""
    )
    return jsonify(
        {
            "items": [_serialize_recording(r) for r in page["items"]],
            "next_cursor": page["next_cursor"],
        }
    )


@api_bp.route("/recordings/<recording_id>", methods=["GET"])
@api_endpoint
def get_recording_rest(recording_id: str) -> tuple[Response, int] | Response:
    """Get a recording by id."""
    recording = get_db().get_recording(recording_id)
    if recording is None:
        raise NotFoundError(f"recording {recording_id} not found")
    project_id = _resolve_recording_project_id(recording)
    if project_id is None:
        raise NotFoundError(f"recording {recording_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=project_id
    )
    return jsonify(_serialize_recording(recording))


@api_bp.route("/recordings/<recording_id>", methods=["PATCH"])
@api_endpoint
def patch_recording_rest(recording_id: str) -> tuple[Response, int] | Response:
    """Partial update of a recording's metadata."""
    recording = get_db().get_recording(recording_id)
    if recording is None:
        raise NotFoundError(f"recording {recording_id} not found")
    project_id = _resolve_recording_project_id(recording)
    if project_id is None:
        raise NotFoundError(f"recording {recording_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    body = _require_dict_body()
    patched = _apply_patch_to_recording(recording, body)
    if not get_db().update_recording(patched):
        raise ConflictError("recording could not be updated")
    return jsonify(_serialize_recording(patched))


@api_bp.route("/recordings/<recording_id>", methods=["DELETE"])
@api_endpoint
def delete_recording_rest(recording_id: str) -> tuple[Response, int]:
    """Delete a recording. Cascades to its RecordingIssues."""
    recording = get_db().get_recording(recording_id)
    if recording is None:
        raise NotFoundError(f"recording {recording_id} not found")
    project_id = _resolve_recording_project_id(recording)
    if project_id is None:
        raise NotFoundError(f"recording {recording_id} not found")
    require_project_role(
        UserRole.ADMIN, project_id=project_id
    )
    get_db().delete_recording(recording_id)
    return Response(status=204), 204


@api_bp.route("/recordings/<recording_id>/issues", methods=["GET"])
@api_endpoint
def list_recording_issues_rest(
    recording_id: str,
) -> tuple[Response, int] | Response:
    """List the issues for a recording with cursor pagination."""
    recording = get_db().get_recording(recording_id)
    if recording is None:
        raise NotFoundError(f"recording {recording_id} not found")
    project_id = _resolve_recording_project_id(recording)
    if project_id is None:
        raise NotFoundError(f"recording {recording_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=project_id
    )

    limit = parse_limit(request.args.get("limit"))
    cursor_raw = request.args.get("cursor")
    cursor = _Cursor.decode(cursor_raw) if cursor_raw else None

    # RecordingIssue.recording_id is the human-readable string
    # (e.g. "NED-A") on the parent Recording, not its ObjectId. Look it
    # up from the resolved recording rather than the URL parameter so a
    # caller cannot inject an arbitrary string here.
    query: dict[str, Any] = {"recording_id": recording.recording_id}
    if cursor is not None:
        from bson import ObjectId
        try:
            query["_id"] = {"$lt": ObjectId(cursor.last_id)}
        except Exception as exc:
            raise ValidationError(
                "cursor.last_id is not a valid ObjectId",
                errors=(_FieldError(field="cursor.last_id", code="invalid_format", message=str(exc)),),
            ) from exc

    docs = list(get_db().recording_issues.find(query).sort("_id", -1).limit(limit + 1))
    issues = [RecordingIssue.from_dict(doc) for doc in docs]
    page = paginate(
        issues, limit=limit, get_id=lambda i: str(i.mongo_id) if i.mongo_id else ""
    )
    return jsonify(
        {
            "items": [_serialize_recording_issue(i) for i in page["items"]],
            "next_cursor": page["next_cursor"],
        }
    )


@api_bp.route("/recording-issues/<issue_id>", methods=["GET"])
@api_endpoint
def get_recording_issue_rest(issue_id: str) -> tuple[Response, int] | Response:
    """Get a recording issue by id."""
    issue = get_db().get_recording_issue(issue_id)
    if issue is None:
        raise NotFoundError(f"recording issue {issue_id} not found")
    if issue.project_id is None:
        raise NotFoundError(f"recording issue {issue_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=issue.project_id
    )
    return jsonify(_serialize_recording_issue(issue))


@api_bp.route("/recording-issues/<issue_id>", methods=["PATCH"])
@api_endpoint
def patch_recording_issue_rest(issue_id: str) -> tuple[Response, int] | Response:
    """Partial update of a recording issue (status, assignment, notes, tags)."""
    issue = get_db().get_recording_issue(issue_id)
    if issue is None:
        raise NotFoundError(f"recording issue {issue_id} not found")
    if issue.project_id is None:
        raise NotFoundError(f"recording issue {issue_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=issue.project_id
    )
    body = _require_dict_body()
    patched = _apply_patch_to_recording_issue(issue, body)
    if not get_db().update_recording_issue(patched):
        raise ConflictError("recording issue could not be updated")
    return jsonify(_serialize_recording_issue(patched))


# ---------------------------------------------------------------------------
# Admin settings (REST shape — uses the @api_endpoint scaffolding).
#
# Settings live in the ``system_settings`` singleton Mongo doc, with each
# named "section" stored under a top-level key and falling back to the
# matching environment variables when the DB section is unset. The
# legacy admin_settings_bp HTML routes still serve the admin UI; these
# REST endpoints offer a JSON shape for programmatic access.
#
# Per docs/REST_API_ROADMAP.md §5.14. Auth is superadmin-only — settings
# changes affect every project on the deployment, so the project-role
# helper isn't sufficient.
# ---------------------------------------------------------------------------

import os  # noqa: E402

from auto_a11y.core.runtime_config import (  # noqa: E402
    CONFIG_SECTIONS,
    ConfigField,
    ConfigSection,
    FieldType,
    FieldValue,
    coerce_value,
    env_value,
    get_section as get_config_section,
    is_bool_true,
    is_password_set,
    parse_bool,
    section_source,
)
from auto_a11y.core.system_settings import SystemSettings  # noqa: E402
from auto_a11y.drupal.config import DRUPAL_SETTINGS_KEY  # noqa: E402
from auto_a11y.web.api import require_superadmin  # noqa: E402


def _serialize_field_value(field: ConfigField, section_data: dict[str, Any] | None) -> Any:
    """Project a field's *current effective* value to JSON.

    Password fields are never echoed — callers see ``"<set>"`` or ``null``
    instead so they can tell whether one is configured without leaking the
    secret. Booleans are real JSON booleans (the form layer uses string
    ``"True"``/``"False"``; we don't carry that into the API).
    """
    if field.field_type is FieldType.PASSWORD:
        return None  # see _serialize_section: password_set sibling reports the bit
    if section_data is not None and field.db_key in section_data:
        return section_data[field.db_key]
    raw_env = env_value(field)
    if field.field_type is FieldType.BOOL:
        return parse_bool(raw_env)
    if field.field_type is FieldType.INT:
        try:
            return int(raw_env) if raw_env != "" else None
        except ValueError:
            return None
    if field.field_type is FieldType.FLOAT:
        try:
            return float(raw_env) if raw_env != "" else None
        except ValueError:
            return None
    return raw_env


def _serialize_settings_section(
    section: ConfigSection, section_data: dict[str, Any] | None
) -> dict[str, Any]:
    """Project a settings section to JSON: values + per-field metadata.

    Each field shows up under its ``db_key`` with the effective value;
    password fields are reported as ``null`` with a sibling
    ``<key>_set`` boolean so callers can render a "[set]" indicator
    without us echoing the secret.
    """
    values: dict[str, Any] = {}
    for field in section.fields:
        if field.field_type is FieldType.PASSWORD:
            values[field.db_key] = None
            values[f"{field.db_key}_set"] = is_password_set(field, section_data)
        elif field.field_type is FieldType.BOOL:
            values[field.db_key] = is_bool_true(field, section_data)
        else:
            values[field.db_key] = _serialize_field_value(field, section_data)
    return {
        "section_id": section.section_id,
        "source": section_source(section, section_data),
        "values": values,
    }


def _serialize_drupal_settings(section_data: dict[str, Any] | None) -> dict[str, Any]:
    """Drupal config has its own shape (not in CONFIG_SECTIONS); project here."""
    if section_data is not None:
        return {
            "source": "database",
            "values": {
                "base_url": section_data.get("base_url", ""),
                "username": section_data.get("username", ""),
                "password_set": bool(section_data.get("password")),
                "enabled": bool(section_data.get("enabled", True)),
            },
        }
    env_present = any(
        os.getenv(name)
        for name in ("DRUPAL_BASE_URL", "DRUPAL_USERNAME", "DRUPAL_PASSWORD")
    )
    return {
        "source": "environment" if env_present else "unset",
        "values": {
            "base_url": os.getenv("DRUPAL_BASE_URL", ""),
            "username": os.getenv("DRUPAL_USERNAME", ""),
            "password_set": bool(os.getenv("DRUPAL_PASSWORD")),
            "enabled": os.getenv("DRUPAL_EXPORT_ENABLED", "true").lower() == "true",
        },
    }


def _coerce_field_value(field: ConfigField, raw: Any) -> FieldValue:
    """Coerce a JSON-decoded value to the field's typed value, raising on bad input.

    JSON ``true``/``false`` map directly to bools; integers and floats are
    accepted as-is or as numeric strings; password fields require strings.
    """
    if field.field_type is FieldType.BOOL:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return parse_bool(raw)
        raise ValidationError(
            f"{field.db_key} must be a boolean",
            errors=(_FieldError(field=field.db_key, code="invalid_type", message="must be boolean"),),
        )
    if field.field_type is FieldType.INT:
        if isinstance(raw, bool):
            raise ValidationError(
                f"{field.db_key} must be an integer",
                errors=(_FieldError(field=field.db_key, code="invalid_type", message="must be integer"),),
            )
        if isinstance(raw, int):
            return raw
        if isinstance(raw, str):
            try:
                return int(raw)
            except ValueError as exc:
                raise ValidationError(
                    f"{field.db_key} must be an integer",
                    errors=(_FieldError(field=field.db_key, code="invalid_format", message=str(exc)),),
                ) from exc
        raise ValidationError(
            f"{field.db_key} must be an integer",
            errors=(_FieldError(field=field.db_key, code="invalid_type", message="must be integer"),),
        )
    if field.field_type is FieldType.FLOAT:
        if isinstance(raw, bool):
            raise ValidationError(
                f"{field.db_key} must be a number",
                errors=(_FieldError(field=field.db_key, code="invalid_type", message="must be number"),),
            )
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str):
            try:
                return float(raw)
            except ValueError as exc:
                raise ValidationError(
                    f"{field.db_key} must be a number",
                    errors=(_FieldError(field=field.db_key, code="invalid_format", message=str(exc)),),
                ) from exc
        raise ValidationError(
            f"{field.db_key} must be a number",
            errors=(_FieldError(field=field.db_key, code="invalid_type", message="must be number"),),
        )
    if not isinstance(raw, str):
        raise ValidationError(
            f"{field.db_key} must be a string",
            errors=(_FieldError(field=field.db_key, code="invalid_type", message="must be string"),),
        )
    return coerce_value(field, raw)


@api_bp.route("/admin/settings", methods=["GET"])
@api_endpoint
def get_admin_settings() -> Response:
    """Return the full settings document — Drupal config + every named section."""
    require_superadmin()
    settings = SystemSettings(get_db())
    sections = [
        _serialize_settings_section(s, settings.get_section(s.section_id))
        for s in CONFIG_SECTIONS
    ]
    return jsonify(
        {
            "drupal": _serialize_drupal_settings(settings.get_section(DRUPAL_SETTINGS_KEY)),
            "sections": sections,
        }
    )


@api_bp.route("/admin/settings/drupal", methods=["PATCH"])
@api_endpoint
def patch_drupal_settings() -> Response:
    """Update Drupal connection settings.

    Validation mirrors the legacy form: ``base_url`` must be http(s),
    ``username`` is required, and a blank ``password`` keeps the existing
    stored value (so admins don't have to re-enter the secret on every
    edit).
    """
    require_superadmin()
    body = _require_dict_body()
    settings = SystemSettings(get_db())
    existing = settings.get_section(DRUPAL_SETTINGS_KEY) or {}

    base_url = body.get("base_url", existing.get("base_url", ""))
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValidationError(
            "base_url is required",
            errors=(_FieldError(field="base_url", code="required", message="required"),),
        )
    base_url = base_url.strip()
    if not (base_url.startswith("http://") or base_url.startswith("https://")):
        raise ValidationError(
            "base_url must be an http(s) URL",
            errors=(_FieldError(field="base_url", code="invalid_format", message="must start with http:// or https://"),),
        )

    username = body.get("username", existing.get("username", ""))
    if not isinstance(username, str) or not username.strip():
        raise ValidationError(
            "username is required",
            errors=(_FieldError(field="username", code="required", message="required"),),
        )
    username = username.strip()

    password_raw = body.get("password")
    existing_password = existing.get("password") if isinstance(existing.get("password"), str) else None
    if password_raw is None:
        password = existing_password
        if password is None:
            raise ValidationError(
                "password is required (no existing password to preserve)",
                errors=(_FieldError(field="password", code="required", message="required"),),
            )
    else:
        if not isinstance(password_raw, str):
            raise ValidationError(
                "password must be a string",
                errors=(_FieldError(field="password", code="invalid_type", message="must be string"),),
            )
        password = password_raw

    enabled_raw: Any = body.get("enabled", existing.get("enabled", True))
    if not isinstance(enabled_raw, bool):
        raise ValidationError(
            "enabled must be a boolean",
            errors=(_FieldError(field="enabled", code="invalid_type", message="must be boolean"),),
        )

    user_id = str(current_user.get_id()) if current_user.is_authenticated else None
    settings.set_section(
        DRUPAL_SETTINGS_KEY,
        {
            "base_url": base_url,
            "username": username,
            "password": password,
            "enabled": enabled_raw,
        },
        updated_by=user_id,
    )
    return jsonify(_serialize_drupal_settings(settings.get_section(DRUPAL_SETTINGS_KEY)))


@api_bp.route("/admin/settings/drupal", methods=["DELETE"])
@api_endpoint
def delete_drupal_settings() -> tuple[Response, int]:
    """Clear the Drupal section so the env-var fallback applies again."""
    require_superadmin()
    SystemSettings(get_db()).clear_section(DRUPAL_SETTINGS_KEY)
    return Response(status=204), 204


@api_bp.route("/admin/settings/<section_id>", methods=["PATCH"])
@api_endpoint
def patch_settings_section(section_id: str) -> Response:
    """Partial update of a named CONFIG_SECTIONS section.

    Each key in the request body must match a ``db_key`` defined in the
    section schema; the value is type-coerced per :class:`ConfigField`.
    Password fields with a blank string preserve the existing value
    (mirrors the legacy "leave blank to keep" form behaviour).

    The Drupal section is *not* reachable through this endpoint — its
    schema is special-cased and lives at /api/v1/admin/settings/drupal.
    """
    require_superadmin()
    if section_id == DRUPAL_SETTINGS_KEY:
        raise NotFoundError(
            f"unknown settings section {section_id!r} — use /admin/settings/drupal"
        )
    section = get_config_section(section_id)
    if section is None:
        raise NotFoundError(f"unknown settings section {section_id!r}")

    body = _require_dict_body()
    settings = SystemSettings(get_db())
    existing = settings.get_section(section_id) or {}
    db_keys = {f.db_key: f for f in section.fields}

    unknown = [k for k in body.keys() if k not in db_keys]
    if unknown:
        raise ValidationError(
            f"unknown field(s) for section {section_id}: {', '.join(unknown)}",
            errors=tuple(
                _FieldError(field=k, code="unknown_field", message="not in section schema")
                for k in unknown
            ),
        )

    new_values: dict[str, Any] = dict(existing)
    for db_key, raw in body.items():
        field = db_keys[db_key]
        if field.field_type is FieldType.PASSWORD:
            if raw == "" or raw is None:
                # Preserve existing value; only overwrite on a real string.
                if not isinstance(existing.get(db_key), str):
                    # Nothing stored to preserve and no new value provided —
                    # treat as clearing the field.
                    new_values[db_key] = ""
                continue
            if not isinstance(raw, str):
                raise ValidationError(
                    f"{db_key} must be a string",
                    errors=(_FieldError(field=db_key, code="invalid_type", message="must be string"),),
                )
            new_values[db_key] = raw
        else:
            new_values[db_key] = _coerce_field_value(field, raw)

    user_id = str(current_user.get_id()) if current_user.is_authenticated else None
    settings.set_section(section_id, new_values, updated_by=user_id)
    return jsonify(_serialize_settings_section(section, settings.get_section(section_id)))


@api_bp.route("/admin/settings/<section_id>", methods=["DELETE"])
@api_endpoint
def delete_settings_section(section_id: str) -> tuple[Response, int]:
    """Clear a named section so the env-var fallback applies again."""
    require_superadmin()
    if section_id == DRUPAL_SETTINGS_KEY:
        raise NotFoundError(
            f"unknown settings section {section_id!r} — use /admin/settings/drupal"
        )
    if get_config_section(section_id) is None:
        raise NotFoundError(f"unknown settings section {section_id!r}")
    SystemSettings(get_db()).clear_section(section_id)
    return Response(status=204), 204


# ---------------------------------------------------------------------------
# Jobs (REST shape — uses the @api_endpoint scaffolding).
#
# The legacy /jobs/* endpoints (stats, active, clear-all, clear-stale,
# cleanup-page-counts) are unauthenticated administrative tools — they
# stay where they are, but the per-job operations (read, cancel) are
# the kind of thing a future SPA would poll, so they get a proper REST
# treatment with auth + RFC 7807 errors.
#
# In scope: ``GET /api/v1/jobs/<job_id>`` and
# ``POST /api/v1/jobs/<job_id>/cancel``. Restart is more involved (the
# legacy reports.py /job/<id>/restart hard-codes the report-job
# generator rebuild) and is deferred to a follow-up PR alongside the
# test-runs / reports REST work, where the per-job-type restart
# generators have a natural home.
#
# Per docs/REST_API_ROADMAP.md §5.15.
# ---------------------------------------------------------------------------

from auto_a11y.web.api import require_authenticated  # noqa: E402


def _serialize_job(doc: dict[str, Any]) -> dict[str, Any]:
    """Project a raw job Mongo doc to a JSON-safe dict.

    Datetimes go to ISO 8601, the Mongo ``_id`` is dropped (the public
    identifier is ``job_id``), and the nested ``progress``/``metadata``
    dicts are passed through as-is so callers can render whatever the
    individual job_type recorded there.
    """

    def _iso(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    return {
        "job_id": doc.get("job_id"),
        "job_type": doc.get("job_type"),
        "status": doc.get("status"),
        "website_id": doc.get("website_id"),
        "project_id": doc.get("project_id"),
        "user_id": doc.get("user_id"),
        "session_id": doc.get("session_id"),
        "created_at": _iso(doc.get("created_at")),
        "updated_at": _iso(doc.get("updated_at")),
        "started_at": _iso(doc.get("started_at")),
        "completed_at": _iso(doc.get("completed_at")),
        "progress": doc.get("progress") or {},
        "metadata": doc.get("metadata") or {},
        "error": doc.get("error"),
        "result": doc.get("result"),
        "cancellation_requested": doc.get("cancellation_requested", False),
        "cancellation_requested_at": _iso(doc.get("cancellation_requested_at")),
        "cancellation_requested_by": doc.get("cancellation_requested_by"),
    }


def _resolve_job_or_404(job_id: str) -> dict[str, Any]:
    job_manager = JobManager(get_db())
    doc = job_manager.get_job(job_id)
    if doc is None:
        raise NotFoundError(f"job {job_id} not found")
    return doc


@api_bp.route("/jobs/<job_id>", methods=["GET"])
@api_endpoint
def get_job_rest(job_id: str) -> tuple[Response, int] | Response:
    """Read a single job's status, progress, and metadata."""
    require_authenticated()
    doc = _resolve_job_or_404(job_id)
    return jsonify(_serialize_job(doc))


@api_bp.route("/jobs/<job_id>/cancel", methods=["POST"])
@api_endpoint
def cancel_job_rest(job_id: str) -> tuple[Response, int] | Response:
    """Request cancellation of a pending or running job.

    Returns 202 because cancellation is asynchronous — the worker
    thread polls the ``cancellation_requested`` flag and transitions to
    CANCELLED on its next checkpoint. The response shape mirrors GET so
    callers can immediately observe the new ``CANCELLING`` status.

    Idempotent: a second POST against an already-cancelling job returns
    409, since the request_cancellation underlying call rejects the
    transition once the status has already moved past
    ``pending``/``running``.
    """
    require_authenticated()
    doc = _resolve_job_or_404(job_id)

    job_manager = JobManager(get_db())
    requested_by = (
        str(current_user.get_id()) if current_user.is_authenticated else None
    )
    requested = job_manager.request_cancellation(job_id, requested_by=requested_by)
    if not requested:
        # Either the job is no longer cancellable (already completed,
        # cancelled, or failed) or the update lost a race. Surface the
        # current status so callers can decide what to do.
        current_status = doc.get("status")
        raise ConflictError(
            f"job {job_id} cannot be cancelled (current status: {current_status})"
        )

    refreshed = job_manager.get_job(job_id)
    if refreshed is None:
        raise ConflictError(f"job {job_id} disappeared after cancel")
    return jsonify(_serialize_job(refreshed)), 202


# ---------------------------------------------------------------------------
# PDF documents (REST shape — uses the @api_endpoint scaffolding).
#
# Sits alongside the existing pdf_bp HTML routes at /projects/<id>/pdfs,
# /websites/<id>/pdfs, /pdfs/<id>, /pdfs/<id>/delete, etc. (still serving
# the admin frontend) and the various /pdfs/<id>/file, /audit, /export,
# /images, /issue-map, /pdfmax-report viewer routes.
#
# In scope: list (project- and website-scoped), single read, delete.
# Out of scope (deferred to follow-up PRs):
#   - POST /api/v1/projects/<id>/pdfs (multipart upload + DB dedup +
#     async fetch) — same complexity bucket as the recordings upload
#   - POST /pdf-documents/<id>/audits / /audits/latest / /audits/latest/cancel
#     (action endpoints; share idempotency-key + JobManager mechanics
#     with the deferred test-runs work)
#   - GET /file, /images/<n>, /export?format=, /issue-map, /reports/pdfmax
#     (binary streaming + cached-artefact serving; needs a separate review
#     pass for cache headers, range requests, and content-disposition)
#
# Per docs/REST_API_ROADMAP.md §5.9.
# ---------------------------------------------------------------------------

from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus  # noqa: E402
from auto_a11y.pdf.storage import PdfStorage  # noqa: E402


def _pdf_storage() -> PdfStorage:
    return PdfStorage(base_dir=Path(get_app_config().PDF_STORAGE_DIR))


def _serialize_pdf_document(pdf: PdfDocument) -> dict[str, Any]:
    """Project a :class:`PdfDocument` to a JSON-safe dict.

    Mirrors the shape of the underlying model except that:
    - datetimes become ISO 8601 strings;
    - the Mongo ``_id`` is dropped (the public id is the string ``id`` property);
    - the ``storage_relpath`` and ``images_relpath`` filesystem paths are kept
      because they're useful identifiers for clients that consume the
      file-streaming endpoints (deferred), but they describe layout under a
      server-side base dir, not absolute paths.
    """

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    return {
        "id": pdf.id,
        "website_id": pdf.website_id,
        "project_id": pdf.project_id,
        "source_url": pdf.source_url,
        "source_type": pdf.source_type,
        "discovered_from_page_id": pdf.discovered_from_page_id,
        "discovered_from_user_id": pdf.discovered_from_user_id,
        "sha256": pdf.sha256,
        "file_size_bytes": pdf.file_size_bytes,
        "storage_relpath": pdf.storage_relpath,
        "images_relpath": pdf.images_relpath,
        "original_filename": pdf.original_filename,
        "pdf_version": pdf.pdf_version,
        "page_count": pdf.page_count,
        "declared_lang": pdf.declared_lang,
        "detected_lang": pdf.detected_lang,
        "lang_confidence": pdf.lang_confidence,
        "status": pdf.status.value,
        "error_reason": pdf.error_reason,
        "last_audit_result_id": pdf.last_audit_result_id,
        "discovered_at": _iso(pdf.discovered_at),
        "last_audited_at": _iso(pdf.last_audited_at),
    }


def _list_pdfs_with_query(query: dict[str, Any]) -> Response:
    """Cursor-paginate ``query`` against the pdf_documents collection."""
    limit = parse_limit(request.args.get("limit"))
    cursor_raw = request.args.get("cursor")
    cursor = _Cursor.decode(cursor_raw) if cursor_raw else None

    status_raw = request.args.get("status")
    if status_raw is not None:
        try:
            query["status"] = PdfDocumentStatus(status_raw).value
        except ValueError as exc:
            raise ValidationError(
                "status is not a recognized PdfDocumentStatus value",
                errors=(_FieldError(field="status", code="invalid_value", message=str(exc)),),
            ) from exc

    if cursor is not None:
        from bson import ObjectId
        try:
            query["_id"] = {"$lt": ObjectId(cursor.last_id)}
        except Exception as exc:
            raise ValidationError(
                "cursor.last_id is not a valid ObjectId",
                errors=(_FieldError(field="cursor.last_id", code="invalid_format", message=str(exc)),),
            ) from exc

    docs = list(get_db().pdf_documents.find(query).sort("_id", -1).limit(limit + 1))
    pdfs = [PdfDocument.from_dict(doc) for doc in docs]
    page = paginate(
        pdfs, limit=limit, get_id=lambda p: str(p.mongo_id) if p.mongo_id else ""
    )
    return jsonify(
        {
            "items": [_serialize_pdf_document(p) for p in page["items"]],
            "next_cursor": page["next_cursor"],
        }
    )


@api_bp.route("/projects/<project_id>/pdfs", methods=["GET"])
@api_endpoint
def list_pdfs_for_project(project_id: str) -> tuple[Response, int] | Response:
    """List PDF documents across every website in a project."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=project_id
    )
    if get_db().get_project(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")
    return _list_pdfs_with_query({"project_id": project_id})


@api_bp.route("/websites/<website_id>/pdfs", methods=["GET"])
@api_endpoint
def list_pdfs_for_website(website_id: str) -> tuple[Response, int] | Response:
    """List PDF documents attached to one website."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, website_id=website_id
    )
    if get_db().get_website(website_id) is None:
        raise NotFoundError(f"website {website_id} not found")
    return _list_pdfs_with_query({"website_id": website_id})


@api_bp.route("/projects/<project_id>/pdfs", methods=["POST"])
@api_endpoint
def create_pdf_for_project(
    project_id: str,
) -> tuple[Response, int] | Response:
    """Add a PDF to a project — multipart upload OR remote URL fetch.

    Two request shapes share this handler:

    1. **multipart/form-data**: ``pdf_file`` part with the raw PDF
       bytes plus a ``website_id`` form field selecting which website
       in the project to attach the document to. Optional
       ``original_filename`` form field overrides the part's
       ``filename`` attribute.

    2. **application/json**: ``{"website_id": "...", "source_url":
       "https://..."}``. The server fetches the URL with
       :meth:`PdfRunner.fetch_pdf_from_url`. Optional ``website_user_id``
       supplies a project test-user credential for sites that require
       auth.

    The two paths converge on
    :meth:`PdfRunner.create_or_find_pdf_document` which dedupes by
    ``(website_id, sha256)``. A dedup hit returns the *existing*
    document with **status 200**; a new document persists and returns
    **201** with a ``Location`` header pointing at
    ``/api/v1/pdf-documents/<id>``.

    Errors:

    - **400** — body missing required fields, ``website_id`` not in
      ``project_id``, JSON body without ``source_url``, multipart
      without ``pdf_file``, fetch failure (4xx/5xx from source url),
      file not a PDF (magic-byte check), or file exceeds the
      ``PDF_MAX_SIZE`` cap.
    - **404** — project or website does not exist.
    - **409** — server has no :class:`PdfRunner` configured.

    Auth: ADMIN/AUDITOR on the project — same as the legacy
    ``POST /projects/<id>/pdfs`` form.
    """
    import asyncio

    from auto_a11y.pdf.errors import FetchFailed, NotAPdf, PdfTooLarge
    from auto_a11y.web.typed_app import get_pdf_runner

    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id,
    )

    runner = get_pdf_runner()
    if runner is None:
        raise ConflictError(
            "PDF runner is not configured on this server"
        )

    user_id_str: str | None = None
    if current_user.is_authenticated:
        raw_uid: Any = current_user.get_id()
        user_id_str = str(raw_uid) if raw_uid is not None else None

    # Branch on Content-Type. Browser-submitted multipart from the
    # legacy form and a curl-style JSON POST both end up here; the
    # handler routes by the body shape rather than separate URLs to
    # keep the upload-or-link choice client-side.
    is_multipart = request.content_type and request.content_type.startswith(
        "multipart/form-data"
    )

    if is_multipart:
        website_id_raw: str | None = request.form.get("website_id")
        if not website_id_raw:
            raise ValidationError(
                "website_id is required",
                errors=(
                    _FieldError(
                        field="website_id", code="required",
                        message="required",
                    ),
                ),
            )
        website = get_db().get_website(website_id_raw)
        if website is None or website.project_id != project_id:
            raise NotFoundError(
                f"website {website_id_raw} not in project {project_id}"
            )

        uploaded = request.files.get("pdf_file")
        if uploaded is None or not uploaded.filename:
            raise ValidationError(
                "pdf_file part is required",
                errors=(
                    _FieldError(
                        field="pdf_file", code="required",
                        message="required",
                    ),
                ),
            )

        original_filename = (
            request.form.get("original_filename")
            or uploaded.filename
            or "document.pdf"
        )
        pdf_bytes = uploaded.read()

        try:
            doc = asyncio.run(
                runner.create_or_find_pdf_document(
                    pdf_bytes,
                    website_id=website_id_raw,
                    project_id=project_id,
                    source_type="uploaded",
                    discovered_from_page_id=None,
                    discovered_from_user_id=user_id_str,
                    original_filename=original_filename,
                    source_url=None,
                )
            )
        except NotAPdf as exc:
            raise ValidationError(
                "uploaded bytes are not a PDF",
                errors=(
                    _FieldError(
                        field="pdf_file", code="invalid_value",
                        message=str(exc),
                    ),
                ),
            ) from exc
        except PdfTooLarge as exc:
            raise ValidationError(
                f"PDF exceeds max size ({exc.size_bytes} > {exc.limit_bytes})",
                errors=(
                    _FieldError(
                        field="pdf_file", code="too_large",
                        message=f"{exc.size_bytes} > {exc.limit_bytes}",
                    ),
                ),
            ) from exc
    else:
        body = _require_dict_body()
        website_id_body = body.get("website_id")
        if not isinstance(website_id_body, str) or not website_id_body:
            raise ValidationError(
                "website_id is required",
                errors=(
                    _FieldError(
                        field="website_id", code="required",
                        message="required",
                    ),
                ),
            )
        website = get_db().get_website(website_id_body)
        if website is None or website.project_id != project_id:
            raise NotFoundError(
                f"website {website_id_body} not in project {project_id}"
            )

        source_url_raw = body.get("source_url")
        if not isinstance(source_url_raw, str) or not source_url_raw:
            raise ValidationError(
                "source_url is required when not uploading a file",
                errors=(
                    _FieldError(
                        field="source_url", code="required",
                        message="required",
                    ),
                ),
            )
        website_user_id_raw = body.get("website_user_id")
        website_user_id = (
            website_user_id_raw
            if isinstance(website_user_id_raw, str) and website_user_id_raw
            else None
        )

        try:
            pdf_bytes = asyncio.run(
                runner.fetch_pdf_from_url(
                    source_url_raw, website_user_id=website_user_id,
                )
            )
        except FetchFailed as exc:
            raise ValidationError(
                f"fetch failed: {exc.reason}",
                errors=(
                    _FieldError(
                        field="source_url", code="fetch_failed",
                        message=exc.reason,
                    ),
                ),
            ) from exc
        except PdfTooLarge as exc:
            raise ValidationError(
                f"remote PDF exceeds max size ({exc.size_bytes} > {exc.limit_bytes})",
                errors=(
                    _FieldError(
                        field="source_url", code="too_large",
                        message=f"{exc.size_bytes} > {exc.limit_bytes}",
                    ),
                ),
            ) from exc
        except NotAPdf as exc:
            raise ValidationError(
                "fetched bytes are not a PDF",
                errors=(
                    _FieldError(
                        field="source_url", code="invalid_value",
                        message=str(exc),
                    ),
                ),
            ) from exc

        derived_filename = (
            source_url_raw.rsplit("/", 1)[-1] or "document.pdf"
        )
        try:
            doc = asyncio.run(
                runner.create_or_find_pdf_document(
                    pdf_bytes,
                    website_id=website_id_body,
                    project_id=project_id,
                    source_type="manual_url",
                    discovered_from_page_id=None,
                    discovered_from_user_id=user_id_str,
                    original_filename=derived_filename,
                    source_url=source_url_raw,
                )
            )
        except NotAPdf as exc:
            raise ValidationError(
                "fetched bytes are not a PDF",
                errors=(
                    _FieldError(
                        field="source_url", code="invalid_value",
                        message=str(exc),
                    ),
                ),
            ) from exc
        except PdfTooLarge as exc:
            raise ValidationError(
                f"remote PDF exceeds max size ({exc.size_bytes} > {exc.limit_bytes})",
                errors=(
                    _FieldError(
                        field="source_url", code="too_large",
                        message=f"{exc.size_bytes} > {exc.limit_bytes}",
                    ),
                ),
            ) from exc

    # Dedup behaviour: ``create_or_find_pdf_document`` returns the
    # existing record on a (website_id, sha256) hit. We can't tell new
    # vs hit from the doc alone, so we infer: a freshly-created doc
    # has its ``discovered_at`` within the last second of "now". This
    # is sound because the legacy form returned the same redirect on
    # both paths — we just want clients to distinguish 200 (hit) from
    # 201 (created) when they care.
    # PdfDocument.discovered_at is non-Optional, so the comparison
    # alone is enough to infer "just created" vs dedup hit.
    is_new = (datetime.now() - doc.discovered_at).total_seconds() < 2.0
    response = jsonify(_serialize_pdf_document(doc))
    response.headers["Location"] = f"/api/v1/pdf-documents/{doc.id}"
    return response, (201 if is_new else 200)


@api_bp.route("/pdf-documents/<pdf_id>", methods=["GET"])
@api_endpoint
def get_pdf_document_rest(pdf_id: str) -> tuple[Response, int] | Response:
    """Read a PDF document's metadata.

    The PDF *bytes* and extracted images live behind separate endpoints
    that are deferred to a follow-up PR — this endpoint is metadata-only.
    """
    pdf = get_db().get_pdf_document(pdf_id)
    if pdf is None:
        raise NotFoundError(f"pdf document {pdf_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=pdf.project_id
    )
    return jsonify(_serialize_pdf_document(pdf))


@api_bp.route("/pdf-documents/<pdf_id>", methods=["DELETE"])
@api_endpoint
def delete_pdf_document_rest(pdf_id: str) -> tuple[Response, int]:
    """Delete a PDF document — DB record + filesystem artefacts.

    Mirrors the legacy /pdfs/<id>/delete cascade: the storage helper
    removes the per-PDF directory (PDF bytes, extracted images, cached
    pdfMax outputs) and then the DB record is dropped. ADMIN/AUDITOR
    only — CLIENT readers cannot tear down audit artefacts.
    """
    pdf = get_db().get_pdf_document(pdf_id)
    if pdf is None:
        raise NotFoundError(f"pdf document {pdf_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=pdf.project_id
    )
    _pdf_storage().delete(pdf)
    get_db().delete_pdf_document(pdf_id)
    return Response(status=204), 204


# ---------------------------------------------------------------------------
# PDF audit action endpoints — §5.9 of REST_API_ROADMAP.md.
#
# Replaces the legacy POST /pdfs/<id>/audit, GET /pdfs/<id>/audit-status,
# and POST /pdfs/<id>/cancel HTML/JSON-mix routes. The legacy ones stay
# alive on `pdf_bp` until the issue #21 frontend migration; these emit
# RFC 7807 errors and use the canonical /pdf-documents path.
#
# A PdfDocument can have *many* audit jobs over time. The
# ``/audits/latest`` segment selects which one the read/cancel apply
# to: most-recent ACTIVE job for that document (PENDING / RUNNING /
# CANCELLING), falling back to the absolute most-recent record so the
# UI can still surface "the last run" after it ends.
# ---------------------------------------------------------------------------


def _find_latest_pdf_audit_job(
    pdf_document_id: str, *, active_only: bool = False,
) -> dict[str, Any] | None:
    """Locate the most relevant PDF_AUDIT job for a document.

    When ``active_only`` is true, return only jobs in
    ``{PENDING, RUNNING, CANCELLING}``. Otherwise prefer active, fall
    back to absolute most-recent. Mirrors the legacy ``audit_status``
    handler's lookup so the new and old surfaces show the same job.
    """
    job_manager = JobManager.get_instance(get_db())
    active_statuses = [
        JobStatus.PENDING.value,
        JobStatus.RUNNING.value,
        JobStatus.CANCELLING.value,
    ]
    active_query: dict[str, Any] = {
        "job_type": JobType.PDF_AUDIT.value,
        "metadata.pdf_document_id": pdf_document_id,
        "status": {"$in": active_statuses},
    }
    active_doc = job_manager.collection.find_one(
        active_query, sort=[("created_at", -1)],
    )
    if active_doc is not None:
        return active_doc
    if active_only:
        return None
    return job_manager.collection.find_one(
        {
            "job_type": JobType.PDF_AUDIT.value,
            "metadata.pdf_document_id": pdf_document_id,
        },
        sort=[("created_at", -1)],
    )


def _serialize_pdf_audit_job(record: dict[str, Any]) -> dict[str, Any]:
    """Shape a PDF_AUDIT job document for the REST progress poll.

    Surfaces the same nested ``progress.{current,total,message,
    stage,fraction}`` shape the legacy ``audit_status`` produces so
    clients that move from the old URL only need to swap the path,
    not the parser. Reads progress fields via ``Any``-typed locals to
    avoid widening nested dict types.
    """
    progress_raw: Any = record.get("progress")
    progress_obj: dict[str, Any] = (
        cast(dict[str, Any], progress_raw)
        if isinstance(progress_raw, dict) else {}
    )
    details_raw: Any = progress_obj.get("details")
    details_obj: dict[str, Any] = (
        cast(dict[str, Any], details_raw)
        if isinstance(details_raw, dict) else {}
    )

    return {
        "job_id": record.get("job_id"),
        "status": record.get("status"),
        "created_at": _iso_or_none(record.get("created_at")),
        "completed_at": _iso_or_none(record.get("completed_at")),
        "progress": {
            "current": progress_obj.get("current"),
            "total": progress_obj.get("total"),
            "message": progress_obj.get("message"),
            "stage": details_obj.get("stage"),
            "fraction": details_obj.get("fraction"),
        },
    }


def _parse_pdf_audit_body(body: dict[str, Any]) -> dict[str, Any]:
    """Validate the audit-start body. Returns the kwargs for PdfAuditJob."""
    run_ai_raw = body.get("run_ai")
    run_ai: bool = run_ai_raw if isinstance(run_ai_raw, bool) else False

    wcag_level_raw = body.get("wcag_level", "AA")
    if not isinstance(wcag_level_raw, str) or wcag_level_raw not in ("AA", "AAA"):
        raise ValidationError(
            "wcag_level must be 'AA' or 'AAA'",
            errors=(
                _FieldError(
                    field="wcag_level", code="invalid_value",
                    message="must be 'AA' or 'AAA'",
                ),
            ),
        )

    locale_raw = body.get("locale", "en")
    if not isinstance(locale_raw, str):
        raise ValidationError(
            "locale must be a string",
            errors=(
                _FieldError(
                    field="locale", code="invalid_type", message="must be string"
                ),
            ),
        )

    return {
        "run_ai": run_ai,
        "wcag_level": wcag_level_raw,
        "locale": locale_raw,
    }


@api_bp.route("/pdf-documents/<pdf_id>/audits", methods=["POST"])
@api_endpoint
def start_pdf_audit(pdf_id: str) -> tuple[Response, int] | Response:
    """Queue a fresh audit for a PDF document.

    Body (all optional):

        {
          "run_ai":     bool,           // default false
          "wcag_level": "AA"|"AAA",     // default AA
          "locale":     "en"|"fr"|...   // default en
        }

    Returns 202 with the new ``job_id``. The audit runs in the
    background via :class:`auto_a11y.core.pdf_audit_job.PdfAuditJob`;
    poll ``GET /pdf-documents/<id>/audits/latest`` for progress.

    Errors:

    - **404** — pdf document does not exist
    - **409** — an audit is already in flight for this document (a
      second start would compete for the same on-disk artefacts)
    - **503** — the server has no ``PdfRunner`` configured (the audit
      pipeline is optional; deployments without the playwright/poppler
      stack run with ``pdf_runner=None``)
    """
    from auto_a11y.core.pdf_audit_job import PdfAuditJob
    from auto_a11y.web.typed_app import get_pdf_runner

    pdf = get_db().get_pdf_document(pdf_id)
    if pdf is None:
        raise NotFoundError(f"pdf document {pdf_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=pdf.project_id
    )

    runner = get_pdf_runner()
    if runner is None:
        raise ConflictError(
            "PDF audit runner is not configured on this server"
        )

    if pdf.status == PdfDocumentStatus.AUDITING:
        raise ConflictError(
            f"pdf document {pdf_id} already has an audit in progress"
        )

    body = _require_dict_body() if request.data else {}
    kwargs = _parse_pdf_audit_body(body)

    user_id_str: str
    if current_user.is_authenticated:
        raw_uid: Any = current_user.get_id()
        user_id_str = str(raw_uid) if raw_uid is not None else "anonymous"
    else:
        user_id_str = "anonymous"

    job = PdfAuditJob(
        runner=runner,
        db=get_db(),
        pdf_document_id=pdf_id,
        run_ai=kwargs["run_ai"],
        ai_api_key=None,
        wcag_level=kwargs["wcag_level"],
        locale=kwargs["locale"],
        user_id=user_id_str,
    )
    job_id = job.start()

    return jsonify({
        "job_id": job_id,
        "pdf_document_id": pdf_id,
        "run_ai": kwargs["run_ai"],
        "wcag_level": kwargs["wcag_level"],
        "locale": kwargs["locale"],
        "status": "queued",
    }), 202


@api_bp.route(
    "/pdf-documents/<pdf_id>/audits/latest", methods=["GET"]
)
@api_endpoint
def get_latest_pdf_audit(pdf_id: str) -> tuple[Response, int] | Response:
    """Read the most-recent (or in-flight) audit's status + progress.

    Returns:

        {
          "pdf_document_id":    "...",
          "doc_status":         "auditing|audited|audit_failed|...",
          "error_reason":       null | "...",
          "last_audit_result_id": null | "...",
          "job":                null | {...job shape...}
        }

    ``job`` is ``null`` only when the document has *never* had an
    audit job recorded. After at least one run the latest job stays
    in the response so clients can render "last audit failed at X"
    even when no fresh job is in flight.
    """
    pdf = get_db().get_pdf_document(pdf_id)
    if pdf is None:
        raise NotFoundError(f"pdf document {pdf_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
        project_id=pdf.project_id,
    )

    job_doc = _find_latest_pdf_audit_job(pdf_id)
    job_payload: dict[str, Any] | None = (
        _serialize_pdf_audit_job(job_doc) if job_doc is not None else None
    )

    return jsonify({
        "pdf_document_id": pdf_id,
        "doc_status": pdf.status.value,
        "error_reason": pdf.error_reason,
        "last_audit_result_id": pdf.last_audit_result_id,
        "job": job_payload,
    })


@api_bp.route(
    "/pdf-documents/<pdf_id>/audits/latest/cancel", methods=["POST"]
)
@api_endpoint
def cancel_latest_pdf_audit(
    pdf_id: str,
) -> tuple[Response, int] | Response:
    """Request cancellation of every in-flight audit for this PDF.

    Iterates over every PDF_AUDIT job for this document in
    ``{PENDING, RUNNING, CANCELLING}`` and calls
    :meth:`JobManager.request_cancellation` on each. The worker reads
    the flag at its next checkpoint and exits cleanly.

    The PDF's ``status`` is forcibly reset to ``AUDIT_FAILED`` with
    ``error_reason='Audit cancelled by user'``. This unblocks the user
    even when no live job exists (e.g. the previous run crashed and
    left the document stuck in ``AUDITING``) — the regular start
    endpoint refuses to re-audit a document already in ``AUDITING``,
    so this manual reset is the escape hatch.

    Returns 202 with the new doc status and how many active jobs were
    flagged. A document with no live job that is also not stuck in
    AUDITING returns 409 — the cancel verb implies there's something
    to cancel.
    """
    pdf = get_db().get_pdf_document(pdf_id)
    if pdf is None:
        raise NotFoundError(f"pdf document {pdf_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=pdf.project_id
    )

    job_manager = JobManager.get_instance(get_db())

    user_id_str: str
    if current_user.is_authenticated:
        raw_uid: Any = current_user.get_id()
        user_id_str = str(raw_uid) if raw_uid is not None else "anonymous"
    else:
        user_id_str = "anonymous"

    active_statuses = [
        JobStatus.PENDING.value,
        JobStatus.RUNNING.value,
        JobStatus.CANCELLING.value,
    ]
    cancelled = 0
    for raw_doc in job_manager.collection.find({
        "job_type": JobType.PDF_AUDIT.value,
        "metadata.pdf_document_id": pdf_id,
        "status": {"$in": active_statuses},
    }):
        doc_any: Any = raw_doc
        job_id_value: Any = doc_any.get("job_id")
        if not isinstance(job_id_value, str):
            continue
        if job_manager.request_cancellation(
            job_id_value, requested_by=user_id_str
        ):
            cancelled += 1

    # 409 when nothing to cancel — but allow forcing through if the
    # document is stuck in AUDITING (the legacy escape hatch).
    if cancelled == 0 and pdf.status != PdfDocumentStatus.AUDITING:
        raise ConflictError(
            f"pdf document {pdf_id} has no audit in progress"
        )

    pdf.status = PdfDocumentStatus.AUDIT_FAILED
    pdf.error_reason = "Audit cancelled by user"
    get_db().update_pdf_document(pdf)

    return jsonify({
        "pdf_document_id": pdf_id,
        "cancellation_requested_count": cancelled,
        "doc_status": pdf.status.value,
    }), 202


# ---------------------------------------------------------------------------
# PDF artefact serving — §5.9 of REST_API_ROADMAP.md.
#
# Five binary/JSON read endpoints that close out the §5.9 cluster:
# stored PDF bytes, extracted images, derived export reports
# (Markdown/HTML), the cached pdfMax issue-map JSON, and the cached
# pdfMax accessibility markdown report. The legacy ``pdf_bp`` routes
# still serve the admin frontend until the issue #21 migration; these
# REST equivalents emit RFC 7807 errors instead of flash + redirect
# and use the canonical /pdf-documents path.
# ---------------------------------------------------------------------------


def _resolve_pdf_or_404(pdf_id: str) -> PdfDocument:
    pdf = get_db().get_pdf_document(pdf_id)
    if pdf is None:
        raise NotFoundError(f"pdf document {pdf_id} not found")
    return pdf


@api_bp.route("/pdf-documents/<pdf_id>/file", methods=["GET"])
@api_endpoint
def get_pdf_file(pdf_id: str) -> Response | tuple[Response, int]:
    """Stream the stored PDF bytes.

    Returns ``application/pdf`` with ``Content-Disposition: inline`` so
    clients can embed the file in an ``<iframe>`` or render with a
    PDF.js viewer. ``X-Frame-Options: SAMEORIGIN`` and a same-origin
    CSP mirror the legacy route's iframe-friendly defaults.

    ``download_name`` falls back to ``document.pdf`` when the source
    record has no ``original_filename``; for uploads we keep the
    user-supplied filename so the browser's "Save As" prefill is
    useful.
    """
    from flask import send_file

    pdf = _resolve_pdf_or_404(pdf_id)
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
        project_id=pdf.project_id,
    )

    pdf_path = _pdf_storage().local_path(pdf)
    if not pdf_path.exists():
        raise NotFoundError(f"pdf document {pdf_id} file no longer on disk")

    download_name = pdf.original_filename or "document.pdf"
    response = send_file(
        str(pdf_path),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=download_name,
    )
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    # Explicit CSP override so the global after_request hook (if any)
    # leaves this iframe-embed surface alone. Same value as the legacy
    # /pdfs/<id>/file route.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; frame-ancestors 'self'"
    )
    response.headers["Content-Disposition"] = (
        f'inline; filename="{download_name}"'
    )
    return response


@api_bp.route(
    "/pdf-documents/<pdf_id>/images/<image_name>", methods=["GET"]
)
@api_endpoint
def get_pdf_image(
    pdf_id: str, image_name: str,
) -> Response | tuple[Response, int]:
    """Stream one extracted image (e.g. ``page_001.png``).

    The audit pipeline rasterises each PDF page into the document's
    ``images_dir_for`` directory; this endpoint serves any of those
    bytes back. Path traversal guard rejects ``..``, ``/``, and ``\\``
    in the image name so the URL cannot escape the per-document image
    directory. Any extracted image is returned as ``image/png`` — the
    pipeline only writes PNGs.
    """
    from flask import send_file

    if ".." in image_name or "/" in image_name or "\\" in image_name:
        raise ValidationError(
            "image_name must not traverse directories",
            errors=(
                _FieldError(
                    field="image_name", code="invalid_value",
                    message="must not contain '..' or path separators",
                ),
            ),
        )

    pdf = _resolve_pdf_or_404(pdf_id)
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
        project_id=pdf.project_id,
    )

    image_path = _pdf_storage().images_dir_for(pdf) / image_name
    if not image_path.exists():
        raise NotFoundError(
            f"image {image_name} not found for pdf document {pdf_id}"
        )

    return send_file(str(image_path), mimetype="image/png")


_PDF_EXPORT_FORMATS: frozenset[str] = frozenset({"md", "html"})
_PDF_EXPORT_LOCALES: frozenset[str] = frozenset({"en", "fr"})


@api_bp.route(
    "/pdf-documents/<pdf_id>/export", methods=["GET"]
)
@api_endpoint
def export_pdf_audit(pdf_id: str) -> Response | tuple[Response, int]:
    """Stream a self-contained audit report (Markdown or HTML).

    Replaces the legacy ``/pdfs/<id>/export.<fmt>`` URL with a
    ``?format=<fmt>`` query string per the roadmap.

    Query:

    - ``format=md|html`` — required
    - ``locale=en|fr``   — optional, default ``en``; unrecognised
      values silently fall back to ``en``

    The report body is derived purely from the persisted
    :class:`TestResult` referenced by ``pdf.last_audit_result_id``, so
    an in-flight audit returns the *previous* report, not a partial
    one. When the document has never been audited the export is still
    served but contains the "no findings" stub the report builders
    emit for an empty result — clients can detect this via the empty
    ``finding`` list in the body rather than a separate 404 branch.

    ``Cache-Control: private, no-cache`` matches the legacy route —
    derived content is safe to cache locally but a shared cache could
    leak between users.
    """
    fmt_raw = request.args.get("format")
    if not isinstance(fmt_raw, str) or fmt_raw not in _PDF_EXPORT_FORMATS:
        raise ValidationError(
            "format must be 'md' or 'html'",
            errors=(
                _FieldError(
                    field="format", code="invalid_value",
                    message="must be 'md' or 'html'",
                ),
            ),
        )

    pdf = _resolve_pdf_or_404(pdf_id)
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
        project_id=pdf.project_id,
    )

    test_result = None
    if pdf.last_audit_result_id:
        test_result = get_db().get_test_result(pdf.last_audit_result_id)

    locale = request.args.get("locale", "en")
    if locale not in _PDF_EXPORT_LOCALES:
        locale = "en"

    # Local import — keeps Fluent-aware report_export off the import
    # graph for every PDF read until an export is actually requested.
    from auto_a11y.pdf.report_export import (
        build_html_report,
        build_markdown_report,
    )

    base_name = (
        pdf.original_filename.removesuffix(".pdf")
        if pdf.original_filename
        and pdf.original_filename.lower().endswith(".pdf")
        else (pdf.original_filename or "audit-report")
    )

    if fmt_raw == "md":
        body = build_markdown_report(pdf, test_result, locale=locale)
        mimetype = "text/markdown; charset=utf-8"
        filename = f"{base_name}_accessibility_report.md"
    else:
        body = build_html_report(pdf, test_result, locale=locale)
        mimetype = "text/html; charset=utf-8"
        filename = f"{base_name}_accessibility_report.html"

    response = Response(body.encode("utf-8"), mimetype=mimetype)
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{filename}"'
    )
    response.headers["Cache-Control"] = "private, no-cache"
    return response


@api_bp.route(
    "/pdf-documents/<pdf_id>/issue-map", methods=["GET"]
)
@api_endpoint
def get_pdf_issue_map(pdf_id: str) -> Response | tuple[Response, int]:
    """Serve the cached pdfMax ``*_issue_map.json`` for viewer overlays.

    The pdfMax subprocess writes one issue-map JSON per audit run
    alongside the Markdown report. Viewer UIs fetch this to position
    issue overlays on each page and highlight the matching sidebar
    card.

    Returns 404 (with a Problem-Details body) when:

    - the document doesn't exist
    - the document's status isn't ``AUDITED`` (clearing test results
      flips status back to PENDING but the cache files may linger;
      gating on status avoids leaking stale overlays into a fresh UI)
    - no ``pdfmax-report`` cache directory exists
    - no ``*_issue_map.json`` file is inside it

    ``Cache-Control: private, max-age=60`` matches the legacy route —
    cheap to re-render within a session, never via a shared cache.
    """
    from flask import send_file

    pdf = _resolve_pdf_or_404(pdf_id)
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
        project_id=pdf.project_id,
    )

    if pdf.status is not PdfDocumentStatus.AUDITED:
        raise NotFoundError(
            f"pdf document {pdf_id} has no current audit issue-map"
        )

    pdf_path = _pdf_storage().local_path(pdf)
    cache_dir = pdf_path.parent / "pdfmax-report"
    if not cache_dir.is_dir():
        raise NotFoundError(
            f"pdf document {pdf_id} issue-map cache not present"
        )

    candidates = sorted(cache_dir.glob("*_issue_map.json"))
    if not candidates:
        raise NotFoundError(
            f"pdf document {pdf_id} issue-map cache file missing"
        )

    response = send_file(str(candidates[0]), mimetype="application/json")
    response.headers["Cache-Control"] = "private, max-age=60"
    return response


@api_bp.route(
    "/pdf-documents/<pdf_id>/reports/pdfmax", methods=["GET"]
)
@api_endpoint
def get_pdfmax_report(pdf_id: str) -> Response | tuple[Response, int]:
    """Return the cached pdfMax accessibility Markdown report.

    Pure cache lookup — the pdfMax subprocess only runs as part of
    the audit job (:class:`PdfAuditJob`), so this endpoint never
    blocks on the 10-30s audit pipeline.

    Status is the gate: only ``AUDITED`` documents return content.
    PENDING / FETCHING / AUDITING / FETCH_FAILED / AUDIT_FAILED all
    return 404 even when stale cache files happen to exist on disk —
    keeps the response semantically aligned with what the audit
    pipeline considers "current".

    Body is ``text/markdown; charset=utf-8`` (the raw report text).
    HTML rendering of the markdown is the legacy ``pdf_bp`` route's
    job; this REST surface returns the source.
    """
    pdf = _resolve_pdf_or_404(pdf_id)
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
        project_id=pdf.project_id,
    )

    if pdf.status is not PdfDocumentStatus.AUDITED:
        raise NotFoundError(
            f"pdf document {pdf_id} has no current pdfmax report"
        )

    pdf_path = _pdf_storage().local_path(pdf)
    cache_dir = pdf_path.parent / "pdfmax-report"
    if not cache_dir.is_dir():
        raise NotFoundError(
            f"pdf document {pdf_id} pdfmax report cache not present"
        )

    candidates = sorted(cache_dir.glob("*_accessibility_report.md"))
    if not candidates:
        raise NotFoundError(
            f"pdf document {pdf_id} pdfmax report cache file missing"
        )

    try:
        text = candidates[0].read_text(encoding="utf-8")
    except OSError as exc:
        raise NotFoundError(
            f"failed to read pdfmax report: {exc}"
        ) from exc

    response = Response(text.encode("utf-8"), mimetype="text/markdown; charset=utf-8")
    response.headers["Cache-Control"] = "private, max-age=60"
    return response


# ---------------------------------------------------------------------------
# Permission groups (REST shape — uses the @api_endpoint scaffolding).
#
# Sits alongside the existing groups_bp HTML routes at /groups/* (still
# serving the admin frontend). Per docs/REST_API_ROADMAP.md §5.11.
# Members consolidation (members.py / project_users.py /
# project_participants.py / website_users.py) is a separate follow-up.
# ---------------------------------------------------------------------------

from auto_a11y.models.permission_group import (  # noqa: E402
    PERMISSION_LEVELS,
    PermissionGroup,
    RESOURCE_NOUNS,
)
from auto_a11y.web.api import require_global_permission  # noqa: E402

_RESOURCE_NOUNS_SET: frozenset[str] = frozenset(RESOURCE_NOUNS)
_PERMISSION_LEVEL_NAMES: frozenset[str] = frozenset(PERMISSION_LEVELS.keys())


def _serialize_permission_group(group: PermissionGroup) -> dict[str, Any]:
    """Project a :class:`PermissionGroup` to a JSON-safe dict."""

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    return {
        "id": group.id,
        "name": group.name,
        "description": group.description,
        "permissions": dict(group.permissions),
        "is_system": group.is_system,
        "created_at": _iso(group.created_at),
        "updated_at": _iso(group.updated_at),
    }


def _validate_permissions_dict(raw: Any, *, field: str) -> dict[str, str]:
    """Validate a permissions dict against the ``RESOURCE_NOUNS`` and
    ``PERMISSION_LEVELS`` enums.

    Unknown resource nouns and unknown permission levels are both 400s
    with structured field errors.  Missing resource nouns default to
    ``'none'`` so callers can send a partial dict (only the resources
    they want to grant something on).
    """
    if not isinstance(raw, dict):
        raise ValidationError(
            f"{field} must be an object",
            errors=(_FieldError(field=field, code="invalid_type", message="must be object"),),
        )
    raw_dict = cast(dict[str, Any], raw)
    field_errors: list[_FieldError] = []
    for key, value in raw_dict.items():
        if key not in _RESOURCE_NOUNS_SET:
            field_errors.append(_FieldError(
                field=f"{field}.{key}", code="unknown_resource",
                message=f"not a known resource noun (allowed: {sorted(_RESOURCE_NOUNS_SET)})",
            ))
            continue
        if not isinstance(value, str) or value not in _PERMISSION_LEVEL_NAMES:
            field_errors.append(_FieldError(
                field=f"{field}.{key}", code="invalid_value",
                message=f"not a recognized permission level (allowed: {sorted(_PERMISSION_LEVEL_NAMES)})",
            ))
            continue
    if field_errors:
        raise ValidationError(
            f"{field} contains invalid entries", errors=tuple(field_errors)
        )

    permissions: dict[str, str] = {r: "none" for r in RESOURCE_NOUNS}
    for key, value in raw_dict.items():
        if isinstance(value, str):
            permissions[key] = value
    return permissions


def _validate_group_name_unique(name: str, *, exclude_id: str | None = None) -> None:
    existing = get_db().get_group_by_name(name)
    if existing is not None and existing.id != exclude_id:
        raise ConflictError(f"group name {name!r} is already in use")


def _build_group_from_body(body: dict[str, Any]) -> PermissionGroup:
    name_raw = body.get("name")
    if not isinstance(name_raw, str) or not name_raw.strip():
        raise ValidationError(
            "name is required",
            errors=(_FieldError(field="name", code="required", message="required"),),
        )
    name = name_raw.strip()
    description_raw = body.get("description", "")
    if not isinstance(description_raw, str):
        raise ValidationError(
            "description must be a string",
            errors=(_FieldError(field="description", code="invalid_type", message="must be string"),),
        )
    permissions = (
        _validate_permissions_dict(body["permissions"], field="permissions")
        if "permissions" in body
        else {r: "none" for r in RESOURCE_NOUNS}
    )
    return PermissionGroup(
        name=name,
        description=description_raw,
        permissions=permissions,
    )


def _apply_patch_to_group(group: PermissionGroup, body: dict[str, Any]) -> PermissionGroup:
    """Apply only the keys present in ``body`` to ``group``.

    ``is_system`` is intentionally not patchable — that flag protects
    the seeded default groups from deletion, and clients shouldn't be
    able to flip it on/off.
    """
    if "name" in body:
        if not isinstance(body["name"], str) or not body["name"].strip():
            raise ValidationError(
                "name must be a non-empty string",
                errors=(_FieldError(field="name", code="invalid_value", message="must be non-empty string"),),
            )
        group.name = body["name"].strip()
    if "description" in body:
        desc = body["description"]
        if not isinstance(desc, str):
            raise ValidationError(
                "description must be a string",
                errors=(_FieldError(field="description", code="invalid_type", message="must be string"),),
            )
        group.description = desc
    if "permissions" in body:
        group.permissions = _validate_permissions_dict(body["permissions"], field="permissions")
    group.updated_at = datetime.now()
    return group


@api_bp.route("/groups", methods=["GET"])
@api_endpoint
def list_groups_rest() -> tuple[Response, int] | Response:
    """List all permission groups with cursor pagination.

    Defaults to the smallest sensible page; the underlying collection
    is bounded (a handful of system groups + custom additions), so the
    response shape with ``next_cursor`` is preserved for forward
    compatibility even though most deployments will fit on one page.
    """
    require_global_permission("groups", "read")

    limit = parse_limit(request.args.get("limit"))
    cursor_raw = request.args.get("cursor")
    cursor = _Cursor.decode(cursor_raw) if cursor_raw else None

    query: dict[str, Any] = {}
    if cursor is not None:
        from bson import ObjectId
        try:
            query["_id"] = {"$lt": ObjectId(cursor.last_id)}
        except Exception as exc:
            raise ValidationError(
                "cursor.last_id is not a valid ObjectId",
                errors=(_FieldError(field="cursor.last_id", code="invalid_format", message=str(exc)),),
            ) from exc

    docs = list(get_db().groups.find(query).sort("_id", -1).limit(limit + 1))
    groups = [PermissionGroup.from_dict(doc) for doc in docs]
    page = paginate(
        groups, limit=limit, get_id=lambda g: str(g.mongo_id) if g.mongo_id else ""
    )
    return jsonify(
        {
            "items": [_serialize_permission_group(g) for g in page["items"]],
            "next_cursor": page["next_cursor"],
        }
    )


@api_bp.route("/groups", methods=["POST"])
@api_endpoint
def create_group_rest() -> tuple[Response, int]:
    """Create a permission group."""
    require_global_permission("groups", "create")
    body = _require_dict_body()
    group = _build_group_from_body(body)
    _validate_group_name_unique(group.name)
    group_id = get_db().create_group(group)
    refreshed = get_db().get_group(group_id)
    if refreshed is None:
        raise ConflictError("group failed to persist")
    response = jsonify(_serialize_permission_group(refreshed))
    response.headers["Location"] = f"/api/v1/groups/{group_id}"
    return response, 201


@api_bp.route("/groups/<group_id>", methods=["GET"])
@api_endpoint
def get_group_rest(group_id: str) -> tuple[Response, int] | Response:
    """Get a permission group by id."""
    require_global_permission("groups", "read")
    group = get_db().get_group(group_id)
    if group is None:
        raise NotFoundError(f"group {group_id} not found")
    return jsonify(_serialize_permission_group(group))


@api_bp.route("/groups/<group_id>", methods=["PUT"])
@api_endpoint
def replace_group_rest(group_id: str) -> tuple[Response, int] | Response:
    """Full replace of a group's editable fields.

    ``is_system`` and ``created_at`` are preserved; the ``updated_at``
    timestamp is bumped.
    """
    require_global_permission("groups", "update")
    existing = get_db().get_group(group_id)
    if existing is None:
        raise NotFoundError(f"group {group_id} not found")
    body = _require_dict_body()
    replaced = _build_group_from_body(body)
    _validate_group_name_unique(replaced.name, exclude_id=group_id)
    replaced.mongo_id = existing.mongo_id
    replaced.is_system = existing.is_system
    replaced.created_at = existing.created_at
    replaced.updated_at = datetime.now()
    if not get_db().update_group(replaced):
        raise ConflictError("group could not be updated")
    return jsonify(_serialize_permission_group(replaced))


@api_bp.route("/groups/<group_id>", methods=["PATCH"])
@api_endpoint
def patch_group_rest(group_id: str) -> tuple[Response, int] | Response:
    """Partial update — only fields present in the request body are changed."""
    require_global_permission("groups", "update")
    group = get_db().get_group(group_id)
    if group is None:
        raise NotFoundError(f"group {group_id} not found")
    body = _require_dict_body()
    if "name" in body and isinstance(body["name"], str):
        _validate_group_name_unique(body["name"].strip(), exclude_id=group_id)
    patched = _apply_patch_to_group(group, body)
    if not get_db().update_group(patched):
        raise ConflictError("group could not be updated")
    return jsonify(_serialize_permission_group(patched))


@api_bp.route("/groups/<group_id>", methods=["DELETE"])
@api_endpoint
def delete_group_rest(group_id: str) -> tuple[Response, int]:
    """Delete a non-system group.

    System groups (``is_system=True``) are the seeded default groups
    (Admin, Auditor, Client). Removing them would orphan every
    project_member.group_ids reference, so the legacy form blocks it
    and the REST endpoint mirrors that with a 409.
    """
    require_global_permission("groups", "delete")
    group = get_db().get_group(group_id)
    if group is None:
        raise NotFoundError(f"group {group_id} not found")
    if group.is_system:
        raise ConflictError(
            f"group {group_id} is a system group and cannot be deleted"
        )
    get_db().delete_group(group_id)
    return Response(status=204), 204


# ---------------------------------------------------------------------------
# Users + project/website members and test users (REST shape).
#
# Per docs/REST_API_ROADMAP.md §5.11. The roadmap recommends folding
# five overlapping legacy blueprints — members.py, project_users.py,
# project_participants.py, website_users.py, plus the user-search bit
# of members — into three top-level surfaces:
#   - /api/v1/users (system-user search and "me" lookup)
#   - /api/v1/projects/<id>/members (platform access control;
#     ProjectMember[user_id, group_ids[]])
#   - /api/v1/websites/<id>/members
#
# We diverge slightly from the roadmap on naming for the test users:
# the legacy ProjectUser and WebsiteUser models are credentials for
# logging into sites *under* test (login automation), not platform
# membership, so calling them "members" would be misleading.  They
# live at /api/v1/projects/<id>/test-users and
# /api/v1/websites/<id>/test-users with a
# /api/v1/project-test-users/<id> + /api/v1/website-test-users/<id>
# single-resource surface.
#
# Out of scope, deferred to a follow-up:
#   - Lived-experience testers and supervisors (§5.11 also mentions
#     project_participants.py — they're inline arrays on the Project
#     document and need a separate endpoint design)
#   - The legacy POST .../toggle endpoint — subsumed by PATCH with
#     {enabled: false} on the test-user resources
#   - Test-login automation action endpoints (POST .../test-login) —
#     they share async-job mechanics with the broader test-runs PR
# ---------------------------------------------------------------------------

from auto_a11y.models.app_user import AppUser  # noqa: E402
from auto_a11y.models.project_member import ProjectMember  # noqa: E402
from auto_a11y.models.project_user import ProjectUser  # noqa: E402
from auto_a11y.models.website_user import WebsiteUser  # noqa: E402

# Allowed values are duplicated between the project_user.AuthenticationMethod
# and website_user.AuthenticationMethod enums — they have identical
# definitions but distinct types. We validate against the string values and
# let each model's ``from_dict`` reconstruct the right enum on its side.
_AUTH_METHOD_VALUES: frozenset[str] = frozenset(
    {"form_login", "basic_auth", "oauth", "sso"}
)


def _serialize_app_user_search_hit(user: AppUser) -> dict[str, Any]:
    """Project an :class:`AppUser` to the search-result shape.

    Deliberately narrow — this endpoint exists for picking a user when
    adding a project member, so it surfaces the email, display name,
    and id and nothing else (no password hash, no SSO id, no
    last_login). The full AppUser surface is out of scope for #27.
    """
    return {
        "user_id": user.id,
        "email": user.email,
        "display_name": user.display_name,
    }


def _serialize_login_config_via_model(config: Any) -> dict[str, Any]:
    """Project either model's LoginConfig to JSON.

    Both ProjectUser and WebsiteUser have their own LoginConfig class;
    they share an identical to_dict() shape, so we delegate to it
    instead of binding to one specific class.
    """
    raw: dict[str, Any] = config.to_dict()
    return {
        "authentication_method": raw["authentication_method"],
        "login_url": raw["login_url"],
        "username_field_selector": raw["username_field_selector"],
        "password_field_selector": raw["password_field_selector"],
        "submit_button_selector": raw["submit_button_selector"],
        "success_indicator_selector": raw["success_indicator_selector"],
        "logout_url": raw["logout_url"],
        "logout_button_selector": raw["logout_button_selector"],
        "logout_success_indicator_selector": raw["logout_success_indicator_selector"],
        "additional_steps": list(raw["additional_steps"]),
        "session_timeout_minutes": raw["session_timeout_minutes"],
    }


def _serialize_project_test_user(user: ProjectUser) -> dict[str, Any]:
    """Project a :class:`ProjectUser` to a JSON-safe dict.

    The raw ``password`` is intentionally not included — it's a
    test-credentials secret used by the login automation and the API
    surfaces only a ``password_set`` boolean. PATCHing with a blank
    password preserves the existing one, mirroring the admin-settings
    secret-handling rule.
    """

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    return {
        "id": user.id,
        "project_id": user.project_id,
        "username": user.username,
        "password_set": bool(user.password),
        "display_name": user.display_name,
        "roles": list(user.roles),
        "description": user.description,
        "login_config": _serialize_login_config_via_model(user.login_config),
        "enabled": user.enabled,
        "last_used": _iso(user.last_used),
        "last_login_success": user.last_login_success,
        "last_login_error": user.last_login_error,
        "created_at": _iso(user.created_at),
        "updated_at": _iso(user.updated_at),
    }


def _serialize_website_test_user(user: WebsiteUser) -> dict[str, Any]:
    """Same shape as project test user, with ``website_id`` instead of ``project_id``."""

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    return {
        "id": user.id,
        "website_id": user.website_id,
        "username": user.username,
        "password_set": bool(user.password),
        "display_name": user.display_name,
        "roles": list(user.roles),
        "description": user.description,
        "login_config": _serialize_login_config_via_model(user.login_config),
        "enabled": user.enabled,
        "last_used": _iso(user.last_used),
        "last_login_success": user.last_login_success,
        "last_login_error": user.last_login_error,
        "created_at": _iso(user.created_at),
        "updated_at": _iso(user.updated_at),
    }


def _parse_login_config_dict(raw: Any, *, field: str) -> dict[str, Any]:
    """Validate a JSON ``login_config`` object and return a normalized dict.

    Returning a dict (rather than a model instance) lets each test-user
    model — ``ProjectUser`` and ``WebsiteUser`` each ship their own
    ``LoginConfig`` class — do its own ``LoginConfig.from_dict()``
    reconstruction without forcing the validator to know which one.

    Validates ``authentication_method`` against the shared set of
    allowed string values, types optional fields, and rejects
    ``additional_steps`` entries that aren't objects.
    """
    if not isinstance(raw, dict):
        raise ValidationError(
            f"{field} must be an object",
            errors=(_FieldError(field=field, code="invalid_type", message="must be object"),),
        )
    raw_dict = cast(dict[str, Any], raw)
    auth_method_raw = raw_dict.get("authentication_method", "form_login")
    if not isinstance(auth_method_raw, str) or auth_method_raw not in _AUTH_METHOD_VALUES:
        raise ValidationError(
            f"{field}.authentication_method is not recognized",
            errors=(_FieldError(
                field=f"{field}.authentication_method", code="invalid_value",
                message=f"must be one of {sorted(_AUTH_METHOD_VALUES)}"),),
        )

    def _opt_str(key: str) -> str | None:
        value = raw_dict.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValidationError(
                f"{field}.{key} must be a string or null",
                errors=(_FieldError(field=f"{field}.{key}", code="invalid_type", message="must be string"),),
            )
        return value

    additional_steps_raw: Any = raw_dict.get("additional_steps", [])
    if not isinstance(additional_steps_raw, list):
        raise ValidationError(
            f"{field}.additional_steps must be an array",
            errors=(_FieldError(field=f"{field}.additional_steps", code="invalid_type", message="must be array"),),
        )
    additional_steps: list[dict[str, Any]] = []
    for index, step in enumerate(_iter_to_any_list(additional_steps_raw)):
        if not isinstance(step, dict):
            raise ValidationError(
                f"{field}.additional_steps[{index}] must be an object",
                errors=(_FieldError(field=f"{field}.additional_steps[{index}]", code="invalid_type", message="must be object"),),
            )
        additional_steps.append(cast(dict[str, Any], step))

    session_timeout_raw: Any = raw_dict.get("session_timeout_minutes", 30)
    if isinstance(session_timeout_raw, bool) or not isinstance(session_timeout_raw, int):
        raise ValidationError(
            f"{field}.session_timeout_minutes must be an integer",
            errors=(_FieldError(field=f"{field}.session_timeout_minutes", code="invalid_type", message="must be integer"),),
        )

    return {
        "authentication_method": auth_method_raw,
        "login_url": _opt_str("login_url"),
        "username_field_selector": _opt_str("username_field_selector"),
        "password_field_selector": _opt_str("password_field_selector"),
        "submit_button_selector": _opt_str("submit_button_selector"),
        "success_indicator_selector": _opt_str("success_indicator_selector"),
        "logout_url": _opt_str("logout_url"),
        "logout_button_selector": _opt_str("logout_button_selector"),
        "logout_success_indicator_selector": _opt_str("logout_success_indicator_selector"),
        "additional_steps": additional_steps,
        "session_timeout_minutes": int(session_timeout_raw),
    }


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


@api_bp.route("/users/me", methods=["GET"])
@api_endpoint
def get_current_user() -> tuple[Response, int] | Response:
    """Return basic info about the currently-authenticated user.

    Useful for SPAs that need to know who they're logged in as without
    rolling their own session-introspection endpoint. Surface is
    intentionally minimal — full AppUser CRUD is out of scope (auth
    flows are owned by the legacy auth.py blueprint per roadmap §5.13).
    """
    require_authenticated()
    return jsonify({
        "user_id": str(current_user.get_id()),
        "email": getattr(current_user, "email", None),
        "display_name": getattr(current_user, "display_name", None),
        "is_superadmin": bool(getattr(current_user, "is_superadmin", False)),
    })


@api_bp.route("/users/search", methods=["GET"])
@api_endpoint
def search_users_rest() -> tuple[Response, int] | Response:
    """Search active app users by email/display name.

    Replaces the legacy ``GET /members/api/search-users``. Results are
    capped at 20 entries server-side. The ``exclude_project`` query
    param strips out users who are already members of that project,
    which is the standard usage when populating a "add member"
    autocomplete.
    """
    require_authenticated()
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"users": []})

    limit_raw = request.args.get("limit", "10")
    try:
        limit = min(max(int(limit_raw), 1), 20)
    except ValueError as exc:
        raise ValidationError(
            "limit must be an integer",
            errors=(_FieldError(field="limit", code="invalid_type", message=str(exc)),),
        ) from exc

    exclude_user_ids: list[str] = []
    exclude_project = (request.args.get("exclude_project") or "").strip()
    if exclude_project:
        project = get_db().get_project(exclude_project)
        if project is not None:
            exclude_user_ids = [m.user_id for m in project.members]

    users = get_db().search_app_users(
        query=q,
        exclude_user_ids=exclude_user_ids or None,
        limit=limit,
    )
    return jsonify({"users": [_serialize_app_user_search_hit(u) for u in users]})


# ---------------------------------------------------------------------------
# Project members (platform access control — ProjectMember[user_id, group_ids[]])
# ---------------------------------------------------------------------------


def _serialize_project_member(member: ProjectMember, *, user: AppUser | None) -> dict[str, Any]:
    return {
        "user_id": member.user_id,
        "email": user.email if user is not None else None,
        "display_name": user.display_name if user is not None else None,
        "group_ids": list(member.group_ids),
    }


def _validate_group_ids_body(body: dict[str, Any], *, field: str = "group_ids") -> list[str]:
    raw = body.get(field)
    if not isinstance(raw, list) or not raw:
        raise ValidationError(
            f"{field} must be a non-empty array",
            errors=(_FieldError(field=field, code="required", message="must be non-empty array"),),
        )
    return _coerce_str_list(raw)


@api_bp.route("/projects/<project_id>/members", methods=["GET"])
@api_endpoint
def list_project_members_rest(project_id: str) -> tuple[Response, int] | Response:
    """List the platform members of a project + the available groups.

    Returns each member with their email + display name + ``group_ids``,
    plus an ``available_groups`` array (id + name) so a UI can render
    a group picker without a second round-trip.
    """
    require_global_permission("project_members", "read")
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")

    members: list[dict[str, Any]] = []
    for member in project.members:
        user = get_db().get_app_user(member.user_id)
        members.append(_serialize_project_member(member, user=user))

    available_groups = [
        {"id": g.id, "name": g.name, "is_system": g.is_system}
        for g in get_db().get_all_groups()
    ]
    return jsonify({"members": members, "available_groups": available_groups})


@api_bp.route("/projects/<project_id>/members", methods=["POST"])
@api_endpoint
def add_project_member_rest(project_id: str) -> tuple[Response, int]:
    """Add a member to a project."""
    require_global_permission("project_members", "create")
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")

    body = _require_dict_body()
    user_id_raw = body.get("user_id")
    if not isinstance(user_id_raw, str) or not user_id_raw.strip():
        raise ValidationError(
            "user_id is required",
            errors=(_FieldError(field="user_id", code="required", message="required"),),
        )
    user_id = user_id_raw.strip()

    user = get_db().get_app_user(user_id)
    if user is None:
        raise NotFoundError(f"user {user_id} not found")

    if any(m.user_id == user_id for m in project.members):
        raise ConflictError(f"user {user_id} is already a member of project {project_id}")

    group_ids = _validate_group_ids_body(body)
    get_db().add_project_member(project_id, user_id, group_ids)

    refreshed = get_db().get_project(project_id)
    if refreshed is None:
        raise ConflictError("project disappeared after member-add")
    member = next((m for m in refreshed.members if m.user_id == user_id), None)
    if member is None:
        raise ConflictError("member-add did not persist")
    response = jsonify(_serialize_project_member(member, user=user))
    response.headers["Location"] = f"/api/v1/projects/{project_id}/members/{user_id}"
    return response, 201


@api_bp.route("/projects/<project_id>/members/<user_id>", methods=["GET"])
@api_endpoint
def get_project_member_rest(project_id: str, user_id: str) -> tuple[Response, int] | Response:
    """Get one project member."""
    require_global_permission("project_members", "read")
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    member = next((m for m in project.members if m.user_id == user_id), None)
    if member is None:
        raise NotFoundError(f"user {user_id} is not a member of project {project_id}")
    user = get_db().get_app_user(user_id)
    return jsonify(_serialize_project_member(member, user=user))


@api_bp.route("/projects/<project_id>/members/<user_id>", methods=["PUT"])
@api_endpoint
def update_project_member_rest(
    project_id: str, user_id: str
) -> tuple[Response, int] | Response:
    """Replace a member's group assignments."""
    require_global_permission("project_members", "update")
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    if not any(m.user_id == user_id for m in project.members):
        raise NotFoundError(f"user {user_id} is not a member of project {project_id}")

    body = _require_dict_body()
    group_ids = _validate_group_ids_body(body)
    if not get_db().update_project_member_groups(project_id, user_id, group_ids):
        raise ConflictError("member group update did not modify any document")

    refreshed = get_db().get_project(project_id)
    if refreshed is None:
        raise ConflictError("project disappeared after member-update")
    member = next((m for m in refreshed.members if m.user_id == user_id), None)
    if member is None:
        raise ConflictError("member disappeared after update")
    user = get_db().get_app_user(user_id)
    return jsonify(_serialize_project_member(member, user=user))


@api_bp.route("/projects/<project_id>/members/<user_id>", methods=["DELETE"])
@api_endpoint
def remove_project_member_rest(
    project_id: str, user_id: str
) -> tuple[Response, int]:
    """Remove a member from a project. Self-removal is rejected (400)."""
    require_global_permission("project_members", "delete")
    if user_id == str(current_user.get_id()):
        raise ValidationError(
            "cannot remove yourself from a project",
            errors=(_FieldError(field="user_id", code="self_removal", message="self-removal is not allowed"),),
        )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    if not any(m.user_id == user_id for m in project.members):
        raise NotFoundError(f"user {user_id} is not a member of project {project_id}")
    get_db().remove_project_member(project_id, user_id)
    return Response(status=204), 204


# ---------------------------------------------------------------------------
# Test users — login automation credentials for sites under test
# ---------------------------------------------------------------------------


def _build_project_test_user_from_body(project_id: str, body: dict[str, Any]) -> ProjectUser:
    from auto_a11y.models.project_user import LoginConfig as ProjectLoginConfig

    username_raw = body.get("username")
    if not isinstance(username_raw, str) or not username_raw.strip():
        raise ValidationError(
            "username is required",
            errors=(_FieldError(field="username", code="required", message="required"),),
        )
    password_raw = body.get("password")
    if not isinstance(password_raw, str) or not password_raw:
        raise ValidationError(
            "password is required on create",
            errors=(_FieldError(field="password", code="required", message="required"),),
        )
    login_config_dict = (
        _parse_login_config_dict(body["login_config"], field="login_config")
        if "login_config" in body and body["login_config"] is not None
        else None
    )
    return ProjectUser(
        project_id=project_id,
        username=username_raw.strip(),
        password=password_raw,
        display_name=_optional_str(body.get("display_name"), field="display_name"),
        roles=_coerce_str_list(body["roles"]) if isinstance(body.get("roles"), list) else [],
        description=_optional_str(body.get("description"), field="description"),
        login_config=ProjectLoginConfig.from_dict(login_config_dict) if login_config_dict else ProjectLoginConfig(),
        enabled=bool(body.get("enabled", True)),
    )


def _build_website_test_user_from_body(website_id: str, body: dict[str, Any]) -> WebsiteUser:
    from auto_a11y.models.website_user import LoginConfig as WebsiteLoginConfig

    username_raw = body.get("username")
    if not isinstance(username_raw, str) or not username_raw.strip():
        raise ValidationError(
            "username is required",
            errors=(_FieldError(field="username", code="required", message="required"),),
        )
    password_raw = body.get("password")
    if not isinstance(password_raw, str) or not password_raw:
        raise ValidationError(
            "password is required on create",
            errors=(_FieldError(field="password", code="required", message="required"),),
        )
    login_config_dict = (
        _parse_login_config_dict(body["login_config"], field="login_config")
        if "login_config" in body and body["login_config"] is not None
        else None
    )
    return WebsiteUser(
        website_id=website_id,
        username=username_raw.strip(),
        password=password_raw,
        display_name=_optional_str(body.get("display_name"), field="display_name"),
        roles=_coerce_str_list(body["roles"]) if isinstance(body.get("roles"), list) else [],
        description=_optional_str(body.get("description"), field="description"),
        login_config=WebsiteLoginConfig.from_dict(login_config_dict) if login_config_dict else WebsiteLoginConfig(),
        enabled=bool(body.get("enabled", True)),
    )


def _optional_str(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(
            f"{field} must be a string or null",
            errors=(_FieldError(field=field, code="invalid_type", message="must be string"),),
        )
    return value if value else None


def _apply_patch_to_test_user(
    user: ProjectUser | WebsiteUser, body: dict[str, Any]
) -> None:
    """Apply a partial update to a test-user model in place.

    Same shape for ProjectUser and WebsiteUser — the only difference
    between them is the parent-id field, which is locked.

    A blank or omitted password preserves the existing value (mirrors
    the admin-settings rule). Setting password to a non-empty string
    rotates it.
    """
    if "username" in body:
        if not isinstance(body["username"], str) or not body["username"].strip():
            raise ValidationError(
                "username must be a non-empty string",
                errors=(_FieldError(field="username", code="invalid_value", message="must be non-empty"),),
            )
        user.username = body["username"].strip()
    if "password" in body:
        password = body["password"]
        if password is None or password == "":
            pass  # preserve existing
        elif not isinstance(password, str):
            raise ValidationError(
                "password must be a string",
                errors=(_FieldError(field="password", code="invalid_type", message="must be string"),),
            )
        else:
            user.password = password
    if "display_name" in body:
        user.display_name = _optional_str(body["display_name"], field="display_name")
    if "description" in body:
        user.description = _optional_str(body["description"], field="description")
    if "roles" in body:
        if not isinstance(body["roles"], list):
            raise ValidationError(
                "roles must be an array",
                errors=(_FieldError(field="roles", code="invalid_type", message="must be array"),),
            )
        user.roles = _coerce_str_list(body["roles"])
    if "login_config" in body:
        login_config_dict = (
            None
            if body["login_config"] is None
            else _parse_login_config_dict(body["login_config"], field="login_config")
        )
        # Each model carries its own LoginConfig class — reach into the
        # right one based on the runtime type.
        if isinstance(user, ProjectUser):
            from auto_a11y.models.project_user import LoginConfig as ProjectLoginConfig
            user.login_config = (
                ProjectLoginConfig.from_dict(login_config_dict) if login_config_dict
                else ProjectLoginConfig()
            )
        else:
            from auto_a11y.models.website_user import LoginConfig as WebsiteLoginConfig
            user.login_config = (
                WebsiteLoginConfig.from_dict(login_config_dict) if login_config_dict
                else WebsiteLoginConfig()
            )
    if "enabled" in body:
        user.enabled = bool(body["enabled"])
    user.update_timestamp()


# --- Project-scoped test users -------------------------------------------------


@api_bp.route("/projects/<project_id>/test-users", methods=["GET"])
@api_endpoint
def list_project_test_users_rest(project_id: str) -> tuple[Response, int] | Response:
    """List test users (login credentials) for a project."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=project_id
    )
    if get_db().get_project(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")
    users = get_db().get_project_users(project_id)
    return jsonify({"items": [_serialize_project_test_user(u) for u in users]})


@api_bp.route("/projects/<project_id>/test-users", methods=["POST"])
@api_endpoint
def create_project_test_user_rest(project_id: str) -> tuple[Response, int]:
    """Create a test user under a project."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    if get_db().get_project(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")

    body = _require_dict_body()
    user = _build_project_test_user_from_body(project_id, body)

    if get_db().get_project_user_by_username(project_id, user.username) is not None:
        raise ConflictError(f"username {user.username!r} is already in use in this project")

    user_id = get_db().create_project_user(user)
    refreshed = get_db().get_project_user(user_id)
    if refreshed is None:
        raise ConflictError("project test user failed to persist")
    response = jsonify(_serialize_project_test_user(refreshed))
    response.headers["Location"] = f"/api/v1/project-test-users/{user_id}"
    return response, 201


@api_bp.route("/project-test-users/<user_id>", methods=["GET"])
@api_endpoint
def get_project_test_user_rest(user_id: str) -> tuple[Response, int] | Response:
    """Get one project test user."""
    user = get_db().get_project_user(user_id)
    if user is None:
        raise NotFoundError(f"project test user {user_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=user.project_id
    )
    return jsonify(_serialize_project_test_user(user))


@api_bp.route("/project-test-users/<user_id>", methods=["PUT"])
@api_endpoint
def replace_project_test_user_rest(user_id: str) -> tuple[Response, int] | Response:
    """Full replace of a project test user's editable fields.

    project_id and metadata (last_used, last_login_*) are preserved;
    blank password preserves the existing one.
    """
    existing = get_db().get_project_user(user_id)
    if existing is None:
        raise NotFoundError(f"project test user {user_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=existing.project_id
    )
    body = _require_dict_body()
    # PUT body needs a password unless the existing record has one we
    # can preserve (admin-settings rule).
    if "password" not in body or not isinstance(body.get("password"), str) or not body["password"]:
        body["password"] = existing.password
    replaced = _build_project_test_user_from_body(existing.project_id, body)
    if (
        replaced.username != existing.username
        and get_db().get_project_user_by_username(existing.project_id, replaced.username) is not None
    ):
        raise ConflictError(f"username {replaced.username!r} is already in use in this project")
    replaced.mongo_id = existing.mongo_id
    replaced.created_at = existing.created_at
    replaced.last_used = existing.last_used
    replaced.last_login_success = existing.last_login_success
    replaced.last_login_error = existing.last_login_error
    replaced.update_timestamp()
    if not get_db().update_project_user(replaced):
        raise ConflictError("project test user could not be updated")
    return jsonify(_serialize_project_test_user(replaced))


@api_bp.route("/project-test-users/<user_id>", methods=["PATCH"])
@api_endpoint
def patch_project_test_user_rest(user_id: str) -> tuple[Response, int] | Response:
    """Partial update — covers the legacy enable/disable toggle."""
    user = get_db().get_project_user(user_id)
    if user is None:
        raise NotFoundError(f"project test user {user_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=user.project_id
    )
    body = _require_dict_body()
    if "username" in body and isinstance(body["username"], str):
        new_username = body["username"].strip()
        if new_username != user.username:
            if get_db().get_project_user_by_username(user.project_id, new_username) is not None:
                raise ConflictError(f"username {new_username!r} is already in use in this project")
    _apply_patch_to_test_user(user, body)
    if not get_db().update_project_user(user):
        raise ConflictError("project test user could not be updated")
    return jsonify(_serialize_project_test_user(user))


@api_bp.route("/project-test-users/<user_id>", methods=["DELETE"])
@api_endpoint
def delete_project_test_user_rest(user_id: str) -> tuple[Response, int]:
    """Delete a project test user."""
    user = get_db().get_project_user(user_id)
    if user is None:
        raise NotFoundError(f"project test user {user_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=user.project_id
    )
    get_db().delete_project_user(user_id)
    return Response(status=204), 204


# --- Website-scoped test users ------------------------------------------------


@api_bp.route("/websites/<website_id>/test-users", methods=["GET"])
@api_endpoint
def list_website_test_users_rest(website_id: str) -> tuple[Response, int] | Response:
    """List test users (login credentials) for a website."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, website_id=website_id
    )
    if get_db().get_website(website_id) is None:
        raise NotFoundError(f"website {website_id} not found")
    users = get_db().get_website_users(website_id)
    return jsonify({"items": [_serialize_website_test_user(u) for u in users]})


@api_bp.route("/websites/<website_id>/test-users", methods=["POST"])
@api_endpoint
def create_website_test_user_rest(website_id: str) -> tuple[Response, int]:
    """Create a test user under a website."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=website_id
    )
    if get_db().get_website(website_id) is None:
        raise NotFoundError(f"website {website_id} not found")

    body = _require_dict_body()
    user = _build_website_test_user_from_body(website_id, body)

    if get_db().get_website_user_by_username(website_id, user.username) is not None:
        raise ConflictError(f"username {user.username!r} is already in use on this website")

    user_id = get_db().create_website_user(user)
    refreshed = get_db().get_website_user(user_id)
    if refreshed is None:
        raise ConflictError("website test user failed to persist")
    response = jsonify(_serialize_website_test_user(refreshed))
    response.headers["Location"] = f"/api/v1/website-test-users/{user_id}"
    return response, 201


@api_bp.route("/website-test-users/<user_id>", methods=["GET"])
@api_endpoint
def get_website_test_user_rest(user_id: str) -> tuple[Response, int] | Response:
    """Get one website test user."""
    user = get_db().get_website_user(user_id)
    if user is None:
        raise NotFoundError(f"website test user {user_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, website_id=user.website_id
    )
    return jsonify(_serialize_website_test_user(user))


@api_bp.route("/website-test-users/<user_id>", methods=["PUT"])
@api_endpoint
def replace_website_test_user_rest(user_id: str) -> tuple[Response, int] | Response:
    """Full replace of a website test user's editable fields."""
    existing = get_db().get_website_user(user_id)
    if existing is None:
        raise NotFoundError(f"website test user {user_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=existing.website_id
    )
    body = _require_dict_body()
    if "password" not in body or not isinstance(body.get("password"), str) or not body["password"]:
        body["password"] = existing.password
    replaced = _build_website_test_user_from_body(existing.website_id, body)
    if (
        replaced.username != existing.username
        and get_db().get_website_user_by_username(existing.website_id, replaced.username) is not None
    ):
        raise ConflictError(f"username {replaced.username!r} is already in use on this website")
    replaced.mongo_id = existing.mongo_id
    replaced.created_at = existing.created_at
    replaced.last_used = existing.last_used
    replaced.last_login_success = existing.last_login_success
    replaced.last_login_error = existing.last_login_error
    replaced.update_timestamp()
    if not get_db().update_website_user(replaced):
        raise ConflictError("website test user could not be updated")
    return jsonify(_serialize_website_test_user(replaced))


@api_bp.route("/website-test-users/<user_id>", methods=["PATCH"])
@api_endpoint
def patch_website_test_user_rest(user_id: str) -> tuple[Response, int] | Response:
    """Partial update of a website test user."""
    user = get_db().get_website_user(user_id)
    if user is None:
        raise NotFoundError(f"website test user {user_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=user.website_id
    )
    body = _require_dict_body()
    if "username" in body and isinstance(body["username"], str):
        new_username = body["username"].strip()
        if new_username != user.username:
            if get_db().get_website_user_by_username(user.website_id, new_username) is not None:
                raise ConflictError(f"username {new_username!r} is already in use on this website")
    _apply_patch_to_test_user(user, body)
    if not get_db().update_website_user(user):
        raise ConflictError("website test user could not be updated")
    return jsonify(_serialize_website_test_user(user))


@api_bp.route("/website-test-users/<user_id>", methods=["DELETE"])
@api_endpoint
def delete_website_test_user_rest(user_id: str) -> tuple[Response, int]:
    """Delete a website test user."""
    user = get_db().get_website_user(user_id)
    if user is None:
        raise NotFoundError(f"website test user {user_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=user.website_id
    )
    get_db().delete_website_user(user_id)
    return Response(status=204), 204


# ---------------------------------------------------------------------------
# Project participants (lived-experience testers + test supervisors).
#
# Closes the last open piece of docs/REST_API_ROADMAP.md §5.11. Both
# resources live as inline arrays on the Project document
# (project.lived_experience_testers, project.test_supervisors), with
# string UUID ids assigned on insert via ``ensure_id()``. The Project
# helpers (add_tester, get_tester, update_tester, remove_tester and
# their supervisor mirrors) are the system of record; we lift them
# into REST and persist by replacing the whole project document.
#
# The legacy project_participants_bp HTML routes still serve the admin
# UI under /projects/<id>/participants/...
# ---------------------------------------------------------------------------

from auto_a11y.models.project import LivedExperienceTester, TestSupervisor  # noqa: E402


def _serialize_tester(tester: LivedExperienceTester) -> dict[str, Any]:
    return {
        "id": tester.id,
        "name": tester.name,
        "email": tester.email,
        "disability_type": tester.disability_type,
        "assistive_tech": list(tester.assistive_tech),
        "notes": tester.notes,
    }


def _serialize_supervisor(supervisor: TestSupervisor) -> dict[str, Any]:
    return {
        "id": supervisor.id,
        "name": supervisor.name,
        "email": supervisor.email,
        "role": supervisor.role,
        "organization": supervisor.organization,
        "notes": supervisor.notes,
    }


def _validate_required_name(body: dict[str, Any]) -> str:
    name_raw = body.get("name")
    if not isinstance(name_raw, str) or not name_raw.strip():
        raise ValidationError(
            "name is required",
            errors=(_FieldError(field="name", code="required", message="required"),),
        )
    return name_raw.strip()


def _build_tester_from_body(body: dict[str, Any]) -> LivedExperienceTester:
    name = _validate_required_name(body)
    assistive_tech_raw: Any = body.get("assistive_tech", [])
    if not isinstance(assistive_tech_raw, list):
        raise ValidationError(
            "assistive_tech must be an array",
            errors=(_FieldError(field="assistive_tech", code="invalid_type", message="must be array"),),
        )
    return LivedExperienceTester(
        name=name,
        email=_optional_str(body.get("email"), field="email"),
        disability_type=_optional_str(body.get("disability_type"), field="disability_type"),
        assistive_tech=_coerce_str_list(assistive_tech_raw),
        notes=_optional_str(body.get("notes"), field="notes"),
    )


def _build_supervisor_from_body(body: dict[str, Any]) -> TestSupervisor:
    name = _validate_required_name(body)
    return TestSupervisor(
        name=name,
        email=_optional_str(body.get("email"), field="email"),
        role=_optional_str(body.get("role"), field="role"),
        organization=_optional_str(body.get("organization"), field="organization"),
        notes=_optional_str(body.get("notes"), field="notes"),
    )


def _apply_patch_to_tester(
    tester: LivedExperienceTester, body: dict[str, Any]
) -> None:
    if "name" in body:
        if not isinstance(body["name"], str) or not body["name"].strip():
            raise ValidationError(
                "name must be a non-empty string",
                errors=(_FieldError(field="name", code="invalid_value", message="must be non-empty"),),
            )
        tester.name = body["name"].strip()
    if "email" in body:
        tester.email = _optional_str(body["email"], field="email")
    if "disability_type" in body:
        tester.disability_type = _optional_str(body["disability_type"], field="disability_type")
    if "assistive_tech" in body:
        if not isinstance(body["assistive_tech"], list):
            raise ValidationError(
                "assistive_tech must be an array",
                errors=(_FieldError(field="assistive_tech", code="invalid_type", message="must be array"),),
            )
        tester.assistive_tech = _coerce_str_list(body["assistive_tech"])
    if "notes" in body:
        tester.notes = _optional_str(body["notes"], field="notes")


def _apply_patch_to_supervisor(
    supervisor: TestSupervisor, body: dict[str, Any]
) -> None:
    if "name" in body:
        if not isinstance(body["name"], str) or not body["name"].strip():
            raise ValidationError(
                "name must be a non-empty string",
                errors=(_FieldError(field="name", code="invalid_value", message="must be non-empty"),),
            )
        supervisor.name = body["name"].strip()
    if "email" in body:
        supervisor.email = _optional_str(body["email"], field="email")
    if "role" in body:
        supervisor.role = _optional_str(body["role"], field="role")
    if "organization" in body:
        supervisor.organization = _optional_str(body["organization"], field="organization")
    if "notes" in body:
        supervisor.notes = _optional_str(body["notes"], field="notes")


# --- Testers ------------------------------------------------------------------


@api_bp.route("/projects/<project_id>/testers", methods=["GET"])
@api_endpoint
def list_testers_rest(project_id: str) -> tuple[Response, int] | Response:
    """List lived-experience testers for a project."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=project_id
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    return jsonify(
        {"items": [_serialize_tester(t) for t in project.lived_experience_testers]}
    )


@api_bp.route("/projects/<project_id>/testers", methods=["POST"])
@api_endpoint
def create_tester_rest(project_id: str) -> tuple[Response, int]:
    """Create a lived-experience tester on a project."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")

    body = _require_dict_body()
    tester = _build_tester_from_body(body)
    project.add_tester(tester)
    if not get_db().update_project(project):
        raise ConflictError("project document could not be updated")

    response = jsonify(_serialize_tester(tester))
    response.headers["Location"] = (
        f"/api/v1/projects/{project_id}/testers/{tester.id}"
    )
    return response, 201


@api_bp.route("/projects/<project_id>/testers/<tester_id>", methods=["GET"])
@api_endpoint
def get_tester_rest(
    project_id: str, tester_id: str
) -> tuple[Response, int] | Response:
    """Get one lived-experience tester."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=project_id
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    tester = project.get_tester(tester_id)
    if tester is None:
        raise NotFoundError(f"tester {tester_id} not found in project {project_id}")
    return jsonify(_serialize_tester(tester))


@api_bp.route("/projects/<project_id>/testers/<tester_id>", methods=["PUT"])
@api_endpoint
def replace_tester_rest(
    project_id: str, tester_id: str
) -> tuple[Response, int] | Response:
    """Full replace of a tester's editable fields. The id is preserved."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    existing = project.get_tester(tester_id)
    if existing is None:
        raise NotFoundError(f"tester {tester_id} not found in project {project_id}")

    body = _require_dict_body()
    replaced = _build_tester_from_body(body)
    replaced.mongo_id = tester_id
    if not project.update_tester(replaced):
        raise ConflictError("tester could not be updated")
    if not get_db().update_project(project):
        raise ConflictError("project document could not be updated")
    return jsonify(_serialize_tester(replaced))


@api_bp.route("/projects/<project_id>/testers/<tester_id>", methods=["PATCH"])
@api_endpoint
def patch_tester_rest(
    project_id: str, tester_id: str
) -> tuple[Response, int] | Response:
    """Partial update of a tester."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    tester = project.get_tester(tester_id)
    if tester is None:
        raise NotFoundError(f"tester {tester_id} not found in project {project_id}")

    body = _require_dict_body()
    _apply_patch_to_tester(tester, body)
    if not project.update_tester(tester):
        raise ConflictError("tester could not be updated")
    if not get_db().update_project(project):
        raise ConflictError("project document could not be updated")
    return jsonify(_serialize_tester(tester))


@api_bp.route("/projects/<project_id>/testers/<tester_id>", methods=["DELETE"])
@api_endpoint
def delete_tester_rest(project_id: str, tester_id: str) -> tuple[Response, int]:
    """Delete a lived-experience tester from a project."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    if not project.remove_tester(tester_id):
        raise NotFoundError(f"tester {tester_id} not found in project {project_id}")
    if not get_db().update_project(project):
        raise ConflictError("project document could not be updated")
    return Response(status=204), 204


# --- Supervisors --------------------------------------------------------------


@api_bp.route("/projects/<project_id>/supervisors", methods=["GET"])
@api_endpoint
def list_supervisors_rest(project_id: str) -> tuple[Response, int] | Response:
    """List test supervisors for a project."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=project_id
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    return jsonify(
        {"items": [_serialize_supervisor(s) for s in project.test_supervisors]}
    )


@api_bp.route("/projects/<project_id>/supervisors", methods=["POST"])
@api_endpoint
def create_supervisor_rest(project_id: str) -> tuple[Response, int]:
    """Create a test supervisor on a project."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")

    body = _require_dict_body()
    supervisor = _build_supervisor_from_body(body)
    project.add_supervisor(supervisor)
    if not get_db().update_project(project):
        raise ConflictError("project document could not be updated")

    response = jsonify(_serialize_supervisor(supervisor))
    response.headers["Location"] = (
        f"/api/v1/projects/{project_id}/supervisors/{supervisor.id}"
    )
    return response, 201


@api_bp.route("/projects/<project_id>/supervisors/<supervisor_id>", methods=["GET"])
@api_endpoint
def get_supervisor_rest(
    project_id: str, supervisor_id: str
) -> tuple[Response, int] | Response:
    """Get one test supervisor."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=project_id
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    supervisor = project.get_supervisor(supervisor_id)
    if supervisor is None:
        raise NotFoundError(
            f"supervisor {supervisor_id} not found in project {project_id}"
        )
    return jsonify(_serialize_supervisor(supervisor))


@api_bp.route("/projects/<project_id>/supervisors/<supervisor_id>", methods=["PUT"])
@api_endpoint
def replace_supervisor_rest(
    project_id: str, supervisor_id: str
) -> tuple[Response, int] | Response:
    """Full replace of a supervisor's editable fields. The id is preserved."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    existing = project.get_supervisor(supervisor_id)
    if existing is None:
        raise NotFoundError(
            f"supervisor {supervisor_id} not found in project {project_id}"
        )

    body = _require_dict_body()
    replaced = _build_supervisor_from_body(body)
    replaced.mongo_id = supervisor_id
    if not project.update_supervisor(replaced):
        raise ConflictError("supervisor could not be updated")
    if not get_db().update_project(project):
        raise ConflictError("project document could not be updated")
    return jsonify(_serialize_supervisor(replaced))


@api_bp.route("/projects/<project_id>/supervisors/<supervisor_id>", methods=["PATCH"])
@api_endpoint
def patch_supervisor_rest(
    project_id: str, supervisor_id: str
) -> tuple[Response, int] | Response:
    """Partial update of a supervisor."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    supervisor = project.get_supervisor(supervisor_id)
    if supervisor is None:
        raise NotFoundError(
            f"supervisor {supervisor_id} not found in project {project_id}"
        )

    body = _require_dict_body()
    _apply_patch_to_supervisor(supervisor, body)
    if not project.update_supervisor(supervisor):
        raise ConflictError("supervisor could not be updated")
    if not get_db().update_project(project):
        raise ConflictError("project document could not be updated")
    return jsonify(_serialize_supervisor(supervisor))


@api_bp.route("/projects/<project_id>/supervisors/<supervisor_id>", methods=["DELETE"])
@api_endpoint
def delete_supervisor_rest(
    project_id: str, supervisor_id: str
) -> tuple[Response, int]:
    """Delete a test supervisor from a project."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    if not project.remove_supervisor(supervisor_id):
        raise NotFoundError(
            f"supervisor {supervisor_id} not found in project {project_id}"
        )
    if not get_db().update_project(project):
        raise ConflictError("project document could not be updated")
    return Response(status=204), 204


# ---------------------------------------------------------------------------
# Discovered pages (REST shape — uses the @api_endpoint scaffolding).
#
# Per docs/REST_API_ROADMAP.md §5.3. A "discovered page" is a key page
# or screen flagged for manual inspection / lived-experience testing
# (typically the 20-25 most interesting pages in an audit). They live
# in their own ``discovered_pages`` Mongo collection — separate from
# the regular Page model — and carry taxonomy tags (``interested_because``,
# ``page_elements``) plus public/private notes that flow into Drupal
# audit-report nodes.
#
# The legacy discovered_pages_bp HTML routes still serve the admin UI;
# the REST endpoints add the standard /api/v1 surface with cursor
# pagination and Problem-Details errors.
# ---------------------------------------------------------------------------

from auto_a11y.models.discovered_page import DiscoveredPage  # noqa: E402


def _serialize_discovered_page(page: DiscoveredPage) -> dict[str, Any]:
    """Project a :class:`DiscoveredPage` to a JSON-safe dict.

    Datetimes go to ISO 8601 and the Mongo ``_id`` is dropped (the public
    identifier is the string ``id`` property). The Drupal-sync fields
    are exposed read-only — they're managed by the Drupal sync subsystem
    rather than by REST clients.
    """

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    return {
        "id": page.id,
        "title": page.title,
        "url": page.url,
        "project_id": page.project_id,
        "source_type": page.source_type,
        "source_page_id": page.source_page_id,
        "source_website_id": page.source_website_id,
        "source_component_signature": page.source_component_signature,
        "source_upload_id": page.source_upload_id,
        "interested_because": list(page.interested_because),
        "page_elements": list(page.page_elements),
        "private_notes": page.private_notes,
        "public_notes": page.public_notes,
        "include_in_report": page.include_in_report,
        "audited": page.audited,
        "manual_audit": page.manual_audit,
        "screenshot_paths": list(page.screenshot_paths),
        "document_links": list(page.document_links),
        "drupal_uuid": page.drupal_uuid,
        "drupal_sync_status": page.drupal_sync_status.value,
        "drupal_last_synced": _iso(page.drupal_last_synced),
        "drupal_error_message": page.drupal_error_message,
        "created_at": _iso(page.created_at),
        "updated_at": _iso(page.updated_at),
        "created_by": page.created_by,
    }


def _validate_document_links(raw: Any, *, field: str) -> list[dict[str, Any]]:
    """``document_links`` is a list of objects; validate the shape minimally."""
    if not isinstance(raw, list):
        raise ValidationError(
            f"{field} must be an array",
            errors=(_FieldError(field=field, code="invalid_type", message="must be array"),),
        )
    items: list[dict[str, Any]] = []
    for index, item in enumerate(_iter_to_any_list(raw)):
        if not isinstance(item, dict):
            raise ValidationError(
                f"{field}[{index}] must be an object",
                errors=(_FieldError(field=f"{field}[{index}]", code="invalid_type", message="must be object"),),
            )
        items.append(cast(dict[str, Any], item))
    return items


def _build_discovered_page_from_body(
    project_id: str, body: dict[str, Any]
) -> DiscoveredPage:
    title_raw = body.get("title")
    if not isinstance(title_raw, str) or not title_raw.strip():
        raise ValidationError(
            "title is required",
            errors=(_FieldError(field="title", code="required", message="required"),),
        )
    url_raw = body.get("url")
    if not isinstance(url_raw, str) or not url_raw.strip():
        raise ValidationError(
            "url is required",
            errors=(_FieldError(field="url", code="required", message="required"),),
        )
    interested_raw: Any = body.get("interested_because", [])
    if not isinstance(interested_raw, list):
        raise ValidationError(
            "interested_because must be an array",
            errors=(_FieldError(field="interested_because", code="invalid_type", message="must be array"),),
        )
    elements_raw: Any = body.get("page_elements", [])
    if not isinstance(elements_raw, list):
        raise ValidationError(
            "page_elements must be an array",
            errors=(_FieldError(field="page_elements", code="invalid_type", message="must be array"),),
        )
    screenshots_raw: Any = body.get("screenshot_paths", [])
    if not isinstance(screenshots_raw, list):
        raise ValidationError(
            "screenshot_paths must be an array",
            errors=(_FieldError(field="screenshot_paths", code="invalid_type", message="must be array"),),
        )
    document_links_raw: Any = body.get("document_links", [])
    document_links = _validate_document_links(document_links_raw, field="document_links")

    return DiscoveredPage(
        title=title_raw.strip(),
        url=url_raw.strip(),
        project_id=project_id,
        source_type=str(body.get("source_type", "manual")),
        interested_because=_coerce_str_list(interested_raw),
        page_elements=_coerce_str_list(elements_raw),
        private_notes=_optional_str(body.get("private_notes"), field="private_notes"),
        public_notes=_optional_str(body.get("public_notes"), field="public_notes"),
        include_in_report=bool(body.get("include_in_report", True)),
        audited=bool(body.get("audited", False)),
        manual_audit=bool(body.get("manual_audit", False)),
        screenshot_paths=_coerce_str_list(screenshots_raw),
        document_links=document_links,
        created_by=str(current_user.get_id()) if current_user.is_authenticated else None,
    )


def _apply_patch_to_discovered_page(
    page: DiscoveredPage, body: dict[str, Any]
) -> None:
    if "title" in body:
        if not isinstance(body["title"], str) or not body["title"].strip():
            raise ValidationError(
                "title must be a non-empty string",
                errors=(_FieldError(field="title", code="invalid_value", message="must be non-empty"),),
            )
        page.title = body["title"].strip()
    if "url" in body:
        if not isinstance(body["url"], str) or not body["url"].strip():
            raise ValidationError(
                "url must be a non-empty string",
                errors=(_FieldError(field="url", code="invalid_value", message="must be non-empty"),),
            )
        page.url = body["url"].strip()
    if "interested_because" in body:
        if not isinstance(body["interested_because"], list):
            raise ValidationError(
                "interested_because must be an array",
                errors=(_FieldError(field="interested_because", code="invalid_type", message="must be array"),),
            )
        page.interested_because = _coerce_str_list(body["interested_because"])
    if "page_elements" in body:
        if not isinstance(body["page_elements"], list):
            raise ValidationError(
                "page_elements must be an array",
                errors=(_FieldError(field="page_elements", code="invalid_type", message="must be array"),),
            )
        page.page_elements = _coerce_str_list(body["page_elements"])
    if "private_notes" in body:
        page.private_notes = _optional_str(body["private_notes"], field="private_notes")
    if "public_notes" in body:
        page.public_notes = _optional_str(body["public_notes"], field="public_notes")
    if "include_in_report" in body:
        page.include_in_report = bool(body["include_in_report"])
    if "audited" in body:
        page.audited = bool(body["audited"])
    if "manual_audit" in body:
        page.manual_audit = bool(body["manual_audit"])
    if "screenshot_paths" in body:
        if not isinstance(body["screenshot_paths"], list):
            raise ValidationError(
                "screenshot_paths must be an array",
                errors=(_FieldError(field="screenshot_paths", code="invalid_type", message="must be array"),),
            )
        page.screenshot_paths = _coerce_str_list(body["screenshot_paths"])
    if "document_links" in body:
        page.document_links = _validate_document_links(
            body["document_links"], field="document_links"
        )
    page.updated_at = datetime.now()


@api_bp.route("/projects/<project_id>/discovered-pages", methods=["GET"])
@api_endpoint
def list_discovered_pages_rest(
    project_id: str,
) -> tuple[Response, int] | Response:
    """List discovered pages within a project, with cursor pagination."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=project_id
    )
    if get_db().get_project(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")

    limit = parse_limit(request.args.get("limit"))
    cursor_raw = request.args.get("cursor")
    cursor = _Cursor.decode(cursor_raw) if cursor_raw else None

    query: dict[str, Any] = {"project_id": project_id}
    if cursor is not None:
        from bson import ObjectId
        try:
            query["_id"] = {"$lt": ObjectId(cursor.last_id)}
        except Exception as exc:
            raise ValidationError(
                "cursor.last_id is not a valid ObjectId",
                errors=(_FieldError(field="cursor.last_id", code="invalid_format", message=str(exc)),),
            ) from exc

    docs = list(get_db().discovered_pages.find(query).sort("_id", -1).limit(limit + 1))
    pages = [DiscoveredPage.from_dict(doc) for doc in docs]
    page = paginate(
        pages, limit=limit, get_id=lambda p: str(p.mongo_id) if p.mongo_id else ""
    )
    return jsonify(
        {
            "items": [_serialize_discovered_page(p) for p in page["items"]],
            "next_cursor": page["next_cursor"],
        }
    )


@api_bp.route("/projects/<project_id>/discovered-pages", methods=["POST"])
@api_endpoint
def create_discovered_page_rest(project_id: str) -> tuple[Response, int]:
    """Create a discovered page on a project."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    if get_db().get_project(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")

    body = _require_dict_body()
    page = _build_discovered_page_from_body(project_id, body)
    new_id = get_db().create_discovered_page(page)
    refreshed = get_db().get_discovered_page_by_id(new_id)
    if refreshed is None:
        raise ConflictError("discovered page failed to persist")
    response = jsonify(_serialize_discovered_page(refreshed))
    response.headers["Location"] = f"/api/v1/discovered-pages/{new_id}"
    return response, 201


@api_bp.route("/discovered-pages/<page_id>", methods=["GET"])
@api_endpoint
def get_discovered_page_rest(page_id: str) -> tuple[Response, int] | Response:
    """Get a discovered page by id."""
    page = get_db().get_discovered_page_by_id(page_id)
    if page is None:
        raise NotFoundError(f"discovered page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=page.project_id
    )
    return jsonify(_serialize_discovered_page(page))


@api_bp.route("/discovered-pages/<page_id>", methods=["PUT"])
@api_endpoint
def replace_discovered_page_rest(
    page_id: str,
) -> tuple[Response, int] | Response:
    """Full replace of a discovered page's editable fields.

    Server-managed fields (project_id, source_*, drupal_*, created_at,
    created_by) are preserved across PUT — clients cannot reassign a
    discovered page to a different project, change its source, or
    rewrite Drupal-sync state through this endpoint.
    """
    existing = get_db().get_discovered_page_by_id(page_id)
    if existing is None:
        raise NotFoundError(f"discovered page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=existing.project_id
    )
    body = _require_dict_body()
    replaced = _build_discovered_page_from_body(existing.project_id, body)
    replaced.mongo_id = existing.mongo_id
    replaced.created_at = existing.created_at
    replaced.created_by = existing.created_by
    replaced.source_type = existing.source_type
    replaced.source_page_id = existing.source_page_id
    replaced.source_website_id = existing.source_website_id
    replaced.source_component_signature = existing.source_component_signature
    replaced.source_upload_id = existing.source_upload_id
    replaced.drupal_uuid = existing.drupal_uuid
    replaced.drupal_sync_status = existing.drupal_sync_status
    replaced.drupal_last_synced = existing.drupal_last_synced
    replaced.drupal_error_message = existing.drupal_error_message
    replaced.updated_at = datetime.now()
    if not get_db().update_discovered_page(replaced):
        raise ConflictError("discovered page could not be updated")
    return jsonify(_serialize_discovered_page(replaced))


@api_bp.route("/discovered-pages/<page_id>", methods=["PATCH"])
@api_endpoint
def patch_discovered_page_rest(
    page_id: str,
) -> tuple[Response, int] | Response:
    """Partial update — covers the legacy edit form's per-field updates."""
    page = get_db().get_discovered_page_by_id(page_id)
    if page is None:
        raise NotFoundError(f"discovered page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=page.project_id
    )
    body = _require_dict_body()
    _apply_patch_to_discovered_page(page, body)
    if not get_db().update_discovered_page(page):
        raise ConflictError("discovered page could not be updated")
    return jsonify(_serialize_discovered_page(page))


@api_bp.route("/discovered-pages/<page_id>", methods=["DELETE"])
@api_endpoint
def delete_discovered_page_rest(page_id: str) -> tuple[Response, int]:
    """Delete a discovered page."""
    page = get_db().get_discovered_page_by_id(page_id)
    if page is None:
        raise NotFoundError(f"discovered page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=page.project_id
    )
    get_db().delete_discovered_page(page_id)
    return Response(status=204), 204