"""Route-handler tests for :mod:`auto_a11y.web.routes.pdf` (Phase 9.3).

Templates referenced by the routes (``pdf/list.html``, ``pdf/add.html``,
``pdf/detail.html``) land in Phase 9.4. Until then, GET routes that
render a template are tested against a tmp template folder containing
stub templates so the handler returns 200 — verifying that the route is
wired up, the DB hooks fire, and the view-model transformations don't
crash.

The :class:`Database` and :class:`PdfRunner` are mocked. The PDF
storage layer is exercised against ``tmp_path`` because it's all
filesystem operations and cheaper than mocking it.

Authentication: every PDF route is protected by ``@login_required``
plus either ``@project_role_required`` (for project/website-keyed
routes) or the local :func:`_require_pdf_role` helper (for PDF-keyed
routes). The session-scoped ``app`` fixture installs a Flask-Login
``request_loader`` that authenticates each request as a synthetic
``is_superadmin=True`` user — that lets the role check short-circuit
without making the test handle group/permission setup.
"""
from __future__ import annotations

import io
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId
from flask import Blueprint, Flask
from flask.testing import FlaskClient
from flask_login import LoginManager

# ``auto_a11y.core`` must be imported before any module under
# ``auto_a11y.testing`` (see ``test_pdf_runner.py`` for the rationale).
import auto_a11y.core as _core_preload
del _core_preload

from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus
from auto_a11y.pdf.errors import (
    NotAPdf,
    PdfTooLarge,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_TINY_PDF_BYTES = b"%PDF-1.4\n%fake\n%%EOF\n"


def _make_doc(
    *,
    status: PdfDocumentStatus = PdfDocumentStatus.AUDITED,
    project_id: str = "p-1",
    website_id: str = "w-1",
) -> PdfDocument:
    oid = ObjectId()
    doc = PdfDocument(
        website_id=website_id,
        project_id=project_id,
        source_url=None,
        source_type="uploaded",
        discovered_from_page_id=None,
        discovered_from_user_id=None,
        sha256="a" * 64,
        file_size_bytes=len(_TINY_PDF_BYTES),
        storage_relpath=f"{website_id}/{oid}/pdf.pdf",
        images_relpath=f"{website_id}/{oid}/images/",
        original_filename="doc.pdf",
        pdf_version=None,
        page_count=None,
        declared_lang=None,
        detected_lang=None,
        lang_confidence=None,
        status=status,
        error_reason=None,
        last_audit_result_id=None,
        discovered_at=datetime.now(),
        last_audited_at=None,
    )
    doc.mongo_id = oid
    return doc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope='session')
def session_template_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Tmp template folder with stub templates for the PDF routes.

    Session-scoped so we don't pay the I/O cost per test.
    """
    base = tmp_path_factory.mktemp("pdf_route_templates")
    pdf_dir = base / "pdf"
    pdf_dir.mkdir()
    # Stub bodies — the production templates land in Phase 9.4.
    (pdf_dir / "list.html").write_text("<html><body>list</body></html>")
    (pdf_dir / "add.html").write_text("<html><body>add</body></html>")
    (pdf_dir / "detail.html").write_text("<html><body>detail</body></html>")
    return base


@pytest.fixture(scope='session')
def session_storage_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Session-scoped PDF storage root used by the file/image streaming tests."""
    return tmp_path_factory.mktemp("pdf_storage")


