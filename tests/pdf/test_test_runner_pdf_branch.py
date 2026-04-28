"""Tests for the TestRunner ↔ PdfRunner integration (Phase 8.2).

Covers:

* :meth:`TestRunner.test_pdf` — thin async delegate to
  :meth:`PdfRunner.audit_pdf_document` and the no-PdfRunner guard.
* :func:`handle_opportunistic_pdf` — the module-level helper invoked by
  :meth:`TestRunner.test_page` when the navigation response is a PDF.
  We test the helper directly: full ``test_page`` integration would
  require mocking the Playwright stack end-to-end, which buys little
  beyond what the helper-level tests already give us.
"""
from __future__ import annotations

# ``auto_a11y.core`` must load before ``auto_a11y.testing`` to break
# the package init's circular import (testing → core → testing). The
# ``import as _``/``del`` pattern keeps strict type-checkers from
# flagging the otherwise-unused import.
import auto_a11y.core as _core_preload
del _core_preload

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

from auto_a11y.models.page import Page, PageStatus
from auto_a11y.pdf.errors import NotAPdf, PdfTooLarge
from auto_a11y.testing import test_runner as test_runner_mod
from auto_a11y.testing.test_runner import (
    TestRunner,
    handle_opportunistic_pdf,
    handle_pdf_url_after_navigation_failed,
    url_looks_like_pdf,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db() -> MagicMock:
    """Bare :class:`MagicMock` standing in for :class:`Database`."""
    return MagicMock()


@pytest.fixture
def mock_pdf_runner() -> AsyncMock:
    """:class:`AsyncMock` shaped like :class:`PdfRunner`."""
    return AsyncMock()


@pytest.fixture
def runner(
    mock_db: MagicMock, mock_pdf_runner: AsyncMock, tmp_path: Any
) -> TestRunner:
    """:class:`TestRunner` with a mocked DB and PdfRunner.

    The browser config points ``SCREENSHOTS_DIR`` at ``tmp_path`` so the
    constructor's ``mkdir`` doesn't pollute the repo with a top-level
    ``screenshots/`` directory.
    """
    return TestRunner(
        database=mock_db,
        browser_config={
            "headless": True,
            "browser_type": "chromium",
            "SCREENSHOTS_DIR": str(tmp_path / "screenshots"),
        },
        pdf_runner=mock_pdf_runner,
    )


def _make_page(url: str = "https://example.com/doc.pdf") -> Page:
    """Build a real :class:`Page` for the test (so attribute writes flow
    through the dataclass and ``page.id`` works).
    """
    p = Page(website_id="w1", url=url)
    p.mongo_id = ObjectId()
    return p


# ---------------------------------------------------------------------------
# test_pdf delegate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_test_pdf_delegates_to_pdf_runner(
    runner: TestRunner, mock_pdf_runner: AsyncMock
) -> None:
    """:meth:`TestRunner.test_pdf` forwards every kwarg verbatim."""
    sentinel = MagicMock()
    mock_pdf_runner.audit_pdf_document.return_value = sentinel

    result = await runner.test_pdf(
        "doc-1",
        run_ai_analysis=False,
        ai_api_key=None,
        wcag_level="AA",
        locale="en",
    )

    assert result is sentinel
    mock_pdf_runner.audit_pdf_document.assert_awaited_once_with(
        "doc-1",
        run_ai=False,
        ai_api_key=None,
        wcag_level="AA",
        locale="en",
    )


@pytest.mark.asyncio
async def test_test_pdf_forwards_overridden_kwargs(
    runner: TestRunner, mock_pdf_runner: AsyncMock
) -> None:
    """Non-default kwargs (``AAA``, French locale, AI on) all pass through."""
    mock_pdf_runner.audit_pdf_document.return_value = MagicMock()
    await runner.test_pdf(
        "doc-2",
        run_ai_analysis=True,
        ai_api_key="key-xyz",
        wcag_level="AAA",
        locale="fr",
    )
    mock_pdf_runner.audit_pdf_document.assert_awaited_once_with(
        "doc-2",
        run_ai=True,
        ai_api_key="key-xyz",
        wcag_level="AAA",
        locale="fr",
    )


