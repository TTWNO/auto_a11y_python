"""Tests for the multipart upload endpoints (§5.6 + §5.9):

- ``POST /api/v1/projects/<id>/pdfs``        (PDF upload OR URL fetch)
- ``POST /api/v1/recordings``                (Dictaphone JSON upload)

Both endpoints reach into the test pipeline (PdfRunner /
DictaphoneImporter) which depends on a real browser or a real
filesystem. We monkey-patch the relevant entry points so the routes
exercise their validation + persistence paths without spinning up
the real machinery.
"""
from __future__ import annotations

import io
import json
import os
import uuid
from collections.abc import Generator, Iterator
from pathlib import Path
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
def flask_app(database: Database, tmp_path: Path) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-deterministic-for-tests-only"
    app.config["WTF_CSRF_ENABLED"] = False
    setattr(app, "db", database)

    from config import Config
    cfg = Config()
    cfg.PDF_STORAGE_DIR = str(tmp_path)
    setattr(app, "app_config", cfg)
    setattr(app, "pdf_runner", _FakePdfRunner())

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


class _FakePdfRunner:
    """Minimal PdfRunner replacement.

    The route routes call to ``create_or_find_pdf_document`` (file
    path) and ``fetch_pdf_from_url`` (URL path); both are mocked to
    return a ``PdfDocument`` placeholder. The route never inspects
    runner state, only the document it returns.
    """

    def __init__(self) -> None:
        # Tests inject behaviour by assigning to these attributes.
        self.raise_for_create: Exception | None = None
        self.raise_for_fetch: Exception | None = None
        self.last_create_kwargs: dict[str, Any] = {}
        self.last_fetch_kwargs: dict[str, Any] = {}
        self.fetched_bytes = b"%PDF-1.7\n%fake fetched\n"
        self.dedup_hit = False

    async def create_or_find_pdf_document(
        self,
        pdf_bytes: bytes,
        **kwargs: Any,
    ) -> Any:
        from datetime import datetime, timedelta

        from auto_a11y.models.pdf_document import (
            PdfDocument, PdfDocumentStatus,
        )
        self.last_create_kwargs = {"pdf_bytes_len": len(pdf_bytes), **kwargs}
        if self.raise_for_create is not None:
            raise self.raise_for_create

        # Force "old" discovered_at when simulating a dedup hit so the
        # route's freshness heuristic flips to 200 instead of 201.
        discovered_at = (
            datetime.now() - timedelta(minutes=5)
            if self.dedup_hit else datetime.now()
        )
        doc = PdfDocument(
            website_id=kwargs["website_id"],
            project_id=kwargs["project_id"],
            source_url=kwargs.get("source_url"),
            source_type=kwargs["source_type"],
            discovered_from_page_id=kwargs.get("discovered_from_page_id"),
            discovered_from_user_id=kwargs.get("discovered_from_user_id"),
            sha256="a" * 64,
            file_size_bytes=len(pdf_bytes),
            storage_relpath=f"{kwargs['website_id']}/fakedoc/pdf.pdf",
            images_relpath=f"{kwargs['website_id']}/fakedoc/images/",
            original_filename=kwargs["original_filename"],
            pdf_version=None,
            page_count=None,
            declared_lang=None,
            detected_lang=None,
            lang_confidence=None,
            status=PdfDocumentStatus.PENDING,
            error_reason=None,
            last_audit_result_id=None,
            discovered_at=discovered_at,
            last_audited_at=None,
        )
        from bson import ObjectId
        doc.mongo_id = ObjectId()
        return doc

    async def fetch_pdf_from_url(
        self, url: str, *, website_user_id: str | None = None,
    ) -> bytes:
        self.last_fetch_kwargs = {
            "url": url, "website_user_id": website_user_id,
        }
        if self.raise_for_fetch is not None:
            raise self.raise_for_fetch
        return self.fetched_bytes


@pytest.fixture
def client(flask_app: Flask) -> FlaskClient:
    return flask_app.test_client()


@pytest.fixture
def pdf_runner(flask_app: Flask) -> _FakePdfRunner:
    runner = getattr(flask_app, "pdf_runner")
    assert isinstance(runner, _FakePdfRunner)
    return runner


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


# ---------------------------------------------------------------------------
# POST /projects/<id>/pdfs (multipart upload)
# ---------------------------------------------------------------------------


