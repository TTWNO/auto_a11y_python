"""Group management routes."""
from __future__ import annotations

from flask import Blueprint, Response, render_template, request, redirect, url_for, flash
from werkzeug.wrappers import Response as WerkzeugResponse
from auto_a11y.web.fluent import ftl
from auto_a11y.web.typed_app import get_db
from flask_login import login_required

from auto_a11y.core.permissions import permission_required
from auto_a11y.models.permission_group import (
    PermissionGroup, RESOURCE_NOUNS, PERMISSION_LEVELS
)

groups_bp = Blueprint('groups', __name__)


@groups_bp.route('/')
@permission_required('groups', 'read')
def list_groups() -> str:
    """List all permission groups."""
    groups = get_db().get_all_groups()
    group_data = []
    for g in groups:
        group_data.append({
            'group': g,
            'member_count': get_db().count_group_members(g.id),
        })
    return render_template('groups/list.html', groups=group_data)


@groups_bp.route('/create', methods=['GET', 'POST'])
@permission_required('groups', 'create')
def create_group() -> str | Response | WerkzeugResponse:
    """Create a new permission group."""
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()

        if not name:
            flash(ftl('groups-group-name-is-required'), 'danger')
            return redirect(url_for('groups.create_group'))

        if get_db().get_group_by_name(name):
            flash(ftl('groups-group-name-already-exists', name=name), 'danger')
            return redirect(url_for('groups.create_group'))

        permissions = {}
        for resource in RESOURCE_NOUNS:
            level = request.form.get(f'perm_{resource}', 'none')
            if level in PERMISSION_LEVELS:
                permissions[resource] = level
            else:
                permissions[resource] = 'none'

        group = PermissionGroup(
            name=name,
            description=description,
            permissions=permissions,
        )
        get_db().create_group(group)
        flash(ftl('groups-group-name-created', name=name), 'success')
        return redirect(url_for('groups.list_groups'))

    return render_template('groups/edit.html',
                           group=None,
                           resource_nouns=RESOURCE_NOUNS,
                           permission_levels=list(PERMISSION_LEVELS.keys()))


@groups_bp.route('/<group_id>/edit', methods=['GET', 'POST'])
@permission_required('groups', 'update')
def edit_group(group_id: str) -> str | Response | WerkzeugResponse:
    """Edit an existing permission group."""
    group = get_db().get_group(group_id)
    if not group:
        flash(ftl('groups-group-not-found'), 'danger')
        return redirect(url_for('groups.list_groups'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()

        if not name:
            flash(ftl('groups-group-name-is-required'), 'danger')
            return redirect(url_for('groups.edit_group', group_id=group_id))

        existing = get_db().get_group_by_name(name)
        if existing and str(existing._id) != group_id:
            flash(ftl('groups-group-name-already-exists', name=name), 'danger')
            return redirect(url_for('groups.edit_group', group_id=group_id))

        permissions = {}
        for resource in RESOURCE_NOUNS:
            level = request.form.get(f'perm_{resource}', 'none')
            if level in PERMISSION_LEVELS:
                permissions[resource] = level
            else:
                permissions[resource] = 'none'

        group.name = name
        group.description = description
        group.permissions = permissions
        get_db().update_group(group)
        flash(ftl('groups-group-name-updated', name=name), 'success')
        return redirect(url_for('groups.list_groups'))

    return render_template('groups/edit.html',
                           group=group,
                           resource_nouns=RESOURCE_NOUNS,
                           permission_levels=list(PERMISSION_LEVELS.keys()))


@groups_bp.route('/<group_id>/delete', methods=['POST'])
@permission_required('groups', 'delete')
def delete_group(group_id: str) -> WerkzeugResponse:
    """Delete a non-system group."""
    group = get_db().get_group(group_id)
    if not group:
        flash(ftl('groups-group-not-found'), 'danger')
        return redirect(url_for('groups.list_groups'))

    if group.is_system:
        flash(ftl('groups-system-groups-cannot-be-deleted'), 'danger')
        return redirect(url_for('groups.list_groups'))

    get_db().delete_group(group_id)
    flash(ftl('groups-group-name-deleted', name=group.name), 'success')
    return redirect(url_for('groups.list_groups'))
