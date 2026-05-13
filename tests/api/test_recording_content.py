"""Tests for the recording-content endpoints (§5.6 deferred slot):

- ``GET   /api/v1/recordings/<id>/content``
- ``PATCH /api/v1/recordings/<id>/content``

The recording model carries three optional per-language content
fields (key_takeaways, user_painpoints, user_assertions). These
endpoints expose them; the legacy upload form bundled them all
together at recording creation, which the REST surface deliberately
splits out so clients can fill the fields later without re-uploading
the whole recording.
"""
from __future__ import annotations

import io
import json
import os
import uuid
from collections.abc import Generator, Iterator
from datetime import datetime
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
    setattr(app, "app_config", Config())

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


def _make_recording(database: Database, project: Project) -> Recording:
    """Create a minimal Recording with the model's default factories."""
    assert project.id is not None
    rec = Recording(
        recording_id=f"REC-{uuid.uuid4().hex[:8]}",
        project_id=project.id,
        title="Test recording",
        auditor_name="Alice",
        auditor_role="UX",
        recording_type=RecordingType.AUDIT,
        recorded_date=datetime.now(),
    )
    rid = database.create_recording(rec)
    saved = database.get_recording(rid)
    assert saved is not None
    return saved


@pytest.fixture
def project(database: Database) -> Project:
    return _make_project(database)


@pytest.fixture
def recording(database: Database, project: Project) -> Recording:
    return _make_recording(database, project)


@pytest.fixture
def login(client: FlaskClient) -> Iterator[Any]:
    def _login(user: AppUser) -> None:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    yield _login


# ---------------------------------------------------------------------------
# GET /recordings/<id>/content
# ---------------------------------------------------------------------------


def test_get_content_unknown_id_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
) -> None:
    login(admin_user)
    response = client.get(
        "/api/v1/recordings/507f1f77bcf86cd799999999/content"
    )
    assert response.status_code == 404


def test_get_content_anonymous_returns_401(
    client: FlaskClient, recording: Recording,
) -> None:
    response = client.get(f"/api/v1/recordings/{recording.id}/content")
    assert response.status_code == 401


def test_get_content_empty_returns_empty_dicts(
    client: FlaskClient, admin_user: AppUser, login: Any,
    recording: Recording,
) -> None:
    """A freshly-created recording with no content returns empty per-field dicts."""
    login(admin_user)
    response = client.get(f"/api/v1/recordings/{recording.id}/content")
    assert response.status_code == 200
    body = response.get_json()
    assert body == {
        "key_takeaways": {},
        "user_painpoints": {},
        "user_assertions": {},
    }


# ---------------------------------------------------------------------------
# PATCH /recordings/<id>/content
# ---------------------------------------------------------------------------


def _key_takeaways_json() -> bytes:
    """Minimal valid key-takeaways JSON payload.

    The parser at :func:`auto_a11y.parsers.parse_key_takeaways_json`
    keys off ``takeaways`` (not ``key_takeaways``), so the shape
    matters.
    """
    return json.dumps({
        "recording": "REC",
        "takeaways": [
            {"number": 1, "topic": "Headings", "description": "Missing h1"},
            {"number": 2, "topic": "Forms", "description": "Unclear labels"},
        ],
    }).encode("utf-8")


def _user_painpoints_json() -> bytes:
    return json.dumps({
        "recording": "REC",
        "pain_points": [
            {
                "title": "Can't tab to submit",
                "user_quote": "I got stuck",
                "timecodes": [],
                "impact": "high",
            },
        ],
    }).encode("utf-8")


def _user_assertions_json() -> bytes:
    return json.dumps({
        "recording": "REC",
        "assertions": [
            {"text": "Site works with screen reader"},
        ],
    }).encode("utf-8")