@pytest.fixture(scope='session')
def app(
    session_template_dir: Path,
    session_storage_dir: Path,
) -> Flask:
    """Flask app, Fluent initialised, blueprints registered.

    Session-scoped so ``init_fluent`` (loads 500+ ``.ftl`` files) runs once.
    Mocks live on ``app.db`` / ``app.pdf_runner`` and are reset per test by
    the function-scoped ``mock_db`` / ``mock_runner`` fixtures below.
    """
    app = Flask(
        __name__,
        template_folder=str(session_template_dir),
    )
    app.testing = True
    app.secret_key = 'test'

    # Minimal config object exposing the attributes the routes touch.
    cfg = MagicMock()
    cfg.PDF_STORAGE_DIR = str(session_storage_dir)
    setattr(app, 'app_config', cfg)

    # Placeholder attrs — function-scoped fixtures replace these per test.
    setattr(app, 'db', MagicMock())
    setattr(app, 'pdf_runner', AsyncMock())

    # Auto-authenticate every request as a superadmin so the role
    # decorators on the PDF routes pass without group setup.
    login_manager = LoginManager()
    login_manager.init_app(app)

    @login_manager.request_loader
    def _load_user_from_request(_req: Any) -> Any:
        user = MagicMock()
        user.is_authenticated = True
        user.is_active = True
        user.is_anonymous = False
        user.is_superadmin = True
        user.id = "test-user"
        user.get_id = lambda: "test-user"
        return user

    # Reference the loader to silence reportUnusedFunction.
    _ = _load_user_from_request

    # Stub the auth.login endpoint that login_required redirects to
    # when a request is unauthenticated — not strictly reachable in
    # these tests (request_loader always succeeds) but url_for needs
    # the endpoint to resolve from inside flask-login internals.
    auth_stub = Blueprint('auth', __name__)

    def _login_stub() -> str:
        return ''

    auth_stub.add_url_rule(
        '/login', endpoint='login', view_func=_login_stub
    )
    app.register_blueprint(auth_stub)

    # Initialise Fluent so ``ftl(...)`` resolves messages.
    from auto_a11y.web.fluent import init_fluent
    init_fluent(app)

    # Stub the cross-blueprint endpoints the PDF routes link to via
    # url_for. The real blueprints land alongside the rest of the app;
    # the test fixture only needs the endpoints to *exist* so url_for
    # can resolve them.
    def _list_projects_stub() -> str:
        return ''

    def _view_project_stub(project_id: str) -> str:
        _ = project_id
        return ''

    def _view_website_stub(website_id: str) -> str:
        _ = website_id
        return ''

    projects_stub = Blueprint('projects', __name__)
    projects_stub.add_url_rule(
        '/projects/', endpoint='list_projects', view_func=_list_projects_stub,
    )
    projects_stub.add_url_rule(
        '/projects/<project_id>',
        endpoint='view_project',
        view_func=_view_project_stub,
    )
    websites_stub = Blueprint('websites', __name__)
    websites_stub.add_url_rule(
        '/websites/<website_id>',
        endpoint='view_website',
        view_func=_view_website_stub,
    )
    app.register_blueprint(projects_stub)
    app.register_blueprint(websites_stub)

    from auto_a11y.web.routes.pdf import pdf_bp
    app.register_blueprint(pdf_bp, url_prefix='')

    return app


@pytest.fixture
def mock_db(app: Flask) -> Iterator[MagicMock]:
    """Per-test fresh :class:`Database` mock attached to ``app.db``."""
    db = MagicMock()
    db.get_project.return_value = MagicMock(id="p-1", name="Project One")
    db.get_website.return_value = MagicMock(
        id="w-1", project_id="p-1", url="https://example.org", name="Site"
    )
    db.get_websites.return_value = []
    db.get_pdf_documents.return_value = []
    db.get_pdf_document.return_value = None
    db.get_test_result.return_value = None
    setattr(app, 'db', db)
    yield db


@pytest.fixture
def mock_runner(app: Flask) -> Iterator[AsyncMock]:
    """Per-test fresh :class:`PdfRunner` mock attached to ``app.pdf_runner``."""
    runner = AsyncMock()
    runner.create_or_find_pdf_document = AsyncMock()
    runner.audit_pdf_document = AsyncMock()
    runner.fetch_pdf_from_url = AsyncMock()
    setattr(app, 'pdf_runner', runner)
    yield runner


