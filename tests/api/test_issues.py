"""Tests for the IssueCatalog documentation-status REST endpoints:

- ``GET  /api/v1/issues/documentation-stats``
- ``PATCH /api/v1/issues/<code>``

The catalog itself is a Python-resident dict
(``auto_a11y.reporting.issue_catalog.IssueCatalog.ISSUES``); only the
``production_ready`` flag is persisted. The legacy HTML endpoint had no
auth — these new REST endpoints fix that (read = any authenticated
user, write = superadmin).

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
    database: Database, *, role: UserRole, email: str, superadmin: bool = False
) -> AppUser:
    user = AppUser.create(email=email, password="x", role=role)
    user_id = database.create_app_user(user)
    saved = database.get_app_user(user_id)
    assert saved is not None
    if superadmin:
        saved.is_superadmin = True
        database.update_app_user(saved)
        refreshed = database.get_app_user(user_id)
        assert refreshed is not None
        return refreshed
    return saved


@pytest.fixture
def superadmin(database: Database) -> AppUser:
    return _make_user(
        database,
        role=UserRole.ADMIN,
        email="root@example.test",
        superadmin=True,
    )


@pytest.fixture
def auditor(database: Database) -> AppUser:
    return _make_user(
        database, role=UserRole.AUDITOR, email="aud@example.test"
    )


@pytest.fixture
def login(client: FlaskClient) -> Iterator[Any]:
    def _login(user: AppUser) -> None:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    yield _login


def _known_issue_code() -> str:
    """Pick a real catalog code so 404 tests don't accidentally hit it."""
    from auto_a11y.reporting.issue_catalog import IssueCatalog

    # Catalog dict is deterministic — first key suffices.
    return next(iter(IssueCatalog.ISSUES.keys()))


# ---------------------------------------------------------------------------
# GET /api/v1/issues/documentation-stats
# ---------------------------------------------------------------------------


def test_stats_anonymous_returns_401(client: FlaskClient) -> None:
    response = client.get("/api/v1/issues/documentation-stats")
    assert response.status_code == 401
    assert response.headers["Content-Type"] == "application/problem+json"


def test_stats_authenticated_user_can_read(
    client: FlaskClient, auditor: AppUser, login: Any
) -> None:
    """An auditor without superadmin should still be able to read."""
    login(auditor)
    response = client.get("/api/v1/issues/documentation-stats")
    assert response.status_code == 200
    body = response.get_json()
    assert "stats" in body
    assert {"total", "production_ready", "pending", "percentage_ready"} <= set(
        body["stats"].keys()
    )
    assert "production_ready_codes" in body
    assert "pending_codes" in body
    # No real codes have been toggled yet — production_ready_count is 0.
    assert body["stats"]["production_ready"] == 0
    assert body["stats"]["pending"] == body["stats"]["total"]


def test_stats_reflects_flag_after_patch(
    client: FlaskClient,
    superadmin: AppUser,
    login: Any,
) -> None:
    """After flipping one issue, stats reports it as production_ready."""
    code = _known_issue_code()
    login(superadmin)
    patch = client.patch(
        f"/api/v1/issues/{code}",
        json={"production_ready": True},
    )
    assert patch.status_code == 200

    stats = client.get("/api/v1/issues/documentation-stats")
    assert stats.status_code == 200
    body = stats.get_json()
    assert body["stats"]["production_ready"] == 1
    assert code in body["production_ready_codes"]
    assert code not in body["pending_codes"]


# ---------------------------------------------------------------------------
# PATCH /api/v1/issues/<code>
# ---------------------------------------------------------------------------


def test_patch_anonymous_returns_401(client: FlaskClient) -> None:
    response = client.patch(
        f"/api/v1/issues/{_known_issue_code()}",
        json={"production_ready": True},
    )
    assert response.status_code == 401


def test_patch_non_superadmin_returns_403(
    client: FlaskClient, auditor: AppUser, login: Any
) -> None:
    """Auditors (not superadmins) cannot flip the production_ready flag."""
    login(auditor)
    response = client.patch(
        f"/api/v1/issues/{_known_issue_code()}",
        json={"production_ready": True},
    )
    assert response.status_code == 403


def test_patch_unknown_issue_returns_404(
    client: FlaskClient, superadmin: AppUser, login: Any
) -> None:
    login(superadmin)
    response = client.patch(
        "/api/v1/issues/DefinitelyNotARealIssueCode",
        json={"production_ready": True},
    )
    assert response.status_code == 404


def test_patch_missing_field_returns_400(
    client: FlaskClient, superadmin: AppUser, login: Any
) -> None:
    login(superadmin)
    response = client.patch(
        f"/api/v1/issues/{_known_issue_code()}",
        json={},
    )
    assert response.status_code == 400
    body = response.get_json()
    fields = {e["field"] for e in body["errors"]}
    assert "production_ready" in fields


def test_patch_wrong_type_returns_400(
    client: FlaskClient, superadmin: AppUser, login: Any
) -> None:
    login(superadmin)
    response = client.patch(
        f"/api/v1/issues/{_known_issue_code()}",
        json={"production_ready": "yes"},
    )
    assert response.status_code == 400


def test_patch_round_trip(
    client: FlaskClient, superadmin: AppUser, login: Any
) -> None:
    code = _known_issue_code()
    login(superadmin)

    on = client.patch(
        f"/api/v1/issues/{code}", json={"production_ready": True}
    )
    assert on.status_code == 200
    assert on.get_json() == {"issue_code": code, "production_ready": True}

    off = client.patch(
        f"/api/v1/issues/{code}", json={"production_ready": False}
    )
    assert off.status_code == 200
    assert off.get_json() == {"issue_code": code, "production_ready": False}
