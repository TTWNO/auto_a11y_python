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
