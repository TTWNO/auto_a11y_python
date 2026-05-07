"""Tests for the /api/v1/projects/<id>/discovered-pages and
/api/v1/discovered-pages/<id> REST endpoints.

Covers:

- Auth matrix: 401 anonymous, 403 non-member, 404 unknown
- CRUD round-trip: POST → GET → PATCH → PUT → DELETE → 404
- PUT preserves server-managed fields (project_id, source_*, drupal_*,
  created_*) — clients cannot reassign or rewrite them
- Validation: missing title, missing url, non-array list fields, malformed
  document_links entries, non-object body
- Cursor pagination
- Project scope isolation between two projects

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
from auto_a11y.models.project import Project, ProjectStatus


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
    user = _make_user(database, email="admin@example.test")
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
    def _login(user: AppUser) -> None:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    yield _login


def _valid_create_body() -> dict[str, Any]:
    return {
        "title": "Checkout flow",
        "url": "https://example.test/checkout",
        "interested_because": ["complex form", "third-party widget"],
        "page_elements": ["main"],
        "private_notes": "<p>cart total looks unstyled at 200% zoom</p>",
        "public_notes": "<p>Checkout flow under review.</p>",
        "include_in_report": True,
    }


# ---------------------------------------------------------------------------
# Auth + 404
# ---------------------------------------------------------------------------


def test_anonymous_list_returns_401(
    client: FlaskClient, project: Project
) -> None:
    response = client.get(f"/api/v1/projects/{project.id}/discovered-pages")
    assert response.status_code == 401
    assert response.headers["Content-Type"] == "application/problem+json"


def test_authenticated_non_member_returns_403(
    client: FlaskClient,
    database: Database,
    project: Project,
    login: Any,
) -> None:
    user = _make_user(database, email="outsider@example.test")
    login(user)
    response = client.get(f"/api/v1/projects/{project.id}/discovered-pages")
    assert response.status_code == 403


def test_unknown_project_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    login(admin_user)
    response = client.get(
        "/api/v1/projects/507f1f77bcf86cd799999999/discovered-pages"
    )
    assert response.status_code == 404


def test_unknown_discovered_page_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    login(admin_user)
    response = client.get("/api/v1/discovered-pages/507f1f77bcf86cd799999999")
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
        f"/api/v1/projects/{project.id}/discovered-pages",
        json=_valid_create_body(),
    )
    assert create.status_code == 201, create.get_json()
    body = create.get_json()
    page_id = body["id"]
    assert body["project_id"] == project.id
    assert body["title"] == "Checkout flow"
    assert body["interested_because"] == ["complex form", "third-party widget"]
    assert body["audited"] is False
    assert create.headers["Location"] == f"/api/v1/discovered-pages/{page_id}"

    fetched = client.get(f"/api/v1/discovered-pages/{page_id}")
    assert fetched.status_code == 200
    assert fetched.get_json()["id"] == page_id


def test_create_with_duplicate_url_returns_existing_page(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    """create_discovered_page upserts by (project_id, url). A second POST
    with the same URL returns the same id rather than creating a duplicate."""
    login(admin_user)
    body = _valid_create_body()
    first = client.post(
        f"/api/v1/projects/{project.id}/discovered-pages", json=body
    )
    second = client.post(
        f"/api/v1/projects/{project.id}/discovered-pages", json=body,
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.get_json()["id"] == second.get_json()["id"]


def test_patch_updates_only_provided_fields(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    create = client.post(
        f"/api/v1/projects/{project.id}/discovered-pages",
        json=_valid_create_body(),
    )
    page_id = create.get_json()["id"]

    response = client.patch(
        f"/api/v1/discovered-pages/{page_id}",
        json={"audited": True, "manual_audit": True},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["audited"] is True
    assert body["manual_audit"] is True
    assert body["title"] == "Checkout flow"  # untouched


def test_put_preserves_server_managed_fields(
    client: FlaskClient,
    admin_user: AppUser,
    database: Database,
    project: Project,
    login: Any,
) -> None:
    """Even if the PUT body tries to set project_id/source_type/drupal_*,
    those fields should be preserved from the existing record."""
    login(admin_user)
    create = client.post(
        f"/api/v1/projects/{project.id}/discovered-pages",
        json=_valid_create_body(),
    )
    page_id = create.get_json()["id"]
    other_project = _make_project(database)

    new_body = _valid_create_body() | {
        "title": "Replaced",
        "project_id": other_project.id,  # ignored — server preserves
        "source_type": "automated_test",  # ignored
        "drupal_uuid": "manipulated",  # ignored
    }
    response = client.put(
        f"/api/v1/discovered-pages/{page_id}", json=new_body
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["title"] == "Replaced"
    assert body["project_id"] == project.id
    assert body["source_type"] == "manual"
    assert body["drupal_uuid"] is None


def test_delete_returns_204_then_get_returns_404(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    create = client.post(
        f"/api/v1/projects/{project.id}/discovered-pages",
        json=_valid_create_body(),
    )
    page_id = create.get_json()["id"]

    delete = client.delete(f"/api/v1/discovered-pages/{page_id}")
    assert delete.status_code == 204
    assert delete.data == b""

    get = client.get(f"/api/v1/discovered-pages/{page_id}")
    assert get.status_code == 404


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_create_with_missing_title_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    body = _valid_create_body()
    body.pop("title")
    response = client.post(
        f"/api/v1/projects/{project.id}/discovered-pages", json=body
    )
    assert response.status_code == 400
    field_errors = {e["field"] for e in response.get_json()["errors"]}
    assert "title" in field_errors


def test_create_with_missing_url_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    body = _valid_create_body()
    body.pop("url")
    response = client.post(
        f"/api/v1/projects/{project.id}/discovered-pages", json=body
    )
    assert response.status_code == 400
    field_errors = {e["field"] for e in response.get_json()["errors"]}
    assert "url" in field_errors


def test_create_with_string_taxonomy_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    """interested_because must be an array, not a comma-separated string."""
    login(admin_user)
    body = _valid_create_body() | {"interested_because": "form, widget"}
    response = client.post(
        f"/api/v1/projects/{project.id}/discovered-pages", json=body
    )
    assert response.status_code == 400


def test_create_with_malformed_document_links_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    """document_links is a list of objects; passing a list of strings 400s."""
    login(admin_user)
    body = _valid_create_body() | {"document_links": ["not", "objects"]}
    response = client.post(
        f"/api/v1/projects/{project.id}/discovered-pages", json=body
    )
    assert response.status_code == 400
    field_errors = {e["field"] for e in response.get_json()["errors"]}
    assert "document_links[0]" in field_errors


def test_create_with_non_object_body_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/projects/{project.id}/discovered-pages",
        json=["not", "a", "dict"],
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Pagination + project isolation
# ---------------------------------------------------------------------------


def test_list_paginates_with_cursor(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    for n in range(3):
        body = _valid_create_body() | {
            "title": f"Page {n}",
            # The DB layer upserts by (project_id, url), so distinct URLs are
            # required to land three rows.
            "url": f"https://example.test/checkout-{n}",
        }
        client.post(
            f"/api/v1/projects/{project.id}/discovered-pages", json=body
        )

    page1 = client.get(
        f"/api/v1/projects/{project.id}/discovered-pages?limit=2"
    )
    assert page1.status_code == 200
    page1_body = page1.get_json()
    assert len(page1_body["items"]) == 2
    assert page1_body["next_cursor"] is not None

    cursor = page1_body["next_cursor"]
    page2 = client.get(
        f"/api/v1/projects/{project.id}/discovered-pages?limit=2&cursor={cursor}"
    )
    assert page2.status_code == 200
    page2_body = page2.get_json()
    assert len(page2_body["items"]) == 1
    assert page2_body["next_cursor"] is None


def test_list_isolates_by_project(
    client: FlaskClient,
    admin_user: AppUser,
    database: Database,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    other = _make_project(database)
    client.post(
        f"/api/v1/projects/{project.id}/discovered-pages",
        json=_valid_create_body() | {"title": "Mine"},
    )
    client.post(
        f"/api/v1/projects/{other.id}/discovered-pages",
        json=_valid_create_body() | {"title": "Theirs"},
    )

    listed = client.get(
        f"/api/v1/projects/{project.id}/discovered-pages"
    ).get_json()
    assert {p["title"] for p in listed["items"]} == {"Mine"}


def test_list_with_malformed_cursor_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    response = client.get(
        f"/api/v1/projects/{project.id}/discovered-pages?cursor=not-a-cursor!@#"
    )
    assert response.status_code == 400
