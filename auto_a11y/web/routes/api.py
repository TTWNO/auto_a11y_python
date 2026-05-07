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

@api_bp.route('/projects/<project_id>/websites', methods=['GET'])
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT)
def get_websites(project_id: str) -> tuple[Response, int] | Response:
    """Get websites for project"""
    project = get_db().get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    
    websites = get_db().get_websites(project_id)
    
    return jsonify({
        'websites': [w.to_dict() for w in websites]
    })


@api_bp.route('/projects/<project_id>/websites', methods=['POST'])
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR)
def add_website(project_id: str) -> tuple[Response, int]:
    """Add website to project"""
    project = get_db().get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    
    data = request.get_json()
    
    if not data or 'url' not in data:
        return jsonify({'error': 'Website URL is required'}), 400
    
    from auto_a11y.models import Website, ScrapingConfig
    
    website = Website(
        project_id=project_id,
        url=data['url'],
        name=data.get('name'),
        scraping_config=ScrapingConfig(**data.get('scraping_config', {}))
    )
    
    website_id = get_db().create_website(website)
    
    return jsonify({
        'id': website_id,
        'message': 'Website added successfully'
    }), 201


@api_bp.route('/websites/<website_id>', methods=['GET'])
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT)
def get_website(website_id: str) -> tuple[Response, int] | Response:
    """Get website by ID"""
    website = get_db().get_website(website_id)
    if not website:
        return jsonify({'error': 'Website not found'}), 404
    
    return jsonify(website.to_dict())


@api_bp.route('/websites/<website_id>', methods=['DELETE'])
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR)
def delete_website(website_id: str) -> tuple[Response, int]:
    """Delete website"""
    website = get_db().get_website(website_id)
    if not website:
        return jsonify({'error': 'Website not found'}), 404
    
    if get_db().delete_website(website_id):
        return jsonify({'message': 'Website deleted successfully'}), 204
    else:
        return jsonify({'error': 'Failed to delete website'}), 500


# Pages API

@api_bp.route('/websites/<website_id>/pages', methods=['GET'])
def get_pages(website_id: str) -> tuple[Response, int] | Response:
    """Get pages for website"""
    website = get_db().get_website(website_id)
    if not website:
        return jsonify({'error': 'Website not found'}), 404
    
    status = request.args.get('status')
    has_violations = request.args.get('has_violations')
    
    if status:
        try:
            status_enum = PageStatus(status)
            pages = get_db().get_pages(website_id, status=status_enum)
        except ValueError:
            return jsonify({'error': 'Invalid status value'}), 400
    else:
        pages = get_db().get_pages(website_id)
    
    if has_violations is not None:
        has_violations_bool = has_violations.lower() == 'true'
        pages = [p for p in pages if p.has_issues == has_violations_bool]
    
    return jsonify({
        'pages': [p.to_dict() for p in pages]
    })


@api_bp.route('/websites/<website_id>/pages', methods=['POST'])
def add_page(website_id: str) -> tuple[Response, int]:
    """Add page to website"""
    website = get_db().get_website(website_id)
    if not website:
        return jsonify({'error': 'Website not found'}), 404
    
    data = request.get_json()
    
    if not data or 'url' not in data:
        return jsonify({'error': 'Page URL is required'}), 400
    
    page = Page(
        website_id=website_id,
        url=data['url'],
        priority=data.get('priority', 'normal')
    )
    
    page_id = get_db().create_page(page)
    
    return jsonify({
        'id': page_id,
        'message': 'Page added successfully'
    }), 201


@api_bp.route('/pages/<page_id>', methods=['GET'])
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT)
def get_page(page_id: str) -> tuple[Response, int] | Response:
    """Get page by ID"""
    page = get_db().get_page(page_id)
    if not page:
        return jsonify({'error': 'Page not found'}), 404
    
    return jsonify(page.to_dict())


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