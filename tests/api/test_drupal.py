"""Tests for the §5.12 Drupal sync REST surface.

Covers the read-only endpoints (status, audits/list, discovered-pages,
recordings, issues) and the auth/404 paths on the action endpoints
(upload, import-pages, import-issues, upload-automated-results).

Happy paths on the action routes require talking to a real Drupal
instance; those are covered as integration tests in CI but skipped
here. The unit tests below monkey-patch ``_drupal_client_or_503``
and ``_drupal_audit_uuid_or_400`` where needed to exercise the
route plumbing without spinning Drupal.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Generator, Iterator
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient
from flask_login import LoginManager
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from auto_a11y.core.database import Database
from auto_a11y.models.app_user import AppUser, UserRole
from auto_a11y.models.project import Project, ProjectStatus


def _mongo_uri() -> str:
    return os.environ.get("MONGODB_URI", "mongodb://localhost:27017/")


@pytest.fixture(scope="module")
def mongo_check() -> None:
    client: MongoClient[dict[str, Any]] = MongoClient(
        _mongo_uri(), serverSelectionTimeoutMS=2000
    )
    try:
        client.admin.command("ping")
    except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
        client.close()
        pytest.skip(f"mongod not available at {_mongo_uri()}: {exc}")
    client.close()


@pytest.fixture
def database(mongo_check: None) -> Generator[Database, None, None]:
    db_name = f"auto_a11y_api_test_{uuid.uuid4().hex}"
    db = Database(_mongo_uri(), db_name)
    yield db
    db.client.drop_database(db_name)
    db.close()


@pytest.fixture
def flask_app(database: Database) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-deterministic-for-tests-only"
    app.config["WTF_CSRF_ENABLED"] = False
    setattr(app, "db", database)

    from config import Config
    setattr(app, "app_config", Config())

    login_manager = LoginManager()
    login_manager.init_app(app)

    def load_user(user_id: str) -> AppUser | None:
        return database.get_app_user(user_id)

    login_manager.user_loader(load_user)

    from auto_a11y.web.api import register_api_error_handlers
    from auto_a11y.web.routes.api import api_bp

    app.register_blueprint(api_bp, url_prefix="/api/v1")
    register_api_error_handlers(app)
    return app


@pytest.fixture
def client(flask_app: Flask) -> FlaskClient:
    return flask_app.test_client()


def _make_user(
    database: Database, *, email: str, is_superadmin: bool = False,
) -> AppUser:
    user = AppUser.create(
        email=email, password="hunter22", role=UserRole.ADMIN,
    )
    if is_superadmin:
        user.is_superadmin = True
    uid = database.create_app_user(user)
    refreshed = database.get_app_user(uid)
    assert refreshed is not None
    return refreshed


@pytest.fixture
def admin_user(database: Database) -> AppUser:
    return _make_user(
        database, email="admin@example.test", is_superadmin=True,
    )


def _make_project(database: Database) -> Project:
    project = Project(
        name=f"Project {uuid.uuid4().hex[:8]}",
        description="",
        status=ProjectStatus.ACTIVE,
        config={},
    )
    pid = database.create_project(project)
    saved = database.get_project(pid)
    assert saved is not None
    return saved


@pytest.fixture
def project(database: Database) -> Project:
    return _make_project(database)


@pytest.fixture
def login(client: FlaskClient) -> Iterator[Any]:
    def _login(user: AppUser) -> None:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    yield _login


class _FakeDrupalConfig:
    enabled = False
    base_url = "https://drupal.test"
    username = "u"
    password = "p"


def _fake_disabled_drupal_config(db: Any = None) -> _FakeDrupalConfig:
    return _FakeDrupalConfig()


@pytest.fixture
def drupal_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ``get_drupal_config().enabled`` False so action routes 409."""
    monkeypatch.setattr(
        "auto_a11y.drupal.config.get_drupal_config",
        _fake_disabled_drupal_config,
    )


# ---------------------------------------------------------------------------
# GET /drupal/audits
# ---------------------------------------------------------------------------


def test_drupal_audits_anonymous_returns_401(client: FlaskClient) -> None:
    response = client.get("/api/v1/drupal/audits")
    assert response.status_code == 401


def test_drupal_audits_disabled_returns_409(
    client: FlaskClient, admin_user: AppUser, login: Any,
    drupal_disabled: None,
) -> None:
    login(admin_user)
    response = client.get("/api/v1/drupal/audits")
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# GET /drupal/projects/<id>/sync-status
# ---------------------------------------------------------------------------


def test_drupal_status_unknown_project_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
) -> None:
    login(admin_user)
    response = client.get(
        "/api/v1/drupal/projects/507f1f77bcf86cd799999999/sync-status",
    )
    assert response.status_code == 404


