# Parallel Test Workers Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace serial page testing with a parallel worker pool that runs multiple browser instances concurrently, reducing test time from hours to minutes for large websites.

**Architecture:** N async worker coroutines each own a `TestRunner` (with its own `BrowserManager` / Chromium process) and pull pages from a shared `asyncio.Queue`. Workers are coordinated via `asyncio.gather` inside `TestingJob.run()`. Progress counters are protected by `asyncio.Lock`.

**Tech Stack:** Python asyncio, Playwright (browser automation), MongoDB (PyMongo)

**Spec:** `docs/superpowers/specs/2026-03-31-parallel-test-workers-design.md`

---

## File Structure

| File | Role |
|------|------|
| `config.py` | Add `MAX_TEST_WORKERS` and `WORKER_STAGGER_SECONDS` settings |
| `auto_a11y/testing/test_runner.py` | Fix concurrency hazards: remove redundant page status write, use atomic `$set` for `website.last_tested` |
| `auto_a11y/core/testing_job.py` | Replace serial `for` loop with parallel worker pool |
| `auto_a11y/core/browser_manager.py` | Remove unused `BrowserPool` class |
| `tests/test_parallel_workers.py` | Unit tests for worker pool logic |

---

## Task 1: Add configuration settings

**Files:**
- Modify: `config.py:66-69` (Testing section)

- [ ] **Step 1: Add new config fields**

In `config.py`, add two new fields to the `Config` dataclass in the "Testing" section (after line 69, the `RUN_AI_ANALYSIS` line):

```python
    # Parallel testing workers
    MAX_TEST_WORKERS: int = int(os.getenv('MAX_TEST_WORKERS', 4))
    WORKER_STAGGER_SECONDS: float = float(os.getenv('WORKER_STAGGER_SECONDS', 1.5))
```

- [ ] **Step 2: Verify config loads**

Run: `.venv/bin/python -c "from config import config; print(f'MAX_TEST_WORKERS={config.MAX_TEST_WORKERS}, WORKER_STAGGER_SECONDS={config.WORKER_STAGGER_SECONDS}')"`
Expected: `MAX_TEST_WORKERS=4, WORKER_STAGGER_SECONDS=1.5`

- [ ] **Step 3: Commit**

```bash
git add config.py
git commit -m "feat: add MAX_TEST_WORKERS and WORKER_STAGGER_SECONDS config"
```

---

## Task 2: Fix concurrency hazards in test_runner.py

These fixes must happen BEFORE adding parallelism, since the existing code has race conditions that only become problematic under concurrent execution.

**Files:**
- Modify: `auto_a11y/testing/test_runner.py:73-75` (redundant TESTING status write)
- Modify: `auto_a11y/testing/test_runner.py:501-505` (website.last_tested race in test_page)
- Modify: `auto_a11y/testing/test_runner.py:566-568` (redundant TESTING status write in multi-state)
- Modify: `auto_a11y/testing/test_runner.py:933-937` (website.last_tested race in test_page_multi_state)
- Modify: `auto_a11y/testing/test_runner.py:1115-1117` (website.last_tested race in test_pages)

### 2a: Remove redundant page status write from test_page()

- [ ] **Step 1: Remove the redundant TESTING status write**

In `auto_a11y/testing/test_runner.py`, remove lines 73-75:

```python
        # Update page status
        page.status = PageStatus.TESTING
        self.db.update_page(page)
```

The caller (`testing_job.py`) is now the single owner of page status transitions. It already sets `page.status = PageStatus.TESTING` and calls `database.update_page(page)` before calling `test_page_multi_state`.

### 2b: Remove redundant page status write from test_page_multi_state()

- [ ] **Step 2: Remove the redundant TESTING status write in multi-state**

In `auto_a11y/testing/test_runner.py`, remove lines 566-568:

```python
        # Update page status
        page.status = PageStatus.TESTING
        self.db.update_page(page)
```

Same reasoning: the caller owns page status transitions.

### 2c: Replace website.last_tested full-document replace with atomic $set

- [ ] **Step 3: Change website.last_tested update to atomic operation (all 3 occurrences)**

There are three places in `auto_a11y/testing/test_runner.py` that do a read-modify-write on `website.last_tested`. All three must be changed to atomic `$set`.

**Occurrence 1 — `test_page()` at lines 501-505:**

Replace:
```python
                # Update website's last_tested timestamp
                website = self.db.get_website(page.website_id)
                if website:
                    website.last_tested = datetime.now()
                    self.db.update_website(website)
```

