"""
Public-facing routes for token-based and client-login-based access to test results.
Integrated into the main app as a blueprint.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from flask import (
    Blueprint, render_template, request,
    abort, g,
)
from auto_a11y.web.fluent import ftl
from auto_a11y.web.typed_app import get_app_config, get_db
from flask_login import current_user

from auto_a11y.models import TokenScope, Page
from auto_a11y.models.test_result import Violation
from auto_a11y.pdf.storage import PdfStorage
from auto_a11y.reporting.issue_descriptions_translated import get_detailed_issue_description
from auto_a11y.web.routes.auth import require_access, check_scope, get_effective_role


def _pdf_storage() -> PdfStorage:
    """Build a PdfStorage from app config. Reused by every route that
    asks the database for project-level totals so the FAIL/WARN counts
    on public pages match the admin overview."""
    return PdfStorage(base_dir=Path(get_app_config().PDF_STORAGE_DIR))

public_bp = Blueprint(
    'public', __name__,
    static_folder='../static/public',
    static_url_path='/public/static',
)
# NOTE: No template_folder set. All render_template calls use 'public/...' paths
# which resolve via the app-level template loader (auto_a11y/web/templates/).


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def group_by_touchpoint(violations: list[Violation]) -> dict[str, list[Violation]]:
    """Group a list of Violation objects by their touchpoint field."""
    groups: defaultdict[str, list[Violation]] = defaultdict(list)
    for v in violations:
        groups[v.touchpoint or ftl('common-other')].append(v)
    return dict(sorted(groups.items()))


def sort_pages(pages: list[Page], sort_by: str = 'url', sort_dir: str = 'asc') -> list[Page]:
    """Sort a list of Page objects by the given field."""
    key_map: dict[str, Callable[[Page], Any]] = {
        'url': lambda p: (p.url or '').lower(),
        'violations': lambda p: p.violation_count,
        'warnings': lambda p: p.warning_count,
        'last_tested': lambda p: p.last_tested or p.discovered_at,
    }
    key_fn: Callable[[Page], Any] = key_map.get(sort_by, key_map['url'])
    reverse = sort_dir == 'desc'
    return sorted(pages, key=key_fn, reverse=reverse)


# ------------------------------------------------------------------
# Token-based routes
# ------------------------------------------------------------------

@public_bp.route('/t/<token>/')
@require_access
def token_landing(token: str) -> str:
    """Landing page for a share-link token."""
    if g.access_scope == TokenScope.WEBSITE:
        website = get_db().get_website(g.access_scope_id)
        if not website:
            abort(404)
        project = get_db().get_project(website.project_id)
        pages = get_db().get_pages(website.id) if website.id else []
        sort_by = request.args.get('sort', 'url')
        sort_dir = request.args.get('dir', 'asc')
        sorted_pages = sort_pages(pages, sort_by, sort_dir)
        return render_template(
            'public/website.html',
            project=project, website=website, pages=sorted_pages,
            sort_by=sort_by, sort_dir=sort_dir, token=token,
        )

    # Project-scoped token
    project = get_db().get_project(g.access_scope_id)
    if not project:
        abort(404)
    stats = get_db().get_project_stats(g.access_scope_id, pdf_storage=_pdf_storage())
    websites = get_db().get_websites(g.access_scope_id)
    return render_template(
        'public/project.html',
        project=project, stats=stats, websites=websites, token=token,
    )


@public_bp.route('/t/<token>/w/<website_id>/')
@require_access
def token_website(token: str, website_id: str) -> str:
    """Website detail via token."""
    check_scope('website', website_id)
    website = get_db().get_website(website_id)
    if not website:
        abort(404)
    project = get_db().get_project(website.project_id)
    pages = get_db().get_pages(website_id)
    sort_by = request.args.get('sort', 'url')
    sort_dir = request.args.get('dir', 'asc')
    sorted_pages = sort_pages(pages, sort_by, sort_dir)
    return render_template(
        'public/website.html',
        project=project, website=website, pages=sorted_pages,
        sort_by=sort_by, sort_dir=sort_dir, token=token,
    )


@public_bp.route('/t/<token>/w/<website_id>/p/<page_id>/')
@require_access
def token_page(token: str, website_id: str, page_id: str) -> str:
    """Page detail (issues) via token."""
    check_scope('page', page_id)
    page = get_db().get_page(page_id)
    if not page:
        abort(404)
    website = get_db().get_website(page.website_id)
    project = get_db().get_project(website.project_id) if website else None
    test_result = get_db().get_latest_test_result(page_id)

    violation_groups = {}
    warning_groups = {}
    info_groups = {}
    if test_result:
        violation_groups = group_by_touchpoint(test_result.violations)
        warning_groups = group_by_touchpoint(test_result.warnings)
        info_groups = group_by_touchpoint(test_result.info)

    return render_template(
        'public/page.html',
        project=project, website=website, page=page,
        test_result=test_result,
        violation_groups=violation_groups,
        warning_groups=warning_groups,
        info_groups=info_groups,
        token=token,
        get_issue_description=get_detailed_issue_description,
    )


# ------------------------------------------------------------------
# Client (logged-in) read-only routes
# ------------------------------------------------------------------

@public_bp.route('/client/projects/')
@require_access
def client_projects() -> str:
    """List all projects (for logged-in clients)."""
    # For logged-in users (no token), filter by membership
    if g.access_scope is None and current_user.is_authenticated:
        if getattr(current_user, 'is_superadmin', False):
            projects = get_db().get_all_projects()
        else:
            projects = get_db().get_projects_for_user(str(current_user.get_id()))
    else:
        projects = get_db().get_all_projects()
    storage = _pdf_storage()
    project_data: list[dict[str, Any]] = []
    for project in projects:
        if not project.id:
            continue
        stats = get_db().get_project_stats(project.id, pdf_storage=storage)
        project_data.append({'project': project, 'stats': stats})
    return render_template('public/project_list.html', project_data=project_data)


@public_bp.route('/client/project/<project_id>/')
@require_access
def client_project(project_id: str) -> str:
    """Project overview (logged-in client)."""
    check_scope('project', project_id)
    if g.access_scope is None and current_user.is_authenticated and not getattr(current_user, 'is_superadmin', False):
        role = get_effective_role(current_user, request, project_id=project_id)
        if role is None:
            abort(403)
    project = get_db().get_project(project_id)
    if not project:
        abort(404)
    stats = get_db().get_project_stats(project_id, pdf_storage=_pdf_storage())
    websites = get_db().get_websites(project_id)
    return render_template(
        'public/project.html',
        project=project, stats=stats, websites=websites, token=None,
    )


@public_bp.route('/client/project/<project_id>/w/<website_id>/')
@require_access
def client_website(project_id: str, website_id: str) -> str:
    """Website detail (logged-in client)."""
    check_scope('website', website_id)
    if g.access_scope is None and current_user.is_authenticated and not getattr(current_user, 'is_superadmin', False):
        role = get_effective_role(current_user, request, project_id=project_id)
        if role is None:
            abort(403)
    website = get_db().get_website(website_id)
    if not website:
        abort(404)
    project = get_db().get_project(project_id)
    pages = get_db().get_pages(website_id)
    sort_by = request.args.get('sort', 'url')
    sort_dir = request.args.get('dir', 'asc')
    sorted_pages = sort_pages(pages, sort_by, sort_dir)
    return render_template(
        'public/website.html',
        project=project, website=website, pages=sorted_pages,
        sort_by=sort_by, sort_dir=sort_dir, token=None,
    )


@public_bp.route('/client/project/<project_id>/w/<website_id>/p/<page_id>/')
@require_access
def client_page(project_id: str, website_id: str, page_id: str) -> str:
    """Page detail (logged-in client)."""
    check_scope('page', page_id)
    page = get_db().get_page(page_id)
    if not page:
        abort(404)
    website = get_db().get_website(page.website_id)
    project = get_db().get_project(project_id)
    test_result = get_db().get_latest_test_result(page_id)

    violation_groups = {}
    warning_groups = {}
    info_groups = {}
    if test_result:
        violation_groups = group_by_touchpoint(test_result.violations)
        warning_groups = group_by_touchpoint(test_result.warnings)
        info_groups = group_by_touchpoint(test_result.info)

    return render_template(
        'public/page.html',
        project=project, website=website, page=page,
        test_result=test_result,
        violation_groups=violation_groups,
        warning_groups=warning_groups,
        info_groups=info_groups,
        token=None,
        get_issue_description=get_detailed_issue_description,
    )
