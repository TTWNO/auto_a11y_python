"""Tests for the /api/v1/test-results/compare REST endpoint.

The legacy POST-with-body shape is replaced by GET-with-query-params,
since this endpoint is a pure read with no resource mutation. Per
docs/REST_API_ROADMAP.md §5.4.

Tests skip when ``mongod`` is not reachable.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Generator, Iterator
from datetime import datetime
from typing import Any

import pytest
from bson import ObjectId
from flask import Flask
from flask.testing import FlaskClient
from flask_login import LoginManager
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from auto_a11y.core.database import Database
from auto_a11y.models.app_user import AppUser, UserRole
from auto_a11y.models.test_result import (
    ImpactLevel,
    TestResult,
    Violation,
)


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


def _make_user(database: Database) -> AppUser:
    user = AppUser.create(
        email=f"user-{uuid.uuid4().hex[:8]}@example.test",
        password="x",
        role=UserRole.AUDITOR,
    )
    user_id = database.create_app_user(user)
    saved = database.get_app_user(user_id)
    assert saved is not None
    return saved


def _make_violation(violation_id: str) -> Violation:
    return Violation(
        id=violation_id,
        impact=ImpactLevel.MEDIUM,
        touchpoint="structure",
        description=f"Violation {violation_id}",
        wcag_criteria=["1.3.1"],
        element="<div>",
        xpath="/html/body/div[1]",
    )


def _persist_test_result(
    database: Database, *, violations: list[Violation]
) -> TestResult:
    """Insert a TestResult directly so the compare endpoint has rows to read."""
    page_id_str = str(ObjectId())
    # ``violation_count`` and friends are computed properties on TestResult;
    # they fall out of ``len(violations)`` etc. once persisted.
    result = TestResult(
        page_id=page_id_str,
        test_date=datetime.now(),
        duration_ms=42,
        violations=violations,
    )
    raw = result.to_dict()
    inserted = database.test_results.insert_one(raw)
    result.mongo_id = inserted.inserted_id
    return result


@pytest.fixture
def authed_user(database: Database) -> AppUser:
    return _make_user(database)


@pytest.fixture
def login(client: FlaskClient) -> Iterator[Any]:
    def _login(user: AppUser) -> None:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    yield _login


# ---------------------------------------------------------------------------
# Auth + bad input
# ---------------------------------------------------------------------------


def test_anonymous_returns_401(client: FlaskClient) -> None:
    response = client.get("/api/v1/test-results/compare?a=x&b=y")
    assert response.status_code == 401
    assert response.headers["Content-Type"] == "application/problem+json"


def test_missing_a_returns_400(
    client: FlaskClient, authed_user: AppUser, login: Any
) -> None:
    login(authed_user)
    response = client.get("/api/v1/test-results/compare?b=somewhere")
    assert response.status_code == 400
    field_errors = {e["field"] for e in response.get_json()["errors"]}
    assert "a" in field_errors


def test_missing_both_lists_both_in_problem_details(
    client: FlaskClient, authed_user: AppUser, login: Any
) -> None:
    login(authed_user)
    response = client.get("/api/v1/test-results/compare")
    assert response.status_code == 400
    field_errors = {e["field"] for e in response.get_json()["errors"]}
    assert field_errors == {"a", "b"}


def test_unknown_id_returns_404(
    client: FlaskClient, authed_user: AppUser, login: Any
) -> None:
    login(authed_user)
    url = "/api/v1/test-results/compare?a=507f1f77bcf86cd799999999&b=507f1f77bcf86cd799999998"
    response = client.get(url)
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Comparison shape
# ---------------------------------------------------------------------------


def test_compare_classifies_new_fixed_persistent(
    client: FlaskClient,
    database: Database,
    authed_user: AppUser,
    login: Any,
) -> None:
    """A has v1 + v2, B has v2 + v3 → v3 is new, v1 is fixed, v2 persists."""
    v1 = _make_violation("ErrOne")
    v2 = _make_violation("ErrTwo")
    v3 = _make_violation("ErrThree")
    a = _persist_test_result(database, violations=[v1, v2])
    b = _persist_test_result(database, violations=[v2, v3])

    login(authed_user)
    response = client.get(
        f"/api/v1/test-results/compare?a={a.id}&b={b.id}"
    )
    assert response.status_code == 200
    body = response.get_json()
    assert {v["id"] for v in body["new_violations"]} == {"ErrThree"}
    assert {v["id"] for v in body["fixed_violations"]} == {"ErrOne"}
    assert {v["id"] for v in body["persistent_violations"]} == {"ErrTwo"}
    assert body["summary"]["new_count"] == 1
    assert body["summary"]["fixed_count"] == 1
    assert body["summary"]["persistent_count"] == 1
    # B has 2 violations, A has 2 — net change is 0.
    assert body["summary"]["net_change"] == 0


def test_compare_returns_a_and_b_summaries(
    client: FlaskClient,
    database: Database,
    authed_user: AppUser,
    login: Any,
) -> None:
    """The response surfaces per-result metadata under ``a`` and ``b``."""
    a = _persist_test_result(database, violations=[_make_violation("ErrA")])
    b = _persist_test_result(database, violations=[])

    login(authed_user)
    response = client.get(
        f"/api/v1/test-results/compare?a={a.id}&b={b.id}"
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["a"]["id"] == a.id
    assert body["b"]["id"] == b.id
    assert body["a"]["violation_count"] == 1
    assert body["b"]["violation_count"] == 0
    assert body["summary"]["fixed_count"] == 1
    assert body["summary"]["net_change"] == -1


def test_legacy_post_returns_405(
    client: FlaskClient, authed_user: AppUser, login: Any
) -> None:
    """The endpoint is GET-only now; a POST should be rejected by Flask
    with a 405 Method Not Allowed (Werkzeug default; not a Problem-Details
    response, which is acceptable for routing-layer rejections)."""
    login(authed_user)
    response = client.post(
        "/api/v1/test-results/compare",
        json={"result_id_1": "x", "result_id_2": "y"},
    )
    assert response.status_code == 405
