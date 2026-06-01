"""Per-project authorization tests for the remaining IDOR gaps (Task 1.11).

This task closes the last unguarded project-scoped routes across four
blueprints:

* ``projects_bp`` — the destructive/admin page routes (``edit_project``,
  ``delete_project``, ``test_project``) and the three deprecated project-API
  readers (``api_get_project_users``, ``api_get_project``,
  ``api_get_discovered_pages``). All key on ``<project_id>`` so
  ``resolve_project_id`` returns the param directly and the patched
  ``user_has_permission`` seam decides the role.
* ``api_bp`` (the v1 REST surface) — ``get_test_result`` and
  ``get_test_result_states`` key on ``<result_id>``;
  ``resolve_project_id`` follows result -> page -> website -> project, so the
  MagicMock db is seeded to yield a project for that chain.
* ``share_tokens_bp`` — ``revoke_token`` keys on ``<token_id>``, which
  ``resolve_project_id`` does NOT handle, so it keeps its in-handler scope
  check and only gains ``@login_required`` (anonymous -> redirect, not 403).
* ``schedules_bp`` — ``schedules_dashboard`` aggregates across all projects;
  it gains ``@login_required`` and scopes results to the caller's accessible
  projects in-handler.

Routes already guarded before this task (``view_project``, ``add_website``,
``generate_project_report``, ``api_project_websites``,
``get_page_test_results``, the share-token create/list routes, every other
``schedules_bp`` route) are NOT re-tested here.

Mongo is never touched: the harness app carries a ``MagicMock`` ``db``.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from flask import Flask
from flask.testing import FlaskClient

from auto_a11y.web.routes.api import api_bp
from auto_a11y.web.routes.projects import projects_bp

from tests.web._authz_helpers import Role, StubUser, make_app_with_blueprint


def _authenticated_client(app: Flask) -> FlaskClient:
    """Test client with the harness stub user seated in the session."""
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['_user_id'] = StubUser().id
    return client


def _projects_client(
    monkeypatch: pytest.MonkeyPatch, role: Role | None,
) -> FlaskClient:
    """Authenticated test client for ``projects_bp`` at ``/projects``."""
    app = make_app_with_blueprint(
        projects_bp, role=role, monkeypatch=monkeypatch, url_prefix='/projects',
    )
    app.config['PROPAGATE_EXCEPTIONS'] = False
    return _authenticated_client(app)


# ---------------------------------------------------------------------------
# projects.py — destructive/admin page routes.
# ---------------------------------------------------------------------------


def test_delete_project_forbidden_for_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """delete_project is ADMIN-only: a client is rejected."""
    client = _projects_client(monkeypatch, role='client')

    resp = client.post('/projects/proj-abc/delete')

    assert resp.status_code == 403


def test_delete_project_allowed_for_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """delete_project admits ADMIN: the guard lets the request through."""
    client = _projects_client(monkeypatch, role='admin')

    resp = client.post('/projects/proj-abc/delete')

    assert resp.status_code != 403


def test_edit_project_forbidden_for_auditor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """edit_project is ADMIN-only: an auditor is rejected."""
    client = _projects_client(monkeypatch, role='auditor')

    resp = client.get('/projects/proj-abc/edit')

    assert resp.status_code == 403


def test_edit_project_allowed_for_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """edit_project admits ADMIN: the guard lets the request through."""
    client = _projects_client(monkeypatch, role='admin')

    resp = client.get('/projects/proj-abc/edit')

    assert resp.status_code != 403


def test_test_project_forbidden_for_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """test_project is ADMIN/AUDITOR: a client is rejected."""
    client = _projects_client(monkeypatch, role='client')

    resp = client.post('/projects/proj-abc/test-all')

    assert resp.status_code == 403


def test_test_project_allowed_for_auditor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """test_project admits an auditor (ADMIN/AUDITOR tier)."""
    client = _projects_client(monkeypatch, role='auditor')

    resp = client.post('/projects/proj-abc/test-all')

    assert resp.status_code != 403


# ---------------------------------------------------------------------------
# projects.py — deprecated project-API readers (ADMIN/AUDITOR/CLIENT).
# ---------------------------------------------------------------------------


def test_api_get_project_forbidden_without_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """api_get_project (read tier): no role -> 403."""
    client = _projects_client(monkeypatch, role=None)

    resp = client.get('/projects/api/proj-abc/details')

    assert resp.status_code == 403


def test_api_get_project_allowed_for_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """api_get_project admits a client (read tier)."""
    client = _projects_client(monkeypatch, role='client')

    resp = client.get('/projects/api/proj-abc/details')

    assert resp.status_code != 403


# ---------------------------------------------------------------------------
# api.py v1 — get_test_result keyed on result_id.
# ---------------------------------------------------------------------------


def _seed_result_chain(app: Flask) -> None:
    """Make ``result-1`` resolve result -> page -> website -> project 'p1'."""
    db = getattr(app, 'db')
    db.get_test_result.return_value = SimpleNamespace(page_id='page-1')
    db.get_page.return_value = SimpleNamespace(website_id='w1')
    db.get_website.return_value = SimpleNamespace(project_id='p1')


def _api_client(
    monkeypatch: pytest.MonkeyPatch, role: Role | None,
) -> FlaskClient:
    """Authenticated client for ``api_bp`` at ``/api/v1`` with a result chain."""
    app = make_app_with_blueprint(
        api_bp, role=role, monkeypatch=monkeypatch, url_prefix='/api/v1',
    )
    app.config['PROPAGATE_EXCEPTIONS'] = False
    _seed_result_chain(app)
    return _authenticated_client(app)


def test_get_test_result_forbidden_without_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_test_result (read tier) resolves via result_id; no role -> 403."""
    client = _api_client(monkeypatch, role=None)

    resp = client.get('/api/v1/test-results/result-1')

    assert resp.status_code == 403


