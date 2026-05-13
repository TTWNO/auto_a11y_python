"""Tests for the PDF audit action endpoints (§5.9):

- ``POST /api/v1/pdf-documents/<id>/audits``
- ``GET  /api/v1/pdf-documents/<id>/audits/latest``
- ``POST /api/v1/pdf-documents/<id>/audits/latest/cancel``

``PdfAuditJob.start`` actually runs the audit on a background thread,
so we monkey-patch it to a no-op that just creates the JobManager
record — enough for the routes to see a real job_id without touching
Playwright/poppler.
"""
from __future__ import annotations

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
from auto_a11y.core.job_manager import JobManager, JobStatus, JobType
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


class _FakePdfRunner:
    """Stand-in for PdfRunner so the route doesn't 503 in tests.

    Only its identity matters — :class:`PdfAuditJob` accepts the
    instance but our monkey-patched ``.start`` never calls into it.
    """


@pytest.fixture
def flask_app(
    database: Database, monkeypatch: pytest.MonkeyPatch,
) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-deterministic-for-tests-only"
    app.config["WTF_CSRF_ENABLED"] = False
    setattr(app, "db", database)

    from config import Config
    cfg = Config()
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

    # Monkey-patch PdfAuditJob.start so the route can call it without
    # touching the real audit pipeline. The replacement creates the
    # JobManager record exactly like the real one would.
    from auto_a11y.core import pdf_audit_job as paj_module

    def fake_start(self: Any) -> str:
        pdf_id = getattr(self, "_pdf_document_id")
        jm = JobManager.get_instance(database)
        jm.create_job(
            job_id=getattr(self, "_job_id"),
            job_type=JobType.PDF_AUDIT,
            project_id=None,
            website_id=None,
            user_id=getattr(self, "_user_id"),
            metadata={
                "pdf_document_id": pdf_id,
                "wcag_level": getattr(self, "_wcag_level"),
                "locale": getattr(self, "_locale"),
                "run_ai": getattr(self, "_run_ai"),
            },
        )
        return cast_str(getattr(self, "_job_id"))

    monkeypatch.setattr(paj_module.PdfAuditJob, "start", fake_start)
    return app


def cast_str(value: Any) -> str:
    return str(value)


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


@pytest.fixture
def non_admin_user(database: Database) -> AppUser:
    return _make_user(
        database, role=UserRole.AUDITOR, email="auditor@example.test"
    )


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


def _make_pdf(
    database: Database,
    project: Project,
    website: Website,
    *,
    status: PdfDocumentStatus = PdfDocumentStatus.PENDING,
) -> PdfDocument:
    assert project.id is not None
    assert website.id is not None
    pdf = PdfDocument(
        website_id=website.id,
        project_id=project.id,
        source_url=f"https://example.test/{uuid.uuid4().hex[:6]}.pdf",
        source_type="manual_url",
        discovered_from_page_id=None,
        discovered_from_user_id=None,
        sha256="0" * 64,
        file_size_bytes=1024,
        storage_relpath=f"projects/{project.id}/{uuid.uuid4().hex[:8]}.pdf",
        images_relpath=f"projects/{project.id}/{uuid.uuid4().hex[:8]}/",
        original_filename="doc.pdf",
        pdf_version="1.7",
        page_count=10,
        declared_lang=None,
        detected_lang="en",
        lang_confidence=0.9,
        status=status,
        error_reason=None,
        last_audit_result_id=None,
        discovered_at=datetime.now(),
        last_audited_at=None,
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
def pdf(database: Database, project: Project, website: Website) -> PdfDocument:
    return _make_pdf(database, project, website)


@pytest.fixture
def login(client: FlaskClient) -> Iterator[Any]:
    def _login(user: AppUser) -> None:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    yield _login


# ---------------------------------------------------------------------------
# POST /pdf-documents/<id>/audits  (start)
# ---------------------------------------------------------------------------


def test_start_audit_unknown_id_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/pdf-documents/507f1f77bcf86cd799999999/audits"
    )
    assert response.status_code == 404


def test_start_audit_anonymous_returns_401(
    client: FlaskClient, pdf: PdfDocument,
) -> None:
    response = client.post(f"/api/v1/pdf-documents/{pdf.id}/audits")
    assert response.status_code == 401


