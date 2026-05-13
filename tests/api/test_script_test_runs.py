"""Tests for the synchronous ``POST /api/v1/scripts/<id>/test-runs``
endpoint (§5.8).

The real route fires a fresh BrowserManager + ScriptExecutor against a
live URL — both are monkey-patched here so the test runs without
playwright. The route's job is to:

1. Resolve the script and its target URL (PAGE → page.url,
   WEBSITE → website.url)
2. Reject TEST_RUN-scoped scripts and missing targets (404)
3. Reject ``BROWSER_MODE`` ∈ {disabled, remote} (409) since the
   endpoint runs the browser inline
4. Pass through the executor's ``{success, duration_ms,
   steps_executed, error}`` dict
5. Return 500 with the same envelope shape on executor exceptions
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Generator, Iterator
from typing import Any

import pytest
from bson import ObjectId
from flask import Flask
from flask.testing import FlaskClient
from flask_login import LoginManager
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from auto_a11y.core.database import Database
from auto_a11y.models import Page, PageStatus
from auto_a11y.models.app_user import AppUser, UserRole
from auto_a11y.models.page_setup_script import PageSetupScript, ScriptScope
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


def _make_page(database: Database, website: Website) -> Page:
    assert website.id is not None
    page = Page(
        website_id=website.id,
        url=f"{website.url}page-{uuid.uuid4().hex[:6]}",
        status=PageStatus.DISCOVERED,
    )
    pid = database.create_page(page)
    saved = database.get_page(pid)
    assert saved is not None
    return saved


def _make_script(
    database: Database,
    *,
    website: Website,
    page: Page | None = None,
    scope: ScriptScope = ScriptScope.PAGE,
) -> PageSetupScript:
    assert website.id is not None
    script = PageSetupScript(
        name=f"Script {uuid.uuid4().hex[:6]}",
        description="",
        scope=scope,
        website_id=website.id,
        page_id=page.id if page else None,
        enabled=True,
    )
    sid = database.create_page_setup_script(script)
    script.mongo_id = ObjectId(sid)
    return script


@pytest.fixture
def project(database: Database) -> Project:
    return _make_project(database)


@pytest.fixture
def website(database: Database, project: Project) -> Website:
    return _make_website(database, project)


@pytest.fixture
def page(database: Database, website: Website) -> Page:
    return _make_page(database, website)


@pytest.fixture
def page_script(
    database: Database, website: Website, page: Page,
) -> PageSetupScript:
    return _make_script(database, website=website, page=page)


@pytest.fixture
def website_script(
    database: Database, website: Website,
) -> PageSetupScript:
    return _make_script(database, website=website, scope=ScriptScope.WEBSITE)


@pytest.fixture
def login(client: FlaskClient) -> Iterator[Any]:
    def _login(user: AppUser) -> None:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    yield _login


@pytest.fixture
def stub_browser_and_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Stub BrowserManager and ScriptExecutor so no real browser launches.

    Returns a dict that tests can mutate to control what the stubbed
    executor returns, plus a ``goto_calls`` list to assert which URL
    the route asked the browser to navigate to.
    """
    state: dict[str, Any] = {
        "result": {
            "success": True,
            "duration_ms": 1234,
            "steps_executed": 3,
            "error": None,
        },
        "goto_calls": [],
        "raise_in_execute": None,
    }

    class _FakePage:
        async def goto(self, url: str, **_: Any) -> None:
            state["goto_calls"].append(url)

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

    class _FakeScriptExecutor:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        async def execute_script(self, _page: Any, _script: Any) -> dict[str, Any]:
            if state["raise_in_execute"] is not None:
                raise state["raise_in_execute"]
            result: dict[str, Any] = state["result"]
            return result

    from auto_a11y.core import browser_manager as bm_module
    from auto_a11y.testing import script_executor as se_module

    monkeypatch.setattr(bm_module, "BrowserManager", _FakeBrowserManager)
    monkeypatch.setattr(se_module, "ScriptExecutor", _FakeScriptExecutor)
    return state


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_unknown_script_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
    stub_browser_and_executor: dict[str, Any],
) -> None:
    login(admin_user)
    response = client.post("/api/v1/scripts/507f1f77bcf86cd799999999/test-runs")
    assert response.status_code == 404


def test_anonymous_returns_401(
    client: FlaskClient, page_script: PageSetupScript,
    stub_browser_and_executor: dict[str, Any],
) -> None:
    response = client.post(f"/api/v1/scripts/{page_script.id}/test-runs")
    assert response.status_code == 401


