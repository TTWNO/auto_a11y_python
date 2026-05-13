"""Tests for the per-page sub-resource endpoints (§5.3):

- ``GET  /api/v1/pages/<id>/violations``
- ``GET  /api/v1/pages/<id>/matrix``
- ``PUT  /api/v1/pages/<id>/matrix``
- ``POST /api/v1/pages/<id>/test-runs/latest/cancel``

The fixture wiring mirrors the other tests under ``tests/api/`` —
ephemeral MongoDB per test, slim Flask app with just the API blueprint
and Flask-Login, superadmin user to bypass per-project ACL checks.

Tests skip when ``mongod`` is not reachable.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Generator, Iterator
from typing import Any

import pytest
from bson import ObjectId
from flask import Flask
from flask.testing import FlaskClient
from flask_login import LoginManager
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from auto_a11y.core.database import Database
from auto_a11y.core.task_runner import task_runner
from auto_a11y.models import (
    Page,
    PageStatus,
    ScriptStateDefinition,
    TestStateMatrix,
)
from auto_a11y.models.app_user import AppUser, UserRole
from auto_a11y.models.page_setup_script import PageSetupScript, ScriptScope
from auto_a11y.models.project import Project, ProjectStatus
from auto_a11y.models.test_result import ImpactLevel, TestResult, Violation
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
    database: Database, website: Website, *, status: PageStatus = PageStatus.DISCOVERED
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
def page(database: Database) -> Page:
    return _make_page(database, _make_website(database, _make_project(database)))


@pytest.fixture
def login(client: FlaskClient) -> Iterator[Any]:
    def _login(user: AppUser) -> None:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    yield _login


def _make_violation(*, code: str, impact: ImpactLevel, description: str) -> Violation:
    return Violation(
        id=code,
        impact=impact,
        touchpoint="forms",
        description=description,
    )


def _make_test_result(
    database: Database,
    page: Page,
    *,
    violations: list[Violation] | None = None,
    warnings: list[Violation] | None = None,
    info: list[Violation] | None = None,
    discovery: list[Violation] | None = None,
) -> TestResult:
    assert page.id is not None
    assert page.website_id is not None
    result = TestResult(
        page_id=page.id,
        website_id=page.website_id,
        violations=violations or [],
        warnings=warnings or [],
        info=info or [],
        discovery=discovery or [],
    )
    result_id = database.create_test_result(result)
    refreshed = database.get_test_result(result_id)
    assert refreshed is not None
    return refreshed


def _make_setup_script(
    database: Database,
    page: Page,
    *,
    name: str,
    test_before: bool = True,
    test_after: bool = True,
) -> PageSetupScript:
    assert page.id is not None
    assert page.website_id is not None
    script = PageSetupScript(
        name=name,
        description="",
        scope=ScriptScope.PAGE,
        page_id=page.id,
        website_id=page.website_id,
        test_before_execution=test_before,
        test_after_execution=test_after,
        enabled=True,
    )
    sid = database.create_page_setup_script(script)
    script.mongo_id = ObjectId(sid)
    return script


# ---------------------------------------------------------------------------
# GET /pages/<id>/violations
# ---------------------------------------------------------------------------


def test_violations_unknown_page_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
) -> None:
    login(admin_user)
    response = client.get("/api/v1/pages/507f1f77bcf86cd799999999/violations")
    assert response.status_code == 404


def test_violations_anonymous_returns_401(
    client: FlaskClient, page: Page,
) -> None:
    response = client.get(f"/api/v1/pages/{page.id}/violations")
    assert response.status_code == 401


def test_violations_empty_buckets_when_never_tested(
    client: FlaskClient, admin_user: AppUser, login: Any, page: Page,
) -> None:
    """A page with no test history returns 200 with empty arrays."""
    login(admin_user)
    response = client.get(f"/api/v1/pages/{page.id}/violations")
    assert response.status_code == 200
    body = response.get_json()
    assert body["page_id"] == page.id
    assert body["test_result_id"] is None
    assert body["tested_at"] is None
    assert body["violations"] == []
    assert body["warnings"] == []
    assert body["info"] == []
    assert body["discovery"] == []
    assert body["ai_findings"] == []


def test_violations_returns_latest_result_buckets(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, page: Page,
) -> None:
    """Issues are flattened from the latest TestResult."""
    result = _make_test_result(
        database, page,
        violations=[
            _make_violation(code="ErrNoAlt", impact=ImpactLevel.HIGH, description="missing alt"),
            _make_violation(code="ErrEmptyLabel", impact=ImpactLevel.HIGH, description="empty label"),
        ],
        warnings=[
            _make_violation(code="WarnLowContrast", impact=ImpactLevel.MEDIUM, description="low contrast"),
        ],
        info=[
            _make_violation(code="InfoLandmark", impact=ImpactLevel.LOW, description="landmark note"),
        ],
        discovery=[
            _make_violation(code="DiscoForm", impact=ImpactLevel.LOW, description="form discovered"),
        ],
    )

    login(admin_user)
    response = client.get(f"/api/v1/pages/{page.id}/violations")
    assert response.status_code == 200
    body = response.get_json()
    assert body["test_result_id"] == result.id
    assert body["tested_at"] is not None
    assert len(body["violations"]) == 2
    assert {v["id"] for v in body["violations"]} == {"ErrNoAlt", "ErrEmptyLabel"}
    assert len(body["warnings"]) == 1
    assert body["warnings"][0]["id"] == "WarnLowContrast"
    assert len(body["info"]) == 1
    assert len(body["discovery"]) == 1
    # Every issue carries the full Violation.to_dict() shape.
    assert "impact" in body["violations"][0]
    assert "touchpoint" in body["violations"][0]


# ---------------------------------------------------------------------------
# GET /pages/<id>/matrix
# ---------------------------------------------------------------------------


def test_matrix_get_unknown_page_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
) -> None:
    login(admin_user)
    response = client.get("/api/v1/pages/507f1f77bcf86cd799999999/matrix")
    assert response.status_code == 404


def test_matrix_get_anonymous_returns_401(
    client: FlaskClient, page: Page,
) -> None:
    response = client.get(f"/api/v1/pages/{page.id}/matrix")
    assert response.status_code == 401


def test_matrix_get_returns_default_when_unsaved(
    client: FlaskClient, admin_user: AppUser, login: Any, page: Page,
) -> None:
    """If no matrix exists, return a defaulted shape with id=null."""
    login(admin_user)
    response = client.get(f"/api/v1/pages/{page.id}/matrix")
    assert response.status_code == 200
    body = response.get_json()
    assert body["id"] is None
    assert body["page_id"] == page.id
    assert body["website_id"] == page.website_id
    assert body["scripts"] == []
    assert body["combinations"] == []


def test_matrix_get_includes_testable_scripts_for_unsaved(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, page: Page,
) -> None:
    """The defaulted matrix is seeded from the page's enabled scripts."""
    _make_setup_script(database, page, name="Cookie")
    _make_setup_script(database, page, name="Modal")

    login(admin_user)
    response = client.get(f"/api/v1/pages/{page.id}/matrix")
    body = response.get_json()
    assert body["id"] is None
    assert len(body["scripts"]) == 2
    assert {s["script_name"] for s in body["scripts"]} == {"Cookie", "Modal"}
    # initialize_matrix() produces N+1 sequential combinations
    # (initial + one per test_after script).
    assert len(body["combinations"]) >= 1


