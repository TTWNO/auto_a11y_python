"""Tests for the recordings + recording-issues REST endpoints.

Endpoints exercised:

- GET    /api/v1/recordings (filtered by ?project_id=)
- GET    /api/v1/recordings/<id>
- PATCH  /api/v1/recordings/<id>
- DELETE /api/v1/recordings/<id>
- GET    /api/v1/recordings/<id>/issues
- GET    /api/v1/recording-issues/<id>
- PATCH  /api/v1/recording-issues/<id>

Covers auth matrix, metadata patching, status enum validation on issues,
the (recording.id ↔ recording.recording_id) split for issue lookups, and
cursor pagination. POST/multipart upload is deferred per the PR scope.

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
from auto_a11y.models.recording import Recording, RecordingType
from auto_a11y.models.recording_issue import RecordingIssue
from auto_a11y.models.test_result import ImpactLevel


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


def _make_recording(
    database: Database, project: Project, *, recording_id: str | None = None
) -> Recording:
    project_id = project.id
    assert project_id is not None
    recording = Recording(
        recording_id=recording_id or f"REC-{uuid.uuid4().hex[:6]}",
        title="Sample audit",
        description="A short test recording",
        recording_type=RecordingType.AUDIT,
        project_id=project_id,
        auditor_name="Alice",
        auditor_role="Screen Reader User",
    )
    rec_id = database.create_recording(recording)
    saved = database.get_recording(rec_id)
    assert saved is not None
    return saved


def _make_recording_issue(
    database: Database,
    recording: Recording,
    *,
    title: str = "Missing label",
    status: str = "open",
) -> RecordingIssue:
    issue = RecordingIssue(
        recording_id=recording.recording_id,
        title=title,
        what="Form field has no label",
        why="Screen reader users cannot identify the field",
        who="Screen reader users",
        remediation="Add a <label> element or aria-label",
        impact=ImpactLevel.MEDIUM,
        project_id=recording.project_id,
        status=status,
    )
    issue_id = database.create_recording_issue(issue)
    saved = database.get_recording_issue(issue_id)
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
def recording(database: Database, project: Project) -> Recording:
    return _make_recording(database, project)


@pytest.fixture
def issue(database: Database, recording: Recording) -> RecordingIssue:
    return _make_recording_issue(database, recording)


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


def test_anonymous_list_returns_401(
    client: FlaskClient, project: Project
) -> None:
    response = client.get(f"/api/v1/recordings?project_id={project.id}")
    assert response.status_code == 401
    assert response.headers["Content-Type"] == "application/problem+json"


def test_list_without_project_id_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    login(admin_user)
    response = client.get("/api/v1/recordings")
    assert response.status_code == 400
    field_errors = {e["field"] for e in response.get_json()["errors"]}
    assert "project_id" in field_errors


def test_authenticated_non_member_returns_403(
    client: FlaskClient,
    database: Database,
    project: Project,
    login: Any,
) -> None:
    user = _make_user(database, role=UserRole.AUDITOR, email="outsider@example.test")
    login(user)
    response = client.get(f"/api/v1/recordings?project_id={project.id}")
    assert response.status_code == 403


def test_unknown_project_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    login(admin_user)
    response = client.get(
        "/api/v1/recordings?project_id=507f1f77bcf86cd799999999"
    )
    assert response.status_code == 404


def test_unknown_recording_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    login(admin_user)
    response = client.get("/api/v1/recordings/507f1f77bcf86cd799999999")
    assert response.status_code == 404


def test_unknown_issue_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    login(admin_user)
    response = client.get("/api/v1/recording-issues/507f1f77bcf86cd799999999")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Read + filter
# ---------------------------------------------------------------------------


def test_get_recording_returns_serialized_resource(
    client: FlaskClient,
    admin_user: AppUser,
    recording: Recording,
    login: Any,
) -> None:
    login(admin_user)
    response = client.get(f"/api/v1/recordings/{recording.id}")
    assert response.status_code == 200
    body = response.get_json()
    assert body["id"] == recording.id
    assert body["recording_id"] == recording.recording_id
    assert body["title"] == "Sample audit"
    assert body["recording_type"] == "audit"


def test_list_filters_by_project_and_type(
    client: FlaskClient,
    admin_user: AppUser,
    database: Database,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    _make_recording(database, project)
    other_project = _make_project(database)
    _make_recording(database, other_project)

    listed = client.get(f"/api/v1/recordings?project_id={project.id}")
    assert listed.status_code == 200
    items = listed.get_json()["items"]
    # Should only see recordings for `project`, not other_project.
    assert {r["project_id"] for r in items} == {project.id}


def test_list_with_unknown_recording_type_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    response = client.get(
        f"/api/v1/recordings?project_id={project.id}&recording_type=fictional"
    )
    assert response.status_code == 400


def test_list_paginates_with_cursor(
    client: FlaskClient,
    admin_user: AppUser,
    database: Database,
    project: Project,
    login: Any,
) -> None:
    login(admin_user)
    for n in range(3):
        _make_recording(database, project, recording_id=f"PAG-{n}")

    page1 = client.get(
        f"/api/v1/recordings?project_id={project.id}&limit=2"
    )
    assert page1.status_code == 200
    page1_body = page1.get_json()
    assert len(page1_body["items"]) == 2
    assert page1_body["next_cursor"] is not None

    cursor = page1_body["next_cursor"]
    page2 = client.get(
        f"/api/v1/recordings?project_id={project.id}&limit=2&cursor={cursor}"
    )
    assert page2.status_code == 200
    page2_body = page2.get_json()
    assert len(page2_body["items"]) == 1
    assert page2_body["next_cursor"] is None


# ---------------------------------------------------------------------------
# PATCH recording
# ---------------------------------------------------------------------------


def test_patch_recording_updates_provided_fields(
    client: FlaskClient,
    admin_user: AppUser,
    recording: Recording,
    login: Any,
) -> None:
    login(admin_user)
    response = client.patch(
        f"/api/v1/recordings/{recording.id}",
        json={
            "title": "Renamed audit",
            "tags": ["high-priority", "demo"],
            "notes": "Reviewed 2026-05-07",
        },
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["title"] == "Renamed audit"
    assert body["tags"] == ["high-priority", "demo"]
    assert body["notes"] == "Reviewed 2026-05-07"
    # Untouched fields preserved.
    assert body["auditor_name"] == "Alice"


def test_patch_recording_with_empty_title_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    recording: Recording,
    login: Any,
) -> None:
    login(admin_user)
    response = client.patch(
        f"/api/v1/recordings/{recording.id}", json={"title": "  "}
    )
    assert response.status_code == 400


def test_patch_recording_with_non_object_body_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    recording: Recording,
    login: Any,
) -> None:
    login(admin_user)
    response = client.patch(
        f"/api/v1/recordings/{recording.id}", json=["nope"]
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# DELETE recording — cascades issues
# ---------------------------------------------------------------------------


def test_delete_recording_returns_204_and_removes_issues(
    client: FlaskClient,
    admin_user: AppUser,
    database: Database,
    recording: Recording,
    issue: RecordingIssue,
    login: Any,
) -> None:
    login(admin_user)
    response = client.delete(f"/api/v1/recordings/{recording.id}")
    assert response.status_code == 204

    assert database.get_recording(recording.id or "") is None
    assert issue.id is not None
    assert database.get_recording_issue(issue.id) is None


# ---------------------------------------------------------------------------
# Issues listing + lookup
# ---------------------------------------------------------------------------


def test_list_issues_returns_only_for_this_recording(
    client: FlaskClient,
    admin_user: AppUser,
    database: Database,
    project: Project,
    recording: Recording,
    login: Any,
) -> None:
    login(admin_user)
    _make_recording_issue(database, recording, title="One")
    _make_recording_issue(database, recording, title="Two")
    other_recording = _make_recording(database, project, recording_id="OTHER-1")
    _make_recording_issue(database, other_recording, title="Three")

    response = client.get(f"/api/v1/recordings/{recording.id}/issues")
    assert response.status_code == 200
    body = response.get_json()
    assert {i["title"] for i in body["items"]} == {"One", "Two"}


def test_get_issue_returns_serialized_resource(
    client: FlaskClient,
    admin_user: AppUser,
    issue: RecordingIssue,
    login: Any,
) -> None:
    login(admin_user)
    response = client.get(f"/api/v1/recording-issues/{issue.id}")
    assert response.status_code == 200
    body = response.get_json()
    assert body["id"] == issue.id
    assert body["status"] == "open"
    assert body["impact"] == "Medium"


# ---------------------------------------------------------------------------
# PATCH issue — status enum + free-form fields
# ---------------------------------------------------------------------------


def test_patch_issue_changes_status_and_assignee(
    client: FlaskClient,
    admin_user: AppUser,
    issue: RecordingIssue,
    login: Any,
) -> None:
    login(admin_user)
    response = client.patch(
        f"/api/v1/recording-issues/{issue.id}",
        json={
            "status": "in_progress",
            "assigned_to": "bob@example.test",
            "resolution_notes": "Investigating",
        },
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "in_progress"
    assert body["assigned_to"] == "bob@example.test"
    assert body["resolution_notes"] == "Investigating"


def test_patch_issue_with_unknown_status_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    issue: RecordingIssue,
    login: Any,
) -> None:
    login(admin_user)
    response = client.patch(
        f"/api/v1/recording-issues/{issue.id}", json={"status": "closed"}
    )
    assert response.status_code == 400
    field_errors = {e["field"] for e in response.get_json()["errors"]}
    assert "status" in field_errors
