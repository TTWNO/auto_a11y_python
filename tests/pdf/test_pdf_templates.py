"""Render-pass smoke tests for the PDF templates (Phase 9.4).

These exercise the production templates under
``auto_a11y/web/templates/pdf/`` together with the shared partials
``_target_header.html`` and ``_target_result_summary.html``. We don't
try to assert the entire DOM — we check that each template renders
without raising and that key landmarks (an iframe on the detail page,
an enctype on the upload form, the empty-state Fluent message on an
empty list) are present.

The tests piggy-back on the same DB-mock pattern as
``test_pdf_routes.py`` but build their own Flask app pointed at the
real template folder.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId
from flask import Blueprint, Flask
from flask.testing import FlaskClient
from flask_login import LoginManager

# Match test_pdf_routes.py: ensure ``auto_a11y.core`` is imported before
# any module under ``auto_a11y.testing``.
import auto_a11y.core as _core_preload
del _core_preload

from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus
from auto_a11y.models.test_result import (
    ImpactLevel,
    TargetType,
    TestResult,
    Violation,
)


_TINY_PDF_BYTES = b"%PDF-1.4\n%fake\n%%EOF\n"


def _make_doc(
    *,
    status: PdfDocumentStatus = PdfDocumentStatus.AUDITED,
    project_id: str = "p-1",
    website_id: str = "w-1",
    last_audit_result_id: str | None = None,
    last_audited_at: datetime | None = None,
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
        original_filename="report.pdf",
        pdf_version="1.7",
        page_count=12,
        declared_lang="en",
        detected_lang="en",
        lang_confidence=0.99,
        status=status,
        error_reason=None,
        last_audit_result_id=last_audit_result_id,
        discovered_at=datetime(2026, 1, 1, 12, 0),
        last_audited_at=last_audited_at,
    )
    doc.mongo_id = oid
    return doc


@pytest.fixture(scope='session')
def session_storage_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("pdf_template_storage")


@pytest.fixture(scope='session')
def app(session_storage_dir: Path) -> Flask:
    """Flask app pointed at the real ``auto_a11y/web/templates`` folder."""
    template_dir = (
        Path(__file__).resolve().parents[2]
        / "auto_a11y" / "web" / "templates"
    )
    static_dir = (
        Path(__file__).resolve().parents[2]
        / "auto_a11y" / "web" / "static"
    )
    public_static_dir = (
        Path(__file__).resolve().parents[2]
        / "auto_a11y" / "web" / "static" / "public"
    )
    app = Flask(
        __name__,
        template_folder=str(template_dir),
        static_folder=str(static_dir),
    )

    # base.html includes ``url_for('public.static', filename='css/tokens.css')``
    # — register a small ``public`` blueprint with its own static endpoint.
    public_bp = Blueprint(
        'public',
        __name__,
        static_folder=str(public_static_dir),
        static_url_path='/public/static',
    )
    app.register_blueprint(public_bp)
    app.testing = True
    app.secret_key = 'test'

    cfg = MagicMock()
    cfg.PDF_STORAGE_DIR = str(session_storage_dir)
    setattr(app, 'app_config', cfg)
    setattr(app, 'db', MagicMock())
    setattr(app, 'pdf_runner', AsyncMock())

    # Auto-auth as superadmin so the @login_required + @project_role_required
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

    # Globals templates may reference. Real values are filled by the
    # base template's authentication chrome — we stub them so the layout
    # does not blow up when rendered without a logged-in user.
    from auto_a11y.web.fluent import get_current_locale

    def _user_can(*args: object, **kwargs: object) -> bool:
        _ = args, kwargs
        return False

    def _csrf_token() -> str:
        return 'test-csrf'

    anonymous_user = MagicMock()
    anonymous_user.is_authenticated = False
    anonymous_user.name_display = 'Anon'

    @app.context_processor
    def _inject_layout_stubs() -> dict[str, object]:
        return {
            'is_superadmin': False,
            'user_has_projects': True,
            'user_can': _user_can,
            'get_locale': get_current_locale,
            'show_error_codes': False,
            'current_user': anonymous_user,
            'csrf_token': _csrf_token,
        }

    # Reference the registered context processor to silence the
    # reportUnusedFunction lint without affecting behaviour.
    _ = _inject_layout_stubs

    from auto_a11y.web.fluent import init_fluent
    init_fluent(app)

    # Cross-blueprint endpoint stubs. base.html references many endpoints
    # via url_for(); we install one stub blueprint per namespace covering
    # all the endpoints referenced by the layout chrome.
    def _empty_view(**kwargs: object) -> str:
        _ = kwargs
        return ''

    # Top-level endpoints (no blueprint prefix).
    app.add_url_rule('/', endpoint='index', view_func=_empty_view)
    app.add_url_rule('/dashboard', endpoint='dashboard', view_func=_empty_view)
    app.add_url_rule('/help', endpoint='help', view_func=_empty_view)
    app.add_url_rule('/accessibility-statement', endpoint='accessibility_statement', view_func=_empty_view)

    # Helper that builds a Blueprint pre-populated with all its endpoints
    # before registration — Flask forbids add_url_rule after register.
    def _make_bp(name: str, rules: list[tuple[str, str]]) -> Blueprint:
        bp = Blueprint(name, __name__)
        for endpoint, rule in rules:
            # Allow URL converters in the rule.
            bp.add_url_rule(rule, endpoint=endpoint, view_func=_empty_view)
        return bp

    projects_bp = _make_bp('projects', [
        ('list_projects', '/'),
        ('view_project', '/<project_id>'),
    ])
    websites_bp = _make_bp('websites', [
        ('view_website', '/<website_id>'),
    ])
    testing_bp = _make_bp('testing', [
        ('testing_dashboard', '/'),
        ('trends_page', '/trends'),
        ('configure_testing', '/configure'),
        ('fixture_status', '/fixtures'),
    ])
    schedules_bp = _make_bp('schedules', [
        ('schedules_dashboard', '/'),
    ])
    reports_bp = _make_bp('reports', [
        ('reports_dashboard', '/'),
    ])
    recordings_bp = _make_bp('recordings', [
        ('list_recordings', '/'),
    ])
    auth_bp = _make_bp('auth', [
        ('login', '/login'),
        ('logout', '/logout'),
        ('profile', '/profile'),
        ('user_list', '/users'),
    ])
    groups_bp = _make_bp('groups', [
        ('list_groups', '/'),
    ])

    app.register_blueprint(projects_bp, url_prefix='/projects')
    app.register_blueprint(websites_bp, url_prefix='/websites')
    app.register_blueprint(testing_bp, url_prefix='/testing')
    app.register_blueprint(schedules_bp, url_prefix='/schedules')
    app.register_blueprint(reports_bp, url_prefix='/reports')
    app.register_blueprint(recordings_bp, url_prefix='/recordings')
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(groups_bp, url_prefix='/groups')

    from auto_a11y.web.routes.pdf import pdf_bp
    app.register_blueprint(pdf_bp, url_prefix='')

    return app


@pytest.fixture
def mock_db(app: Flask) -> Iterator[MagicMock]:
    db = MagicMock()
    db.get_project.return_value = MagicMock(id="p-1", name="Project One")
    website_mock = MagicMock(
        id="w-1", project_id="p-1", url="https://example.org", name="Site"
    )
    db.get_website.return_value = website_mock
    db.get_websites.return_value = []
    db.get_pdf_documents.return_value = []
    db.get_pdf_document.return_value = None
    db.get_test_result.return_value = None
    setattr(app, 'db', db)
    yield db


@pytest.fixture
def client(app: Flask, mock_db: MagicMock) -> FlaskClient:
    _ = mock_db
    return app.test_client()


# ---------------------------------------------------------------------------
# List view
# ---------------------------------------------------------------------------


def test_list_renders_empty_state_when_no_pdfs(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    mock_db.get_pdf_documents.return_value = []
    resp = client.get('/projects/p-1/pdfs')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Empty-state Fluent text should appear (English bundle is loaded).
    assert 'No PDF documents' in body
    # Upload CTA link to the add form should be rendered.
    assert '/projects/p-1/pdfs/add' in body


def test_list_renders_table_for_each_pdf(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    docs = [_make_doc(), _make_doc(status=PdfDocumentStatus.PENDING)]
    mock_db.get_pdf_documents.return_value = docs
    resp = client.get('/projects/p-1/pdfs')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert '<table' in body
    # Status badge text from pdf-status.ftl.
    assert 'Audited' in body or 'Pending' in body
    # Both filenames should be linked.
    for doc in docs:
        assert doc.id is not None
        assert f'/pdfs/{doc.id}' in body


def test_list_for_website_renders(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    """Website-scoped list also renders without raising."""
    mock_db.get_pdf_documents.return_value = []
    resp = client.get('/websites/w-1/pdfs')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # The breadcrumb shows the website name.
    assert 'Site' in body
    # The empty-state message is still rendered.
    assert 'No PDF documents' in body


# ---------------------------------------------------------------------------
# Add form
# ---------------------------------------------------------------------------


def test_add_form_renders_multipart_form(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    mock_db.get_websites.return_value = [
        MagicMock(id="w-1", url="https://example.org", name="Site"),
    ]
    resp = client.get('/projects/p-1/pdfs/add')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert '<form' in body
    assert 'enctype="multipart/form-data"' in body
    # Both the upload and URL groups are rendered (JS toggles visibility).
    assert 'data-pdf-source-group="upload"' in body
    assert 'data-pdf-source-group="url"' in body
    # The website select includes the available website.
    assert 'w-1' in body


# ---------------------------------------------------------------------------
# Detail view
# ---------------------------------------------------------------------------


def test_detail_renders_pdfjs_viewer_pointing_at_file_route(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    doc = _make_doc()
    assert doc.id is not None
    mock_db.get_pdf_document.return_value = doc
    resp = client.get(f'/pdfs/{doc.id}')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # pdf_viewer_app.js bootstraps from data-pdf-* attributes on the
    # host element. The PDF URL is the same /file route the iframe
    # used previously — the rendering technology changed (PDF.js
    # inside our shell vs. a Chromium iframe) but the data source did
    # not.
    assert 'data-pdf-viewer-host' in body
    assert f'data-pdf-url="/pdfs/{doc.id}/file"' in body
    assert 'data-pdf-viewer-canvas' in body
    assert 'data-pdf-viewer-overlay-layer' in body
    assert 'data-pdf-viewer-semantic-layer' in body
    assert 'data-pdf-viewer-connector-layer' in body
    # No leftover Chromium iframe markup.
    assert '<iframe' not in body
    assert 'data-pdf-iframe' not in body


def test_detail_renders_violations_with_jump_button(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    doc = _make_doc(last_audit_result_id="r-1")
    assert doc.id is not None
    mock_db.get_pdf_document.return_value = doc

    violation = Violation(
        id="pdf-no-document-title",
        impact=ImpactLevel.HIGH,
        touchpoint="DocumentProperties",
        description="The PDF is missing a Title in /Info.",
        metadata={"pdf_page": 3},
    )
    test_result = TestResult(
        page_id=None,
        target_type=TargetType.PDF_DOCUMENT,
        target_id=doc.id,
        violations=[violation],
    )
    mock_db.get_test_result.return_value = test_result

    resp = client.get(f'/pdfs/{doc.id}')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Jump-to-page button rendered with the metadata page number.
    assert 'data-pdf-page="3"' in body
    # The violation description is in the body.
    assert 'missing a Title' in body


def test_detail_renders_audit_progress_when_auditing(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    doc = _make_doc(status=PdfDocumentStatus.AUDITING)
    assert doc.id is not None
    mock_db.get_pdf_document.return_value = doc
    resp = client.get(f'/pdfs/{doc.id}')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert f'data-pdf-audit-progress="{doc.id}"' in body
    # The polite live-region is present so SR users hear stage updates.
    assert 'aria-live="polite"' in body


def test_detail_status_badge_uses_severity_token_class(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    """The shared header partial maps status -> token-based badge class."""
    doc = _make_doc(status=PdfDocumentStatus.FETCH_FAILED)
    assert doc.id is not None
    mock_db.get_pdf_document.return_value = doc
    resp = client.get(f'/pdfs/{doc.id}')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # FETCH_FAILED is rendered with the high-severity token class.
    assert 'badge-high' in body
    # And NOT with any Bootstrap colour utility — explicitly check the
    # forbidden classes are absent from the page body.
    for forbidden in ('btn-primary', 'bg-danger', 'text-warning',
                      'alert-success', 'badge bg-info'):
        assert forbidden not in body


# ---------------------------------------------------------------------------
# Static asset
# ---------------------------------------------------------------------------


def test_pdf_viewer_js_is_served_as_static_asset(client: FlaskClient) -> None:
    """The PDF.js viewer module is reachable as a standalone static asset."""
    resp = client.get('/static/js/pdf_viewer_app.js')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Spot-check the module's distinguishing identifiers.
    assert 'data-pdf-viewer-host' in body
    assert 'PdfViewerApp' in body


def test_pdfjs_runtime_is_served_locally(client: FlaskClient) -> None:
    """pdfjs-dist is vendored under /static/vendor so we don't depend on a CDN."""
    main = client.get('/static/vendor/pdfjs/pdf.min.mjs')
    worker = client.get('/static/vendor/pdfjs/pdf.worker.min.mjs')
    assert main.status_code == 200
    assert worker.status_code == 200