def test_start_audit_when_already_running_returns_409(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project, website: Website,
) -> None:
    pdf_doc = _make_pdf(
        database, project, website, status=PdfDocumentStatus.AUDITING,
    )
    login(admin_user)
    response = client.post(f"/api/v1/pdf-documents/{pdf_doc.id}/audits")
    assert response.status_code == 409


def test_start_audit_no_runner_returns_409(
    client: FlaskClient, admin_user: AppUser, login: Any,
    pdf: PdfDocument, flask_app: Flask,
) -> None:
    """``BROWSER_MODE`` doesn't gate this — only ``pdf_runner is None`` does."""
    setattr(flask_app, "pdf_runner", None)
    login(admin_user)
    response = client.post(f"/api/v1/pdf-documents/{pdf.id}/audits")
    assert response.status_code == 409


def test_start_audit_happy_path_returns_202(
    client: FlaskClient, admin_user: AppUser, login: Any,
    pdf: PdfDocument, database: Database,
) -> None:
    login(admin_user)
    response = client.post(f"/api/v1/pdf-documents/{pdf.id}/audits")
    assert response.status_code == 202
    body = response.get_json()
    assert body["pdf_document_id"] == pdf.id
    assert body["wcag_level"] == "AA"
    assert body["locale"] == "en"
    assert body["run_ai"] is False
    assert body["status"] == "queued"
    assert body["job_id"].startswith(f"pdf_audit_{pdf.id}_")

    # The job record exists in JobManager.
    jm = JobManager.get_instance(database)
    record = jm.get_job(body["job_id"])
    assert record is not None
    assert record["job_type"] == JobType.PDF_AUDIT.value


def test_start_audit_invalid_wcag_level_returns_400(
    client: FlaskClient, admin_user: AppUser, login: Any, pdf: PdfDocument,
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/pdf-documents/{pdf.id}/audits",
        json={"wcag_level": "AAAA"},
    )
    assert response.status_code == 400


def test_start_audit_honours_run_ai_and_locale(
    client: FlaskClient, admin_user: AppUser, login: Any, pdf: PdfDocument,
) -> None:
    login(admin_user)
    response = client.post(
        f"/api/v1/pdf-documents/{pdf.id}/audits",
        json={"run_ai": True, "locale": "fr", "wcag_level": "AAA"},
    )
    body = response.get_json()
    assert body["run_ai"] is True
    assert body["locale"] == "fr"
    assert body["wcag_level"] == "AAA"


# ---------------------------------------------------------------------------
# GET /pdf-documents/<id>/audits/latest
# ---------------------------------------------------------------------------


def test_latest_audit_unknown_id_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
) -> None:
    login(admin_user)
    response = client.get(
        "/api/v1/pdf-documents/507f1f77bcf86cd799999999/audits/latest"
    )
    assert response.status_code == 404


def test_latest_audit_anonymous_returns_401(
    client: FlaskClient, pdf: PdfDocument,
) -> None:
    response = client.get(f"/api/v1/pdf-documents/{pdf.id}/audits/latest")
    assert response.status_code == 401


def test_latest_audit_no_jobs_returns_null_job(
    client: FlaskClient, admin_user: AppUser, login: Any, pdf: PdfDocument,
) -> None:
    """Document with no audit history → job=null but 200."""
    login(admin_user)
    response = client.get(f"/api/v1/pdf-documents/{pdf.id}/audits/latest")
    assert response.status_code == 200
    body = response.get_json()
    assert body["pdf_document_id"] == pdf.id
    assert body["doc_status"] == "pending"
    assert body["job"] is None


def test_latest_audit_prefers_active_over_completed(
    client: FlaskClient, admin_user: AppUser, login: Any,
    pdf: PdfDocument, database: Database,
) -> None:
    """If both an active and a completed run exist, the active wins."""
    jm = JobManager.get_instance(database)
    # Older completed job
    jm.create_job(
        job_id="pdf_audit_old", job_type=JobType.PDF_AUDIT,
        metadata={"pdf_document_id": pdf.id, "wcag_level": "AA"},
    )
    jm.update_job_status(
        "pdf_audit_old", JobStatus.COMPLETED, result={"ok": True},
    )
    # Newer running job
    jm.create_job(
        job_id="pdf_audit_running", job_type=JobType.PDF_AUDIT,
        metadata={"pdf_document_id": pdf.id, "wcag_level": "AA"},
    )
    jm.update_job_status("pdf_audit_running", JobStatus.RUNNING)

    login(admin_user)
    response = client.get(f"/api/v1/pdf-documents/{pdf.id}/audits/latest")
    body = response.get_json()
    assert body["job"]["job_id"] == "pdf_audit_running"
    assert body["job"]["status"] == "running"


