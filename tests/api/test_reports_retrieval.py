"""Tests for the reports-retrieval trio (§5.5, deferred from the
generation slice that landed in the previous commit):

- ``GET    /api/v1/reports/<id>/file``
- ``DELETE /api/v1/reports/<id>``
- ``GET    /api/v1/projects/<id>/report-summary``

Report ids are JobManager job_ids — the tests seed completed report
jobs directly via :class:`JobManager` to keep generation out of the
test path.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Generator, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient
from flask_login import LoginManager
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from auto_a11y.core.database import Database
from auto_a11y.core.job_manager import JobManager, JobStatus, JobType
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
    setattr(JobManager, "_instance", None)
    db.client.drop_database(db_name)
    db.close()


@pytest.fixture
def reports_dir(tmp_path: Path) -> Path:
    """Per-test isolated reports dir that gets wired into Config."""
    return tmp_path


@pytest.fixture
def flask_app(database: Database, reports_dir: Path) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-deterministic-for-tests-only"
    app.config["WTF_CSRF_ENABLED"] = False
    setattr(app, "db", database)

    from config import Config
    cfg = Config()
    cfg.REPORTS_DIR = reports_dir
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


@pytest.fixture
def project(database: Database) -> Project:
    return _make_project(database)


@pytest.fixture
def website(database: Database, project: Project) -> Website:
    return _make_website(database, project)


@pytest.fixture
def login(client: FlaskClient) -> Iterator[Any]:
    def _login(user: AppUser) -> None:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    yield _login


def _seed_completed_report(
    database: Database,
    reports_dir: Path,
    *,
    job_id: str,
    project_id: str | None = None,
    website_id: str | None = None,
    report_type: str = "html",
    scope: str = "project",
    filename: str | None = None,
    create_file: bool = True,
) -> Path:
    """Insert a COMPLETED report-generation job + (optionally) its
    on-disk file. Returns the file path even when ``create_file=False``
    so tests can stat-verify or simulate a missing file."""
    jm = JobManager(database)
    file_name = filename or f"report_{uuid.uuid4().hex[:8]}.{report_type}"
    file_path = reports_dir / file_name
    if create_file:
        file_path.write_text("dummy report contents", encoding="utf-8")

    jm.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        project_id=project_id,
        website_id=website_id,
        metadata={
            "scope": scope,
            "report_type": report_type,
            "display_name": f"Report {job_id}",
        },
    )
    jm.update_job_status(
        job_id, JobStatus.COMPLETED,
        result={"filename": file_name, "path": str(file_path)},
    )
    return file_path


# ---------------------------------------------------------------------------
# GET /reports/<id>/file
# ---------------------------------------------------------------------------


def test_download_unknown_id_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
) -> None:
    login(admin_user)
    response = client.get("/api/v1/reports/missing_id/file")
    assert response.status_code == 404


def test_download_non_report_job_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database,
) -> None:
    """A TESTING job with the same id space must not be downloadable."""
    jm = JobManager(database)
    jm.create_job(
        job_id="testing_abc", job_type=JobType.TESTING,
        metadata={"scope": "website"},
    )
    login(admin_user)
    response = client.get("/api/v1/reports/testing_abc/file")
    assert response.status_code == 404


def test_download_pending_report_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project,
) -> None:
    """Generation not finished → no file to send."""
    jm = JobManager(database)
    jm.create_job(
        job_id="report_pending", job_type=JobType.REPORT_GENERATION,
        project_id=project.id,
        metadata={"scope": "project", "report_type": "html"},
    )
    login(admin_user)
    response = client.get("/api/v1/reports/report_pending/file")
    assert response.status_code == 404


def test_download_missing_file_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project, reports_dir: Path,
) -> None:
    """Job COMPLETED but the file was unlinked outside the API → 404."""
    _seed_completed_report(
        database, reports_dir, job_id="report_filegone",
        project_id=project.id, create_file=False,
    )
    login(admin_user)
    response = client.get("/api/v1/reports/report_filegone/file")
    assert response.status_code == 404


def test_download_anonymous_returns_401(
    client: FlaskClient, database: Database, project: Project, reports_dir: Path,
) -> None:
    _seed_completed_report(
        database, reports_dir, job_id="report_dl1", project_id=project.id,
    )
    response = client.get("/api/v1/reports/report_dl1/file")
    assert response.status_code == 401


def test_download_happy_path_serves_file(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project, reports_dir: Path,
) -> None:
    _seed_completed_report(
        database, reports_dir, job_id="report_dl2",
        project_id=project.id, filename="myreport.html",
    )
    login(admin_user)
    response = client.get("/api/v1/reports/report_dl2/file")
    assert response.status_code == 200
    cd = response.headers.get("Content-Disposition", "")
    assert "myreport.html" in cd
    assert response.data == b"dummy report contents"


def test_download_for_website_scoped_report(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, website: Website, reports_dir: Path,
) -> None:
    """Auth path that resolves via website_id (no project_id on job)."""
    _seed_completed_report(
        database, reports_dir, job_id="report_dl3",
        website_id=website.id, scope="website",
    )
    login(admin_user)
    response = client.get("/api/v1/reports/report_dl3/file")
    assert response.status_code == 200


def test_download_all_scope_requires_superadmin(
    client: FlaskClient, non_admin_user: AppUser, login: Any,
    database: Database, reports_dir: Path,
) -> None:
    """Job with no project_id/website_id ("all" rollup) is superadmin-only."""
    _seed_completed_report(
        database, reports_dir, job_id="report_dl4", scope="all",
    )
    login(non_admin_user)
    response = client.get("/api/v1/reports/report_dl4/file")
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /reports/<id>
# ---------------------------------------------------------------------------


def test_delete_unknown_id_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
) -> None:
    login(admin_user)
    response = client.delete("/api/v1/reports/missing_id")
    assert response.status_code == 404


def test_delete_anonymous_returns_401(
    client: FlaskClient, database: Database, project: Project, reports_dir: Path,
) -> None:
    _seed_completed_report(
        database, reports_dir, job_id="report_del1", project_id=project.id,
    )
    response = client.delete("/api/v1/reports/report_del1")
    assert response.status_code == 401


def test_delete_removes_file_and_record(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project, reports_dir: Path,
) -> None:
    file_path = _seed_completed_report(
        database, reports_dir, job_id="report_del2", project_id=project.id,
    )
    assert file_path.exists()

    login(admin_user)
    response = client.delete("/api/v1/reports/report_del2")
    assert response.status_code == 204
    assert not file_path.exists()
    jm = JobManager(database)
    assert jm.get_job("report_del2") is None


def test_delete_idempotent_when_file_already_gone(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project, reports_dir: Path,
) -> None:
    """Job record exists but file removed externally — still returns 204
    and deletes the record."""
    _seed_completed_report(
        database, reports_dir, job_id="report_del3",
        project_id=project.id, create_file=False,
    )
    login(admin_user)
    response = client.delete("/api/v1/reports/report_del3")
    assert response.status_code == 204
    jm = JobManager(database)
    assert jm.get_job("report_del3") is None


# ---------------------------------------------------------------------------
# GET /projects/<id>/report-summary
# ---------------------------------------------------------------------------


def test_summary_unknown_project_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
) -> None:
    login(admin_user)
    response = client.get(
        "/api/v1/projects/507f1f77bcf86cd799999999/report-summary"
    )
    assert response.status_code == 404


def test_summary_anonymous_returns_401(
    client: FlaskClient, project: Project,
) -> None:
    response = client.get(
        f"/api/v1/projects/{project.id}/report-summary"
    )
    assert response.status_code == 401


def test_summary_empty_project_returns_zero_counts(
    client: FlaskClient, admin_user: AppUser, login: Any, project: Project,
) -> None:
    login(admin_user)
    response = client.get(
        f"/api/v1/projects/{project.id}/report-summary"
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["project_id"] == project.id
    assert body["total_completed"] == 0
    assert body["by_scope"] == {}
    assert body["by_report_type"] == {}
    assert body["recent"] == []


def test_summary_aggregates_by_scope_and_type(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project, reports_dir: Path,
) -> None:
    _seed_completed_report(
        database, reports_dir, job_id="r1",
        project_id=project.id, scope="project", report_type="html",
    )
    _seed_completed_report(
        database, reports_dir, job_id="r2",
        project_id=project.id, scope="project", report_type="xlsx",
    )
    _seed_completed_report(
        database, reports_dir, job_id="r3",
        project_id=project.id, scope="discovery_project", report_type="html",
    )
    login(admin_user)
    response = client.get(
        f"/api/v1/projects/{project.id}/report-summary"
    )
    body = response.get_json()
    assert body["total_completed"] == 3
    assert body["by_scope"]["project"] == 2
    assert body["by_scope"]["discovery_project"] == 1
    assert body["by_report_type"]["html"] == 2
    assert body["by_report_type"]["xlsx"] == 1


def test_summary_omits_failed_jobs(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project,
) -> None:
    """The summary only counts COMPLETED jobs."""
    jm = JobManager(database)
    jm.create_job(
        job_id="r_fail", job_type=JobType.REPORT_GENERATION,
        project_id=project.id,
        metadata={"scope": "project", "report_type": "html"},
    )
    jm.update_job_status("r_fail", JobStatus.FAILED, error="boom")

    login(admin_user)
    response = client.get(
        f"/api/v1/projects/{project.id}/report-summary"
    )
    body = response.get_json()
    assert body["total_completed"] == 0


def test_summary_recent_capped_at_ten(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project, reports_dir: Path,
) -> None:
    """``recent`` returns at most the latest 10 even with more jobs."""
    for idx in range(12):
        _seed_completed_report(
            database, reports_dir, job_id=f"rN{idx}",
            project_id=project.id,
        )
    login(admin_user)
    response = client.get(
        f"/api/v1/projects/{project.id}/report-summary"
    )
    body = response.get_json()
    assert body["total_completed"] == 12
    assert len(body["recent"]) == 10


def test_summary_isolated_by_project(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, reports_dir: Path,
) -> None:
    """Project A's summary doesn't see Project B's reports."""
    proj_a = _make_project(database)
    proj_b = _make_project(database)
    _seed_completed_report(
        database, reports_dir, job_id="rA1", project_id=proj_a.id,
    )
    _seed_completed_report(
        database, reports_dir, job_id="rB1", project_id=proj_b.id,
    )

    login(admin_user)
    response = client.get(
        f"/api/v1/projects/{proj_a.id}/report-summary"
    )
    body = response.get_json()
    assert body["total_completed"] == 1
    assert body["recent"][0]["id"] == "rA1"


def test_summary_recent_carries_summary_shape(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project, reports_dir: Path,
) -> None:
    """Sanity-check each summary item exposes the documented keys."""
    _seed_completed_report(
        database, reports_dir, job_id="rShape",
        project_id=project.id, scope="project", report_type="xlsx",
        filename="shape-report.xlsx",
    )
    login(admin_user)
    response = client.get(
        f"/api/v1/projects/{project.id}/report-summary"
    )
    body = response.get_json()
    item = body["recent"][0]
    assert item["id"] == "rShape"
    assert item["scope"] == "project"
    assert item["report_type"] == "xlsx"
    assert item["filename"] == "shape-report.xlsx"
    assert item["created_at"] is not None
    # Sanity: created_at parses as ISO 8601.
    datetime.fromisoformat(item["created_at"])
