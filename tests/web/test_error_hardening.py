"""Error-hardening tests (Task 1.12).

Broad ``except Exception as e:`` handlers across the project/website/user/page
route blueprints used to put the raw ``str(e)`` into the client-facing JSON or
flash response, leaking internal detail (file paths, Mongo errors, stack
fragments). These tests pin the fix: an internal exception carrying a
recognizable secret marker must NOT reach the client; the response must instead
carry a generic, translated message.

We drive two representative JSON endpoints whose handler body runs for any
authenticated user (``@login_required`` only, no per-project guard):

* ``projects.api_list_projects`` (``GET /projects/api/list``)
* ``websites.api_list_websites`` (``GET /websites/api/list``)

The shared route-authz harness builds a Mongo-free Flask app with a MagicMock
``db``; we make the db method the handler calls raise an exception containing a
secret marker, then assert it is not echoed back.
"""
from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient

from auto_a11y.web.fluent import ftl
from auto_a11y.web.routes.projects import projects_bp
from auto_a11y.web.routes.websites import websites_bp

from tests.web._authz_helpers import Role, StubUser, make_app_with_blueprint

#: A marker that must never appear in a client-facing response. Mimics the kind
#: of internal detail a real exception (file path / Mongo error) would carry.
_SECRET = "SECRET_INTERNAL_DETAIL /etc/passwd"

#: The Fluent id the hardened handlers resolve for the generic user-facing
#: message. We resolve it to its translated value inside an app context (where
#: Fluent is initialised) rather than at import time -- ``ftl`` returns the id
#: verbatim until ``init_fluent`` has run.
_GENERIC_ID = 'common-unexpected-error'


def _resolve_generic(app: Flask) -> str:
    """Resolve the generic message id within the (Fluent-initialised) app."""
    with app.app_context():
        return ftl(_GENERIC_ID)


def _authenticated_client(app: Flask) -> FlaskClient:
    """Test client with the harness stub user seated in the session."""
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['_user_id'] = StubUser().id
    return client


def _client_for(
    blueprint: object,
    url_prefix: str,
    monkeypatch: pytest.MonkeyPatch,
    role: Role | None,
) -> FlaskClient:
    from flask import Blueprint
    assert isinstance(blueprint, Blueprint)
    app = make_app_with_blueprint(
        blueprint, role=role, monkeypatch=monkeypatch, url_prefix=url_prefix,
    )
    app.config['PROPAGATE_EXCEPTIONS'] = False
    return _authenticated_client(app)


def test_api_list_projects_does_not_leak_internal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """projects.api_list_projects masks an internal exception."""
    client = _client_for(projects_bp, '/projects', monkeypatch, role=None)
    db = getattr(client.application, 'db')
    db.get_projects_for_user.side_effect = Exception(_SECRET)

    resp = client.get('/projects/api/list')

    assert resp.status_code == 500
    body = resp.get_data(as_text=True)
    assert _SECRET not in body
    assert "/etc/passwd" not in body
    payload = resp.get_json()
    assert payload is not None
    assert payload.get('success') is False
    assert payload.get('error') == _resolve_generic(client.application)


def test_api_list_websites_does_not_leak_internal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """websites.api_list_websites masks an internal exception."""
    client = _client_for(websites_bp, '/websites', monkeypatch, role=None)
    db = getattr(client.application, 'db')
    db.get_all_websites.side_effect = Exception(_SECRET)

    resp = client.get('/websites/api/list')

    assert resp.status_code == 500
    body = resp.get_data(as_text=True)
    assert _SECRET not in body
    assert "/etc/passwd" not in body
    payload = resp.get_json()
    assert payload is not None
    assert payload.get('success') is False
    assert payload.get('error') == _resolve_generic(client.application)
