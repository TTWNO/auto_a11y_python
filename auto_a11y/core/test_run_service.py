"""Service helpers for starting accessibility test runs.

Both the legacy HTML routes (``auto_a11y/web/routes/pages.py``) and the
REST API surface (``auto_a11y/web/routes/api.py``) need to start a
page-level test run, but the legacy code did all of the wiring inline
inside the route handler. That kept the work coupled to the Flask
request/response shape and made the REST endpoint with the same
behaviour impossible to write without duplicating ~90 lines of
event-loop / browser-config / task-runner boilerplate.

This module extracts the *orchestration* step — building the browser
config, queuing the page, submitting the wrapped coroutine to the
task runner — into a small set of typed helpers. The underlying
:class:`TestRunner` implementation, browser config layout, and
multi-state logic are unchanged; this module is purely a callable
re-entry point.

Domain exceptions are raised so the caller can map them to whichever
response shape the surface needs:

- :class:`PageNotFoundError` → 404 on REST, ``flash('not found')`` on HTML
- :class:`BrowserDisabledError` → 503
- :class:`BrowserRemoteError` → 503

The handle returned on success carries the queued ``job_id`` plus the
inputs that affect the run shape, so callers can build their
response (``status_url``, success message, etc.) without re-parsing
the request body.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from auto_a11y.core.task_runner import task_runner
from auto_a11y.models import PageStatus

if TYPE_CHECKING:
    from auto_a11y.core.database import Database
    from config import Config


class TestRunServiceError(Exception):
    """Base for service-layer errors. Callers map these to HTTP status."""


class PageNotFoundError(TestRunServiceError):
    """Raised when the requested page id does not exist."""


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

            async def run_test_with_cleanup() -> list[Any]:
                test_runner_instance = TestRunner(database, browser_config)
                try:
                    if enable_multi_state:
                        return await test_runner_instance.test_page_multi_state(
                            page,
                            enable_multi_state=True,
                            take_screenshot=True,
                            run_ai_analysis=False,
                            ai_api_key=ai_key,
                            website_user_id=website_user_id,
                        )
                    result = await test_runner_instance.test_page(
                        page,
                        take_screenshot=True,
                        run_ai_analysis=False,
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
