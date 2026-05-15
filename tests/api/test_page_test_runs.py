"""Tests for the page-level test-run REST endpoint:

- ``POST /api/v1/pages/<id>/test-runs``  (canonical)
- ``POST /api/v1/pages/<id>/test``         (legacy alias, kept until
   the frontend migration)

Both go through :mod:`auto_a11y.core.test_run_service`, so these tests
also cover the service module by verifying the page status moves to
QUEUED and the task runner is invoked with the expected task id
shape.

To avoid spinning up Playwright we monkey-patch
``task_runner.submit_task`` to a no-op recorder.  The actual
Playwright code path is exercised by the existing browser-using
test suite, not by the unit-style endpoint tests here.

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
    db.client.drop_database(db_name)
    db.close()


@pytest.fixture
def submitted_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, Any]]:
    """Capture calls to ``task_runner.submit_task`` without running them.

    Returns a list of dicts: ``{"task_id": str, "func": Callable}``.
    The captured ``func`` is *not* invoked — the queued background
    test would need a live Playwright + Chromium and is well outside
    the scope of an endpoint-level unit test.
    """
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


@pytest.fixture
def auditor(database: Database) -> AppUser:
    return _make_user(
        database, role=UserRole.AUDITOR, email="aud@example.test"
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


def _make_page(database: Database, website: Website) -> Page:
    assert website.id is not None
    page = Page(
        website_id=website.id,
        url=f"{website.url}page-{uuid.uuid4().hex[:6]}",
        status=PageStatus.DISCOVERED,
    )
    pid = database.create_page(page)
    saved = database.get_page(pid)
    assert saved is not None
    return saved


@pytest.fixture
def page(database: Database) -> Page:
    return _make_page(database, _make_website(database, _make_project(database)))


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


def test_service_queues_page_and_submits_task(
    database: Database, flask_app: Flask, page: Page,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    """The service helper updates page status + submits a single task."""
    from auto_a11y.core.test_run_service import start_page_test_run

    with flask_app.app_context():
        from auto_a11y.web.typed_app import get_app_config

        assert page.id is not None
        handle = start_page_test_run(
            database, get_app_config(), page.id
        )

    assert handle.page_id == page.id
    assert handle.multi_state is True
    assert handle.job_id.startswith(f"test_page_{page.id}_")
    assert len(submitted_tasks) == 1
    assert submitted_tasks[0]["task_id"] == handle.job_id

    refreshed = database.get_page(page.id)
    assert refreshed is not None
    assert refreshed.status == PageStatus.QUEUED


def test_service_raises_for_unknown_page(
    database: Database, flask_app: Flask,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    from auto_a11y.core.test_run_service import (
        PageNotFoundError,
        start_page_test_run,
    )

    with flask_app.app_context():
        from auto_a11y.web.typed_app import get_app_config

        with pytest.raises(PageNotFoundError):
            start_page_test_run(
                database,
                get_app_config(),
                "507f1f77bcf86cd799999999",
            )

    # Nothing queued on failure.
    assert submitted_tasks == []


def test_service_raises_when_browser_disabled(
    database: Database, flask_app: Flask, page: Page,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    from auto_a11y.core.test_run_service import (
        BrowserDisabledError,
        start_page_test_run,
    )

    with flask_app.app_context():
        from auto_a11y.web.typed_app import get_app_config

        cfg = get_app_config()
        cfg.BROWSER_MODE = "disabled"
        assert page.id is not None
        with pytest.raises(BrowserDisabledError):
            start_page_test_run(database, cfg, page.id)

    refreshed = database.get_page(page.id)
    assert refreshed is not None
    # Page status must NOT change when the run is rejected.
    assert refreshed.status == PageStatus.DISCOVERED
    assert submitted_tasks == []


def test_service_raises_when_browser_remote(
    database: Database, flask_app: Flask, page: Page,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    from auto_a11y.core.test_run_service import (
        BrowserRemoteError,
        start_page_test_run,
    )

    with flask_app.app_context():
        from auto_a11y.web.typed_app import get_app_config

        cfg = get_app_config()
        cfg.BROWSER_MODE = "remote"
        assert page.id is not None
        with pytest.raises(BrowserRemoteError):
            start_page_test_run(database, cfg, page.id)

    assert submitted_tasks == []


def test_service_respects_enable_multi_state_false(
    database: Database, flask_app: Flask, page: Page,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    from auto_a11y.core.test_run_service import start_page_test_run

    with flask_app.app_context():
        from auto_a11y.web.typed_app import get_app_config

        assert page.id is not None
        handle = start_page_test_run(
            database, get_app_config(), page.id, enable_multi_state=False
        )
    assert handle.multi_state is False


# ---------------------------------------------------------------------------
# REST endpoint
# ---------------------------------------------------------------------------


def test_canonical_test_runs_endpoint_returns_202(
    client: FlaskClient, page: Page,
    admin_user: AppUser, login: Any,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/pages/{page.id}/test-runs", json={}
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["page_id"] == page.id
    assert body["multi_state"] is True
    assert body["status"] == "queued"
    assert body["job_id"].startswith(f"test_page_{page.id}_")
    assert len(submitted_tasks) == 1


def test_legacy_alias_test_endpoint_still_works(
    client: FlaskClient, page: Page,
    admin_user: AppUser, login: Any,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    """The old POST /pages/<id>/test stays alive until frontend
    migration; same shape as the canonical /test-runs endpoint."""
    login(admin_user)
    response = client.post(f"/api/v1/pages/{page.id}/test", json={})
    assert response.status_code == 202
    assert response.get_json()["page_id"] == page.id
    assert len(submitted_tasks) == 1


def test_endpoint_passes_enable_multi_state_false_through(
    client: FlaskClient, page: Page,
    admin_user: AppUser, login: Any,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/pages/{page.id}/test-runs",
        json={"enable_multi_state": False},
    )
    assert response.status_code == 202
    assert response.get_json()["multi_state"] is False


def test_endpoint_returns_404_for_unknown_page(
    client: FlaskClient, admin_user: AppUser, login: Any,
    submitted_tasks: list[dict[str, Any]],
    database: Database,
) -> None:
    """A page id that doesn't exist returns 404 (not 403, because the
    @project_role_required decorator falls through to the handler when
    superadmin is true — which our admin_user is)."""
    login(admin_user)
    _ = database
    response = client.post(
        "/api/v1/pages/507f1f77bcf86cd799999999/test-runs", json={}
    )
    assert response.status_code == 404
    assert submitted_tasks == []


def test_endpoint_returns_503_when_browser_disabled(
    client: FlaskClient, page: Page,
    admin_user: AppUser, login: Any,
    submitted_tasks: list[dict[str, Any]],
    flask_app: Flask,
) -> None:
    """``BROWSER_MODE='disabled'`` short-circuits to a 503."""
    cfg = getattr(flask_app, "app_config")
    cfg.BROWSER_MODE = "disabled"
    login(admin_user)
    response = client.post(
        f"/api/v1/pages/{page.id}/test-runs", json={}
    )
    assert response.status_code == 503
    assert submitted_tasks == []

    refreshed = page  # status didn't change either
    assert refreshed.status == PageStatus.DISCOVERED