def test_drupal_status_anonymous_returns_401(
    client: FlaskClient, project: Project,
) -> None:
    response = client.get(
        f"/api/v1/drupal/projects/{project.id}/sync-status",
    )
    assert response.status_code == 401


def test_drupal_status_empty_project_returns_zero_counts(
    client: FlaskClient, admin_user: AppUser, login: Any,
    project: Project,
) -> None:
    login(admin_user)
    response = client.get(
        f"/api/v1/drupal/projects/{project.id}/sync-status",
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["project_name"] == project.name
    assert body["discovered_pages"]["total"] == 0
    assert body["recordings"]["total"] == 0
    assert body["last_sync_time"] is None
    assert body["sync_errors"] == []


def test_drupal_status_counts_aggregate_correctly(
    client: FlaskClient, admin_user: AppUser, login: Any,
    project: Project, database: Database,
) -> None:
    """Seed pages with different drupal_sync_status values + verify totals."""
    project_id = project.id
    assert project_id is not None
    database.discovered_pages.insert_many([
        {
            "project_id": project_id,
            "title": "p1",
            "url": "https://a/1",
            "drupal_sync_status": "synced",
        },
        {
            "project_id": project_id,
            "title": "p2",
            "url": "https://a/2",
            "drupal_sync_status": "not_synced",
        },
        {
            "project_id": project_id,
            "title": "p3",
            "url": "https://a/3",
            "drupal_sync_status": "sync_failed",
            "drupal_error_message": "boom",
        },
    ])

    login(admin_user)
    response = client.get(
        f"/api/v1/drupal/projects/{project_id}/sync-status",
    )
    body = response.get_json()
    assert body["discovered_pages"]["total"] == 3
    assert body["discovered_pages"]["synced"] == 1
    assert body["discovered_pages"]["pending"] == 1
    assert body["discovered_pages"]["failed"] == 1
    assert len(body["sync_errors"]) == 1
    assert "boom" in body["sync_errors"][0]


# ---------------------------------------------------------------------------
# GET /drupal/projects/<id>/discovered-pages
# ---------------------------------------------------------------------------


def test_drupal_discovered_pages_unknown_project_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
) -> None:
    login(admin_user)
    response = client.get(
        "/api/v1/drupal/projects/507f1f77bcf86cd799999999/discovered-pages",
    )
    assert response.status_code == 404


def test_drupal_discovered_pages_returns_only_project_rows(
    client: FlaskClient, admin_user: AppUser, login: Any,
    project: Project, database: Database,
) -> None:
    """Seed a page on one project + a page on a different project; only
    the right project's row comes back."""
    other = _make_project(database)
    assert project.id is not None
    assert other.id is not None
    database.discovered_pages.insert_one({
        "project_id": project.id,
        "title": "mine",
        "url": "https://mine",
        "drupal_sync_status": "not_synced",
    })
    database.discovered_pages.insert_one({
        "project_id": other.id,
        "title": "theirs",
        "url": "https://theirs",
        "drupal_sync_status": "not_synced",
    })

    login(admin_user)
    response = client.get(
        f"/api/v1/drupal/projects/{project.id}/discovered-pages",
    )
    body = response.get_json()
    titles = [p["title"] for p in body["discovered_pages"]]
    assert titles == ["mine"]


# ---------------------------------------------------------------------------
# GET /drupal/projects/<id>/recordings
# ---------------------------------------------------------------------------


def test_drupal_recordings_empty_project_returns_empty(
    client: FlaskClient, admin_user: AppUser, login: Any, project: Project,
) -> None:
    login(admin_user)
    response = client.get(
        f"/api/v1/drupal/projects/{project.id}/recordings",
    )
    assert response.status_code == 200
    assert response.get_json() == {"recordings": []}


# ---------------------------------------------------------------------------
# GET /drupal/projects/<id>/issues
# ---------------------------------------------------------------------------


def test_drupal_issues_empty_project_returns_empty(
    client: FlaskClient, admin_user: AppUser, login: Any, project: Project,
) -> None:
    login(admin_user)
    response = client.get(
        f"/api/v1/drupal/projects/{project.id}/issues",
    )
    assert response.status_code == 200
    assert response.get_json() == {"issues": []}


# ---------------------------------------------------------------------------
# POST /drupal/projects/<id>/upload — disabled + 404 paths
# ---------------------------------------------------------------------------


def test_drupal_upload_anonymous_returns_401(
    client: FlaskClient, project: Project,
) -> None:
    response = client.post(
        f"/api/v1/drupal/projects/{project.id}/upload",
        json={"discovered_page_ids": []},
    )
    assert response.status_code == 401


