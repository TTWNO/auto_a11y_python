"""Tests for the PDF artefact-serving endpoints (§5.9 read cluster):

- ``GET /api/v1/pdf-documents/<id>/file``
- ``GET /api/v1/pdf-documents/<id>/images/<name>``
- ``GET /api/v1/pdf-documents/<id>/export?format=md|html``
- ``GET /api/v1/pdf-documents/<id>/issue-map``
- ``GET /api/v1/pdf-documents/<id>/reports/pdfmax``

Each test seeds the on-disk artefacts under a tmp_path PDF_STORAGE_DIR
so the routes can `send_file` real bytes without spinning the audit
pipeline.
"""
from __future__ import annotations

import os
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
from auto_a11y.core.job_manager import JobManager
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
    setattr(JobManager, "_instance", None)
    db.client.drop_database(db_name)
    db.close()


@pytest.fixture
def pdf_storage_root(tmp_path: Path) -> Path:
    """Per-test isolated PDF_STORAGE_DIR rooted at tmp_path."""
    return tmp_path


@pytest.fixture
def flask_app(database: Database, pdf_storage_root: Path) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-deterministic-for-tests-only"
    app.config["WTF_CSRF_ENABLED"] = False
    setattr(app, "db", database)

    from config import Config
    cfg = Config()
    cfg.PDF_STORAGE_DIR = str(pdf_storage_root)
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


def _make_pdf_with_files(
    database: Database,
    pdf_storage_root: Path,
    project: Project,
    website: Website,
    *,
    status: PdfDocumentStatus = PdfDocumentStatus.AUDITED,
    original_filename: str | None = "doc.pdf",
    pdf_bytes: bytes = b"%PDF-1.7 fake pdf bytes",
    image_files: dict[str, bytes] | None = None,
    issue_map_json: str | None = None,
    pdfmax_markdown: str | None = None,
) -> PdfDocument:
    """Build a PdfDocument backed by real files under
    ``pdf_storage_root``. Each per-PDF directory looks like:

        <storage_root>/<doc_dir>/document.pdf
        <storage_root>/<doc_dir>/images/page_001.png
        <storage_root>/<doc_dir>/pdfmax-report/<basename>_issue_map.json
        <storage_root>/<doc_dir>/pdfmax-report/<basename>_accessibility_report.md
    """
    assert project.id is not None
    assert website.id is not None
    doc_id = uuid.uuid4().hex[:8]
    storage_rel = f"projects/{project.id}/{doc_id}/document.pdf"
    images_rel = f"projects/{project.id}/{doc_id}/images/"
    doc_dir = pdf_storage_root / Path(storage_rel).parent
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / "document.pdf").write_bytes(pdf_bytes)

    images_dir = pdf_storage_root / Path(images_rel)
    images_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in (image_files or {}).items():
        (images_dir / name).write_bytes(payload)

    if issue_map_json is not None or pdfmax_markdown is not None:
        cache_dir = doc_dir / "pdfmax-report"
        cache_dir.mkdir(parents=True, exist_ok=True)
        base = "doc"
        if issue_map_json is not None:
            (cache_dir / f"{base}_issue_map.json").write_text(
                issue_map_json, encoding="utf-8",
            )
        if pdfmax_markdown is not None:
            (cache_dir / f"{base}_accessibility_report.md").write_text(
                pdfmax_markdown, encoding="utf-8",
            )

    pdf = PdfDocument(
        website_id=website.id,
        project_id=project.id,
        source_url=f"https://example.test/{doc_id}.pdf",
        source_type="manual_url",
        discovered_from_page_id=None,
        discovered_from_user_id=None,
        sha256="0" * 64,
        file_size_bytes=len(pdf_bytes),
        storage_relpath=storage_rel,
        images_relpath=images_rel,
        original_filename=original_filename,
        pdf_version="1.7",
        page_count=10,
        declared_lang=None,
        detected_lang="en",
        lang_confidence=0.9,
        status=status,
        error_reason=None,
        last_audit_result_id=None,
        discovered_at=datetime.now(),
        last_audited_at=datetime.now(),
    )
    pid = database.create_pdf_document(pdf)
    saved = database.get_pdf_document(pid)
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
# GET /pdf-documents/<id>/file
# ---------------------------------------------------------------------------


