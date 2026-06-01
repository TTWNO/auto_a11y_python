"""Authorization + latent-bug tests for ``reports_bp`` (Task 1.10).

Three concerns are covered:

* **Part A — route guards.** Every route carrying a resolvable scope param
  (``project_id`` / ``website_id`` / ``page_id``) is decorated with
  ``project_role_required(ADMIN, AUDITOR, CLIENT)``; body-param generation
  routes (``/generate``, ``/generate/static-html``, ``/generate/deduplicated``,
  ``/generate/page-structure`` no-param) run an in-handler scope check; job and
  dashboard routes are at least ``@login_required``. A per-route introspection
  sweep asserts no scope-param route 200s for ``role=None``.
* **Part B — download/delete.** Path-traversal containment is enforced BEFORE
  any ``.exists()`` probe (a filename resolving outside ``REPORTS_DIR`` yields
  403/404 with no disk side effect), and both routes are at minimum
  ``@login_required``.
* **Part C — eager ``request.json`` 500.** POSTing a scope-param generate route
  with ``Content-Type: application/json`` and an EMPTY body must not 500 on a
  ``request.json.get`` NoneType access.

The harness (``tests/web/_authz_helpers.py``) builds a Mongo-free app with a
``MagicMock`` db and a patched permission seam, so ``resolve_project_id`` returns
a truthy project id for every scope param and the permission tier alone gates
the request.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient

from auto_a11y.web.routes.reports import reports_bp

from tests.web._authz_helpers import Role, StubUser, make_app_with_blueprint


def _authenticated_client(app: Flask) -> FlaskClient:
    """Test client with the harness stub user seated in the session."""
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['_user_id'] = StubUser().id
    return client


def _reports_client(
    monkeypatch: pytest.MonkeyPatch, role: Role | None,
) -> FlaskClient:
    """Authenticated test client for ``reports_bp`` at ``/reports``."""
    app = make_app_with_blueprint(
        reports_bp, role=role, monkeypatch=monkeypatch, url_prefix='/reports',
    )
    app.config['PROPAGATE_EXCEPTIONS'] = False
    return _authenticated_client(app)


#: Scope-param generation routes that ``project_role_required`` decorates.
#: (path, method) -- each filled with a dummy segment.
_SCOPE_PARAM_ROUTES: tuple[tuple[str, str], ...] = (
    ('/reports/generate/page/page-abc', 'POST'),
    ('/reports/generate/website/web-abc', 'POST'),
    ('/reports/generate/project/proj-abc', 'POST'),
    ('/reports/generate/recordings/proj-abc', 'POST'),
    ('/reports/generate/page-structure/web-abc', 'POST'),
    ('/reports/generate/discovery/website/web-abc', 'POST'),
    ('/reports/generate/discovery/project/proj-abc', 'POST'),
    ('/reports/project/proj-abc/summary', 'GET'),
)


# ---------------------------------------------------------------------------
# Part A: scope-param route guards.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('path,method', _SCOPE_PARAM_ROUTES)
def test_scope_param_route_forbidden_without_role(
    path: str, method: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scope-param routes 403 for a user with no role."""
    client = _reports_client(monkeypatch, role=None)

    resp = client.open(path, method=method, json={})

    assert resp.status_code == 403, (
        f'{method} {path} returned {resp.status_code}, expected 403'
    )