With:
```python
                # Update website's last_tested timestamp atomically
                # (avoids read-modify-write race with parallel workers)
                from bson import ObjectId
                self.db.websites.update_one(
                    {"_id": ObjectId(page.website_id)},
                    {"$set": {"last_tested": datetime.now()}}
                )
```

**Occurrence 2 — `test_page_multi_state()` at lines 933-937:**

Replace:
```python
            # Update website's last_tested timestamp
            website = self.db.get_website(page.website_id)
            if website:
                website.last_tested = datetime.now()
                self.db.update_website(website)
```

With:
```python
            # Update website's last_tested timestamp atomically
            # (avoids read-modify-write race with parallel workers)
            from bson import ObjectId
            self.db.websites.update_one(
                {"_id": ObjectId(page.website_id)},
                {"$set": {"last_tested": datetime.now()}}
            )
```

**Occurrence 3 — `test_pages()` at lines 1115-1117:**

Replace:
```python
        # Update website last_tested
        website.last_tested = datetime.now()
        self.db.update_website(website)
```

With:
```python
        # Update website last_tested timestamp atomically
        from bson import ObjectId
        self.db.websites.update_one(
            {"_id": ObjectId(website._id)},
            {"$set": {"last_tested": datetime.now()}}
        )
```

- [ ] **Step 4: Verify the app still starts and no import errors**

Run: `.venv/bin/python -c "from auto_a11y.testing.test_runner import TestRunner; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add auto_a11y/testing/test_runner.py
git commit -m "fix: remove redundant page status writes and use atomic website.last_tested update

Prevents race conditions when multiple workers test pages concurrently:
- Remove duplicate page.status=TESTING writes (caller owns status transitions)
- Use MongoDB \$set for website.last_tested instead of full document replace"
```

---

## Task 3: Replace serial loop with parallel worker pool

This is the main change. Replace the serial `for` loop in `TestingJob.run()` (lines 340-460) with the worker pool pattern. Also move the `TestRunner` import to module level so it can be mocked in tests.

**Files:**
- Modify: `auto_a11y/core/testing_job.py:273-460` (the `run` method)

- [ ] **Step 1: Move TestRunner import to module level and add PlaywrightError import**

At the top of `auto_a11y/core/testing_job.py`, after the existing imports (line 11), add:

```python
from auto_a11y.testing import TestRunner
from playwright.async_api import Error as PlaywrightError
```

Then remove the local import inside `run()` at line 293:
```python
        from auto_a11y.testing import TestRunner
```

This allows tests to mock `TestRunner` via `patch("auto_a11y.core.testing_job.TestRunner")`.

- [ ] **Step 2: Replace the serial loop with the worker pool**

Replace lines 336-460 of `auto_a11y/core/testing_job.py` (from `# Mark as running` through the end of the `try/except/finally` block) with:

