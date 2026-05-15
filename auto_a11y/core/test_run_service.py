"""Service helpers for starting accessibility test runs and discoveries.

Both the legacy HTML routes (``auto_a11y/web/routes/pages.py``,
``auto_a11y/web/routes/websites.py``) and the REST API surface
(``auto_a11y/web/routes/api.py``) need to queue background work, but
the legacy code did all of the wiring inline inside each route
handler. That kept the work coupled to the Flask request/response
shape and made REST endpoints with the same behaviour impossible to
write without duplicating dozens of lines of event-loop /
browser-config / task-runner boilerplate.

This module extracts the *orchestration* step — building the browser
config, transitioning the resource state, submitting the wrapped
coroutine to the task runner — into a small set of typed helpers.
The underlying :class:`TestRunner` and :class:`WebsiteManager`
implementations are unchanged; this module is purely a callable
re-entry point.

Domain exceptions are raised so the caller can map them to whichever
response shape the surface needs:

- :class:`PageNotFoundError`     → 404 on REST, ``flash('not found')`` on HTML
- :class:`WebsiteNotFoundError`  → 404
- :class:`BrowserDisabledError`  → 503
- :class:`BrowserRemoteError`    → 503

The handles returned on success carry the queued ``job_id`` plus the
inputs that affect the run shape, so callers can build their
response (``status_url``, success message, etc.) without re-parsing
the request body.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from auto_a11y.core.task_runner import task_runner
from auto_a11y.models import PageStatus

if TYPE_CHECKING:
    from auto_a11y.core.database import Database
    from auto_a11y.testing.pdf_runner import PdfRunner
    from config import Config


class TestRunServiceError(Exception):
    """Base for service-layer errors. Callers map these to HTTP status."""


class PageNotFoundError(TestRunServiceError):
    """Raised when the requested page id does not exist."""


class WebsiteNotFoundError(TestRunServiceError):
    """Raised when the requested website id does not exist."""


class NoPagesToTestError(TestRunServiceError):
    """Raised when a website has no pages eligible for a test run.

    Carries the requested ``untested_only`` flag so callers can render
    different messages for "no pages discovered yet" vs "all already
    tested".
    """

    def __init__(self, website_id: str, *, untested_only: bool) -> None:
        super().__init__(f"website {website_id} has no testable pages")
        self.website_id = website_id
        self.untested_only = untested_only


class BrowserDisabledError(TestRunServiceError):
    """Raised when ``BROWSER_MODE='disabled'``.

    The server has opted out of running its own browser; callers
    should surface a 503 so clients can fall back to a different
    backend or surface a clear error.
    """


class BrowserRemoteError(TestRunServiceError):
    """Raised when ``BROWSER_MODE='remote'``.

    Distinct from :class:`BrowserDisabledError` because the remediation
    is different — a remote-browser deployment expects callers to use
    the remote-runner orchestration path, not the local one.
    """


@dataclass(frozen=True)
class PageTestRunHandle:
    """Result of :func:`start_page_test_run`.

    Attributes:
        job_id: The task-runner id of the queued background test.
        page_id: Echoed for caller convenience.
        multi_state: Whether multi-state testing was requested for the
            run. Surfaced so callers can include it in their response
            envelope without re-reading the input.
    """

    job_id: str
    page_id: str
    multi_state: bool


@dataclass(frozen=True)
class WebsiteTestRunHandle:
    """Result of :func:`start_website_test_run`.

    Attributes:
        job_id: The task-runner id of the queued background test job.
        website_id: Echoed for caller convenience.
        pages_queued: Number of pages that will be tested per user.
        user_count: Number of distinct project users the run will
            test as (``[''] guest`` counts as 1).
        total_tests: ``pages_queued * user_count`` — the aggregate
            test count the UI shows.
    """

    job_id: str
    website_id: str
    pages_queued: int
    user_count: int
    total_tests: int


@dataclass(frozen=True)
class WebsiteDiscoveryHandle:
    """Result of :func:`start_website_discovery`.

    Attributes:
        job_id: The task-runner id of the queued background crawl.
        website_id: Echoed for caller convenience.
        max_pages: Optional crawl cap (``None`` for unbounded).
        user_count: Number of distinct project users the crawl will
            run as (``[''] guest`` counts as 1).
    """

    job_id: str
    website_id: str
    max_pages: int | None
    user_count: int


def _build_browser_config(
    app_config: Config, project_config: dict[str, Any] | None
) -> dict[str, Any]:
    """Merge the runtime app config with any project-level overrides.

    Mirrors the inline merge from ``pages.py``: project-level
    ``stealth_mode`` and ``headless_browser`` always win when present
    on the project; otherwise the runtime defaults apply.

    Returns a plain dict (not a Config object) because the
    :class:`TestRunner` consumers read both attribute-style and
    dict-style keys from this value.
    """
    browser_config = app_config.__dict__.copy()
    if project_config:
        browser_config["stealth_mode"] = project_config.get(
            "stealth_mode", False
        )
        headless_setting = project_config.get("headless_browser", "true")
        browser_config["BROWSER_HEADLESS"] = headless_setting == "true"
    return browser_config


def start_page_test_run(
    database: Database,
    app_config: Config,
    page_id: str,
    *,
    enable_multi_state: bool = True,
    website_user_id: str | None = None,
    take_screenshot: bool | None = None,
    run_ai_analysis: bool | None = None,
) -> PageTestRunHandle:
    """Queue an accessibility test run for a single page.

    Args:
        database: The shared :class:`Database` instance for the app.
        app_config: The shared :class:`Config` — read once for
            ``BROWSER_MODE``, ``CLAUDE_API_KEY``, and the headless /
            stealth merge inputs.
        page_id: The page to test.
        enable_multi_state: When true (default), the run uses
            ``TestRunner.test_page_multi_state``; when false, the
            legacy single-state path.
        website_user_id: Optional authenticated-user credential for
            login-required pages.
        take_screenshot: Per-run override for screenshot capture.
            ``None`` (default) keeps the historical behaviour
            (``True``); callers that want to disable screenshots for a
            specific run pass ``False`` explicitly.
        run_ai_analysis: Per-run override for Claude-AI visual
            analysis. ``None`` (default) keeps the historical
            behaviour (``False``); pass ``True`` to opt in for this
            run when the project has an AI key configured.

    Returns:
        :class:`PageTestRunHandle` carrying the queued job id.

    Raises:
        PageNotFoundError: when ``page_id`` does not resolve.
        BrowserDisabledError: when ``BROWSER_MODE='disabled'``.
        BrowserRemoteError: when ``BROWSER_MODE='remote'``.
    """
    browser_mode = getattr(app_config, "BROWSER_MODE", "local")
    if browser_mode == "disabled":
        raise BrowserDisabledError(
            "Browser testing is disabled on this server (BROWSER_MODE=disabled)"
        )
    if browser_mode == "remote":
        raise BrowserRemoteError(
            "This server is configured for remote browser execution"
        )

    page = database.get_page(page_id)
    if page is None:
        raise PageNotFoundError(f"page {page_id} not found")

    page.status = PageStatus.QUEUED
    database.update_page(page)

    website = database.get_website(page.website_id)
    project = (
        database.get_project(website.project_id) if website is not None else None
    )
    project_config = project.config if project is not None else None
    browser_config = _build_browser_config(app_config, project_config)
    ai_key = getattr(app_config, "CLAUDE_API_KEY", None)

    def run_test_sync() -> list[Any]:
        # Lazy import: TestRunner imports Playwright at module load,
        # which is expensive and unwanted in callers that only ever
        # queue work (e.g. the REST handler in a unit test).
        from auto_a11y.testing import TestRunner

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:

            screenshot_flag = True if take_screenshot is None else take_screenshot
            ai_flag = False if run_ai_analysis is None else run_ai_analysis

            async def run_test_with_cleanup() -> list[Any]:
                test_runner_instance = TestRunner(database, browser_config)
                try:
                    if enable_multi_state:
                        return await test_runner_instance.test_page_multi_state(
                            page,
                            enable_multi_state=True,
                            take_screenshot=screenshot_flag,
                            run_ai_analysis=ai_flag,
                            ai_api_key=ai_key,
                            website_user_id=website_user_id,
                        )
                    result = await test_runner_instance.test_page(
                        page,
                        take_screenshot=screenshot_flag,
                        run_ai_analysis=ai_flag,
                        ai_api_key=ai_key,
                        website_user_id=website_user_id,
                    )
                    return [result]
                finally:
                    await test_runner_instance.cleanup()

            return loop.run_until_complete(run_test_with_cleanup())
        finally:
            loop.close()

    job_id = task_runner.submit_task(
        func=run_test_sync,
        args=(),
        task_id=f"test_page_{page_id}_{datetime.now().timestamp()}",
    )
    return PageTestRunHandle(
        job_id=job_id, page_id=page_id, multi_state=enable_multi_state
    )


def _normalize_user_ids(raw: list[str] | str | None) -> list[str]:
    """Coerce the legacy ``project_user_ids`` shape to a list.

    The HTML form accepts either a single id or a list; an empty list
    means "guest only" — the empty-string sentinel preserved here
    matches what WebsiteManager.discover_pages expects.
    """
    if raw is None:
        return [""]
    if isinstance(raw, str):
        return [raw] if raw else [""]
    if not raw:
        return [""]
    return list(raw)


def start_website_discovery(
    database: Database,
    app_config: Config,
    website_id: str,
    *,
    max_pages: int | None = None,
    project_user_ids: list[str] | str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    pdf_runner: PdfRunner | None = None,
) -> WebsiteDiscoveryHandle:
    """Queue a page-discovery crawl for a website.

    Args:
        database: The shared :class:`Database` instance.
        app_config: The shared :class:`Config` — read for the runtime
            browser settings that get merged with the project-level
            overrides.
        website_id: The website to crawl.
        max_pages: Cap on the number of pages to discover. ``None`` or
            a non-positive value means unbounded.
        project_user_ids: Project-level test users to crawl as. May
            be a single id, a list, an empty list, or ``None``;
            anything empty means "guest only".
        user_id: Optional app-user id for job-attribution.
        session_id: Optional Flask session id for job-attribution.
        pdf_runner: Optional :class:`PdfRunner` to pass through to
            :class:`WebsiteManager` so PDFs found during the crawl
            are auto-fetched into the PDFs UI.

    Returns:
        :class:`WebsiteDiscoveryHandle` carrying the queued job id.

    Raises:
        WebsiteNotFoundError: when ``website_id`` does not resolve.

    Note:
        Unlike :func:`start_page_test_run`, this function does *not*
        guard against ``BROWSER_MODE='disabled'`` / ``'remote'`` —
        the legacy ``websites.py`` discovery handler doesn't either
        (it relies on ``BrowserManager`` to fail with a clearer error
        at launch time). Keeping the parity is intentional so the
        REST and HTML surfaces behave identically.
    """
    website = database.get_website(website_id)
    if website is None:
        raise WebsiteNotFoundError(f"website {website_id} not found")

    project = database.get_project(website.project_id)
    project_config = project.config if project is not None else None
    browser_config = _build_browser_config(app_config, project_config)
    if project_config is None:
        # Match the legacy default — ``stealth_mode`` is set explicitly
        # to False on the "no project config" path.
        browser_config["stealth_mode"] = False

    user_ids_list = _normalize_user_ids(project_user_ids)
    capped_max_pages = (
        max_pages if max_pages is not None and max_pages > 0 else None
    )

    task_id = f"discovery_{website_id}_{uuid.uuid4().hex[:8]}"

    def discovery_wrapper() -> object:
        # Lazy imports keep WebsiteManager + nest_asyncio off the
        # import path of callers that only ever queue work (e.g. the
        # REST handler in a unit test).
        from auto_a11y.core.website_manager import WebsiteManager
        import nest_asyncio

        nest_asyncio.apply()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        website_manager = WebsiteManager(
            database, browser_config, pdf_runner=pdf_runner
        )
        try:
            return loop.run_until_complete(
                website_manager.discover_pages(
                    website_id,
                    max_pages=capped_max_pages,
                    job_id=task_id,
                    user_id=user_id,
                    session_id=session_id,
                    website_user_ids=user_ids_list,
                )
            )
        finally:
            try:
                if not loop.is_running():
                    loop.close()
            except Exception:
                pass

    submitted_id = task_runner.submit_task(
        func=discovery_wrapper, args=(), task_id=task_id
    )
    return WebsiteDiscoveryHandle(
        job_id=submitted_id,
        website_id=website_id,
        max_pages=capped_max_pages,
        user_count=len(user_ids_list),
    )


def start_website_test_run(
    database: Database,
    app_config: Config,
    website_id: str,
    *,
    project_user_ids: list[str] | str | None = None,
    max_pages: int | None = None,
    untested_only: bool = False,
    user_id: str | None = None,
    session_id: str | None = None,
    pdf_runner: PdfRunner | None = None,
    take_screenshot: bool | None = None,
    run_ai_analysis: bool | None = None,
) -> WebsiteTestRunHandle:
    """Queue a batch accessibility test run for every page on a website.

    The submitted task processes the configured project-users
    *sequentially* (concurrent browser sessions corrupt each other),
    then queues PDF audits *after* the HTML tests finish (running
    them concurrently with Playwright deadlocks on the per-request
    ``nest_asyncio`` patched loop). Combined progress is streamed
    into the same JobManager record so the UI's existing poller sees
    a single percentage.

    Args:
        database: The shared :class:`Database` instance.
        app_config: The shared :class:`Config`.
        website_id: The website to test.
        project_user_ids: Project users to test as. ``None`` / empty
            collapses to ``[''] guest``.
        max_pages: Optional cap on number of pages per user.
        untested_only: When true, skip pages already in the TESTED
            state — useful for "retry the rest" flows.
        user_id / session_id: Optional ids for JobManager attribution.
        pdf_runner: Optional :class:`PdfRunner` — when present, PDF
            audits are queued after the HTML page tests complete and
            their progress is folded into the same job's totals.

    Returns:
        :class:`WebsiteTestRunHandle` carrying the queued job id.

    Raises:
        WebsiteNotFoundError: when ``website_id`` does not resolve.
        NoPagesToTestError: when the website has no eligible pages.
    """
    website = database.get_website(website_id)
    if website is None:
        raise WebsiteNotFoundError(f"website {website_id} not found")

    project = database.get_project(website.project_id)
    project_config = project.config if project is not None else None
    browser_config = _build_browser_config(app_config, project_config)
    if project_config is None:
        browser_config["stealth_mode"] = False
    ai_key = getattr(app_config, "CLAUDE_API_KEY", None)

    user_ids_list = _normalize_user_ids(project_user_ids)

    pages = database.get_pages(website_id, latest_only=False, limit=0)
    testable_pages = [p for p in pages if p.status != PageStatus.TESTING]
    if untested_only:
        testable_pages = [
            p for p in testable_pages if p.status != PageStatus.TESTED
        ]
    if max_pages is not None and max_pages > 0:
        testable_pages = testable_pages[:max_pages]
    if not testable_pages:
        raise NoPagesToTestError(website_id, untested_only=untested_only)

    page_ids = [p.id for p in testable_pages if p.id is not None]
    pages_queued = len(testable_pages)
    user_count = len(user_ids_list)
    total_tests = pages_queued * user_count
    job_id = f"testing_{website_id}_{uuid.uuid4().hex[:8]}"

    def testing_wrapper() -> object:
        # Lazy imports — see :func:`start_page_test_run` rationale.
        from auto_a11y.core.job_manager import JobStatus
        from auto_a11y.core.website_manager import WebsiteManager
        import nest_asyncio

        nest_asyncio.apply()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        website_manager = WebsiteManager(database, browser_config)
        last_result: Any = None
        try:
            for current_user_id in user_ids_list:
                user_arg = current_user_id if current_user_id else None
                try:
                    last_result = loop.run_until_complete(
                        website_manager.test_website(
                            website_id=website_id,
                            page_ids=page_ids,
                            job_id=job_id,
                            user_id=user_id,
                            session_id=session_id,
                            test_all=False,
                            take_screenshot=(
                                True if take_screenshot is None else take_screenshot
                            ),
                            run_ai_analysis=run_ai_analysis,
                            ai_api_key=ai_key,
                            website_user_id=user_arg,
                            skip_completion=True,
                        )
                    )
                except Exception as user_error:
                    # Continue with the next user even if one fails —
                    # matches the legacy behaviour.
                    import logging
                    logging.getLogger(__name__).error(
                        "Error testing user %s: %s",
                        current_user_id or "guest",
                        user_error,
                    )

            pdf_audit_job_ids: list[str] = []
            if pdf_runner is not None:
                from auto_a11y.core.pdf_audit_job import (
                    queue_audits_for_website,
                )

                try:
                    pdf_audit_job_ids = queue_audits_for_website(
                        runner=pdf_runner,
                        db=database,
                        website_id=website_id,
                        user_id=str(user_id) if user_id else "anonymous",
                        session_id=str(session_id) if session_id else None,
                    )
                except Exception as audit_err:
                    import logging
                    logging.getLogger(__name__).warning(
                        "Failed to queue PDF audits for website %s: %s",
                        website_id,
                        audit_err,
                    )

            jm = website_manager.job_manager

            def _read_int(
                record: dict[str, Any] | None, key: str, default: int
            ) -> int:
                if record is None:
                    return default
                progress_obj: Any = record.get("progress")
                if not isinstance(progress_obj, dict):
                    return default
                progress_dict = cast(dict[str, Any], progress_obj)
                details_obj: Any = progress_dict.get("details")
                if not isinstance(details_obj, dict):
                    return default
                details_dict = cast(dict[str, Any], details_obj)
                value: Any = details_dict.get(key, default)
                if isinstance(value, int):
                    return value
                if isinstance(value, str):
                    try:
                        return int(value)
                    except ValueError:
                        return default
                return default

            page_record = jm.get_job(job_id)
            pages_tested_final = _read_int(
                page_record, "pages_tested", len(page_ids)
            )
            pages_total_final = _read_int(
                page_record, "total_pages", len(page_ids)
            )
            page_details: dict[str, Any] = {
                "pages_tested": pages_tested_final,
                "total_pages": pages_total_final,
            }
            pdf_total = len(pdf_audit_job_ids)
            combined_total = pages_total_final + pdf_total
            terminal_states = {"completed", "failed", "cancelled"}

            if pdf_audit_job_ids:
                import time

                while True:
                    done = 0
                    for aid in pdf_audit_job_ids:
                        rec = jm.get_job(aid)
                        if rec and rec.get("status") in terminal_states:
                            done += 1
                    combined_done = pages_tested_final + done
                    jm.update_job_status(
                        job_id=job_id,
                        status=JobStatus.RUNNING,
                        progress={
                            "current": combined_done,
                            "total": combined_total,
                            "message": f"Auditing PDFs: {done}/{pdf_total}",
                            "details": {
                                **page_details,
                                "pages_tested": combined_done,
                                "total_pages": combined_total,
                                "pdf_audits_done": done,
                                "pdf_audits_total": pdf_total,
                            },
                        },
                    )
                    if done >= pdf_total:
                        break
                    time.sleep(2)

            jm.update_job_status(
                job_id=job_id,
                status=JobStatus.COMPLETED,
                progress={
                    "current": combined_total,
                    "total": combined_total,
                    "message": (
                        f"Testing complete: {pages_total_final} "
                        f"page(s), {pdf_total} PDF(s)"
                    ),
                    "details": {
                        **page_details,
                        "pages_tested": combined_total,
                        "total_pages": combined_total,
                        "pdf_audits_done": pdf_total,
                        "pdf_audits_total": pdf_total,
                    },
                },
            )
            return last_result
        finally:
            try:
                if not loop.is_running():
                    loop.close()
            except Exception:
                pass

    submitted_id = task_runner.submit_task(
        func=testing_wrapper, args=(), task_id=job_id
    )
    return WebsiteTestRunHandle(
        job_id=submitted_id,
        website_id=website_id,
        pages_queued=pages_queued,
        user_count=user_count,
        total_tests=total_tests,
    )
