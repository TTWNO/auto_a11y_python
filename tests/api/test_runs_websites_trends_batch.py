"""Tests for the §5.2 / §5.4 batch:

- ``GET /api/v1/test-runs?status=active``
- ``GET /api/v1/websites?project_id=<id>``
- ``GET /api/v1/test-runs/trends/detailed``
- ``GET /api/v1/test-runs/trends/compare``

Tests skip when ``mongod`` is not reachable.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from collections.abc import Generator, Iterator
from datetime import datetime, timedelta
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
    db.client.drop_database(db_name)
    db.close()


@pytest.fixture
def pdf_storage_dir() -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory(prefix="auto-a11y-test-pdfs-") as path:
        yield Path(path)


@pytest.fixture
def flask_app(database: Database, pdf_storage_dir: Path) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-deterministic-for-tests-only"
    app.config["WTF_CSRF_ENABLED"] = False
    setattr(app, "db", database)

    # Trends/detailed and trends/compare helpers reach into the typed app
    # config for PDF_STORAGE_DIR via the aggregate-stats helpers.
    from config import Config
    cfg = Config()
    cfg.PDF_STORAGE_DIR = str(pdf_storage_dir)
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


def _make_project(database: Database) -> Project:
    project = Project(
        name=f"Project {uuid.uuid4().hex[:8]}",
        description="",
        status=ProjectStatus.ACTIVE,
        config={},
    )
    project_id = database.create_project(project)
    saved = database.get_project(project_id)
    assert saved is not None
    return saved


def _make_website(database: Database, project: Project) -> Website:
    project_id = project.id
    assert project_id is not None
    website = Website(
        project_id=project_id,
        url=f"https://example-{uuid.uuid4().hex[:8]}.test/",
        name="Test website",
    )
    website_id = database.create_website(website)
    saved = database.get_website(website_id)
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
# /test-runs?status=active
# ---------------------------------------------------------------------------


def test_test_runs_anonymous_returns_401(client: FlaskClient) -> None:
    response = client.get("/api/v1/test-runs?status=active")
    assert response.status_code == 401


def test_test_runs_requires_active_status(
    client: FlaskClient, auditor: AppUser, login: Any
) -> None:
    login(auditor)
    missing = client.get("/api/v1/test-runs")
    assert missing.status_code == 400
    fields = {e["field"] for e in missing.get_json()["errors"]}
    assert "status" in fields

    wrong = client.get("/api/v1/test-runs?status=completed")
    assert wrong.status_code == 400


def test_test_runs_returns_active_testing_jobs(
    client: FlaskClient,
    database: Database,
    auditor: AppUser,
    login: Any,
    website: Website,
) -> None:
    """A RUNNING TESTING job should appear; a COMPLETED one should not."""
    assert website.id is not None
    jm = JobManager(database)
    jm.create_job(
        job_id="running-job",
        job_type=JobType.TESTING,
        website_id=website.id,
        metadata={},
    )
    jm.update_job_status("running-job", JobStatus.RUNNING)
    jm.create_job(
        job_id="completed-job",
        job_type=JobType.TESTING,
        website_id=website.id,
        metadata={},
    )
    jm.update_job_status("completed-job", JobStatus.COMPLETED)
    # And a non-TESTING active job to ensure type filtering works.
    jm.create_job(
        job_id="report-job",
        job_type=JobType.REPORT_GENERATION,
        metadata={},
    )
    jm.update_job_status("report-job", JobStatus.RUNNING)

    login(auditor)
    response = client.get("/api/v1/test-runs?status=active")
    assert response.status_code == 200
    body = response.get_json()
    job_ids = {item["job_id"] for item in body["items"]}
    assert "running-job" in job_ids
    assert "completed-job" not in job_ids
    assert "report-job" not in job_ids
    # Website name is resolved.
    [running] = [j for j in body["items"] if j["job_id"] == "running-job"]
    assert running["website_id"] == website.id
    assert running["website_name"] == website.name


# ---------------------------------------------------------------------------
# /websites?project_id=...
# ---------------------------------------------------------------------------


def test_websites_anonymous_returns_401(client: FlaskClient) -> None:
    response = client.get("/api/v1/websites?project_id=anything")
    assert response.status_code == 401


def test_websites_non_superadmin_requires_project_id(
    client: FlaskClient, auditor: AppUser, login: Any
) -> None:
    login(auditor)
    response = client.get("/api/v1/websites")
    assert response.status_code == 400
    fields = {e["field"] for e in response.get_json()["errors"]}
    assert "project_id" in fields


def test_websites_unknown_project_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    login(admin_user)
    response = client.get(
        "/api/v1/websites?project_id=507f1f77bcf86cd799999999"
    )
    assert response.status_code == 404


def test_websites_outsider_returns_403(
    client: FlaskClient,
    database: Database,
    auditor: AppUser,
    login: Any,
) -> None:
    project = _make_project(database)
    login(auditor)
    response = client.get(f"/api/v1/websites?project_id={project.id}")
    assert response.status_code == 403


def test_websites_returns_project_websites(
    client: FlaskClient,
    database: Database,
    admin_user: AppUser,
    login: Any,
) -> None:
    project = _make_project(database)
    w1 = _make_website(database, project)
    w2 = _make_website(database, project)
    login(admin_user)

    response = client.get(f"/api/v1/websites?project_id={project.id}")
    assert response.status_code == 200
    body = response.get_json()
    ids = {item["id"] for item in body["items"]}
    assert ids == {w1.id, w2.id}


def test_websites_superadmin_no_filter_lists_all(
    client: FlaskClient,
    database: Database,
    admin_user: AppUser,
    login: Any,
) -> None:
    project_a = _make_project(database)
    project_b = _make_project(database)
    a = _make_website(database, project_a)
    b = _make_website(database, project_b)
    login(admin_user)

    response = client.get("/api/v1/websites")
    assert response.status_code == 200
    body = response.get_json()
    ids = {item["id"] for item in body["items"]}
    assert {a.id, b.id} <= ids


# ---------------------------------------------------------------------------
# /test-runs/trends/detailed
# ---------------------------------------------------------------------------


def test_trends_detailed_anonymous_returns_401(client: FlaskClient) -> None:
    response = client.get("/api/v1/test-runs/trends/detailed")
    assert response.status_code == 401


def test_trends_detailed_bad_granularity_returns_400(
    client: FlaskClient, auditor: AppUser, login: Any
) -> None:
    login(auditor)
    response = client.get(
        "/api/v1/test-runs/trends/detailed?granularity=hourly"
    )
    assert response.status_code == 400
    fields = {e["field"] for e in response.get_json()["errors"]}
    assert "granularity" in fields


def test_trends_detailed_bad_date_returns_400(
    client: FlaskClient, auditor: AppUser, login: Any
) -> None:
    login(auditor)
    response = client.get(
        "/api/v1/test-runs/trends/detailed?start_date=not-a-date"
    )
    assert response.status_code == 400
    fields = {e["field"] for e in response.get_json()["errors"]}
    assert "start_date" in fields


def test_trends_detailed_authenticated_returns_envelope(
    client: FlaskClient, auditor: AppUser, login: Any
) -> None:
    login(auditor)
    response = client.get("/api/v1/test-runs/trends/detailed")
    assert response.status_code == 200
    body = response.get_json()
    # Bare body — no 'success' wrapper.
    assert "success" not in body


# ---------------------------------------------------------------------------
# /test-runs/trends/compare
# ---------------------------------------------------------------------------


def test_trends_compare_anonymous_returns_401(client: FlaskClient) -> None:
    response = client.get("/api/v1/test-runs/trends/compare")
    assert response.status_code == 401


def test_trends_compare_missing_dates_returns_400(
    client: FlaskClient, auditor: AppUser, login: Any
) -> None:
    login(auditor)
    response = client.get("/api/v1/test-runs/trends/compare")
    assert response.status_code == 400
    fields = {e["field"] for e in response.get_json()["errors"]}
    # Validates the *first* missing field; the body's structured error
    # array is enough — we don't require all four be listed.
    assert "period_a_start" in fields


def test_trends_compare_period_ordering_validated(
    client: FlaskClient, auditor: AppUser, login: Any
) -> None:
    login(auditor)
    url = (
        "/api/v1/test-runs/trends/compare?period_a_start=2026-05-10"
        + "&period_a_end=2026-05-05"
        + "&period_b_start=2026-05-01&period_b_end=2026-05-03"
    )
    response = client.get(url)
    assert response.status_code == 400
    fields = {e["field"] for e in response.get_json()["errors"]}
    assert "period_a_start" in fields


def test_trends_compare_happy_path(
    client: FlaskClient, auditor: AppUser, login: Any
) -> None:
    login(auditor)
    today = datetime.now().date()
    b_end = today
    b_start = today - timedelta(days=7)
    a_end = b_start - timedelta(days=1)
    a_start = a_end - timedelta(days=7)

    url = (
        "/api/v1/test-runs/trends/compare"
        f"?period_a_start={a_start.isoformat()}"
        f"&period_a_end={a_end.isoformat()}"
        f"&period_b_start={b_start.isoformat()}"
        f"&period_b_end={b_end.isoformat()}"
    )
    response = client.get(url)
    assert response.status_code == 200
    body = response.get_json()
    assert "period_a" in body
    assert "period_b" in body
    assert "change" in body