```python
            # Mark as running
            self.set_running(len(testable_pages))
            logger.info(f"Job {self.job_id} marked as running with {len(testable_pages)} pages to test")

            # Determine worker count
            max_workers = browser_config.get('MAX_TEST_WORKERS', 4)
            stagger_seconds = browser_config.get('WORKER_STAGGER_SECONDS', 1.5)
            num_workers = min(max_workers, len(testable_pages))
            logger.info(f"Starting {num_workers} parallel test workers (max configured: {max_workers})")

            # Fill queue with pages to test
            page_queue = asyncio.Queue()
            for page in testable_pages:
                page_queue.put_nowait(page)

            # Shared progress counters protected by lock
            progress_lock = asyncio.Lock()
            progress = {
                'tested': 0,
                'passed': 0,
                'failed': 0,
                'skipped': 0,
            }

            async def _test_worker(worker_id: int):
                """Worker coroutine: owns a TestRunner, pulls pages from queue."""
                runner = None
                try:
                    # Stagger browser launches to avoid thundering herd
                    if worker_id > 0:
                        await asyncio.sleep(worker_id * stagger_seconds)

                    # Check cancellation after stagger wait
                    if self.is_cancelled():
                        logger.info(f"Worker {worker_id}: cancelled before start")
                        return

                    runner = TestRunner(database, browser_config)
                    logger.info(f"Worker {worker_id}: browser started")

                    while not page_queue.empty():
                        # Check for cancellation before each page
                        if self.is_cancelled():
                            logger.info(f"Worker {worker_id}: cancelled")
                            return

                        try:
                            page = page_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            return

                        # Mark page as testing
                        page.status = PageStatus.TESTING
                        database.update_page(page)

                        try:
                            logger.info(f"Worker {worker_id}: testing {page.url}")
                            test_results_list = await runner.test_page_multi_state(
                                page=page,
                                enable_multi_state=True,
                                take_screenshot=take_screenshot,
                                run_ai_analysis=run_ai_analysis,
                                ai_api_key=ai_api_key,
                                website_user_id=self.website_user_id
                            )

                            test_results = test_results_list[-1] if test_results_list else None

                            if test_results:
                                page.status = PageStatus.TESTED
                                is_pass = page.violation_count == 0
                            else:
                                page.status = PageStatus.ERROR
                                is_pass = False

                            del test_results_list
                            del test_results

                            # Update shared progress under lock
                            async with progress_lock:
                                progress['tested'] += 1
                                if is_pass:
                                    progress['passed'] += 1
                                else:
                                    progress['failed'] += 1

                                user_label = self._get_user_label()
                                self.update_progress(
                                    pages_tested=progress['tested'],
                                    total_pages=len(testable_pages),
                                    current_page=page.url,
                                    message=f"[{user_label}] Completed {progress['tested']}/{len(testable_pages)} pages ({num_workers} workers)",
                                    pages_passed=progress['passed'],
                                    pages_failed=progress['failed'],
                                    pages_skipped=progress['skipped'],
                                )

                        except PlaywrightError as e:
                            logger.error(f"Worker {worker_id}: browser error testing {page.url}: {e}")
                            page.status = PageStatus.ERROR
                            page.error_reason = str(e)
                            database.update_page(page)

                            async with progress_lock:
                                progress['failed'] += 1
                                progress['tested'] += 1
                                self.update_progress(
                                    pages_tested=progress['tested'],
                                    total_pages=len(testable_pages),
                                    current_page=page.url,
                                    message=f"[{self._get_user_label()}] Completed {progress['tested']}/{len(testable_pages)} pages (with errors)",
                                    pages_passed=progress['passed'],
                                    pages_failed=progress['failed'],
                                    pages_skipped=progress['skipped'],
                                )

                            # Attempt browser recovery (skip if OOM-like)
                            error_str = str(e).lower()
                            if 'oom' not in error_str and 'out of memory' not in error_str:
                                try:
                                    logger.info(f"Worker {worker_id}: attempting browser recovery")
                                    await runner.cleanup()
                                    runner = TestRunner(database, browser_config)
                                    logger.info(f"Worker {worker_id}: browser recovered")
                                except Exception as recovery_err:
                                    logger.error(f"Worker {worker_id}: recovery failed: {recovery_err}")
                                    return  # Worker exits, others absorb remaining pages
                            else:
                                logger.warning(f"Worker {worker_id}: OOM detected, exiting")
                                return

                        except Exception as e:
                            logger.error(f"Worker {worker_id}: error testing {page.url}: {e}")
                            page.status = PageStatus.ERROR
                            page.error_reason = str(e)
                            database.update_page(page)

                            async with progress_lock:
                                progress['failed'] += 1
                                progress['tested'] += 1
                                self.update_progress(
                                    pages_tested=progress['tested'],
                                    total_pages=len(testable_pages),
                                    current_page=page.url,
                                    message=f"[{self._get_user_label()}] Completed {progress['tested']}/{len(testable_pages)} pages (with errors)",
                                    pages_passed=progress['passed'],
                                    pages_failed=progress['failed'],
                                    pages_skipped=progress['skipped'],
                                )

                except Exception as e:
                    logger.error(f"Worker {worker_id}: fatal error: {e}")
                finally:
                    if runner:
                        try:
                            await runner.cleanup()
                        except Exception as cleanup_err:
                            logger.warning(f"Worker {worker_id}: cleanup error: {cleanup_err}")

            # Launch all workers and wait for completion
            workers = [_test_worker(i) for i in range(num_workers)]
            results = await asyncio.gather(*workers, return_exceptions=True)

            # Log any worker-level exceptions
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"Worker {i} raised exception: {result}")

            # Check final cancellation status
            if self.is_cancelled():
                logger.info(f"Testing job {self.job_id} was cancelled")
                self.set_cancelled()
            elif skip_completion:
                user_label = self._get_user_label()
                logger.info(f"Testing job {self.job_id} finished for user {user_label}, skipping completion (more users pending)")
            else:
                self.set_completed(
                    progress['tested'], progress['passed'],
                    progress['failed'], progress['skipped']
                )

        except Exception as e:
            logger.error(f"Testing job {self.job_id} failed: {e}")
            self.set_failed(str(e))
            raise
```

