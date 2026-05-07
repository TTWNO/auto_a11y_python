"""Tests for the /api/v1/jobs/<id> + /api/v1/jobs/<id>/cancel REST endpoints.

Covers:

- 401 anonymous, 404 unknown
- GET serialization shape (datetimes ISO 8601, no Mongo `_id`)
- POST cancel returns 202 + the updated CANCELLING document
- POST cancel against a completed job returns 409 (idempotent reject)
- Two cancels in a row: first 202, second 409

Tests skip when ``mongod`` is not reachable.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Generator, Iterator
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
    # JobManager is a singleton — reset its cached instance so the next
    # test fixture binds to the new database. Without this reset,
    # JobManager keeps a reference to the previous test's now-dropped
    # database and silently no-ops every read. Reach in via setattr to
    # avoid pyright's private-attr warning.
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


def _make_user(database: Database, *, email: str) -> AppUser:
    user = AppUser.create(email=email, password="x", role=UserRole.AUDITOR)
    user_id = database.create_app_user(user)
    saved = database.get_app_user(user_id)
    assert saved is not None
    return saved


@pytest.fixture
def authed_user(database: Database) -> AppUser:
    return _make_user(database, email="auth@example.test")


@pytest.fixture
def login(client: FlaskClient) -> Iterator[Any]:
    def _login(user: AppUser) -> None:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    yield _login


@pytest.fixture
def pending_job(database: Database) -> dict[str, Any]:
    """Create a single PENDING job and return its document."""
    job_manager = JobManager(database)
    job_id = f"job-{uuid.uuid4().hex[:8]}"
    return job_manager.create_job(
        job_id=job_id,
        job_type=JobType.TESTING,
        website_id=None,
        project_id=None,
    )


# ---------------------------------------------------------------------------
# Auth + 404
# ---------------------------------------------------------------------------


def test_anonymous_get_returns_401(
    client: FlaskClient, pending_job: dict[str, Any]
) -> None:
    response = client.get(f"/api/v1/jobs/{pending_job['job_id']}")
    assert response.status_code == 401
    assert response.headers["Content-Type"] == "application/problem+json"


def test_anonymous_cancel_returns_401(
    client: FlaskClient, pending_job: dict[str, Any]
) -> None:
    response = client.post(f"/api/v1/jobs/{pending_job['job_id']}/cancel")
    assert response.status_code == 401


def test_unknown_job_get_returns_404(
    client: FlaskClient, authed_user: AppUser, login: Any
) -> None:
    login(authed_user)
    response = client.get("/api/v1/jobs/does-not-exist")
    assert response.status_code == 404


def test_unknown_job_cancel_returns_404(
    client: FlaskClient, authed_user: AppUser, login: Any
) -> None:
    login(authed_user)
    response = client.post("/api/v1/jobs/does-not-exist/cancel")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET shape
# ---------------------------------------------------------------------------


def test_get_returns_serialized_job(
    client: FlaskClient,
    authed_user: AppUser,
    pending_job: dict[str, Any],
    login: Any,
) -> None:
    login(authed_user)
    response = client.get(f"/api/v1/jobs/{pending_job['job_id']}")
    assert response.status_code == 200
    body = response.get_json()
    assert body["job_id"] == pending_job["job_id"]
    assert body["job_type"] == "testing"
    assert body["status"] == "pending"
    # Datetimes serialize to ISO 8601 strings, not raw datetimes.
    assert isinstance(body["created_at"], str) and "T" in body["created_at"]
    # Mongo `_id` is intentionally dropped — the public id is `job_id`.
    assert "_id" not in body


# ---------------------------------------------------------------------------
# Cancel happy path + idempotency
# ---------------------------------------------------------------------------


def test_cancel_pending_job_returns_202(
    client: FlaskClient,
    authed_user: AppUser,
    pending_job: dict[str, Any],
    login: Any,
) -> None:
    login(authed_user)
    response = client.post(f"/api/v1/jobs/{pending_job['job_id']}/cancel")
    assert response.status_code == 202
    body = response.get_json()
    assert body["status"] == "cancelling"
    assert body["cancellation_requested"] is True
    assert body["cancellation_requested_by"] == str(authed_user.id)


def test_double_cancel_second_call_returns_409(
    client: FlaskClient,
    authed_user: AppUser,
    pending_job: dict[str, Any],
    login: Any,
) -> None:
    """request_cancellation rejects a second transition once the job is
    already in CANCELLING — the REST endpoint surfaces that as 409."""
    login(authed_user)
    first = client.post(f"/api/v1/jobs/{pending_job['job_id']}/cancel")
    assert first.status_code == 202
    second = client.post(f"/api/v1/jobs/{pending_job['job_id']}/cancel")
    assert second.status_code == 409
    body = second.get_json()
    assert body["type"].endswith("/conflict")


def test_cancel_completed_job_returns_409(
    client: FlaskClient,
    authed_user: AppUser,
    database: Database,
    pending_job: dict[str, Any],
    login: Any,
) -> None:
    """A COMPLETED job cannot be cancelled — the underlying request_cancellation
    only accepts PENDING/RUNNING transitions."""
    job_manager = JobManager(database)
    job_manager.update_job_status(pending_job["job_id"], JobStatus.COMPLETED)

    login(authed_user)
    response = client.post(f"/api/v1/jobs/{pending_job['job_id']}/cancel")
    assert response.status_code == 409
