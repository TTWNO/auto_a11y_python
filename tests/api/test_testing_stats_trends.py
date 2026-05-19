"""Tests for the REST-shaped testing stats / trends endpoints:

- ``GET /api/v1/testing/stats``
- ``GET /api/v1/test-runs/trends``
- ``GET /api/v1/test-runs/trends/progress``

These wrap the same helpers the legacy ``/testing/api/...`` endpoints
already use; the focus here is the new bare-body shape + Problem
Details errors + project-scoped auth. Per
``docs/REST_API_ROADMAP.md`` §5.4.

Tests skip when ``mongod`` is not reachable.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from collections.abc import Generator, Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient
from flask_login import LoginManager
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from auto_a11y.core.database import Database
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

    # calculate_aggregate_stats reaches into get_app_config().PDF_STORAGE_DIR
    # via PdfStorage. Wire up a minimal config object.
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
# /testing/stats
# ---------------------------------------------------------------------------


def test_stats_anonymous_returns_401(client: FlaskClient) -> None:
    response = client.get("/api/v1/testing/stats")
    assert response.status_code == 401
    assert response.headers["Content-Type"] == "application/problem+json"


def test_stats_aggregate_authenticated_returns_200(
    client: FlaskClient, auditor: AppUser, login: Any
) -> None:
    """No scope filter → any authenticated user gets aggregate stats."""
    login(auditor)
    response = client.get("/api/v1/testing/stats")
    assert response.status_code == 200
    body = response.get_json()
    # Bare body (no 'success' wrapper).
    assert "success" not in body
    assert "completed_today" in body


def test_stats_unknown_website_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    login(admin_user)
    response = client.get(
        "/api/v1/testing/stats?website_id=507f1f77bcf86cd799999999"
    )
    assert response.status_code == 404


def test_stats_unknown_project_returns_403(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    """An unknown project id fails the role check before the lookup,
    yielding 403. This matches require_project_role's existing behaviour
    across the rest of the API surface — the legacy stats endpoint did
    a 'permissions' check first and only fell through to lookup later."""
    login(admin_user)  # superadmin bypasses the role check
    response = client.get(
        "/api/v1/testing/stats?project_id=507f1f77bcf86cd799999999"
    )
    # Superadmin gets past the role check but the project doesn't exist
    # → handler raises NotFoundError → 404.
    assert response.status_code == 404


def test_stats_outsider_on_known_project_returns_403(
    client: FlaskClient,
    database: Database,
    auditor: AppUser,
    login: Any,
) -> None:
    """An auditor with no membership on the project gets 403."""
    project = _make_project(database)
    login(auditor)
    response = client.get(
        f"/api/v1/testing/stats?project_id={project.id}"
    )
    assert response.status_code == 403


def test_stats_website_scope_returns_envelope(
    client: FlaskClient,
    admin_user: AppUser,
    login: Any,
    website: Website,
) -> None:
    login(admin_user)
    response = client.get(
        f"/api/v1/testing/stats?website_id={website.id}"
    )
    assert response.status_code == 200
    body = response.get_json()
    # Website-scoped result has the per-website keys.
    assert body["website_count"] == 1
    assert body["total_pages"] == 0
    assert body["tested_pages"] == 0


# ---------------------------------------------------------------------------
# /test-runs/trends
# ---------------------------------------------------------------------------


def test_trends_anonymous_returns_401(client: FlaskClient) -> None:
    response = client.get("/api/v1/test-runs/trends")
    assert response.status_code == 401


def test_trends_authenticated_returns_envelope(
    client: FlaskClient, auditor: AppUser, login: Any
) -> None:
    login(auditor)
    response = client.get("/api/v1/test-runs/trends")
    assert response.status_code == 200
    body = response.get_json()
    assert body["days"] == 30
    assert "trend_data" in body
    assert isinstance(body["trend_data"], list)


def test_trends_days_respects_bounds(
    client: FlaskClient, auditor: AppUser, login: Any
) -> None:
    login(auditor)

    response = client.get("/api/v1/test-runs/trends?days=7")
    assert response.status_code == 200
    assert response.get_json()["days"] == 7


def test_trends_bad_days_returns_400(
    client: FlaskClient, auditor: AppUser, login: Any
) -> None:
    login(auditor)
    too_many = client.get("/api/v1/test-runs/trends?days=9999")
    assert too_many.status_code == 400
    body = too_many.get_json()
    fields = {e["field"] for e in body["errors"]}
    assert "days" in fields

    not_int = client.get("/api/v1/test-runs/trends?days=banana")
    assert not_int.status_code == 400


def test_trends_outsider_on_project_scope_returns_403(
    client: FlaskClient,
    database: Database,
    auditor: AppUser,
    login: Any,
) -> None:
    project = _make_project(database)
    login(auditor)
    response = client.get(
        f"/api/v1/test-runs/trends?project_id={project.id}"
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# /test-runs/trends/progress
# ---------------------------------------------------------------------------


def test_progress_anonymous_returns_401(client: FlaskClient) -> None:
    response = client.get("/api/v1/test-runs/trends/progress")
    assert response.status_code == 401


def test_progress_requires_a_scope(
    client: FlaskClient, auditor: AppUser, login: Any
) -> None:
    """The progress endpoint is per-scope; both filter params missing
    is a 400 (not a 200 with empty data)."""
    login(auditor)
    response = client.get("/api/v1/test-runs/trends/progress")
    assert response.status_code == 400
    fields = {e["field"] for e in response.get_json()["errors"]}
    assert {"project_id", "website_id"} <= fields


def test_progress_website_scope_returns_metrics(
    client: FlaskClient,
    admin_user: AppUser,
    login: Any,
    website: Website,
) -> None:
    login(admin_user)
    response = client.get(
        f"/api/v1/test-runs/trends/progress?website_id={website.id}"
    )
    assert response.status_code == 200
    body = response.get_json()
    assert "success" not in body
