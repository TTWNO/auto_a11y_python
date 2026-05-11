"""
Routes for managing website users (test users for authenticated testing)
"""
from __future__ import annotations

import asyncio

from flask import Blueprint, Response, render_template, request, redirect, url_for, flash, jsonify
from werkzeug.wrappers import Response as WerkzeugResponse
from auto_a11y.web.fluent import ftl
from auto_a11y.web.typed_app import get_db, get_app_config
from auto_a11y.models import WebsiteUser, LoginConfig, AuthenticationMethod
from auto_a11y.core.browser_manager import BrowserManager
from auto_a11y.testing.login_automation import LoginAutomation
import logging

logger = logging.getLogger(__name__)

website_users_bp = Blueprint('website_users', __name__)


@website_users_bp.route('/website/<website_id>/users')
def list_users(website_id: str) -> str | Response | WerkzeugResponse:
    """List all test users for a website"""
    website = get_db().get_website(website_id)
    if not website:
        flash(ftl('common-website-not-found'), 'error')
        return redirect(url_for('index'))

    # Get project for context
    project = get_db().get_project(website.project_id) if website else None

    # Get all users
    users = get_db().get_website_users(website_id)

    # Get unique roles
    all_roles = get_db().get_user_roles_for_website(website_id)

    return render_template('website_users/list.html',
                         website=website,
                         project=project,
                         users=users,
                         all_roles=all_roles)


@website_users_bp.route('/website/<website_id>/users/create', methods=['GET', 'POST'])
def create_user(website_id: str) -> str | Response | WerkzeugResponse:
    """Create a new test user"""
    website = get_db().get_website(website_id)
    if not website:
        flash(ftl('common-website-not-found'), 'error')
        return redirect(url_for('index'))

    project = get_db().get_project(website.project_id) if website else None

    if request.method == 'POST':
        try:
            data = request.form

            # Parse roles from comma-separated string
            roles_str = data.get('roles', '').strip()
            roles = [r.strip() for r in roles_str.split(',') if r.strip()] if roles_str else []

            # Create login config
            auth_method_str = data.get('authentication_method', 'form_login')
            login_config = LoginConfig(
                authentication_method=AuthenticationMethod(auth_method_str),
                login_url=data.get('login_url', '').strip() or None,
                username_field_selector=data.get('username_field_selector', '').strip() or None,
                password_field_selector=data.get('password_field_selector', '').strip() or None,
                submit_button_selector=data.get('submit_button_selector', '').strip() or None,
                success_indicator_selector=data.get('success_indicator_selector', '').strip() or None,
                logout_url=data.get('logout_url', '').strip() or None,
                logout_button_selector=data.get('logout_button_selector', '').strip() or None,
                logout_success_indicator_selector=data.get('logout_success_indicator_selector', '').strip() or None,
                manual_login_wait_seconds=max(1, int(data.get('manual_login_wait_seconds', 120) or 120)),
                session_timeout_minutes=int(data.get('session_timeout_minutes', 30))
            )

            # Create user
            user = WebsiteUser(
                website_id=website_id,
                username=data.get('username', '').strip(),
                password=data.get('password', '').strip(),
                display_name=data.get('display_name', '').strip() or None,
                roles=roles,
                description=data.get('description', '').strip() or None,
                login_config=login_config,
                enabled=data.get('enabled') == 'on'
            )

            _user_id = get_db().create_website_user(user)
            flash(ftl('common-test-user-name-created-successfully', name=user.name_display), 'success')
            return redirect(url_for('website_users.list_users', website_id=website_id))

        except Exception as e:
            logger.error(f"Error creating user: {e}")
            flash(ftl('common-error-creating-user-error', error=str(e)), 'error')

    # Get existing roles for autocomplete
    existing_roles = get_db().get_user_roles_for_website(website_id)

    return render_template('website_users/create.html',
                         website=website,
                         project=project,
                         existing_roles=existing_roles,
                         auth_methods=[m for m in AuthenticationMethod])


@website_users_bp.route('/user/<user_id>')
def view_user(user_id: str) -> str | Response | WerkzeugResponse:
    """View user details"""
    user = get_db().get_website_user(user_id)
    if not user:
        flash(ftl('common-user-not-found'), 'error')
        return redirect(url_for('index'))

    website = get_db().get_website(user.website_id)
    project = get_db().get_project(website.project_id) if website else None

    return render_template('website_users/view.html',
                         user=user,
                         website=website,
                         project=project)


