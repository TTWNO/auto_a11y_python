"""Persistent, user-driven Playwright browser sessions.

Each session holds one visible browser window per website, driven by a human
through the auto_a11y UI. Two flows are supported on top of the same session:

* **Manual discovery** — the user navigates the visible browser (including SPA
  hash routes), then presses a button to capture the current URL/title and add
  it to the website's page list.
* **Manual testing** — once the user has navigated to a state they want to
  test, they press a button to run the existing Playwright test suite against
  whatever is currently on screen, without re-navigating.

Because Flask handlers are synchronous and Playwright is async, each session
runs its own dedicated asyncio loop in a daemon thread. Handlers dispatch
coroutines onto that loop with :func:`asyncio.run_coroutine_threadsafe` and
wait for the result synchronously.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any, TypeVar

from playwright.async_api import BrowserContext, Page as PlaywrightPage

from auto_a11y.core.browser_manager import BrowserManager

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ManualSessionError(RuntimeError):
    """Raised when a manual-session operation fails or is not allowed."""


@dataclass
class ManualSessionInfo:
    """Snapshot of a manual session, safe to serialize for the UI."""

    website_id: str
    started_at: float
    current_url: str
    current_title: str
    is_running: bool


class ManualSession:
    """One persistent visible browser session, dedicated to a single website."""

    _DEFAULT_TIMEOUT: float = 120.0

    def __init__(self, website_id: str, browser_config: dict[str, Any]) -> None:
        self.website_id = website_id
        self._browser_config: dict[str, Any] = dict(browser_config)
        # Piggyback on the proven INTERACTIVE_AUTH_DELAY code path in
        # BrowserManager: any truthy value forces the browser visible AND
        # applies the relaxed-flag / allow-service-workers set, which is the
        # combination known to render protected enterprise SPAs (Dayforce,
        # Microsoft SSO, etc.) correctly. The scraper's separate "wait N
        # seconds for sign-in" logic is never invoked here — manual sessions
        # create their own BrowserManager directly.
        if not self._browser_config.get("INTERACTIVE_AUTH_DELAY_SECONDS", 0):
            self._browser_config["INTERACTIVE_AUTH_DELAY_SECONDS"] = 1
        self._browser_config["BROWSER_HEADLESS"] = False
        self._browser_config["headless"] = False
        # The crawler's default User-Agent identifies itself as
        # "Auto-A11y/1.0 Accessibility Scanner". Enterprise WAFs (Dayforce,
        # anything behind Akamai or Cloudflare bot detection) return an
        # empty 200 to non-browser UAs — the visible window then shows a
        # blank viewport even though Chromium is rendering correctly.
        # Strip both casings so BrowserManager.create_context() falls back
        # to its real Chrome-on-Mac UA, which WAFs accept. The default in
        # config.py was also changed to a real browser UA so headless
        # discovery / testing stops hitting the same WAF stub.
        self._browser_config.pop("USER_AGENT", None)
        self._browser_config.pop("user_agent", None)

        self._loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"manual-session-{website_id}",
            daemon=True,
        )
        self._call_lock = threading.Lock()
        self._browser_manager: BrowserManager | None = None
        self._context: BrowserContext | None = None
        self._page: PlaywrightPage | None = None
        self._started_at: float = time.time()
        self._closed: bool = False
        self._thread.start()

        deadline = time.time() + 2.0
        while not self._loop.is_running() and time.time() < deadline:
            time.sleep(0.01)
        if not self._loop.is_running():
            raise ManualSessionError("Manual session loop failed to start")

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            try:
                pending = asyncio.all_tasks(self._loop)
                for task in pending:
                    task.cancel()
            except Exception:
                pass
            try:
                self._loop.close()
            except Exception:
                pass

    def _submit(
        self,
        coro: Coroutine[Any, Any, T],
        *,
        timeout: float | None = None,
    ) -> T:
        if self._closed:
            raise ManualSessionError("Session is closed")
        future: concurrent.futures.Future[T] = asyncio.run_coroutine_threadsafe(
            coro, self._loop
        )
        result: T = future.result(timeout=timeout or self._DEFAULT_TIMEOUT)
        return result

    def start(self, initial_url: str) -> ManualSessionInfo:
        """Launch the visible browser and navigate to ``initial_url``."""

        async def _start() -> None:
            bm = BrowserManager(self._browser_config)
            await bm.start()
            context = await bm.create_context()
            page = await context.new_page()
            try:
                await page.goto(
                    initial_url, wait_until="domcontentloaded", timeout=60000
                )
            except Exception as e:
                logger.warning(
                    "Manual session initial navigation failed: %s", e
                )
            self._browser_manager = bm
            self._context = context
            self._page = page

        with self._call_lock:
            self._submit(_start())
        return self.info()

    def is_alive(self) -> bool:
        """True if the browser is still running and the page is open."""
        if self._closed or self._page is None or self._browser_manager is None:
            return False

        async def _check() -> bool:
            assert self._page is not None
            return not self._page.is_closed()

        try:
            return bool(self._submit(_check(), timeout=5.0))
        except Exception:
            return False

    def info(self) -> ManualSessionInfo:
        """Return a snapshot of the session's current state."""
        if self._closed or self._page is None:
            return ManualSessionInfo(
                website_id=self.website_id,
                started_at=self._started_at,
                current_url="",
                current_title="",
                is_running=False,
            )

        async def _info() -> tuple[str, str]:
            assert self._page is not None
            return self._page.url, await self._page.title()

        try:
            url, title = self._submit(_info(), timeout=10.0)
        except Exception as e:
            logger.warning("Manual session info failed: %s", e)
            return ManualSessionInfo(
                website_id=self.website_id,
                started_at=self._started_at,
                current_url="",
                current_title="",
                is_running=False,
            )
        return ManualSessionInfo(
            website_id=self.website_id,
            started_at=self._started_at,
            current_url=url,
            current_title=title,
            is_running=True,
        )

    def run_with_page(
        self,
        func: Callable[[PlaywrightPage], Awaitable[T]],
        *,
        timeout: float = 300.0,
    ) -> T:
        """Run a coroutine in this session's loop with the live Playwright page.

        Used by manual testing to invoke the test suite against the visible
        browser page without re-navigating.
        """
        if self._closed or self._page is None:
            raise ManualSessionError("Session is not running")

        async def _wrapped() -> T:
            assert self._page is not None
            return await func(self._page)

        with self._call_lock:
            return self._submit(_wrapped(), timeout=timeout)

    def stop(self) -> None:
        """Close the visible browser and tear down the session."""
        if self._closed:
            return

        async def _stop() -> None:
            if self._browser_manager is not None:
                await self._browser_manager.stop()

        with self._call_lock:
            try:
                self._submit(_stop(), timeout=30.0)
            except Exception as e:
                logger.warning(
                    "Error stopping manual session for %s: %s",
                    self.website_id,
                    e,
                )
            self._closed = True
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5.0)


