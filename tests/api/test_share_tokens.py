"""Tests for the share-tokens REST endpoints.

Endpoints exercised:

- POST /api/v1/projects/<project_id>/share-tokens
- POST /api/v1/websites/<website_id>/share-tokens
- GET  /api/v1/projects/<project_id>/share-tokens
- GET  /api/v1/websites/<website_id>/share-tokens
- GET  /api/v1/share-tokens/<token_id>
- DELETE /api/v1/share-tokens/<token_id>

Coverage:

- Anonymous → 401, authenticated non-member → 403, unknown scope → 404.
- POST returns the raw token + public_url **once** at creation; GET/list
  responses never include the raw token or its hash.
- DELETE is idempotent (repeated calls return 204).
- Project-scoped tokens isolate from website-scoped tokens.

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
    # The share-token serializer reads SECRET_KEY off the typed app
    # config object; mirror what create_app does.
    from config import Config
    cfg = Config()
    cfg.SECRET_KEY = "test-secret-deterministic-for-tests-only"
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
        url=f"https://example-{uuid.uuid4().hex[:6]}.test/",
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


def _valid_create_body() -> dict[str, Any]:
    return {"label": "Quarterly review share"}


# ---------------------------------------------------------------------------
# Auth matrix
# ---------------------------------------------------------------------------


def test_anonymous_create_returns_401(
    client: FlaskClient, project: Project
) -> None:
    response = client.post(
        f"/api/v1/projects/{project.id}/share-tokens", json=_valid_create_body()
    )
    assert response.status_code == 401
    assert response.headers["Content-Type"] == "application/problem+json"


def test_anonymous_list_returns_401(
    client: FlaskClient, project: Project
) -> None:
    response = client.get(f"/api/v1/projects/{project.id}/share-tokens")
    assert response.status_code == 401


def test_authenticated_non_member_returns_403(
    client: FlaskClient,
    database: Database,
    project: Project,
    login: Any,
) -> None:
    user = _make_user(database, role=UserRole.AUDITOR, email="outsider@example.test")
    login(user)
    response = client.post(
        f"/api/v1/projects/{project.id}/share-tokens", json=_valid_create_body()
    )
    assert response.status_code == 403


def test_unknown_project_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/projects/507f1f77bcf86cd799999999/share-tokens",
        json=_valid_create_body(),
    )
    assert response.status_code == 404


def test_unknown_website_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/websites/507f1f77bcf86cd799999999/share-tokens",
        json=_valid_create_body(),
    )
    assert response.status_code == 404


def test_unknown_token_get_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    login(admin_user)
    response = client.get("/api/v1/share-tokens/507f1f77bcf86cd799999999")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Create + retrieve
# ---------------------------------------------------------------------------


def test_create_project_token_returns_raw_token_and_public_url(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/projects/{project.id}/share-tokens",
        json={"label": "Q4 audit"},
    )
    assert response.status_code == 201
    body = response.get_json()
    assert body["scope"] == "project"
    assert body["scope_id"] == project.id
    assert body["label"] == "Q4 audit"
    # Raw token + public_url present on creation only.
    assert isinstance(body["token"], str) and len(body["token"]) > 16
    assert body["public_url"].endswith(f"/t/{body['token']}/")
    assert response.headers["Location"] == f"/api/v1/share-tokens/{body['id']}"


def test_create_website_token_succeeds(
    client: FlaskClient,
    admin_user: AppUser,
    website: Website,
    login: Any,
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/websites/{website.id}/share-tokens",
        json={"label": "Vendor handoff"},
    )
    assert response.status_code == 201
    body = response.get_json()
    assert body["scope"] == "website"
    assert body["scope_id"] == website.id


def test_get_token_returns_metadata_only(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    create = client.post(
        f"/api/v1/projects/{project.id}/share-tokens",
        json=_valid_create_body(),
    )
    token_id = create.get_json()["id"]

    get = client.get(f"/api/v1/share-tokens/{token_id}")
    assert get.status_code == 200
    body = get.get_json()
    # No raw token or hash leak.
    assert "token" not in body
    assert "token_hash" not in body
    assert body["id"] == token_id
    assert body["is_valid"] is True


# ---------------------------------------------------------------------------
# List + pagination
# ---------------------------------------------------------------------------


def test_list_returns_only_metadata(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    client.post(
        f"/api/v1/projects/{project.id}/share-tokens",
        json={"label": "First"},
    )

    listed = client.get(f"/api/v1/projects/{project.id}/share-tokens")
    assert listed.status_code == 200
    body = listed.get_json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert "token" not in item
    assert "token_hash" not in item
    assert item["label"] == "First"


def test_list_paginates_with_cursor(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    for n in range(3):
        client.post(
            f"/api/v1/projects/{project.id}/share-tokens",
            json={"label": f"T{n}"},
        )

    page1 = client.get(f"/api/v1/projects/{project.id}/share-tokens?limit=2")
    assert page1.status_code == 200
    page1_body = page1.get_json()
    assert len(page1_body["items"]) == 2
    assert page1_body["next_cursor"] is not None

    cursor = page1_body["next_cursor"]
    page2 = client.get(
        f"/api/v1/projects/{project.id}/share-tokens?limit=2&cursor={cursor}"
    )
    assert page2.status_code == 200
    page2_body = page2.get_json()
    assert len(page2_body["items"]) == 1
    assert page2_body["next_cursor"] is None


def test_list_isolates_project_and_website_scopes(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    website: Website,
    login: Any,
) -> None:
    """Tokens scoped to the project don't appear in the website's list."""
    login(admin_user)
    client.post(
        f"/api/v1/projects/{project.id}/share-tokens",
        json={"label": "Project-scope"},
    )
    client.post(
        f"/api/v1/websites/{website.id}/share-tokens",
        json={"label": "Website-scope"},
    )

    project_listed = client.get(
        f"/api/v1/projects/{project.id}/share-tokens"
    )
    website_listed = client.get(
        f"/api/v1/websites/{website.id}/share-tokens"
    )
    assert {t["label"] for t in project_listed.get_json()["items"]} == {"Project-scope"}
    assert {t["label"] for t in website_listed.get_json()["items"]} == {"Website-scope"}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_create_with_missing_label_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/projects/{project.id}/share-tokens", json={}
    )
    assert response.status_code == 400
    body = response.get_json()
    field_errors = {e["field"] for e in body["errors"]}
    assert "label" in field_errors


def test_create_with_blank_label_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/projects/{project.id}/share-tokens",
        json={"label": "   "},
    )
    assert response.status_code == 400


def test_create_with_invalid_expires_at_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/projects/{project.id}/share-tokens",
        json={"label": "X", "expires_at": "not-a-datetime"},
    )
    assert response.status_code == 400


def test_create_with_non_object_body_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/projects/{project.id}/share-tokens", json=["nope"]
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Revoke (DELETE)
# ---------------------------------------------------------------------------


def test_delete_returns_204_and_marks_token_revoked(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    create = client.post(
        f"/api/v1/projects/{project.id}/share-tokens",
        json=_valid_create_body(),
    )
    token_id = create.get_json()["id"]

    delete = client.delete(f"/api/v1/share-tokens/{token_id}")
    assert delete.status_code == 204
    assert delete.data == b""

    # Token still exists, but is_valid is now False.
    get = client.get(f"/api/v1/share-tokens/{token_id}")
    assert get.status_code == 200
    body = get.get_json()
    assert body["revoked"] is True
    assert body["is_valid"] is False
    assert body["revoked_at"] is not None


def test_delete_is_idempotent(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    create = client.post(
        f"/api/v1/projects/{project.id}/share-tokens",
        json=_valid_create_body(),
    )
    token_id = create.get_json()["id"]

    first = client.delete(f"/api/v1/share-tokens/{token_id}")
    second = client.delete(f"/api/v1/share-tokens/{token_id}")
    assert first.status_code == 204
    assert second.status_code == 204
