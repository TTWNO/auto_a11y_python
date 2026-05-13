"""Tests for the §5.11 test-login action endpoints:

- ``POST /api/v1/project-test-users/<id>/test-login``
- ``POST /api/v1/website-test-users/<id>/test-login``

Both routes spin a fresh browser and run :class:`LoginAutomation` —
both are monkey-patched here so the tests exercise the validation,
auth, and envelope shape without launching Playwright.
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
from auto_a11y.models.project_user import (
    AuthenticationMethod as ProjectAuthMethod,
    LoginConfig as ProjectLoginConfig,
    ProjectUser,
)
from auto_a11y.models.website import Website
from auto_a11y.models.website_user import (
    AuthenticationMethod as WebsiteAuthMethod,
    LoginConfig as WebsiteLoginConfig,
    WebsiteUser,
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

    from config import Config
    cfg = Config()
    cfg.BROWSER_MODE = "local"
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


@pytest.fixture
def admin_user(database: Database) -> AppUser:
    user = _make_user(database, role=UserRole.ADMIN, email="admin@example.test")
    user.is_superadmin = True
    database.update_app_user(user)
    refreshed = database.get_app_user(user.id) if user.id else None
    assert refreshed is not None
    return refreshed


def _make_project(database: Database) -> Project:
    project = Project(
        name=f"Project {uuid.uuid4().hex[:8]}",
        description="",
        status=ProjectStatus.ACTIVE,
        config={},
    )
    pid = database.create_project(project)
    saved = database.get_project(pid)
    assert saved is not None
    return saved


def _make_website(database: Database, project: Project) -> Website:
    assert project.id is not None
    website = Website(
        project_id=project.id,
        url=f"https://example-{uuid.uuid4().hex[:8]}.test/",
        name="Test website",
    )
    wid = database.create_website(website)
    saved = database.get_website(wid)
    assert saved is not None
    return saved


def _make_project_user(
    database: Database,
    project: Project,
    *,
    login_url: str | None = "https://example.test/login",
    manual: bool = False,
) -> ProjectUser:
    assert project.id is not None
    user = ProjectUser(
        project_id=project.id,
        username="alice",
        password="hunter2",
        login_config=ProjectLoginConfig(
            authentication_method=(
                ProjectAuthMethod.MANUAL_LOGIN if manual
                else ProjectAuthMethod.FORM_LOGIN
            ),
            login_url=login_url,
        ),
    )
    uid = database.create_project_user(user)
    saved = database.get_project_user(uid)
    assert saved is not None
    return saved


def _make_website_user(
    database: Database,
    website: Website,
    *,
    login_url: str | None = "https://example.test/login",
    manual: bool = False,
) -> WebsiteUser:
    assert website.id is not None
    user = WebsiteUser(
        website_id=website.id,
        username="bob",
        password="hunter2",
        login_config=WebsiteLoginConfig(
            authentication_method=(
                WebsiteAuthMethod.MANUAL_LOGIN if manual
                else WebsiteAuthMethod.FORM_LOGIN
            ),
            login_url=login_url,
        ),
    )
    uid = database.create_website_user(user)
    saved = database.get_website_user(uid)
    assert saved is not None
    return saved


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


@pytest.fixture
def stub_login_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Replace BrowserManager + LoginAutomation with no-op stubs.

    Tests mutate ``state["result"]`` to control what perform_login
    returns, or ``state["raise"]`` to make it throw.
    """
    state: dict[str, Any] = {
        "result": {
            "success": True,
            "duration_ms": 850,
            "error": None,
        },
        "raise": None,
        "perform_calls": [],
    }

    class _FakePage:
        pass

    class _FakeContext:
        async def new_page(self) -> _FakePage:
            return _FakePage()

    class _FakeBrowserManager:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        async def start(self) -> None:
            pass

        async def create_context(self) -> _FakeContext:
            return _FakeContext()

        async def stop(self) -> None:
            pass

    class _FakeLoginAutomation:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        async def perform_login(
            self, _page: Any, user_obj: Any, *, timeout: int,
        ) -> dict[str, Any]:
            state["perform_calls"].append(
                {"user_id": user_obj.id, "timeout": timeout},
            )
            if state["raise"] is not None:
                raise state["raise"]
            result: dict[str, Any] = state["result"]
            return result

    from auto_a11y.core import browser_manager as bm_module
    from auto_a11y.testing import login_automation as la_module

    monkeypatch.setattr(bm_module, "BrowserManager", _FakeBrowserManager)
    monkeypatch.setattr(la_module, "LoginAutomation", _FakeLoginAutomation)
    return state


# ---------------------------------------------------------------------------
# project-test-users
# ---------------------------------------------------------------------------


def test_project_login_unknown_user_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
    stub_login_pipeline: dict[str, Any],
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/project-test-users/507f1f77bcf86cd799999999/test-login"
    )
    assert response.status_code == 404


def test_project_login_anonymous_returns_401(
    client: FlaskClient, database: Database, project: Project,
    stub_login_pipeline: dict[str, Any],
) -> None:
    user = _make_project_user(database, project)
    response = client.post(
        f"/api/v1/project-test-users/{user.id}/test-login"
    )
    assert response.status_code == 401