@pytest.mark.asyncio
async def test_test_pdf_raises_when_no_pdf_runner_attached(
    mock_db: MagicMock, tmp_path: Any
) -> None:
    """Calling :meth:`test_pdf` without a :class:`PdfRunner` raises."""
    runner = TestRunner(
        database=mock_db,
        browser_config={
            "headless": True,
            "SCREENSHOTS_DIR": str(tmp_path / "s"),
        },
        pdf_runner=None,
    )
    with pytest.raises(RuntimeError, match="no PdfRunner"):
        await runner.test_pdf("doc-1")


# ---------------------------------------------------------------------------
# handle_opportunistic_pdf — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_opportunistic_pdf_success(
    mock_pdf_runner: AsyncMock, mock_db: MagicMock
) -> None:
    """End-to-end: PDF response → create_or_find → audit_pdf_document."""
    page = _make_page()
    mock_db.get_website.return_value = MagicMock(project_id="proj-1")

    pdf_doc = MagicMock(id="doc-1")
    mock_pdf_runner.create_or_find_pdf_document.return_value = pdf_doc
    audit_result = MagicMock()
    mock_pdf_runner.audit_pdf_document.return_value = audit_result

    response = AsyncMock()
    response.body = AsyncMock(return_value=b"%PDF-1.7\n%fake\n%%EOF\n")

    result = await handle_opportunistic_pdf(
        db=mock_db,
        pdf_runner=mock_pdf_runner,
        page=page,
        browser_page=MagicMock(),
        response=response,
        run_ai_analysis=False,
        ai_api_key=None,
    )

    assert result is audit_result
    assert page.status == PageStatus.IS_PDF
    assert page.linked_pdf_document_id == "doc-1"
    mock_db.update_page.assert_called_with(page)

    create_call = mock_pdf_runner.create_or_find_pdf_document.await_args
    assert create_call is not None
    assert create_call.args[0] == b"%PDF-1.7\n%fake\n%%EOF\n"
    assert create_call.kwargs["website_id"] == "w1"
    assert create_call.kwargs["project_id"] == "proj-1"
    assert create_call.kwargs["source_type"] == "opportunistic"
    assert create_call.kwargs["original_filename"] == "doc.pdf"

    mock_pdf_runner.audit_pdf_document.assert_awaited_once_with(
        "doc-1", run_ai=False, ai_api_key=None, wcag_level="AA"
    )


# ---------------------------------------------------------------------------
# handle_opportunistic_pdf — error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_opportunistic_pdf_missing_runner_marks_page_error(
    mock_db: MagicMock,
) -> None:
    """No :class:`PdfRunner` → page gets ``ERROR`` and ``RuntimeError`` is raised."""
    page = _make_page()
    response = AsyncMock()
    with pytest.raises(RuntimeError, match="no PdfRunner"):
        await handle_opportunistic_pdf(
            db=mock_db,
            pdf_runner=None,
            page=page,
            browser_page=MagicMock(),
            response=response,
            run_ai_analysis=False,
            ai_api_key=None,
        )
    assert page.status == PageStatus.ERROR
    assert page.error_reason is not None
    assert "no PdfRunner" in page.error_reason
    mock_db.update_page.assert_called_with(page)


