"""
Routes for managing test schedules (scheduled accessibility testing)
"""
from __future__ import annotations

from typing import Any

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from werkzeug.wrappers import Response
from auto_a11y.web.fluent import ftl
from auto_a11y.web.typed_app import get_db
from flask_login import current_user
from datetime import datetime
from zoneinfo import ZoneInfo
from auto_a11y.models import (
    TestSchedule, ScheduleType, AITestMode, ScheduleRunStatus,
    ScheduleTestConfig, PresetConfig
)
from auto_a11y.models.app_user import UserRole
from auto_a11y.web.routes.auth import project_role_required
from auto_a11y.core.scheduler import get_scheduler_service
import logging

logger = logging.getLogger(__name__)

schedules_bp = Blueprint('schedules', __name__)


@schedules_bp.route('/schedules')
def schedules_dashboard() -> str:
    """Global schedules dashboard - shows all schedules across all projects"""
    # Get filter parameters
    project_id = request.args.get('project_id')

    # Get all projects for filter dropdown
    projects = get_db().get_projects()

    # Get selected project
    selected_project = None
    if project_id:
        selected_project = get_db().get_project(project_id)

    # Get all schedules (optionally filtered by project)
    schedules = get_db().get_all_test_schedules(project_id=project_id)

    # Enrich schedules with website and project info
    for schedule in schedules:
        website = get_db().get_website(schedule.website_id)
        if website:
            setattr(schedule, '_website_name', website.name)
            setattr(schedule, '_website_id', website.id)
            project = get_db().get_project(website.project_id)
            if project:
                setattr(schedule, '_project_name', project.name)
                setattr(schedule, '_project_id', project.id)
            else:
                setattr(schedule, '_project_name', 'Unknown')
                setattr(schedule, '_project_id', None)
        else:
            setattr(schedule, '_website_name', 'Unknown')
            setattr(schedule, '_website_id', None)
            setattr(schedule, '_project_name', 'Unknown')
            setattr(schedule, '_project_id', None)

    # Calculate summary stats
    total_schedules = len(schedules)
    enabled_schedules = sum(1 for s in schedules if s.enabled)
    disabled_schedules = total_schedules - enabled_schedules

    # Count by schedule type
    type_counts: dict[str, int] = {}
    for s in schedules:
        type_name = s.schedule_type.value if hasattr(s.schedule_type, 'value') else str(s.schedule_type)
        type_counts[type_name] = type_counts.get(type_name, 0) + 1

    # Get upcoming runs (next 24 hours)
    scheduler = get_scheduler_service()
    upcoming_runs: list[dict[str, Any]] = []
    if scheduler:
        for schedule in schedules:
            if schedule.enabled and schedule.next_run_at:
                upcoming_runs.append({
                    'schedule': schedule,
                    'next_run': schedule.next_run_at
                })
    upcoming_runs.sort(key=lambda x: x.get('next_run') or datetime.max)
    upcoming_runs = upcoming_runs[:10]  # Limit to next 10

    return render_template('schedules/dashboard.html',
                         schedules=schedules,
                         projects=projects,
                         selected_project=selected_project,
                         total_schedules=total_schedules,
                         enabled_schedules=enabled_schedules,
                         disabled_schedules=disabled_schedules,
                         type_counts=type_counts,
                         upcoming_runs=upcoming_runs,
                         now=datetime.now())


@schedules_bp.route('/websites/<website_id>/schedules')
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR)
def list_schedules(website_id: str) -> str | Response:
    """List all schedules for a website"""
    website = get_db().get_website(website_id)
    if not website:
        flash(ftl('common-website-not-found'), 'error')
        return redirect(url_for('index'))

    project = get_db().get_project(website.project_id)

    # Get all schedules
    schedules = get_db().get_test_schedules_for_website(website_id)

    return render_template('schedules/list.html',
                         website=website,
                         project=project,
                         schedules=schedules)


