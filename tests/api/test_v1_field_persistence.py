"""Regression tests for v1-API fields that were silently dropped after the
``issue-21`` cutover from server-rendered POST handlers to StrictModel-gated
JSON endpoints.

Each form still renders the field, but the v1 request schema didn't model it,
so ``StrictModel`` (``extra='forbid'``) dropped/rejected it and the value
never persisted. These tests assert the field now round-trips to the DB.

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
from auto_a11y.models.website import ScrapingConfig, Website


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


def _make_user(
    database: Database, *, email: str, superadmin: bool = False
) -> AppUser:
    user = AppUser.create(email=email, password="password123", role=UserRole.AUDITOR)
    user_id = database.create_app_user(user)
    saved = database.get_app_user(user_id)
    assert saved is not None
    if superadmin:
        saved.is_superadmin = True
        database.update_app_user(saved)
        refreshed = database.get_app_user(saved.id) if saved.id else None
        assert refreshed is not None
        return refreshed
    return saved


@pytest.fixture
def admin_user(database: Database) -> AppUser:
    return _make_user(database, email="admin@example.test", superadmin=True)


@pytest.fixture
def login(client: FlaskClient) -> Iterator[Any]:
    def _login(user: AppUser) -> None:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    yield _login


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


# ---------------------------------------------------------------------------
# password_hint — POST /users, PATCH /users/<id>, PATCH /auth/me
# ---------------------------------------------------------------------------


def test_create_user_persists_password_hint(
    client: FlaskClient, admin_user: AppUser, login: Any, database: Database
) -> None:
    login(admin_user)
    email = f"newuser-{uuid.uuid4().hex[:8]}@example.test"
    resp = client.post(
        "/api/v1/users",
        json={"email": email, "password": "password123", "password_hint": "my dog"},
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    saved = database.get_app_user_by_email(email)
    assert saved is not None
    assert saved.password_hint == "my dog"


def test_patch_user_persists_password_hint(
    client: FlaskClient, admin_user: AppUser, login: Any, database: Database
) -> None:
    target = _make_user(database, email=f"t-{uuid.uuid4().hex[:8]}@example.test")
    assert target.id is not None
    login(admin_user)
    resp = client.patch(
        f"/api/v1/users/{target.id}",
        json={"password_hint": "first pet"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    saved = database.get_app_user(target.id)
    assert saved is not None
    assert saved.password_hint == "first pet"


def test_patch_auth_me_persists_password_hint(
    client: FlaskClient, login: Any, database: Database
) -> None:
    user = _make_user(database, email=f"self-{uuid.uuid4().hex[:8]}@example.test")
    assert user.id is not None
    login(user)
    resp = client.patch("/api/v1/auth/me", json={"password_hint": "childhood street"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    saved = database.get_app_user(user.id)
    assert saved is not None
    assert saved.password_hint == "childhood street"


# ---------------------------------------------------------------------------
# manual_login_wait_seconds — POST /projects/<id>/test-users (+ GET surfaces it)
# ---------------------------------------------------------------------------


def test_create_test_user_persists_manual_login_wait_seconds(
    client: FlaskClient, admin_user: AppUser, login: Any, project: Project,
    database: Database,
) -> None:
    login(admin_user)
    assert project.id is not None
    resp = client.post(
        f"/api/v1/projects/{project.id}/test-users",
        json={
            "username": "tester1",
            "password": "password123",
            "login_config": {
                "authentication_method": "manual_login",
                "manual_login_wait_seconds": 90,
            },
        },
    )
    assert resp.status_code in (200, 201), resp.get_data(as_text=True)

    users = database.get_project_users(project.id)
    assert len(users) == 1
    saved_user = users[0]
    assert saved_user.login_config.manual_login_wait_seconds == 90

    # GET must surface the value so the edit form can pre-fill it.
    assert saved_user.id is not None
    fetched = client.get(f"/api/v1/project-test-users/{saved_user.id}")
    assert fetched.status_code == 200
    assert fetched.get_json()["login_config"]["manual_login_wait_seconds"] == 90


# ---------------------------------------------------------------------------
# website scraping_config — PATCH must merge, not reset unspecified fields
# ---------------------------------------------------------------------------


def test_patch_website_preserves_unspecified_scraping_config(
    client: FlaskClient, admin_user: AppUser, login: Any, project: Project,
    database: Database,
) -> None:
    """Patching one scraping_config key must NOT reset the others to defaults."""
    assert project.id is not None
    ws = Website(
        project_id=project.id,
        url="https://example.com",
        name="W",
        scraping_config=ScrapingConfig(
            max_pages=3,
            auto_fetch_pdfs=False,
            allowed_paths=["/keep"],
            spa_click_discovery=True,
            spa_ready_selector="#app",
        ),
    )
    website_id = database.create_website(ws)
    login(admin_user)
    resp = client.patch(
        f"/api/v1/websites/{website_id}",
        json={"scraping_config": {"max_pages": 99}},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)

    saved = database.get_website(website_id)
    assert saved is not None
    cfg = saved.scraping_config
    assert cfg.max_pages == 99               # changed
    assert cfg.auto_fetch_pdfs is False      # preserved (would reset to True if rebuilt)
    assert cfg.allowed_paths == ["/keep"]    # preserved
    assert cfg.spa_click_discovery is True   # preserved
    assert cfg.spa_ready_selector == "#app"  # preserved


def test_patch_website_persists_spa_fields(
    client: FlaskClient, admin_user: AppUser, login: Any, project: Project,
    database: Database,
) -> None:
    assert project.id is not None
    ws = Website(project_id=project.id, url="https://example.com", name="W2")
    website_id = database.create_website(ws)
    login(admin_user)
    resp = client.patch(
        f"/api/v1/websites/{website_id}",
        json={"scraping_config": {"spa_click_discovery": True, "spa_ready_selector": "#root"}},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)

    saved = database.get_website(website_id)
    assert saved is not None
    assert saved.scraping_config.spa_click_discovery is True
    assert saved.scraping_config.spa_ready_selector == "#root"