def test_project_login_missing_url_form_login_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project,
    stub_login_pipeline: dict[str, Any],
) -> None:
    """FORM_LOGIN without login_url → 400, no browser launch."""
    user = _make_project_user(database, project, login_url=None)
    login(admin_user)
    response = client.post(
        f"/api/v1/project-test-users/{user.id}/test-login"
    )
    assert response.status_code == 400
    assert stub_login_pipeline["perform_calls"] == []


def test_project_login_missing_url_manual_login_is_ok(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project,
    stub_login_pipeline: dict[str, Any],
) -> None:
    """MANUAL_LOGIN doesn't need a login_url — the operator drives it."""
    user = _make_project_user(
        database, project, login_url=None, manual=True,
    )
    login(admin_user)
    response = client.post(
        f"/api/v1/project-test-users/{user.id}/test-login"
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["manual_login"] is True
    assert body["wait_seconds"] is not None


def test_project_login_browser_mode_remote_returns_409(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project, flask_app: Flask,
    stub_login_pipeline: dict[str, Any],
) -> None:
    cfg = getattr(flask_app, "app_config")
    cfg.BROWSER_MODE = "remote"
    user = _make_project_user(database, project)
    login(admin_user)
    response = client.post(
        f"/api/v1/project-test-users/{user.id}/test-login"
    )
    assert response.status_code == 409


def test_project_login_happy_path_returns_200(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project,
    stub_login_pipeline: dict[str, Any],
) -> None:
    user = _make_project_user(database, project)
    login(admin_user)
    response = client.post(
        f"/api/v1/project-test-users/{user.id}/test-login"
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["user_id"] == user.id
    assert body["scope"] == "project"
    assert body["success"] is True
    assert body["duration_ms"] == 850
    assert body["manual_login"] is False
    assert body["wait_seconds"] is None
    # perform_login was called once with the right user.
    assert len(stub_login_pipeline["perform_calls"]) == 1
    assert stub_login_pipeline["perform_calls"][0]["user_id"] == user.id


def test_project_login_perform_failure_returns_200_with_envelope(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project,
    stub_login_pipeline: dict[str, Any],
) -> None:
    """perform_login returning ``success: false`` keeps 200."""
    stub_login_pipeline["result"] = {
        "success": False,
        "duration_ms": 2000,
        "error": "Login form not found",
    }
    user = _make_project_user(database, project)
    login(admin_user)
    response = client.post(
        f"/api/v1/project-test-users/{user.id}/test-login"
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is False
    assert body["error"] == "Login form not found"


def test_project_login_exception_returns_500_with_envelope(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project,
    stub_login_pipeline: dict[str, Any],
) -> None:
    """perform_login raising → 500 with the same envelope shape."""
    stub_login_pipeline["raise"] = RuntimeError("browser exploded")
    user = _make_project_user(database, project)
    login(admin_user)
    response = client.post(
        f"/api/v1/project-test-users/{user.id}/test-login"
    )
    assert response.status_code == 500
    body = response.get_json()
    assert body["success"] is False
    assert "browser exploded" in body["error"]
    assert body["duration_ms"] == 0
    assert body["scope"] == "project"


# ---------------------------------------------------------------------------
# website-test-users
# ---------------------------------------------------------------------------


def test_website_login_unknown_user_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
    stub_login_pipeline: dict[str, Any],
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/website-test-users/507f1f77bcf86cd799999999/test-login"
    )
    assert response.status_code == 404


def test_website_login_anonymous_returns_401(
    client: FlaskClient, database: Database, website: Website,
    stub_login_pipeline: dict[str, Any],
) -> None:
    user = _make_website_user(database, website)
    response = client.post(
        f"/api/v1/website-test-users/{user.id}/test-login"
    )
    assert response.status_code == 401


def test_website_login_missing_url_form_login_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, website: Website,
    stub_login_pipeline: dict[str, Any],
) -> None:
    user = _make_website_user(database, website, login_url=None)
    login(admin_user)
    response = client.post(
        f"/api/v1/website-test-users/{user.id}/test-login"
    )
    assert response.status_code == 400


def test_website_login_happy_path_returns_200(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, website: Website,
    stub_login_pipeline: dict[str, Any],
) -> None:
    user = _make_website_user(database, website)
    login(admin_user)
    response = client.post(
        f"/api/v1/website-test-users/{user.id}/test-login"
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["user_id"] == user.id
    assert body["scope"] == "website"
    assert body["success"] is True


def test_website_login_manual_carries_wait_seconds(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, website: Website,
    stub_login_pipeline: dict[str, Any],
) -> None:
    user = _make_website_user(
        database, website, login_url=None, manual=True,
    )
    login(admin_user)
    response = client.post(
        f"/api/v1/website-test-users/{user.id}/test-login"
    )
    body = response.get_json()
    assert body["manual_login"] is True
    # Default LoginConfig.manual_login_wait_seconds is 120.
    assert body["wait_seconds"] == 120


def test_website_login_browser_mode_disabled_returns_409(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, website: Website, flask_app: Flask,
    stub_login_pipeline: dict[str, Any],
) -> None:
    cfg = getattr(flask_app, "app_config")
    cfg.BROWSER_MODE = "disabled"
    user = _make_website_user(database, website)
    login(admin_user)
    response = client.post(
        f"/api/v1/website-test-users/{user.id}/test-login"
    )
    assert response.status_code == 409