@schedules_bp.route('/websites/<website_id>/schedules/create', methods=['GET', 'POST'])
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR)
def create_schedule(website_id: str) -> str | Response:
    """Create a new schedule"""
    website = get_db().get_website(website_id)
    if not website:
        flash(ftl('common-website-not-found'), 'error')
        return redirect(url_for('index'))

    project = get_db().get_project(website.project_id)

    if request.method == 'POST':
        try:
            data = request.form

            # Parse schedule type
            schedule_type_str = data.get('schedule_type', 'daily')
            schedule_type = ScheduleType(schedule_type_str)

            # Parse preset config
            preset_config = PresetConfig(
                time=data.get('time', '02:00'),
                day_of_week=int(data.get('day_of_week', 0)),
                day_of_month=int(data.get('day_of_month', 1)),
                timezone=data.get('timezone', 'America/Toronto')
            )

            # Parse scheduled datetime for one-time
            scheduled_datetime = None
            if schedule_type == ScheduleType.ONE_TIME:
                date_str = data.get('scheduled_date', '')
                time_str = data.get('scheduled_time', '02:00')
                if date_str:
                    datetime_str = f"{date_str} {time_str}"
                    scheduled_datetime = datetime.strptime(datetime_str, '%Y-%m-%d %H:%M')
                    # Apply timezone
                    tz = ZoneInfo(preset_config.timezone)
                    scheduled_datetime = scheduled_datetime.replace(tzinfo=tz)

            # Parse AI pages mode
            ai_pages_mode_str = data.get('ai_pages_mode', 'all')
            ai_pages_mode = AITestMode(ai_pages_mode_str)

            # Parse selected AI page IDs
            ai_page_ids = []
            if ai_pages_mode == AITestMode.SELECTED:
                ai_page_ids = request.form.getlist('ai_page_ids')

            # Parse enabled touchpoints
            enabled_touchpoints = request.form.getlist('enabled_touchpoints')

            # Parse project user IDs
            project_user_ids = request.form.getlist('project_user_ids')

            # Create test config
            test_config = ScheduleTestConfig(
                run_ai_tests=data.get('run_ai_tests') == 'on',
                run_javascript_tests=data.get('run_javascript_tests', 'on') == 'on',
                run_python_tests=data.get('run_python_tests', 'on') == 'on',
                enabled_touchpoints=enabled_touchpoints,
                ai_pages_mode=ai_pages_mode,
                ai_page_ids=ai_page_ids,
                take_screenshots=data.get('take_screenshots', 'on') == 'on'
            )

            # Create schedule
            schedule = TestSchedule(
                website_id=website_id,
                name=data.get('name', '').strip(),
                description=data.get('description', '').strip() or None,
                schedule_type=schedule_type,
                scheduled_datetime=scheduled_datetime,
                cron_expression=data.get('cron_expression', '').strip() or None,
                preset_config=preset_config,
                test_config=test_config,
                project_user_ids=project_user_ids,
                enabled=data.get('enabled') == 'on',
                created_by=current_user.id if current_user.is_authenticated else None
            )

            # Save to database
            schedule_id = get_db().create_test_schedule(schedule)

            # Register with scheduler if enabled
            scheduler = get_scheduler_service()
            if scheduler and schedule.enabled:
                refreshed_schedule = get_db().get_test_schedule(schedule_id)
                if refreshed_schedule:
                    scheduler._register_schedule_with_apscheduler(refreshed_schedule)

            flash(ftl('schedules-schedule-name-created-successfully', name=schedule.name), 'success')
            return redirect(url_for('schedules.list_schedules', website_id=website_id))

        except Exception as e:
            logger.error(f"Error creating schedule: {e}")
            flash(ftl('schedules-error-creating-schedule-error', error=str(e)), 'error')

    # Get pages for AI selection
    pages = get_db().get_pages(website_id)

    # Get project users
    project_users = get_db().get_project_users(project.id, enabled_only=True) if project and project.id else []

    # Get touchpoints from touchpoint_tests mapping
    from auto_a11y.config.touchpoint_tests import TOUCHPOINT_TEST_MAPPING
    touchpoints = list(TOUCHPOINT_TEST_MAPPING.keys())

    return render_template('schedules/form.html',
                         website=website,
                         project=project,
                         schedule=None,
                         pages=pages,
                         project_users=project_users,
                         touchpoints=touchpoints,
                         schedule_types=ScheduleType,
                         ai_modes=AITestMode,
                         is_edit=False)


@schedules_bp.route('/websites/<website_id>/schedules/<schedule_id>')
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR)
def view_schedule(website_id: str, schedule_id: str) -> str | Response:
    """View schedule details"""
    website = get_db().get_website(website_id)
    if not website:
        flash(ftl('common-website-not-found'), 'error')
        return redirect(url_for('index'))

    schedule = get_db().get_test_schedule(schedule_id)
    if not schedule:
        flash(ftl('schedules-schedule-not-found'), 'error')
        return redirect(url_for('schedules.list_schedules', website_id=website_id))

    project = get_db().get_project(website.project_id)

    # Get scheduler status
    scheduler = get_scheduler_service()
    scheduler_status = scheduler.get_job_status(schedule_id) if scheduler else {"status": "scheduler_not_available"}

    # Get next run times
    next_runs = scheduler.get_next_run_times(schedule_id, 5) if scheduler else []

    return render_template('schedules/view.html',
                         website=website,
                         project=project,
                         schedule=schedule,
                         scheduler_status=scheduler_status,
                         next_runs=next_runs)