def test_test_run_scoped_script_is_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, website: Website,
    stub_browser_and_executor: dict[str, Any],
) -> None:
    """TEST_RUN-scoped scripts are runtime-internal and not exposed."""
    script = _make_script(
        database, website=website, scope=ScriptScope.TEST_RUN,
    )
    login(admin_user)
    response = client.post(f"/api/v1/scripts/{script.id}/test-runs")
    assert response.status_code == 404


def test_browser_disabled_returns_409(
    client: FlaskClient, admin_user: AppUser, login: Any,
    page_script: PageSetupScript, flask_app: Flask,
    stub_browser_and_executor: dict[str, Any],
) -> None:
    cfg = getattr(flask_app, "app_config")
    cfg.BROWSER_MODE = "disabled"
    login(admin_user)
    response = client.post(f"/api/v1/scripts/{page_script.id}/test-runs")
    assert response.status_code == 409


def test_browser_remote_returns_409(
    client: FlaskClient, admin_user: AppUser, login: Any,
    page_script: PageSetupScript, flask_app: Flask,
    stub_browser_and_executor: dict[str, Any],
) -> None:
    cfg = getattr(flask_app, "app_config")
    cfg.BROWSER_MODE = "remote"
    login(admin_user)
    response = client.post(f"/api/v1/scripts/{page_script.id}/test-runs")
    assert response.status_code == 409


def test_page_scope_uses_page_url(
    client: FlaskClient, admin_user: AppUser, login: Any,
    page_script: PageSetupScript, page: Page,
    stub_browser_and_executor: dict[str, Any],
) -> None:
    """PAGE-scoped → ``page.url`` is the navigation target."""
    login(admin_user)
    response = client.post(f"/api/v1/scripts/{page_script.id}/test-runs")
    assert response.status_code == 200
    body = response.get_json()
    assert body["target_url"] == page.url
    assert stub_browser_and_executor["goto_calls"] == [page.url]


def test_website_scope_uses_website_url(
    client: FlaskClient, admin_user: AppUser, login: Any,
    website_script: PageSetupScript, website: Website,
    stub_browser_and_executor: dict[str, Any],
) -> None:
    login(admin_user)
    response = client.post(f"/api/v1/scripts/{website_script.id}/test-runs")
    assert response.status_code == 200
    body = response.get_json()
    assert body["target_url"] == website.url
    assert stub_browser_and_executor["goto_calls"] == [website.url]


def test_happy_path_returns_executor_result(
    client: FlaskClient, admin_user: AppUser, login: Any,
    page_script: PageSetupScript,
    stub_browser_and_executor: dict[str, Any],
) -> None:
    """The 200 body mirrors the executor's dict."""
    stub_browser_and_executor["result"] = {
        "success": True,
        "duration_ms": 5678,
        "steps_executed": 9,
        "error": None,
    }
    login(admin_user)
    response = client.post(f"/api/v1/scripts/{page_script.id}/test-runs")
    body = response.get_json()
    assert body["success"] is True
    assert body["duration_ms"] == 5678
    assert body["steps_executed"] == 9
    assert body["error"] is None


def test_executor_failure_returns_inline_result(
    client: FlaskClient, admin_user: AppUser, login: Any,
    page_script: PageSetupScript,
    stub_browser_and_executor: dict[str, Any],
) -> None:
    """An executor that returns ``success: False`` keeps status 200."""
    stub_browser_and_executor["result"] = {
        "success": False,
        "duration_ms": 100,
        "steps_executed": 2,
        "error": "Element not found: #login",
    }
    login(admin_user)
    response = client.post(f"/api/v1/scripts/{page_script.id}/test-runs")
    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is False
    assert body["error"] == "Element not found: #login"


def test_executor_exception_returns_500_with_envelope(
    client: FlaskClient, admin_user: AppUser, login: Any,
    page_script: PageSetupScript,
    stub_browser_and_executor: dict[str, Any],
) -> None:
    """Unhandled executor exceptions → 500 carrying the same JSON shape."""
    stub_browser_and_executor["raise_in_execute"] = RuntimeError("browser crashed")
    login(admin_user)
    response = client.post(f"/api/v1/scripts/{page_script.id}/test-runs")
    assert response.status_code == 500
    body = response.get_json()
    assert body["success"] is False
    assert "browser crashed" in body["error"]
    assert body["duration_ms"] == 0
    assert body["steps_executed"] == 0


def test_page_scope_with_missing_page_target_is_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, website: Website, page: Page,
    stub_browser_and_executor: dict[str, Any],
) -> None:
    """A PAGE-scoped script whose page was deleted → 404."""
    script = _make_script(database, website=website, page=page)
    assert page.id is not None
    database.delete_page(page.id)

    login(admin_user)
    response = client.post(f"/api/v1/scripts/{script.id}/test-runs")
    assert response.status_code == 404
