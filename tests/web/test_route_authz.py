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

import pytest
from flask import Blueprint, Flask, jsonify
from flask.testing import FlaskClient
from werkzeug.wrappers import Response

from auto_a11y.models.app_user import UserRole
from auto_a11y.web.routes.auth import project_role_required
from auto_a11y.web.routes.pages import pages_bp

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
