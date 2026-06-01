"""Per-project authorization tests for ``scripts_bp`` (IDOR fix, Task 1.8).

The page-setup-scripts blueprint exposes nine routes, every one keyed on a
``<page_id>``, ``<website_id>``, or ``<script_id>`` URL param.
``project_role_required`` resolves each of those to the owning project via the
central ``resolve_project_id`` (which, for a ``script_id``, loads the
``PageSetupScript`` and follows its ``page_id`` -> page -> website, or its
``website_id`` -> website, to the project). The patched ``user_has_permission``
seam in the harness then decides the effective role.

Tiering:
* read/list (``list_page_scripts``, ``list_website_scripts``, ``view_script``)
  -> ADMIN / AUDITOR / CLIENT
* create / edit / toggle / test (``create_page_script``,
  ``create_website_script``, ``edit_script``, ``toggle_script``,
  ``test_script``) -> ADMIN / AUDITOR
* destructive (``delete_script``) -> ADMIN

Mongo is never touched: the harness app carries a ``MagicMock`` ``db`` so
``resolve_project_id`` returns a truthy project id for every param kind, and the
permission seam alone gates the request.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from flask import Flask
from flask.testing import FlaskClient

from auto_a11y.web.routes.scripts import scripts_bp

from tests.web._authz_helpers import Role, StubUser, make_app_with_blueprint


def _authenticated_client(app: Flask) -> FlaskClient:
    """Test client with the harness stub user seated in the session."""
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['_user_id'] = StubUser().id
    return client


def _scripts_client(
    monkeypatch: pytest.MonkeyPatch, role: Role | None,
) -> FlaskClient:
    """Authenticated test client for ``scripts_bp`` at ``/scripts``."""
    app = make_app_with_blueprint(
        scripts_bp, role=role, monkeypatch=monkeypatch, url_prefix='/scripts',
    )
    app.config['PROPAGATE_EXCEPTIONS'] = False
    return _authenticated_client(app)


# ---------------------------------------------------------------------------
# Read tier: view_script (and the list routes) admit admin/auditor/client.
# ---------------------------------------------------------------------------


def test_view_script_forbidden_without_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read route (view_script): no role -> 403."""
    client = _scripts_client(monkeypatch, role=None)

    resp = client.get('/scripts/script-abc')

    assert resp.status_code == 403


