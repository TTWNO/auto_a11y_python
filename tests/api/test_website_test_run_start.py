"""Tests for the website batch test-run start endpoint:

- ``POST /api/v1/websites/<id>/test-runs`` (canonical)
- ``POST /api/v1/websites/<id>/test``       (legacy alias)

Both routes share a single handler that delegates to
``auto_a11y.core.test_run_service.start_website_test_run`` — these
tests also cover the service helper for the page-filtering, user-id
normalisation, and ``NoPagesToTestError`` paths.

``task_runner.submit_task`` is monkey-patched to a no-op recorder
so we don't actually launch a real browser test loop.

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
from auto_a11y.core.job_manager import JobManager
from auto_a11y.core.task_runner import task_runner
from auto_a11y.models import Page, PageStatus
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
    # JobManager singleton — see the discovery test for context.
    setattr(JobManager, "_instance", None)
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


def _make_page(
    database: Database, website: Website, *, status: PageStatus
) -> Page:
    assert website.id is not None
    page = Page(
        website_id=website.id,
        url=f"{website.url}page-{uuid.uuid4().hex[:6]}",
        status=status,
    )
    pid = database.create_page(page)
    saved = database.get_page(pid)
    assert saved is not None
    return saved


@pytest.fixture
def website(database: Database) -> Website:
    return _make_website(database, _make_project(database))


@pytest.fixture
def website_with_pages(database: Database, website: Website) -> Website:
    _make_page(database, website, status=PageStatus.DISCOVERED)
    _make_page(database, website, status=PageStatus.TESTED)
    _make_page(database, website, status=PageStatus.DISCOVERED)
    return website


@pytest.fixture
def login(client: FlaskClient) -> Iterator[Any]:
    def _login(user: AppUser) -> None:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    yield _login


# ---------------------------------------------------------------------------
# Service unit tests
# ---------------------------------------------------------------------------


def test_service_queues_one_task_with_expected_id_shape(
    database: Database, flask_app: Flask, website_with_pages: Website,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    from auto_a11y.core.test_run_service import start_website_test_run

    with flask_app.app_context():
        from auto_a11y.web.typed_app import get_app_config

        assert website_with_pages.id is not None
        handle = start_website_test_run(
            database, get_app_config(), website_with_pages.id,
        )

    assert handle.website_id == website_with_pages.id
    assert handle.pages_queued == 3
    assert handle.user_count == 1
    assert handle.total_tests == 3
    assert handle.job_id.startswith(f"testing_{website_with_pages.id}_")
    assert len(submitted_tasks) == 1
    assert submitted_tasks[0]["task_id"] == handle.job_id


def test_service_raises_for_unknown_website(
    database: Database, flask_app: Flask,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    from auto_a11y.core.test_run_service import (
        WebsiteNotFoundError,
        start_website_test_run,
    )

    with flask_app.app_context():
        from auto_a11y.web.typed_app import get_app_config

        with pytest.raises(WebsiteNotFoundError):
            start_website_test_run(
                database, get_app_config(),
                "507f1f77bcf86cd799999999",
            )
    assert submitted_tasks == []


def test_service_raises_when_no_eligible_pages(
    database: Database, flask_app: Flask, website: Website,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    """No pages discovered at all → NoPagesToTestError."""
    from auto_a11y.core.test_run_service import (
        NoPagesToTestError,
        start_website_test_run,
    )

    with flask_app.app_context():
        from auto_a11y.web.typed_app import get_app_config

        assert website.id is not None
        with pytest.raises(NoPagesToTestError) as exc_info:
            start_website_test_run(
                database, get_app_config(), website.id,
            )
        assert exc_info.value.website_id == website.id
        assert exc_info.value.untested_only is False
    assert submitted_tasks == []


def test_service_untested_only_filters_tested_pages(
    database: Database, flask_app: Flask, website_with_pages: Website,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    """Of 3 pages (2 DISCOVERED, 1 TESTED), untested_only sees 2."""
    from auto_a11y.core.test_run_service import start_website_test_run

    with flask_app.app_context():
        from auto_a11y.web.typed_app import get_app_config

        assert website_with_pages.id is not None
        handle = start_website_test_run(
            database, get_app_config(), website_with_pages.id,
            untested_only=True,
        )
    assert handle.pages_queued == 2


def test_service_caps_pages_to_max_pages(
    database: Database, flask_app: Flask, website_with_pages: Website,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    from auto_a11y.core.test_run_service import start_website_test_run

    with flask_app.app_context():
        from auto_a11y.web.typed_app import get_app_config

        assert website_with_pages.id is not None
        handle = start_website_test_run(
            database, get_app_config(), website_with_pages.id,
            max_pages=2,
        )
    assert handle.pages_queued == 2


def test_service_total_tests_multiplies_users_by_pages(
    database: Database, flask_app: Flask, website_with_pages: Website,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    """3 pages × 2 users = 6 total_tests."""
    from auto_a11y.core.test_run_service import start_website_test_run

    with flask_app.app_context():
        from auto_a11y.web.typed_app import get_app_config

        assert website_with_pages.id is not None
        handle = start_website_test_run(
            database, get_app_config(), website_with_pages.id,
            project_user_ids=["u1", "u2"],
        )
    assert handle.pages_queued == 3
    assert handle.user_count == 2
    assert handle.total_tests == 6


# ---------------------------------------------------------------------------
# REST endpoint
# ---------------------------------------------------------------------------


def test_canonical_test_runs_endpoint_returns_202(
    client: FlaskClient, admin_user: AppUser, login: Any,
    website_with_pages: Website,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/websites/{website_with_pages.id}/test-runs", json={}
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["website_id"] == website_with_pages.id
    assert body["pages_queued"] == 3
    assert body["user_count"] == 1
    assert body["total_tests"] == 3
    assert body["status"] == "queued"
    assert body["job_id"].startswith(f"testing_{website_with_pages.id}_")
    assert len(submitted_tasks) == 1


def test_legacy_test_alias_still_works(
    client: FlaskClient, admin_user: AppUser, login: Any,
    website_with_pages: Website,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    """The old POST /websites/<id>/test stays alive until frontend
    migration; same shape as the canonical /test-runs endpoint."""
    login(admin_user)
    response = client.post(
        f"/api/v1/websites/{website_with_pages.id}/test", json={}
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["website_id"] == website_with_pages.id
    assert len(submitted_tasks) == 1


def test_endpoint_unknown_website_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/websites/507f1f77bcf86cd799999999/test-runs", json={}
    )
    assert response.status_code == 404
    assert submitted_tasks == []


def test_endpoint_returns_400_when_no_pages(
    client: FlaskClient, admin_user: AppUser, login: Any,
    website: Website,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    """Website with no pages discovered yet → 400, no task queued."""
    login(admin_user)
    response = client.post(
        f"/api/v1/websites/{website.id}/test-runs", json={}
    )
    assert response.status_code == 400
    assert submitted_tasks == []


def test_endpoint_passes_user_ids_and_max_pages(
    client: FlaskClient, admin_user: AppUser, login: Any,
    website_with_pages: Website,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/websites/{website_with_pages.id}/test-runs",
        json={
            "project_user_ids": ["a", "b", "c"],
            "max_pages": 2,
        },
    )
    body = response.get_json()
    assert body["pages_queued"] == 2
    assert body["user_count"] == 3
    assert body["total_tests"] == 6


def test_endpoint_accepts_legacy_website_user_ids_key(
    client: FlaskClient, admin_user: AppUser, login: Any,
    website_with_pages: Website,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/websites/{website_with_pages.id}/test-runs",
        json={"website_user_ids": ["x", "y"]},
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["user_count"] == 2


def test_endpoint_untested_only_filters_pages(
    client: FlaskClient, admin_user: AppUser, login: Any,
    website_with_pages: Website,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/websites/{website_with_pages.id}/test-runs",
        json={"untested_only": True},
    )
    body = response.get_json()
    assert body["pages_queued"] == 2
