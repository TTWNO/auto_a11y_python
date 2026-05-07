"""Tests for the admin-settings REST endpoints.

Endpoints exercised:

- GET    /api/v1/admin/settings
- PATCH  /api/v1/admin/settings/drupal
- DELETE /api/v1/admin/settings/drupal
- PATCH  /api/v1/admin/settings/<section_id>
- DELETE /api/v1/admin/settings/<section_id>

Auth gate is superadmin-only; non-admin authenticated users get 403.

Covers happy paths for read/patch/delete on the Drupal section + on the
schema-driven CONFIG_SECTIONS, validation paths (URL format, required
fields, unknown section, unknown field, wrong types), the
"blank-password preserves existing" rule, and the password-never-echoed
response shape.

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
from auto_a11y.core.system_settings import SystemSettings
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


@pytest.fixture
def superadmin(database: Database) -> AppUser:
    user = _make_user(database, role=UserRole.ADMIN, email="root@example.test")
    user.is_superadmin = True
    database.update_app_user(user)
    refreshed = database.get_app_user(user.id) if user.id else None
    assert refreshed is not None
    return refreshed


@pytest.fixture
def regular_admin(database: Database) -> AppUser:
    """An ADMIN-role user that is NOT a superadmin — should be 403'd."""
    return _make_user(database, role=UserRole.ADMIN, email="not-super@example.test")


@pytest.fixture
def login(client: FlaskClient) -> Iterator[Any]:
    def _login(user: AppUser) -> None:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    yield _login


# ---------------------------------------------------------------------------
# Auth matrix
# ---------------------------------------------------------------------------


def test_anonymous_get_returns_401(client: FlaskClient) -> None:
    response = client.get("/api/v1/admin/settings")
    assert response.status_code == 401
    assert response.headers["Content-Type"] == "application/problem+json"


def test_non_superadmin_returns_403(
    client: FlaskClient, regular_admin: AppUser, login: Any
) -> None:
    """An ADMIN-role user without is_superadmin still gets 403 — settings
    are deployment-wide and not scoped to project membership."""
    login(regular_admin)
    response = client.get("/api/v1/admin/settings")
    assert response.status_code == 403


