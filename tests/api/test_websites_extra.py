"""Tests for website-scoped action endpoints added alongside the
existing websites REST surface.

Covers:

- ``GET /api/v1/websites/<id>/documents`` — paginated document listing
  with the optional ``?is_internal=`` filter.
- ``DELETE /api/v1/websites/<id>/test-results`` — bulk clear of all
  test results for a website, returning the counts of what was reset.

Tests skip when ``mongod`` is not reachable.
"""
from __future__ import annotations

import os
import tempfile
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
from auto_a11y.models.document_reference import DocumentReference, DocumentType
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
    with tempfile.TemporaryDirectory(prefix="auto-a11y-test-pdfs-") as path:
        yield Path(path)


@pytest.fixture
def flask_app(database: Database, pdf_storage_dir: Path) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-deterministic-for-tests-only"
    app.config["WTF_CSRF_ENABLED"] = False
    setattr(app, "db", database)

    # The clear-test-results handler reads PDF_STORAGE_DIR off the typed
    # app config (it wipes the pdfMax disk cache for PDFs scoped to the
    # cleared website).
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
        url=f"https://example-{uuid.uuid4().hex[:8]}.test/",
        name="Test website",
    )
    website_id = database.create_website(website)
    saved = database.get_website(website_id)
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
def website(database: Database) -> Website:
    return _make_website(database, _make_project(database))


@pytest.fixture
def login(client: FlaskClient) -> Iterator[Any]:
    def _login(user: AppUser) -> None:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    yield _login


def _insert_document_reference(
    database: Database,
    *,
    website_id: str,
    url: str,
    is_internal: bool = True,
) -> DocumentReference:
    ref = DocumentReference(
        website_id=website_id,
        document_url=url,
        referring_page_url=f"{url}#page",
        mime_type=DocumentType.PDF.value,
        is_internal=is_internal,
        link_text="A PDF",
        file_extension="pdf",
    )
    inserted = database.document_references.insert_one(ref.to_dict())
    ref.mongo_id = inserted.inserted_id
    return ref


# ---------------------------------------------------------------------------
# GET /api/v1/websites/<id>/documents
# ---------------------------------------------------------------------------


def test_documents_list_anonymous_returns_401(
    client: FlaskClient, website: Website
) -> None:
    response = client.get(f"/api/v1/websites/{website.id}/documents")
    assert response.status_code == 401
    assert response.headers["Content-Type"] == "application/problem+json"


def test_documents_list_unknown_website_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    login(admin_user)
    response = client.get(
        "/api/v1/websites/507f1f77bcf86cd799999999/documents"
    )
    assert response.status_code == 404


def test_documents_list_returns_items(
    client: FlaskClient,
    database: Database,
    admin_user: AppUser,
    login: Any,
    website: Website,
) -> None:
    assert website.id is not None
    _insert_document_reference(
        database,
        website_id=website.id,
        url="https://example.test/a.pdf",
    )
    _insert_document_reference(
        database,
        website_id=website.id,
        url="https://other.test/b.pdf",
        is_internal=False,
    )
    login(admin_user)

    response = client.get(f"/api/v1/websites/{website.id}/documents")
    assert response.status_code == 200
    body = response.get_json()
    assert len(body["items"]) == 2
    urls = {item["document_url"] for item in body["items"]}
    assert urls == {"https://example.test/a.pdf", "https://other.test/b.pdf"}


def test_documents_list_filters_by_is_internal(
    client: FlaskClient,
    database: Database,
    admin_user: AppUser,
    login: Any,
    website: Website,
) -> None:
    assert website.id is not None
    _insert_document_reference(
        database,
        website_id=website.id,
        url="https://example.test/internal.pdf",
        is_internal=True,
    )
    _insert_document_reference(
        database,
        website_id=website.id,
        url="https://other.test/external.pdf",
        is_internal=False,
    )
    login(admin_user)

    only_internal = client.get(
        f"/api/v1/websites/{website.id}/documents?is_internal=true"
    )
    assert only_internal.status_code == 200
    only_body = only_internal.get_json()
    assert [d["document_url"] for d in only_body["items"]] == [
        "https://example.test/internal.pdf"
    ]

    only_external = client.get(
        f"/api/v1/websites/{website.id}/documents?is_internal=false"
    )
    assert only_external.status_code == 200
    external_body = only_external.get_json()
    assert [d["document_url"] for d in external_body["items"]] == [
        "https://other.test/external.pdf"
    ]


def test_documents_list_rejects_bad_is_internal_value(
    client: FlaskClient, admin_user: AppUser, login: Any, website: Website
) -> None:
    login(admin_user)
    response = client.get(
        f"/api/v1/websites/{website.id}/documents?is_internal=maybe"
    )
    assert response.status_code == 400
    body = response.get_json()
    assert any(e["field"] == "is_internal" for e in body["errors"])


def test_documents_list_paginates(
    client: FlaskClient,
    database: Database,
    admin_user: AppUser,
    login: Any,
    website: Website,
) -> None:
    assert website.id is not None
    for i in range(3):
        _insert_document_reference(
            database,
            website_id=website.id,
            url=f"https://example.test/doc-{i}.pdf",
        )
    login(admin_user)

    first = client.get(
        f"/api/v1/websites/{website.id}/documents?limit=1"
    )
    assert first.status_code == 200
    first_body = first.get_json()
    assert len(first_body["items"]) == 1
    assert first_body["next_cursor"] is not None

    cursor = first_body["next_cursor"]
    second = client.get(
        f"/api/v1/websites/{website.id}/documents?limit=1&cursor={cursor}"
    )
    assert second.status_code == 200
    second_body = second.get_json()
    assert len(second_body["items"]) == 1
    assert (
        second_body["items"][0]["id"] != first_body["items"][0]["id"]
    )


# ---------------------------------------------------------------------------
# DELETE /api/v1/websites/<id>/test-results
# ---------------------------------------------------------------------------


def test_clear_test_results_anonymous_returns_401(
    client: FlaskClient, website: Website
) -> None:
    response = client.delete(f"/api/v1/websites/{website.id}/test-results")
    assert response.status_code == 401


def test_clear_test_results_outsider_returns_403(
    client: FlaskClient,
    database: Database,
    website: Website,
    login: Any,
) -> None:
    outsider = _make_user(
        database, role=UserRole.AUDITOR, email="outsider@example.test"
    )
    login(outsider)
    response = client.delete(f"/api/v1/websites/{website.id}/test-results")
    assert response.status_code == 403


def test_clear_test_results_unknown_website_returns_404(
    client: FlaskClient, admin_user: AppUser, login: Any
) -> None:
    login(admin_user)
    response = client.delete(
        "/api/v1/websites/507f1f77bcf86cd799999999/test-results"
    )
    assert response.status_code == 404


def test_clear_test_results_returns_counts(
    client: FlaskClient, admin_user: AppUser, login: Any, website: Website
) -> None:
    """Even with no test results, the endpoint should return the
    zero-counts envelope so the caller can render its UI."""
    login(admin_user)
    response = client.delete(f"/api/v1/websites/{website.id}/test-results")
    assert response.status_code == 200
    body = response.get_json()
    assert body["website_id"] == website.id
    assert body["test_results_deleted"] == 0
    assert body["pages_reset"] == 0
    assert body["pdf_documents_reset"] == 0