def test_drupal_upload_unknown_project_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/drupal/projects/507f1f77bcf86cd799999999/upload",
        json={},
    )
    assert response.status_code == 404


def test_drupal_upload_disabled_integration_returns_409(
    client: FlaskClient, admin_user: AppUser, login: Any,
    project: Project, drupal_disabled: None,
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/drupal/projects/{project.id}/upload",
        json={"discovered_page_ids": []},
    )
    assert response.status_code == 409


def _fake_client_or_503() -> object:
    return object()


def _fake_audit_uuid(_p: Any) -> str:
    return "fake-uuid"


def _fake_taxonomies_ctor(_client: Any) -> _FakeTaxonomies:
    return _FakeTaxonomies()


def _fake_wcag_ctor(_client: Any) -> _FakeWcag:
    return _FakeWcag()


def _fake_one_arg_ctor(_a: Any) -> object:
    return object()


def _fake_two_arg_ctor(_a: Any, _b: Any) -> object:
    return object()


def _fake_three_arg_ctor(_a: Any, _b: Any, _c: Any) -> object:
    return object()


def test_drupal_upload_empty_input_returns_zero_counts(
    client: FlaskClient, admin_user: AppUser, login: Any,
    project: Project, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An upload with empty arrays runs the audit lookup once and
    returns zero counts — no items to iterate over."""
    monkeypatch.setattr(
        "auto_a11y.web.routes.api._drupal_client_or_503",
        _fake_client_or_503,
    )
    monkeypatch.setattr(
        "auto_a11y.web.routes.api._drupal_audit_uuid_or_400",
        _fake_audit_uuid,
    )
    monkeypatch.setattr(
        "auto_a11y.drupal.DiscoveredPageTaxonomies", _fake_taxonomies_ctor,
    )
    monkeypatch.setattr(
        "auto_a11y.drupal.WCAGChapterCache", _fake_wcag_ctor,
    )
    monkeypatch.setattr(
        "auto_a11y.drupal.DiscoveredPageExporter", _fake_two_arg_ctor,
    )
    monkeypatch.setattr(
        "auto_a11y.drupal.RecordingExporter", _fake_one_arg_ctor,
    )
    monkeypatch.setattr(
        "auto_a11y.drupal.IssueExporter", _fake_three_arg_ctor,
    )

    login(admin_user)
    response = client.post(
        f"/api/v1/drupal/projects/{project.id}/upload",
        json={
            "discovered_page_ids": [],
            "recording_ids": [],
            "issue_ids": [],
        },
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["audit_uuid"] == "fake-uuid"
    assert body["success_count"] == 0
    assert body["failure_count"] == 0
    assert body["errors"] == []


class _FakeTaxonomies:
    def __init__(self) -> None:
        self.cache = _FakeTaxonomyCache()


class _FakeTaxonomyCache:
    def get_terms(self, _name: str) -> list[Any]:
        return []


class _FakeWcag:
    def get_chapters(self) -> list[Any]:
        return []


# ---------------------------------------------------------------------------
# POST /drupal/projects/<id>/import-pages + import-issues
# ---------------------------------------------------------------------------


def test_drupal_import_pages_anonymous_returns_401(
    client: FlaskClient, project: Project,
) -> None:
    response = client.post(
        f"/api/v1/drupal/projects/{project.id}/import-pages",
    )
    assert response.status_code == 401


def test_drupal_import_pages_unknown_project_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/drupal/projects/507f1f77bcf86cd799999999/import-pages",
    )
    assert response.status_code == 404


def test_drupal_import_pages_disabled_returns_409(
    client: FlaskClient, admin_user: AppUser, login: Any,
    project: Project, drupal_disabled: None,
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/drupal/projects/{project.id}/import-pages",
    )
    assert response.status_code == 409


def test_drupal_import_issues_disabled_returns_409(
    client: FlaskClient, admin_user: AppUser, login: Any,
    project: Project, drupal_disabled: None,
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/drupal/projects/{project.id}/import-issues",
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# POST /drupal/projects/<id>/upload-automated-results
# ---------------------------------------------------------------------------


def test_drupal_upload_automated_anonymous_returns_401(
    client: FlaskClient, project: Project,
) -> None:
    response = client.post(
        f"/api/v1/drupal/projects/{project.id}/upload-automated-results",
    )
    assert response.status_code == 401


def test_drupal_upload_automated_unknown_project_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/drupal/projects/507f1f77bcf86cd799999999/upload-automated-results",
    )
    assert response.status_code == 404


def test_drupal_upload_automated_disabled_returns_409(
    client: FlaskClient, admin_user: AppUser, login: Any,
    project: Project, drupal_disabled: None,
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/drupal/projects/{project.id}/upload-automated-results",
    )
    assert response.status_code == 409
