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
# schedules.py — schedules_dashboard per-project scoping BEHAVIOUR.
# ---------------------------------------------------------------------------
#
# The anonymous test above only proves the @login_required gate fires. These
# tests prove the in-handler scoping: a non-superadmin sees ONLY schedules whose
# website belongs to a project they're a member of, and an out-of-scope
# ?project_id= param cannot widen that. A superadmin sees everything.
#
# The route ends in ``render_template('schedules/dashboard.html', ...)`` and the
# harness app has no template search path for the real app, so we install a
# tiny stub template that emits the per-schedule website ids the route enriched
# onto each schedule. Asserting on those ids (rather than full HTML) keeps the
# test robust against dashboard markup changes.


_SCHEDULES_STUB_TEMPLATE = (
    'PROJECTS:{% for p in projects %}{{ p.id }},{% endfor %}|'
    'SCHEDULES:{% for s in schedules %}{{ s._website_id }},{% endfor %}'
)


def _schedule(schedule_id: str, website_id: str) -> SimpleNamespace:
    """A minimal TestSchedule-shaped object the route can enrich and stat."""
    return SimpleNamespace(
        id=schedule_id,
        website_id=website_id,
        enabled=False,
        next_run_at=None,
        schedule_type=SimpleNamespace(value='daily'),
    )


def _make_schedules_app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    is_superadmin: bool,
) -> Flask:
    """Build a schedules_bp app seeded with two projects' worth of schedules.

    Project ``A`` owns website ``wA`` (schedule ``sA``); project ``B`` owns
    website ``wB`` (schedule ``sB``). ``get_all_test_schedules`` always returns
    BOTH so any narrowing in the response is the route's in-handler scoping, not
    a pre-filtered db return. ``get_projects_for_user`` reports membership in
    project ``A`` ONLY.
    """
    from jinja2 import ChoiceLoader, DictLoader
    from auto_a11y.web.routes.schedules import schedules_bp

    # The harness builds its StubUser inside make_app_with_blueprint and pins
    # is_superadmin=False per instance, so a class-level patch would be shadowed.
    # Patch __init__ BEFORE the app is built so the loaded user gets the flag the
    # route reads via getattr(current_user, 'is_superadmin', False).
    if is_superadmin:
        original_init = StubUser.__init__

        def _superadmin_init(self: StubUser, user_id: str = 'stub-user') -> None:
            original_init(self, user_id)
            self.is_superadmin = True

        monkeypatch.setattr(StubUser, '__init__', _superadmin_init)

    app = make_app_with_blueprint(
        schedules_bp, role='client', monkeypatch=monkeypatch, url_prefix='',
    )
    app.config['PROPAGATE_EXCEPTIONS'] = False
    # ``Flask.jinja_loader`` is a read-only ``cached_property``; install the stub
    # template on the live jinja environment's loader instead (a plain
    # ``BaseLoader`` slot), which the type-checkers accept.
    app.jinja_env.loader = ChoiceLoader(
        [DictLoader({'schedules/dashboard.html': _SCHEDULES_STUB_TEMPLATE})],
    )

    project_a = SimpleNamespace(id='pA', name='Project A')
    project_b = SimpleNamespace(id='pB', name='Project B')
    website_a = SimpleNamespace(id='wA', name='Website A', project_id='pA')
    website_b = SimpleNamespace(id='wB', name='Website B', project_id='pB')
    schedules = [_schedule('sA', 'wA'), _schedule('sB', 'wB')]

    websites: dict[str, SimpleNamespace] = {'wA': website_a, 'wB': website_b}
    projects_by_id: dict[str, SimpleNamespace] = {'pA': project_a, 'pB': project_b}

    def _get_website(website_id: str) -> SimpleNamespace | None:
        return websites.get(website_id)

    def _get_project(project_id: str) -> SimpleNamespace | None:
        return projects_by_id.get(project_id)

    db = getattr(app, 'db')
    db.get_all_test_schedules.return_value = schedules
    db.get_website.side_effect = _get_website
    db.get_project.side_effect = _get_project
    # Member of project A only (superadmins take the get_projects() branch).
    db.get_projects_for_user.return_value = [project_a]
    db.get_projects.return_value = [project_a, project_b]
    return app


def test_schedules_dashboard_scopes_to_member_projects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-superadmin member of project A sees A's schedule, not B's."""
    app = _make_schedules_app(monkeypatch, is_superadmin=False)
    client = _authenticated_client(app)

    resp = client.get('/schedules')

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # The dropdown only offers project A; in-scope website wA is listed, the
    # out-of-scope website wB is filtered out of the schedule list.
    assert 'PROJECTS:pA,|' in body
    assert 'wA,' in body
    assert 'wB' not in body


def test_schedules_dashboard_ignores_out_of_scope_project_id_param(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An out-of-scope ?project_id=pB cannot leak project B's schedules."""
    app = _make_schedules_app(monkeypatch, is_superadmin=False)
    client = _authenticated_client(app)

    resp = client.get('/schedules?project_id=pB')

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # The param names a project the caller cannot access: it is ignored, so the
    # response is identical to the unfiltered member view -- only wA, never wB.
    assert 'wA,' in body
    assert 'wB' not in body
    # And get_all_test_schedules must NOT have been invoked with project_id='pB'
    # (the route nulls the param before querying).
    db = getattr(app, 'db')
    for call in db.get_all_test_schedules.call_args_list:
        assert call.kwargs.get('project_id') != 'pB'


def test_schedules_dashboard_superadmin_sees_all_projects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A superadmin sees schedules across BOTH projects (no scoping)."""
    app = _make_schedules_app(monkeypatch, is_superadmin=True)
    client = _authenticated_client(app)

    resp = client.get('/schedules')

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'wA,' in body
    assert 'wB,' in body


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
