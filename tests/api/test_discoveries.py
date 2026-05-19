"""Tests for the read-only discovery-run REST endpoints:

- ``GET /api/v1/websites/<id>/discoveries``
- ``GET /api/v1/websites/<id>/discoveries/latest``
- ``GET /api/v1/discoveries/<run_id>``

The corresponding write endpoints (start, cancel) belong with the
async-job action endpoints and are deferred. Per
``docs/REST_API_ROADMAP.md`` §5.2.

Tests skip when ``mongod`` is not reachable.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Generator, Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient
from flask_login import LoginManager
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from auto_a11y.core.database import Database
from auto_a11y.models.app_user import AppUser, UserRole
from auto_a11y.models.discovery_run import DiscoveryRun, DiscoveryStatus
from auto_a11y.models.project import Project, ProjectStatus
from auto_a11y.models.website import Website


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


def _make_user(database: Database, *, role: UserRole, email: str) -> AppUser:
    user = AppUser.create(email=email, password="x", role=role)
    user_id = database.create_app_user(user)
    saved = database.get_app_user(user_id)
    assert saved is not None
    return saved


def _make_project(database: Database) -> Project:
    project = Project(
        name=f"Project {uuid.uuid4().hex[:8]}",
        description="",
        status=ProjectStatus.ACTIVE,
        config={},
    )
    project_id = database.create_project(project)
    saved = database.get_project(project_id)
    assert saved is not None
    return saved


def _make_website(database: Database, project: Project) -> Website:
    project_id = project.id
    assert project_id is not None
    website = Website(
        project_id=project_id,
        url=f"https://example-{uuid.uuid4().hex[:8]}.test/",
        name="Test website",
    )
    website_id = database.create_website(website)
    saved = database.get_website(website_id)
    assert saved is not None
    return saved


@pytest.fixture
def admin_user(database: Database) -> AppUser:
    user = _make_user(database, role=UserRole.ADMIN, email="admin@example.test")
    user.is_superadmin = True
    database.update_app_user(user)
    refreshed = database.get_app_user(user.id) if user.id else None
    assert refreshed is not None
    return refreshed


@pytest.fixture
def website(database: Database) -> Website:
    return _make_website(database, _make_project(database))


@pytest.fixture
def login(client: FlaskClient) -> Iterator[Any]:
    def _login(user: AppUser) -> None:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    yield _login


def _insert_discovery_run(
    database: Database,
    *,
    website_id: str,
    started_at: datetime,
    status: DiscoveryStatus = DiscoveryStatus.COMPLETED,
    is_latest: bool = False,
    pages_discovered: int = 0,
) -> DiscoveryRun:
    run = DiscoveryRun(
        website_id=website_id,
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=30),
        status=status,
        pages_discovered=pages_discovered,
        duration_seconds=30,
        is_latest=is_latest,
    )
    inserted = database.discovery_runs.insert_one(run.to_dict())
    run.mongo_id = inserted.inserted_id
    return run


# ---------------------------------------------------------------------------
# Auth + missing-resource
# ---------------------------------------------------------------------------


def test_list_anonymous_returns_401(
    client: FlaskClient, website: Website
) -> None:
    response = client.get(f"/api/v1/websites/{website.id}/discoveries")
    assert response.status_code == 401


def test_list_outsider_returns_403(
    client: FlaskClient,
    database: Database,
    website: Website,
    login: Any,
) -> None:
    outsider = _make_user(
        database, role=UserRole.AUDITOR, email="outsider@example.test"
    )
    login(outsider)
    response = client.get(f"/api/v1/websites/{website.id}/discoveries")
    assert response.status_code == 403


def test_list_unknown_website_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    login(admin_user)
    response = client.get(
        "/api/v1/websites/507f1f77bcf86cd799999999/discoveries"
    )
    assert response.status_code == 404


def test_latest_anonymous_returns_401(
    client: FlaskClient, website: Website
) -> None:
    response = client.get(
        f"/api/v1/websites/{website.id}/discoveries/latest"
    )
    assert response.status_code == 401


def test_latest_returns_404_when_no_runs(
    client: FlaskClient, admin_user: AppUser, login: Any, website: Website
) -> None:
    login(admin_user)
    response = client.get(
        f"/api/v1/websites/{website.id}/discoveries/latest"
    )
    assert response.status_code == 404


def test_single_run_anonymous_on_existing_returns_401(
    client: FlaskClient, database: Database, website: Website
) -> None:
    """For a real run id, anon must get 401 — not 404 — so existence
    isn't leaked to unauthenticated probers."""
    assert website.id is not None
    run = _insert_discovery_run(
        database,
        website_id=website.id,
        started_at=datetime.now(),
    )
    response = client.get(f"/api/v1/discoveries/{run.id}")
    assert response.status_code == 401