@pytest.fixture
def client(app: Flask, mock_db: MagicMock, mock_runner: AsyncMock) -> FlaskClient:
    # ``mock_db`` and ``mock_runner`` are listed as deps so they install
    # before the client is used.
    _ = mock_db, mock_runner
    return app.test_client()


# ---------------------------------------------------------------------------
# GET /projects/<id>/pdfs and /websites/<id>/pdfs
# ---------------------------------------------------------------------------


def test_list_for_project_renders_with_no_pdfs(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    """Empty pdfs list still renders successfully."""
    mock_db.get_pdf_documents.return_value = []
    resp = client.get('/projects/p-1/pdfs')
    assert resp.status_code == 200
    mock_db.get_pdf_documents.assert_called_with(project_id='p-1', limit=500)


def test_list_for_project_redirects_when_project_missing(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    mock_db.get_project.return_value = None
    resp = client.get('/projects/p-bogus/pdfs')
    assert resp.status_code == 302
    assert '/projects' in resp.headers['Location']


def test_list_for_project_filters_by_status(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    """The ?status= query string narrows the list."""
    audited = _make_doc(status=PdfDocumentStatus.AUDITED)
    pending = _make_doc(status=PdfDocumentStatus.PENDING)
    mock_db.get_pdf_documents.return_value = [audited, pending]
    resp = client.get('/projects/p-1/pdfs?status=audited')
    assert resp.status_code == 200


def test_list_for_website_redirects_when_website_missing(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    mock_db.get_website.return_value = None
    resp = client.get('/websites/w-bogus/pdfs')
    assert resp.status_code == 302


def test_list_for_website_renders(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    resp = client.get('/websites/w-1/pdfs')
    assert resp.status_code == 200
    mock_db.get_pdf_documents.assert_called_with(website_id='w-1', limit=500)


# ---------------------------------------------------------------------------
# GET /projects/<id>/pdfs/add
# ---------------------------------------------------------------------------


def test_add_form_renders_with_websites(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    mock_db.get_websites.return_value = [
        MagicMock(id="w-1", url="https://x", name="X"),
    ]
    resp = client.get('/projects/p-1/pdfs/add')
    assert resp.status_code == 200


def test_add_form_redirects_when_project_missing(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    mock_db.get_project.return_value = None
    resp = client.get('/projects/p-bogus/pdfs/add')
    assert resp.status_code == 302


# ---------------------------------------------------------------------------
# POST /projects/<id>/pdfs (create)
# ---------------------------------------------------------------------------


def test_create_uploaded_pdf_invokes_runner_and_redirects_to_detail(
    client: FlaskClient, mock_runner: AsyncMock
) -> None:
    """File upload path: runner is awaited, success flash is set, redirects to detail."""
    created = _make_doc()
    mock_runner.create_or_find_pdf_document.return_value = created

    resp = client.post(
        '/projects/p-1/pdfs',
        data={
            'website_id': 'w-1',
            'pdf_file': (io.BytesIO(_TINY_PDF_BYTES), 'test.pdf'),
        },
        content_type='multipart/form-data',
    )
    assert resp.status_code == 302
    assert f'/pdfs/{created.id}' in resp.headers['Location']
    mock_runner.create_or_find_pdf_document.assert_awaited_once()
    kwargs: dict[str, Any] = (
        mock_runner.create_or_find_pdf_document.await_args.kwargs
    )
    assert kwargs['source_type'] == 'uploaded'
    assert kwargs['website_id'] == 'w-1'
    assert kwargs['project_id'] == 'p-1'


def test_create_redirects_when_website_missing(
    client: FlaskClient, mock_runner: AsyncMock
) -> None:
    """Empty website_id → flash + redirect to add form, runner not invoked."""
    resp = client.post(
        '/projects/p-1/pdfs',
        data={'pdf_file': (io.BytesIO(_TINY_PDF_BYTES), 'x.pdf')},
        content_type='multipart/form-data',
    )
    assert resp.status_code == 302
    assert '/pdfs/add' in resp.headers['Location']
    mock_runner.create_or_find_pdf_document.assert_not_called()


def test_create_redirects_when_website_in_other_project(
    client: FlaskClient, mock_db: MagicMock, mock_runner: AsyncMock
) -> None:
    mock_db.get_website.return_value = MagicMock(
        id="w-1", project_id="p-OTHER", url="https://x", name="X"
    )
    resp = client.post(
        '/projects/p-1/pdfs',
        data={
            'website_id': 'w-1',
            'pdf_file': (io.BytesIO(_TINY_PDF_BYTES), 'x.pdf'),
        },
        content_type='multipart/form-data',
    )
    assert resp.status_code == 302
    mock_runner.create_or_find_pdf_document.assert_not_called()


def test_create_handles_not_a_pdf_exception(
    client: FlaskClient, mock_runner: AsyncMock
) -> None:
    mock_runner.create_or_find_pdf_document.side_effect = NotAPdf("bad bytes")
    resp = client.post(
        '/projects/p-1/pdfs',
        data={
            'website_id': 'w-1',
            'pdf_file': (io.BytesIO(b'GIF89a'), 'fake.pdf'),
        },
        content_type='multipart/form-data',
    )
    assert resp.status_code == 302
    assert '/pdfs/add' in resp.headers['Location']


def test_create_handles_pdf_too_large(
    client: FlaskClient, mock_runner: AsyncMock
) -> None:
    mock_runner.create_or_find_pdf_document.side_effect = PdfTooLarge(
        size_bytes=999, limit_bytes=100
    )
    resp = client.post(
        '/projects/p-1/pdfs',
        data={
            'website_id': 'w-1',
            'pdf_file': (io.BytesIO(_TINY_PDF_BYTES), 'big.pdf'),
        },
        content_type='multipart/form-data',
    )
    assert resp.status_code == 302
    assert '/pdfs/add' in resp.headers['Location']


def test_create_url_path_fetches_and_creates(
    client: FlaskClient, mock_runner: AsyncMock
) -> None:
    """No file upload → URL-fetch path: runner.fetch_pdf_from_url is awaited
    and create_or_find_pdf_document is called with source_type='manual_url'."""
    mock_runner.fetch_pdf_from_url.return_value = _TINY_PDF_BYTES
    created = _make_doc()
    mock_runner.create_or_find_pdf_document.return_value = created
    resp = client.post(
        '/projects/p-1/pdfs',
        data={'website_id': 'w-1', 'source_url': 'https://example.org/x.pdf'},
        content_type='multipart/form-data',
    )
    assert resp.status_code == 302
    assert f'/pdfs/{created.id}' in resp.headers['Location']
    mock_runner.fetch_pdf_from_url.assert_awaited_once()
    fetch_kwargs: dict[str, Any] = (
        mock_runner.fetch_pdf_from_url.await_args.kwargs
    )
    assert fetch_kwargs['website_user_id'] is None
    create_kwargs: dict[str, Any] = (
        mock_runner.create_or_find_pdf_document.await_args.kwargs
    )
    assert create_kwargs['source_type'] == 'manual_url'
    assert create_kwargs['source_url'] == 'https://example.org/x.pdf'
    assert create_kwargs['original_filename'] == 'x.pdf'


def test_create_url_path_flashes_when_neither_provided(
    client: FlaskClient, mock_runner: AsyncMock
) -> None:
    """Neither file nor URL → ``pdf-error-source-required`` flash."""
    resp = client.post(
        '/projects/p-1/pdfs',
        data={'website_id': 'w-1'},
        content_type='multipart/form-data',
    )
    assert resp.status_code == 302
    assert '/pdfs/add' in resp.headers['Location']
    mock_runner.create_or_find_pdf_document.assert_not_called()


def test_create_url_path_handles_fetch_failed(
    client: FlaskClient, mock_runner: AsyncMock
) -> None:
    from auto_a11y.pdf.errors import FetchFailed
    mock_runner.fetch_pdf_from_url.side_effect = FetchFailed(
        'https://x', 'HTTP 503'
    )
    resp = client.post(
        '/projects/p-1/pdfs',
        data={'website_id': 'w-1', 'source_url': 'https://x'},
        content_type='multipart/form-data',
    )
    assert resp.status_code == 302
    assert '/pdfs/add' in resp.headers['Location']
    mock_runner.create_or_find_pdf_document.assert_not_called()


def test_create_url_path_handles_pdf_too_large(
    client: FlaskClient, mock_runner: AsyncMock
) -> None:
    mock_runner.fetch_pdf_from_url.side_effect = PdfTooLarge(
        size_bytes=999, limit_bytes=100
    )
    resp = client.post(
        '/projects/p-1/pdfs',
        data={'website_id': 'w-1', 'source_url': 'https://x'},
        content_type='multipart/form-data',
    )
    assert resp.status_code == 302
    assert '/pdfs/add' in resp.headers['Location']


def test_create_url_path_handles_not_a_pdf(
    client: FlaskClient, mock_runner: AsyncMock
) -> None:
    mock_runner.fetch_pdf_from_url.side_effect = NotAPdf('no magic bytes')
    resp = client.post(
        '/projects/p-1/pdfs',
        data={'website_id': 'w-1', 'source_url': 'https://x'},
        content_type='multipart/form-data',
    )
    assert resp.status_code == 302
    assert '/pdfs/add' in resp.headers['Location']


def test_create_redirects_when_runner_unavailable(
    app: Flask, client: FlaskClient
) -> None:
    """``app.pdf_runner = None`` → friendly flash, no AttributeError."""
    setattr(app, 'pdf_runner', None)
    resp = client.post(
        '/projects/p-1/pdfs',
        data={
            'website_id': 'w-1',
            'pdf_file': (io.BytesIO(_TINY_PDF_BYTES), 'x.pdf'),
        },
        content_type='multipart/form-data',
    )
    assert resp.status_code == 302
    assert '/pdfs/add' in resp.headers['Location']


# ---------------------------------------------------------------------------
# GET /pdfs/<id>
# ---------------------------------------------------------------------------


def test_detail_renders_for_existing_pdf(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    doc = _make_doc()
    mock_db.get_pdf_document.return_value = doc
    resp = client.get(f'/pdfs/{doc.id}')
    assert resp.status_code == 200


def test_detail_redirects_when_pdf_missing(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    mock_db.get_pdf_document.return_value = None
    resp = client.get('/pdfs/nope')
    assert resp.status_code == 302


# ---------------------------------------------------------------------------
# POST /pdfs/<id>/audit
# ---------------------------------------------------------------------------
#
# audit() enqueues a :class:`PdfAuditJob` and returns immediately — the
# job class is mocked so the audit pipeline doesn't run inside the
# request thread.


def test_audit_enqueues_job_and_redirects(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    """Audit POST creates a PdfAuditJob and calls ``start()`` exactly once."""
    doc = _make_doc()
    mock_db.get_pdf_document.return_value = doc
    with patch(
        'auto_a11y.core.pdf_audit_job.PdfAuditJob'
    ) as job_cls:
        instance = job_cls.return_value
        instance.start.return_value = 'pdf_audit_xyz'
        resp = client.post(f'/pdfs/{doc.id}/audit')
    assert resp.status_code == 302
    job_cls.assert_called_once()
    instance.start.assert_called_once_with()


def test_audit_blocks_when_already_running(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    """A doc already in AUDITING flashes a warning and skips the job submit."""
    doc = _make_doc(status=PdfDocumentStatus.AUDITING)
    mock_db.get_pdf_document.return_value = doc
    with patch(
        'auto_a11y.core.pdf_audit_job.PdfAuditJob'
    ) as job_cls:
        resp = client.post(f'/pdfs/{doc.id}/audit')
    assert resp.status_code == 302
    job_cls.assert_not_called()


def test_audit_redirects_when_runner_unavailable(
    app: Flask, client: FlaskClient, mock_db: MagicMock
) -> None:
    """``app.pdf_runner = None`` → friendly flash, no job submitted."""
    doc = _make_doc()
    mock_db.get_pdf_document.return_value = doc
    setattr(app, 'pdf_runner', None)
    with patch(
        'auto_a11y.core.pdf_audit_job.PdfAuditJob'
    ) as job_cls:
        resp = client.post(f'/pdfs/{doc.id}/audit')
    assert resp.status_code == 302
    job_cls.assert_not_called()
    # Restore so other tests don't see a missing runner.
    setattr(app, 'pdf_runner', AsyncMock())


def test_audit_redirects_when_pdf_missing(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    mock_db.get_pdf_document.return_value = None
    with patch(
        'auto_a11y.core.pdf_audit_job.PdfAuditJob'
    ) as job_cls:
        resp = client.post('/pdfs/nope/audit')
    assert resp.status_code == 302
    job_cls.assert_not_called()


# ---------------------------------------------------------------------------
# GET /pdfs/<id>/file and /images/<name>
# ---------------------------------------------------------------------------


def test_file_streams_existing_pdf(
    app: Flask, client: FlaskClient, mock_db: MagicMock, tmp_path: Path
) -> None:
    """``/file`` returns the stored bytes inline with X-Frame-Options=SAMEORIGIN."""
    doc = _make_doc()
    mock_db.get_pdf_document.return_value = doc

    # Materialise the bytes on disk under PDF_STORAGE_DIR.
    storage_root = Path(getattr(app, 'app_config').PDF_STORAGE_DIR)
    pdf_path = storage_root / doc.storage_relpath
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(_TINY_PDF_BYTES)

    resp = client.get(f'/pdfs/{doc.id}/file')
    assert resp.status_code == 200
    assert resp.headers['X-Frame-Options'] == 'SAMEORIGIN'
    assert 'inline' in resp.headers['Content-Disposition']
    assert resp.mimetype == 'application/pdf'


def test_file_redirects_when_bytes_missing(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    """File doesn't exist on disk → redirect to detail with flash."""
    doc = _make_doc()
    mock_db.get_pdf_document.return_value = doc
    resp = client.get(f'/pdfs/{doc.id}/file')
    assert resp.status_code == 302


def test_image_rejects_path_traversal(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    """``..`` in the image name is rejected before any FS access."""
    doc = _make_doc()
    mock_db.get_pdf_document.return_value = doc
    resp = client.get(f'/pdfs/{doc.id}/images/..%2Fetc%2Fpasswd')
    assert resp.status_code in (302, 404)


def test_image_redirects_when_not_found(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    doc = _make_doc()
    mock_db.get_pdf_document.return_value = doc
    resp = client.get(f'/pdfs/{doc.id}/images/missing.png')
    assert resp.status_code == 302


# ---------------------------------------------------------------------------
# POST /pdfs/<id>/delete
# ---------------------------------------------------------------------------


def test_delete_removes_doc_and_redirects(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    doc = _make_doc()
    mock_db.get_pdf_document.return_value = doc
    resp = client.post(f'/pdfs/{doc.id}/delete')
    assert resp.status_code == 302
    mock_db.delete_pdf_document.assert_called_once_with(doc.id)


def test_delete_redirects_when_pdf_missing(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    mock_db.get_pdf_document.return_value = None
    resp = client.post('/pdfs/nope/delete')
    assert resp.status_code == 302
    mock_db.delete_pdf_document.assert_not_called()
