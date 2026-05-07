"""Tests for the users / members / test-users REST endpoints.

Covers:

- /api/v1/users/me + /api/v1/users/search
- /api/v1/projects/<id>/members + /api/v1/projects/<id>/members/<user_id>
- /api/v1/projects/<id>/test-users + /api/v1/project-test-users/<id>
- /api/v1/websites/<id>/test-users + /api/v1/website-test-users/<id>

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


def _make_user(
    database: Database, *, email: str, role: UserRole = UserRole.AUDITOR
) -> AppUser:
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
        url=f"https://example-{uuid.uuid4().hex[:6]}.test/",
        name="Test website",
    )
    website_id = database.create_website(website)
    saved = database.get_website(website_id)
    assert saved is not None
    return saved


def _make_group(database: Database, *, name: str) -> PermissionGroup:
    group = PermissionGroup(name=name)
    new_id = database.create_group(group)
    saved = database.get_group(new_id)
    assert saved is not None
    return saved


@pytest.fixture
def superadmin(database: Database) -> AppUser:
    user = _make_user(database, email="root@example.test")
    user.is_superadmin = True
    database.update_app_user(user)
    refreshed = database.get_app_user(user.id) if user.id else None
    assert refreshed is not None
    return refreshed


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


# ---------------------------------------------------------------------------
# /users/me + /users/search
# ---------------------------------------------------------------------------


def test_users_me_anonymous_returns_401(client: FlaskClient) -> None:
    response = client.get("/api/v1/users/me")
    assert response.status_code == 401


def test_users_me_returns_basic_profile(
    client: FlaskClient, superadmin: AppUser, login: Any
) -> None:
    login(superadmin)
    response = client.get("/api/v1/users/me")
    assert response.status_code == 200
    body = response.get_json()
    assert body["user_id"] == str(superadmin.id)
    assert body["email"] == superadmin.email
    assert body["is_superadmin"] is True


def test_users_search_short_query_returns_empty(
    client: FlaskClient, superadmin: AppUser, login: Any
) -> None:
    login(superadmin)
    response = client.get("/api/v1/users/search?q=a")
    assert response.status_code == 200
    assert response.get_json() == {"users": []}


def test_users_search_finds_by_email(
    client: FlaskClient,
    database: Database,
    superadmin: AppUser,
    login: Any,
) -> None:
    _make_user(database, email="searchable.alice@example.test")
    _make_user(database, email="other.bob@example.test")
    login(superadmin)
    response = client.get("/api/v1/users/search?q=alice")
    assert response.status_code == 200
    body = response.get_json()
    emails = {u["email"] for u in body["users"]}
    assert "searchable.alice@example.test" in emails
    assert "other.bob@example.test" not in emails


def test_users_search_excludes_existing_project_members(
    client: FlaskClient,
    database: Database,
    superadmin: AppUser,
    project: Project,
    login: Any,
) -> None:
    alice = _make_user(database, email="alice.search@example.test")
    bob = _make_user(database, email="bob.search@example.test")
    group = _make_group(database, name=f"perms-{uuid.uuid4().hex[:6]}")
    assert project.id is not None and alice.id is not None and group.id is not None
    database.add_project_member(project.id, alice.id, [group.id])
    login(superadmin)
    response = client.get(
        f"/api/v1/users/search?q=search&exclude_project={project.id}"
    )
    assert response.status_code == 200
    user_ids = {u["user_id"] for u in response.get_json()["users"]}
    assert alice.id not in user_ids
    assert bob.id in user_ids


# ---------------------------------------------------------------------------
# Project members CRUD
# ---------------------------------------------------------------------------


def test_anonymous_list_members_returns_401(
    client: FlaskClient, project: Project
) -> None:
    response = client.get(f"/api/v1/projects/{project.id}/members")
    assert response.status_code == 401


def test_unknown_project_returns_404(
    client: FlaskClient, superadmin: AppUser, login: Any
) -> None:
    login(superadmin)
    response = client.get(
        "/api/v1/projects/507f1f77bcf86cd799999999/members"
    )
    assert response.status_code == 404


def test_add_member_round_trip(
    client: FlaskClient,
    database: Database,
    superadmin: AppUser,
    project: Project,
    login: Any,
) -> None:
    target = _make_user(database, email="member.target@example.test")
    group = _make_group(database, name=f"editors-{uuid.uuid4().hex[:6]}")
    assert target.id is not None and group.id is not None

    login(superadmin)
    create = client.post(
        f"/api/v1/projects/{project.id}/members",
        json={"user_id": target.id, "group_ids": [group.id]},
    )
    assert create.status_code == 201, create.get_json()
    body = create.get_json()
    assert body["user_id"] == target.id
    assert body["email"] == target.email
    assert group.id in body["group_ids"]
    assert create.headers["Location"] == f"/api/v1/projects/{project.id}/members/{target.id}"

    # GET single
    fetched = client.get(f"/api/v1/projects/{project.id}/members/{target.id}")
    assert fetched.status_code == 200
    assert fetched.get_json()["user_id"] == target.id

    # Listing includes them and exposes available_groups.
    listed = client.get(f"/api/v1/projects/{project.id}/members")
    listed_body = listed.get_json()
    assert any(m["user_id"] == target.id for m in listed_body["members"])
    available_group_ids = {g["id"] for g in listed_body["available_groups"]}
    assert group.id in available_group_ids


def test_add_member_with_unknown_user_returns_404(
    client: FlaskClient,
    database: Database,
    superadmin: AppUser,
    project: Project,
    login: Any,
) -> None:
    group = _make_group(database, name=f"g-{uuid.uuid4().hex[:6]}")
    assert group.id is not None
    login(superadmin)
    response = client.post(
        f"/api/v1/projects/{project.id}/members",
        json={"user_id": "507f1f77bcf86cd799999999", "group_ids": [group.id]},
    )
    assert response.status_code == 404


def test_add_member_without_group_ids_returns_400(
    client: FlaskClient,
    database: Database,
    superadmin: AppUser,
    project: Project,
    login: Any,
) -> None:
    target = _make_user(database, email="bare.add@example.test")
    assert target.id is not None
    login(superadmin)
    response = client.post(
        f"/api/v1/projects/{project.id}/members",
        json={"user_id": target.id, "group_ids": []},
    )
    assert response.status_code == 400


def test_add_duplicate_member_returns_409(
    client: FlaskClient,
    database: Database,
    superadmin: AppUser,
    project: Project,
    login: Any,
) -> None:
    target = _make_user(database, email="dupe.member@example.test")
    group = _make_group(database, name=f"g-{uuid.uuid4().hex[:6]}")
    assert target.id is not None and group.id is not None and project.id is not None
    database.add_project_member(project.id, target.id, [group.id])
    login(superadmin)
    response = client.post(
        f"/api/v1/projects/{project.id}/members",
        json={"user_id": target.id, "group_ids": [group.id]},
    )
    assert response.status_code == 409


def test_put_replaces_member_groups(
    client: FlaskClient,
    database: Database,
    superadmin: AppUser,
    project: Project,
    login: Any,
) -> None:
    target = _make_user(database, email="put.member@example.test")
    g1 = _make_group(database, name=f"g1-{uuid.uuid4().hex[:6]}")
    g2 = _make_group(database, name=f"g2-{uuid.uuid4().hex[:6]}")
    assert target.id is not None and g1.id is not None and g2.id is not None and project.id is not None
    database.add_project_member(project.id, target.id, [g1.id])

    login(superadmin)
    response = client.put(
        f"/api/v1/projects/{project.id}/members/{target.id}",
        json={"group_ids": [g2.id]},
    )
    assert response.status_code == 200
    assert response.get_json()["group_ids"] == [g2.id]


def test_delete_self_returns_400(
    client: FlaskClient,
    superadmin: AppUser,
    database: Database,
    project: Project,
    login: Any,
) -> None:
    """Removing yourself from a project is rejected — same rule as the
    legacy form, mirrored here as a 400 with a structured field error."""
    group = _make_group(database, name=f"g-{uuid.uuid4().hex[:6]}")
    assert superadmin.id is not None and group.id is not None and project.id is not None
    database.add_project_member(project.id, superadmin.id, [group.id])
    login(superadmin)
    response = client.delete(
        f"/api/v1/projects/{project.id}/members/{superadmin.id}"
    )
    assert response.status_code == 400


def test_delete_other_returns_204(
    client: FlaskClient,
    superadmin: AppUser,
    database: Database,
    project: Project,
    login: Any,
) -> None:
    target = _make_user(database, email="del.other@example.test")
    group = _make_group(database, name=f"g-{uuid.uuid4().hex[:6]}")
    assert target.id is not None and group.id is not None and project.id is not None
    database.add_project_member(project.id, target.id, [group.id])

    login(superadmin)
    response = client.delete(
        f"/api/v1/projects/{project.id}/members/{target.id}"
    )
    assert response.status_code == 204
    refreshed = database.get_project(project.id)
    assert refreshed is not None
    assert all(m.user_id != target.id for m in refreshed.members)


# ---------------------------------------------------------------------------
# Project test users (login automation creds)
# ---------------------------------------------------------------------------


def _valid_test_user_body() -> dict[str, Any]:
    return {
        "username": f"alice-{uuid.uuid4().hex[:6]}",
        "password": "s3cret-password",
        "display_name": "Alice (admin)",
        "roles": ["admin"],
        "description": "QA login account",
        "login_config": {
            "authentication_method": "form_login",
            "login_url": "https://example.test/login",
            "username_field_selector": "#user",
            "password_field_selector": "#pass",
            "submit_button_selector": "button[type=submit]",
            "additional_steps": [],
            "session_timeout_minutes": 60,
        },
    }


def test_create_project_test_user_does_not_echo_password(
    client: FlaskClient,
    superadmin: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(superadmin)
    response = client.post(
        f"/api/v1/projects/{project.id}/test-users",
        json=_valid_test_user_body(),
    )
    assert response.status_code == 201
    body = response.get_json()
    assert "password" not in body
    assert body["password_set"] is True
    assert body["project_id"] == project.id
    assert body["login_config"]["authentication_method"] == "form_login"
    assert response.headers["Location"].startswith("/api/v1/project-test-users/")


def test_create_with_duplicate_username_returns_409(
    client: FlaskClient,
    superadmin: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(superadmin)
    body = _valid_test_user_body()
    first = client.post(f"/api/v1/projects/{project.id}/test-users", json=body)
    assert first.status_code == 201
    second = client.post(f"/api/v1/projects/{project.id}/test-users", json=body)
    assert second.status_code == 409


def test_patch_blank_password_preserves_existing(
    client: FlaskClient,
    database: Database,
    superadmin: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(superadmin)
    create = client.post(
        f"/api/v1/projects/{project.id}/test-users",
        json=_valid_test_user_body(),
    )
    user_id = create.get_json()["id"]

    response = client.patch(
        f"/api/v1/project-test-users/{user_id}",
        json={"display_name": "renamed", "password": ""},
    )
    assert response.status_code == 200
    assert response.get_json()["display_name"] == "renamed"
    # Verify the secret hasn't been blanked in the DB.
    stored = database.get_project_user(user_id)
    assert stored is not None
    assert stored.password == "s3cret-password"


def test_create_with_unknown_authentication_method_returns_400(
    client: FlaskClient,
    superadmin: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(superadmin)
    body = _valid_test_user_body()
    body["login_config"]["authentication_method"] = "telepathy"
    response = client.post(
        f"/api/v1/projects/{project.id}/test-users", json=body
    )
    assert response.status_code == 400
    field_errors = {e["field"] for e in response.get_json()["errors"]}
    assert "login_config.authentication_method" in field_errors


def test_delete_project_test_user_returns_204(
    client: FlaskClient,
    database: Database,
    superadmin: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(superadmin)
    create = client.post(
        f"/api/v1/projects/{project.id}/test-users",
        json=_valid_test_user_body(),
    )
    user_id = create.get_json()["id"]
    response = client.delete(f"/api/v1/project-test-users/{user_id}")
    assert response.status_code == 204
    assert database.get_project_user(user_id) is None


# ---------------------------------------------------------------------------
# Website test users
# ---------------------------------------------------------------------------


def test_create_website_test_user_round_trip(
    client: FlaskClient,
    database: Database,
    superadmin: AppUser,
    website: Website,
    login: Any,
) -> None:
    login(superadmin)
    create = client.post(
        f"/api/v1/websites/{website.id}/test-users",
        json=_valid_test_user_body(),
    )
    assert create.status_code == 201
    body = create.get_json()
    assert body["website_id"] == website.id
    assert "password" not in body
    user_id = body["id"]

    fetched = client.get(f"/api/v1/website-test-users/{user_id}")
    assert fetched.status_code == 200
    assert fetched.get_json()["id"] == user_id

    # PATCH toggle off via {enabled: false}.
    toggled = client.patch(
        f"/api/v1/website-test-users/{user_id}", json={"enabled": False}
    )
    assert toggled.status_code == 200
    assert toggled.get_json()["enabled"] is False

    # DELETE.
    deleted = client.delete(f"/api/v1/website-test-users/{user_id}")
    assert deleted.status_code == 204
    assert database.get_website_user(user_id) is None


def test_unknown_website_test_user_returns_404(
    client: FlaskClient, superadmin: AppUser, login: Any
) -> None:
    login(superadmin)
    response = client.get("/api/v1/website-test-users/507f1f77bcf86cd799999999")
    assert response.status_code == 404


def test_test_user_listings_isolate_by_parent(
    client: FlaskClient,
    database: Database,
    superadmin: AppUser,
    project: Project,
    website: Website,
    login: Any,
) -> None:
    """Project and website test users live in separate collections;
    each list endpoint should only show its own."""
    login(superadmin)
    client.post(
        f"/api/v1/projects/{project.id}/test-users",
        json=_valid_test_user_body() | {"username": f"proj-{uuid.uuid4().hex[:6]}"},
    )
    client.post(
        f"/api/v1/websites/{website.id}/test-users",
        json=_valid_test_user_body() | {"username": f"site-{uuid.uuid4().hex[:6]}"},
    )

    project_listing = client.get(f"/api/v1/projects/{project.id}/test-users").get_json()
    website_listing = client.get(f"/api/v1/websites/{website.id}/test-users").get_json()
    assert all(u["project_id"] == project.id for u in project_listing["items"])
    assert all(u["website_id"] == website.id for u in website_listing["items"])
