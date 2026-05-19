"""Tests for the async-action cancel + latest-status REST endpoints:

- ``POST /api/v1/websites/<id>/discoveries/latest/cancel``
- ``POST /api/v1/discoveries/<run_id>/cancel``
- ``GET  /api/v1/websites/<id>/test-runs/latest``
- ``POST /api/v1/websites/<id>/test-runs/latest/cancel``

These wrap existing JobManager state — *starting* test runs and
discoveries is still deferred.  Per ``docs/REST_API_ROADMAP.md``
§5.2 and §5.4.

Tests skip when ``mongod`` is not reachable.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Generator, Iterator
from datetime import datetime, timedelta
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
from auto_a11y.models.discovery_run import DiscoveryRun, DiscoveryStatus
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
    # JobManager is a singleton (see auto_a11y/core/job_manager.py:46);
    # without resetting its cached instance the next test's
    # JobManager(database) call keeps a stale reference to *this*
    # test's now-closed MongoClient and every read raises
    # ``InvalidOperation: Cannot use MongoClient after close``.
    setattr(JobManager, "_instance", None)
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


def _create_running_testing_job(
    database: Database, *, website_id: str, job_id: str | None = None
) -> str:
    jm = JobManager(database)
    actual = job_id or f"test-{uuid.uuid4().hex[:8]}"
    jm.create_job(
        job_id=actual,
        job_type=JobType.TESTING,
        website_id=website_id,
        metadata={},
    )
    jm.update_job_status(actual, JobStatus.RUNNING)
    return actual


def _insert_discovery_run(
    database: Database,
    *,
    website_id: str,
    job_id: str | None,
    is_latest: bool = True,
    status: DiscoveryStatus = DiscoveryStatus.RUNNING,
) -> DiscoveryRun:
    run = DiscoveryRun(
        website_id=website_id,
        started_at=datetime.now() - timedelta(minutes=2),
        status=status,
        is_latest=is_latest,
        job_id=job_id,
    )
    inserted = database.discovery_runs.insert_one(run.to_dict())
    run.mongo_id = inserted.inserted_id
    return run


# ---------------------------------------------------------------------------
# /websites/<id>/discoveries/latest/cancel
# ---------------------------------------------------------------------------


def test_cancel_latest_discovery_anonymous_returns_401(
    client: FlaskClient, website: Website
) -> None:
    response = client.post(
        f"/api/v1/websites/{website.id}/discoveries/latest/cancel"
    )
    assert response.status_code == 401


def test_cancel_latest_discovery_unknown_website_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/websites/507f1f77bcf86cd799999999/"
        + "discoveries/latest/cancel"
    )
    assert response.status_code == 404


def test_cancel_latest_discovery_no_runs_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any, website: Website
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/websites/{website.id}/discoveries/latest/cancel"
    )
    assert response.status_code == 404


def test_cancel_latest_discovery_outsider_returns_403(
    client: FlaskClient,
    database: Database,
    auditor: AppUser,
    login: Any,
    website: Website,
) -> None:
    assert website.id is not None
    job_id = _create_running_testing_job(
        database, website_id=website.id, job_id="some-running-job"
    )
    _insert_discovery_run(
        database, website_id=website.id, job_id=job_id
    )
    login(auditor)
    response = client.post(
        f"/api/v1/websites/{website.id}/discoveries/latest/cancel"
    )
    assert response.status_code == 403


def test_cancel_latest_discovery_happy_path_returns_202(
    client: FlaskClient,
    database: Database,
    admin_user: AppUser,
    login: Any,
    website: Website,
) -> None:
    assert website.id is not None
    job_id = "discovery-running"
    jm = JobManager(database)
    jm.create_job(
        job_id=job_id,
        job_type=JobType.DISCOVERY,
        website_id=website.id,
        metadata={},
    )
    jm.update_job_status(job_id, JobStatus.RUNNING)
    _insert_discovery_run(
        database, website_id=website.id, job_id=job_id
    )
    login(admin_user)

    response = client.post(
        f"/api/v1/websites/{website.id}/discoveries/latest/cancel"
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["job_id"] == job_id
    assert body["status"] == JobStatus.CANCELLING.value
    assert body["cancellation_requested"] is True


def test_cancel_latest_discovery_without_job_id_returns_409(
    client: FlaskClient,
    database: Database,
    admin_user: AppUser,
    login: Any,
    website: Website,
) -> None:
    """A legacy DiscoveryRun with no job_id can't be cancelled."""
    assert website.id is not None
    _insert_discovery_run(
        database, website_id=website.id, job_id=None
    )
    login(admin_user)
    response = client.post(
        f"/api/v1/websites/{website.id}/discoveries/latest/cancel"
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# /discoveries/<run_id>/cancel
# ---------------------------------------------------------------------------


def test_cancel_specific_discovery_unknown_run_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/discoveries/507f1f77bcf86cd799999999/cancel"
    )
    assert response.status_code == 404


