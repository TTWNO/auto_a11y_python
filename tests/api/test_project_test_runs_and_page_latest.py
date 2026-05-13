"""Tests for the two final §27 endpoints that close the roadmap:

- ``POST /api/v1/projects/<id>/test-runs`` — project-wide "test all"
- ``GET  /api/v1/pages/<id>/test-runs/latest`` — per-page run status

``task_runner.submit_task`` is monkey-patched so neither test runs a
real browser.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Generator, Iterator
from datetime import datetime
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
from auto_a11y.models.test_result import TestResult
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
    database: Database,
    website: Website,
    *,
    status: PageStatus = PageStatus.DISCOVERED,
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
# POST /projects/<id>/test-runs
# ---------------------------------------------------------------------------


def test_project_test_runs_unknown_project_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/projects/507f1f77bcf86cd799999999/test-runs", json={},
    )
    assert response.status_code == 404
    assert submitted_tasks == []


def test_project_test_runs_anonymous_returns_401(
    client: FlaskClient, project: Project,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    response = client.post(
        f"/api/v1/projects/{project.id}/test-runs", json={},
    )
    assert response.status_code == 401


def test_project_test_runs_no_websites_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
    project: Project, submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/projects/{project.id}/test-runs", json={},
    )
    assert response.status_code == 400
    assert submitted_tasks == []


def test_project_test_runs_no_eligible_pages_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    """Project with websites but no pages → 400 (nothing to queue)."""
    _make_website(database, project)
    _make_website(database, project)
    login(admin_user)
    response = client.post(
        f"/api/v1/projects/{project.id}/test-runs", json={},
    )
    assert response.status_code == 400
    assert submitted_tasks == []


def test_project_test_runs_happy_path_queues_per_website(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    website_a = _make_website(database, project)
    website_b = _make_website(database, project)
    _make_page(database, website_a)
    _make_page(database, website_a)
    _make_page(database, website_b)

    login(admin_user)
    response = client.post(
        f"/api/v1/projects/{project.id}/test-runs", json={},
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["project_id"] == project.id
    assert body["websites_queued"] == 2
    assert body["pages_queued"] == 3
    assert len(body["test_runs"]) == 2
    assert {tr["website_id"] for tr in body["test_runs"]} == {
        website_a.id, website_b.id,
    }
    # One task per website (the fan-out is at the website level).
    assert len(submitted_tasks) == 2


def test_project_test_runs_skips_websites_with_no_pages(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    """One website with pages, one without → only the populated one queues."""
    website_with_pages = _make_website(database, project)
    _make_website(database, project)  # empty
    _make_page(database, website_with_pages)

    login(admin_user)
    response = client.post(
        f"/api/v1/projects/{project.id}/test-runs", json={},
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["websites_queued"] == 1
    assert len(submitted_tasks) == 1


def test_project_test_runs_total_tests_sums_across_websites(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    """2 websites × 2 pages each × 2 users = 8 total_tests."""
    for _ in range(2):
        w = _make_website(database, project)
        _make_page(database, w)
        _make_page(database, w)

    login(admin_user)
    response = client.post(
        f"/api/v1/projects/{project.id}/test-runs",
        json={"project_user_ids": ["u1", "u2"]},
    )
    body = response.get_json()
    assert body["total_tests"] == 8


def test_project_test_runs_respects_untested_only(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    """Of 4 pages (2 DISCOVERED + 2 TESTED), untested_only sees 2."""
    website = _make_website(database, project)
    _make_page(database, website, status=PageStatus.DISCOVERED)
    _make_page(database, website, status=PageStatus.DISCOVERED)
    _make_page(database, website, status=PageStatus.TESTED)
    _make_page(database, website, status=PageStatus.TESTED)

    login(admin_user)
    response = client.post(
        f"/api/v1/projects/{project.id}/test-runs",
        json={"untested_only": True},
    )
    body = response.get_json()
    assert body["pages_queued"] == 2


# ---------------------------------------------------------------------------
# GET /pages/<id>/test-runs/latest
# ---------------------------------------------------------------------------


def test_page_latest_unknown_page_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
) -> None:
    login(admin_user)
    response = client.get(
        "/api/v1/pages/507f1f77bcf86cd799999999/test-runs/latest"
    )
    assert response.status_code == 404


def test_page_latest_anonymous_returns_401(
    client: FlaskClient, database: Database, project: Project,
) -> None:
    website = _make_website(database, project)
    page = _make_page(database, website)
    response = client.get(
        f"/api/v1/pages/{page.id}/test-runs/latest"
    )
    assert response.status_code == 401


def test_page_latest_no_history_returns_nulls(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project,
) -> None:
    """Brand-new page → status=discovered, no active task, no last result."""
    website = _make_website(database, project)
    page = _make_page(database, website)
    login(admin_user)
    response = client.get(
        f"/api/v1/pages/{page.id}/test-runs/latest"
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["page_id"] == page.id
    assert body["status"] == "discovered"
    assert body["last_tested"] is None
    assert body["last_test_result_id"] is None
    assert body["active_task_id"] is None


def test_page_latest_carries_last_test_result_id(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project,
) -> None:
    """A persisted TestResult shows up as ``last_test_result_id``."""
    website = _make_website(database, project)
    page = _make_page(database, website, status=PageStatus.TESTED)
    assert page.id is not None
    assert website.id is not None
    result = TestResult(
        page_id=page.id, website_id=website.id, test_date=datetime.now(),
    )
    result_id = database.create_test_result(result)

    login(admin_user)
    response = client.get(
        f"/api/v1/pages/{page.id}/test-runs/latest"
    )
    body = response.get_json()
    assert body["status"] == "tested"
    assert body["last_test_result_id"] == result_id


def test_page_latest_carries_active_task_id(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live ``test_page_<id>_<ts>`` task surfaces as ``active_task_id``."""
    website = _make_website(database, project)
    page = _make_page(database, website, status=PageStatus.TESTING)
    assert page.id is not None
    fake_task_id = f"test_page_{page.id}_1234.5"

    monkeypatch.setattr(
        task_runner, "get_active_tasks",
        lambda: ["unrelated_task", fake_task_id, "other_test_page_xxx"],
    )

    login(admin_user)
    response = client.get(
        f"/api/v1/pages/{page.id}/test-runs/latest"
    )
    body = response.get_json()
    assert body["active_task_id"] == fake_task_id
    assert body["status"] == "testing"


def test_page_latest_ignores_unrelated_active_tasks(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Active tasks for OTHER pages don't leak into this page's response."""
    website = _make_website(database, project)
    page = _make_page(database, website)

    monkeypatch.setattr(
        task_runner, "get_active_tasks",
        lambda: [
            "test_page_OTHERPAGE_999.9",  # not this page
            "testing_some_website_abc",
        ],
    )

    login(admin_user)
    response = client.get(
        f"/api/v1/pages/{page.id}/test-runs/latest"
    )
    body = response.get_json()
    assert body["active_task_id"] is None