@pytest.mark.parametrize('role', ['admin', 'auditor', 'client'])
def test_view_script_allowed_for_authorized_roles(
    role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """view_script admits admin/auditor/client (read tier).

    The guard lets the request through; the body runs against the MagicMock db
    (a truthy script) and renders/redirects -- in any case NOT 403.
    """
    client = _scripts_client(monkeypatch, role=role)

    resp = client.get('/scripts/script-abc')

    assert resp.status_code != 403


def test_list_page_scripts_forbidden_without_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read route keyed on page_id (list_page_scripts): no role -> 403."""
    client = _scripts_client(monkeypatch, role=None)

    resp = client.get('/scripts/page/page-abc/scripts')

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Edit tier: create / test routes admit admin/auditor, reject client.
# ---------------------------------------------------------------------------


def test_create_page_script_forbidden_for_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_page_script is edit tier (ADMIN/AUDITOR): a client is rejected."""
    client = _scripts_client(monkeypatch, role='client')

    resp = client.get('/scripts/page/page-abc/scripts/create')

    assert resp.status_code == 403


@pytest.mark.parametrize('role', ['admin', 'auditor'])
def test_create_page_script_allowed_for_editors(
    role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_page_script admits admin and auditor (edit tier)."""
    client = _scripts_client(monkeypatch, role=role)

    resp = client.get('/scripts/page/page-abc/scripts/create')

    assert resp.status_code != 403


def test_test_script_forbidden_for_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """test_script is edit tier (ADMIN/AUDITOR): a client is rejected.

    Sent as JSON so the guard's JSON 403 branch fires; with role=client the
    body never runs (no browser launched).
    """
    client = _scripts_client(monkeypatch, role='client')

    resp = client.post('/scripts/script-abc/test', json={})

    assert resp.status_code == 403


def test_test_script_allowed_for_auditor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """test_script admits an auditor (edit tier): the guard lets it through."""
    client = _scripts_client(monkeypatch, role='auditor')

    resp = client.post('/scripts/script-abc/test', json={})

    assert resp.status_code != 403


# ---------------------------------------------------------------------------
# Destructive tier: delete_script is ADMIN-only.
# ---------------------------------------------------------------------------


def test_delete_script_forbidden_for_auditor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """delete_script is ADMIN-only: an auditor must be rejected."""
    client = _scripts_client(monkeypatch, role='auditor')

    resp = client.post('/scripts/script-abc/delete', json={})

    assert resp.status_code == 403


def test_delete_script_allowed_for_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """delete_script admits ADMIN: the guard lets the request through."""
    client = _scripts_client(monkeypatch, role='admin')

    resp = client.post('/scripts/script-abc/delete', json={})

    assert resp.status_code != 403


# ---------------------------------------------------------------------------
# Website-scoped script_id resolution path (from a prior code review).
# ---------------------------------------------------------------------------
#
# A website-level PageSetupScript has ``website_id`` set and ``page_id`` None.
# ``resolve_project_id`` must follow that branch: script -> website -> project.
# We seed the MagicMock db so ``get_page_setup_script`` returns such a script
# and ``get_website`` returns its owning project, then assert the guard keys on
# the resolved project id (role=None -> 403, admin -> not-403).


def _seed_website_scoped_script(app: Flask) -> None:
    """Make ``script-w`` resolve via website_id -> project 'p1' on ``app.db``."""
    db = getattr(app, 'db')
    db.get_page_setup_script.return_value = SimpleNamespace(
        page_id=None, website_id='w1',
    )
    db.get_website.return_value = SimpleNamespace(project_id='p1')


def test_view_script_website_scoped_resolution_forbidden_without_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """script_id with website_id (no page_id): resolves to project; no role -> 403."""
    app = make_app_with_blueprint(
        scripts_bp, role=None, monkeypatch=monkeypatch, url_prefix='/scripts',
    )
    app.config['PROPAGATE_EXCEPTIONS'] = False
    _seed_website_scoped_script(app)
    client = _authenticated_client(app)

    resp = client.get('/scripts/script-w')

    assert resp.status_code == 403


def test_view_script_website_scoped_resolution_allowed_for_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """script_id with website_id resolves to project 'p1'; admin -> not-403.

    Proves the website-scoped ``script_id`` branch of ``resolve_project_id`` is
    exercised: the guard grants access only because the resolved project id is
    one the admin tier holds permission on.
    """
    app = make_app_with_blueprint(
        scripts_bp, role='admin', monkeypatch=monkeypatch, url_prefix='/scripts',
    )
    app.config['PROPAGATE_EXCEPTIONS'] = False
    _seed_website_scoped_script(app)
    client = _authenticated_client(app)

    resp = client.get('/scripts/script-w')

    assert resp.status_code != 403


# ---------------------------------------------------------------------------
# Per-route introspection sweep: EVERY scripts_bp route 403s for role=None.
# ---------------------------------------------------------------------------


def test_every_scripts_route_enforces_authz(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-route introspection: EVERY scripts_bp route 403s for role=None.

    Iterate the url_map, fill each ``<param>`` with a dummy segment (the
    MagicMock db makes ``resolve_project_id`` truthy for page_id / website_id /
    script_id), drive an allowed method, and assert 403. Also assert the
    endpoint set equals exactly the nine documented routes -- so a newly added
    route that forgets the guard fails this test.
    """
    client = _scripts_client(monkeypatch, role=None)

    endpoints: set[str] = set()
    for rule in client.application.url_map.iter_rules():
        endpoint = str(rule.endpoint)
        if not endpoint.startswith('scripts.'):
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
        'scripts.list_page_scripts',
        'scripts.create_page_script',
        'scripts.create_website_script',
        'scripts.list_website_scripts',
        'scripts.view_script',
        'scripts.edit_script',
        'scripts.delete_script',
        'scripts.toggle_script',
        'scripts.test_script',
    }