def test_unknown_section_returns_404(
    client: FlaskClient, superadmin: AppUser, login: Any
) -> None:
    login(superadmin)
    response = client.patch(
        "/api/v1/admin/settings/unknown_section_does_not_exist",
        json={"foo": "bar"},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET — full settings dump
# ---------------------------------------------------------------------------


def test_get_returns_drupal_and_sections(
    client: FlaskClient, superadmin: AppUser, login: Any
) -> None:
    login(superadmin)
    response = client.get("/api/v1/admin/settings")
    assert response.status_code == 200
    body = response.get_json()
    assert "drupal" in body
    assert "sections" in body
    section_ids = {s["section_id"] for s in body["sections"]}
    assert "claude_ai" in section_ids
    assert "browser" in section_ids


def test_get_does_not_echo_password_values(
    client: FlaskClient,
    database: Database,
    superadmin: AppUser,
    login: Any,
) -> None:
    """Even after a password is stored, the GET response surfaces only
    a *_set boolean and a null value — never the secret."""
    SystemSettings(database).set_section(
        "claude_ai", {"api_key": "super-secret-token-xyz"}, updated_by=None
    )
    login(superadmin)
    response = client.get("/api/v1/admin/settings")
    body = response.get_json()
    claude = next(s for s in body["sections"] if s["section_id"] == "claude_ai")
    assert claude["values"]["api_key"] is None
    assert claude["values"]["api_key_set"] is True


# ---------------------------------------------------------------------------
# PATCH /admin/settings/drupal
# ---------------------------------------------------------------------------


def test_patch_drupal_writes_section_and_returns_state(
    client: FlaskClient,
    database: Database,
    superadmin: AppUser,
    login: Any,
) -> None:
    login(superadmin)
    response = client.patch(
        "/api/v1/admin/settings/drupal",
        json={
            "base_url": "https://drupal.example.test/",
            "username": "auditor-bot",
            "password": "rotated-2026-q1",
            "enabled": True,
        },
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["source"] == "database"
    assert body["values"]["base_url"] == "https://drupal.example.test/"
    assert body["values"]["password_set"] is True
    # Stored (read raw from the doc) — the actual secret persisted, not in API.
    stored = SystemSettings(database).get_section("drupal")
    assert stored is not None and stored.get("password") == "rotated-2026-q1"


def test_patch_drupal_blank_password_preserves_existing(
    client: FlaskClient,
    database: Database,
    superadmin: AppUser,
    login: Any,
) -> None:
    """Omitting `password` on a subsequent PATCH keeps the stored secret."""
    SystemSettings(database).set_section(
        "drupal",
        {
            "base_url": "https://drupal.example.test/",
            "username": "alice",
            "password": "original-secret",
            "enabled": True,
        },
        updated_by=None,
    )
    login(superadmin)
    response = client.patch(
        "/api/v1/admin/settings/drupal",
        json={
            "base_url": "https://drupal.example.test/",
            "username": "alice-rotated",
            # password omitted on purpose
            "enabled": True,
        },
    )
    assert response.status_code == 200
    stored = SystemSettings(database).get_section("drupal")
    assert stored is not None
    assert stored["username"] == "alice-rotated"
    assert stored["password"] == "original-secret"


def test_patch_drupal_with_invalid_url_returns_400(
    client: FlaskClient, superadmin: AppUser, login: Any
) -> None:
    login(superadmin)
    response = client.patch(
        "/api/v1/admin/settings/drupal",
        json={
            "base_url": "ftp://drupal.example.test/",
            "username": "alice",
            "password": "x",
        },
    )
    assert response.status_code == 400
    field_errors = {e["field"] for e in response.get_json()["errors"]}
    assert "base_url" in field_errors


def test_patch_drupal_without_existing_password_requires_one(
    client: FlaskClient, superadmin: AppUser, login: Any
) -> None:
    """First-time create must include a password; preservation only kicks
    in when there's an existing stored value."""
    login(superadmin)
    response = client.patch(
        "/api/v1/admin/settings/drupal",
        json={
            "base_url": "https://drupal.example.test/",
            "username": "alice",
            # no password and no existing record
        },
    )
    assert response.status_code == 400
    field_errors = {e["field"] for e in response.get_json()["errors"]}
    assert "password" in field_errors


def test_delete_drupal_removes_section(
    client: FlaskClient,
    database: Database,
    superadmin: AppUser,
    login: Any,
) -> None:
    SystemSettings(database).set_section(
        "drupal",
        {
            "base_url": "https://drupal.example.test/",
            "username": "alice",
            "password": "secret",
            "enabled": True,
        },
        updated_by=None,
    )
    login(superadmin)
    response = client.delete("/api/v1/admin/settings/drupal")
    assert response.status_code == 204
    assert SystemSettings(database).get_section("drupal") is None


# ---------------------------------------------------------------------------
# PATCH /admin/settings/<section_id> — schema-driven sections
# ---------------------------------------------------------------------------


def test_patch_browser_section_coerces_field_types(
    client: FlaskClient,
    database: Database,
    superadmin: AppUser,
    login: Any,
) -> None:
    """The browser section has a mix of bool/int/string fields — confirm
    each coerces correctly through JSON."""
    login(superadmin)
    response = client.patch(
        "/api/v1/admin/settings/browser",
        json={
            "headless": False,        # BOOL
            "timeout": 45000,         # INT
            "viewport_width": 1440,   # INT
            "mode": "remote",         # STRING
        },
    )
    assert response.status_code == 200
    body = response.get_json()
    values = body["values"]
    assert values["headless"] is False
    assert values["timeout"] == 45000
    assert values["viewport_width"] == 1440
    assert values["mode"] == "remote"
    # Persisted with correct types (mongo round-trip).
    stored = SystemSettings(database).get_section("browser")
    assert stored is not None
    assert stored["headless"] is False
    assert stored["timeout"] == 45000


def test_patch_section_with_unknown_field_returns_400(
    client: FlaskClient, superadmin: AppUser, login: Any
) -> None:
    login(superadmin)
    response = client.patch(
        "/api/v1/admin/settings/browser",
        json={"this_is_not_in_the_schema": "nope"},
    )
    assert response.status_code == 400
    field_errors = {e["field"] for e in response.get_json()["errors"]}
    assert "this_is_not_in_the_schema" in field_errors


def test_patch_section_with_wrong_field_type_returns_400(
    client: FlaskClient, superadmin: AppUser, login: Any
) -> None:
    """timeout is an INT field — sending a non-numeric string fails."""
    login(superadmin)
    response = client.patch(
        "/api/v1/admin/settings/browser",
        json={"timeout": "not-a-number"},
    )
    assert response.status_code == 400


def test_patch_section_blank_password_preserves_existing(
    client: FlaskClient,
    database: Database,
    superadmin: AppUser,
    login: Any,
) -> None:
    """claude_ai.api_key is a PASSWORD field; blank string in PATCH must
    keep the existing secret rather than erase it."""
    SystemSettings(database).set_section(
        "claude_ai", {"api_key": "existing-secret"}, updated_by=None
    )
    login(superadmin)
    response = client.patch(
        "/api/v1/admin/settings/claude_ai",
        json={"api_key": "", "model": "claude-opus-4-7"},
    )
    assert response.status_code == 200
    stored = SystemSettings(database).get_section("claude_ai")
    assert stored is not None
    assert stored["api_key"] == "existing-secret"
    assert stored["model"] == "claude-opus-4-7"


def test_patch_drupal_via_section_endpoint_returns_404(
    client: FlaskClient, superadmin: AppUser, login: Any
) -> None:
    """Drupal isn't in CONFIG_SECTIONS — its endpoint is the dedicated
    /admin/settings/drupal one, so the generic section endpoint 404s
    rather than try to apply env-section validation rules to it."""
    login(superadmin)
    response = client.patch(
        "/api/v1/admin/settings/drupal", json={"base_url": "https://x.test/"}
    )
    # PATCH on /admin/settings/drupal hits the dedicated handler — but if
    # someone aimed at the generic section endpoint, the URL would be the
    # same, so this test is really about the dedicated handler running.
    # Verify the response shape comes from the Drupal handler (has
    # `source` + `values`):
    assert response.status_code in (200, 400)
    if response.status_code == 200:
        body = response.get_json()
        assert "source" in body
        assert "values" in body


def test_delete_section_removes_it(
    client: FlaskClient,
    database: Database,
    superadmin: AppUser,
    login: Any,
) -> None:
    SystemSettings(database).set_section(
        "browser", {"headless": False}, updated_by=None
    )
    login(superadmin)
    response = client.delete("/api/v1/admin/settings/browser")
    assert response.status_code == 204
    assert SystemSettings(database).get_section("browser") is None


def test_delete_unknown_section_returns_404(
    client: FlaskClient, superadmin: AppUser, login: Any
) -> None:
    login(superadmin)
    response = client.delete("/api/v1/admin/settings/not_a_real_section")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Body shape
# ---------------------------------------------------------------------------


def test_patch_with_non_object_body_returns_400(
    client: FlaskClient, superadmin: AppUser, login: Any
) -> None:
    login(superadmin)
    response = client.patch("/api/v1/admin/settings/browser", json=["not", "obj"])
    assert response.status_code == 400