def test_latest_audit_falls_back_to_most_recent_when_no_active(
    client: FlaskClient, admin_user: AppUser, login: Any,
    pdf: PdfDocument, database: Database,
) -> None:
    """No live job — surface the most recent terminal record."""
    jm = JobManager.get_instance(database)
    jm.create_job(
        job_id="pdf_audit_done", job_type=JobType.PDF_AUDIT,
        metadata={"pdf_document_id": pdf.id, "wcag_level": "AA"},
    )
    jm.update_job_status(
        "pdf_audit_done", JobStatus.COMPLETED, result={"ok": True},
    )

    login(admin_user)
    response = client.get(f"/api/v1/pdf-documents/{pdf.id}/audits/latest")
    body = response.get_json()
    assert body["job"]["job_id"] == "pdf_audit_done"
    assert body["job"]["status"] == "completed"


# ---------------------------------------------------------------------------
# POST /pdf-documents/<id>/audits/latest/cancel
# ---------------------------------------------------------------------------


def test_cancel_audit_unknown_id_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any,
) -> None:
    login(admin_user)
    response = client.post(
        "/api/v1/pdf-documents/507f1f77bcf86cd799999999/audits/latest/cancel"
    )
    assert response.status_code == 404


def test_cancel_audit_anonymous_returns_401(
    client: FlaskClient, pdf: PdfDocument,
) -> None:
    response = client.post(
        f"/api/v1/pdf-documents/{pdf.id}/audits/latest/cancel"
    )
    assert response.status_code == 401


def test_cancel_audit_nothing_to_cancel_returns_409(
    client: FlaskClient, admin_user: AppUser, login: Any, pdf: PdfDocument,
) -> None:
    """No active job + doc not stuck in AUDITING → 409."""
    login(admin_user)
    response = client.post(
        f"/api/v1/pdf-documents/{pdf.id}/audits/latest/cancel"
    )
    assert response.status_code == 409


def test_cancel_audit_with_active_job_flips_to_audit_failed(
    client: FlaskClient, admin_user: AppUser, login: Any,
    pdf: PdfDocument, database: Database,
) -> None:
    jm = JobManager.get_instance(database)
    jm.create_job(
        job_id="pdf_audit_live", job_type=JobType.PDF_AUDIT,
        metadata={"pdf_document_id": pdf.id, "wcag_level": "AA"},
    )
    jm.update_job_status("pdf_audit_live", JobStatus.RUNNING)

    login(admin_user)
    response = client.post(
        f"/api/v1/pdf-documents/{pdf.id}/audits/latest/cancel"
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["cancellation_requested_count"] == 1
    assert body["doc_status"] == "audit_failed"

    refreshed = database.get_pdf_document(pdf.id) if pdf.id else None
    assert refreshed is not None
    assert refreshed.status == PdfDocumentStatus.AUDIT_FAILED
    assert refreshed.error_reason == "Audit cancelled by user"


def test_cancel_audit_unblocks_stuck_auditing_doc(
    client: FlaskClient, admin_user: AppUser, login: Any,
    database: Database, project: Project, website: Website,
) -> None:
    """Document stuck in AUDITING with no live job → still allowed.

    The legacy escape hatch from a crashed worker — no jobs to cancel,
    but the doc needs reset so a re-audit becomes possible.
    """
    pdf_doc = _make_pdf(
        database, project, website, status=PdfDocumentStatus.AUDITING,
    )

    login(admin_user)
    response = client.post(
        f"/api/v1/pdf-documents/{pdf_doc.id}/audits/latest/cancel"
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["cancellation_requested_count"] == 0
    assert body["doc_status"] == "audit_failed"


def test_cancel_audit_non_admin_returns_403(
    client: FlaskClient, non_admin_user: AppUser, login: Any,
    pdf: PdfDocument,
) -> None:
    """ADMIN/AUDITOR required — auditor with no project membership → 403."""
    login(non_admin_user)
    response = client.post(
        f"/api/v1/pdf-documents/{pdf.id}/audits/latest/cancel"
    )
    assert response.status_code == 403
