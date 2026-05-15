"""Tests for the §5.5 reports endpoints:

- ``POST /api/v1/pages/<id>/reports``
- ``POST /api/v1/websites/<id>/reports``
- ``POST /api/v1/projects/<id>/reports``
- ``POST /api/v1/reports``
- ``POST /api/v1/jobs/<id>/restart``

``task_runner.submit_task`` is monkey-patched so no real report
generator runs; we only verify the routing + scope resolution + job
record creation. The actual generators are tested elsewhere.
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
from auto_a11y.core.job_manager import JobManager, JobStatus, JobType
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
    # JobManager is a singleton; tests that touch it leak state across
    # databases unless reset.
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
def flask_app(database: Database, tmp_path: Any) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-deterministic-for-tests-only"
    app.config["WTF_CSRF_ENABLED"] = False
    setattr(app, "db", database)

    from config import Config
    cfg = Config()
    cfg.REPORTS_DIR = tmp_path  # avoid touching the real reports dir
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
def non_admin_user(database: Database) -> AppUser:
    return _make_user(
        database, role=UserRole.AUDITOR, email="other@example.test"
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
def project(database: Database) -> Project:
    return _make_project(database)


@pytest.fixture
def website(database: Database, project: Project) -> Website:
    return _make_website(database, project)


@pytest.fixture
def page(database: Database, website: Website) -> Page:
    return _make_page(database, website)


@pytest.fixture
def login(client: FlaskClient) -> Iterator[Any]:
    def _login(user: AppUser) -> None:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    yield _login


def _read_job_metadata(database: Database, job_id: str) -> dict[str, Any]:
    """Helper: fetch the JobManager record's metadata dict."""
    from typing import cast
    jm = JobManager(database)
    record = jm.get_job(job_id)
    assert record is not None
    metadata_any: Any = record.get("metadata") or {}
    assert isinstance(metadata_any, dict)
    return cast(dict[str, Any], metadata_any)


# ---------------------------------------------------------------------------
# POST /pages/<id>/reports
# ---------------------------------------------------------------------------


def test_page_report_anonymous_returns_401(
    client: FlaskClient, page: Page,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    response = client.post(f"/api/v1/pages/{page.id}/reports", json={})
    assert response.status_code == 401
    assert submitted_tasks == []


def test_page_report_unknown_id_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/pages/507f1f77bcf86cd799999999/reports", json={}
    )
    assert response.status_code == 404
    assert submitted_tasks == []


def test_page_report_invalid_format_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any, page: Page,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/pages/{page.id}/reports", json={"format": "garbage"}
    )
    assert response.status_code == 400
    assert submitted_tasks == []


def test_page_report_happy_path_queues_job(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, page: Page,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/pages/{page.id}/reports",
        json={"format": "html", "include_ai": False},
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["scope"] == "page"
    assert body["status"] == "queued"
    assert body["job_id"].startswith("report_")
    assert len(submitted_tasks) == 1

    metadata = _read_job_metadata(database, body["job_id"])
    assert metadata["scope"] == "page"
    assert metadata["report_type"] == "html"
    assert metadata["include_ai"] is False
    assert metadata["page_id"] == page.id


# ---------------------------------------------------------------------------
# POST /websites/<id>/reports
# ---------------------------------------------------------------------------


def test_website_report_unknown_id_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/websites/507f1f77bcf86cd799999999/reports", json={}
    )
    assert response.status_code == 404
    assert submitted_tasks == []


def test_website_report_invalid_type_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any, website: Website,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/websites/{website.id}/reports",
        json={"type": "nonsense"},
    )
    assert response.status_code == 400
    assert submitted_tasks == []


@pytest.mark.parametrize("report_type,expected_scope", [
    ("accessibility", "website"),
    ("page-structure", "page_structure"),
    ("discovery", "discovery_website"),
])
def test_website_report_type_maps_to_scope(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, website: Website,
    submitted_tasks: list[dict[str, Any]],
    report_type: str, expected_scope: str,
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/websites/{website.id}/reports",
        json={"type": report_type, "format": "html"},
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["scope"] == expected_scope
    metadata = _read_job_metadata(database, body["job_id"])
    assert metadata["scope"] == expected_scope


def test_website_report_default_type_is_accessibility(
    client: FlaskClient, admin_user: AppUser, login: Any, website: Website,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/websites/{website.id}/reports", json={}
    )
    body = response.get_json()
    assert body["scope"] == "website"


# ---------------------------------------------------------------------------
# POST /projects/<id>/reports
# ---------------------------------------------------------------------------


def test_project_report_unknown_id_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/projects/507f1f77bcf86cd799999999/reports", json={}
    )
    assert response.status_code == 404
    assert submitted_tasks == []