def test_matrix_get_returns_persisted_matrix(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, page: Page,
) -> None:
    assert page.id is not None
    assert page.website_id is not None
    matrix = TestStateMatrix(
        page_id=page.id,
        website_id=page.website_id,
        scripts=[ScriptStateDefinition(
            script_id="script-abc",
            script_name="My Script",
            test_before=True,
            test_after=True,
            execution_order=0,
        )],
        combinations=[{"script-abc": "before"}, {"script-abc": "after"}],
    )
    saved_id = database.create_test_state_matrix(matrix)

    login(admin_user)
    response = client.get(f"/api/v1/pages/{page.id}/matrix")
    body = response.get_json()
    assert body["id"] == saved_id
    assert len(body["combinations"]) == 2
    assert body["combinations"][0] == {"script-abc": "before"}


# ---------------------------------------------------------------------------
# PUT /pages/<id>/matrix
# ---------------------------------------------------------------------------


def test_matrix_put_unknown_page_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
) -> None:
    login(admin_user)
    response = client.put(
        "/api/v1/pages/507f1f77bcf86cd799999999/matrix",
        json={"combinations": []},
    )
    assert response.status_code == 404


def test_matrix_put_anonymous_returns_401(
    client: FlaskClient, page: Page,
) -> None:
    response = client.put(
        f"/api/v1/pages/{page.id}/matrix", json={"combinations": []}
    )
    assert response.status_code == 401


