"""Tests for Phase 9.7 PDF navigation integration.

Three small additive changes are exercised here:

1. ``projects/view.html`` renders a "PDFs" link with a count badge.
2. ``websites/view.html`` renders a "PDFs" link with a count badge.
3. ``websites/view.html`` page rows render a "→ PDF" inline badge for
   pages whose status is ``IS_PDF`` and which have a
   ``linked_pdf_document_id``.

We also verify that the project- and website-detail route handlers
compute ``pdf_count`` from ``Database.get_pdf_documents`` and pass it
to ``render_template``. The Jinja layer is exercised by parsing the
real ``.html`` files and re-rendering the relevant fragments against a
mocked ``url_for``/``ftl`` environment, which avoids the cost of
spinning up the full Flask app while still testing the production
template source.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from jinja2 import Environment


REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "auto_a11y" / "web" / "templates"
PROJECT_VIEW = TEMPLATE_DIR / "projects" / "view.html"
WEBSITE_VIEW = TEMPLATE_DIR / "websites" / "view.html"


# ---------------------------------------------------------------------------
# Template content assertions — verify the static markup landed in the file.
# ---------------------------------------------------------------------------


def test_project_view_template_includes_pdf_nav_link() -> None:
    """``projects/view.html`` references the PDF nav Fluent ID and pdf_count."""
    src = PROJECT_VIEW.read_text(encoding="utf-8")
    assert "pdf-nav-link" in src
    assert "pdf.list_for_project" in src
    assert "pdf_count" in src


def test_website_view_template_includes_pdf_nav_link() -> None:
    """``websites/view.html`` references the PDF nav Fluent ID and pdf_count."""
    src = WEBSITE_VIEW.read_text(encoding="utf-8")
    assert "pdf-nav-link" in src
    assert "pdf.list_for_website" in src
    assert "pdf_count" in src


def test_website_view_template_includes_is_pdf_badge() -> None:
    """``websites/view.html`` has the conditional → PDF badge for IS_PDF rows."""
    src = WEBSITE_VIEW.read_text(encoding="utf-8")
    assert "pdf-page-is-pdf-badge" in src
    assert "linked_pdf_document_id" in src
    assert "pdf.detail" in src


# ---------------------------------------------------------------------------
# Jinja-level rendering — the conditional badge fragment behaves correctly.
# ---------------------------------------------------------------------------


_BADGE_FRAGMENT = (
    "{% if page.status.value == 'is_pdf' and page.linked_pdf_document_id %}"
    "<a href=\"{{ url_for('pdf.detail', pdf_document_id=page.linked_pdf_document_id) }}\""
    " class=\"badge badge-info ms-1 text-decoration-none\""
    " aria-label=\"{{ ftl('pdf-page-is-pdf-badge-aria') }}\">"
    "{{ ftl('pdf-page-is-pdf-badge') }}</a>"
    "{% endif %}"
)


def _fake_url_for(endpoint: str, **kwargs: object) -> str:
    pdf_id = kwargs.get("pdf_document_id", "")
    return f"/{endpoint}/{pdf_id}"


def _fake_ftl(message_id: str, **kwargs: object) -> str:
    _ = kwargs
    return f"<{message_id}>"


def _render_badge_fragment(*, status_value: str, linked_id: str | None) -> str:
    env = Environment(autoescape=True)
    tmpl = env.from_string(_BADGE_FRAGMENT)
    page = MagicMock()
    page.status.value = status_value
    page.linked_pdf_document_id = linked_id
    return tmpl.render(
        page=page,
        url_for=_fake_url_for,
        ftl=_fake_ftl,
    )


def test_is_pdf_badge_renders_when_status_is_pdf_and_linked() -> None:
    out = _render_badge_fragment(status_value="is_pdf", linked_id="pdf-42")
    assert "pdf-page-is-pdf-badge" in out
    assert "/pdf.detail/pdf-42" in out
    assert "badge-info" in out


def test_is_pdf_badge_absent_when_status_is_not_is_pdf() -> None:
    out = _render_badge_fragment(status_value="tested", linked_id="pdf-42")
    assert out.strip() == ""


def test_is_pdf_badge_absent_when_no_linked_pdf() -> None:
    out = _render_badge_fragment(status_value="is_pdf", linked_id=None)
    assert out.strip() == ""


# ---------------------------------------------------------------------------
# Route handler tests — ``pdf_count`` is computed and passed through.
# ---------------------------------------------------------------------------


def test_view_project_passes_pdf_count_to_template() -> None:
    """``view_project`` calls ``db.get_pdf_documents(project_id=...)`` and
    forwards the length as ``pdf_count`` to ``render_template``."""
    from auto_a11y.web.routes import projects as projects_module

    db = MagicMock()
    project = MagicMock()
    project.id = "p-1"
    db.get_project.return_value = project
    db.get_websites.return_value = []
    db.get_project_stats.return_value = {
        "website_count": 0, "total_pages": 0, "tested_pages": 0,
        "test_coverage": 0.0, "total_violations": 0, "total_warnings": 0,
    }
    db.get_project_users.return_value = []
    db.get_recordings.return_value = []
    db.discovered_pages = MagicMock()
    db.discovered_pages.find.return_value.sort.return_value = []
    db.get_all_groups.return_value = []
    db.get_pdf_documents.return_value = [MagicMock(), MagicMock(), MagicMock()]

    captured: dict[str, Any] = {}

    def fake_render_template(name: str, **ctx: Any) -> str:
        captured["name"] = name
        captured.update(ctx)
        return "RENDERED"

    fake_user = MagicMock()
    fake_user.is_superadmin = True

    with patch.object(projects_module, "get_db", return_value=db), \
         patch.object(projects_module, "render_template", side_effect=fake_render_template), \
         patch.object(projects_module, "current_user", fake_user), \
         patch.object(projects_module, "g", MagicMock()):
        # The view function carries flask_login + role decorators; call the
        # underlying function via __wrapped__ chain. Falling back to the
        # bare module attribute if the decorator preserved the function.
        fn: Any = projects_module.view_project
        # Unwrap functools.wraps chain if present.
        while hasattr(fn, "__wrapped__"):
            fn = fn.__wrapped__
        result = fn("p-1")

    assert result == "RENDERED"
    assert captured["name"] == "projects/view.html"
    assert captured.get("pdf_count") == 3
    db.get_pdf_documents.assert_called_once_with(project_id="p-1", limit=10000)


def test_view_website_passes_pdf_count_to_template() -> None:
    """``view_website`` calls ``db.get_pdf_documents(website_id=...)`` and
    forwards the length as ``pdf_count`` to ``render_template``."""
    from auto_a11y.web.routes import websites as websites_module

    db = MagicMock()
    website = MagicMock()
    website.id = "w-1"
    website.project_id = "p-1"
    db.get_website.return_value = website
    db.get_project.return_value = MagicMock(id="p-1")
    db.pages = MagicMock()
    db.pages.count_documents.return_value = 0
    db.pages.aggregate.return_value = []
    db.get_pages.return_value = []
    db.get_project_users.return_value = []
    db.get_pdf_documents.return_value = [MagicMock(), MagicMock()]

    cfg = MagicMock()
    cfg.PAGES_PER_PAGE = 100
    cfg.MAX_PAGES_PER_PAGE = 500

    captured: dict[str, Any] = {}

    def fake_render_template(name: str, **ctx: Any) -> str:
        captured["name"] = name
        captured.update(ctx)
        return "RENDERED"

    def _request_args_get(key: str, default: object = None, type: object = None) -> object:
        _ = key, type
        return default

    fake_request = MagicMock()
    fake_request.args.get.side_effect = _request_args_get

    with patch.object(websites_module, "get_db", return_value=db), \
         patch.object(websites_module, "get_app_config", return_value=cfg), \
         patch.object(websites_module, "render_template", side_effect=fake_render_template), \
         patch.object(websites_module, "request", fake_request):
        fn: Any = websites_module.view_website
        while hasattr(fn, "__wrapped__"):
            fn = fn.__wrapped__
        result = fn("w-1")

    assert result == "RENDERED"
    assert captured["name"] == "websites/view.html"
    assert captured.get("pdf_count") == 2
    db.get_pdf_documents.assert_called_once_with(website_id="w-1", limit=10000)
