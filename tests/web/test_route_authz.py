"""Smoke test proving the route-authorization harness models the real
``@project_role_required`` decorator.

The harness lives in :mod:`tests.web._authz_helpers`. This test defines a
minimal blueprint whose single route is wrapped in the *production* decorator
``auto_a11y.web.routes.auth.project_role_required`` and uses a ``project_id``
URL param (so ``resolve_project_id`` returns it directly with no real DB
query). We then assert:

* ``role=None``      -> 403 (no effective role)
* an authorized role -> NOT 403 (the route body runs)

If this fails, the bug is in the HARNESS, not the production decorator.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from flask import Blueprint, Flask, jsonify
from flask.testing import FlaskClient
from werkzeug.wrappers import Response

from auto_a11y.models.app_user import UserRole
from auto_a11y.web.routes.auth import project_role_required
from auto_a11y.web.routes.pages import pages_bp
from auto_a11y.web.routes.project_users import project_users_bp
from auto_a11y.web.routes.recordings import recordings_bp
from auto_a11y.web.routes.websites import websites_bp

from tests.web._authz_helpers import StubUser, Role, make_app_with_blueprint


def _guarded_blueprint() -> Blueprint:
    """A blueprint with one JSON route guarded by the real decorator.

    The route has a ``project_id`` URL param and returns JSON on success, so
    the only thing distinguishing pass from fail is the decorator's 403.
    """
    bp = Blueprint('authz_smoke', __name__)

    def guarded(project_id: str) -> Response:
        return jsonify({'ok': True, 'project_id': project_id})

    guard = project_role_required(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
    )
    bp.add_url_rule('/smoke/<project_id>', view_func=guard(guarded))
    return bp


def _authenticated_client(app: Flask) -> FlaskClient:
    """Test client with the harness stub user seated in the session."""
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['_user_id'] = StubUser().id
    return client


def test_guarded_route_forbids_when_no_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = make_app_with_blueprint(
        _guarded_blueprint(), role=None, monkeypatch=monkeypatch,
    )
    client = _authenticated_client(app)

    resp = client.get('/smoke/proj-123')

    assert resp.status_code == 403


@pytest.mark.parametrize('role', ['admin', 'auditor', 'client'])
def test_guarded_route_allows_authorized_role(
    role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = make_app_with_blueprint(
        _guarded_blueprint(), role=role, monkeypatch=monkeypatch,
    )
    client = _authenticated_client(app)

    resp = client.get('/smoke/proj-123')

    # The decorator let the request through: status is NOT 403 and the JSON
    # body the route returns is present.
    assert resp.status_code != 403
    assert resp.status_code == 200
    assert resp.get_json() == {'ok': True, 'project_id': 'proj-123'}


def test_unauthenticated_request_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No session -> the decorator's unauthenticated branch fires.

    Sent as JSON so the decorator returns a 401 JSON body rather than
    redirecting to ``auth.login`` (which isn't registered in the harness).
    """
    app = make_app_with_blueprint(
        _guarded_blueprint(), role='admin', monkeypatch=monkeypatch,
    )
    client = app.test_client()  # no session => anonymous

    resp = client.get('/smoke/proj-123', headers={'Accept': 'application/json'},
                      content_type='application/json')

    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Task 1.3: pages_bp per-project authorization (IDOR fix)
# ---------------------------------------------------------------------------
#
# Every pages_bp route takes a ``page_id`` URL param. In the harness the
# MagicMock ``db`` makes ``resolve_project_id`` return a (truthy MagicMock)
# project id without touching real Mongo, so the patched ``user_has_permission``
# seam alone decides the effective role: ``role=None`` -> 403, granted -> not 403.


