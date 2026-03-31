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

Workers start `WORKER_STAGGER_SECONDS` apart (default: 1.5s) to avoid thundering herd on Chromium launch. Small upfront cost, prevents N simultaneous browser spawns competing for CPU.

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

async def worker(worker_id, runner, queue, ...):
    try:
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
        await runner.cleanup()

workers = []
for i in range(num_workers):
    runner = TestRunner(database, browser_config)
    if i > 0:
        await asyncio.sleep(WORKER_STAGGER_SECONDS)
    workers.append(worker(i, runner, queue, ...))

results = await asyncio.gather(*workers, return_exceptions=True)
# Log any worker-level exceptions from results
```

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

- If a worker's browser disconnects mid-test, worker catches `PlaywrightError`, marks current page as `ERROR`, attempts one restart (new `TestRunner`). If restart fails, worker exits.

### Graceful cancellation

- Workers check `self.is_cancelled()` before each page.
- On cancel: workers stop pulling from queue, current in-flight pages finish, then all workers exit.
- Remaining pages in queue keep their pre-test status (not marked as ERROR).

## Files Changed

| File | Change |
|------|--------|
| `auto_a11y/core/testing_job.py` | Replace serial loop (lines 340-460) with worker pool. Add worker function, staggered startup, lock-protected progress. |
| `auto_a11y/core/browser_manager.py` | Remove unused `BrowserPool` class (lines 744-792). |
| `config.py` | Add `MAX_TEST_WORKERS` and `WORKER_STAGGER_SECONDS`. |

### Unchanged files

- `auto_a11y/web/routes/websites.py` — job submission unchanged, parallelism is invisible from this layer.
- `auto_a11y/core/website_manager.py` — no changes.
- `auto_a11y/testing/test_runner.py` — each worker creates its own instance; class works in isolation as-is.
- Web UI progress polling — already works with `update_progress()` calls.