_sessions: dict[str, ManualSession] = {}
_registry_lock = threading.Lock()


def get_session(website_id: str) -> ManualSession | None:
    with _registry_lock:
        session = _sessions.get(website_id)
    if session is not None and not session.is_alive():
        with _registry_lock:
            _sessions.pop(website_id, None)
        return None
    return session


def start_session(
    website_id: str, initial_url: str, browser_config: dict[str, Any]
) -> ManualSessionInfo:
    """Start (or restart) a manual session for ``website_id``."""
    with _registry_lock:
        existing = _sessions.pop(website_id, None)
    if existing is not None:
        try:
            existing.stop()
        except Exception:
            pass

    session = ManualSession(website_id, browser_config)
    info = session.start(initial_url)
    with _registry_lock:
        _sessions[website_id] = session
    return info


def stop_session(website_id: str) -> bool:
    """Stop the manual session for ``website_id``. Returns True if one existed."""
    with _registry_lock:
        session = _sessions.pop(website_id, None)
    if session is None:
        return False
    session.stop()
    return True


def stop_all_sessions() -> None:
    """Stop every running manual session. Intended for process shutdown."""
    with _registry_lock:
        sessions = list(_sessions.values())
        _sessions.clear()
    for session in sessions:
        try:
            session.stop()
        except Exception as e:
            logger.warning("Error stopping manual session: %s", e)
