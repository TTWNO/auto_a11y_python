"""Tests for the /api/v1/groups REST endpoints (permission-group CRUD).

Covers:

- Auth matrix: 401 anonymous, 403 authenticated user with no project
  membership granting `groups` permission, 404 unknown group
- CRUD round-trip: POST → GET → PATCH → PUT → DELETE → 404
- name uniqueness on create + replace (409)
- permissions dict validation: unknown resource noun → 400, unknown
  level → 400, partial dict fills missing resources with "none"
- is_system protection: system groups can't be deleted (409)
- is_system is server-managed (not editable via PATCH/PUT)
- Cursor pagination

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
from auto_a11y.models.app_user import AppUser, UserRole
from auto_a11y.models.permission_group import PermissionGroup


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


def _make_user(database: Database, *, email: str) -> AppUser:
    user = AppUser.create(email=email, password="x", role=UserRole.AUDITOR)
    user_id = database.create_app_user(user)
    saved = database.get_app_user(user_id)
    assert saved is not None
    return saved


@pytest.fixture
def superadmin(database: Database) -> AppUser:
    """Use a superadmin throughout — bypasses the per-resource permission
    sweep so tests focus on endpoint behaviour rather than permission
    fixtures."""
    user = _make_user(database, email="root@example.test")
    user.is_superadmin = True
    database.update_app_user(user)
    refreshed = database.get_app_user(user.id) if user.id else None
    assert refreshed is not None
    return refreshed


@pytest.fixture
def regular_user(database: Database) -> AppUser:
    """An authenticated user with no project memberships — should get 403
    on every group endpoint because user_has_global_permission sweeps
    project memberships and finds nothing."""
    return _make_user(database, email="nobody@example.test")


@pytest.fixture
def login(client: FlaskClient) -> Iterator[Any]:
    def _login(user: AppUser) -> None:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    yield _login


def _valid_create_body() -> dict[str, Any]:
    return {
        "name": f"Custom group {uuid.uuid4().hex[:6]}",
        "description": "test fixture",
        "permissions": {
            "projects": "read",
            "websites": "update",
        },
    }


# ---------------------------------------------------------------------------
# Auth matrix
# ---------------------------------------------------------------------------


def test_anonymous_list_returns_401(client: FlaskClient) -> None:
    response = client.get("/api/v1/groups")
    assert response.status_code == 401
    assert response.headers["Content-Type"] == "application/problem+json"


def test_authenticated_non_member_returns_403(
    client: FlaskClient, regular_user: AppUser, login: Any
) -> None:
    """A logged-in user with no project membership has no `groups`
    permission on any project, so the global-permission check fails."""
    login(regular_user)
    response = client.get("/api/v1/groups")
    assert response.status_code == 403


def test_unknown_group_returns_404(
    client: FlaskClient, superadmin: AppUser, login: Any
) -> None:
    login(superadmin)
    response = client.get("/api/v1/groups/507f1f77bcf86cd799999999")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# CRUD round-trip
# ---------------------------------------------------------------------------


def test_create_then_get_returns_resource(
    client: FlaskClient, superadmin: AppUser, login: Any
) -> None:
    login(superadmin)
    body = _valid_create_body()
    create = client.post("/api/v1/groups", json=body)
    assert create.status_code == 201, create.get_json()
    assert create.headers["Location"].startswith("/api/v1/groups/")
    payload = create.get_json()
    assert payload["name"] == body["name"]
    assert payload["is_system"] is False
    # Permissions present in body are recorded; resources omitted from
    # the body default to "none".
    assert payload["permissions"]["projects"] == "read"
    assert payload["permissions"]["websites"] == "update"
    assert payload["permissions"]["users"] == "none"

    group_id = payload["id"]
    get_response = client.get(f"/api/v1/groups/{group_id}")
    assert get_response.status_code == 200
    assert get_response.get_json()["id"] == group_id


def test_patch_updates_only_provided_fields(
    client: FlaskClient, superadmin: AppUser, login: Any
) -> None:
    login(superadmin)
    create = client.post("/api/v1/groups", json=_valid_create_body())
    group_id = create.get_json()["id"]
    original_name = create.get_json()["name"]

    response = client.patch(
        f"/api/v1/groups/{group_id}", json={"description": "updated description"}
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["description"] == "updated description"
    assert body["name"] == original_name


def test_put_replaces_full_resource_preserving_is_system(
    client: FlaskClient,
    superadmin: AppUser,
    database: Database,
    login: Any,
) -> None:
    """PUT should not let a client flip is_system on or off."""
    # Manually create a system group via the model to verify PUT preserves the flag.
    sys_group = PermissionGroup(name=f"sys-{uuid.uuid4().hex[:6]}", is_system=True)
    sys_group_id = database.create_group(sys_group)

    login(superadmin)
    response = client.put(
        f"/api/v1/groups/{sys_group_id}",
        json={
            "name": f"renamed-{uuid.uuid4().hex[:6]}",
            "description": "renamed",
            "permissions": {"projects": "read"},
            "is_system": False,  # should be ignored — server preserves the flag
        },
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["is_system"] is True


def test_delete_returns_204_then_get_returns_404(
    client: FlaskClient, superadmin: AppUser, login: Any
) -> None:
    login(superadmin)
    create = client.post("/api/v1/groups", json=_valid_create_body())
    group_id = create.get_json()["id"]

    delete = client.delete(f"/api/v1/groups/{group_id}")
    assert delete.status_code == 204
    assert delete.data == b""

    get = client.get(f"/api/v1/groups/{group_id}")
    assert get.status_code == 404


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_create_with_missing_name_returns_400(
    client: FlaskClient, superadmin: AppUser, login: Any
) -> None:
    login(superadmin)
    response = client.post(
        "/api/v1/groups", json={"description": "no name"}
    )
    assert response.status_code == 400
    field_errors = {e["field"] for e in response.get_json()["errors"]}
    assert "name" in field_errors


def test_create_with_unknown_resource_in_permissions_returns_400(
    client: FlaskClient, superadmin: AppUser, login: Any
) -> None:
    login(superadmin)
    response = client.post(
        "/api/v1/groups",
        json={
            "name": f"bad-{uuid.uuid4().hex[:6]}",
            "permissions": {"unicorns": "read"},
        },
    )
    assert response.status_code == 400
    field_errors = {e["field"] for e in response.get_json()["errors"]}
    assert "permissions.unicorns" in field_errors


def test_create_with_unknown_level_in_permissions_returns_400(
    client: FlaskClient, superadmin: AppUser, login: Any
) -> None:
    login(superadmin)
    response = client.post(
        "/api/v1/groups",
        json={
            "name": f"bad-{uuid.uuid4().hex[:6]}",
            "permissions": {"projects": "godmode"},
        },
    )
    assert response.status_code == 400
    field_errors = {e["field"] for e in response.get_json()["errors"]}
    assert "permissions.projects" in field_errors


def test_create_with_duplicate_name_returns_409(
    client: FlaskClient, superadmin: AppUser, login: Any
) -> None:
    login(superadmin)
    body = _valid_create_body()
    first = client.post("/api/v1/groups", json=body)
    assert first.status_code == 201
    second = client.post("/api/v1/groups", json=body)
    assert second.status_code == 409
    assert second.get_json()["type"].endswith("/conflict")


def test_create_with_non_object_body_returns_400(
    client: FlaskClient, superadmin: AppUser, login: Any
) -> None:
    login(superadmin)
    response = client.post("/api/v1/groups", json=["not", "a", "dict"])
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# is_system protection
# ---------------------------------------------------------------------------


def test_delete_system_group_returns_409(
    client: FlaskClient,
    superadmin: AppUser,
    database: Database,
    login: Any,
) -> None:
    sys_group = PermissionGroup(name=f"sys-{uuid.uuid4().hex[:6]}", is_system=True)
    sys_group_id = database.create_group(sys_group)

    login(superadmin)
    response = client.delete(f"/api/v1/groups/{sys_group_id}")
    assert response.status_code == 409
    assert response.get_json()["type"].endswith("/conflict")
    # Group still exists.
    assert database.get_group(sys_group_id) is not None


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


def test_list_paginates_with_cursor(
    client: FlaskClient, superadmin: AppUser, login: Any
) -> None:
    login(superadmin)
    for n in range(3):
        body = _valid_create_body() | {"name": f"page-{n}-{uuid.uuid4().hex[:4]}"}
        client.post("/api/v1/groups", json=body)

    page1 = client.get("/api/v1/groups?limit=2")
    assert page1.status_code == 200
    page1_body = page1.get_json()
    assert len(page1_body["items"]) == 2
    assert page1_body["next_cursor"] is not None

    cursor = page1_body["next_cursor"]
    page2 = client.get(f"/api/v1/groups?limit=2&cursor={cursor}")
    assert page2.status_code == 200
    page2_body = page2.get_json()
    # We seeded 3 groups + 0 system groups in this fresh test database;
    # page 2 should have the remainder.
    assert len(page2_body["items"]) == 1
    assert page2_body["next_cursor"] is None


def test_list_with_malformed_cursor_returns_400(
    client: FlaskClient, superadmin: AppUser, login: Any
) -> None:
    login(superadmin)
    response = client.get("/api/v1/groups?cursor=not-a-cursor!@#")
    assert response.status_code == 400