@schedules_bp.route('/websites/<website_id>/schedules/<schedule_id>/edit', methods=['GET', 'POST'])
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR)
def edit_schedule(website_id: str, schedule_id: str) -> str | Response:
    """Edit an existing schedule"""
    website = get_db().get_website(website_id)
    if not website:
        flash(ftl('common-website-not-found'), 'error')
        return redirect(url_for('index'))

    schedule = get_db().get_test_schedule(schedule_id)
    if not schedule:
        flash(ftl('schedules-schedule-not-found'), 'error')
        return redirect(url_for('schedules.list_schedules', website_id=website_id))

    project = get_db().get_project(website.project_id)

    if request.method == 'POST':
        try:
            data = request.form

            # Update schedule fields
            schedule.name = data.get('name', '').strip()
            schedule.description = data.get('description', '').strip() or None

            # Parse schedule type
            schedule_type_str = data.get('schedule_type', 'daily')
            schedule.schedule_type = ScheduleType(schedule_type_str)

            # Parse preset config
            schedule.preset_config = PresetConfig(
                time=data.get('time', '02:00'),
                day_of_week=int(data.get('day_of_week', 0)),
                day_of_month=int(data.get('day_of_month', 1)),
                timezone=data.get('timezone', 'America/Toronto')
            )

            # Parse scheduled datetime for one-time
            if schedule.schedule_type == ScheduleType.ONE_TIME:
                date_str = data.get('scheduled_date', '')
                time_str = data.get('scheduled_time', '02:00')
                if date_str:
                    datetime_str = f"{date_str} {time_str}"
                    scheduled_datetime = datetime.strptime(datetime_str, '%Y-%m-%d %H:%M')
                    tz = ZoneInfo(schedule.preset_config.timezone)
                    schedule.scheduled_datetime = scheduled_datetime.replace(tzinfo=tz)
            else:
                schedule.scheduled_datetime = None

            schedule.cron_expression = data.get('cron_expression', '').strip() or None

            # Parse AI pages mode
            ai_pages_mode_str = data.get('ai_pages_mode', 'all')
            ai_pages_mode = AITestMode(ai_pages_mode_str)

            # Parse selected AI page IDs
            ai_page_ids = []
            if ai_pages_mode == AITestMode.SELECTED:
                ai_page_ids = request.form.getlist('ai_page_ids')

            # Parse enabled touchpoints
            enabled_touchpoints = request.form.getlist('enabled_touchpoints')

            # Parse project user IDs
            project_user_ids = request.form.getlist('project_user_ids')

            # Update test config
            schedule.test_config = ScheduleTestConfig(
                run_ai_tests=data.get('run_ai_tests') == 'on',
                run_javascript_tests=data.get('run_javascript_tests', 'on') == 'on',
                run_python_tests=data.get('run_python_tests', 'on') == 'on',
                enabled_touchpoints=enabled_touchpoints,
                ai_pages_mode=ai_pages_mode,
                ai_page_ids=ai_page_ids,
                take_screenshots=data.get('take_screenshots', 'on') == 'on'
            )

            schedule.project_user_ids = project_user_ids
            schedule.enabled = data.get('enabled') == 'on'

            # Save to database
            get_db().update_test_schedule(schedule)

            # Update scheduler
            scheduler = get_scheduler_service()
            if scheduler:
                if schedule.enabled:
                    scheduler._register_schedule_with_apscheduler(schedule)
                else:
                    scheduler.remove_from_apscheduler(schedule_id)

            flash(ftl('schedules-schedule-name-updated-successfully', name=schedule.name), 'success')
            return redirect(url_for('schedules.view_schedule', website_id=website_id, schedule_id=schedule_id))

        except Exception as e:
            logger.error(f"Error updating schedule: {e}")
            flash(ftl('schedules-error-updating-schedule-error', error=str(e)), 'error')

    # Get pages for AI selection
    pages = get_db().get_pages(website_id)

    # Get project users
    project_users = get_db().get_project_users(project.id, enabled_only=True) if project and project.id else []

    # Get touchpoints from touchpoint_tests mapping
    from auto_a11y.config.touchpoint_tests import TOUCHPOINT_TEST_MAPPING
    touchpoints = list(TOUCHPOINT_TEST_MAPPING.keys())

    return render_template('schedules/form.html',
                         website=website,
                         project=project,
                         schedule=schedule,
                         pages=pages,
                         project_users=project_users,
                         touchpoints=touchpoints,
                         schedule_types=ScheduleType,
                         ai_modes=AITestMode,
                         is_edit=True)


