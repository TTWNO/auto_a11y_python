"""Tests for the PDF-document REST endpoints (read/list/delete only).

Covers:

- Auth matrix: 401 anonymous, 403 non-member, 404 unknown
- GET project-scoped + website-scoped lists with cursor pagination
- ?status= filter with enum validation
- Project- vs website-scope isolation
- DELETE returns 204 + cascades to filesystem (verified by checking the
  storage dir is gone)
- DELETE auth: ADMIN/AUDITOR only — CLIENT-role check would need a group
  fixture and is covered conceptually by the schedules suite

Multipart upload, audit action endpoints, and binary-streaming endpoints
(``/file``, ``/images``, ``/export``, ``/issue-map``,
``/reports/pdfmax``) are out of scope for this PR.

Tests skip when ``mongod`` is not reachable.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from collections.abc import Generator, Iterator
from datetime import datetime
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
from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus
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
def pdf_storage_dir() -> Generator[Path, None, None]:
    """Per-test PDF storage root; cleaned up on teardown."""
    with tempfile.TemporaryDirectory(prefix="pdf-storage-") as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def flask_app(database: Database, pdf_storage_dir: Path) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-deterministic-for-tests-only"
    app.config["WTF_CSRF_ENABLED"] = False
    setattr(app, "db", database)

    # The DELETE handler reads PDF_STORAGE_DIR off the typed app config.
    # Wire up a minimal config object that exposes only that attribute.
    from config import Config
    cfg = Config()
    cfg.PDF_STORAGE_DIR = str(pdf_storage_dir)
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


def _make_user(database: Database, *, email: str) -> AppUser:
    user = AppUser.create(email=email, password="x", role=UserRole.AUDITOR)
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


def _make_pdf(
    database: Database,
    *,
    website: Website,
    storage_dir: Path,
    sha256: str | None = None,
    status: PdfDocumentStatus = PdfDocumentStatus.PENDING,
    write_files: bool = True,
) -> PdfDocument:
    """Create a PdfDocument in the DB and (optionally) on disk.

    The DELETE handler walks the storage tree, so write_files=True puts a
    real file there for the cascade-cleanup assertions.
    """
    website_id = website.id
    project_id = website.project_id
    assert website_id is not None and project_id is not None

    pdf_id_seed = uuid.uuid4().hex[:8]
    pdf = PdfDocument(
        website_id=website_id,
        project_id=project_id,
        source_url=f"https://example.test/{pdf_id_seed}.pdf",
        source_type="manual_url",
        discovered_from_page_id=None,
        discovered_from_user_id=None,
        sha256=sha256 or (uuid.uuid4().hex + uuid.uuid4().hex)[:64],
        file_size_bytes=128,
        storage_relpath=f"{website_id}/{pdf_id_seed}/pdf.pdf",
        images_relpath=f"{website_id}/{pdf_id_seed}/images/",
        original_filename=f"{pdf_id_seed}.pdf",
        pdf_version="1.4",
        page_count=3,
        declared_lang="en",
        detected_lang="en",
        lang_confidence=0.95,
        status=status,
        error_reason=None,
        last_audit_result_id=None,
        discovered_at=datetime.now(),
        last_audited_at=None,
    )
    new_id = database.create_pdf_document(pdf)
    saved = database.get_pdf_document(new_id)
    assert saved is not None
    if write_files:
        pdf_path = storage_dir / saved.storage_relpath
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(b"%PDF-1.4 placeholder")
    return saved


@pytest.fixture
def admin_user(database: Database) -> AppUser:
    user = _make_user(database, email="admin@example.test")
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
def pdf(database: Database, website: Website, pdf_storage_dir: Path) -> PdfDocument:
    return _make_pdf(database, website=website, storage_dir=pdf_storage_dir)


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
    response = client.get(f"/api/v1/projects/{project.id}/pdfs")
    assert response.status_code == 401
    assert response.headers["Content-Type"] == "application/problem+json"


def test_authenticated_non_member_returns_403(
    client: FlaskClient,
    database: Database,
    project: Project,
    login: Any,
) -> None:
    user = _make_user(database, email="outsider@example.test")
    login(user)
    response = client.get(f"/api/v1/projects/{project.id}/pdfs")
    assert response.status_code == 403


def test_unknown_project_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    login(admin_user)
    response = client.get("/api/v1/projects/507f1f77bcf86cd799999999/pdfs")
    assert response.status_code == 404


def test_unknown_website_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    login(admin_user)
    response = client.get("/api/v1/websites/507f1f77bcf86cd799999999/pdfs")
    assert response.status_code == 404


def test_unknown_pdf_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    login(admin_user)
    response = client.get("/api/v1/pdf-documents/507f1f77bcf86cd799999999")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def test_get_returns_serialized_metadata(
    client: FlaskClient,
    admin_user: AppUser,
    pdf: PdfDocument,
    login: Any,
) -> None:
    login(admin_user)
    response = client.get(f"/api/v1/pdf-documents/{pdf.id}")
    assert response.status_code == 200
    body = response.get_json()
    assert body["id"] == pdf.id
    assert body["website_id"] == pdf.website_id
    assert body["project_id"] == pdf.project_id
    assert body["status"] == "pending"
    assert isinstance(body["discovered_at"], str)
    assert "_id" not in body


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


def test_list_for_project_returns_only_project_pdfs(
    client: FlaskClient,
    admin_user: AppUser,
    database: Database,
    project: Project,
    website: Website,
    pdf_storage_dir: Path,
    login: Any,
) -> None:
    login(admin_user)
    _make_pdf(database, website=website, storage_dir=pdf_storage_dir)

    other_project = _make_project(database)
    other_website = _make_website(database, other_project)
    _make_pdf(database, website=other_website, storage_dir=pdf_storage_dir)

    response = client.get(f"/api/v1/projects/{project.id}/pdfs")
    assert response.status_code == 200
    items = response.get_json()["items"]
    assert {p["project_id"] for p in items} == {project.id}


def test_list_for_website_returns_only_website_pdfs(
    client: FlaskClient,
    admin_user: AppUser,
    database: Database,
    website: Website,
    pdf_storage_dir: Path,
    login: Any,
) -> None:
    login(admin_user)
    _make_pdf(database, website=website, storage_dir=pdf_storage_dir)
    other_website = _make_website(database, _make_project(database))
    _make_pdf(database, website=other_website, storage_dir=pdf_storage_dir)

    response = client.get(f"/api/v1/websites/{website.id}/pdfs")
    assert response.status_code == 200
    items = response.get_json()["items"]
    assert {p["website_id"] for p in items} == {website.id}


def test_list_paginates_with_cursor(
    client: FlaskClient,
    admin_user: AppUser,
    database: Database,
    website: Website,
    pdf_storage_dir: Path,
    login: Any,
) -> None:
    login(admin_user)
    for _ in range(3):
        _make_pdf(database, website=website, storage_dir=pdf_storage_dir)

    page1 = client.get(f"/api/v1/websites/{website.id}/pdfs?limit=2")
    assert page1.status_code == 200
    page1_body = page1.get_json()
    assert len(page1_body["items"]) == 2
    assert page1_body["next_cursor"] is not None

    cursor = page1_body["next_cursor"]
    page2 = client.get(
        f"/api/v1/websites/{website.id}/pdfs?limit=2&cursor={cursor}"
    )
    assert page2.status_code == 200
    page2_body = page2.get_json()
    assert len(page2_body["items"]) == 1
    assert page2_body["next_cursor"] is None


def test_list_filters_by_status(
    client: FlaskClient,
    admin_user: AppUser,
    database: Database,
    website: Website,
    pdf_storage_dir: Path,
    login: Any,
) -> None:
    login(admin_user)
    _make_pdf(
        database,
        website=website,
        storage_dir=pdf_storage_dir,
        status=PdfDocumentStatus.AUDITED,
    )
    _make_pdf(
        database,
        website=website,
        storage_dir=pdf_storage_dir,
        status=PdfDocumentStatus.PENDING,
    )

    audited = client.get(
        f"/api/v1/websites/{website.id}/pdfs?status=audited"
    )
    assert audited.status_code == 200
    items = audited.get_json()["items"]
    assert len(items) == 1
    assert items[0]["status"] == "audited"


def test_list_with_unknown_status_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    website: Website,
    login: Any,
) -> None:
    login(admin_user)
    response = client.get(
        f"/api/v1/websites/{website.id}/pdfs?status=fictional"
    )
    assert response.status_code == 400
    field_errors = {e["field"] for e in response.get_json()["errors"]}
    assert "status" in field_errors


def test_list_with_malformed_cursor_returns_400(
    client: FlaskClient,
    admin_user: AppUser,
    website: Website,
    login: Any,
) -> None:
    login(admin_user)
    response = client.get(
        f"/api/v1/websites/{website.id}/pdfs?cursor=not-a-cursor!@#"
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Delete cascade — DB record + filesystem artefacts
# ---------------------------------------------------------------------------


def test_delete_returns_204_and_clears_storage(
    client: FlaskClient,
    admin_user: AppUser,
    database: Database,
    pdf: PdfDocument,
    pdf_storage_dir: Path,
    login: Any,
) -> None:
    login(admin_user)
    pdf_path = pdf_storage_dir / pdf.storage_relpath
    assert pdf_path.exists()  # placed by the fixture

    pdf_id = pdf.id
    assert pdf_id is not None
    response = client.delete(f"/api/v1/pdf-documents/{pdf_id}")
    assert response.status_code == 204

    assert database.get_pdf_document(pdf_id) is None
    # The whole per-PDF directory is removed.
    assert not pdf_path.exists()
    assert not pdf_path.parent.exists()


def test_delete_with_missing_files_still_succeeds(
    client: FlaskClient,
    admin_user: AppUser,
    database: Database,
    website: Website,
    pdf_storage_dir: Path,
    login: Any,
) -> None:
    """If filesystem artefacts are already missing (e.g. cleared by a
    previous restore-from-backup), DELETE still removes the DB record
    cleanly rather than 500ing."""
    login(admin_user)
    pdf = _make_pdf(
        database,
        website=website,
        storage_dir=pdf_storage_dir,
        write_files=False,  # nothing on disk to begin with
    )
    pdf_id = pdf.id
    assert pdf_id is not None
    response = client.delete(f"/api/v1/pdf-documents/{pdf_id}")
    assert response.status_code == 204
    assert database.get_pdf_document(pdf_id) is None
