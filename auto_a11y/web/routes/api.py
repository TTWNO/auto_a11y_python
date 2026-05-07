"""
RESTful API routes
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from flask import Blueprint, Response, jsonify, request
from flask_login import current_user
from auto_a11y.models import Project, Page, ProjectStatus, PageStatus
from auto_a11y.models.app_user import UserRole
from auto_a11y.pdf.storage import PdfStorage
from auto_a11y.web.routes.auth import project_role_required
from auto_a11y.core.job_manager import JobManager, JobStatus
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
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR)
def test_page(page_id: str) -> tuple[Response, int]:
    """Run test on page"""
    page = get_db().get_page(page_id)
    if not page:
        return jsonify({'error': 'Page not found'}), 404
    
    data: dict[str, Any] = request.get_json() or {}
    _config: dict[str, Any] = data.get('config', {})

    # Queue test job
    job_id = f'test_{page_id}_{datetime.now().timestamp()}'
    
    # Update page status
    page.status = PageStatus.QUEUED
    get_db().update_page(page)
    
    return jsonify({
        'job_id': job_id,
        'status': 'queued',
        'message': 'Test job queued successfully'
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
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR)
def discover_pages(website_id: str) -> tuple[Response, int]:
    """Start page discovery for website"""
    website = get_db().get_website(website_id)
    if not website:
        return jsonify({'error': 'Website not found'}), 404
    
    data: dict[str, Any] = request.get_json() or {}
    _strategy: str = data.get('strategy', 'crawl')
    _config: dict[str, Any] = data.get('config', {})

    # Queue discovery job
    job_id = f'discovery_{website_id}_{datetime.now().timestamp()}'
    
    return jsonify({
        'job_id': job_id,
        'status': 'started',
        'message': 'Page discovery started'
    }), 202


@api_bp.route('/websites/<website_id>/test', methods=['POST'])
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR)
def test_website(website_id: str) -> tuple[Response, int]:
    """Run tests on all pages in website"""
    website = get_db().get_website(website_id)
    if not website:
        return jsonify({'error': 'Website not found'}), 404
    
    data: dict[str, Any] = request.get_json() or {}
    page_ids: list[str] | str = data.get('page_ids', 'all')
    _config: dict[str, Any] = data.get('config', {})
    
    if page_ids == 'all':
        pages = get_db().get_pages(website_id)
    else:
        pages_raw = [get_db().get_page(pid) for pid in page_ids]
        pages = [p for p in pages_raw if p is not None]
    
    if not pages:
        return jsonify({'error': 'No pages to test'}), 400
    
    # Queue batch test job
    job_id = f'batch_test_{website_id}_{datetime.now().timestamp()}'
    
    return jsonify({
        'job_id': job_id,
        'status': 'queued',
        'pages_queued': len(pages),
        'message': f'Batch testing queued for {len(pages)} pages'
    }), 202


# Reports API

@api_bp.route('/projects/<project_id>/reports', methods=['POST'])
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT)
def generate_report(project_id: str) -> tuple[Response, int]:
    """Generate report for project"""
    project = get_db().get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    
    data: dict[str, Any] = request.get_json() or {}
    _format_type: str = data.get('format', 'xlsx')
    _include: dict[str, Any] = data.get('include', {})
    _filters: dict[str, Any] = data.get('filters', {})
    
    # Queue report generation
    report_id = f'report_{project_id}_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
    
    return jsonify({
        'report_id': report_id,
        'status': 'generating',
        'message': 'Report generation started'
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