@schedules_bp.route('/websites/<website_id>/schedules/<schedule_id>/delete', methods=['POST'])
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR)
def delete_schedule(website_id: str, schedule_id: str) -> Response:
    """Delete a schedule"""
    schedule = get_db().get_test_schedule(schedule_id)
    if not schedule:
        flash(ftl('schedules-schedule-not-found'), 'error')
        return redirect(url_for('schedules.list_schedules', website_id=website_id))

    # Remove from scheduler
    scheduler = get_scheduler_service()
    if scheduler:
        scheduler.remove_from_apscheduler(schedule_id)

    # Delete from database
    get_db().delete_test_schedule(schedule_id)

    flash(ftl('schedules-schedule-name-deleted', name=schedule.name), 'success')
    return redirect(url_for('schedules.list_schedules', website_id=website_id))


@schedules_bp.route('/websites/<website_id>/schedules/<schedule_id>/toggle', methods=['POST'])
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR)
def toggle_schedule(website_id: str, schedule_id: str) -> Response | tuple[Response, int]:
    """Enable or disable a schedule"""
    schedule = get_db().get_test_schedule(schedule_id)
    if not schedule:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'error': ftl('schedules-schedule-not-found')}), 404
        flash(ftl('schedules-schedule-not-found'), 'error')
        return redirect(url_for('schedules.list_schedules', website_id=website_id))

    # Toggle enabled state
    new_state = not schedule.enabled

    # Update database
    get_db().toggle_test_schedule(schedule_id, new_state)

    # Update scheduler
    scheduler = get_scheduler_service()
    if scheduler:
        if new_state:
            refreshed = get_db().get_test_schedule(schedule_id)
            if refreshed:
                scheduler._register_schedule_with_apscheduler(refreshed)
        else:
            scheduler.remove_from_apscheduler(schedule_id)

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({
            'success': True,
            'enabled': new_state,
            'message': ftl('schedules-schedule-enabled') if new_state else ftl('schedules-schedule-disabled')
        })

    flash(ftl('schedules-schedule-enabled') if new_state else ftl('schedules-schedule-disabled'), 'success')
    return redirect(url_for('schedules.list_schedules', website_id=website_id))


@schedules_bp.route('/websites/<website_id>/schedules/<schedule_id>/run-now', methods=['POST'])
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR)
def run_now(website_id: str, schedule_id: str) -> Response | tuple[Response, int]:
    """Trigger immediate execution of a schedule"""
    schedule = get_db().get_test_schedule(schedule_id)
    if not schedule:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'error': ftl('schedules-schedule-not-found')}), 404
        flash(ftl('schedules-schedule-not-found'), 'error')
        return redirect(url_for('schedules.list_schedules', website_id=website_id))

    # Trigger immediate execution
    scheduler = get_scheduler_service()
    if scheduler:
        job_id = scheduler.run_now(schedule_id)
        if job_id:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({
                    'success': True,
                    'job_id': job_id,
                    'message': ftl('schedules-test-run-started')
                })
            flash(ftl('schedules-test-run-started'), 'success')
        else:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'error': ftl('schedules-failed-to-start-test-run')}), 500
            flash(ftl('schedules-failed-to-start-test-run'), 'error')
    else:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'error': ftl('schedules-scheduler-not-available')}), 500
        flash(ftl('schedules-scheduler-not-available'), 'error')

    return redirect(url_for('schedules.view_schedule', website_id=website_id, schedule_id=schedule_id))


@schedules_bp.route('/websites/<website_id>/schedules/<schedule_id>/preview')
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR)
def preview_runs(website_id: str, schedule_id: str) -> Response | tuple[Response, int]:
    """Preview next run times for a schedule"""
    schedule = get_db().get_test_schedule(schedule_id)
    if not schedule:
        return jsonify({'error': ftl('schedules-schedule-not-found')}), 404

    scheduler = get_scheduler_service()
    if not scheduler:
        return jsonify({'error': ftl('schedules-scheduler-not-available')}), 500

    count = request.args.get('count', 5, type=int)
    next_runs = scheduler.get_next_run_times(schedule_id, count)

    return jsonify({
        'schedule_id': schedule_id,
        'next_runs': [dt.isoformat() for dt in next_runs]
    })
