"""Tests for the ``/api/v1/projects/<project_id>/websites`` and
``/api/v1/websites/<website_id>`` REST endpoints.

Coverage:

- Anonymous requests get 401 with a Problem-Details body.
- Authenticated callers without the right role get 403.
- Missing resources get 404.
- Round-trip: POST → GET → PATCH → PUT → GET → DELETE → 404.
- Validation errors carry RFC 7807 ``errors`` arrays with field paths.
- List endpoint paginates by cursor.

Tests skip when ``mongod`` is not reachable. CI provisions one; local
runs need ``mongod`` listening on the default port (or ``MONGODB_URI``
set).
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
from auto_a11y.models.app_user import AppUser, UserRole
from auto_a11y.models.project import Project, ProjectStatus


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
def project(database: Database) -> Project:
    return _make_project(database)


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
        "url": f"https://example-{uuid.uuid4().hex[:6]}.test/",
        "name": "Test website",
    }


# ---------------------------------------------------------------------------
# Auth matrix
# ---------------------------------------------------------------------------


def test_anonymous_list_returns_401(
    client: FlaskClient, project: Project
) -> None:
    response = client.get(f"/api/v1/projects/{project.id}/websites")
    assert response.status_code == 401
    assert response.headers["Content-Type"] == "application/problem+json"
    body = response.get_json()
    assert body["type"].endswith("/unauthorized")


def test_anonymous_create_returns_401(
    client: FlaskClient, project: Project
) -> None:
    response = client.post(
        f"/api/v1/projects/{project.id}/websites", json=_valid_create_body()
    )
    assert response.status_code == 401


def test_authenticated_non_member_list_returns_403(
    client: FlaskClient,
    database: Database,
    project: Project,
    login: Any,
) -> None:
    user = _make_user(database, role=UserRole.AUDITOR, email="outsider@example.test")
    login(user)
    response = client.get(f"/api/v1/projects/{project.id}/websites")
    assert response.status_code == 403
    body = response.get_json()
    assert body["type"].endswith("/forbidden")


def test_unknown_project_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    login(admin_user)
    response = client.get("/api/v1/projects/507f1f77bcf86cd799999999/websites")
    assert response.status_code == 404
    body = response.get_json()
    assert body["type"].endswith("/not-found")


def test_unknown_website_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    login(admin_user)
    response = client.get("/api/v1/websites/507f1f77bcf86cd799999999")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# CRUD round-trip
# ---------------------------------------------------------------------------


def test_create_then_get_returns_resource(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    create = client.post(
        f"/api/v1/projects/{project.id}/websites", json=_valid_create_body()
    )
    assert create.status_code == 201, create.get_json()
    assert create.headers["Location"].startswith("/api/v1/websites/")
    body = create.get_json()
    website_id = body["id"]
    assert body["name"] == "Test website"
    assert body["project_id"] == project.id
    assert body["url"].startswith("https://example-")
    assert "scraping_config" in body

    get = client.get(f"/api/v1/websites/{website_id}")
    assert get.status_code == 200
    assert get.get_json()["id"] == website_id


def test_patch_updates_only_provided_fields(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    create = client.post(
        f"/api/v1/projects/{project.id}/websites", json=_valid_create_body()
    )
    website_id = create.get_json()["id"]
    original_url = create.get_json()["url"]

    response = client.patch(
        f"/api/v1/websites/{website_id}",
        json={"name": "Renamed website"},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["name"] == "Renamed website"
    assert body["url"] == original_url


def test_patch_scraping_config_replaces_nested_object(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    create = client.post(
        f"/api/v1/projects/{project.id}/websites", json=_valid_create_body()
    )
    website_id = create.get_json()["id"]

    response = client.patch(
        f"/api/v1/websites/{website_id}",
        json={"scraping_config": {"max_depth": 3, "respect_robots": False}},
    )
    assert response.status_code == 200
    cfg = response.get_json()["scraping_config"]
    assert cfg["max_depth"] == 3
    assert cfg["respect_robots"] is False


def test_put_replaces_full_resource(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    create = client.post(
        f"/api/v1/projects/{project.id}/websites", json=_valid_create_body()
    )
    website_id = create.get_json()["id"]

    new_body = _valid_create_body() | {"name": "Replaced"}
    response = client.put(f"/api/v1/websites/{website_id}", json=new_body)
    assert response.status_code == 200
    body = response.get_json()
    assert body["name"] == "Replaced"
    # project_id is server-managed; PUT cannot reassign it.
    assert body["project_id"] == project.id


def test_put_cannot_reassign_project_id(
    client: FlaskClient,
    admin_user: AppUser,
    database: Database,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    create = client.post(
        f"/api/v1/projects/{project.id}/websites", json=_valid_create_body()
    )
    website_id = create.get_json()["id"]
    other = _make_project(database)

    body = _valid_create_body() | {"project_id": other.id}
    response = client.put(f"/api/v1/websites/{website_id}", json=body)
    assert response.status_code == 200
    # Even though the request body included a different project_id, it
    # is silently ignored by the handler (which preserves the existing
    # value) — the PUT is for editable fields only.
    assert response.get_json()["project_id"] == project.id


def test_delete_returns_204_then_get_returns_404(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    create = client.post(
        f"/api/v1/projects/{project.id}/websites", json=_valid_create_body()
    )
    website_id = create.get_json()["id"]

    delete = client.delete(f"/api/v1/websites/{website_id}")
    assert delete.status_code == 204
    assert delete.data == b""

    get = client.get(f"/api/v1/websites/{website_id}")
    assert get.status_code == 404


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_create_with_missing_url_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/projects/{project.id}/websites", json={"name": "no url"}
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["type"].endswith("/validation")
    field_errors = {e["field"] for e in payload["errors"]}
    assert "url" in field_errors


def test_create_with_non_http_url_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/projects/{project.id}/websites",
        json={"url": "ftp://example.test/"},
    )
    assert response.status_code == 400
    payload = response.get_json()
    field_errors = {e["field"] for e in payload["errors"]}
    assert "url" in field_errors


def test_create_with_non_object_body_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/projects/{project.id}/websites", json=["not", "a", "dict"]
    )
    assert response.status_code == 400


def test_patch_with_invalid_scraping_config_type_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    create = client.post(
        f"/api/v1/projects/{project.id}/websites", json=_valid_create_body()
    )
    website_id = create.get_json()["id"]

    response = client.patch(
        f"/api/v1/websites/{website_id}",
        json={"scraping_config": {"max_depth": "deep"}},
    )
    assert response.status_code == 400
    payload = response.get_json()
    field_errors = {e["field"] for e in payload["errors"]}
    assert "scraping_config.max_depth" in field_errors


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


def test_list_paginates_with_cursor(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    for _ in range(3):
        body = _valid_create_body()
        client.post(f"/api/v1/projects/{project.id}/websites", json=body)

    page1 = client.get(f"/api/v1/projects/{project.id}/websites?limit=2")
    assert page1.status_code == 200
    page1_body = page1.get_json()
    assert len(page1_body["items"]) == 2
    assert page1_body["next_cursor"] is not None

    cursor = page1_body["next_cursor"]
    page2 = client.get(
        f"/api/v1/projects/{project.id}/websites?limit=2&cursor={cursor}"
    )
    assert page2.status_code == 200
    page2_body = page2.get_json()
    assert len(page2_body["items"]) == 1
    assert page2_body["next_cursor"] is None


def test_list_with_malformed_cursor_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    response = client.get(
        f"/api/v1/projects/{project.id}/websites?cursor=not-a-cursor!@#"
    )
    assert response.status_code == 400


def test_list_isolates_websites_by_project(
    client: FlaskClient,
    admin_user: AppUser,
    database: Database,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    other = _make_project(database)
    # Create one in each project.
    client.post(
        f"/api/v1/projects/{project.id}/websites", json=_valid_create_body()
    )
    client.post(
        f"/api/v1/projects/{other.id}/websites", json=_valid_create_body()
    )

    list_main = client.get(f"/api/v1/projects/{project.id}/websites")
    assert list_main.status_code == 200
    items = list_main.get_json()["items"]
    assert len(items) == 1
    assert items[0]["project_id"] == project.id


# Note on DELETE role distinction (ADMIN-only): the test_scheduled_tests
# suite already exercises the require_project_role wrapper end-to-end,
# and the same helper guards DELETE here. A direct ADMIN-vs-AUDITOR test
# for this resource would need group seeding fixtures that are out of
# scope for this PR; the auth matrix above covers anonymous (401) and
# unaffiliated authenticated users (403), which is the failure mode that
# matters for the surface boundary.
