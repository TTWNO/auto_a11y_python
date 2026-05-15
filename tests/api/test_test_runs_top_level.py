"""Tests for the top-level §5.4 endpoints:

- ``POST /api/v1/test-runs``        — single-page generic
- ``POST /api/v1/test-runs/batch``  — multi-page generic
- ``GET  /api/v1/testing/config``   — read runtime testing config
- ``PUT  /api/v1/testing/config``   — replace runtime testing config

``task_runner.submit_task`` is monkey-patched so no real browser test
is launched. ``mongod`` is required for the routes that hit the DB.
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
    """Stub task_runner.submit_task so tests don't spawn real browsers."""
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


@pytest.fixture
def non_admin_user(database: Database) -> AppUser:
    return _make_user(
        database, role=UserRole.AUDITOR, email="auditor@example.test"
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


def _make_page(
    database: Database, website: Website
) -> Page:
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
def two_pages(database: Database) -> tuple[Page, Page]:
    website = _make_website(database, _make_project(database))
    return _make_page(database, website), _make_page(database, website)


@pytest.fixture
def login(client: FlaskClient) -> Iterator[Any]:
    def _login(user: AppUser) -> None:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    yield _login


# ---------------------------------------------------------------------------
# POST /test-runs (single-page)
# ---------------------------------------------------------------------------


def test_test_run_anonymous_returns_401(
    client: FlaskClient, page: Page,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    response = client.post("/api/v1/test-runs", json={"page_id": page.id})
    assert response.status_code == 401
    assert submitted_tasks == []


def test_test_run_unknown_page_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/test-runs", json={"page_id": "507f1f77bcf86cd799999999"}
    )
    assert response.status_code == 404
    assert submitted_tasks == []


def test_test_run_missing_page_id_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post("/api/v1/test-runs", json={})
    assert response.status_code == 400
    body = response.get_json()
    assert any(err["field"] == "page_id" for err in body.get("errors", []))
    assert submitted_tasks == []


def test_test_run_non_object_body_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post("/api/v1/test-runs", json=[])
    assert response.status_code == 400
    assert submitted_tasks == []


def test_test_run_invalid_enable_multi_state_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any, page: Page,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/test-runs",
        json={"page_id": page.id, "enable_multi_state": "yes"},
    )
    assert response.status_code == 400
    assert submitted_tasks == []