def test_file_unknown_id_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
) -> None:
    login(admin_user)
    response = client.get(
        "/api/v1/pdf-documents/507f1f77bcf86cd799999999/file"
    )
    assert response.status_code == 404


def test_file_anonymous_returns_401(
    client: FlaskClient, database: Database, pdf_storage_root: Path,
    project: Project, website: Website,
) -> None:
    pdf = _make_pdf_with_files(database, pdf_storage_root, project, website)
    response = client.get(f"/api/v1/pdf-documents/{pdf.id}/file")
    assert response.status_code == 401


def test_file_happy_path_returns_pdf_bytes(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, pdf_storage_root: Path,
    project: Project, website: Website,
) -> None:
    pdf = _make_pdf_with_files(
        database, pdf_storage_root, project, website,
        pdf_bytes=b"%PDF-1.7 hello",
    )
    login(admin_user)
    response = client.get(f"/api/v1/pdf-documents/{pdf.id}/file")
    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data == b"%PDF-1.7 hello"
    cd = response.headers.get("Content-Disposition", "")
    assert "inline" in cd
    assert "doc.pdf" in cd
    assert response.headers.get("X-Frame-Options") == "SAMEORIGIN"


def test_file_missing_on_disk_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, pdf_storage_root: Path,
    project: Project, website: Website,
) -> None:
    pdf = _make_pdf_with_files(database, pdf_storage_root, project, website)
    # Unlink the file but keep the DB record.
    (pdf_storage_root / Path(pdf.storage_relpath)).unlink()
    login(admin_user)
    response = client.get(f"/api/v1/pdf-documents/{pdf.id}/file")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /pdf-documents/<id>/images/<name>
# ---------------------------------------------------------------------------


def test_image_traversal_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, pdf_storage_root: Path,
    project: Project, website: Website,
) -> None:
    """``..`` in the URL is caught before we hit the filesystem."""
    pdf = _make_pdf_with_files(database, pdf_storage_root, project, website)
    login(admin_user)
    response = client.get(
        f"/api/v1/pdf-documents/{pdf.id}/images/..%2Fsecret"
    )
    # The server-side check rejects the resolved value `../secret`.
    assert response.status_code in (400, 404)


def test_image_missing_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, pdf_storage_root: Path,
    project: Project, website: Website,
) -> None:
    pdf = _make_pdf_with_files(database, pdf_storage_root, project, website)
    login(admin_user)
    response = client.get(
        f"/api/v1/pdf-documents/{pdf.id}/images/page_001.png"
    )
    assert response.status_code == 404


def test_image_happy_path_serves_bytes(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, pdf_storage_root: Path,
    project: Project, website: Website,
) -> None:
    pdf = _make_pdf_with_files(
        database, pdf_storage_root, project, website,
        image_files={"page_001.png": b"\x89PNG\r\n\x1a\nfake"},
    )
    login(admin_user)
    response = client.get(
        f"/api/v1/pdf-documents/{pdf.id}/images/page_001.png"
    )
    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert response.data == b"\x89PNG\r\n\x1a\nfake"


# ---------------------------------------------------------------------------
# GET /pdf-documents/<id>/export
# ---------------------------------------------------------------------------


def test_export_missing_format_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, pdf_storage_root: Path,
    project: Project, website: Website,
) -> None:
    pdf = _make_pdf_with_files(database, pdf_storage_root, project, website)
    login(admin_user)
    response = client.get(f"/api/v1/pdf-documents/{pdf.id}/export")
    assert response.status_code == 400


def test_export_invalid_format_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, pdf_storage_root: Path,
    project: Project, website: Website,
) -> None:
    pdf = _make_pdf_with_files(database, pdf_storage_root, project, website)
    login(admin_user)
    response = client.get(f"/api/v1/pdf-documents/{pdf.id}/export?format=pdf")
    assert response.status_code == 400


def test_export_md_returns_markdown(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, pdf_storage_root: Path,
    project: Project, website: Website,
) -> None:
    pdf = _make_pdf_with_files(database, pdf_storage_root, project, website)
    login(admin_user)
    response = client.get(f"/api/v1/pdf-documents/{pdf.id}/export?format=md")
    assert response.status_code == 200
    assert response.mimetype == "text/markdown"
    cd = response.headers.get("Content-Disposition", "")
    assert "doc_accessibility_report.md" in cd