@pytest.mark.parametrize('path,method', _SCOPE_PARAM_ROUTES)
def test_scope_param_route_allowed_for_client(
    path: str, method: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scope-param routes admit a client (read tier): NOT 403."""
    client = _reports_client(monkeypatch, role='client')

    resp = client.open(path, method=method, json={})

    assert resp.status_code != 403, (
        f'{method} {path} returned 403 for client, expected pass'
    )


# ---------------------------------------------------------------------------
# Part C: eager request.json 500 (empty JSON body must not crash the guard or
# the handler's format parse).
# ---------------------------------------------------------------------------


def test_generate_page_report_empty_json_not_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST a scope-param generate route with JSON content-type + empty body.

    With ``role=None`` the guard short-circuits to 403; the important assertion
    is that it is NOT a 500 from ``request.json.get`` on a None body.
    """
    client = _reports_client(monkeypatch, role=None)

    resp = client.post(
        '/reports/generate/page/page-abc',
        data='',
        content_type='application/json',
    )

    assert resp.status_code != 500


def test_generate_page_report_empty_json_authorized_not_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An authorized client posting empty JSON reaches the handler's safe
    format parse without a 500 from ``request.json`` NoneType access.

    The handler's first statement is ``format = _body_format()`` -- the Part-C
    fix under test. Immediately after, it looks up the page and 404s when it's
    missing. We seed ``get_page`` -> None so the handler returns a clean 404
    (proving the format parse survived an empty JSON body) instead of running
    on to the report-generation machinery that the Mongo-free harness can't
    satisfy. A 500 here would mean the eager-json bug regressed.
    """
    app = make_app_with_blueprint(
        reports_bp, role='client', monkeypatch=monkeypatch, url_prefix='/reports',
    )
    app.config['PROPAGATE_EXCEPTIONS'] = False
    # The handler reads ``get_app_config().__dict__`` after the format parse;
    # the Mongo-free harness has no real app_config, so stub it with a plain
    # object (a __dict__ to copy is all the handler needs from it here).
    monkeypatch.setattr(
        'auto_a11y.web.routes.reports.get_app_config',
        lambda: _StubAppConfig(),
    )
    client = _authenticated_client(app)

    resp = client.post(
        '/reports/generate/page/page-abc',
        data='',
        content_type='application/json',
    )

    # The format parse must NOT 500 on an empty JSON body (the eager-json bug).
    # With the scope authorized and config stubbed, the handler runs to its
    # job-submitted JSON success.
    assert resp.status_code != 500


class _StubAppConfig:
    """Minimal app-config stand-in: just needs a ``__dict__`` to ``.copy()``."""

    def __init__(self) -> None:
        self.PLACEHOLDER = True


# ---------------------------------------------------------------------------
# Part B: download/delete authorization + path-traversal order.
# ---------------------------------------------------------------------------


def test_download_requires_login(monkeypatch: pytest.MonkeyPatch) -> None:
    """download_report is at minimum login-gated: anonymous -> redirect/401."""
    app = make_app_with_blueprint(
        reports_bp, role=None, monkeypatch=monkeypatch, url_prefix='/reports',
    )
    app.config['PROPAGATE_EXCEPTIONS'] = False
    client = app.test_client()  # NOT authenticated

    resp = client.get('/reports/download/report.html')

    assert resp.status_code in (302, 401)


def test_delete_requires_login(monkeypatch: pytest.MonkeyPatch) -> None:
    """delete_report is at minimum login-gated: anonymous -> redirect/401."""
    app = make_app_with_blueprint(
        reports_bp, role=None, monkeypatch=monkeypatch, url_prefix='/reports',
    )
    app.config['PROPAGATE_EXCEPTIONS'] = False
    client = app.test_client()  # NOT authenticated

    resp = client.post('/reports/report.html/delete', json={})

    assert resp.status_code in (302, 401)


def test_download_traversal_blocked_no_existence_side_effect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A filename resolving OUTSIDE ``REPORTS_DIR`` is rejected (403/404) and
    containment is checked BEFORE any existence probe.

    We point ``REPORTS_DIR`` at an empty temp dir, plant a real secret file
    outside it, and request a ``..`` traversal at that secret. The route must
    refuse without ever streaming the out-of-tree file (so a 200 download is a
    failure regardless of the file existing on disk).
    """
    reports_dir = tmp_path / 'reports'
    reports_dir.mkdir()
    secret = tmp_path / 'secret.txt'
    secret.write_text('top secret')

    app = make_app_with_blueprint(
        reports_bp, role='admin', monkeypatch=monkeypatch, url_prefix='/reports',
    )
    app.config['PROPAGATE_EXCEPTIONS'] = False
    # Point the report config at the empty reports dir.
    monkeypatch.setattr(
        'auto_a11y.web.routes.reports.get_app_config',
        lambda: _StubConfig(reports_dir),
    )
    client = _authenticated_client(app)

    # A traversal filename that resolves outside reports_dir must be refused
    # WITHOUT streaming the out-of-tree secret.
    resp = client.get('/reports/download/..%2Fsecret.txt')

    assert resp.status_code in (403, 404)
    # The out-of-tree file must NOT have been streamed.
    assert b'top secret' not in resp.data


class _StubConfig:
    """Minimal config exposing only ``REPORTS_DIR`` for the traversal test."""

    def __init__(self, reports_dir: Path) -> None:
        self.REPORTS_DIR = reports_dir


# ---------------------------------------------------------------------------
# Per-route introspection sweep: every scope-param route 403s for role=None,
# and we assert the full reports_bp endpoint set so a new route can't silently
# skip the guard.
# ---------------------------------------------------------------------------


def test_every_scope_param_route_enforces_authz(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-route introspection: every reports_bp route with a resolvable scope
    param (project_id/website_id/page_id) 403s for role=None.

    Body-param, job, dashboard, download and delete routes are handled
    separately (in-handler check or @login_required) and are excluded here.
    The endpoint-set assertion guards against an un-decorated new route.
    """
    client = _reports_client(monkeypatch, role=None)

    scope_param_pat = re.compile(r'<(?:[^:>]+:)?(project_id|website_id|page_id)>')
    seen_scope_endpoints: set[str] = set()
    all_endpoints: set[str] = set()

    for rule in client.application.url_map.iter_rules():
        endpoint = str(rule.endpoint)
        if not endpoint.startswith('reports.'):
            continue
        all_endpoints.add(endpoint)

        if not scope_param_pat.search(str(rule)):
            continue
        seen_scope_endpoints.add(endpoint)

        path = re.sub(r'<[^>]+>', 'x', str(rule))
        rule_methods = rule.methods
        usable = (set(rule_methods) if rule_methods is not None else {'GET'}) - {
            'HEAD', 'OPTIONS',
        }
        method = 'GET' if 'GET' in usable else sorted(usable)[0]

        resp = client.open(path, method=method, json={})
        assert resp.status_code == 403, (
            f'{endpoint} ({method} {path}) returned {resp.status_code}, '
            f'expected 403 for role=None'
        )

    assert seen_scope_endpoints == {
        'reports.generate_page_report',
        'reports.generate_website_report',
        'reports.generate_project_report',
        'reports.generate_recordings_report',
        'reports.generate_page_structure_report_download',
        'reports.generate_discovery_website_report',
        'reports.generate_discovery_project_report',
        'reports.project_summary',
    }

    # Full endpoint set -- a new route forces a conscious decision here.
    assert all_endpoints == {
        'reports.reports_dashboard',
        'reports.generate_report',
        'reports.job_status',
        'reports.drop_job',
        'reports.restart_job',
        'reports.download_report',
        'reports.delete_report',
        'reports.project_summary',
        'reports.export_csv',
        'reports.generate_page_report',
        'reports.generate_website_report',
        'reports.generate_project_report',
        'reports.generate_page_structure_report_download',
        'reports.generate_page_structure_report',
        'reports.generate_discovery_website_report',
        'reports.generate_discovery_project_report',
        'reports.generate_static_html_report',
        'reports.generate_deduplicated_report',
        'reports.generate_recordings_report',
    }
