"""
Project management routes
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from werkzeug.wrappers import Response
from auto_a11y.web.api.deprecation import deprecated
from auto_a11y.web.fluent import ftl, lazy_ftl, get_current_locale as get_locale
from auto_a11y.web.typed_app import get_db, get_app_config, get_test_config
from flask_login import login_required
from flask import g
from auto_a11y.models import Project, ProjectStatus, ProjectType
from auto_a11y.models.page import PageStatus
from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus
from auto_a11y.models.app_user import UserRole

from auto_a11y.core.issue_aggregator import count_website_issues
from auto_a11y.pdf.storage import PdfStorage


def summarise_pdf_status(pdfs: list[PdfDocument]) -> dict[str, int]:
    """Bucket a list of PdfDocuments by audit status for the nav-card counter.

    Returns a dict with the keys ``total`` (== ``len(pdfs)``) and one entry
    per :class:`PdfDocumentStatus` value (snake-cased). Templates render
    "X of Y audited" by reading ``audited`` and ``total``; the per-status
    breakdown is available for richer summaries.
    """
    counts: dict[str, int] = {"total": len(pdfs)}
    for status in PdfDocumentStatus:
        counts[status.value] = 0
    for pdf in pdfs:
        counts[pdf.status.value] = counts.get(pdf.status.value, 0) + 1
    return counts
from auto_a11y.web.routes.auth import auditor_required, project_role_required
from auto_a11y.core.job_manager import JobManager, JobType, JobStatus
from auto_a11y.core.task_runner import task_runner
from auto_a11y.core.report_job import ReportJob
from flask_login import current_user
from uuid import uuid4
import logging

logger = logging.getLogger(__name__)
projects_bp = Blueprint('projects', __name__)


@projects_bp.route('/api/list')
@login_required
@deprecated(successor="/api/v1/projects", sunset="2026-09-01")
def api_list_projects() -> Response | tuple[Response, int]:
    """API endpoint to list projects the current user can access"""
    try:
        if getattr(current_user, 'is_superadmin', False):
            projects = get_db().get_all_projects()
        else:
            projects = get_db().get_projects_for_user(str(current_user.get_id()))
        return jsonify({
            'success': True,
            'projects': [
                {
                    'id': p.id,
                    'name': p.name,
                    'description': p.description
                } for p in projects
            ]
        })
    except Exception as e:
        logger.error(f"Error listing projects: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@projects_bp.route('/api/<project_id>/websites')
@login_required
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT)
@deprecated(successor="/api/v1/projects/<project_id>/websites", sunset="2026-09-01")
def api_project_websites(project_id: str) -> Response | tuple[Response, int]:
    """API endpoint to list websites in a project"""
    try:
        websites = get_db().get_websites(project_id)
        return jsonify({
            'success': True,
            'websites': [
                {
                    'id': w.id,
                    'name': w.name,
                    'url': w.url
                } for w in websites
            ]
        })
    except Exception as e:
        logger.error(f"Error listing project websites: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@projects_bp.route('/api/test-details/<test_id>')
@deprecated(successor="/api/v1/issues/<code>", sunset="2026-09-01")
def api_test_details(test_id: str) -> Response | tuple[Response, int]:
    """API endpoint to get detailed information about a test"""
    try:
        from auto_a11y.reporting.issue_catalog import IssueCatalog
        from auto_a11y.reporting.issue_descriptions_translated import get_detailed_issue_description

        # Get test details from the issue catalog
        test_info = IssueCatalog.get_issue(test_id)

        if not test_info:
            return jsonify({
                'success': False,
                'error': ftl('projects-test-test_id-not-found-in-catalog', test_id=test_id)
            }), 404

        # Get production_ready status from database
        doc_status = get_db().get_issue_documentation_status(test_id)
        production_ready = doc_status.get('production_ready', False) if doc_status else False

        # Get enhanced description with message templates
        enhanced_info = get_detailed_issue_description(test_id)

        # Build response with both catalog and enhanced info
        response_data = {
            'id': test_info.get('id', test_id),
            'type': test_info.get('type', 'Unknown'),
            'impact': test_info.get('impact', 'Unknown'),
            'wcag': test_info.get('wcag', []),
            'wcag_full': test_info.get('wcag_full', ''),
            'category': test_info.get('category', ''),
            'description': test_info.get('description', 'No description available'),
            'why_it_matters': test_info.get('why_it_matters', ''),
            'who_it_affects': test_info.get('who_it_affects', ''),
            'how_to_fix': test_info.get('how_to_fix', ''),
            'production_ready': production_ready
        }

        # Add message template info if available
        if enhanced_info:
            response_data['message_template'] = {
                'title': enhanced_info.get('title', ''),
                'what': enhanced_info.get('what', ''),
                'why': enhanced_info.get('why', ''),
                'remediation': enhanced_info.get('remediation', '')
            }

        return jsonify({
            'success': True,
            'test': response_data
        })
    except Exception as e:
        logger.error(f"Error getting test details for {test_id}: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@projects_bp.route('/api/test-details/<test_id>/production-ready', methods=['POST'])
@deprecated(successor="/api/v1/issues/<code>", sunset="2026-09-01")
def api_set_test_production_ready(test_id: str) -> Response | tuple[Response, int]:
    """API endpoint to toggle production_ready flag for a test"""
    try:
        from auto_a11y.reporting.issue_catalog import IssueCatalog

        # Verify test exists in catalog
        test_info = IssueCatalog.get_issue(test_id)
        if not test_info:
            return jsonify({
                'success': False,
                'error': ftl('projects-test-test_id-not-found-in-catalog', test_id=test_id)
            }), 404

        # Get the new value from request
        data = request.get_json()
        production_ready = data.get('production_ready', False)

        # Update in database
        success = get_db().set_issue_production_ready(
            test_id,
            production_ready,
            updated_by="web_user"
        )

        if success:
            return jsonify({
                'success': True,
                'message': ftl('projects-production-ready-status-updated-for-test_id', test_id=test_id),
                'production_ready': production_ready
            })
        else:
            return jsonify({
                'success': False,
                'error': ftl('projects-failed-to-update-production-ready-status')
            }), 500

    except Exception as e:
        logger.error(f"Error setting production ready for {test_id}: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@projects_bp.route('/api/issue-documentation-stats')
@deprecated(sunset="2026-09-01")  # successor TBD — pending #51 (/api/v1/issues/documentation-stats)
def api_issue_documentation_stats() -> Response | tuple[Response, int]:
    """API endpoint to get statistics about issue documentation status"""
    try:
        from auto_a11y.reporting.issue_catalog import IssueCatalog

        # Get all issue codes from catalog
        all_codes = list(IssueCatalog.ISSUES.keys())

        # Get production ready statuses from database
        statuses = get_db().get_all_issue_documentation_statuses()

        # Calculate stats
        production_ready_count = sum(1 for ready in statuses.values() if ready)
        total_count = len(all_codes)
        pending_count = total_count - production_ready_count

        # Get lists
        production_ready_codes = [code for code, ready in statuses.items() if ready]
        pending_codes = [code for code in all_codes if not statuses.get(code, False)]

        return jsonify({
            'success': True,
            'stats': {
                'total': total_count,
                'production_ready': production_ready_count,
                'pending': pending_count,
                'percentage_ready': round(production_ready_count * 100 / total_count, 1) if total_count > 0 else 0
            },
            'production_ready_codes': production_ready_codes,
            'pending_codes': pending_codes
        })

    except Exception as e:
        logger.error(f"Error getting documentation stats: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@projects_bp.route('/')
@login_required
def list_projects() -> str:
    """List all projects"""
    status_filter = request.args.get('status')
    
    if getattr(current_user, 'is_superadmin', False):
        if status_filter:
            status = ProjectStatus(status_filter)
            projects = get_db().get_projects(status=status)
        else:
            projects = get_db().get_projects()
    else:
        projects = get_db().get_projects_for_user(str(current_user.get_id()))
        if status_filter:
            status = ProjectStatus(status_filter)
            projects = [p for p in projects if p.status == status]
    
    return render_template('projects/list.html', projects=projects)


@projects_bp.route('/create', methods=['GET', 'POST'])
@auditor_required
def create_project() -> str | Response:
    """Create new project"""
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description', '')
        wcag_level = request.form.get('wcag_level', 'AA')
        project_type_value = request.form.get('project_type', 'website')

        # Parse project type
        try:
            project_type = ProjectType(project_type_value)
        except ValueError:
            project_type = ProjectType.WEBSITE

        # Get type-specific fields
        app_identifier = request.form.get('app_identifier', '').strip() or None
        device_model = request.form.get('device_model', '').strip() or None
        location = request.form.get('location', '').strip() or None
        drupal_audit_name = request.form.get('drupal_audit_name', '').strip() or None

        if not name:
            flash(ftl('projects-project-name-is-required'), 'error')
            # Redirect to GET handler which will populate everything
            return redirect(url_for('projects.create_project'))

        # Check if project name exists
        existing = get_db().projects.find_one({'name': name})
        if existing:
            flash(ftl('projects-project-name-already-exists', name=name), 'error')
            # Redirect to GET handler which will populate everything
            return redirect(url_for('projects.create_project'))
        
        # Get touchpoint configuration
        from auto_a11y.config.touchpoint_tests import TOUCHPOINT_TEST_MAPPING
        
        touchpoints_config = {}
        touchpoint_ids = [
            'headings', 'images', 'forms', 'buttons', 'links', 'navigation',
            'colors_contrast', 'keyboard_navigation', 'landmarks', 'language',
            'tables', 'lists', 'media', 'dialogs', 'animation', 'timing',
            'fonts', 'semantic_structure', 'aria', 'focus_management',
            'reading_order', 'event_handling', 'accessible_names', 'page',
            'title_attributes'
        ]
        
        for touchpoint_id in touchpoint_ids:
            enabled = request.form.get(f'touchpoint_{touchpoint_id}') == 'on'
            
            # Get individual test configurations for this touchpoint
            tests_config = {}
            test_ids = TOUCHPOINT_TEST_MAPPING.get(touchpoint_id, [])
            
            for test_id in test_ids:
                # Check if individual test checkbox exists and is checked
                test_enabled = request.form.get(f'test_{touchpoint_id}_{test_id}') == 'on'
                tests_config[test_id] = test_enabled
            
            touchpoints_config[touchpoint_id] = {
                'enabled': enabled,
                'tests': tests_config
            }
        
        # Get AI testing configuration
        enable_ai_testing = request.form.get('enable_ai_testing') == 'on'
        ai_tests: list[str] = []
        if enable_ai_testing:
            # Collect selected AI tests
            for test_name in ['headings', 'reading_order', 'modals', 'language', 'animations', 'interactive']:
                if request.form.get(f'ai_test_{test_name}'):
                    ai_tests.append(test_name)

        # Get stealth mode configuration
        stealth_mode = request.form.get('stealth_mode') == 'true'

        # Get page load strategy
        page_load_strategy = request.form.get('page_load_strategy', 'networkidle2')

        # Get headless browser setting
        headless_browser = request.form.get('headless_browser', 'default')

        # Create project with WCAG level, touchpoints, AI config, and stealth mode
        project = Project(
            name=name,
            description=description,
            status=ProjectStatus.ACTIVE,
            project_type=project_type,
            app_identifier=app_identifier,
            device_model=device_model,
            location=location,
            drupal_audit_name=drupal_audit_name,
            config={
                'wcag_level': wcag_level,
                'page_load_strategy': page_load_strategy,
                'headless_browser': headless_browser,
                'touchpoints': touchpoints_config,
                'enable_ai_testing': enable_ai_testing,
                'ai_tests': ai_tests,
                'stealth_mode': stealth_mode
            }
        )
        
        project_id = get_db().create_project(project)
        # Auto-add creator as member with Admin group
        admin_group = get_db().get_group_by_name('Admin')
        if admin_group and admin_group.id:
            get_db().add_project_member(project_id, str(current_user.get_id()), [admin_group.id])
        flash(ftl('projects-project-name-created-successfully', name=name), 'success')
        
        return redirect(url_for('projects.view_project', project_id=project_id))
    
    # Get fixture test status for all tests
    test_statuses: dict[str, Any] = {}
    passing_tests: set[str] = set()

    if hasattr(current_app, 'test_config') and get_test_config():
        test_statuses = get_test_config().get_all_test_statuses()
        if get_test_config().fixture_validator:
            passing_tests = get_test_config().fixture_validator.get_passing_tests()
        debug_mode = get_test_config().debug_mode
    else:
        debug_mode = get_app_config().DEBUG

    # Group tests by touchpoint dynamically
    from collections import defaultdict
    tests_by_touchpoint: defaultdict[str, list[str]] = defaultdict(list)

    # Map fixture directory names to UI touchpoint names
    touchpoint_mapping = {
        'ARIA': 'aria',
        'AccessibleNames': 'accessible_names',
        'Animation': 'animation',
        'Animations': 'animation',
        'Buttons': 'buttons',
        'Colors': 'colors_contrast',
        'ColorsAndContrast': 'colors_contrast',
        'Contrast': 'colors_contrast',
        'DialogsAndModals': 'dialogs',
        'Modals': 'dialogs',
        'Documents': 'documents',
        'EventHandling': 'event_handling',
        'Focus': 'focus_management',
        'Fonts': 'fonts',
        'Forms': 'forms',
        'Headings': 'headings',
        'IFrames': 'iframes',
        'Images': 'images',
        'SVG': 'images',
        'Interactive': 'aria',
        'Keyboard': 'keyboard_navigation',
        'Tabindex': 'keyboard_navigation',
        'Landmarks': 'landmarks',
        'Language': 'language',
        'Links': 'links',
        'Lists': 'lists',
        'Maps': 'maps',
        'Media': 'media',
        'Video': 'media',
        'Audio': 'media',
        'Navigation': 'navigation',
        'ReadingOrder': 'reading_order',
        'Semantic': 'semantic_structure',
        'SemanticStructure': 'semantic_structure',
        'Structure': 'semantic_structure',
        'DocumentType': 'semantic_structure',
        'Page': 'page',
        'PageTitle': 'page',
        'Tables': 'tables',
        'Timing': 'timing',
        'TitleAttributes': 'title_attributes',
        'Typography': 'fonts',
        'Style': 'styles',
        'Styles': 'styles'
    }

    # Touchpoint display names (ordered)
    touchpoint_names = {
        'accessible_names': lazy_ftl('testing-accessible-names'),
        'animation': lazy_ftl('testing-animation'),
        'aria': lazy_ftl('testing-aria'),
        'buttons': lazy_ftl('testing-buttons'),
        'colors_contrast': lazy_ftl('testing-colors-contrast'),
        'dialogs': lazy_ftl('testing-dialogs-modals'),
        'documents': lazy_ftl('common-documents'),
        'event_handling': lazy_ftl('testing-event-handling'),
        'focus_management': lazy_ftl('testing-focus-management'),
        'forms': lazy_ftl('common-forms'),
        'headings': lazy_ftl('testing-headings'),
        'iframes': lazy_ftl('testing-iframes'),
        'styles': lazy_ftl('testing-inline-styles'),
        'images': lazy_ftl('testing-images'),
        'keyboard_navigation': lazy_ftl('testing-keyboard-navigation'),
        'landmarks': lazy_ftl('testing-landmarks'),
        'language': lazy_ftl('common-language'),
        'links': lazy_ftl('testing-links'),
        'lists': lazy_ftl('testing-lists'),
        'maps': lazy_ftl('testing-maps'),
        'media': lazy_ftl('testing-media'),
        'navigation': lazy_ftl('testing-navigation'),
        'page': lazy_ftl('common-page-2'),
        'reading_order': lazy_ftl('common-reading-order'),
        'semantic_structure': lazy_ftl('testing-semantic-structure'),
        'tables': lazy_ftl('testing-tables'),
        'timing': lazy_ftl('testing-timing'),
        'title_attributes': lazy_ftl('testing-title-attributes'),
        'fonts': lazy_ftl('testing-fonts'),
        'other': lazy_ftl('common-other')
    }

    # Group all tests by touchpoint
    for err_code, status_val in test_statuses.items():
        # Try to determine touchpoint from fixture paths
        fixture_paths: list[str] = status_val.get('fixture_paths', [])
        if fixture_paths:
            # Get directory from first fixture path
            first_path: str = fixture_paths[0]
            if '/' in first_path:
                directory: str = first_path.split('/')[0]
                touchpoint = touchpoint_mapping.get(directory, 'other')
            else:
                touchpoint = 'other'
        else:
            touchpoint = 'other'

        tests_by_touchpoint[touchpoint].append(err_code)

    # Sort tests within each touchpoint
    for touchpoint in tests_by_touchpoint:
        tests_by_touchpoint[touchpoint].sort()

    # Get production ready statuses for all tests
    production_ready_statuses = get_db().get_all_issue_documentation_statuses()

    # DEBUG: Log what we're passing to template
    logger.warning(f"DEBUG: Create Project - tests_by_touchpoint keys: {sorted(tests_by_touchpoint.keys())}")
    if 'links' in tests_by_touchpoint:
        logger.warning(f"DEBUG: Links touchpoint has {len(tests_by_touchpoint['links'])} tests")
    else:
        logger.warning(f"DEBUG: 'links' key NOT in tests_by_touchpoint!")
    logger.warning(f"DEBUG: Total passing_tests: {len(passing_tests)}")
    logger.warning(f"DEBUG: Total test_statuses: {len(test_statuses)}")

    return render_template('projects/create.html',
                         test_statuses=test_statuses,
                         passing_tests=passing_tests,
                         debug_mode=debug_mode,
                         tests_by_touchpoint=dict(tests_by_touchpoint),
                         touchpoint_names=touchpoint_names,
                         production_ready_statuses=production_ready_statuses)


@projects_bp.route('/<project_id>')
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT)
def view_project(project_id: str) -> str | Response:
    """View project details"""
    project = get_db().get_project(project_id)
    if not project:
        flash(ftl('common-project-not-found'), 'error')
        return redirect(url_for('projects.list_projects'))

    websites = get_db().get_websites(project_id)

    # PDF rollup prep (2026-05-01 spec Part 2). One DB call for the
    # whole project; reuse the result for both the per-website
    # violation/warning rollup AND the existing PDF nav badge count.
    project_pdfs = get_db().get_pdf_documents(project_id=project_id, limit=10000)
    pdf_count = len(project_pdfs)
    pdf_status_counts = summarise_pdf_status(project_pdfs)
    storage = PdfStorage(base_dir=Path(get_app_config().PDF_STORAGE_DIR))
    pdfs_by_website: dict[str, list[PdfDocument]] = {}
    for pdf_doc in project_pdfs:
        pdfs_by_website.setdefault(pdf_doc.website_id, []).append(pdf_doc)

    # Pass storage so the project-level totals include audited PDFs and
    # match the per-website badges below. count_website_issues is the
    # single source of truth for both numbers; new test sources added
    # there flow into both the overview and the badges automatically.
    stats = get_db().get_project_stats(project_id, pdf_storage=storage)

    # Calculate stats for each website (violations, warnings, and actual page count)
    website_stats: dict[str | None, dict[str, int]] = {}
    for website in websites:
        if not website.id:
            continue
        pages = get_db().get_pages(website.id)
        tested_page_ids = [p.id for p in pages if p.status == PageStatus.TESTED]
        counts = count_website_issues(
            get_db(),
            storage,
            website.id,
            tested_page_ids=tested_page_ids,
            pdfs=pdfs_by_website.get(website.id, []),
        )
        website_stats[website.id] = {
            'violations': counts.violations,
            'warnings': counts.warnings,
        }
        # Use actual page count from DB rather than the cached counter
        website.page_count = len(pages)

    # Get available test users for this project (for discovery modal)
    project_users = get_db().get_project_users(project_id, enabled_only=True)

    # Get recordings for this project
    recordings = get_db().get_recordings(project_id=project_id)

    # Get discovered pages for this project
    discovered_pages_cursor = get_db().discovered_pages.find({'project_id': project_id}).sort('created_at', -1)
    discovered_pages = list(discovered_pages_cursor)

    is_project_admin = getattr(current_user, 'is_superadmin', False) or (
        hasattr(g, 'effective_role') and g.effective_role == UserRole.ADMIN
    )

    all_groups = get_db().get_all_groups()

    return render_template('projects/view.html',
                         project=project,
                         websites=websites,
                         stats=stats,
                         website_stats=website_stats,
                         project_users=project_users,
                         recordings=recordings,
                         discovered_pages=discovered_pages,
                         is_project_admin=is_project_admin,
                         all_groups=all_groups,
                         pdf_count=pdf_count,
                         pdf_status_counts=pdf_status_counts)


@projects_bp.route('/<project_id>/edit', methods=['GET', 'POST'])
def edit_project(project_id: str) -> str | Response:
    """Edit project"""
    project = get_db().get_project(project_id)
    if not project:
        flash(ftl('common-project-not-found'), 'error')
        return redirect(url_for('projects.list_projects'))

    # Import TOUCHPOINT_TEST_MAPPING for rendering the form
    from auto_a11y.config.touchpoint_tests import TOUCHPOINT_TEST_MAPPING

    # Build touchpoint data structure for the template
    touchpoint_names = {
        'headings': lazy_ftl('testing-headings'),
        'images': lazy_ftl('testing-images'),
        'forms': lazy_ftl('common-forms'),
        'buttons': lazy_ftl('testing-buttons'),
        'links': lazy_ftl('testing-links'),
        'navigation': lazy_ftl('testing-navigation'),
        'colors_contrast': lazy_ftl('testing-colors-contrast'),
        'keyboard_navigation': lazy_ftl('testing-keyboard-navigation'),
        'landmarks': lazy_ftl('testing-landmarks'),
        'language': lazy_ftl('common-language'),
        'tables': lazy_ftl('testing-tables'),
        'lists': lazy_ftl('testing-lists'),
        'media': lazy_ftl('testing-media'),
        'dialogs': lazy_ftl('testing-dialogs-modals'),
        'animation': lazy_ftl('testing-animation'),
        'timing': lazy_ftl('testing-timing'),
        'fonts': lazy_ftl('testing-fonts'),
        'semantic_structure': lazy_ftl('testing-semantic-structure'),
        'aria': lazy_ftl('testing-aria'),
        'focus_management': lazy_ftl('testing-focus-management'),
        'reading_order': lazy_ftl('common-reading-order'),
        'event_handling': lazy_ftl('testing-event-handling'),
        'accessible_names': lazy_ftl('testing-accessible-names'),
        'page': lazy_ftl('common-page-2'),
        'documents': lazy_ftl('common-documents'),
        'maps': lazy_ftl('testing-maps'),
        'styles': lazy_ftl('testing-inline-styles'),
        'iframes': lazy_ftl('testing-iframes')
    }

    if request.method == 'POST':
        project.name = request.form.get('name', project.name)
        project.description = request.form.get('description', project.description)
        status = request.form.get('status', project.status.value)
        project.status = ProjectStatus(status)

        # Update Drupal audit name
        drupal_audit_name = request.form.get('drupal_audit_name', '').strip() or None
        project.drupal_audit_name = drupal_audit_name

        # Update WCAG level in config
        wcag_level = request.form.get('wcag_level', 'AA')
        if not project.config:
            project.config = {}
        project.config['wcag_level'] = wcag_level

        # Update page load strategy in config
        page_load_strategy = request.form.get('page_load_strategy', 'networkidle2')
        project.config['page_load_strategy'] = page_load_strategy

        # Update headless browser setting in config
        headless_browser = request.form.get('headless_browser', 'default')
        project.config['headless_browser'] = headless_browser

        # Update page title length limit
        try:
            title_length_limit = int(request.form.get('title_length_limit', 60))
            # Validate range
            if 30 <= title_length_limit <= 120:
                project.config['titleLengthLimit'] = title_length_limit
            else:
                project.config['titleLengthLimit'] = 60
        except (ValueError, TypeError):
            project.config['titleLengthLimit'] = 60

        # Update heading length limit
        try:
            heading_length_limit = int(request.form.get('heading_length_limit', 60))
            # Validate range
            if 30 <= heading_length_limit <= 120:
                project.config['headingLengthLimit'] = heading_length_limit
            else:
                project.config['headingLengthLimit'] = 60
        except (ValueError, TypeError):
            project.config['headingLengthLimit'] = 60

        # Update touchpoint configuration
        from auto_a11y.config.touchpoint_tests import TOUCHPOINT_TEST_MAPPING
        
        touchpoints_config = {}
        touchpoint_ids = [
            'headings', 'images', 'forms', 'buttons', 'links', 'navigation',
            'colors_contrast', 'keyboard_navigation', 'landmarks', 'language',
            'tables', 'lists', 'media', 'dialogs', 'animation', 'timing',
            'fonts', 'semantic_structure', 'aria', 'focus_management',
            'reading_order', 'event_handling', 'accessible_names', 'page',
            'title_attributes'
        ]
        
        for touchpoint_id in touchpoint_ids:
            enabled = request.form.get(f'touchpoint_{touchpoint_id}') == 'on'
            
            # Get individual test configurations for this touchpoint
            tests_config = {}
            test_ids = TOUCHPOINT_TEST_MAPPING.get(touchpoint_id, [])
            
            for test_id in test_ids:
                # Check if individual test checkbox exists and is checked
                test_enabled = request.form.get(f'test_{touchpoint_id}_{test_id}') == 'on'
                tests_config[test_id] = test_enabled
            
            touchpoints_config[touchpoint_id] = {
                'enabled': enabled,
                'tests': tests_config
            }
        
        project.config['touchpoints'] = touchpoints_config
        
        # Update AI testing configuration
        enable_ai_testing = request.form.get('enable_ai_testing') == 'on'
        project.config['enable_ai_testing'] = enable_ai_testing

        ai_tests: list[str] = []
        if enable_ai_testing:
            # Collect selected AI tests
            for test_name in ['headings', 'reading_order', 'modals', 'language', 'animations', 'interactive']:
                if request.form.get(f'ai_test_{test_name}'):
                    ai_tests.append(test_name)
        project.config['ai_tests'] = ai_tests

        # Update stealth mode configuration
        stealth_mode = request.form.get('stealth_mode') == 'true'
        project.config['stealth_mode'] = stealth_mode

        # Update font accessibility configuration
        use_default_fonts = request.form.get('use_default_fonts') == 'on'
        additional_fonts_raw = request.form.get('additional_inaccessible_fonts', '').strip()
        excluded_fonts_raw = request.form.get('excluded_fonts', '').strip()

        # Parse textarea input (one font per line)
        additional_fonts = [f.strip().lower() for f in additional_fonts_raw.split('\n') if f.strip()]
        excluded_fonts = [f.strip().lower() for f in excluded_fonts_raw.split('\n') if f.strip()]

        project.config['font_accessibility'] = {
            'use_defaults': use_default_fonts,
            'additional_inaccessible_fonts': additional_fonts,
            'excluded_fonts': excluded_fonts
        }

        if get_db().update_project(project):
            flash(ftl('projects-project-updated-successfully'), 'success')
            return redirect(url_for('projects.view_project', project_id=project_id))
        else:
            flash(ftl('projects-failed-to-update-project'), 'error')

    return render_template('projects/edit.html',
                         project=project,
                         touchpoint_names=touchpoint_names,
                         touchpoint_test_mapping=TOUCHPOINT_TEST_MAPPING)


@projects_bp.route('/<project_id>/delete', methods=['POST'])
def delete_project(project_id: str) -> Response:
    """Delete project"""
    project = get_db().get_project(project_id)
    if not project:
        flash(ftl('common-project-not-found'), 'error')
        return redirect(url_for('projects.list_projects'))

    if get_db().delete_project(project_id):
        flash(ftl('projects-project-name-deleted-successfully', name=project.name), 'success')
    else:
        flash(ftl('projects-failed-to-delete-project'), 'error')
    
    return redirect(url_for('projects.list_projects'))


@projects_bp.route('/<project_id>/add-website', methods=['POST'])
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR)
def add_website(project_id: str) -> Response | tuple[Response, int]:
    """Add website to project"""
    from auto_a11y.models import Website, ScrapingConfig
    
    project = get_db().get_project(project_id)
    if not project:
        return jsonify({'error': ftl('common-project-not-found')}), 404

    url = request.form.get('url')
    name = request.form.get('name', '')

    if not url:
        return jsonify({'error': ftl('common-url-is-required')}), 400
    
    # Create website
    website = Website(
        project_id=project_id,
        url=url,
        name=name,
        scraping_config=ScrapingConfig()
    )
    
    website_id = get_db().create_website(website)
    
    # Check if this is an AJAX request
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({
            'success': True,
            'website_id': website_id,
            'redirect': url_for('websites.view_website', website_id=website_id)
        })
    else:
        # Regular form submission - redirect directly
        flash(ftl('projects-website-name-added-successfully', name=name or url), 'success')
        return redirect(url_for('websites.view_website', website_id=website_id))


@projects_bp.route('/<project_id>/test-all', methods=['POST'])
def test_project(project_id: str) -> Response:
    """Test all websites in a project"""
    import asyncio
    from auto_a11y.core.website_manager import WebsiteManager
    
    project = get_db().get_project(project_id)
    if not project:
        flash(ftl('common-project-not-found'), 'error')
        return redirect(url_for('projects.list_projects'))

    # Get test parameters
    take_screenshot = request.form.get('take_screenshot', 'true') == 'true'
    run_ai = request.form.get('run_ai', 'false') == 'true'
    test_all = request.form.get('test_all', 'true') == 'true'
    
    try:
        # Create browser config with project-specific stealth_mode setting
        browser_config = get_app_config().__dict__.copy()
        if project and project.config:
            browser_config['stealth_mode'] = project.config.get('stealth_mode', False)
        else:
            browser_config['stealth_mode'] = False

        # Initialize website manager
        manager = WebsiteManager(get_db(), browser_config)
        logger.info(f"Created website manager for project {project_id} testing (stealth_mode: {browser_config.get('stealth_mode', False)})")
        
        # Generate unique job ID
        import uuid
        job_id = f"proj_{project_id}_{uuid.uuid4().hex[:8]}"
        
        # Run project-level testing
        # Try to get the running loop, or create a new one
        try:
            loop = asyncio.get_running_loop()
            logger.info("Using existing event loop for project testing")
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            logger.info("Created new event loop for project testing")
        
        try:
            jobs = loop.run_until_complete(manager.test_project(
                project_id=project_id,
                job_id=job_id,
                test_all=test_all,
                take_screenshot=take_screenshot,
                run_ai_analysis=run_ai
            ))
        finally:
            # Don't close the loop immediately if it's running
            try:
                if not loop.is_running():
                    loop.close()
                    logger.info("Closed project testing event loop")
            except:
                pass
        
        if jobs:
            flash(ftl('projects-started-testing-count-websites-in-project-name', count=len(jobs), name=project.name), 'success')
        else:
            flash(ftl('projects-no-websites-found-to-test-in-this-project'), 'warning')
        
    except Exception as e:
        logger.error(f"Failed to start project testing: {e}")
        flash(ftl('projects-failed-to-start-testing-error', error=str(e)), 'error')
    
    return redirect(url_for('projects.view_project', project_id=project_id))


@projects_bp.route('/<project_id>/report', methods=['GET', 'POST'])
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT)
def generate_project_report(project_id: str) -> Response:
    """Generate accessibility report for entire project (background job)"""
    from auto_a11y.reporting.project_report import ProjectReport

    project = get_db().get_project(project_id)
    if not project:
        flash(ftl('common-project-not-found'), 'error')
        return redirect(url_for('projects.list_projects'))

    format = request.args.get('format', 'html')

    # Capture all data in route handler
    db = get_db()
    app = getattr(current_app, '_get_current_object')()
    reports_dir = str(get_app_config().REPORTS_DIR)
    websites = db.get_websites(project_id)
    pages_by_website: dict[str, list[Any]] = {}
    for website in websites:
        if not website.id:
            continue
        pages_by_website[website.id] = db.get_pages(website.id)

    language = str(get_locale()) if get_locale() else 'en'
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
            with app.app_context():
                def generate_and_save(progress_callback: Callable[[int, int, str], None] | None = None) -> object:
                    report = ProjectReport(db, project, websites, pages_by_website, language=language)
                    report.generate(progress_callback=progress_callback)
                    return report.save(format, reports_dir=reports_dir)
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


@projects_bp.route('/api/<project_id>/users')
@deprecated(successor="/api/v1/projects/<project_id>/users", sunset="2026-09-01")
def api_get_project_users(project_id: str) -> Response | tuple[Response, int]:
    """API endpoint to get project users"""
    try:
        project_users = get_db().get_project_users(project_id, enabled_only=True)

        return jsonify({
            'success': True,
            'users': [
                {
                    'id': user.id,
                    'username': user.username,
                    'display_name': user.display_name,
                    'roles': user.roles
                } for user in project_users
            ]
        })
    except Exception as e:
        logger.error(f"Error fetching project users: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@projects_bp.route('/api/<project_id>/details')
@deprecated(successor="/api/v1/projects/<project_id>", sunset="2026-09-01")
def api_get_project(project_id: str) -> Response | tuple[Response, int]:
    """API endpoint to get project details including testers and supervisors"""
    try:
        project = get_db().get_project(project_id)
        if not project:
            return jsonify({'success': False, 'error': ftl('common-project-not-found')}), 404

        return jsonify({
            'success': True,
            'project': {
                'id': project.id,
                'name': project.name,
                'description': project.description,
                'project_type': project.project_type.value,
                'lived_experience_testers': [t.to_dict() for t in project.lived_experience_testers],
                'test_supervisors': [s.to_dict() for s in project.test_supervisors]
            }
        })
    except Exception as e:
        logger.error(f"Error fetching project: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@projects_bp.route('/api/<project_id>/discovered-pages')
@deprecated(successor="/api/v1/projects/<project_id>/discovered-pages", sunset="2026-09-01")
def api_get_discovered_pages(project_id: str) -> Response | tuple[Response, int]:
    """API endpoint to get discovered pages for a project"""
    try:
        db = get_db()

        # Query discovered pages for this project
        discovered_pages_docs = db.discovered_pages.find({'project_id': project_id})

        # Convert to list of dicts
        pages: list[dict[str, Any]] = []
        for doc in discovered_pages_docs:
            pages.append({
                '_id': str(doc['_id']),
                'title': doc.get('title', ''),
                'url': doc.get('url', ''),
                'interested_because': doc.get('interested_because', []),
                'page_elements': doc.get('page_elements', [])
            })

        return jsonify({
            'success': True,
            'discovered_pages': pages
        })
    except Exception as e:
        logger.error(f"Error fetching discovered pages: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500