@pytest.mark.asyncio
async def test_handle_opportunistic_pdf_falls_back_to_aiohttp(
    mock_pdf_runner: AsyncMock,
    mock_db: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``response.body()`` raising → the aiohttp fallback path runs."""
    page = _make_page()
    mock_db.get_website.return_value = MagicMock(project_id="proj-1")

    pdf_doc = MagicMock(id="doc-fallback")
    mock_pdf_runner.create_or_find_pdf_document.return_value = pdf_doc
    mock_pdf_runner.audit_pdf_document.return_value = MagicMock()

    response = AsyncMock()
    response.body = AsyncMock(side_effect=RuntimeError("body unavailable"))

    fallback_bytes = b"%PDF-1.7\n%fallback\n%%EOF\n"

    async def _stub_fallback(_bp: Any, _url: str) -> bytes:
        return fallback_bytes

    monkeypatch.setattr(
        test_runner_mod,
        "fetch_pdf_with_playwright_cookies",
        _stub_fallback,
    )

    await handle_opportunistic_pdf(
        db=mock_db,
        pdf_runner=mock_pdf_runner,
        page=page,
        browser_page=MagicMock(),
        response=response,
        run_ai_analysis=False,
        ai_api_key=None,
    )

    create_call = mock_pdf_runner.create_or_find_pdf_document.await_args
    assert create_call is not None
    assert create_call.args[0] == fallback_bytes
    assert page.status == PageStatus.IS_PDF


@pytest.mark.asyncio
async def test_handle_opportunistic_pdf_marks_page_error_on_download_failure(
    mock_pdf_runner: AsyncMock,
    mock_db: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both ``response.body()`` and the aiohttp fallback failing → page ERROR, no doc."""
    page = _make_page()
    response = AsyncMock()
    response.body = AsyncMock(side_effect=RuntimeError("body unavailable"))

    async def _stub_fallback(_bp: Any, _url: str) -> bytes:
        raise RuntimeError("network down")

    monkeypatch.setattr(
        test_runner_mod,
        "fetch_pdf_with_playwright_cookies",
        _stub_fallback,
    )

    with pytest.raises(RuntimeError, match="network down"):
        await handle_opportunistic_pdf(
            db=mock_db,
            pdf_runner=mock_pdf_runner,
            page=page,
            browser_page=MagicMock(),
            response=response,
            run_ai_analysis=False,
            ai_api_key=None,
        )

    assert page.status == PageStatus.ERROR
    assert page.error_reason is not None
    assert "PDF download failed" in page.error_reason
    mock_pdf_runner.create_or_find_pdf_document.assert_not_called()
    mock_pdf_runner.audit_pdf_document.assert_not_called()


@pytest.mark.asyncio
async def test_handle_opportunistic_pdf_missing_website_marks_page_error(
    mock_pdf_runner: AsyncMock, mock_db: MagicMock
) -> None:
    """``Database.get_website`` returning ``None`` → page ERROR, no PdfDocument created."""
    page = _make_page()
    mock_db.get_website.return_value = None

    response = AsyncMock()
    response.body = AsyncMock(return_value=b"%PDF-1.7\n%fake\n%%EOF\n")

    with pytest.raises(RuntimeError, match="Website w1 not found"):
        await handle_opportunistic_pdf(
            db=mock_db,
            pdf_runner=mock_pdf_runner,
            page=page,
            browser_page=MagicMock(),
            response=response,
            run_ai_analysis=False,
            ai_api_key=None,
        )

    assert page.status == PageStatus.ERROR
    mock_pdf_runner.create_or_find_pdf_document.assert_not_called()


@pytest.mark.asyncio
async def test_handle_opportunistic_pdf_rejects_non_pdf_bytes(
    mock_pdf_runner: AsyncMock, mock_db: MagicMock
) -> None:
    """:class:`NotAPdf` from ``create_or_find_pdf_document`` → page ERROR + re-raise."""
    page = _make_page()
    mock_db.get_website.return_value = MagicMock(project_id="proj-1")

    response = AsyncMock()
    response.body = AsyncMock(return_value=b"<html>not a pdf</html>")

    mock_pdf_runner.create_or_find_pdf_document.side_effect = NotAPdf(
        "Bytes do not start with %PDF- magic header"
    )

    with pytest.raises(NotAPdf):
        await handle_opportunistic_pdf(
            db=mock_db,
            pdf_runner=mock_pdf_runner,
            page=page,
            browser_page=MagicMock(),
            response=response,
            run_ai_analysis=False,
            ai_api_key=None,
        )

    assert page.status == PageStatus.ERROR
    assert page.error_reason is not None
    assert "PDF rejected" in page.error_reason
    mock_pdf_runner.audit_pdf_document.assert_not_called()


@pytest.mark.asyncio
async def test_handle_opportunistic_pdf_rejects_oversize(
    mock_pdf_runner: AsyncMock, mock_db: MagicMock
) -> None:
    """:class:`PdfTooLarge` from ``create_or_find_pdf_document`` → page ERROR + re-raise."""
    page = _make_page()
    mock_db.get_website.return_value = MagicMock(project_id="proj-1")

    response = AsyncMock()
    response.body = AsyncMock(return_value=b"%PDF-1.7\nlots-of-bytes\n%%EOF\n")

    mock_pdf_runner.create_or_find_pdf_document.side_effect = PdfTooLarge(
        500_000_000, 100_000_000
    )

    with pytest.raises(PdfTooLarge):
        await handle_opportunistic_pdf(
            db=mock_db,
            pdf_runner=mock_pdf_runner,
            page=page,
            browser_page=MagicMock(),
            response=response,
            run_ai_analysis=False,
            ai_api_key=None,
        )

    assert page.status == PageStatus.ERROR
    assert page.error_reason is not None
    assert "PDF rejected" in page.error_reason
    mock_pdf_runner.audit_pdf_document.assert_not_called()


# ---------------------------------------------------------------------------
# url_looks_like_pdf heuristic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url,expected", [
    ("https://example.com/doc.pdf", True),
    ("https://example.com/doc.PDF", True),
    ("https://example.com/path/to/file.pdf?download=1", True),
    ("https://example.com/path/to/file.pdf#page=2", True),
    ("https://example.com/index.html", False),
    ("https://example.com/", False),
    ("https://example.com/pdf-viewer", False),
    ("https://example.com/document.pdfx", False),
])
def test_url_looks_like_pdf(url: str, expected: bool) -> None:
    assert url_looks_like_pdf(url) is expected