def test_single_run_unknown_id_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    login(admin_user)
    response = client.get(
        "/api/v1/discoveries/507f1f77bcf86cd799999999"
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_list_returns_runs_newest_first(
    client: FlaskClient,
    database: Database,
    admin_user: AppUser,
    login: Any,
    website: Website,
) -> None:
    assert website.id is not None
    now = datetime.now()
    older = _insert_discovery_run(
        database,
        website_id=website.id,
        started_at=now - timedelta(days=3),
        pages_discovered=10,
    )
    newer = _insert_discovery_run(
        database,
        website_id=website.id,
        started_at=now,
        is_latest=True,
        pages_discovered=20,
    )
    login(admin_user)

    response = client.get(f"/api/v1/websites/{website.id}/discoveries")
    assert response.status_code == 200
    body = response.get_json()
    # Mongo ObjectId sort puts the newer row first.
    ids = [item["id"] for item in body["items"]]
    assert ids == [newer.id, older.id]
    assert body["items"][0]["is_latest"] is True
    assert body["items"][1]["is_latest"] is False


def test_list_paginates(
    client: FlaskClient,
    database: Database,
    admin_user: AppUser,
    login: Any,
    website: Website,
) -> None:
    assert website.id is not None
    now = datetime.now()
    for i in range(3):
        _insert_discovery_run(
            database,
            website_id=website.id,
            started_at=now - timedelta(hours=i),
        )
    login(admin_user)

    first = client.get(
        f"/api/v1/websites/{website.id}/discoveries?limit=1"
    )
    assert first.status_code == 200
    first_body = first.get_json()
    assert len(first_body["items"]) == 1
    assert first_body["next_cursor"] is not None

    cursor = first_body["next_cursor"]
    second = client.get(
        f"/api/v1/websites/{website.id}/discoveries?limit=1&cursor={cursor}"
    )
    second_body = second.get_json()
    assert len(second_body["items"]) == 1
    assert second_body["items"][0]["id"] != first_body["items"][0]["id"]


def test_latest_returns_is_latest_run(
    client: FlaskClient,
    database: Database,
    admin_user: AppUser,
    login: Any,
    website: Website,
) -> None:
    assert website.id is not None
    now = datetime.now()
    _insert_discovery_run(
        database,
        website_id=website.id,
        started_at=now - timedelta(days=5),
    )
    latest = _insert_discovery_run(
        database,
        website_id=website.id,
        started_at=now,
        is_latest=True,
        pages_discovered=42,
    )
    login(admin_user)

    response = client.get(
        f"/api/v1/websites/{website.id}/discoveries/latest"
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["id"] == latest.id
    assert body["is_latest"] is True
    assert body["pages_discovered"] == 42


def test_single_run_lookup(
    client: FlaskClient,
    database: Database,
    admin_user: AppUser,
    login: Any,
    website: Website,
) -> None:
    assert website.id is not None
    run = _insert_discovery_run(
        database,
        website_id=website.id,
        started_at=datetime.now(),
        status=DiscoveryStatus.FAILED,
        pages_discovered=7,
    )
    login(admin_user)

    response = client.get(f"/api/v1/discoveries/{run.id}")
    assert response.status_code == 200
    body = response.get_json()
    assert body["id"] == run.id
    assert body["status"] == "failed"
    assert body["pages_discovered"] == 7
    assert body["website_id"] == website.id


def test_single_run_cross_tenant_returns_403(
    client: FlaskClient,
    database: Database,
    login: Any,
    website: Website,
) -> None:
    """A user with no membership on the run's project must get 403."""
    assert website.id is not None
    run = _insert_discovery_run(
        database,
        website_id=website.id,
        started_at=datetime.now(),
    )
    outsider = _make_user(
        database, role=UserRole.AUDITOR, email="outsider@example.test"
    )
    login(outsider)

    response = client.get(f"/api/v1/discoveries/{run.id}")
    assert response.status_code == 403