def test_matrix_put_creates_new_matrix(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, page: Page,
) -> None:
    """First PUT inserts a new matrix row."""
    script = _make_setup_script(database, page, name="Login")
    assert script.id is not None

    login(admin_user)
    response = client.put(
        f"/api/v1/pages/{page.id}/matrix",
        json={
            "combinations": [
                {script.id: "before"},
                {script.id: "after"},
            ],
        },
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["id"] is not None
    assert len(body["combinations"]) == 2
    assert len(body["scripts"]) == 1
    assert body["scripts"][0]["script_id"] == script.id

    # Round-trip: fetching the matrix back returns the same id.
    follow = client.get(f"/api/v1/pages/{page.id}/matrix")
    assert follow.get_json()["id"] == body["id"]


def test_matrix_put_updates_existing_matrix(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, page: Page,
) -> None:
    """Second PUT updates in place; matrix id is preserved."""
    script = _make_setup_script(database, page, name="Login")
    assert script.id is not None
    login(admin_user)

    first = client.put(
        f"/api/v1/pages/{page.id}/matrix",
        json={"combinations": [{script.id: "before"}]},
    )
    first_id = first.get_json()["id"]

    second = client.put(
        f"/api/v1/pages/{page.id}/matrix",
        json={"combinations": [{script.id: "after"}]},
    )
    body = second.get_json()
    assert body["id"] == first_id
    assert body["combinations"] == [{script.id: "after"}]


def test_matrix_put_applies_script_order(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, page: Page,
) -> None:
    """script_order is applied before sorting; scripts come back ordered."""
    s_a = _make_setup_script(database, page, name="Alpha")
    s_b = _make_setup_script(database, page, name="Beta")
    assert s_a.id is not None and s_b.id is not None

    login(admin_user)
    response = client.put(
        f"/api/v1/pages/{page.id}/matrix",
        json={
            "combinations": [{s_a.id: "before", s_b.id: "before"}],
            "script_order": [
                {"script_id": s_a.id, "execution_order": 1},
                {"script_id": s_b.id, "execution_order": 0},
            ],
        },
    )
    body = response.get_json()
    order = [s["script_id"] for s in body["scripts"]]
    # Beta (order 0) comes before Alpha (order 1).
    assert order == [s_b.id, s_a.id]


def test_matrix_put_rejects_non_object_body(
    client: FlaskClient, admin_user: AppUser, login: Any, page: Page,
) -> None:
    login(admin_user)
    response = client.put(
        f"/api/v1/pages/{page.id}/matrix", json=[]
    )
    assert response.status_code == 400


def test_matrix_put_rejects_missing_combinations(
    client: FlaskClient, admin_user: AppUser, login: Any, page: Page,
) -> None:
    login(admin_user)
    response = client.put(f"/api/v1/pages/{page.id}/matrix", json={})
    assert response.status_code == 400
    body = response.get_json()
    assert any(
        err["field"] == "combinations" for err in body.get("errors", [])
    )


def test_matrix_put_rejects_invalid_state_value(
    client: FlaskClient, admin_user: AppUser, login: Any, page: Page,
) -> None:
    login(admin_user)
    response = client.put(
        f"/api/v1/pages/{page.id}/matrix",
        json={"combinations": [{"script-1": "sideways"}]},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# POST /pages/<id>/test-runs/latest/cancel
# ---------------------------------------------------------------------------


def test_cancel_unknown_page_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/pages/507f1f77bcf86cd799999999/test-runs/latest/cancel"
    )
    assert response.status_code == 404


def test_cancel_anonymous_returns_401(
    client: FlaskClient, page: Page,
) -> None:
    response = client.post(
        f"/api/v1/pages/{page.id}/test-runs/latest/cancel"
    )
    assert response.status_code == 401


def test_cancel_returns_409_when_page_not_running(
    client: FlaskClient, admin_user: AppUser, login: Any, page: Page,
) -> None:
    """page.status == DISCOVERED → no in-flight test → 409."""
    login(admin_user)
    response = client.post(
        f"/api/v1/pages/{page.id}/test-runs/latest/cancel"
    )
    assert response.status_code == 409


def test_cancel_flips_queued_back_to_discovered(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, page: Page,
) -> None:
    """A QUEUED page with no live task is still resolved back to DISCOVERED."""
    page.status = PageStatus.QUEUED
    database.update_page(page)
    assert page.id is not None

    login(admin_user)
    response = client.post(
        f"/api/v1/pages/{page.id}/test-runs/latest/cancel"
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["page_id"] == page.id
    assert body["status"] == "discovered"
    assert body["cancellation_requested"] is False

    refreshed = database.get_page(page.id)
    assert refreshed is not None
    assert refreshed.status == PageStatus.DISCOVERED


def test_cancel_flips_queued_back_to_tested_when_history_exists(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, page: Page,
) -> None:
    """A QUEUED retry on a page that has prior results flips back to TESTED."""
    _make_test_result(database, page)
    page.status = PageStatus.QUEUED
    database.update_page(page)
    assert page.id is not None

    login(admin_user)
    response = client.post(
        f"/api/v1/pages/{page.id}/test-runs/latest/cancel"
    )
    body = response.get_json()
    assert body["status"] == "tested"


def test_cancel_calls_task_runner_when_task_active(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, page: Page,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A TESTING page with a live task triggers task_runner.cancel_task."""
    page.status = PageStatus.TESTING
    database.update_page(page)
    assert page.id is not None
    fake_task_id = f"test_page_{page.id}_1234.5"

    cancel_calls: list[str] = []
    monkeypatch.setattr(
        task_runner, "get_active_tasks", lambda: [fake_task_id]
    )

    def fake_cancel(task_id: str) -> bool:
        cancel_calls.append(task_id)
        return True

    monkeypatch.setattr(task_runner, "cancel_task", fake_cancel)

    login(admin_user)
    response = client.post(
        f"/api/v1/pages/{page.id}/test-runs/latest/cancel"
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["task_id"] == fake_task_id
    assert body["cancellation_requested"] is True
    assert cancel_calls == [fake_task_id]