def test_patch_content_unknown_id_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
) -> None:
    login(admin_user)
    response = client.patch(
        "/api/v1/recordings/507f1f77bcf86cd799999999/content",
        data={
            "key_takeaways_file_en": (
                io.BytesIO(_key_takeaways_json()), "kt.json",
            ),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 404


def test_patch_content_not_multipart_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
    recording: Recording,
) -> None:
    login(admin_user)
    response = client.patch(
        f"/api/v1/recordings/{recording.id}/content", json={}
    )
    assert response.status_code == 400


def test_patch_content_no_files_is_noop(
    client: FlaskClient, admin_user: AppUser, login: Any,
    recording: Recording,
) -> None:
    """An empty multipart body is a valid no-op — returns current content."""
    login(admin_user)
    response = client.patch(
        f"/api/v1/recordings/{recording.id}/content",
        data={},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert response.get_json() == {
        "key_takeaways": {},
        "user_painpoints": {},
        "user_assertions": {},
    }


def test_patch_content_invalid_extension_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
    recording: Recording,
) -> None:
    login(admin_user)
    response = client.patch(
        f"/api/v1/recordings/{recording.id}/content",
        data={
            "key_takeaways_file_en": (
                io.BytesIO(b"some text"), "kt.txt",
            ),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_patch_content_bad_json_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
    recording: Recording,
) -> None:
    login(admin_user)
    response = client.patch(
        f"/api/v1/recordings/{recording.id}/content",
        data={
            "key_takeaways_file_en": (
                io.BytesIO(b"not { valid"), "kt.json",
            ),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_patch_content_single_file_populates_slot(
    client: FlaskClient, admin_user: AppUser, login: Any,
    recording: Recording, database: Database,
) -> None:
    login(admin_user)
    response = client.patch(
        f"/api/v1/recordings/{recording.id}/content",
        data={
            "key_takeaways_file_en": (
                io.BytesIO(_key_takeaways_json()), "kt.json",
            ),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    body = response.get_json()
    assert "en" in body["key_takeaways"]
    assert len(body["key_takeaways"]["en"]) == 2
    assert body["user_painpoints"] == {}
    assert body["user_assertions"] == {}

    # Persisted to DB.
    assert recording.id is not None
    refreshed = database.get_recording(recording.id)
    assert refreshed is not None
    assert len(refreshed.key_takeaways["en"]) == 2


def test_patch_content_multiple_files_each_updates_own_slot(
    client: FlaskClient, admin_user: AppUser, login: Any,
    recording: Recording,
) -> None:
    """Patch with 3 files → each lands in its own (type, lang) slot."""
    login(admin_user)
    response = client.patch(
        f"/api/v1/recordings/{recording.id}/content",
        data={
            "key_takeaways_file_en": (
                io.BytesIO(_key_takeaways_json()), "kt.json",
            ),
            "user_painpoints_file_en": (
                io.BytesIO(_user_painpoints_json()), "pp.json",
            ),
            "user_assertions_file_fr": (
                io.BytesIO(_user_assertions_json()), "ua.json",
            ),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    body = response.get_json()
    assert "en" in body["key_takeaways"]
    assert "en" in body["user_painpoints"]
    assert "fr" in body["user_assertions"]
    # The non-targeted slot is untouched.
    assert "fr" not in body["key_takeaways"]
    assert "en" not in body["user_assertions"]


def test_patch_content_merge_preserves_other_languages(
    client: FlaskClient, admin_user: AppUser, login: Any,
    recording: Recording, database: Database,
) -> None:
    """A second PATCH for ``_fr`` keeps the previous ``_en`` content."""
    login(admin_user)

    # Seed English content.
    client.patch(
        f"/api/v1/recordings/{recording.id}/content",
        data={
            "key_takeaways_file_en": (
                io.BytesIO(_key_takeaways_json()), "kt_en.json",
            ),
        },
        content_type="multipart/form-data",
    )
    # Add French content in a second request.
    response = client.patch(
        f"/api/v1/recordings/{recording.id}/content",
        data={
            "key_takeaways_file_fr": (
                io.BytesIO(_key_takeaways_json()), "kt_fr.json",
            ),
        },
        content_type="multipart/form-data",
    )
    body = response.get_json()
    assert set(body["key_takeaways"]) == {"en", "fr"}


def test_patch_content_html_file_is_parsed(
    client: FlaskClient, admin_user: AppUser, login: Any,
    recording: Recording,
) -> None:
    """HTML key-takeaways files parse via the HTML parser."""
    # Minimal HTML the recording_content_parser accepts — an unordered
    # list of items. The parser is loose so a plain list works.
    html_body = (
        "<html><body>"
        "<ul>"
        "<li>Item one needs alt text</li>"
        "<li>Item two has bad contrast</li>"
        "</ul>"
        "</body></html>"
    ).encode("utf-8")
    login(admin_user)
    response = client.patch(
        f"/api/v1/recordings/{recording.id}/content",
        data={
            "key_takeaways_file_en": (
                io.BytesIO(html_body), "kt.html",
            ),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    body = response.get_json()
    assert "en" in body["key_takeaways"]


def test_patch_content_empty_filename_is_treated_as_absent(
    client: FlaskClient, admin_user: AppUser, login: Any,
    recording: Recording,
) -> None:
    """Browser parts with no file selected come through with an empty
    filename — those slots should be skipped, not 400."""
    login(admin_user)
    response = client.patch(
        f"/api/v1/recordings/{recording.id}/content",
        data={
            "key_takeaways_file_en": (io.BytesIO(b""), ""),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["key_takeaways"] == {}
