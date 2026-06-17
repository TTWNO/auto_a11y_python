"""Tests for the project create/update REST endpoints, focused on the
``drupal_audit_name`` field round-tripping through the v1 API.

Regression: the project create/edit forms submit via ``POST/PUT
/api/v1/projects`` (``ProjectIn`` / ``ProjectPatch``). Neither schema
modeled ``drupal_audit_name``, so selecting a Drupal audit on a project
was silently dropped on save even though the GET response surfaces it.

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


@pytest.fixture
def admin_user(database: Database) -> AppUser:
    user = AppUser.create(email="admin@example.test", password="x", role=UserRole.AUDITOR)
    user_id = database.create_app_user(user)
    saved = database.get_app_user(user_id)
    assert saved is not None
    saved.is_superadmin = True
    database.update_app_user(saved)
    refreshed = database.get_app_user(saved.id) if saved.id else None
    assert refreshed is not None
    return refreshed


@pytest.fixture
def project(database: Database) -> Project:
    proj = Project(
        name=f"Project {uuid.uuid4().hex[:8]}",
        description="",
        status=ProjectStatus.ACTIVE,
        config={},
    )
    project_id = database.create_project(proj)
    saved = database.get_project(project_id)
    assert saved is not None
    return saved


@pytest.fixture
def login(client: FlaskClient) -> Iterator[Any]:
    def _login(user: AppUser) -> None:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    yield _login


def test_create_project_persists_drupal_audit_name(
    client: FlaskClient, admin_user: AppUser, login: Any, database: Database
) -> None:
    """POST /projects with drupal_audit_name must persist it to the DB."""
    login(admin_user)
    create = client.post(
        "/api/v1/projects",
        json={
            "name": f"Drupal proj {uuid.uuid4().hex[:8]}",
            "drupal_audit_name": "My Audit Node",
        },
    )
    assert create.status_code == 201, create.get_data(as_text=True)
    project_id = create.get_json()["id"]

    saved = database.get_project(project_id)
    assert saved is not None
    assert saved.drupal_audit_name == "My Audit Node"


def test_create_project_persists_type_and_identifiers(
    client: FlaskClient, admin_user: AppUser, login: Any, database: Database
) -> None:
    """POST /projects must persist project_type + type-specific identifiers."""
    login(admin_user)
    create = client.post(
        "/api/v1/projects",
        json={
            "name": f"App proj {uuid.uuid4().hex[:8]}",
            "project_type": "app",
            "app_identifier": "com.example.app",
        },
    )
    assert create.status_code == 201, create.get_data(as_text=True)
    saved = database.get_project(create.get_json()["id"])
    assert saved is not None
    assert saved.project_type.value == "app"
    assert saved.app_identifier == "com.example.app"


def test_create_tangible_device_project_persists_location(
    client: FlaskClient, admin_user: AppUser, login: Any, database: Database
) -> None:
    login(admin_user)
    create = client.post(
        "/api/v1/projects",
        json={
            "name": f"Device proj {uuid.uuid4().hex[:8]}",
            "project_type": "tangible_device",
            "device_model": "Kiosk X1",
            "location": "Building A, Floor 2",
        },
    )
    assert create.status_code == 201, create.get_data(as_text=True)
    saved = database.get_project(create.get_json()["id"])
    assert saved is not None
    assert saved.project_type.value == "tangible_device"
    assert saved.device_model == "Kiosk X1"
    assert saved.location == "Building A, Floor 2"


def test_update_project_persists_drupal_audit_name(
    client: FlaskClient, admin_user: AppUser, login: Any, project: Project,
    database: Database,
) -> None:
    """PUT /projects/<id> with drupal_audit_name must persist it to the DB."""
    login(admin_user)
    resp = client.put(
        f"/api/v1/projects/{project.id}",
        json={"drupal_audit_name": "Chosen Audit"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)

    assert project.id is not None
    saved = database.get_project(project.id)
    assert saved is not None
    assert saved.drupal_audit_name == "Chosen Audit"


def test_update_project_can_clear_drupal_audit_name(
    client: FlaskClient, admin_user: AppUser, login: Any, project: Project,
    database: Database,
) -> None:
    """Sending an empty string clears the stored Drupal audit selection."""
    login(admin_user)
    client.put(
        f"/api/v1/projects/{project.id}",
        json={"drupal_audit_name": "Temp"},
    )
    cleared = client.put(
        f"/api/v1/projects/{project.id}",
        json={"drupal_audit_name": ""},
    )
    assert cleared.status_code == 200, cleared.get_data(as_text=True)

    assert project.id is not None
    saved = database.get_project(project.id)
    assert saved is not None
    assert saved.drupal_audit_name is None