def test_export_html_returns_html(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, pdf_storage_root: Path,
    project: Project, website: Website,
) -> None:
    pdf = _make_pdf_with_files(database, pdf_storage_root, project, website)
    login(admin_user)
    response = client.get(f"/api/v1/pdf-documents/{pdf.id}/export?format=html")
    assert response.status_code == 200
    assert response.mimetype == "text/html"


def test_export_unknown_locale_falls_back_to_en(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, pdf_storage_root: Path,
    project: Project, website: Website,
) -> None:
    """An unrecognised locale silently defaults to ``en`` — no 400."""
    pdf = _make_pdf_with_files(database, pdf_storage_root, project, website)
    login(admin_user)
    response = client.get(
        f"/api/v1/pdf-documents/{pdf.id}/export?format=md&locale=zh"
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# GET /pdf-documents/<id>/issue-map
# ---------------------------------------------------------------------------


def test_issue_map_not_audited_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, pdf_storage_root: Path,
    project: Project, website: Website,
) -> None:
    pdf = _make_pdf_with_files(
        database, pdf_storage_root, project, website,
        status=PdfDocumentStatus.PENDING,
        issue_map_json='{"items": []}',
    )
    login(admin_user)
    response = client.get(f"/api/v1/pdf-documents/{pdf.id}/issue-map")
    assert response.status_code == 404


def test_issue_map_no_cache_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, pdf_storage_root: Path,
    project: Project, website: Website,
) -> None:
    """Status AUDITED but no cache dir → 404."""
    pdf = _make_pdf_with_files(
        database, pdf_storage_root, project, website,
        status=PdfDocumentStatus.AUDITED,
    )
    login(admin_user)
    response = client.get(f"/api/v1/pdf-documents/{pdf.id}/issue-map")
    assert response.status_code == 404


def test_issue_map_happy_path_returns_json(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, pdf_storage_root: Path,
    project: Project, website: Website,
) -> None:
    pdf = _make_pdf_with_files(
        database, pdf_storage_root, project, website,
        status=PdfDocumentStatus.AUDITED,
        issue_map_json='{"items": [{"id": 1}]}',
    )
    login(admin_user)
    response = client.get(f"/api/v1/pdf-documents/{pdf.id}/issue-map")
    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert b'"items":' in response.data
    assert response.headers.get("Cache-Control") == "private, max-age=60"


# ---------------------------------------------------------------------------
# GET /pdf-documents/<id>/reports/pdfmax
# ---------------------------------------------------------------------------


def test_pdfmax_not_audited_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, pdf_storage_root: Path,
    project: Project, website: Website,
) -> None:
    pdf = _make_pdf_with_files(
        database, pdf_storage_root, project, website,
        status=PdfDocumentStatus.AUDIT_FAILED,
        pdfmax_markdown="# stale",
    )
    login(admin_user)
    response = client.get(
        f"/api/v1/pdf-documents/{pdf.id}/reports/pdfmax"
    )
    assert response.status_code == 404


def test_pdfmax_no_cache_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, pdf_storage_root: Path,
    project: Project, website: Website,
) -> None:
    pdf = _make_pdf_with_files(
        database, pdf_storage_root, project, website,
        status=PdfDocumentStatus.AUDITED,
    )
    login(admin_user)
    response = client.get(
        f"/api/v1/pdf-documents/{pdf.id}/reports/pdfmax"
    )
    assert response.status_code == 404


def test_pdfmax_happy_path_returns_markdown(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, pdf_storage_root: Path,
    project: Project, website: Website,
) -> None:
    pdf = _make_pdf_with_files(
        database, pdf_storage_root, project, website,
        status=PdfDocumentStatus.AUDITED,
        pdfmax_markdown="# pdfMax Report\n\nAll good.",
    )
    login(admin_user)
    response = client.get(
        f"/api/v1/pdf-documents/{pdf.id}/reports/pdfmax"
    )
    assert response.status_code == 200
    assert response.mimetype == "text/markdown"
    assert b"# pdfMax Report" in response.data
