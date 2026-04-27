"""Tests for pdf_documents collection CRUD."""
from __future__ import annotations

from collections.abc import Generator
from datetime import datetime
from typing import Any

import pytest
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from auto_a11y.core.database import Database
from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus
from auto_a11y.models.project import Project, ProjectStatus
from auto_a11y.models.website import Website
from config import Config


@pytest.fixture
def db() -> Generator[Database, None, None]:
    cfg = Config()
    # Probe Mongo before constructing Database, since Database.__init__
    # calls _create_indexes which requires a live server.
    probe: MongoClient[dict[str, Any]] = MongoClient(
        cfg.MONGODB_URI, serverSelectionTimeoutMS=2000
    )
    try:
        probe.admin.command('ping')
    except PyMongoError as exc:
        probe.close()
        pytest.skip(f"MongoDB not available: {exc}")
    finally:
        probe.close()
    database = Database(cfg.MONGODB_URI, cfg.DATABASE_NAME + "_test")
    database.db.pdf_documents.delete_many({})
    database.db.test_results.delete_many({})
    database.db.projects.delete_many({})
    database.db.websites.delete_many({})
    yield database
    database.db.pdf_documents.delete_many({})
    database.db.test_results.delete_many({})
    database.db.projects.delete_many({})
    database.db.websites.delete_many({})


def _make_doc(sha256: str = 'a' * 64, website_id: str = 'w1', project_id: str = 'p1') -> PdfDocument:
    return PdfDocument(
        website_id=website_id,
        project_id=project_id,
        source_url='https://example.com/doc.pdf',
        source_type='manual_url',
        discovered_from_page_id=None,
        discovered_from_user_id='u1',
        sha256=sha256,
        file_size_bytes=1024,
        storage_relpath='w1/xxx/pdf.pdf',
        images_relpath='w1/xxx/images/',
        original_filename='doc.pdf',
        pdf_version=None,
        page_count=None,
        declared_lang=None,
        detected_lang=None,
        lang_confidence=None,
        status=PdfDocumentStatus.PENDING,
        error_reason=None,
        last_audit_result_id=None,
        discovered_at=datetime.now(),
        last_audited_at=None,
    )


def test_create_and_get_pdf_document(db: Database) -> None:
    doc_id = db.create_pdf_document(_make_doc())
    fetched = db.get_pdf_document(doc_id)
    assert fetched is not None
    assert fetched.sha256 == 'a' * 64


def test_dedup_by_website_and_sha(db: Database) -> None:
    doc_id = db.create_pdf_document(_make_doc(sha256='d' * 64))
    found = db.find_pdf_document_by_sha256('w1', 'd' * 64)
    assert found is not None
    assert found.id == doc_id
    not_found = db.find_pdf_document_by_sha256('w2', 'd' * 64)
    assert not_found is None


def test_update_pdf_document(db: Database) -> None:
    doc = _make_doc(sha256='e' * 64)
    doc_id = db.create_pdf_document(doc)
    fetched = db.get_pdf_document(doc_id)
    assert fetched is not None
    fetched.status = PdfDocumentStatus.AUDITED
    fetched.page_count = 42
    db.update_pdf_document(fetched)
    reloaded = db.get_pdf_document(doc_id)
    assert reloaded is not None
    assert reloaded.status is PdfDocumentStatus.AUDITED
    assert reloaded.page_count == 42


def test_update_pdf_document_without_id_raises(db: Database) -> None:
    doc = _make_doc(sha256='c' * 64)
    # Fresh doc, never inserted; mongo_id is None.
    assert doc.mongo_id is None
    with pytest.raises(ValueError):
        db.update_pdf_document(doc)


def test_delete_pdf_document(db: Database) -> None:
    doc_id = db.create_pdf_document(_make_doc(sha256='f' * 64))
    assert db.delete_pdf_document(doc_id) is True
    assert db.get_pdf_document(doc_id) is None


def test_get_pdf_documents_by_website(db: Database) -> None:
    db.create_pdf_document(_make_doc(sha256='1' * 64))
    db.create_pdf_document(_make_doc(sha256='2' * 64))
    docs = db.get_pdf_documents(website_id='w1')
    assert len(docs) == 2


def test_delete_website_cascades_pdfs(db: Database) -> None:
    project = Project(name='Test Project', status=ProjectStatus.ACTIVE)
    project_id = db.create_project(project)
    website = Website(project_id=project_id, url='https://example.com/')
    website_id = db.create_website(website)

    doc = _make_doc(sha256='9' * 64, website_id=website_id, project_id=project_id)
    doc_id = db.create_pdf_document(doc)
    assert db.get_pdf_document(doc_id) is not None

    assert db.delete_website(website_id) is True
    assert db.get_pdf_document(doc_id) is None
