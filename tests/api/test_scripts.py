"""Tests for the page-setup-scripts REST endpoints.

Endpoints exercised:

- GET    /api/v1/pages/<page_id>/scripts
- POST   /api/v1/pages/<page_id>/scripts
- GET    /api/v1/websites/<website_id>/scripts
- POST   /api/v1/websites/<website_id>/scripts
- GET    /api/v1/scripts/<script_id>
- PUT    /api/v1/scripts/<script_id>
- PATCH  /api/v1/scripts/<script_id>
- DELETE /api/v1/scripts/<script_id>

Coverage:

- Anonymous → 401, authenticated non-member → 403, unknown scope → 404.
- Round-trip: POST → GET → PATCH (toggle enabled) → PUT → DELETE → 404.
- Steps array round-trip with ActionType validation.
- Trigger=conditional requires condition_selector.
- TEST_RUN-scoped scripts are not exposed via REST (404 on direct id lookup).
- Pagination + scope isolation (page-scope vs website-scope).

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
from auto_a11y.models.page import Page
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


def _make_page(database: Database, website: Website) -> Page:
    website_id = website.id
    assert website_id is not None
    page = Page(
        website_id=website_id,
        url=f"https://example.test/page-{uuid.uuid4().hex[:8]}",
        title="A page",
    )
    page_id = database.create_page(page)
    saved = database.get_page(page_id)
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
def page(database: Database, website: Website) -> Page:
    return _make_page(database, website)


@pytest.fixture
def login(client: FlaskClient) -> Iterator[Any]:
    def _login(user: AppUser) -> None:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    yield _login


def _valid_create_body() -> dict[str, Any]:
    return {
        "name": "Login flow",
        "description": "Logs in using stored credentials",
        "trigger": "once_per_session",
        "steps": [
            {
                "action_type": "click",
                "description": "Click login button",
                "selector": "button#login",
                "timeout": 3000,
            },
            {
                "action_type": "type",
                "description": "Enter username",
                "selector": "input#username",
                "value": "alice",
            },
        ],
        "enabled": True,
    }


# ---------------------------------------------------------------------------
# Auth matrix
# ---------------------------------------------------------------------------


def test_anonymous_list_returns_401(
    client: FlaskClient, page: Page
) -> None:
    response = client.get(f"/api/v1/pages/{page.id}/scripts")
    assert response.status_code == 401
    assert response.headers["Content-Type"] == "application/problem+json"


def test_anonymous_create_returns_401(
    client: FlaskClient, page: Page
) -> None:
    response = client.post(
        f"/api/v1/pages/{page.id}/scripts", json=_valid_create_body()
    )
    assert response.status_code == 401


def test_authenticated_non_member_returns_403(
    client: FlaskClient,
    database: Database,
    page: Page,
    login: Any,
) -> None:
    user = _make_user(database, role=UserRole.AUDITOR, email="outsider@example.test")
    login(user)
    response = client.get(f"/api/v1/pages/{page.id}/scripts")
    assert response.status_code == 403


def test_unknown_page_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    login(admin_user)
    response = client.get("/api/v1/pages/507f1f77bcf86cd799999999/scripts")
    assert response.status_code == 404


def test_unknown_website_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    login(admin_user)
    response = client.get("/api/v1/websites/507f1f77bcf86cd799999999/scripts")
    assert response.status_code == 404


def test_unknown_script_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    login(admin_user)
    response = client.get("/api/v1/scripts/507f1f77bcf86cd799999999")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# CRUD round-trip — page-scoped
# ---------------------------------------------------------------------------


def test_create_page_script_then_get(
    client: FlaskClient,
    admin_user: AppUser,
    page: Page,
    login: Any,
) -> None:
    login(admin_user)
    create = client.post(
        f"/api/v1/pages/{page.id}/scripts", json=_valid_create_body()
    )
    assert create.status_code == 201, create.get_json()
    assert create.headers["Location"].startswith("/api/v1/scripts/")
    body = create.get_json()
    script_id = body["id"]
    assert body["scope"] == "page"
    assert body["page_id"] == page.id
    assert body["website_id"] is None
    assert body["enabled"] is True
    assert len(body["steps"]) == 2
    assert body["steps"][0]["action_type"] == "click"
    assert body["steps"][0]["step_number"] == 1

    get = client.get(f"/api/v1/scripts/{script_id}")
    assert get.status_code == 200
    assert get.get_json()["id"] == script_id


def test_create_website_script_succeeds(
    client: FlaskClient,
    admin_user: AppUser,
    website: Website,
    login: Any,
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/websites/{website.id}/scripts", json=_valid_create_body()
    )
    assert response.status_code == 201
    body = response.get_json()
    assert body["scope"] == "website"
    assert body["website_id"] == website.id
    assert body["page_id"] is None


def test_patch_toggles_enabled(
    client: FlaskClient,
    admin_user: AppUser,
    page: Page,
    login: Any,
) -> None:
    """The legacy POST /scripts/<id>/toggle becomes PATCH with {enabled: false}."""
    login(admin_user)
    create = client.post(
        f"/api/v1/pages/{page.id}/scripts", json=_valid_create_body()
    )
    script_id = create.get_json()["id"]

    disable = client.patch(
        f"/api/v1/scripts/{script_id}", json={"enabled": False}
    )
    assert disable.status_code == 200
    assert disable.get_json()["enabled"] is False
    # Other fields untouched.
    assert disable.get_json()["name"] == "Login flow"


def test_put_replaces_editable_fields_and_preserves_scope(
    client: FlaskClient,
    admin_user: AppUser,
    page: Page,
    login: Any,
) -> None:
    login(admin_user)
    create = client.post(
        f"/api/v1/pages/{page.id}/scripts", json=_valid_create_body()
    )
    script_id = create.get_json()["id"]

    empty_steps: list[Any] = []
    new_body = _valid_create_body() | {
        "name": "Replaced",
        "description": "All new",
        "steps": empty_steps,
    }
    response = client.put(f"/api/v1/scripts/{script_id}", json=new_body)
    assert response.status_code == 200
    body = response.get_json()
    assert body["name"] == "Replaced"
    assert body["description"] == "All new"
    assert body["steps"] == []
    # scope/page_id preserved by the server even if the body had ignored such fields.
    assert body["scope"] == "page"
    assert body["page_id"] == page.id


def test_delete_returns_204_then_get_returns_404(
    client: FlaskClient,
    admin_user: AppUser,
    page: Page,
    login: Any,
) -> None:
    login(admin_user)
    create = client.post(
        f"/api/v1/pages/{page.id}/scripts", json=_valid_create_body()
    )
    script_id = create.get_json()["id"]

    delete = client.delete(f"/api/v1/scripts/{script_id}")
    assert delete.status_code == 204

    get = client.get(f"/api/v1/scripts/{script_id}")
    assert get.status_code == 404


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_create_with_missing_name_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    page: Page,
    login: Any,
) -> None:
    login(admin_user)
    body = _valid_create_body()
    body.pop("name")
    response = client.post(f"/api/v1/pages/{page.id}/scripts", json=body)
    assert response.status_code == 400
    field_errors = {e["field"] for e in response.get_json()["errors"]}
    assert "name" in field_errors


def test_create_with_unknown_action_type_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    page: Page,
    login: Any,
) -> None:
    login(admin_user)
    body = _valid_create_body() | {
        "steps": [{"action_type": "telekinesis", "description": "X"}],
    }
    response = client.post(f"/api/v1/pages/{page.id}/scripts", json=body)
    assert response.status_code == 400
    field_errors = {e["field"] for e in response.get_json()["errors"]}
    assert "steps[0].action_type" in field_errors


def test_create_conditional_without_selector_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    page: Page,
    login: Any,
) -> None:
    login(admin_user)
    body = _valid_create_body() | {"trigger": "conditional"}
    response = client.post(f"/api/v1/pages/{page.id}/scripts", json=body)
    assert response.status_code == 400
    field_errors = {e["field"] for e in response.get_json()["errors"]}
    assert "condition_selector" in field_errors


def test_create_with_non_object_body_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    page: Page,
    login: Any,
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/pages/{page.id}/scripts", json=["not", "a", "dict"]
    )
    assert response.status_code == 400


def test_create_with_steps_not_array_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    page: Page,
    login: Any,
) -> None:
    login(admin_user)
    body = _valid_create_body() | {"steps": "click some button"}
    response = client.post(f"/api/v1/pages/{page.id}/scripts", json=body)
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Pagination + scope isolation
# ---------------------------------------------------------------------------


def test_list_paginates_with_cursor(
    client: FlaskClient,
    admin_user: AppUser,
    page: Page,
    login: Any,
) -> None:
    login(admin_user)
    for n in range(3):
        body = _valid_create_body() | {"name": f"Script {n}"}
        client.post(f"/api/v1/pages/{page.id}/scripts", json=body)

    page1 = client.get(f"/api/v1/pages/{page.id}/scripts?limit=2")
    assert page1.status_code == 200
    page1_body = page1.get_json()
    assert len(page1_body["items"]) == 2
    assert page1_body["next_cursor"] is not None

    cursor = page1_body["next_cursor"]
    page2 = client.get(
        f"/api/v1/pages/{page.id}/scripts?limit=2&cursor={cursor}"
    )
    assert page2.status_code == 200
    page2_body = page2.get_json()
    assert len(page2_body["items"]) == 1
    assert page2_body["next_cursor"] is None


def test_page_list_does_not_include_website_scoped_scripts(
    client: FlaskClient,
    admin_user: AppUser,
    page: Page,
    website: Website,
    login: Any,
) -> None:
    login(admin_user)
    client.post(
        f"/api/v1/pages/{page.id}/scripts",
        json=_valid_create_body() | {"name": "page-scope"},
    )
    client.post(
        f"/api/v1/websites/{website.id}/scripts",
        json=_valid_create_body() | {"name": "website-scope"},
    )

    page_listed = client.get(f"/api/v1/pages/{page.id}/scripts").get_json()
    site_listed = client.get(f"/api/v1/websites/{website.id}/scripts").get_json()
    assert {s["name"] for s in page_listed["items"]} == {"page-scope"}
    assert {s["name"] for s in site_listed["items"]} == {"website-scope"}


# ---------------------------------------------------------------------------
# TEST_RUN scope is not part of the REST surface.
# ---------------------------------------------------------------------------


def test_test_run_scope_script_returns_404_via_rest(
    client: FlaskClient,
    admin_user: AppUser,
    database: Database,
    login: Any,
) -> None:
    """TEST_RUN-scoped scripts are runtime-internal — REST 404s rather than
    leak them to clients that obtained their id."""
    login(admin_user)
    # Insert directly through the model (bypassing REST) so we have a
    # TEST_RUN-scoped script to attempt fetching.
    script = PageSetupScript(
        name="internal",
        description="runtime-only",
        scope=ScriptScope.TEST_RUN,
        test_run_id="run-abc",
    )
    script_id = database.create_page_setup_script(script)

    response = client.get(f"/api/v1/scripts/{script_id}")
    assert response.status_code == 404