# ---------------------------------------------------------------------------
# handle_pdf_url_after_navigation_failed (goto returned None)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_navigation_aborted_pdf_falls_back_to_direct_fetch(
    mock_pdf_runner: AsyncMock, mock_db: MagicMock
) -> None:
    """When goto returns None on a .pdf URL, we re-fetch via PdfRunner and audit."""
    page = _make_page()
    mock_db.get_website.return_value = MagicMock(project_id="proj-1")

    pdf_doc = MagicMock(id="doc-1")
    mock_pdf_runner.fetch_pdf_from_url.return_value = b"%PDF-1.7\n...\n%%EOF\n"
    mock_pdf_runner.create_or_find_pdf_document.return_value = pdf_doc
    mock_pdf_runner.audit_pdf_document.return_value = MagicMock()

    result = await handle_pdf_url_after_navigation_failed(
        db=mock_db,
        pdf_runner=mock_pdf_runner,
        page=page,
        run_ai_analysis=False,
        ai_api_key=None,
    )

    mock_pdf_runner.fetch_pdf_from_url.assert_awaited_once_with(page.url)
    mock_pdf_runner.create_or_find_pdf_document.assert_awaited_once()
    mock_pdf_runner.audit_pdf_document.assert_awaited_once()
    assert page.status == PageStatus.IS_PDF
    assert page.linked_pdf_document_id == "doc-1"
    assert result is mock_pdf_runner.audit_pdf_document.return_value


@pytest.mark.asyncio
async def test_navigation_aborted_pdf_marks_page_error_on_fetch_failure(
    mock_pdf_runner: AsyncMock, mock_db: MagicMock
) -> None:
    """A non-NotAPdf/PdfTooLarge fetch error → page.status=ERROR with reason."""
    page = _make_page()
    mock_pdf_runner.fetch_pdf_from_url.side_effect = RuntimeError("connection refused")

    with pytest.raises(RuntimeError, match="connection refused"):
        await handle_pdf_url_after_navigation_failed(
            db=mock_db,
            pdf_runner=mock_pdf_runner,
            page=page,
            run_ai_analysis=False,
            ai_api_key=None,
        )

    assert page.status == PageStatus.ERROR
    assert page.error_reason is not None
    assert "PDF download failed" in page.error_reason
    mock_pdf_runner.create_or_find_pdf_document.assert_not_called()


@pytest.mark.asyncio
async def test_navigation_aborted_pdf_no_runner_marks_page_error(
    mock_db: MagicMock,
) -> None:
    """No PdfRunner → page.status=ERROR; never tries to fetch."""
    page = _make_page()

    with pytest.raises(RuntimeError, match="no PdfRunner"):
        await handle_pdf_url_after_navigation_failed(
            db=mock_db,
            pdf_runner=None,
            page=page,
            run_ai_analysis=False,
            ai_api_key=None,
        )

    assert page.status == PageStatus.ERROR


@pytest.mark.asyncio
async def test_navigation_aborted_pdf_rejects_not_a_pdf(
    mock_pdf_runner: AsyncMock, mock_db: MagicMock
) -> None:
    """fetch_pdf_from_url raises NotAPdf → page.status=ERROR with 'PDF rejected'."""
    page = _make_page()
    mock_pdf_runner.fetch_pdf_from_url.side_effect = NotAPdf("no magic bytes")

    with pytest.raises(NotAPdf):
        await handle_pdf_url_after_navigation_failed(
            db=mock_db,
            pdf_runner=mock_pdf_runner,
            page=page,
            run_ai_analysis=False,
            ai_api_key=None,
        )

    assert page.status == PageStatus.ERROR
    assert page.error_reason is not None
    assert "PDF rejected" in page.error_reason