@pytest.mark.parametrize("report_type,expected_scope", [
    ("accessibility", "project"),
    ("discovery", "discovery_project"),
    ("recordings", "recordings"),
    ("deduplicated", "deduplicated"),
])
def test_project_report_type_maps_to_scope(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project,
    submitted_tasks: list[dict[str, Any]],
    report_type: str, expected_scope: str,
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/projects/{project.id}/reports",
        json={"type": report_type, "format": "html"},
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["scope"] == expected_scope


def test_project_report_happy_path_queues_job(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/projects/{project.id}/reports", json={"format": "xlsx"}
    )
    assert response.status_code == 202
    assert len(submitted_tasks) == 1
    body = response.get_json()
    metadata = _read_job_metadata(database, body["job_id"])
    assert metadata["report_type"] == "xlsx"


# ---------------------------------------------------------------------------
# POST /reports (generic)
# ---------------------------------------------------------------------------


def test_generic_report_anonymous_returns_401(
    client: FlaskClient,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    response = client.post("/api/v1/reports", json={"type": "static-html"})
    assert response.status_code == 401
    assert submitted_tasks == []


def test_generic_report_no_scope_requires_superadmin(
    client: FlaskClient, non_admin_user: AppUser, login: Any,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    """No ids → roll-up across all projects → superadmin only."""
    login(non_admin_user)
    response = client.post(
        "/api/v1/reports", json={"type": "accessibility"}
    )
    assert response.status_code == 403
    assert submitted_tasks == []


def test_generic_report_with_project_id_resolves_to_project_scope(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/reports",
        json={"type": "accessibility", "project_id": project.id},
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["scope"] == "project"


def test_generic_report_with_website_id_resolves_to_website_scope(
    client: FlaskClient, admin_user: AppUser, login: Any,
    website: Website,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/reports",
        json={"type": "accessibility", "website_id": website.id},
    )
    body = response.get_json()
    assert body["scope"] == "website"


def test_generic_report_with_page_id_resolves_to_page_scope(
    client: FlaskClient, admin_user: AppUser, login: Any, page: Page,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/reports",
        json={"type": "accessibility", "page_id": page.id},
    )
    body = response.get_json()
    assert body["scope"] == "page"


def test_generic_report_discovery_with_website_id_uses_website_scope(
    client: FlaskClient, admin_user: AppUser, login: Any, website: Website,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/reports",
        json={"type": "discovery", "website_id": website.id},
    )
    body = response.get_json()
    assert body["scope"] == "discovery_website"


def test_generic_report_unknown_target_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/reports",
        json={
            "type": "accessibility",
            "project_id": "507f1f77bcf86cd799999999",
        },
    )
    assert response.status_code == 404
    assert submitted_tasks == []


# ---------------------------------------------------------------------------
# POST /jobs/<id>/restart
# ---------------------------------------------------------------------------


def test_restart_unknown_job_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
) -> None:
    login(admin_user)
    response = client.post("/api/v1/jobs/does-not-exist/restart")
    assert response.status_code == 404


def test_restart_anonymous_returns_401(
    client: FlaskClient, database: Database,
) -> None:
    # Seed a job so the 401 is from auth, not 404.
    jm = JobManager(database)
    jm.create_job(
        job_id="report_seed1", job_type=JobType.REPORT_GENERATION,
        metadata={"scope": "all", "report_type": "html"},
    )
    response = client.post("/api/v1/jobs/report_seed1/restart")
    assert response.status_code == 401


def test_restart_non_report_job_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database,
) -> None:
    """Only REPORT_GENERATION jobs can be restarted via this endpoint."""
    jm = JobManager(database)
    jm.create_job(
        job_id="testing_xyz", job_type=JobType.TESTING,
        metadata={"scope": "website"},
    )
    login(admin_user)
    response = client.post("/api/v1/jobs/testing_xyz/restart")
    assert response.status_code == 400


def test_restart_creates_fresh_job_with_same_metadata(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    jm = JobManager(database)
    jm.create_job(
        job_id="report_old1",
        job_type=JobType.REPORT_GENERATION,
        project_id=project.id,
        metadata={
            "scope": "project",
            "report_type": "html",
            "include_ai": True,
            "display_name": "Old Report",
        },
    )
    # Simulate the job having failed so the restart-as-cancel path is
    # exercised in its NOT-pending/running variant (no cancellation).
    jm.update_job_status("report_old1", JobStatus.FAILED, error="boom")

    login(admin_user)
    response = client.post("/api/v1/jobs/report_old1/restart")
    assert response.status_code == 202
    body = response.get_json()
    assert body["old_job_id"] == "report_old1"
    new_job_id = body["job_id"]
    assert new_job_id != "report_old1"
    assert new_job_id.startswith("report_")
    assert body["scope"] == "project"

    new_metadata = _read_job_metadata(database, new_job_id)
    assert new_metadata["scope"] == "project"
    assert new_metadata["report_type"] == "html"
    assert new_metadata["include_ai"] is True
    assert len(submitted_tasks) == 1


def test_restart_pending_job_requests_cancellation(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project,
    submitted_tasks: list[dict[str, Any]],
) -> None:
    """Restarting a still-pending job should also cancel the old one."""
    jm = JobManager(database)
    jm.create_job(
        job_id="report_pending1",
        job_type=JobType.REPORT_GENERATION,
        project_id=project.id,
        metadata={"scope": "project", "report_type": "html"},
    )

    login(admin_user)
    response = client.post("/api/v1/jobs/report_pending1/restart")
    assert response.status_code == 202

    old_record = jm.get_job("report_pending1")
    assert old_record is not None
    assert old_record.get("cancellation_requested") is True
