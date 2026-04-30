"""Tests for :func:`auto_a11y.core.scraping_job.promote_discovered_pdfs`.

The promotion step that turns internal PDF :class:`DocumentReference`
records into :class:`PdfDocument` records is exercised in isolation —
the surrounding discovery loop, browser, and Flask app are not touched.

Covered:

* ``pdf_runner=None`` short-circuits.
* ``website.scraping_config.auto_fetch_pdfs == False`` short-circuits.
* PDFs already present (matching ``source_url``) are skipped without
  a download.
* :class:`FetchFailed` / :class:`NotAPdf` / :class:`PdfTooLarge` from
  fetch are isolated per-PDF and don't abort the loop.
* Non-PDF document references (zip, rtf) are filtered out.
* Cancellation interrupts the loop.

Audit queueing is *not* part of this helper any more — it lives in
:func:`auto_a11y.core.pdf_audit_job.queue_audits_for_website`, which
the "Test All Pages" route invokes. See
``tests/test_queue_audits_for_website.py``.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# Same import-order dance as test_pdf_runner.py: `core` must load before
# `testing`, so the package chain resolves before we touch
# ``testing.pdf_runner``.
import auto_a11y.core as _core_preload
del _core_preload
from auto_a11y.core import scraping_job as scraping_job_module
from auto_a11y.core.scraping_job import promote_discovered_pdfs
from auto_a11y.models.document_reference import DocumentReference
from auto_a11y.pdf.errors import FetchFailed, NotAPdf, PdfTooLarge


def _make_website(*, auto_fetch: bool = True) -> Any:
    """Minimal website stand-in with ``project_id`` and scraping_config."""
    website = MagicMock()
    website.project_id = "pid-abc"
    website.scraping_config = MagicMock()
    website.scraping_config.auto_fetch_pdfs = auto_fetch
    return website


def _make_runner() -> MagicMock:
    """Mock PdfRunner with the public fields ``promote_discovered_pdfs`` reads."""
    runner = MagicMock()
    runner.max_size_bytes = 100 * 1024 * 1024
    runner.create_or_find_pdf_document = AsyncMock()
    return runner


def _ref(url: str, *, mime: str = "application/pdf") -> DocumentReference:
    return DocumentReference(
        website_id="wid-123",
        document_url=url,
        referring_page_url="http://host/",
        mime_type=mime,
        is_internal=True,
    )


@pytest.mark.asyncio
async def test_skipped_when_pdf_runner_is_none() -> None:
    db = MagicMock()
    await promote_discovered_pdfs(
        database=db,
        website=_make_website(),
        pdf_runner=None,
        website_id="wid-123",
        is_cancelled=lambda: False,
    )
    db.get_document_references.assert_not_called()


@pytest.mark.asyncio
async def test_skipped_when_auto_fetch_disabled_on_website() -> None:
    db = MagicMock()
    runner = _make_runner()
    await promote_discovered_pdfs(
        database=db,
        website=_make_website(auto_fetch=False),
        pdf_runner=runner,
        website_id="wid-123",
        is_cancelled=lambda: False,
    )
    db.get_document_references.assert_not_called()
    runner.create_or_find_pdf_document.assert_not_called()


@pytest.mark.asyncio
async def test_fetches_internal_pdfs_and_skips_other_doc_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = MagicMock()
    db.get_document_references.return_value = [
        _ref("http://h/a.pdf"),
        _ref("http://h/b.zip", mime="application/zip"),
        _ref("http://h/c.pdf"),
    ]
    db.find_pdf_document_by_url.return_value = None

    fetch = MagicMock(return_value=b"%PDF-1.4 fake")
    monkeypatch.setattr(scraping_job_module, "_fetch_pdf_bytes_sync", fetch)

    runner = _make_runner()
    await promote_discovered_pdfs(
        database=db,
        website=_make_website(),
        pdf_runner=runner,
        website_id="wid-123",
        is_cancelled=lambda: False,
    )

    db.get_document_references.assert_called_once_with(
        "wid-123", internal_only=True
    )
    fetched_urls = [c.args[0] for c in fetch.call_args_list]
    assert fetched_urls == ["http://h/a.pdf", "http://h/c.pdf"]
    assert runner.create_or_find_pdf_document.await_count == 2
    create_kwargs = runner.create_or_find_pdf_document.await_args_list[0].kwargs
    assert create_kwargs["website_id"] == "wid-123"
    assert create_kwargs["project_id"] == "pid-abc"
    assert create_kwargs["source_type"] == "discovered"
    assert create_kwargs["source_url"] == "http://h/a.pdf"
    assert create_kwargs["original_filename"] == "a.pdf"


@pytest.mark.asyncio
async def test_skips_pdfs_already_present_by_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = MagicMock()
    db.get_document_references.return_value = [
        _ref("http://h/a.pdf"),
        _ref("http://h/b.pdf"),
    ]
    db.find_pdf_document_by_url.side_effect = [object(), None]

    fetch = MagicMock(return_value=b"%PDF-1.4 fake")
    monkeypatch.setattr(scraping_job_module, "_fetch_pdf_bytes_sync", fetch)

    runner = _make_runner()
    await promote_discovered_pdfs(
        database=db,
        website=_make_website(),
        pdf_runner=runner,
        website_id="wid-123",
        is_cancelled=lambda: False,
    )

    fetch.assert_called_once()
    assert fetch.call_args.args[0] == "http://h/b.pdf"
    runner.create_or_find_pdf_document.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_errors_are_isolated_per_pdf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = MagicMock()
    db.get_document_references.return_value = [
        _ref("http://h/bad-fetch.pdf"),
        _ref("http://h/not-a-pdf.pdf"),
        _ref("http://h/too-big.pdf"),
        _ref("http://h/good.pdf"),
    ]
    db.find_pdf_document_by_url.return_value = None

    fetch = MagicMock(
        side_effect=[
            FetchFailed("http://h/bad-fetch.pdf", "boom"),
            NotAPdf("nope"),
            PdfTooLarge(size_bytes=999, limit_bytes=10),
            b"%PDF-1.4 fake",
        ]
    )
    monkeypatch.setattr(scraping_job_module, "_fetch_pdf_bytes_sync", fetch)

    runner = _make_runner()
    await promote_discovered_pdfs(
        database=db,
        website=_make_website(),
        pdf_runner=runner,
        website_id="wid-123",
        is_cancelled=lambda: False,
    )

    assert fetch.call_count == 4
    runner.create_or_find_pdf_document.assert_awaited_once()
    create_call = runner.create_or_find_pdf_document.await_args
    assert create_call is not None
    assert create_call.kwargs["source_url"] == "http://h/good.pdf"


@pytest.mark.asyncio
async def test_cancellation_interrupts_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = MagicMock()
    db.get_document_references.return_value = [
        _ref("http://h/a.pdf"),
        _ref("http://h/b.pdf"),
        _ref("http://h/c.pdf"),
    ]
    db.find_pdf_document_by_url.return_value = None

    fetch = MagicMock(return_value=b"%PDF-1.4 fake")
    monkeypatch.setattr(scraping_job_module, "_fetch_pdf_bytes_sync", fetch)

    runner = _make_runner()

    flag = {"calls": 0}

    def is_cancelled() -> bool:
        flag["calls"] += 1
        return flag["calls"] > 1  # cancel after the first iteration

    await promote_discovered_pdfs(
        database=db,
        website=_make_website(),
        pdf_runner=runner,
        website_id="wid-123",
        is_cancelled=is_cancelled,
    )

    assert fetch.call_count == 1