def test_pdf_report_navigation_js_is_served_as_static_asset(
    client: FlaskClient,
) -> None:
    """The arrow-key + hash-link nav handler is reachable as JS."""
    resp = client.get('/static/js/pdf_report_navigation.js')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Spot-check the script's distinguishing identifiers are present.
    assert 'pdf-violation-card-highlight' in body
    assert 'data-pdf-violation-card' in body


def test_detail_includes_export_buttons_when_audited(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    """Save-as-HTML / Save-as-Markdown links surface only after an audit ran."""
    doc = _make_doc(last_audit_result_id="r-1")
    assert doc.id is not None
    mock_db.get_pdf_document.return_value = doc
    resp = client.get(f'/pdfs/{doc.id}')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert f'/pdfs/{doc.id}/export.html' in body
    assert f'/pdfs/{doc.id}/export.md' in body
    # Both anchors carry the ``download`` attribute so the browser
    # offers a "Save As" dialog instead of navigating in-place.
    assert 'download' in body


def test_detail_omits_export_buttons_before_first_audit(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    """No audit result yet → no export links (nothing to export)."""
    doc = _make_doc(last_audit_result_id=None)
    assert doc.id is not None
    mock_db.get_pdf_document.return_value = doc
    resp = client.get(f'/pdfs/{doc.id}')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert '/export.html' not in body
    assert '/export.md' not in body


def test_detail_includes_report_navigation_script(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    """The detail template loads pdf_report_navigation.js so arrow-key
    nav and hash-link auto-expand are wired up client-side."""
    doc = _make_doc(last_audit_result_id="r-1")
    assert doc.id is not None
    mock_db.get_pdf_document.return_value = doc
    resp = client.get(f'/pdfs/{doc.id}')
    body = resp.get_data(as_text=True)
    assert 'pdf_report_navigation.js' in body


def test_create_post_uses_uploaded_file_path(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    """Smoke test the POST route still works against the new templates."""
    mock_db.get_websites.return_value = [
        MagicMock(id="w-1", url="https://x", name="X"),
    ]
    # No file → URL path → flash + redirect (URL path is not implemented).
    resp = client.post(
        '/projects/p-1/pdfs',
        data={'website_id': 'w-1'},
        content_type='multipart/form-data',
    )
    assert resp.status_code == 302


def test_list_filter_form_present_on_project_view(
    client: FlaskClient, mock_db: MagicMock
) -> None:
    mock_db.get_pdf_documents.return_value = []
    resp = client.get('/projects/p-1/pdfs')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'pdf-filter-status' in body
    # Status options match the PdfDocumentStatus enum values.
    for opt in ('pending', 'auditing', 'audited', 'audit_failed'):
        assert f'value="{opt}"' in body