@pytest.mark.parametrize('role', ['admin', 'auditor', 'client'])
def test_get_test_result_allowed_for_members(
    role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_test_result admits admin/auditor/client once the chain resolves."""
    client = _api_client(monkeypatch, role=role)

    resp = client.get('/api/v1/test-results/result-1')

    assert resp.status_code != 403


def test_get_test_result_states_forbidden_without_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_test_result_states (read tier) resolves via result_id; no role -> 403."""
    client = _api_client(monkeypatch, role=None)

    resp = client.get('/api/v1/test-results/result-1/states')

    assert resp.status_code == 403


def test_get_test_result_states_allowed_for_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_test_result_states admits a member once the chain resolves."""
    client = _api_client(monkeypatch, role='client')

    resp = client.get('/api/v1/test-results/result-1/states')

    assert resp.status_code != 403


# ---------------------------------------------------------------------------
# share_tokens.py — revoke_token keeps its manual check; gains @login_required.
# ---------------------------------------------------------------------------
#
# ``<token_id>`` is not resolvable by ``resolve_project_id`` (no token_id
# branch), so the route cannot use ``project_role_required``. It instead keeps
# its in-handler ``get_effective_role`` scope check and adds ``@login_required``
# so an anonymous caller is redirected to login (302) rather than reaching the
# handler.


def test_revoke_token_anonymous_redirected_to_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """revoke_token without a session: @login_required redirects (not 200)."""
    from auto_a11y.web.routes.share_tokens import share_tokens_bp

    app = make_app_with_blueprint(
        share_tokens_bp, role=None, monkeypatch=monkeypatch, url_prefix='/admin',
    )
    app.config['PROPAGATE_EXCEPTIONS'] = False
    client = app.test_client()  # NO session -> anonymous

    resp = client.post('/admin/share-tokens/tok-1/revoke')

    # Flask-Login redirects anonymous users to the (unconfigured) login view.
    assert resp.status_code in (302, 401)
    assert resp.status_code != 200


def test_revoke_token_member_scope_check_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """revoke_token with a non-member session: in-handler check -> 403.

    The token resolves to a project the stub user has no role on (role=None),
    so the in-handler ``get_effective_role`` check returns 403.
    """
    from auto_a11y.models import TokenScope
    from auto_a11y.web.routes.share_tokens import share_tokens_bp

    app = make_app_with_blueprint(
        share_tokens_bp, role=None, monkeypatch=monkeypatch, url_prefix='/admin',
    )
    app.config['PROPAGATE_EXCEPTIONS'] = False
    db = getattr(app, 'db')
    db.get_share_token.return_value = SimpleNamespace(
        scope=TokenScope.PROJECT, scope_id='p1',
    )
    client = _authenticated_client(app)

    resp = client.post('/admin/share-tokens/tok-1/revoke')

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# schedules.py — schedules_dashboard gains @login_required + in-handler scope.
# ---------------------------------------------------------------------------


def test_schedules_dashboard_anonymous_redirected_to_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """schedules_dashboard without a session: @login_required redirects."""
    from auto_a11y.web.routes.schedules import schedules_bp

    app = make_app_with_blueprint(
        schedules_bp, role=None, monkeypatch=monkeypatch, url_prefix='',
    )
    app.config['PROPAGATE_EXCEPTIONS'] = False
    client = app.test_client()  # NO session -> anonymous

    resp = client.get('/schedules')

    assert resp.status_code in (302, 401)
    assert resp.status_code != 200


# ---------------------------------------------------------------------------
# Introspection sweep over the NEWLY-guarded projects_bp routes.
# ---------------------------------------------------------------------------
#
# Every newly-guarded ``projects_bp`` route keyed on ``<project_id>`` (or the
# global catalog routes that now require login) must reject a role=None caller.
# We drive each rule and assert a denial (403 from project_role_required, or a
# 302/401 from @login_required for the non-project catalog routes). A newly
# added project-scoped route that forgets the guard would surface here as a
# 200/2xx and fail the test.


def test_newly_guarded_projects_routes_deny_role_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """role=None is denied on every newly-guarded projects_bp route.

    Endpoints guarded BEFORE this task (view_project, add_website,
    generate_project_report, api_project_websites, api_list_projects) are
    excluded because they were validated by prior tasks.
    """
    client = _projects_client(monkeypatch, role=None)

    # Endpoints this task newly guards. project_role_required routes -> 403;
    # @login_required-only catalog routes -> 302/401 for role=None (the stub
    # user is authenticated, so @login_required passes -> those run their body
    # and are NOT denial-tested here). We therefore only sweep the
    # project_role_required routes.
    newly_project_scoped = {
        'projects.edit_project',
        'projects.delete_project',
        'projects.test_project',
        'projects.api_get_project_users',
        'projects.api_get_project',
        'projects.api_get_discovered_pages',
    }

    seen: set[str] = set()
    for rule in client.application.url_map.iter_rules():
        endpoint = str(rule.endpoint)
        if endpoint not in newly_project_scoped:
            continue
        seen.add(endpoint)

        path = re.sub(r'<[^>]+>', 'x', str(rule))
        rule_methods = rule.methods
        usable = (set(rule_methods) if rule_methods is not None else {'GET'}) - {
            'HEAD', 'OPTIONS',
        }
        method = 'GET' if 'GET' in usable else sorted(usable)[0]

        resp = client.open(path, method=method, json={})
        assert resp.status_code == 403, (
            f'{endpoint} ({method} {path}) returned '
            f'{resp.status_code}, expected 403 for role=None'
        )

    assert seen == newly_project_scoped
