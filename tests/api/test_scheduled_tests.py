"""Tests for the ``/api/v1/scheduled-tests`` REST endpoints.

Coverage:

- Anonymous requests get 401 with a Problem-Details body.
- Authenticated callers without the right role get 403.
- Missing resources get 404.
- Round-trip: POST → GET → PATCH → PUT → GET → DELETE → 404.
- Validation errors carry RFC 7807 ``errors`` arrays with field paths.
- List endpoint paginates by cursor.
- Action endpoints (``/runs``, ``/preview``) react correctly when the
  scheduler service is unavailable (the test environment never wires
  one up — that's a follow-up that lands with a real scheduler stub).

Tests skip when ``mongod`` is not reachable. CI provisions one; local
runs need ``mongod`` listening on the default port (or
``MONGODB_URI`` set).
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Generator, Iterator
from datetime import datetime
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
    """Skip the whole module unless mongod is reachable."""
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
    """Per-test ephemeral test database; dropped on teardown."""
    db_name = f"auto_a11y_api_test_{uuid.uuid4().hex}"
    db = Database(_mongo_uri(), db_name)
    yield db
    db.client.drop_database(db_name)
    db.close()


@pytest.fixture
def flask_app(database: Database) -> Flask:
    """Slim Flask app: api_bp + Flask-Login + db, no migrations or runners."""
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


def _make_project(
    database: Database, *, members: list[tuple[str, list[str]]] | None = None
) -> Project:
    project = Project(
        name=f"Project {uuid.uuid4().hex[:8]}",
        description="",
        status=ProjectStatus.ACTIVE,
        config={},
    )
    project_id = database.create_project(project)
    if members:
        for user_id, group_ids in members:
            database.add_project_member(project_id, user_id, group_ids)
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
    """A superadmin who passes every project_role_required check."""
    user = _make_user(database, role=UserRole.ADMIN, email="admin@example.test")
    user.is_superadmin = True
    database.update_app_user(user)
    refreshed = database.get_app_user(user.id) if user.id else None
    assert refreshed is not None
    return refreshed


@pytest.fixture
def website(database: Database) -> Website:
    return _make_website(database, _make_project(database))


@pytest.fixture
def login(client: FlaskClient) -> Iterator[Any]:
    """Helper context manager-style fixture: ``login(user)`` to set session."""

    def _login(user: AppUser) -> None:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    yield _login


def _valid_create_body() -> dict[str, Any]:
    return {
        "name": "Nightly run",
        "schedule_type": "daily",
        "preset_config": {"time": "02:00"},
        "test_config": {
            "run_javascript_tests": True,
            "run_python_tests": True,
            "take_screenshots": True,
        },
        "enabled": False,  # avoid scheduler interaction by default
    }


# ---------------------------------------------------------------------------
# Auth matrix
# ---------------------------------------------------------------------------


def test_anonymous_request_returns_401(
    client: FlaskClient, website: Website
) -> None:
    response = client.get(f"/api/v1/websites/{website.id}/scheduled-tests")
    assert response.status_code == 401
    assert response.headers["Content-Type"] == "application/problem+json"
    body = response.get_json()
    assert body["type"].endswith("/unauthorized")


def test_authenticated_non_member_returns_403(
    client: FlaskClient,
    database: Database,
    website: Website,
    login: Any,
) -> None:
    """A logged-in user with no project membership should get 403, not 200."""
    user = _make_user(
        database, role=UserRole.AUDITOR, email="outsider@example.test"
    )
    login(user)

    response = client.get(f"/api/v1/websites/{website.id}/scheduled-tests")
    assert response.status_code == 403
    body = response.get_json()
    assert body["type"].endswith("/forbidden")


def test_unknown_website_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    login(admin_user)
    response = client.get(
        "/api/v1/websites/507f1f77bcf86cd799999999/scheduled-tests"
    )
    assert response.status_code == 404
    body = response.get_json()
    assert body["type"].endswith("/not-found")


# ---------------------------------------------------------------------------
# CRUD round-trip
# ---------------------------------------------------------------------------


def test_create_then_get_returns_resource(
    client: FlaskClient,
    admin_user: AppUser,
    website: Website,
    login: Any,
) -> None:
    login(admin_user)
    create = client.post(
        f"/api/v1/websites/{website.id}/scheduled-tests",
        json=_valid_create_body(),
    )
    assert create.status_code == 201, create.get_json()
    assert create.headers["Location"].startswith("/api/v1/scheduled-tests/")
    body = create.get_json()
    schedule_id = body["id"]
    assert body["name"] == "Nightly run"

    get = client.get(f"/api/v1/scheduled-tests/{schedule_id}")
    assert get.status_code == 200
    assert get.get_json()["id"] == schedule_id


def test_patch_updates_only_provided_fields(
    client: FlaskClient,
    admin_user: AppUser,
    website: Website,
    login: Any,
) -> None:
    login(admin_user)
    create = client.post(
        f"/api/v1/websites/{website.id}/scheduled-tests",
        json=_valid_create_body(),
    )
    schedule_id = create.get_json()["id"]

    response = client.patch(
        f"/api/v1/scheduled-tests/{schedule_id}",
        json={"description": "now with a description"},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["description"] == "now with a description"
    assert body["name"] == "Nightly run"  # untouched


def test_put_replaces_full_resource(
    client: FlaskClient,
    admin_user: AppUser,
    website: Website,
    login: Any,
) -> None:
    login(admin_user)
    create = client.post(
        f"/api/v1/websites/{website.id}/scheduled-tests",
        json=_valid_create_body(),
    )
    schedule_id = create.get_json()["id"]

    new_body = _valid_create_body() | {"name": "Replaced", "enabled": False}
    response = client.put(
        f"/api/v1/scheduled-tests/{schedule_id}", json=new_body
    )
    assert response.status_code == 200
    assert response.get_json()["name"] == "Replaced"


def test_delete_returns_204_then_get_returns_404(
    client: FlaskClient,
    admin_user: AppUser,
    website: Website,
    login: Any,
) -> None:
    login(admin_user)
    create = client.post(
        f"/api/v1/websites/{website.id}/scheduled-tests",
        json=_valid_create_body(),
    )
    schedule_id = create.get_json()["id"]

    delete = client.delete(f"/api/v1/scheduled-tests/{schedule_id}")
    assert delete.status_code == 204
    assert delete.data == b""

    get = client.get(f"/api/v1/scheduled-tests/{schedule_id}")
    assert get.status_code == 404


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_create_with_empty_name_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    website: Website,
    login: Any,
) -> None:
    login(admin_user)
    body = _valid_create_body() | {"name": ""}
    response = client.post(
        f"/api/v1/websites/{website.id}/scheduled-tests", json=body
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["type"].endswith("/validation")
    field_errors = {e["field"] for e in payload["errors"]}
    assert "name" in field_errors


def test_create_with_unknown_schedule_type_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    website: Website,
    login: Any,
) -> None:
    login(admin_user)
    body = _valid_create_body() | {"schedule_type": "fortnightly"}
    response = client.post(
        f"/api/v1/websites/{website.id}/scheduled-tests", json=body
    )
    assert response.status_code == 400
    payload = response.get_json()
    field_errors = {e["field"] for e in payload["errors"]}
    assert "schedule_type" in field_errors


def test_create_one_time_without_datetime_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    website: Website,
    login: Any,
) -> None:
    login(admin_user)
    body = _valid_create_body() | {"schedule_type": "one_time"}
    response = client.post(
        f"/api/v1/websites/{website.id}/scheduled-tests", json=body
    )
    assert response.status_code == 400
    payload = response.get_json()
    field_errors = {e["field"] for e in payload["errors"]}
    assert "scheduled_datetime" in field_errors


def test_create_cron_without_expression_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    website: Website,
    login: Any,
) -> None:
    login(admin_user)
    body = _valid_create_body() | {"schedule_type": "cron"}
    response = client.post(
        f"/api/v1/websites/{website.id}/scheduled-tests", json=body
    )
    assert response.status_code == 400
    payload = response.get_json()
    field_errors = {e["field"] for e in payload["errors"]}
    assert "cron_expression" in field_errors


def test_create_with_non_object_body_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    website: Website,
    login: Any,
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/websites/{website.id}/scheduled-tests",
        json=["not", "a", "dict"],
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


def test_list_paginates_with_cursor(
    client: FlaskClient,
    admin_user: AppUser,
    website: Website,
    login: Any,
) -> None:
    login(admin_user)
    for n in range(3):
        body = _valid_create_body() | {"name": f"S{n}"}
        client.post(
            f"/api/v1/websites/{website.id}/scheduled-tests", json=body
        )

    page1 = client.get(
        f"/api/v1/websites/{website.id}/scheduled-tests?limit=2"
    )
    assert page1.status_code == 200
    page1_body = page1.get_json()
    assert len(page1_body["items"]) == 2
    assert page1_body["next_cursor"] is not None

    cursor = page1_body["next_cursor"]
    page2 = client.get(
        f"/api/v1/websites/{website.id}/scheduled-tests?limit=2&cursor={cursor}"
    )
    assert page2.status_code == 200
    page2_body = page2.get_json()
    assert len(page2_body["items"]) == 1
    assert page2_body["next_cursor"] is None


def test_list_with_malformed_cursor_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    website: Website,
    login: Any,
) -> None:
    login(admin_user)
    response = client.get(
        f"/api/v1/websites/{website.id}/scheduled-tests?cursor=not-a-cursor!@#"
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Action endpoints — scheduler is not available in test environment.
# Both /runs and /preview return 409 in that case, which is what we assert.
# Full happy-path coverage requires a scheduler stub fixture; deferred to a
# follow-up PR per ``docs/REST_API_ROADMAP.md``.
# ---------------------------------------------------------------------------


def test_runs_returns_409_when_scheduler_unavailable(
    client: FlaskClient,
    admin_user: AppUser,
    website: Website,
    login: Any,
) -> None:
    login(admin_user)
    create = client.post(
        f"/api/v1/websites/{website.id}/scheduled-tests",
        json=_valid_create_body(),
    )
    schedule_id = create.get_json()["id"]

    response = client.post(f"/api/v1/scheduled-tests/{schedule_id}/runs")
    assert response.status_code == 409
    body = response.get_json()
    assert body["type"].endswith("/conflict")


def test_preview_returns_409_when_scheduler_unavailable(
    client: FlaskClient,
    admin_user: AppUser,
    website: Website,
    login: Any,
) -> None:
    login(admin_user)
    create = client.post(
        f"/api/v1/websites/{website.id}/scheduled-tests",
        json=_valid_create_body(),
    )
    schedule_id = create.get_json()["id"]

    response = client.get(f"/api/v1/scheduled-tests/{schedule_id}/preview")
    assert response.status_code == 409


# Suppress unused-import warnings for tests that don't reference these.
_ = datetime