def test_cancel_specific_discovery_happy_path(
    client: FlaskClient,
    database: Database,
    admin_user: AppUser,
    login: Any,
    website: Website,
) -> None:
    assert website.id is not None
    job_id = "discovery-explicit"
    jm = JobManager(database)
    jm.create_job(
        job_id=job_id,
        job_type=JobType.DISCOVERY,
        website_id=website.id,
        metadata={},
    )
    jm.update_job_status(job_id, JobStatus.RUNNING)
    run = _insert_discovery_run(
        database, website_id=website.id, job_id=job_id, is_latest=False
    )
    login(admin_user)

    response = client.post(f"/api/v1/discoveries/{run.id}/cancel")
    assert response.status_code == 202
    body = response.get_json()
    assert body["job_id"] == job_id


def test_cancel_specific_discovery_already_completed_returns_409(
    client: FlaskClient,
    database: Database,
    admin_user: AppUser,
    login: Any,
    website: Website,
) -> None:
    assert website.id is not None
    job_id = "discovery-done"
    jm = JobManager(database)
    jm.create_job(
        job_id=job_id,
        job_type=JobType.DISCOVERY,
        website_id=website.id,
        metadata={},
    )
    jm.update_job_status(job_id, JobStatus.COMPLETED)
    run = _insert_discovery_run(
        database,
        website_id=website.id,
        job_id=job_id,
        status=DiscoveryStatus.COMPLETED,
    )
    login(admin_user)

    response = client.post(f"/api/v1/discoveries/{run.id}/cancel")
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# /websites/<id>/test-runs/latest  (GET)
# ---------------------------------------------------------------------------


def test_latest_test_run_anonymous_returns_401(
    client: FlaskClient, website: Website
) -> None:
    response = client.get(f"/api/v1/websites/{website.id}/test-runs/latest")
    assert response.status_code == 401


def test_latest_test_run_unknown_website_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    login(admin_user)
    response = client.get(
        "/api/v1/websites/507f1f77bcf86cd799999999/test-runs/latest"
    )
    assert response.status_code == 404


def test_latest_test_run_no_runs_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any, website: Website
) -> None:
    login(admin_user)
    response = client.get(
        f"/api/v1/websites/{website.id}/test-runs/latest"
    )
    assert response.status_code == 404


def test_latest_test_run_returns_newest_testing_job(
    client: FlaskClient,
    database: Database,
    admin_user: AppUser,
    login: Any,
    website: Website,
) -> None:
    assert website.id is not None
    # An older job and a newer one — the newer must win.
    _create_running_testing_job(
        database, website_id=website.id, job_id="older"
    )
    # Insert with a manually-overridden created_at via raw update.
    jm = JobManager(database)
    jm.collection.update_one(
        {"job_id": "older"},
        {"$set": {"created_at": datetime.now() - timedelta(days=3)}},
    )
    _create_running_testing_job(
        database, website_id=website.id, job_id="newer"
    )
    login(admin_user)

    response = client.get(
        f"/api/v1/websites/{website.id}/test-runs/latest"
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["job_id"] == "newer"


def test_latest_test_run_ignores_non_testing_jobs(
    client: FlaskClient,
    database: Database,
    admin_user: AppUser,
    login: Any,
    website: Website,
) -> None:
    """A REPORT_GENERATION job on the same website must NOT match."""
    assert website.id is not None
    jm = JobManager(database)
    jm.create_job(
        job_id="report-job",
        job_type=JobType.REPORT_GENERATION,
        website_id=website.id,
        metadata={},
    )
    jm.update_job_status("report-job", JobStatus.RUNNING)
    login(admin_user)

    response = client.get(
        f"/api/v1/websites/{website.id}/test-runs/latest"
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# /websites/<id>/test-runs/latest/cancel
# ---------------------------------------------------------------------------


def test_cancel_latest_test_run_anonymous_returns_401(
    client: FlaskClient, website: Website
) -> None:
    response = client.post(
        f"/api/v1/websites/{website.id}/test-runs/latest/cancel"
    )
    assert response.status_code == 401


def test_cancel_latest_test_run_no_runs_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any, website: Website
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/websites/{website.id}/test-runs/latest/cancel"
    )
    assert response.status_code == 404


def test_cancel_latest_test_run_happy_path(
    client: FlaskClient,
    database: Database,
    admin_user: AppUser,
    login: Any,
    website: Website,
) -> None:
    assert website.id is not None
    job_id = _create_running_testing_job(
        database, website_id=website.id, job_id="cancel-me"
    )
    login(admin_user)

    response = client.post(
        f"/api/v1/websites/{website.id}/test-runs/latest/cancel"
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["job_id"] == job_id
    assert body["status"] == JobStatus.CANCELLING.value


def test_cancel_latest_test_run_already_completed_returns_409(
    client: FlaskClient,
    database: Database,
    admin_user: AppUser,
    login: Any,
    website: Website,
) -> None:
    assert website.id is not None
    jm = JobManager(database)
    jm.create_job(
        job_id="already-done",
        job_type=JobType.TESTING,
        website_id=website.id,
        metadata={},
    )
    jm.update_job_status("already-done", JobStatus.COMPLETED)
    login(admin_user)
    response = client.post(
        f"/api/v1/websites/{website.id}/test-runs/latest/cancel"
    )
    assert response.status_code == 409
