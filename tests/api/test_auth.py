"""Tests for the §5.13 auth REST surface.

Covers the email/password flows, Bearer-token validation in
``require_authenticated``, and the admin user-CRUD endpoints. SSO
endpoints test only the 404 path (provider disabled / unknown);
their happy paths depend on Microsoft/Google credentials that aren't
available in CI.
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
from auto_a11y.models.api_token import (
    ApiToken, hash_token, generate_raw_token,
)
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

    from config import Config
    cfg = Config()
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


def _make_user(
    database: Database,
    *,
    email: str,
    password: str = "hunter22",
    role: UserRole = UserRole.CLIENT,
    is_superadmin: bool = False,
) -> AppUser:
    user = AppUser.create(email=email, password=password, role=role)
    if is_superadmin:
        user.is_superadmin = True
    uid = database.create_app_user(user)
    refreshed = database.get_app_user(uid)
    assert refreshed is not None
    return refreshed


@pytest.fixture
def normal_user(database: Database) -> AppUser:
    return _make_user(
        database, email="alice@example.test", password="hunter22",
    )


@pytest.fixture
def admin_user(database: Database) -> AppUser:
    return _make_user(
        database, email="admin@example.test", password="hunter22",
        is_superadmin=True,
    )


@pytest.fixture
def session_login(client: FlaskClient) -> Iterator[Any]:
    def _login(user: AppUser) -> None:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    yield _login


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------


def test_login_happy_path_returns_token(
    client: FlaskClient, normal_user: AppUser, database: Database,
) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.test", "password": "hunter22"},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["token"].startswith("a11y_")
    assert body["user"]["email"] == "alice@example.test"
    assert body["token_record"]["user_id"] == normal_user.id

    # The token is actually persisted (hashed).
    persisted = database.get_api_token_by_hash(hash_token(body["token"]))
    assert persisted is not None
    assert persisted.user_id == normal_user.id


def test_login_unknown_email_returns_401(client: FlaskClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.test", "password": "x"},
    )
    assert response.status_code == 401


def test_login_wrong_password_returns_401(
    client: FlaskClient, normal_user: AppUser,
) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.test", "password": "wrong"},
    )
    assert response.status_code == 401


def test_login_inactive_user_returns_401(
    client: FlaskClient, normal_user: AppUser, database: Database,
) -> None:
    normal_user.is_active = False
    database.update_app_user(normal_user)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.test", "password": "hunter22"},
    )
    assert response.status_code == 401


def test_login_invalid_body_returns_401(client: FlaskClient) -> None:
    """Non-string email/password is treated as bad credentials."""
    response = client.post(
        "/api/v1/auth/login", json={"email": 123, "password": None},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /auth/logout
# ---------------------------------------------------------------------------


def test_logout_with_token_revokes(
    client: FlaskClient, normal_user: AppUser, database: Database,
) -> None:
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.test", "password": "hunter22"},
    )
    token = login_resp.get_json()["token"]

    response = client.post(
        "/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 204

    persisted = database.get_api_token_by_hash(hash_token(token))
    assert persisted is not None
    assert persisted.revoked_at is not None


def test_logout_without_token_is_idempotent_204(
    client: FlaskClient,
) -> None:
    """No header → still 204 (the user is "logged out" already)."""
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 204


# ---------------------------------------------------------------------------
# POST /auth/register
# ---------------------------------------------------------------------------


def test_register_happy_path_returns_201(
    client: FlaskClient, database: Database,
) -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "newcomer@example.test",
            "password": "hunter22",
            "display_name": "Newcomer",
        },
    )
    assert response.status_code == 201
    body = response.get_json()
    assert body["token"].startswith("a11y_")
    assert body["user"]["email"] == "newcomer@example.test"
    assert body["user"]["display_name"] == "Newcomer"

    assert database.get_app_user_by_email("newcomer@example.test") is not None


def test_register_missing_email_returns_400(client: FlaskClient) -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={"password": "hunter22"},
    )
    assert response.status_code == 400


def test_register_short_password_returns_400(client: FlaskClient) -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "x@example.test", "password": "abc"},
    )
    assert response.status_code == 400


def test_register_duplicate_email_returns_400(
    client: FlaskClient, normal_user: AppUser,
) -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "alice@example.test", "password": "hunter22"},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# POST /auth/forgot-password (always 202 — no enumeration)
# ---------------------------------------------------------------------------


def test_forgot_password_unknown_email_returns_202(
    client: FlaskClient,
) -> None:
    response = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "stranger@example.test"},
    )
    assert response.status_code == 202


def test_forgot_password_known_email_returns_202(
    client: FlaskClient, normal_user: AppUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing user → 202 same as the unknown case (no enumeration)."""
    calls: list[Any] = []

    def _capture(u: AppUser) -> bool:
        calls.append(u)
        return True

    monkeypatch.setattr(
        "auto_a11y.web.routes.auth.send_password_reset_email",
        _capture,
    )
    response = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "alice@example.test"},
    )
    assert response.status_code == 202
    assert len(calls) == 1
    assert calls[0].id == normal_user.id


