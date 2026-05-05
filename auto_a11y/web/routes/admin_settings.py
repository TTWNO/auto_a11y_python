"""
Admin settings routes.

Superadmin-only UI for managing system settings stored in MongoDB
(``system_settings`` collection). Settings here override the matching
environment variables, so ops teams can configure features like Drupal
without needing shell access to edit ``.env``.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from flask import Blueprint, Response, flash, render_template, request, url_for

from auto_a11y.core.system_settings import SystemSettings
from auto_a11y.drupal.config import DRUPAL_SETTINGS_KEY
from auto_a11y.web.fluent import ftl
from auto_a11y.web.routes.auth import admin_required
from auto_a11y.web.typed_app import get_db, redirect

logger = logging.getLogger(__name__)

admin_settings_bp = Blueprint('admin_settings', __name__)


def _drupal_form_view(settings: SystemSettings) -> dict[str, Any]:
    """Build the data passed to the template for the Drupal section.

    Falls back to environment-variable values when the DB section is unset
    so the form always shows the *currently effective* configuration. The
    ``source`` field tells the template whether the user is editing the
    DB-stored override or a (display-only) env-var fallback.
    """
    section = settings.get_section(DRUPAL_SETTINGS_KEY)

    if section is not None:
        return {
            "source": "database",
            "base_url": section.get("base_url", ""),
            "username": section.get("username", ""),
            "password_set": bool(section.get("password")),
            "enabled": bool(section.get("enabled", True)),
        }

    return {
        "source": "environment" if (
            os.getenv('DRUPAL_BASE_URL')
            or os.getenv('DRUPAL_USERNAME')
            or os.getenv('DRUPAL_PASSWORD')
        ) else "unset",
        "base_url": os.getenv('DRUPAL_BASE_URL', ''),
        "username": os.getenv('DRUPAL_USERNAME', ''),
        "password_set": bool(os.getenv('DRUPAL_PASSWORD')),
        "enabled": os.getenv('DRUPAL_EXPORT_ENABLED', 'true').lower() == 'true',
    }


@admin_settings_bp.route('/admin/settings', methods=['GET'])
@admin_required
def settings_page() -> str | Response:
    """Render the admin system-settings page."""
    db = get_db()
    settings = SystemSettings(db)
    return render_template(
        'admin_settings/index.html',
        drupal=_drupal_form_view(settings),
    )


@admin_settings_bp.route('/admin/settings/drupal', methods=['POST'])
@admin_required
def update_drupal() -> Response:
    """Save Drupal connection settings (or clear them).

    A blank ``base_url`` clears the section so the env-var fallback applies
    again. Leaving ``password`` blank when an existing password is stored
    keeps the existing value (avoids the user having to re-enter it on
    every edit).
    """
    db = get_db()
    settings = SystemSettings(db)

    action = request.form.get('action', 'save')
    user_id = _current_user_id()

    if action == 'clear':
        settings.clear_section(DRUPAL_SETTINGS_KEY)
        flash(ftl('admin-settings-drupal-cleared'), 'success')
        return redirect(url_for('admin_settings.settings_page'))

    base_url = request.form.get('base_url', '').strip()
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    enabled = request.form.get('enabled') == 'on'

    if not base_url:
        settings.clear_section(DRUPAL_SETTINGS_KEY)
        flash(ftl('admin-settings-drupal-cleared'), 'success')
        return redirect(url_for('admin_settings.settings_page'))

    if not base_url.startswith(('http://', 'https://')):
        flash(ftl('admin-settings-drupal-invalid-url'), 'danger')
        return redirect(url_for('admin_settings.settings_page'))

    if not username:
        flash(ftl('admin-settings-drupal-username-required'), 'danger')
        return redirect(url_for('admin_settings.settings_page'))

    existing = settings.get_section(DRUPAL_SETTINGS_KEY) or {}
    existing_password = existing.get('password') if isinstance(existing.get('password'), str) else None

    if not password:
        if not existing_password:
            flash(ftl('admin-settings-drupal-password-required'), 'danger')
            return redirect(url_for('admin_settings.settings_page'))
        password = existing_password

    settings.set_section(
        DRUPAL_SETTINGS_KEY,
        {
            "base_url": base_url,
            "username": username,
            "password": password,
            "enabled": enabled,
        },
        updated_by=user_id,
    )
    flash(ftl('admin-settings-drupal-saved'), 'success')
    return redirect(url_for('admin_settings.settings_page'))


def _current_user_id() -> str | None:
    from flask_login import current_user

    if not current_user.is_authenticated:
        return None
    user_id = getattr(current_user, 'id', None)
    return str(user_id) if user_id is not None else None
