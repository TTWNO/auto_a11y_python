"""Per-project authorization tests for ``discovered_pages_bp`` (IDOR fix, Task 1.9).

The discovered-pages blueprint keys every route on a ``<page_id>`` URL param,
but that id is a **DiscoveredPage ObjectId**, NOT a regular ``Page`` id. The
central ``resolve_project_id`` would treat ``page_id`` as a regular page (via
``db.get_page``) and resolve the WRONG project, so these routes use the
dedicated ``discovered_page_role_required`` decorator, which resolves the
owning project via ``db.get_discovered_page_by_id(page_id).project_id``.

Tiering:
* read (``view_discovered_page``)            -> ADMIN / AUDITOR / CLIENT
* edit (``edit_discovered_page``)            -> ADMIN / AUDITOR
* destructive (``delete_discovered_page``)   -> ADMIN

Mongo is never touched: the harness app carries a ``MagicMock`` ``db``. We seed
``db.get_discovered_page_by_id`` to return an object exposing ``project_id`` so
the dedicated decorator resolves a project; the patched ``user_has_permission``
seam then decides the effective role.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from flask import Flask
from flask.testing import FlaskClient

from auto_a11y.web.routes.discovered_pages import discovered_pages_bp

from tests.web._authz_helpers import Role, StubUser, make_app_with_blueprint


def _authenticated_client(app: Flask) -> FlaskClient:
    """Test client with the harness stub user seated in the session."""
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['_user_id'] = StubUser().id
    return client


def _seed_discovered_page(app: Flask, project_id: str = 'p1') -> None:
    """Make ``db.get_discovered_page_by_id`` resolve to ``project_id``."""
    db = getattr(app, 'db')
    db.get_discovered_page_by_id.return_value = SimpleNamespace(project_id=project_id)


def _dp_app(monkeypatch: pytest.MonkeyPatch, role: Role | None) -> Flask:
    """Harness app for ``discovered_pages_bp`` with a resolvable discovered page."""
    app = make_app_with_blueprint(
        discovered_pages_bp, role=role, monkeypatch=monkeypatch,
    )
    app.config['PROPAGATE_EXCEPTIONS'] = False
    _seed_discovered_page(app)
    return app


def _dp_client(monkeypatch: pytest.MonkeyPatch, role: Role | None) -> FlaskClient:
    """Authenticated test client for ``discovered_pages_bp``."""
    return _authenticated_client(_dp_app(monkeypatch, role))


# ---------------------------------------------------------------------------
# Read tier: view_discovered_page admits admin/auditor/client.
# ---------------------------------------------------------------------------


def test_view_forbidden_without_role(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read route (view_discovered_page): no role -> 403."""
    client = _dp_client(monkeypatch, role=None)

    resp = client.get('/discovered-pages/dp-abc')

    assert resp.status_code == 403


@pytest.mark.parametrize('role', ['admin', 'auditor', 'client'])
def test_view_allowed_for_authorized_roles(
    role: Role, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """view_discovered_page admits admin/auditor/client (read tier): NOT 403."""
    client = _dp_client(monkeypatch, role=role)

    resp = client.get('/discovered-pages/dp-abc')

    assert resp.status_code != 403


# ---------------------------------------------------------------------------
# Edit tier: edit_discovered_page admits admin/auditor, rejects client.
# ---------------------------------------------------------------------------


def test_edit_forbidden_for_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """edit_discovered_page is edit tier (ADMIN/AUDITOR): a client is rejected."""
    client = _dp_client(monkeypatch, role='client')

    resp = client.post('/discovered-pages/dp-abc/edit', json={})

    assert resp.status_code == 403


def test_edit_allowed_for_auditor(monkeypatch: pytest.MonkeyPatch) -> None:
    """edit_discovered_page admits an auditor (edit tier): the guard lets it through."""
    client = _dp_client(monkeypatch, role='auditor')

    resp = client.post('/discovered-pages/dp-abc/edit', json={})

    assert resp.status_code != 403


# ---------------------------------------------------------------------------
# Destructive tier: delete_discovered_page is ADMIN-only.
# ---------------------------------------------------------------------------


def test_delete_forbidden_for_auditor(monkeypatch: pytest.MonkeyPatch) -> None:
    """delete_discovered_page is ADMIN-only: an auditor must be rejected."""
    client = _dp_client(monkeypatch, role='auditor')

    resp = client.post('/discovered-pages/dp-abc/delete', json={})

    assert resp.status_code == 403


def test_delete_allowed_for_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    """delete_discovered_page admits ADMIN: the guard lets the request through."""
    client = _dp_client(monkeypatch, role='admin')

    resp = client.post('/discovered-pages/dp-abc/delete', json={})

    assert resp.status_code != 403


# ---------------------------------------------------------------------------
# Resolution proof: the dedicated decorator keys on get_discovered_page_by_id,
# NOT on the regular-page accessor (get_page). Seed ONLY that accessor and
# confirm an admin passes; leave get_page returning a MagicMock that would
# resolve the WRONG project (or none) -- it must be irrelevant.
# ---------------------------------------------------------------------------


def test_resolution_uses_get_discovered_page_by_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Project resolution flows through get_discovered_page_by_id().project_id.

    Seed ONLY the discovered-page accessor to yield project 'p1'. With an admin
    (who holds permission on every project here) the guard must let the request
    through, and the discovered-page accessor must have been consulted with the
    URL's page_id.
    """
    app = make_app_with_blueprint(
        discovered_pages_bp, role='admin', monkeypatch=monkeypatch,
    )
    app.config['PROPAGATE_EXCEPTIONS'] = False
    db = getattr(app, 'db')
    db.get_discovered_page_by_id.return_value = SimpleNamespace(project_id='p1')
    client = _authenticated_client(app)

    resp = client.get('/discovered-pages/dp-xyz')

    assert resp.status_code != 403
    db.get_discovered_page_by_id.assert_called_with('dp-xyz')


def test_unresolvable_discovered_page_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the discovered page does not resolve to a project -> 403, even for admin-tier perms."""
    app = make_app_with_blueprint(
        discovered_pages_bp, role='admin', monkeypatch=monkeypatch,
    )
    app.config['PROPAGATE_EXCEPTIONS'] = False
    db = getattr(app, 'db')
    db.get_discovered_page_by_id.return_value = None
    client = _authenticated_client(app)

    # admin tier here is granted via permission seam, but with no project the
    # decorator cannot grant a per-project role -> 403. (Superadmin bypass is
    # NOT exercised: the stub user is a non-superadmin.)
    resp = client.get('/discovered-pages/dp-missing')

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Per-route introspection sweep: EVERY discovered_pages_bp route 403s for
# role=None. A new route that forgets the guard fails this test.
# ---------------------------------------------------------------------------


def test_every_discovered_pages_route_enforces_authz(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-route introspection: EVERY discovered_pages_bp route 403s for role=None."""
    client = _dp_client(monkeypatch, role=None)

    endpoints: set[str] = set()
    for rule in client.application.url_map.iter_rules():
        endpoint = str(rule.endpoint)
        if not endpoint.startswith('discovered_pages.'):
            continue
        endpoints.add(endpoint)

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

    assert endpoints == {
        'discovered_pages.view_discovered_page',
        'discovered_pages.edit_discovered_page',
        'discovered_pages.delete_discovered_page',
    }