# ---------------------------------------------------------------------------
# POST /auth/reset-password
# ---------------------------------------------------------------------------


def test_reset_password_bad_token_returns_400(client: FlaskClient) -> None:
    response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": "garbage", "password": "newpass1"},
    )
    assert response.status_code == 400


def test_reset_password_short_password_returns_400(
    client: FlaskClient,
) -> None:
    response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": "garbage", "password": "abc"},
    )
    assert response.status_code == 400


def test_reset_password_happy_path_changes_password(
    client: FlaskClient, normal_user: AppUser, database: Database,
    flask_app: Flask,
) -> None:
    with flask_app.app_context():
        from auto_a11y.web.routes.auth import generate_reset_token
        reset_token = generate_reset_token(normal_user.email)

    response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": reset_token, "password": "newhunter"},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["token"].startswith("a11y_")

    # The password actually changed.
    refreshed = database.get_app_user_by_email(normal_user.email)
    assert refreshed is not None
    assert refreshed.check_password("newhunter")


# ---------------------------------------------------------------------------
# GET / PATCH /auth/me
# ---------------------------------------------------------------------------


def test_me_anonymous_returns_401(client: FlaskClient) -> None:
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_me_via_session_returns_user(
    client: FlaskClient, normal_user: AppUser, session_login: Any,
) -> None:
    session_login(normal_user)
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 200
    assert response.get_json()["email"] == "alice@example.test"


def test_me_via_bearer_returns_user(
    client: FlaskClient, normal_user: AppUser,
) -> None:
    """A token from /auth/login authenticates /auth/me."""
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.test", "password": "hunter22"},
    )
    token = login_resp.get_json()["token"]

    response = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.get_json()["email"] == "alice@example.test"


def test_me_revoked_bearer_returns_401(
    client: FlaskClient, normal_user: AppUser,
) -> None:
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.test", "password": "hunter22"},
    )
    token = login_resp.get_json()["token"]
    client.post(
        "/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"},
    )
    response = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


def test_me_patch_changes_display_name(
    client: FlaskClient, normal_user: AppUser, database: Database,
    session_login: Any,
) -> None:
    session_login(normal_user)
    response = client.patch(
        "/api/v1/auth/me", json={"display_name": "Alice the Auditor"},
    )
    assert response.status_code == 200
    refreshed = database.get_app_user(normal_user.id) if normal_user.id else None
    assert refreshed is not None
    assert refreshed.display_name == "Alice the Auditor"