@website_users_bp.route('/user/<user_id>/edit', methods=['GET', 'POST'])
def edit_user(user_id: str) -> str | Response | WerkzeugResponse:
    """Edit a test user"""
    user = get_db().get_website_user(user_id)
    if not user:
        flash(ftl('common-user-not-found'), 'error')
        return redirect(url_for('index'))

    website = get_db().get_website(user.website_id)
    project = get_db().get_project(website.project_id) if website else None

    if request.method == 'POST':
        try:
            data = request.form

            # Parse roles
            roles_str = data.get('roles', '').strip()
            roles = [r.strip() for r in roles_str.split(',') if r.strip()] if roles_str else []

            # Update login config
            user.login_config.authentication_method = AuthenticationMethod(
                data.get('authentication_method', 'form_login')
            )
            user.login_config.login_url = data.get('login_url', '').strip() or None
            user.login_config.username_field_selector = data.get('username_field_selector', '').strip() or None
            user.login_config.password_field_selector = data.get('password_field_selector', '').strip() or None
            user.login_config.submit_button_selector = data.get('submit_button_selector', '').strip() or None
            user.login_config.success_indicator_selector = data.get('success_indicator_selector', '').strip() or None
            user.login_config.logout_url = data.get('logout_url', '').strip() or None
            user.login_config.logout_button_selector = data.get('logout_button_selector', '').strip() or None
            user.login_config.logout_success_indicator_selector = data.get('logout_success_indicator_selector', '').strip() or None
            user.login_config.manual_login_wait_seconds = max(
                1, int(data.get('manual_login_wait_seconds', 120) or 120)
            )
            user.login_config.session_timeout_minutes = int(data.get('session_timeout_minutes', 30))

            # Update user fields
            user.username = data.get('username', '').strip()
            password = data.get('password', '').strip()
            if password:  # Only update password if provided
                user.password = password
            user.display_name = data.get('display_name', '').strip() or None
            user.roles = roles
            user.description = data.get('description', '').strip() or None
            user.enabled = data.get('enabled') == 'on'

            get_db().update_website_user(user)
            flash(ftl('common-test-user-name-updated-successfully', name=user.name_display), 'success')
            return redirect(url_for('website_users.list_users', website_id=user.website_id))

        except Exception as e:
            logger.error(f"Error updating user: {e}")
            flash(ftl('common-error-updating-user-error', error=str(e)), 'error')

    # Get existing roles for autocomplete
    existing_roles = get_db().get_user_roles_for_website(user.website_id)

    return render_template('website_users/edit.html',
                         user=user,
                         website=website,
                         project=project,
                         existing_roles=existing_roles,
                         auth_methods=[m for m in AuthenticationMethod])


@website_users_bp.route('/user/<user_id>/delete', methods=['POST'])
def delete_user(user_id: str) -> Response | tuple[Response, int]:
    """Delete a test user"""
    user = get_db().get_website_user(user_id)
    if not user:
        return jsonify({'error': ftl('common-user-not-found')}), 404

    try:
        website_id = user.website_id
        get_db().delete_website_user(user_id)
        return jsonify({
            'success': True,
            'message': ftl('common-user-deleted-successfully'),
            'redirect': url_for('website_users.list_users', website_id=website_id)
        })
    except Exception as e:
        logger.error(f"Error deleting user: {e}")
        return jsonify({'error': str(e)}), 500


@website_users_bp.route('/user/<user_id>/test-login', methods=['POST'])
def test_login(user_id: str) -> Response | tuple[Response, int]:
    """Test login for a website user without running a full test"""
    user = get_db().get_website_user(user_id)
    if not user:
        return jsonify({'error': ftl('common-user-not-found')}), 404

    login_config = user.login_config
    is_manual = login_config.authentication_method.value == 'manual_login'
    if not login_config.login_url and not is_manual:
        return jsonify({
            'success': False,
            'error': ftl('websites-login-url-not-configured')
        })

    # Build browser config
    website = get_db().get_website(user.website_id)
    project = get_db().get_project(website.project_id) if website else None
    browser_config = get_app_config().__dict__.copy()
    if project and project.config:
        browser_config['stealth_mode'] = project.config.get('stealth_mode', False)
        headless_setting = project.config.get('headless_browser', 'true')
        browser_config['BROWSER_HEADLESS'] = (headless_setting == 'true')
    else:
        browser_config['stealth_mode'] = False

    # Manual login spawns a separate visible browser inside LoginAutomation
    # (the test-run browser stays whatever the project config says); we only
    # need to extend the request timeout to cover the configured wait.
    wait_seconds = login_config.manual_login_wait_seconds if is_manual else 30
    timeout_ms = max(30000, (wait_seconds + 30) * 1000)

    async def _do_test_login() -> dict[str, object]:
        bm = BrowserManager(browser_config)
        try:
            await bm.start()
            context = await bm.create_context()
            page = await context.new_page()
            login_automation = LoginAutomation(get_db())
            result = await login_automation.perform_login(page, user, timeout=timeout_ms)
            return result
        finally:
            await bm.stop()

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_do_test_login())
        finally:
            loop.close()

        return jsonify({
            'success': result.get('success', False),
            'error': result.get('error'),
            'duration_ms': result.get('duration_ms', 0),
            'manual_login': is_manual,
            'wait_seconds': wait_seconds if is_manual else None,
        })

    except Exception as e:
        logger.error(f"Test login error for user {user_id}: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@website_users_bp.route('/user/<user_id>/toggle', methods=['POST'])
def toggle_user(user_id: str) -> Response | tuple[Response, int]:
    """Enable/disable a test user"""
    user = get_db().get_website_user(user_id)
    if not user:
        return jsonify({'error': ftl('common-user-not-found')}), 404

    try:
        user.enabled = not user.enabled
        success = get_db().update_website_user(user)

        return jsonify({
            'success': success,
            'enabled': user.enabled,
            'message': ftl('common-user-enabled') if user.enabled else ftl('common-user-disabled')
        })
    except Exception as e:
        logger.error(f"Error toggling user: {e}")
        return jsonify({'error': str(e)}), 500


@website_users_bp.route('/user/<user_id>/clear-cache', methods=['POST'])
def clear_cache(user_id: str) -> Response | tuple[Response, int]:
    """Delete the cached manual-login session for this website user."""
    from auto_a11y.testing.login_automation import clear_session_cache

    user = get_db().get_website_user(user_id)
    if not user:
        return jsonify({'success': False, 'error': ftl('common-user-not-found')}), 404

    removed = clear_session_cache(user)
    return jsonify({
        'success': True,
        'removed': removed,
        'message': (ftl('websites-login-cache-cleared') if removed
                    else ftl('websites-login-cache-empty')),
    })
