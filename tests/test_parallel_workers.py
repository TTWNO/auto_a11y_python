from __future__ import annotations

from typing import Any

"""
Tests for parallel worker pool in TestingJob.

These tests mock TestRunner and Database to validate the worker pool
orchestration logic: queue draining, progress counting, cancellation,
error isolation, and worker count capping.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from auto_a11y.core.testing_job import TestingJob
from auto_a11y.models import Page, PageStatus


def _make_page(page_id: str, url: str = "http://example.com") -> Page:
    """Create a mock Page with required fields."""
    page = MagicMock(spec=Page)
    page.id = page_id
    page._id = page_id
    page.url = f"{url}/{page_id}"
    page.status = PageStatus.DISCOVERED
    page.website_id = "website_1"
    page.violation_count = 0
    page.warning_count = 0
    page.info_count = 0
    page.discovery_count = 0
    page.pass_count = 0
    page.error_reason = None
    return page


def _make_test_result() -> MagicMock:
    """Create a mock TestResult."""
    result = MagicMock()
    result.violation_count = 0
    return result


def _make_job(page_count: int) -> TestingJob:
    """Create a TestingJob with mocked JobManager."""
    job_manager = MagicMock()
    job_manager.get_job.return_value = None
    job_manager.create_job.return_value = {"job_id": "test_job"}
    job_manager.is_cancellation_requested.return_value = False

    job = TestingJob(
        job_manager=job_manager,
        website_id="website_1",
        job_id="test_job",
        page_ids=[f"page_{i}" for i in range(page_count)],
    )
    return job


@pytest.mark.asyncio
async def test_all_pages_tested_with_multiple_workers() -> None:
    """All pages should be tested exactly once across workers."""
    pages = [_make_page(f"page_{i}") for i in range(10)]
    job = _make_job(len(pages))

    mock_db = MagicMock()
    mock_website = MagicMock(project_id="proj_1")
    mock_website.scraping_config.request_delay = 0.0
    mock_db.get_website.return_value = mock_website
    mock_db.get_project_user.return_value = None
    mock_db.get_page.side_effect = lambda pid: next(
        (p for p in pages if p.id == pid), None
    )

    tested_urls = []

    async def mock_test_page_multi_state(page: Any, **kwargs: Any) -> list[MagicMock]:
        tested_urls.append(page.url)
        result = _make_test_result()
        return [result]

    browser_config = {"MAX_TEST_WORKERS": 3, "WORKER_STAGGER_SECONDS": 0}

    with patch("auto_a11y.core.testing_job.TestRunner") as MockRunner:
        instance = AsyncMock()
        instance.test_page_multi_state = mock_test_page_multi_state
        instance.cleanup = AsyncMock()
        MockRunner.return_value = instance

        await job.run(
            database=mock_db,
            browser_config=browser_config,
            take_screenshot=False,
            run_ai_analysis=False,
        )

    # All 10 pages should be tested exactly once
    assert len(tested_urls) == 10
    assert len(set(tested_urls)) == 10  # No duplicates


@pytest.mark.asyncio
async def test_worker_count_capped_by_page_count() -> None:
    """If there are fewer pages than MAX_TEST_WORKERS, use fewer workers."""
    pages = [_make_page(f"page_{i}") for i in range(2)]
    job = _make_job(len(pages))

    mock_db = MagicMock()
    mock_website = MagicMock(project_id="proj_1")
    mock_website.scraping_config.request_delay = 0.0
    mock_db.get_website.return_value = mock_website
    mock_db.get_project_user.return_value = None
    mock_db.get_page.side_effect = lambda pid: next(
        (p for p in pages if p.id == pid), None
    )

    async def mock_test_page_multi_state(page: Any, **kwargs: Any) -> list[MagicMock]:
        return [_make_test_result()]

    browser_config = {"MAX_TEST_WORKERS": 8, "WORKER_STAGGER_SECONDS": 0}

    runners_created = []

    with patch("auto_a11y.core.testing_job.TestRunner") as MockRunner:
        def make_runner(*args: Any, **kwargs: Any) -> AsyncMock:
            instance = AsyncMock()
            instance.test_page_multi_state = mock_test_page_multi_state
            instance.cleanup = AsyncMock()
            runners_created.append(instance)
            return instance

        MockRunner.side_effect = make_runner

        await job.run(
            database=mock_db,
            browser_config=browser_config,
            take_screenshot=False,
            run_ai_analysis=False,
        )

    # Only 2 workers should be created for 2 pages, not 8
    assert len(runners_created) == 2


@pytest.mark.asyncio
async def test_cancellation_stops_workers() -> None:
    """Workers should stop pulling pages when job is cancelled."""
    pages = [_make_page(f"page_{i}") for i in range(20)]
    job = _make_job(len(pages))

    # Cancel after a few is_cancelled checks
    call_count = 0

    def check_cancelled(job_id: str | None = None) -> bool:
        nonlocal call_count
        call_count += 1
        return call_count > 6  # Each page causes ~2 calls to is_cancelled

    job.job_manager.is_cancellation_requested.side_effect = check_cancelled

    mock_db = MagicMock()
    mock_website = MagicMock(project_id="proj_1")
    mock_website.scraping_config.request_delay = 0.0
    mock_db.get_website.return_value = mock_website
    mock_db.get_project_user.return_value = None
    mock_db.get_page.side_effect = lambda pid: next(
        (p for p in pages if p.id == pid), None
    )

    tested_count = 0

    async def mock_test_page_multi_state(page: Any, **kwargs: Any) -> list[MagicMock]:
        nonlocal tested_count
        tested_count += 1
        return [_make_test_result()]

    browser_config = {"MAX_TEST_WORKERS": 2, "WORKER_STAGGER_SECONDS": 0}

    with patch("auto_a11y.core.testing_job.TestRunner") as MockRunner:
        instance = AsyncMock()
        instance.test_page_multi_state = mock_test_page_multi_state
        instance.cleanup = AsyncMock()
        MockRunner.return_value = instance

        await job.run(
            database=mock_db,
            browser_config=browser_config,
            take_screenshot=False,
            run_ai_analysis=False,
        )

    # Should have tested far fewer than 20 pages
    assert tested_count < 20


@pytest.mark.asyncio
async def test_single_page_error_does_not_crash_worker() -> None:
    """A page error should not stop the worker from testing remaining pages."""
    pages = [_make_page(f"page_{i}") for i in range(5)]
    job = _make_job(len(pages))

    mock_db = MagicMock()
    mock_website = MagicMock(project_id="proj_1")
    mock_website.scraping_config.request_delay = 0.0
    mock_db.get_website.return_value = mock_website
    mock_db.get_project_user.return_value = None
    mock_db.get_page.side_effect = lambda pid: next(
        (p for p in pages if p.id == pid), None
    )

    tested_urls = []

    async def mock_test_page_multi_state(page: Any, **kwargs: Any) -> list[MagicMock]:
        tested_urls.append(page.url)
        if "page_2" in page.url:
            raise RuntimeError("Simulated page error")
        return [_make_test_result()]

    browser_config = {"MAX_TEST_WORKERS": 1, "WORKER_STAGGER_SECONDS": 0}

    with patch("auto_a11y.core.testing_job.TestRunner") as MockRunner:
        instance = AsyncMock()
        instance.test_page_multi_state = mock_test_page_multi_state
        instance.cleanup = AsyncMock()
        MockRunner.return_value = instance

        await job.run(
            database=mock_db,
            browser_config=browser_config,
            take_screenshot=False,
            run_ai_analysis=False,
        )

    # All 5 pages should have been attempted, even with the error on page_2
    assert len(tested_urls) == 5


@pytest.mark.asyncio
async def test_progress_counts_are_accurate() -> None:
    """Progress counters should reflect actual test outcomes."""
    pages = [_make_page(f"page_{i}") for i in range(4)]
    job = _make_job(len(pages))

    mock_db = MagicMock()
    mock_website = MagicMock(project_id="proj_1")
    mock_website.scraping_config.request_delay = 0.0
    mock_db.get_website.return_value = mock_website
    mock_db.get_project_user.return_value = None
    mock_db.get_page.side_effect = lambda pid: next(
        (p for p in pages if p.id == pid), None
    )

    async def mock_test_page_multi_state(page: Any, **kwargs: Any) -> list[MagicMock]:
        if "page_1" in page.url:
            raise RuntimeError("fail")
        result = _make_test_result()
        if "page_3" in page.url:
            # Simulate violations
            page.violation_count = 3
        return [result]

    browser_config = {"MAX_TEST_WORKERS": 1, "WORKER_STAGGER_SECONDS": 0}

    with patch("auto_a11y.core.testing_job.TestRunner") as MockRunner:
        instance = AsyncMock()
        instance.test_page_multi_state = mock_test_page_multi_state
        instance.cleanup = AsyncMock()
        MockRunner.return_value = instance

        await job.run(
            database=mock_db,
            browser_config=browser_config,
            take_screenshot=False,
            run_ai_analysis=False,
        )

    # Verify via the progress details
    progress_calls = [
        call for call in job.job_manager.update_job_progress.call_args_list
    ]
    # The last progress update should have tested=4
    last_progress = progress_calls[-1]
    assert last_progress[1]['details']['pages_tested'] == 4