def test_me_patch_short_password_returns_400(
    client: FlaskClient, normal_user: AppUser, session_login: Any,
) -> None:
    session_login(normal_user)
    response = client.patch(
        "/api/v1/auth/me", json={"password": "abc"},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Admin user CRUD
# ---------------------------------------------------------------------------


def test_list_users_non_admin_returns_403(
    client: FlaskClient, normal_user: AppUser, session_login: Any,
) -> None:
    session_login(normal_user)
    response = client.get("/api/v1/users")
    assert response.status_code == 403


def test_list_users_admin_returns_all(
    client: FlaskClient, admin_user: AppUser, normal_user: AppUser,
    session_login: Any,
) -> None:
    session_login(admin_user)
    response = client.get("/api/v1/users")
    body = response.get_json()
    emails = {u["email"] for u in body["users"]}
    assert "alice@example.test" in emails
    assert "admin@example.test" in emails


def test_create_user_non_admin_returns_403(
    client: FlaskClient, normal_user: AppUser, session_login: Any,
) -> None:
    session_login(normal_user)
    response = client.post(
        "/api/v1/users",
        json={"email": "x@example.test", "password": "hunter22"},
    )
    assert response.status_code == 403


def test_create_user_invalid_role_returns_400(
    client: FlaskClient, admin_user: AppUser, session_login: Any,
) -> None:
    session_login(admin_user)
    response = client.post(
        "/api/v1/users",
        json={
            "email": "x@example.test", "password": "hunter22", "role": "wizard",
        },
    )
    assert response.status_code == 400


def test_create_user_happy_path_returns_201(
    client: FlaskClient, admin_user: AppUser, session_login: Any,
    database: Database,
) -> None:
    session_login(admin_user)
    response = client.post(
        "/api/v1/users",
        json={
            "email": "fresh@example.test",
            "password": "hunter22",
            "display_name": "Fresh",
            "role": "auditor",
        },
    )
    assert response.status_code == 201
    body = response.get_json()
    assert body["email"] == "fresh@example.test"
    assert body["role"] == "auditor"
    assert response.headers["Location"].startswith("/api/v1/users/")


def test_get_user_admin_can_read(
    client: FlaskClient, admin_user: AppUser, normal_user: AppUser,
    session_login: Any,
) -> None:
    session_login(admin_user)
    response = client.get(f"/api/v1/users/{normal_user.id}")
    assert response.status_code == 200


def test_get_user_unknown_returns_404(
    client: FlaskClient, admin_user: AppUser, session_login: Any,
) -> None:
    session_login(admin_user)
    response = client.get("/api/v1/users/507f1f77bcf86cd799999999")
    assert response.status_code == 404


def test_patch_user_role_change(
    client: FlaskClient, admin_user: AppUser, normal_user: AppUser,
    session_login: Any, database: Database,
) -> None:
    session_login(admin_user)
    response = client.patch(
        f"/api/v1/users/{normal_user.id}", json={"role": "auditor"},
    )
    body = response.get_json()
    assert body["role"] == "auditor"
    refreshed = database.get_app_user(normal_user.id) if normal_user.id else None
    assert refreshed is not None
    assert refreshed.role == UserRole.AUDITOR


def test_patch_user_invalid_role_returns_400(
    client: FlaskClient, admin_user: AppUser, normal_user: AppUser,
    session_login: Any,
) -> None:
    session_login(admin_user)
    response = client.patch(
        f"/api/v1/users/{normal_user.id}", json={"role": "wizard"},
    )
    assert response.status_code == 400


def test_delete_user_admin_can_delete(
    client: FlaskClient, admin_user: AppUser, normal_user: AppUser,
    session_login: Any, database: Database,
) -> None:
    session_login(admin_user)
    response = client.delete(f"/api/v1/users/{normal_user.id}")
    assert response.status_code == 204
    assert normal_user.id is not None
    assert database.get_app_user(normal_user.id) is None


# ---------------------------------------------------------------------------
# SSO 404 paths (happy paths need provider creds)
# ---------------------------------------------------------------------------


def test_sso_unknown_provider_returns_404(client: FlaskClient) -> None:
    response = client.get("/api/v1/auth/sso/twitter/url")
    assert response.status_code == 404


def test_sso_disabled_provider_returns_404(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both SSO providers are disabled, both URL endpoints 404.

    The derived ``MICROSOFT_SSO_ENABLED`` / ``GOOGLE_SSO_ENABLED``
    properties on Config can pick up credentials from a developer's
    .env at test time; monkey-patch ``_sso_enabled`` so the test
    asserts the right behaviour regardless of dev-machine config.
    """
    def _disabled(_provider: str) -> bool:
        return False

    monkeypatch.setattr(
        "auto_a11y.web.routes.api._sso_enabled", _disabled,
    )
    response = client.get("/api/v1/auth/sso/microsoft/url")
    assert response.status_code == 404
    response = client.get("/api/v1/auth/sso/google/url")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Bearer auth on existing /api/v1 endpoints
# ---------------------------------------------------------------------------


def test_bearer_token_authorizes_existing_endpoints(
    client: FlaskClient, normal_user: AppUser,
) -> None:
    """A token from /auth/login should also let the caller hit /users/me."""
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.test", "password": "hunter22"},
    )
    token = login_resp.get_json()["token"]
    response = client.get(
        "/api/v1/users/me", headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["email"] == "alice@example.test"


def test_invalid_bearer_returns_401(client: FlaskClient) -> None:
    response = client.get(
        "/api/v1/users/me", headers={"Authorization": "Bearer not_a_real_token"},
    )
    assert response.status_code == 401


def test_malformed_bearer_returns_401(client: FlaskClient) -> None:
    """Authorization header without the Bearer prefix is rejected."""
    response = client.get(
        "/api/v1/users/me", headers={"Authorization": "Basic abc"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Token lifecycle directly through the model + db
# ---------------------------------------------------------------------------


def test_token_hash_is_deterministic_per_raw() -> None:
    raw = "a11y_xyz123"
    assert hash_token(raw) == hash_token(raw)


def test_token_generation_yields_unique_strings() -> None:
    a, b = generate_raw_token(), generate_raw_token()
    assert a != b
    assert a.startswith("a11y_")
    assert b.startswith("a11y_")


def test_api_token_is_valid_after_creation() -> None:
    """Newly-minted tokens are not revoked and not expired."""
    from datetime import datetime, timedelta
    token = ApiToken(
        user_id="u",
        token_hash=hash_token("a11y_aaa"),
        expires_at=datetime.now() + timedelta(days=30),
    )
    assert token.is_valid


def test_api_token_revoked_is_invalid() -> None:
    from datetime import datetime
    token = ApiToken(
        user_id="u",
        token_hash=hash_token("a11y_aaa"),
        revoked_at=datetime.now(),
    )
    assert not token.is_valid


def test_api_token_expired_is_invalid() -> None:
    from datetime import datetime, timedelta
    token = ApiToken(
        user_id="u",
        token_hash=hash_token("a11y_aaa"),
        expires_at=datetime.now() - timedelta(seconds=1),
    )
    assert not token.is_valid
