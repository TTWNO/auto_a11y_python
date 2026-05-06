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

from auto_a11y.core.runtime_config import (
    CONFIG_SECTIONS,
    ConfigField,
    ConfigSection,
    FieldType,
    coerce_value,
    get_field_view_value,
    get_section,
    is_bool_true,
    is_password_set,
    section_source,
)
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


def _section_form_view(section: ConfigSection, settings: SystemSettings) -> dict[str, Any]:
    """Build the data passed to the template for one env-var section."""
    section_data = settings.get_section(section.section_id)
    fields_view: list[dict[str, Any]] = []
    for field in section.fields:
        fields_view.append({
            "env_var": field.env_var,
            "db_key": field.db_key,
            "type": field.field_type.value,
            "label_id": field.label_id,
            "help_id": field.help_id,
            "value": get_field_view_value(field, section_data),
            "password_set": is_password_set(field, section_data),
            "checked": is_bool_true(field, section_data),
            "input_id": f"setting-{section.section_id}-{field.db_key}".replace("_", "-"),
        })
    return {
        "section_id": section.section_id,
        "heading_id": section.heading_id,
        "description_id": section.description_id,
        "icon": section.icon,
        "note_id": section.note_id,
        "source": section_source(section, section_data),
        "fields": fields_view,
    }


@admin_settings_bp.route('/admin/settings', methods=['GET'])
@admin_required
def settings_page() -> str | Response:
    """Render the admin system-settings page."""
    db = get_db()
    settings = SystemSettings(db)
    sections_view = [_section_form_view(s, settings) for s in CONFIG_SECTIONS]
    return render_template(
        'admin_settings/index.html',
        drupal=_drupal_form_view(settings),
        env_sections=sections_view,
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


@admin_settings_bp.route('/admin/settings/section/<section_id>', methods=['POST'])
@admin_required
def update_env_section(section_id: str) -> Response:
    """Save (or clear) one env-var-backed section.

    For each field in the section schema, parse the form value, coerce it
    to the right type, and persist the whole section dict to
    ``system_settings``. Password fields left blank preserve the existing
    stored password (mirrors the Drupal flow).

    ``action=clear`` removes the section entirely so the env-var fallback
    takes over again.
    """
    section = get_section(section_id)
    if section is None:
        flash(ftl('admin-settings-unknown-section'), 'danger')
        return redirect(url_for('admin_settings.settings_page'))

    db = get_db()
    settings = SystemSettings(db)
    user_id = _current_user_id()

    if request.form.get('action') == 'clear':
        settings.clear_section(section.section_id)
        flash(ftl('admin-settings-section-cleared', heading=ftl(section.heading_id)), 'success')
        return redirect(url_for('admin_settings.settings_page'))

    existing = settings.get_section(section.section_id) or {}
    new_values: dict[str, Any] = {}

    for field in section.fields:
        try:
            new_values[field.db_key] = _read_field(field, existing)
        except ValueError as exc:
            flash(
                ftl(
                    'admin-settings-field-invalid',
                    label=ftl(field.label_id),
                    detail=str(exc),
                ),
                'danger',
            )
            return redirect(url_for('admin_settings.settings_page'))

    settings.set_section(section.section_id, new_values, updated_by=user_id)
    flash(ftl('admin-settings-section-saved', heading=ftl(section.heading_id)), 'success')
    return redirect(url_for('admin_settings.settings_page'))


def _read_field(field: ConfigField, existing: dict[str, Any]) -> Any:
    """Parse a single field's form value, with type coercion and the
    "blank password keeps existing" rule."""
    if field.field_type is FieldType.BOOL:
        return request.form.get(field.db_key) == 'on'

    raw = request.form.get(field.db_key, '')

    if field.field_type is FieldType.PASSWORD:
        if raw == '':
            existing_pw = existing.get(field.db_key)
            return existing_pw if isinstance(existing_pw, str) else ''
        return raw

    raw = raw.strip()

    if field.field_type is FieldType.INT:
        source = raw if raw else field.default
        if source == '':
            return 0
        try:
            return int(source)
        except ValueError as exc:
            raise ValueError(f"expected integer, got {raw!r}") from exc

    if field.field_type is FieldType.FLOAT:
        source = raw if raw else field.default
        if source == '':
            return 0.0
        try:
            return float(source)
        except ValueError as exc:
            raise ValueError(f"expected number, got {raw!r}") from exc

    coerced = coerce_value(field, raw)
    return coerced


def _current_user_id() -> str | None:
    from flask_login import current_user

    if not current_user.is_authenticated:
        return None
    user_id = getattr(current_user, 'id', None)
    return str(user_id) if user_id is not None else None
