"""Tests for the website-discovery start endpoint:

- ``POST /api/v1/websites/<id>/discoveries`` (canonical)
- ``POST /api/v1/websites/<id>/discover``     (legacy alias)

Both routes share a single handler that delegates to
``auto_a11y.core.test_run_service.start_website_discovery`` — these
tests also cover the service helper for that path.

``task_runner.submit_task`` is monkey-patched to a no-op recorder so
we don't fire up a real browser crawl.

Tests skip when ``mongod`` is not reachable.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Generator, Iterator
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient
from flask_login import LoginManager
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from auto_a11y.core.database import Database
from auto_a11y.core.task_runner import task_runner
from auto_a11y.models.app_user import AppUser, UserRole
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
def submitted_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    def fake_submit(
        func: Callable[..., Any],
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> str:
        actual_id = task_id or f"fake-task-{uuid.uuid4().hex[:8]}"
        captured.append({"task_id": actual_id, "func": func})
        return actual_id

    monkeypatch.setattr(task_runner, "submit_task", fake_submit)
    return captured


@pytest.fixture
def flask_app(database: Database) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-deterministic-for-tests-only"
    app.config["WTF_CSRF_ENABLED"] = False
    setattr(app, "db", database)

    from config import Config
    cfg = Config()
    cfg.BROWSER_MODE = "local"
    cfg.CLAUDE_API_KEY = ""
    setattr(app, "app_config", cfg)
    setattr(app, "pdf_runner", None)

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


@pytest.fixture
def admin_user(database: Database) -> AppUser:
    user = _make_user(database, role=UserRole.ADMIN, email="admin@example.test")
    user.is_superadmin = True
    database.update_app_user(user)
    refreshed = database.get_app_user(user.id) if user.id else None
    assert refreshed is not None
    return refreshed


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


def _make_website(database: Database, project: Project) -> Website:
    assert project.id is not None
    website = Website(
        project_id=project.id,
        url=f"https://example-{uuid.uuid4().hex[:8]}.test/",
        name="Test website",
    )
    wid = database.create_website(website)
    saved = database.get_website(wid)
    assert saved is not None
    return saved


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


# ---------------------------------------------------------------------------
# Service unit
# ---------------------------------------------------------------------------


def test_service_queues_discovery_task(
    database: Database, flask_app: Flask, website: Website,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    from auto_a11y.core.test_run_service import start_website_discovery

    with flask_app.app_context():
        from auto_a11y.web.typed_app import get_app_config

        assert website.id is not None
        handle = start_website_discovery(
            database, get_app_config(), website.id,
        )

    assert handle.website_id == website.id
    assert handle.max_pages is None
    assert handle.user_count == 1  # default guest
    assert handle.job_id.startswith(f"discovery_{website.id}_")
    assert len(submitted_tasks) == 1
    assert submitted_tasks[0]["task_id"] == handle.job_id


def test_service_raises_for_unknown_website(
    database: Database, flask_app: Flask,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    from auto_a11y.core.test_run_service import (
        WebsiteNotFoundError,
        start_website_discovery,
    )

    with flask_app.app_context():
        from auto_a11y.web.typed_app import get_app_config

        with pytest.raises(WebsiteNotFoundError):
            start_website_discovery(
                database, get_app_config(),
                "507f1f77bcf86cd799999999",
            )
    assert submitted_tasks == []


def test_service_normalizes_user_ids_and_caps_max_pages(
    database: Database, flask_app: Flask, website: Website,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    from auto_a11y.core.test_run_service import start_website_discovery

    with flask_app.app_context():
        from auto_a11y.web.typed_app import get_app_config

        assert website.id is not None
        handle = start_website_discovery(
            database, get_app_config(), website.id,
            max_pages=50,
            project_user_ids=["u1", "u2", "u3"],
        )

    assert handle.max_pages == 50
    assert handle.user_count == 3


def test_service_treats_non_positive_max_pages_as_unbounded(
    database: Database, flask_app: Flask, website: Website,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    from auto_a11y.core.test_run_service import start_website_discovery

    with flask_app.app_context():
        from auto_a11y.web.typed_app import get_app_config

        assert website.id is not None
        handle = start_website_discovery(
            database, get_app_config(), website.id, max_pages=0,
        )
    assert handle.max_pages is None


# ---------------------------------------------------------------------------
# REST endpoint
# ---------------------------------------------------------------------------


def test_canonical_discoveries_endpoint_returns_202(
    client: FlaskClient, admin_user: AppUser, login: Any,
    website: Website,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/websites/{website.id}/discoveries", json={}
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["website_id"] == website.id
    assert body["user_count"] == 1
    assert body["status"] == "started"
    assert body["job_id"].startswith(f"discovery_{website.id}_")
    assert len(submitted_tasks) == 1


def test_legacy_discover_alias_still_works(
    client: FlaskClient, admin_user: AppUser, login: Any,
    website: Website,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/websites/{website.id}/discover", json={}
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["website_id"] == website.id
    assert len(submitted_tasks) == 1


def test_endpoint_passes_max_pages_through(
    client: FlaskClient, admin_user: AppUser, login: Any,
    website: Website,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/websites/{website.id}/discoveries",
        json={"max_pages": 25},
    )
    body = response.get_json()
    assert body["max_pages"] == 25


def test_endpoint_passes_user_id_list_through(
    client: FlaskClient, admin_user: AppUser, login: Any,
    website: Website,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/websites/{website.id}/discoveries",
        json={"project_user_ids": ["a", "b"]},
    )
    body = response.get_json()
    assert body["user_count"] == 2


def test_endpoint_returns_404_for_unknown_website(
    client: FlaskClient, admin_user: AppUser, login: Any,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/websites/507f1f77bcf86cd799999999/discoveries", json={}
    )
    assert response.status_code == 404
    assert submitted_tasks == []


def test_endpoint_accepts_legacy_website_user_ids_key(
    client: FlaskClient, admin_user: AppUser, login: Any,
    website: Website,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    """Some in-flight JS clients still send ``website_user_ids``."""
    login(admin_user)
    response = client.post(
        f"/api/v1/websites/{website.id}/discoveries",
        json={"website_user_ids": ["x", "y", "z"]},
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["user_count"] == 3