Note: The old `finally` block that cleaned up a single `test_runner` is removed. Each worker now cleans up its own `TestRunner` in its own `finally` block. The outer `try/except` for job-level failures is preserved.

- [ ] **Step 3: Verify syntax is valid**

Run: `.venv/bin/python -c "from auto_a11y.core.testing_job import TestingJob; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add auto_a11y/core/testing_job.py
git commit -m "feat: replace serial test loop with parallel worker pool

Each worker owns its own TestRunner/BrowserManager (separate Chromium
process). Workers pull pages from a shared asyncio.Queue. Progress
counters protected by asyncio.Lock. Staggered browser startup prevents
thundering herd. Browser recovery on crash (skip on OOM)."
```

---

## Task 4: Remove unused BrowserPool class

**Files:**
- Modify: `auto_a11y/core/browser_manager.py:744-792`

- [ ] **Step 1: Verify BrowserPool is not imported anywhere**

Run: `grep -r "BrowserPool" --include="*.py" /home/tait/Documents/cnib/code/auto_a11y_python/ | grep -v __pycache__ | grep -v ".md"`
Expected: Only the definition in `browser_manager.py` itself (no imports or usages).

- [ ] **Step 2: Remove the BrowserPool class**

Delete lines 744-792 from `auto_a11y/core/browser_manager.py` (the entire `BrowserPool` class, from `class BrowserPool:` to the end of the file).

- [ ] **Step 3: Verify no import errors**

Run: `.venv/bin/python -c "from auto_a11y.core.browser_manager import BrowserManager; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add auto_a11y/core/browser_manager.py
git commit -m "chore: remove unused BrowserPool class

Superseded by the worker pool pattern in TestingJob where each worker
owns its own TestRunner/BrowserManager."
```

---

## Task 5: Write tests for parallel worker logic

**Files:**
- Create: `tests/test_parallel_workers.py`

The tests validate the core worker pool orchestration logic without requiring a real browser or database. We mock `TestRunner` and `Database` to test: queue draining, progress counting, cancellation, error isolation, and staggered startup.

- [ ] **Step 1: Write the test file**

Create `tests/test_parallel_workers.py`:

```python
"""
Tests for parallel worker pool in TestingJob.

These tests mock TestRunner and Database to validate the worker pool
orchestration logic: queue draining, progress counting, cancellation,
error isolation, and worker count capping.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from datetime import datetime

from auto_a11y.core.testing_job import TestingJob
from auto_a11y.models import Page, PageStatus


def _make_page(page_id: str, url: str = "http://example.com") -> Page:
    """Create a mock Page with required fields."""
    page = MagicMock(spec=Page)
    page.id = page_id
    page._id = page_id
    page.url = f"{url}/{page_id}"
    page.status = PageStatus.PENDING
    page.website_id = "website_1"
    page.violation_count = 0
    page.warning_count = 0
    page.info_count = 0
    page.discovery_count = 0
    page.pass_count = 0
    page.error_reason = None
    return page


def _make_test_result():
    """Create a mock TestResult."""
    result = MagicMock()
    result.violation_count = 0
    return result


def _make_job(page_count: int, browser_config: dict = None):
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
async def test_all_pages_tested_with_multiple_workers():
    """All pages should be tested exactly once across workers."""
    pages = [_make_page(f"page_{i}") for i in range(10)]
    job = _make_job(len(pages))

    mock_db = MagicMock()
    mock_db.get_website.return_value = MagicMock(project_id="proj_1")
    mock_db.get_project_user.return_value = None
    mock_db.get_page.side_effect = lambda pid: next(
        (p for p in pages if p.id == pid), None
    )

    tested_urls = []

    async def mock_test_page_multi_state(page, **kwargs):
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
async def test_worker_count_capped_by_page_count():
    """If there are fewer pages than MAX_TEST_WORKERS, use fewer workers."""
    pages = [_make_page(f"page_{i}") for i in range(2)]
    job = _make_job(len(pages))

    mock_db = MagicMock()
    mock_db.get_website.return_value = MagicMock(project_id="proj_1")
    mock_db.get_project_user.return_value = None
    mock_db.get_page.side_effect = lambda pid: next(
        (p for p in pages if p.id == pid), None
    )

    worker_ids_seen = []

    original_runner = None

    async def mock_test_page_multi_state(page, **kwargs):
        return [_make_test_result()]

    browser_config = {"MAX_TEST_WORKERS": 8, "WORKER_STAGGER_SECONDS": 0}

    runners_created = []

    with patch("auto_a11y.core.testing_job.TestRunner") as MockRunner:
        def make_runner(*args, **kwargs):
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
async def test_cancellation_stops_workers():
    """Workers should stop pulling pages when job is cancelled."""
    pages = [_make_page(f"page_{i}") for i in range(20)]
    job = _make_job(len(pages))

    # Cancel after 3 pages
    call_count = 0

    def check_cancelled():
        nonlocal call_count
        call_count += 1
        return call_count > 6  # Each page causes ~2 calls to is_cancelled

    job.job_manager.is_cancellation_requested.side_effect = check_cancelled

    mock_db = MagicMock()
    mock_db.get_website.return_value = MagicMock(project_id="proj_1")
    mock_db.get_project_user.return_value = None
    mock_db.get_page.side_effect = lambda pid: next(
        (p for p in pages if p.id == pid), None
    )

    tested_count = 0

    async def mock_test_page_multi_state(page, **kwargs):
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
async def test_single_page_error_does_not_crash_worker():
    """A page error should not stop the worker from testing remaining pages."""
    pages = [_make_page(f"page_{i}") for i in range(5)]
    job = _make_job(len(pages))

    mock_db = MagicMock()
    mock_db.get_website.return_value = MagicMock(project_id="proj_1")
    mock_db.get_project_user.return_value = None
    mock_db.get_page.side_effect = lambda pid: next(
        (p for p in pages if p.id == pid), None
    )

    tested_urls = []

    async def mock_test_page_multi_state(page, **kwargs):
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
async def test_progress_counts_are_accurate():
    """Progress counters should reflect actual test outcomes."""
    pages = [_make_page(f"page_{i}") for i in range(4)]
    job = _make_job(len(pages))

    mock_db = MagicMock()
    mock_db.get_website.return_value = MagicMock(project_id="proj_1")
    mock_db.get_project_user.return_value = None
    mock_db.get_page.side_effect = lambda pid: next(
        (p for p in pages if p.id == pid), None
    )

    async def mock_test_page_multi_state(page, **kwargs):
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

    # Check the final set_completed call
    completed_call = job.job_manager.update_job_status.call_args
    assert completed_call is not None
    result = completed_call[1].get('result') or completed_call[0][-1] if completed_call[0] else None

    # Verify via the progress details
    progress_calls = [
        call for call in job.job_manager.update_job_progress.call_args_list
    ]
    # The last progress update should have tested=4
    last_progress = progress_calls[-1]
    assert last_progress[1]['details']['pages_tested'] == 4
```

- [ ] **Step 2: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_parallel_workers.py -v`
Expected: All tests pass. If any fail, fix the implementation (not the tests) — the tests describe the correct behavior.

- [ ] **Step 3: Commit**

```bash
git add tests/test_parallel_workers.py
git commit -m "test: add parallel worker pool tests

Tests cover: all-pages-tested, worker count capping, cancellation,
per-page error isolation, and progress counter accuracy."
```

---

## Task 6: Manual integration test

This validates the full flow with a real browser and database.

- [ ] **Step 1: Start the app**

Run: `.venv/bin/python run.py --debug`

- [ ] **Step 2: Test with a small website**

In the web UI:
1. Go to an existing project/website with at least 10 pages
2. Click "Run Untested Pages" (or "Test All Pages")
3. Watch the progress indicator — it should show `(N workers)` in the message
4. Check the Flask console logs — you should see interleaved `Worker 0: testing ...` and `Worker 1: testing ...` messages

- [ ] **Step 3: Test cancellation**

1. Start testing a website with many pages
2. Click "Cancel Testing"
3. Verify the job stops and remaining pages are not marked as ERROR

- [ ] **Step 4: Test with MAX_TEST_WORKERS=1 (regression)**

Set `MAX_TEST_WORKERS=1` in `.env` (or environment), restart the app, and run tests. This should behave identically to the old serial behavior — one worker, one page at a time.

- [ ] **Step 5: Commit (if any fixes needed)**

If fixes were needed during integration testing:

```bash
git add -A
git commit -m "fix: integration test fixes for parallel workers"
```
