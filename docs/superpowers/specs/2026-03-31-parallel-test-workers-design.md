# Parallel Test Workers Design

**Date:** 2026-03-31
**Status:** Approved
**Problem:** Website tests run serially — hundreds of pages take hours because each page waits for the previous one to finish.
**Solution:** Multiple browser instances pulling pages from a shared queue, tested in parallel.

## Architecture

### Worker Pool Pattern

`TestingJob.run()` replaces its serial `for` loop with a pool of async workers, each owning an independent `TestRunner` (which owns a `BrowserManager` → Chromium process).

```
TestingJob.run()
  ├── Fill asyncio.Queue with testable pages
  └── Spawn N worker coroutines via asyncio.gather()
        ├── Worker 0: TestRunner (own BrowserManager) → pulls from Queue
        ├── Worker 1: TestRunner (own BrowserManager) → pulls from Queue
        ├── ...
        └── Worker N-1: TestRunner (own BrowserManager) → pulls from Queue
```

Each worker loops: pull page from queue → test it → update progress → repeat until queue is empty.

### Worker Count

```python
num_workers = min(MAX_TEST_WORKERS, len(testable_pages))
```

- `MAX_TEST_WORKERS` defaults to 4, configurable in `config.py` / `.env`.
- No point spawning more workers than pages.

### Staggered Startup

Workers stagger their browser launch by sleeping `worker_id * WORKER_STAGGER_SECONDS` at the top of their body. This is inside the worker coroutine so that `asyncio.gather` starts all workers immediately but each waits its turn before launching Chromium. Default: 1.5s between launches.

## Integration with TestingJob

### Current (serial)

```python
# testing_job.py:340-460
test_runner = TestRunner(database, browser_config)
for i, page in enumerate(testable_pages):
    await test_runner.test_page_multi_state(page, ...)
    await asyncio.sleep(0.5)
```

### New (parallel)

```python
queue = asyncio.Queue()
for page in testable_pages:
    queue.put_nowait(page)

async def worker(worker_id, queue, ...):
    runner = None
    try:
        # Stagger browser launches to avoid thundering herd
        if worker_id > 0:
            await asyncio.sleep(worker_id * WORKER_STAGGER_SECONDS)
        # Check cancellation after stagger wait
        if self.is_cancelled():
            return

        runner = TestRunner(database, browser_config)

        while not queue.empty():
            if self.is_cancelled():
                return
            try:
                page = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            # Mark page as TESTING, test it, update progress
            # Per-page try/except for error isolation
            ...
    finally:
        if runner:
            await runner.cleanup()

workers = [worker(i, queue, ...) for i in range(num_workers)]
results = await asyncio.gather(*workers, return_exceptions=True)
# Log any worker-level exceptions from results
```

**Removed:** The `await asyncio.sleep(0.5)` inter-page delay from the serial loop is intentionally dropped. Each worker has its own browser, so there is no need to let a shared browser "stabilize" between pages.

### Progress Tracking

Shared counters (`pages_tested`, `pages_passed`, `pages_failed`, `pages_skipped`) protected by `asyncio.Lock`. Each worker increments atomically and calls `self.update_progress()`.

Progress messages change from:
- "Testing page 5/100" → "Testing pages... 5/100 completed (4 workers)"

## Multi-User Testing

No changes to the multi-user flow. In `websites.py`, users are still tested sequentially (one pass per user). The parallelism is within each user's page set.

Each worker logs in independently in its own browser. Login cost is paid once per worker per user (not per page), since `TestRunner` reuses sessions across pages — same as today.

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `MAX_TEST_WORKERS` | `4` | Parallel browser instances per test job |
| `WORKER_STAGGER_SECONDS` | `1.5` | Delay between worker startups |

No separate AI concurrency setting needed — AI calls are gated by worker count (one page at a time per worker).

### Resource estimates

- Each Chromium instance: ~150-300MB baseline
- 4 workers: ~0.6-1.2GB total browser overhead
- 8 workers: ~1.2-2.4GB total browser overhead
- Target machine (16 threads, 64GB RAM): can comfortably run 8+ workers

## Error Handling and Resilience

### Per-worker crash isolation

- `asyncio.gather(*workers, return_exceptions=True)` — one worker's browser crash doesn't kill others.
- Surviving workers absorb remaining pages from the shared queue.
- Crashed worker's `TestRunner.cleanup()` called in `finally` block.

### Per-page error isolation (unchanged from today)

- Each page test wrapped in try/except within worker loop.
- On failure: page status → `ERROR`, `pages_failed` incremented, worker moves to next page.

### Browser recovery

- If a worker's browser disconnects mid-test (e.g., crash, not OOM), worker catches `PlaywrightError`, marks current page as `ERROR`, attempts one restart (new `TestRunner`). If restart fails, worker exits.
- Recovery is skipped if the failure looks like resource exhaustion (e.g., Chromium killed by OOM) — restarting would likely fail and worsen the situation.

### Graceful cancellation

- Workers check `self.is_cancelled()` before each page.
- On cancel: workers stop pulling from queue, current in-flight pages finish, then all workers exit.
- Remaining pages in queue keep their pre-test status (not marked as ERROR).

## Concurrency Hazards to Fix

### 1. `website.last_tested` lost-update race (Medium)

`test_runner.py` does a read-modify-write on the website document after each page test:
```python
website = self.db.get_website(page.website_id)
website.last_tested = datetime.now()
self.db.update_website(website)  # replace_one — replaces entire document
```
With parallel workers, one worker's `get_website()` reads stale data, then `replace_one` overwrites the other's update. **Fix:** Change `test_runner.py` to use an atomic `$set` operation for `last_tested` instead of a full document replace.

### 2. Redundant page status writes (Medium)

Both `testing_job.py` (the worker loop) and `test_runner.test_page()` set `page.status = TESTING` and call `database.update_page()` (full `replace_one`). With parallel workers, two concurrent `replace_one` calls on different pages are fine, but the redundant write within `test_runner.test_page()` could clobber fields updated by the worker loop. **Fix:** Remove the redundant `page.status = TESTING` + `update_page()` from `test_runner.test_page()` — the worker loop in `testing_job.py` is the single owner of page status transitions.

### 3. AI analysis memory (Low)

With N workers doing AI concurrently, N screenshots are held in memory simultaneously (~1-5MB each). At 4-8 workers this is negligible (~40MB max), but worth noting. No fix needed.

## Files Changed

| File | Change |
|------|--------|
| `auto_a11y/core/testing_job.py` | Replace serial loop (lines 340-460) with worker pool. Add worker function, staggered startup, lock-protected progress. |
| `auto_a11y/core/browser_manager.py` | Remove unused `BrowserPool` class (lines 744-792). |
| `auto_a11y/testing/test_runner.py` | Remove redundant `page.status = TESTING` + `update_page()` (concurrency hazard #2). Change `website.last_tested` update to atomic `$set` (concurrency hazard #1). |
| `config.py` | Add `MAX_TEST_WORKERS` and `WORKER_STAGGER_SECONDS`. |

### Unchanged files

- `auto_a11y/web/routes/websites.py` — job submission unchanged, parallelism is invisible from this layer.
- `auto_a11y/core/website_manager.py` — no changes.
- Web UI progress polling — already works with `update_progress()` calls.