def _pages_client(monkeypatch: pytest.MonkeyPatch, role: Role | None) -> FlaskClient:
    """Authenticated test client for ``pages_bp`` at ``/pages`` prefix.

    Exception propagation is disabled so that when an *authorized* request
    passes the guard and runs the view body, any error raised by the body
    against the MagicMock db (e.g. a ``url_for`` ``BuildError`` for an
    endpoint that lives in another, unregistered blueprint) is converted to a
    500 response instead of bubbling out of the test client. The guard's own
    403 is returned before the body runs, so it is never masked by this.
    """
    app = make_app_with_blueprint(
        pages_bp, role=role, monkeypatch=monkeypatch, url_prefix='/pages',
    )
    app.config['PROPAGATE_EXCEPTIONS'] = False
    return _authenticated_client(app)


def test_pages_view_forbidden_without_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read route (view_page) IDOR guard: no role -> 403, body never runs."""
    client = _pages_client(monkeypatch, role=None)

    resp = client.get('/pages/page-abc')

    assert resp.status_code == 403


@pytest.mark.parametrize('role', ['admin', 'auditor', 'client'])
def test_pages_view_allowed_for_authorized_roles(
    role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """view_page admits admin/auditor/client (read tier).

    A non-403 status proves the guard let the request through to the body
    (which then 404s/500s against the MagicMock db -- that's fine here).
    """
    client = _pages_client(monkeypatch, role=role)

    resp = client.get('/pages/page-abc')

    assert resp.status_code != 403


def test_pages_delete_forbidden_without_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Destructive route (delete_page) IDOR guard: no role -> 403."""
    client = _pages_client(monkeypatch, role=None)

    resp = client.post('/pages/page-abc/delete')

    assert resp.status_code == 403


def test_pages_delete_forbidden_for_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """delete_page is ADMIN-only: a client (read tier) must be rejected."""
    client = _pages_client(monkeypatch, role='client')

    resp = client.post('/pages/page-abc/delete')

    assert resp.status_code == 403


def test_pages_delete_allowed_for_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """delete_page admits ADMIN: guard lets the request through (not 403)."""
    client = _pages_client(monkeypatch, role='admin')

    resp = client.post('/pages/page-abc/delete')

    assert resp.status_code != 403


def test_every_pages_route_is_guarded() -> None:
    """Inspection: every pages_bp view is wrapped by project_role_required.

    The decorator uses ``functools.wraps``, so a guarded view exposes a
    ``__wrapped__`` attribute pointing at the original module function. An
    unguarded route would register the bare function (no ``__wrapped__``).
    """
    app = Flask(__name__)
    setattr(app, 'db', None)
    app.register_blueprint(pages_bp, url_prefix='/pages')

    pages_endpoints = [
        (rule.endpoint, view)
        for rule, view in (
            (rule, app.view_functions[rule.endpoint])
            for rule in app.url_map.iter_rules()
            if rule.endpoint.startswith('pages.')
        )
    ]
    assert pages_endpoints, 'no pages_bp routes registered'

    unguarded = [
        endpoint
        for endpoint, view in pages_endpoints
        if not hasattr(view, '__wrapped__')
    ]
    assert not unguarded, f'unguarded pages_bp routes: {unguarded}'


# ---------------------------------------------------------------------------
# Task 1.4: websites_bp per-project authorization (IDOR fix)
# ---------------------------------------------------------------------------
#
# Every websites_bp route except ``api_list_websites`` takes a ``website_id``
# URL param. ``resolve_project_id`` turns that into the owning project's id by
# looking it up via the MagicMock db, then the patched ``user_has_permission``
# seam decides the effective role. ``api_list_websites`` has no resource to
# resolve and is instead protected by filtering its result to the current
# user's accessible projects (verified separately).


def _websites_client(
    monkeypatch: pytest.MonkeyPatch, role: Role | None,
) -> FlaskClient:
    """Authenticated test client for ``websites_bp`` at ``/websites`` prefix."""
    app = make_app_with_blueprint(
        websites_bp, role=role, monkeypatch=monkeypatch, url_prefix='/websites',
    )
    app.config['PROPAGATE_EXCEPTIONS'] = False
    return _authenticated_client(app)


def test_websites_view_forbidden_without_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read route (view_website) IDOR guard: no role -> 403."""
    client = _websites_client(monkeypatch, role=None)

    resp = client.get('/websites/web-abc')

    assert resp.status_code == 403


@pytest.mark.parametrize('role', ['admin', 'auditor', 'client'])
def test_websites_test_status_allowed_for_authorized_roles(
    role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """test_status (read tier) admits admin/auditor/client.

    A read-tier route with a fast body (no template render / heavyweight
    work) so a non-403 cleanly proves the guard let the request through.
    """
    client = _websites_client(monkeypatch, role=role)

    resp = client.get('/websites/web-abc/test-status')

    assert resp.status_code != 403


def test_websites_clear_results_forbidden_without_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Destructive route (clear_test_results) IDOR guard: no role -> 403."""
    client = _websites_client(monkeypatch, role=None)

    resp = client.post('/websites/web-abc/clear-test-results')

    assert resp.status_code == 403


def test_websites_clear_results_forbidden_for_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """clear_test_results is ADMIN-only: a client must be rejected."""
    client = _websites_client(monkeypatch, role='client')

    resp = client.post('/websites/web-abc/clear-test-results')

    assert resp.status_code == 403


def test_websites_clear_results_forbidden_for_auditor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """clear_test_results is ADMIN-only: an auditor must be rejected."""
    client = _websites_client(monkeypatch, role='auditor')

    resp = client.post('/websites/web-abc/clear-test-results')

    assert resp.status_code == 403


def test_websites_clear_results_allowed_for_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """clear_test_results admits ADMIN: guard lets the request through."""
    client = _websites_client(monkeypatch, role='admin')

    resp = client.post('/websites/web-abc/clear-test-results')

    assert resp.status_code != 403


def test_websites_discover_forbidden_for_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """discover_pages is edit tier (ADMIN/AUDITOR): a client is rejected.

    The guard fires before the body, so the discovery job is never queued
    (asserting on the body would spawn real crawl work; we only need the 403).
    """
    client = _websites_client(monkeypatch, role='client')

    resp = client.post('/websites/web-abc/discover')

    assert resp.status_code == 403


def test_websites_add_page_forbidden_for_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """add_page is edit tier (ADMIN/AUDITOR): a client is rejected."""
    client = _websites_client(monkeypatch, role='client')

    resp = client.post('/websites/web-abc/add-page')

    assert resp.status_code == 403


def test_websites_add_page_allowed_for_auditor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """add_page admits AUDITOR (edit tier).

    With no ``url`` form field the body returns a fast 400, proving the guard
    let the request through without doing any heavyweight work.
    """
    client = _websites_client(monkeypatch, role='auditor')

    resp = client.post('/websites/web-abc/add-page')

    assert resp.status_code != 403


def test_api_list_websites_scopes_to_accessible_projects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``api_list_websites`` must not leak websites from other tenants.

    Two websites live in two different projects; the stub user is a member of
    only one. The endpoint must return only the website in the accessible
    project, with the JSON shape unchanged.
    """
    app = make_app_with_blueprint(
        websites_bp, role='client', monkeypatch=monkeypatch,
        url_prefix='/websites',
    )

    accessible = SimpleNamespace(
        id='web-1', name='Accessible', url='https://a.example',
        project_id='proj-accessible',
    )
    hidden = SimpleNamespace(
        id='web-2', name='Hidden', url='https://b.example',
        project_id='proj-hidden',
    )
    accessible_project = SimpleNamespace(
        id='proj-accessible', name='Accessible', description='',
    )

    db = getattr(app, 'db')
    db.get_all_websites.return_value = [accessible, hidden]
    db.get_projects_for_user.return_value = [accessible_project]

    client = _authenticated_client(app)
    resp = client.get('/websites/api/list')

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['success'] is True
    returned_ids = {w['id'] for w in body['websites']}
    assert returned_ids == {'web-1'}
    # Shape unchanged: each entry still carries the same keys.
    assert set(body['websites'][0].keys()) == {
        'id', 'name', 'url', 'project_id',
    }


def test_every_websites_route_is_guarded() -> None:
    """Every websites_bp view is wrapped by project_role_required.

    ``api_list_websites`` is the sole exception: it has no per-resource id to
    resolve and is instead protected by filtering its result to the user's
    accessible projects, so it is excluded from the wrapping requirement.
    """
    app = Flask(__name__)
    setattr(app, 'db', None)
    app.register_blueprint(websites_bp, url_prefix='/websites')

    websites_endpoints = [
        (rule.endpoint, view)
        for rule, view in (
            (rule, app.view_functions[rule.endpoint])
            for rule in app.url_map.iter_rules()
            if rule.endpoint.startswith('websites.')
        )
    ]
    assert websites_endpoints, 'no websites_bp routes registered'

    unguarded = [
        endpoint
        for endpoint, view in websites_endpoints
        if not hasattr(view, '__wrapped__')
        and endpoint != 'websites.api_list_websites'
    ]
    assert not unguarded, f'unguarded websites_bp routes: {unguarded}'


# ---------------------------------------------------------------------------
# Task 1.5: project_users_bp per-project authorization (IDOR fix)
# ---------------------------------------------------------------------------
#
# project_users_bp manages stored test-site login CREDENTIALS, so it uses the
# stricter tiering: NO CLIENT access on any route. Routes are keyed on either
# ``project_id`` (list_users, create_user) or ``user_id`` (view/edit/delete/
# test-login/toggle/clear-cache). ``resolve_project_id`` turns ``user_id`` into
# the owning project's id by looking it up via the MagicMock db (truthy), then
# the patched ``user_has_permission`` seam decides the effective role.
# Destructive/state-changing routes (delete_user, toggle_user, clear_cache) are
# ADMIN-only; the rest are ADMIN/AUDITOR.


def _project_users_client(
    monkeypatch: pytest.MonkeyPatch, role: Role | None,
) -> FlaskClient:
    """Authenticated test client for ``project_users_bp`` (no url_prefix).

    The blueprint declares full ``/projects/...`` paths itself, so it is
    registered without a prefix. Exception propagation is disabled so an
    authorized request that passes the guard and 404s/500s against the
    MagicMock db yields a non-403 status rather than bubbling out.
    """
    app = make_app_with_blueprint(
        project_users_bp, role=role, monkeypatch=monkeypatch,
    )
    app.config['PROPAGATE_EXCEPTIONS'] = False
    return _authenticated_client(app)


def test_project_users_list_forbidden_without_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """project_id-scoped route (list_users): no role -> 403."""
    client = _project_users_client(monkeypatch, role=None)

    resp = client.get('/projects/proj-abc/users')

    assert resp.status_code == 403


def test_project_users_list_forbidden_for_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """list_users is credential-tier (ADMIN/AUDITOR): a client is rejected."""
    client = _project_users_client(monkeypatch, role='client')

    resp = client.get('/projects/proj-abc/users')

    assert resp.status_code == 403


def test_project_users_list_allowed_for_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """list_users admits ADMIN: guard lets the request through (not 403)."""
    client = _project_users_client(monkeypatch, role='admin')

    resp = client.get('/projects/proj-abc/users')

    assert resp.status_code != 403


def test_project_users_view_forbidden_without_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """user_id-scoped route (view_user): no role -> 403."""
    client = _project_users_client(monkeypatch, role=None)

    resp = client.get('/projects/users/user-abc')

    assert resp.status_code == 403


def test_project_users_view_forbidden_for_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """view_user is credential-tier (ADMIN/AUDITOR): a client is rejected.

    Credential routes expose stored login secrets, so CLIENT never has access.
    """
    client = _project_users_client(monkeypatch, role='client')

    resp = client.get('/projects/users/user-abc')

    assert resp.status_code == 403


def test_project_users_view_allowed_for_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """view_user admits ADMIN: guard lets the request through (not 403)."""
    client = _project_users_client(monkeypatch, role='admin')

    resp = client.get('/projects/users/user-abc')

    assert resp.status_code != 403


def test_project_users_delete_forbidden_for_auditor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """delete_user is ADMIN-only: an auditor must be rejected."""
    client = _project_users_client(monkeypatch, role='auditor')

    resp = client.post('/projects/users/user-abc/delete')

    assert resp.status_code == 403


def test_project_users_delete_forbidden_for_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """delete_user is ADMIN-only: a client must be rejected."""
    client = _project_users_client(monkeypatch, role='client')

    resp = client.post('/projects/users/user-abc/delete')

    assert resp.status_code == 403


def test_project_users_delete_allowed_for_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """delete_user admits ADMIN: guard lets the request through (not 403)."""
    client = _project_users_client(monkeypatch, role='admin')

    resp = client.post('/projects/users/user-abc/delete')

    assert resp.status_code != 403


def test_every_project_users_route_enforces_authz(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-route introspection: EVERY project_users_bp route 403s for role=None.

    Stronger than a ``__wrapped__`` check: it proves real authorization runs on
    every route. We iterate the url_map, turn each rule's path pattern into a
    concrete URL by filling every ``<param>`` placeholder with a dummy value
    (the MagicMock db makes ``resolve_project_id`` truthy for both
    ``project_id`` and ``user_id``), drive an allowed method, and assert 403.
    role=None never reaches the view body, so this is safe to drive for real.

    If a new credential route is added without a guard it will return non-403
    here and fail -- this catches additions the per-route tests above don't.
    """
    import re

    client = _project_users_client(monkeypatch, role=None)

    endpoints: set[str] = set()
    for rule in client.application.url_map.iter_rules():
        endpoint = str(rule.endpoint)
        if not endpoint.startswith('project_users.'):
            continue
        endpoints.add(endpoint)

        # ``str(rule)`` is the path pattern, e.g. ``/projects/users/<user_id>``.
        # Replace each ``<...>`` placeholder with a concrete dummy segment.
        path = re.sub(r'<[^>]+>', 'x', str(rule))

        # ``rule.methods`` may be ``None``; pick a concrete, non-automatic verb.
        rule_methods = rule.methods
        usable = (set(rule_methods) if rule_methods is not None else {'GET'}) - {
            'HEAD', 'OPTIONS',
        }
        method = 'GET' if 'GET' in usable else sorted(usable)[0]

        resp = client.open(path, method=method)
        assert resp.status_code == 403, (
            f'{endpoint} ({method} {path}) returned '
            f'{resp.status_code}, expected 403 for role=None'
        )

    # Every documented credential route was exercised; if this set ever grows,
    # a contributor must add its guard (or this test fails for the new route).
    assert endpoints == {
        'project_users.list_users',
        'project_users.create_user',
        'project_users.view_user',
        'project_users.edit_user',
        'project_users.delete_user',
        'project_users.test_login',
        'project_users.toggle_user',
        'project_users.clear_cache',
    }


# ---------------------------------------------------------------------------
# Task 1.6: recordings_bp per-project authorization (IDOR fix)
# ---------------------------------------------------------------------------
#
# Recordings are project-scoped manual-audit artefacts. Routes are keyed on
# ``recording_id`` (view/process/cancel/delete/callouts/issues), ``project_id``
# (combined view), or ``issue_id`` (update-issue-status). ``resolve_project_id``
# turns any of those into the owning project's id via the MagicMock db (truthy),
# then the patched ``user_has_permission`` seam decides the effective role.
#
# Tiering: read/download (view, combined, callouts, issues list) ->
# ADMIN/AUDITOR/CLIENT; state-changing (process, cancel, issue-status) ->
# ADMIN/AUDITOR; destructive (delete) -> ADMIN.
#
# Routes with NO resolvable project id in URL kwargs -- the all-recordings
# listings (``list_recordings``, ``api_list_recordings``) and the upload flows
# (``upload_recording``, ``upload_video``, ``upload_json``) -- cannot use
# ``project_role_required``. The listings are scoped to the user's accessible
# projects (verified separately); the uploads are ``@login_required`` and
# resolve/validate the target project inside the handler from a form field.

_RECORDINGS_UNRESOLVABLE = {
    'recordings.list_recordings',
    'recordings.api_list_recordings',
    'recordings.upload_recording',
    'recordings.upload_video',
    'recordings.upload_json',
}


def _recordings_client(
    monkeypatch: pytest.MonkeyPatch, role: Role | None,
) -> FlaskClient:
    """Authenticated test client for ``recordings_bp`` at ``/recordings``."""
    app = make_app_with_blueprint(
        recordings_bp, role=role, monkeypatch=monkeypatch,
        url_prefix='/recordings',
    )
    app.config['PROPAGATE_EXCEPTIONS'] = False
    return _authenticated_client(app)


def test_recordings_view_forbidden_without_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read route (view_recording) IDOR guard: no role -> 403."""
    client = _recordings_client(monkeypatch, role=None)

    resp = client.get('/recordings/rec-abc')

    assert resp.status_code == 403


@pytest.mark.parametrize('role', ['admin', 'auditor', 'client'])
def test_recordings_issues_api_allowed_for_authorized_roles(
    role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """api_recording_issues admits admin/auditor/client (read tier).

    Read-tier route keyed on ``recording_id`` whose body fails fast against
    the MagicMock db (a non-403 status), so it cleanly proves the guard let
    the request through without running the heavyweight detail-page scorer.
    """
    client = _recordings_client(monkeypatch, role=role)

    resp = client.get('/recordings/api/rec-abc/issues')

    assert resp.status_code != 403


def test_recordings_process_forbidden_without_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """State-changing route (process_recording): no role -> 403."""
    client = _recordings_client(monkeypatch, role=None)

    resp = client.post('/recordings/rec-abc/process')

    assert resp.status_code == 403


def test_recordings_process_forbidden_for_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """process_recording is edit tier (ADMIN/AUDITOR): a client is rejected."""
    client = _recordings_client(monkeypatch, role='client')

    resp = client.post('/recordings/rec-abc/process')

    assert resp.status_code == 403


def test_recordings_cancel_forbidden_for_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cancel_recording is edit tier (ADMIN/AUDITOR): a client is rejected."""
    client = _recordings_client(monkeypatch, role='client')

    resp = client.post('/recordings/rec-abc/cancel')

    assert resp.status_code == 403


def test_recordings_delete_forbidden_for_auditor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """delete_recording is ADMIN-only: an auditor must be rejected."""
    client = _recordings_client(monkeypatch, role='auditor')

    resp = client.post('/recordings/rec-abc/delete')

    assert resp.status_code == 403


def test_recordings_delete_allowed_for_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """delete_recording admits ADMIN: guard lets the request through."""
    client = _recordings_client(monkeypatch, role='admin')

    resp = client.post('/recordings/rec-abc/delete')

    assert resp.status_code != 403


def test_recordings_issue_status_keyed_on_issue_id_forbidden_without_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """api_update_issue_status resolves via issue_id: no role -> 403.

    Sent as JSON so the guard's JSON 403 branch fires (and so the body, if it
    ran, would read ``request.json``). With role=None the body never runs.
    """
    client = _recordings_client(monkeypatch, role=None)

    resp = client.post(
        '/recordings/api/issue/issue-abc/status',
        json={'status': 'open'},
    )

    assert resp.status_code == 403


def test_recordings_issue_status_forbidden_for_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """api_update_issue_status is edit tier (ADMIN/AUDITOR): client rejected."""
    client = _recordings_client(monkeypatch, role='client')

    resp = client.post(
        '/recordings/api/issue/issue-abc/status',
        json={'status': 'open'},
    )

    assert resp.status_code == 403


def test_api_list_recordings_scopes_to_accessible_projects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``api_list_recordings`` must not leak recordings from other tenants.

    Two recordings live in two different projects; the stub user is a member of
    only one. The endpoint must return only the recording in the accessible
    project, with the JSON shape unchanged.
    """
    from auto_a11y.models import RecordingType

    app = make_app_with_blueprint(
        recordings_bp, role='client', monkeypatch=monkeypatch,
        url_prefix='/recordings',
    )

    accessible = SimpleNamespace(
        id='rec-1', recording_id='REC-A', title='Accessible',
        auditor_name='a', auditor_role='r',
        recording_type=RecordingType.AUDIT, total_issues=0,
        high_impact_count=0, medium_impact_count=0, low_impact_count=0,
        duration=0, recorded_date=None, project_id='proj-accessible',
    )
    hidden = SimpleNamespace(
        id='rec-2', recording_id='REC-B', title='Hidden',
        auditor_name='a', auditor_role='r',
        recording_type=RecordingType.AUDIT, total_issues=0,
        high_impact_count=0, medium_impact_count=0, low_impact_count=0,
        duration=0, recorded_date=None, project_id='proj-hidden',
    )
    accessible_project = SimpleNamespace(
        id='proj-accessible', name='Accessible', description='',
    )

    db = getattr(app, 'db')
    db.get_recordings.return_value = [accessible, hidden]
    db.get_projects_for_user.return_value = [accessible_project]

    client = _authenticated_client(app)
    resp = client.get('/recordings/api/list')

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['success'] is True
    returned_ids = {r['id'] for r in body['recordings']}
    assert returned_ids == {'rec-1'}


def test_every_recordings_route_enforces_authz(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-route introspection: EVERY resolvable recordings_bp route 403s for
    role=None.

    Iterate the url_map, fill each ``<param>`` with a dummy segment (the
    MagicMock db makes ``resolve_project_id`` truthy for recording_id /
    project_id / issue_id), drive an allowed method, and assert 403. Routes
    with no resolvable project id (the all-recordings listings + upload flows)
    are excluded -- they are ``@login_required`` and scoped/validated inside
    the handler -- but are still asserted to exist in the endpoint set.
    """
    import re

    client = _recordings_client(monkeypatch, role=None)

    endpoints: set[str] = set()
    for rule in client.application.url_map.iter_rules():
        endpoint = str(rule.endpoint)
        if not endpoint.startswith('recordings.'):
            continue
        endpoints.add(endpoint)

        if endpoint in _RECORDINGS_UNRESOLVABLE:
            continue

        path = re.sub(r'<[^>]+>', 'x', str(rule))
        rule_methods = rule.methods
        usable = (set(rule_methods) if rule_methods is not None else {'GET'}) - {
            'HEAD', 'OPTIONS',
        }
        method = 'GET' if 'GET' in usable else sorted(usable)[0]

        # JSON content-type so the issue-status POST body (if ever reached)
        # reads cleanly; role=None means the guard fires before the body.
        resp = client.open(path, method=method, json={})
        assert resp.status_code == 403, (
            f'{endpoint} ({method} {path}) returned '
            f'{resp.status_code}, expected 403 for role=None'
        )

    assert endpoints == {
        'recordings.list_recordings',
        'recordings.view_recording',
        'recordings.view_combined_recordings',
        'recordings.upload_recording',
        'recordings.upload_video',
        'recordings.upload_json',
        'recordings.process_recording',
        'recordings.download_callouts_video',
        'recordings.cancel_recording',
        'recordings.delete_recording',
        'recordings.api_list_recordings',
        'recordings.api_recording_issues',
        'recordings.api_update_issue_status',
    }
