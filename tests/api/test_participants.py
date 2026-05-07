"""Tests for the project participants REST endpoints (testers + supervisors).

Endpoints exercised:

- GET    /api/v1/projects/<id>/testers
- POST   /api/v1/projects/<id>/testers
- GET    /api/v1/projects/<id>/testers/<tester_id>
- PUT    /api/v1/projects/<id>/testers/<tester_id>
- PATCH  /api/v1/projects/<id>/testers/<tester_id>
- DELETE /api/v1/projects/<id>/testers/<tester_id>
- (same set for supervisors)

Tests skip when ``mongod`` is not reachable.
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


def _make_user(database: Database, *, email: str) -> AppUser:
    user = AppUser.create(email=email, password="x", role=UserRole.AUDITOR)
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


@pytest.fixture
def admin_user(database: Database) -> AppUser:
    user = _make_user(database, email="admin@example.test")
    user.is_superadmin = True
    database.update_app_user(user)
    refreshed = database.get_app_user(user.id) if user.id else None
    assert refreshed is not None
    return refreshed


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


# ---------------------------------------------------------------------------
# Auth + 404
# ---------------------------------------------------------------------------


def test_anonymous_list_testers_returns_401(
    client: FlaskClient, project: Project
) -> None:
    response = client.get(f"/api/v1/projects/{project.id}/testers")
    assert response.status_code == 401


def test_authenticated_non_member_returns_403(
    client: FlaskClient,
    database: Database,
    project: Project,
    login: Any,
) -> None:
    user = _make_user(database, email="outsider@example.test")
    login(user)
    response = client.get(f"/api/v1/projects/{project.id}/testers")
    assert response.status_code == 403


def test_unknown_project_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    login(admin_user)
    response = client.get(
        "/api/v1/projects/507f1f77bcf86cd799999999/testers"
    )
    assert response.status_code == 404


def test_unknown_tester_returns_404(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    response = client.get(
        f"/api/v1/projects/{project.id}/testers/not-a-real-id"
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Testers — CRUD round-trip
# ---------------------------------------------------------------------------


def test_create_tester_round_trip(
    client: FlaskClient,
    admin_user: AppUser,
    database: Database,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    create = client.post(
        f"/api/v1/projects/{project.id}/testers",
        json={
            "name": "Alice",
            "email": "alice@example.test",
            "disability_type": "Low Vision",
            "assistive_tech": ["Screen Magnifier", "Voiceover"],
            "notes": "prefers afternoon sessions",
        },
    )
    assert create.status_code == 201, create.get_json()
    body = create.get_json()
    tester_id = body["id"]
    assert tester_id  # generated UUID
    assert body["name"] == "Alice"
    assert body["assistive_tech"] == ["Screen Magnifier", "Voiceover"]
    assert create.headers["Location"] == (
        f"/api/v1/projects/{project.id}/testers/{tester_id}"
    )

    # Persisted on the project document.
    refreshed = database.get_project(project.id or "")
    assert refreshed is not None
    assert any(t.id == tester_id for t in refreshed.lived_experience_testers)

    # GET returns the same record.
    fetched = client.get(
        f"/api/v1/projects/{project.id}/testers/{tester_id}"
    )
    assert fetched.status_code == 200
    assert fetched.get_json()["id"] == tester_id


def test_create_tester_without_name_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/projects/{project.id}/testers",
        json={"email": "no-name@example.test"},
    )
    assert response.status_code == 400
    field_errors = {e["field"] for e in response.get_json()["errors"]}
    assert "name" in field_errors


def test_patch_tester_updates_only_provided_fields(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    create = client.post(
        f"/api/v1/projects/{project.id}/testers",
        json={"name": "Bob", "disability_type": "Blind"},
    )
    tester_id = create.get_json()["id"]
    response = client.patch(
        f"/api/v1/projects/{project.id}/testers/{tester_id}",
        json={"notes": "uses NVDA"},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["name"] == "Bob"
    assert body["disability_type"] == "Blind"
    assert body["notes"] == "uses NVDA"


def test_put_replaces_tester_preserving_id(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    create = client.post(
        f"/api/v1/projects/{project.id}/testers", json={"name": "Carol"}
    )
    tester_id = create.get_json()["id"]
    response = client.put(
        f"/api/v1/projects/{project.id}/testers/{tester_id}",
        json={"name": "Carol Replaced", "assistive_tech": ["JAWS"]},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["id"] == tester_id  # id preserved across PUT
    assert body["name"] == "Carol Replaced"
    assert body["assistive_tech"] == ["JAWS"]
    # disability_type omitted from PUT body — replaced model has None.
    assert body["disability_type"] is None


def test_delete_tester_returns_204(
    client: FlaskClient,
    admin_user: AppUser,
    database: Database,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    create = client.post(
        f"/api/v1/projects/{project.id}/testers", json={"name": "Dana"}
    )
    tester_id = create.get_json()["id"]
    response = client.delete(
        f"/api/v1/projects/{project.id}/testers/{tester_id}"
    )
    assert response.status_code == 204
    refreshed = database.get_project(project.id or "")
    assert refreshed is not None
    assert all(t.id != tester_id for t in refreshed.lived_experience_testers)


# ---------------------------------------------------------------------------
# Supervisors — CRUD round-trip
# ---------------------------------------------------------------------------


def test_create_supervisor_round_trip(
    client: FlaskClient,
    admin_user: AppUser,
    database: Database,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    create = client.post(
        f"/api/v1/projects/{project.id}/supervisors",
        json={
            "name": "Sam",
            "email": "sam@example.test",
            "role": "Accessibility Specialist",
            "organization": "ACME",
            "notes": "available Tu/Th",
        },
    )
    assert create.status_code == 201
    body = create.get_json()
    supervisor_id = body["id"]
    assert body["role"] == "Accessibility Specialist"
    assert body["organization"] == "ACME"
    assert create.headers["Location"] == (
        f"/api/v1/projects/{project.id}/supervisors/{supervisor_id}"
    )

    refreshed = database.get_project(project.id or "")
    assert refreshed is not None
    assert any(s.id == supervisor_id for s in refreshed.test_supervisors)


def test_patch_supervisor_updates_only_provided_fields(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    create = client.post(
        f"/api/v1/projects/{project.id}/supervisors",
        json={"name": "Tara", "organization": "Old Org"},
    )
    supervisor_id = create.get_json()["id"]
    response = client.patch(
        f"/api/v1/projects/{project.id}/supervisors/{supervisor_id}",
        json={"organization": "New Org"},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["name"] == "Tara"
    assert body["organization"] == "New Org"


def test_delete_supervisor_returns_204(
    client: FlaskClient,
    admin_user: AppUser,
    database: Database,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    create = client.post(
        f"/api/v1/projects/{project.id}/supervisors", json={"name": "Una"}
    )
    supervisor_id = create.get_json()["id"]
    response = client.delete(
        f"/api/v1/projects/{project.id}/supervisors/{supervisor_id}"
    )
    assert response.status_code == 204
    refreshed = database.get_project(project.id or "")
    assert refreshed is not None
    assert all(s.id != supervisor_id for s in refreshed.test_supervisors)


# ---------------------------------------------------------------------------
# Listings isolate testers from supervisors (separate inline arrays)
# ---------------------------------------------------------------------------


def test_listings_keep_testers_and_supervisors_distinct(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    client.post(
        f"/api/v1/projects/{project.id}/testers", json={"name": "Tester One"}
    )
    client.post(
        f"/api/v1/projects/{project.id}/supervisors", json={"name": "Sup One"}
    )

    testers = client.get(f"/api/v1/projects/{project.id}/testers").get_json()
    supervisors = client.get(
        f"/api/v1/projects/{project.id}/supervisors"
    ).get_json()
    assert {t["name"] for t in testers["items"]} == {"Tester One"}
    assert {s["name"] for s in supervisors["items"]} == {"Sup One"}