def test_pdf_upload_unknown_project_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/projects/507f1f77bcf86cd799999999/pdfs",
        data={"website_id": "anything", "pdf_file": (io.BytesIO(b"%PDF-1"), "x.pdf")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 404


def test_pdf_upload_anonymous_returns_401(
    client: FlaskClient, project: Project, website: Website,
) -> None:
    response = client.post(
        f"/api/v1/projects/{project.id}/pdfs",
        data={"website_id": website.id, "pdf_file": (io.BytesIO(b"%PDF-1"), "x.pdf")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 401


def test_pdf_upload_no_runner_returns_409(
    client: FlaskClient, admin_user: AppUser, login: Any,
    project: Project, website: Website, flask_app: Flask,
) -> None:
    setattr(flask_app, "pdf_runner", None)
    login(admin_user)
    response = client.post(
        f"/api/v1/projects/{project.id}/pdfs",
        data={"website_id": website.id, "pdf_file": (io.BytesIO(b"%PDF-1"), "x.pdf")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 409


def test_pdf_upload_missing_pdf_file_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
    project: Project, website: Website,
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/projects/{project.id}/pdfs",
        data={"website_id": website.id},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_pdf_upload_website_not_in_project_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project,
) -> None:
    other_project = _make_project(database)
    other_website = _make_website(database, other_project)
    login(admin_user)
    response = client.post(
        f"/api/v1/projects/{project.id}/pdfs",
        data={
            "website_id": other_website.id,
            "pdf_file": (io.BytesIO(b"%PDF-1"), "x.pdf"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 404


def test_pdf_upload_happy_path_returns_201(
    client: FlaskClient, admin_user: AppUser, login: Any,
    project: Project, website: Website, pdf_runner: _FakePdfRunner,
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/projects/{project.id}/pdfs",
        data={
            "website_id": website.id,
            "pdf_file": (io.BytesIO(b"%PDF-1.7 hello"), "report.pdf"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 201
    body = response.get_json()
    assert body["project_id"] == project.id
    assert body["website_id"] == website.id
    assert body["source_type"] == "uploaded"
    assert response.headers["Location"].startswith("/api/v1/pdf-documents/")
    assert pdf_runner.last_create_kwargs["original_filename"] == "report.pdf"


def test_pdf_upload_not_a_pdf_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
    project: Project, website: Website, pdf_runner: _FakePdfRunner,
) -> None:
    from auto_a11y.pdf.errors import NotAPdf
    pdf_runner.raise_for_create = NotAPdf("bad magic")
    login(admin_user)
    response = client.post(
        f"/api/v1/projects/{project.id}/pdfs",
        data={
            "website_id": website.id,
            "pdf_file": (io.BytesIO(b"not a pdf"), "x.pdf"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_pdf_upload_too_large_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
    project: Project, website: Website, pdf_runner: _FakePdfRunner,
) -> None:
    from auto_a11y.pdf.errors import PdfTooLarge
    pdf_runner.raise_for_create = PdfTooLarge(100_000_000, 50_000_000)
    login(admin_user)
    response = client.post(
        f"/api/v1/projects/{project.id}/pdfs",
        data={
            "website_id": website.id,
            "pdf_file": (io.BytesIO(b"%PDF-1.7 big"), "huge.pdf"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_pdf_upload_dedup_hit_returns_200(
    client: FlaskClient, admin_user: AppUser, login: Any,
    project: Project, website: Website, pdf_runner: _FakePdfRunner,
) -> None:
    """Same (website_id, sha256) hits dedup → 200, not 201."""
    pdf_runner.dedup_hit = True
    login(admin_user)
    response = client.post(
        f"/api/v1/projects/{project.id}/pdfs",
        data={
            "website_id": website.id,
            "pdf_file": (io.BytesIO(b"%PDF-1.7 dup"), "dup.pdf"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# POST /projects/<id>/pdfs (URL fetch)
# ---------------------------------------------------------------------------


def test_pdf_url_fetch_missing_url_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
    project: Project, website: Website,
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/projects/{project.id}/pdfs",
        json={"website_id": website.id},
    )
    assert response.status_code == 400


def test_pdf_url_fetch_happy_path_returns_201(
    client: FlaskClient, admin_user: AppUser, login: Any,
    project: Project, website: Website, pdf_runner: _FakePdfRunner,
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/projects/{project.id}/pdfs",
        json={
            "website_id": website.id,
            "source_url": "https://example.com/doc.pdf",
        },
    )
    assert response.status_code == 201
    body = response.get_json()
    assert body["source_type"] == "manual_url"
    assert body["source_url"] == "https://example.com/doc.pdf"
    assert pdf_runner.last_fetch_kwargs["url"] == "https://example.com/doc.pdf"


def test_pdf_url_fetch_failure_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
    project: Project, website: Website, pdf_runner: _FakePdfRunner,
) -> None:
    from auto_a11y.pdf.errors import FetchFailed
    pdf_runner.raise_for_fetch = FetchFailed(
        "https://broken.example/doc.pdf", "connection refused",
    )
    login(admin_user)
    response = client.post(
        f"/api/v1/projects/{project.id}/pdfs",
        json={
            "website_id": website.id,
            "source_url": "https://broken.example/doc.pdf",
        },
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# POST /recordings
# ---------------------------------------------------------------------------


def _dictaphone_json(recording_id: str = "REC-001") -> dict[str, Any]:
    """Minimal valid Dictaphone payload — one issue, all required fields."""
    return {
        "recording": recording_id,
        "issues": [
            {
                "title": "Heading missing",
                "impact": "major",
                "category": "headings",
                "description": "There is no h1 on the page",
            },
        ],
    }


def test_recordings_upload_anonymous_returns_401(
    client: FlaskClient, project: Project,
) -> None:
    response = client.post(
        "/api/v1/recordings",
        data={
            "project_id": project.id,
            "recording_json_en": (
                io.BytesIO(json.dumps(_dictaphone_json()).encode("utf-8")),
                "rec.json",
            ),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 401


def test_recordings_upload_unknown_project_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/recordings",
        data={
            "project_id": "507f1f77bcf86cd799999999",
            "recording_json_en": (
                io.BytesIO(json.dumps(_dictaphone_json()).encode("utf-8")),
                "rec.json",
            ),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 404


def test_recordings_upload_missing_json_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
    project: Project,
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/recordings",
        data={"project_id": project.id},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_recordings_upload_non_json_filename_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
    project: Project,
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/recordings",
        data={
            "project_id": project.id,
            "recording_json_en": (
                io.BytesIO(b"some text"),
                "rec.txt",
            ),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_recordings_upload_bad_json_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
    project: Project,
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/recordings",
        data={
            "project_id": project.id,
            "recording_json_en": (
                io.BytesIO(b"not { valid"),
                "rec.json",
            ),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_recordings_upload_invalid_recording_type_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
    project: Project,
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/recordings",
        data={
            "project_id": project.id,
            "recording_type": "made-up-type",
            "recording_json_en": (
                io.BytesIO(json.dumps(_dictaphone_json()).encode("utf-8")),
                "rec.json",
            ),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_recordings_upload_not_multipart_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
    project: Project,
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/recordings",
        json={"project_id": project.id},
    )
    assert response.status_code == 400


def test_recordings_upload_happy_path_returns_201(
    client: FlaskClient, admin_user: AppUser, login: Any,
    project: Project, database: Database,
) -> None:
    rec_id = f"REC-{uuid.uuid4().hex[:8]}"
    login(admin_user)
    response = client.post(
        "/api/v1/recordings",
        data={
            "project_id": project.id,
            "title": "Manual audit walkthrough",
            "auditor_name": "Alice",
            "recording_type": "audit",
            "recording_json_en": (
                io.BytesIO(json.dumps(_dictaphone_json(rec_id)).encode("utf-8")),
                "rec.json",
            ),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 201
    body = response.get_json()
    assert body["recording_id"] == rec_id
    assert body["project_id"] == project.id
    assert response.headers["Location"].startswith("/api/v1/recordings/")

    # Project's recording_ids was updated.
    refreshed = database.get_project(project.id) if project.id else None
    assert refreshed is not None
    assert body["id"] in refreshed.recording_ids


def test_recordings_upload_duplicate_returns_409(
    client: FlaskClient, admin_user: AppUser, login: Any,
    project: Project,
) -> None:
    """Two uploads with the same Dictaphone ``recording`` field → 409."""
    rec_id = f"REC-{uuid.uuid4().hex[:8]}"
    login(admin_user)
    payload = json.dumps(_dictaphone_json(rec_id)).encode("utf-8")
    response_a = client.post(
        "/api/v1/recordings",
        data={
            "project_id": project.id,
            "recording_json_en": (io.BytesIO(payload), "rec.json"),
        },
        content_type="multipart/form-data",
    )
    assert response_a.status_code == 201

    response_b = client.post(
        "/api/v1/recordings",
        data={
            "project_id": project.id,
            "recording_json_en": (io.BytesIO(payload), "rec.json"),
        },
        content_type="multipart/form-data",
    )
    assert response_b.status_code == 409


def test_recordings_upload_fr_mismatch_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
    project: Project,
) -> None:
    login(admin_user)
    payload_en = json.dumps(_dictaphone_json("REC-en")).encode("utf-8")
    payload_fr = json.dumps(_dictaphone_json("REC-fr")).encode("utf-8")
    response = client.post(
        "/api/v1/recordings",
        data={
            "project_id": project.id,
            "recording_json_en": (io.BytesIO(payload_en), "rec_en.json"),
            "recording_json_fr": (io.BytesIO(payload_fr), "rec_fr.json"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