def test_test_run_happy_path_returns_202(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, page: Page,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post("/api/v1/test-runs", json={"page_id": page.id})
    assert response.status_code == 202
    body = response.get_json()
    assert body["page_id"] == page.id
    assert body["multi_state"] is True
    assert body["status"] == "queued"
    assert body["job_id"].startswith(f"test_page_{page.id}_")
    assert len(submitted_tasks) == 1

    # The service helper moves the page to QUEUED.
    refreshed = database.get_page(page.id) if page.id else None
    assert refreshed is not None
    assert refreshed.status == PageStatus.QUEUED


def test_test_run_respects_enable_multi_state_false(
    client: FlaskClient, admin_user: AppUser, login: Any, page: Page,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/test-runs",
        json={"page_id": page.id, "enable_multi_state": False},
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["multi_state"] is False


# ---------------------------------------------------------------------------
# POST /test-runs/batch
# ---------------------------------------------------------------------------


def test_batch_anonymous_returns_401(
    client: FlaskClient, page: Page,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    response = client.post(
        "/api/v1/test-runs/batch", json={"page_ids": [page.id]}
    )
    assert response.status_code == 401
    assert submitted_tasks == []


def test_batch_missing_page_ids_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post("/api/v1/test-runs/batch", json={})
    assert response.status_code == 400
    assert submitted_tasks == []


def test_batch_empty_page_ids_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/test-runs/batch", json={"page_ids": []}
    )
    assert response.status_code == 400
    assert submitted_tasks == []


def test_batch_unknown_id_returns_404_and_queues_nothing(
    client: FlaskClient, admin_user: AppUser, login: Any, page: Page,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    """A typo in any id should fail the whole batch — no half-queued runs."""
    login(admin_user)
    response = client.post(
        "/api/v1/test-runs/batch",
        json={"page_ids": [page.id, "507f1f77bcf86cd799999999"]},
    )
    assert response.status_code == 404
    assert submitted_tasks == []


def test_batch_happy_path_queues_one_per_page(
    client: FlaskClient, admin_user: AppUser, login: Any,
    two_pages: tuple[Page, Page],
    submitted_tasks: list[dict[str, Any]],
) -> None:
    p1, p2 = two_pages
    login(admin_user)
    response = client.post(
        "/api/v1/test-runs/batch", json={"page_ids": [p1.id, p2.id]}
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["pages_queued"] == 2
    assert body["status"] == "queued"
    page_ids_in_response = [run["page_id"] for run in body["runs"]]
    assert page_ids_in_response == [p1.id, p2.id]
    assert len(submitted_tasks) == 2


def test_batch_non_string_id_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/test-runs/batch", json={"page_ids": ["ok", 123]}
    )
    assert response.status_code == 400
    assert submitted_tasks == []


# ---------------------------------------------------------------------------
# GET /testing/config
# ---------------------------------------------------------------------------


def test_testing_config_get_anonymous_returns_401(
    client: FlaskClient,
) -> None:
    response = client.get("/api/v1/testing/config")
    assert response.status_code == 401


def test_testing_config_get_non_admin_returns_403(
    client: FlaskClient, non_admin_user: AppUser, login: Any,
) -> None:
    login(non_admin_user)
    response = client.get("/api/v1/testing/config")
    assert response.status_code == 403


def test_testing_config_get_returns_full_shape(
    client: FlaskClient, admin_user: AppUser, login: Any,
) -> None:
    login(admin_user)
    response = client.get("/api/v1/testing/config")
    assert response.status_code == 200
    body = response.get_json()
    # All 9 fields documented in §5.4 are present.
    expected = {
        "parallel_tests", "test_timeout", "run_ai_analysis",
        "browser_headless", "viewport_width", "viewport_height",
        "pages_per_page", "max_pages_per_page", "show_error_codes",
    }
    assert set(body) == expected


# ---------------------------------------------------------------------------
# PUT /testing/config
# ---------------------------------------------------------------------------


def test_testing_config_put_anonymous_returns_401(
    client: FlaskClient,
) -> None:
    response = client.put(
        "/api/v1/testing/config", json={"parallel_tests": 4}
    )
    assert response.status_code == 401


def test_testing_config_put_non_admin_returns_403(
    client: FlaskClient, non_admin_user: AppUser, login: Any,
) -> None:
    login(non_admin_user)
    response = client.put(
        "/api/v1/testing/config", json={"parallel_tests": 4}
    )
    assert response.status_code == 403


def test_testing_config_put_unknown_field_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
) -> None:
    login(admin_user)
    response = client.put(
        "/api/v1/testing/config",
        json={"parallel_tests": 4, "secret_setting": "hax"},
    )
    assert response.status_code == 400
    body = response.get_json()
    assert any(
        err["field"] == "secret_setting" for err in body.get("errors", [])
    )


def test_testing_config_put_bool_for_int_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
) -> None:
    """`True` is a valid int in Python, but JSON booleans must not coerce."""
    login(admin_user)
    response = client.put(
        "/api/v1/testing/config", json={"parallel_tests": True}
    )
    assert response.status_code == 400


def test_testing_config_put_int_for_bool_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
) -> None:
    login(admin_user)
    response = client.put(
        "/api/v1/testing/config", json={"run_ai_analysis": 1}
    )
    assert response.status_code == 400


def test_testing_config_put_partial_update_applies_changes(
    client: FlaskClient, admin_user: AppUser, login: Any, flask_app: Flask,
) -> None:
    """Setting two keys updates exactly those keys on the live Config."""
    login(admin_user)
    response = client.put(
        "/api/v1/testing/config",
        json={"parallel_tests": 7, "browser_headless": False},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["parallel_tests"] == 7
    assert body["browser_headless"] is False

    # The change is visible on the live Config object.
    cfg = getattr(flask_app, "app_config")
    assert cfg.PARALLEL_TESTS == 7
    assert cfg.BROWSER_HEADLESS is False


def test_testing_config_put_returns_full_post_update(
    client: FlaskClient, admin_user: AppUser, login: Any,
) -> None:
    """The response is the full config, not just the keys touched."""
    login(admin_user)
    response = client.put(
        "/api/v1/testing/config", json={"parallel_tests": 9}
    )
    body = response.get_json()
    assert len(body) == 9
    assert body["parallel_tests"] == 9
