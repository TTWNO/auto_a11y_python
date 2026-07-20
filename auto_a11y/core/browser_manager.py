"""
Browser management using Playwright

This module provides a Playwright-based browser manager that implements the same
interface as the Pyppeteer-based BrowserManager, but with improved stability
for multi-state testing and authenticated user scenarios.

Key improvements over Pyppeteer:
- Browser context isolation for clean test states
- Built-in auto-waiting reduces timing issues
- Storage state API for authentication persistence
- More stable connection handling
- Active maintenance by Microsoft
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncGenerator
from typing import Any, Literal
from pathlib import Path
import logging
from contextlib import asynccontextmanager

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    Response,
    Playwright,
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError
)

logger = logging.getLogger(__name__)


def _ensure_playwright_browsers_path() -> None:
    """
    Set PLAYWRIGHT_BROWSERS_PATH so Playwright can find browsers installed
    inside the source tree (needed on Render where only the source dir persists).
    """
    if os.environ.get('PLAYWRIGHT_BROWSERS_PATH'):
        return  # Already set externally

    # Check for .playwright inside the project root (next to config.py)
    project_root = Path(__file__).resolve().parent.parent.parent  # auto_a11y/core/ -> project root
    local_browsers = project_root / '.playwright'
    if local_browsers.is_dir():
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(local_browsers)
        logger.info(f"Set PLAYWRIGHT_BROWSERS_PATH={local_browsers}")


WaitUntilType = Literal['commit', 'domcontentloaded', 'load', 'networkidle']
SelectorStateType = Literal['attached', 'detached', 'hidden', 'visible']


class BrowserManager:
    """
    Manages Playwright browser instances with context isolation.

    This class provides the same interface as the Pyppeteer BrowserManager
    but uses Playwright for improved stability, especially for:
    - Multi-state testing
    - Authenticated user testing
    - Multiple viewport changes
    - Script injection scenarios
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """
        Initialize browser manager

        Args:
            config: Browser configuration dictionary with keys:
                - headless: Run in headless mode (default: True)
                - timeout: Default timeout in ms (default: 60000)
                - viewport_width: Viewport width (default: 1920)
                - viewport_height: Viewport height (default: 1080)
                - user_agent: User agent string
                - stealth_mode: Apply anti-detection measures (default: False)
                - max_concurrent_pages: Max concurrent pages (default: 5)
        """
        self.config = config
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._default_context: BrowserContext | None = None
        self._contexts: list[BrowserContext] = []
        self._pages: list[Page] = []
        self._semaphore = asyncio.Semaphore(config.get('max_concurrent_pages', 5))
        self._start_lock = asyncio.Lock()
        # Track pages created in default context to trigger periodic recycling
        self._default_context_page_count: int = 0
        self._default_context_max_pages: int = config.get('context_recycle_after', 50)

    @property
    def pages(self) -> list[Page]:
        """Get list of open pages (for compatibility)"""
        return self._pages

    @property
    def browser(self) -> Browser | None:
        """Get browser instance (for compatibility)"""
        return self._browser

    @property
    def default_context(self) -> BrowserContext | None:
        """Get the current default BrowserContext (may be None before any page is created)."""
        return self._default_context

    async def start(self) -> None:
        """Start browser instance (safe for concurrent callers)"""
        async with self._start_lock:
            if self._browser and self._browser.is_connected():
                return  # Browser already running

            # Get headless setting (check both uppercase and lowercase keys).
            # If an interactive auth delay is configured, force the browser to be
            # visible so the user can sign in through the window.
            is_headless = self.config.get('headless', self.config.get('BROWSER_HEADLESS', True))
            if self.config.get('INTERACTIVE_AUTH_DELAY_SECONDS', 0):
                is_headless = False

            # Build args list (similar to Pyppeteer for consistency)
            is_interactive = bool(self.config.get('INTERACTIVE_AUTH_DELAY_SECONDS', 0))
            if is_interactive:
                logger.info("Browser launching in interactive mode (relaxed flags)")
            else:
                logger.info("Browser launching in headless-crawl mode (strict flags)")

            browser_args = [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-accelerated-2d-canvas',
                '--no-first-run',
                f'--window-size={self.config.get("viewport_width", 1920)},{self.config.get("viewport_height", 1080)}',
                '--disable-extensions',
                '--disable-blink-features=AutomationControlled',
                '--disable-features=TranslateUI',
                '--disable-ipc-flooding-protection',
                '--disable-renderer-backgrounding',
                '--disable-backgrounding-occluded-windows',
            ]
            if not is_interactive:
                # These flags optimize for fast headless crawling but break SPAs by
                # preventing background networking, caching, and GPU rendering that
                # SPA boot sequences depend on.
                browser_args.extend([
                    '--disable-gpu',
                    '--disable-background-networking',
                    '--disable-component-update',
                    # Memory management: prevent cache/renderer bloat during long test runs
                    '--disk-cache-size=0',              # Disable HTTP disk cache (not needed for testing)
                    '--aggressive-cache-discard',       # Aggressively discard cached data
                    '--disable-application-cache',      # Disable application cache
                ])

            launch_options: dict[str, Any] = {
                'headless': is_headless,
                'args': browser_args,
                'timeout': self.config.get('timeout', 60000),
            }

            # Add explicit executable path only if one was passed in config
            if self.config.get('executable_path'):
                launch_options['executable_path'] = self.config['executable_path']

            # Make sure PLAYWRIGHT_BROWSERS_PATH points to .playwright inside the
            # source tree (critical on Render where /opt/render/project/.playwright
            # does not survive from build to runtime).
            _ensure_playwright_browsers_path()

            try:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(**launch_options)
                logger.info("Playwright browser started successfully")
            except Exception as e:
                logger.error(f"Failed to start browser: {e}")
                logger.info("Attempting runtime Chromium install...")
                try:
                    result = subprocess.run(
                        [sys.executable, '-m', 'playwright', 'install', 'chromium', 'chromium-headless-shell'],
                        check=True, capture_output=True, text=True, timeout=300,
                    )
                    logger.info(f"Playwright install output: {result.stdout}")
                    _ensure_playwright_browsers_path()
                    if self._playwright is not None:
                        self._browser = await self._playwright.chromium.launch(**launch_options)
                    logger.info("Playwright browser started after runtime install")
                    return
                except Exception as retry_err:
                    logger.error(f"Runtime install also failed: {retry_err}")
                await self._cleanup_playwright()
                raise

    async def _cleanup_playwright(self) -> None:
        """Clean up Playwright resources"""
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    async def stop(self) -> None:
        """Stop browser instance completely"""
        # Close all pages
        for page in self._pages[:]:  # Copy list to avoid modification during iteration
            try:
                if not page.is_closed():
                    await page.close()
            except Exception:
                pass
        self._pages.clear()

        # Close all contexts
        for context in self._contexts[:]:
            try:
                await context.close()
            except Exception:
                pass
        self._contexts.clear()

        # Close default context
        if self._default_context:
            try:
                await self._default_context.close()
            except Exception:
                pass
            self._default_context = None

        # Close browser
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None

        # Stop Playwright
        await self._cleanup_playwright()
        logger.info("Playwright browser stopped")

    async def create_context(
        self,
        storage_state: str | None = None,
        viewport: dict[str, int] | None = None,
        user_agent: str | None = None
    ) -> BrowserContext:
        """
        Create an isolated browser context.

        Browser contexts provide complete isolation:
        - Separate cookies, localStorage, sessionStorage
        - Separate cache
        - Can have different viewports and user agents

        This is the KEY FEATURE that makes Playwright more stable
        for multi-state and multi-user testing.

        Args:
            storage_state: Path to saved storage state (for auth persistence)
            viewport: Custom viewport {width, height}
            user_agent: Custom user agent string

        Returns:
            BrowserContext instance
        """
        if not self._browser:
            await self.start()

        assert self._browser is not None  # ensured by start()

        resolved_viewport = viewport or {
            'width': self.config.get('viewport_width', self.config.get('BROWSER_VIEWPORT_WIDTH', 1920)),
            'height': self.config.get('viewport_height', self.config.get('BROWSER_VIEWPORT_HEIGHT', 1080))
        }
        resolved_user_agent = (
            user_agent
            or self.config.get('user_agent')
            or self.config.get('USER_AGENT')
            or 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36'
        )

        # Build keyword arguments explicitly for type safety
        # Allow service workers in interactive mode: SPAs depend on them for routing,
        # caching, and auth token refresh.  In headless-crawl mode we block them to
        # prevent background fetch interference with test measurements.
        service_workers_mode: Literal['allow', 'block'] = (
            'allow' if self.config.get('INTERACTIVE_AUTH_DELAY_SECONDS', 0) else 'block'
        )
        ctx_kwargs: dict[str, Any] = {
            'viewport': resolved_viewport,
            'user_agent': resolved_user_agent,
            'service_workers': service_workers_mode,
        }

        # If no explicit storage_state was given, check whether a
        # session file was captured by an interactive-auth delay and
        # stored in browser_config['INTERACTIVE_AUTH_STATE'].
        if storage_state is None:
            config_state: str | None = self.config.get('INTERACTIVE_AUTH_STATE')
            if config_state:
                storage_state = config_state

        # Load saved authentication state if provided
        if storage_state and Path(storage_state).exists():
            ctx_kwargs['storage_state'] = storage_state
            logger.debug(f"Loading storage state from: {storage_state}")

        context = await self._browser.new_context(**ctx_kwargs)

        # Set default timeouts
        default_timeout = self.config.get('timeout', 60000)
        context.set_default_timeout(default_timeout)
        context.set_default_navigation_timeout(default_timeout)

        # Apply stealth if enabled
        if self.config.get('stealth_mode', False):
            await self._apply_stealth(context)

        self._contexts.append(context)
        logger.debug(f"Created browser context (total: {len(self._contexts)})")
        return context

    async def close_context(self, context: BrowserContext) -> None:
        """
        Close a browser context and all its pages.

        Args:
            context: BrowserContext to close
        """
        try:
            # Remove pages from tracking
            for page in context.pages:
                if page in self._pages:
                    self._pages.remove(page)

            await context.close()

            if context in self._contexts:
                self._contexts.remove(context)

            logger.debug(f"Closed browser context (remaining: {len(self._contexts)})")
        except Exception as e:
            logger.warning(f"Error closing context: {e}")

    async def save_storage_state(self, context: BrowserContext, path: str) -> None:
        """
        Save authentication/storage state to file for reuse.

        This allows you to:
        1. Login once
        2. Save the state
        3. Create new contexts with that state (instant auth)

        Args:
            context: BrowserContext with authenticated state
            path: File path to save state to
        """
        await context.storage_state(path=path)
        logger.info(f"Saved storage state to: {path}")

    async def _apply_stealth(self, context: BrowserContext) -> None:
        """
        Apply stealth techniques to make the browser harder to detect.

        Args:
            context: BrowserContext to apply stealth to
        """
        stealth_script = '''() => {
            // Overwrite the `navigator.webdriver` property
            Object.defineProperty(navigator, 'webdriver', {
                get: () => false,
            });

            // Overwrite the `plugins` property to add fake plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5],
            });

            // Overwrite the `languages` property
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en'],
            });

            // Pass the Chrome Test
            window.chrome = {
                runtime: {},
            };

            // Pass the Permissions Test
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
        }'''

        await context.add_init_script(stealth_script)

    @staticmethod
    def _is_target_closed_error(exc: BaseException) -> bool:
        """True when an exception means the page/context/browser died mid-operation."""
        msg = str(exc).lower()
        return any(marker in msg for marker in (
            "has been closed",
            "target closed",
            "target crashed",
            "browser has been closed",
            "connection closed",
            "page crashed",
        ))

    async def _acquire_default_context(self) -> BrowserContext:
        """Return a live default context, recycling it once the per-context page cap is hit."""
        if (self._default_context is not None and
                self._default_context_page_count >= self._default_context_max_pages):
            logger.info(
                f"Recycling default context after {self._default_context_page_count} pages"
            )
            await self.close_context(self._default_context)
            self._default_context = None
            self._default_context_page_count = 0
        if self._default_context is None:
            self._default_context = await self.create_context()
            self._default_context_page_count = 0
        return self._default_context

    async def _open_managed_page(self, context: BrowserContext | None) -> Page:
        """Open a new page, relaunching the browser and rebuilding the default context if a
        previous page crashed them. A single resource-heavy page (e.g. runaway animation or
        timer) can take down the shared browser; without this recovery every later page in the
        run would fail with "Target ... has been closed". Recovery only applies to the default
        context (a caller-supplied context cannot be recreated here)."""
        using_default = context is None
        last_exc: Exception | None = None
        for attempt in range(2):
            await self.ensure_running()
            target_context = context if context is not None else await self._acquire_default_context()
            try:
                page = await target_context.new_page()
            except Exception as exc:
                last_exc = exc
                if attempt == 0 and using_default and self._is_target_closed_error(exc):
                    logger.warning(f"Browser/context closed mid-run; relaunching and retrying: {exc}")
                    await self.stop()  # nulls the dead browser and default context
                    continue
                raise
            if using_default:
                self._default_context_page_count += 1
            return page
        assert last_exc is not None  # loop only falls through after a caught, recoverable error
        raise last_exc

    @asynccontextmanager
    async def get_page(self, context: BrowserContext | None = None) -> AsyncGenerator[Page]:
        """
        Get a new page with resource management.

        Args:
            context: Optional BrowserContext to use (creates default if not provided)

        Yields:
            Page instance
        """
        async with self._semaphore:
            page: Page | None = None
            try:
                page = await self._open_managed_page(context)
                self._pages.append(page)
                yield page

            finally:
                if page and not page.is_closed():
                    try:
                        await page.close()
                        if page in self._pages:
                            self._pages.remove(page)
                    except Exception as e:
                        logger.warning(f"Error closing page: {e}")

    async def create_page(self, context: BrowserContext | None = None) -> Page:
        """
        Create a new page without context manager (caller must close it).

        Args:
            context: Optional BrowserContext to use

        Returns:
            Page instance
        """
        page = await self._open_managed_page(context)
        self._pages.append(page)
        return page

    async def close_page(self, page: Page) -> None:
        """
        Close a page created with create_page().

        Args:
            page: Page instance to close
        """
        try:
            # Clean up any CSS capture cache for this page
            try:
                from auto_a11y.testing.css_focus_capture import clear_css_capture_for_page
                clear_css_capture_for_page(page)
            except ImportError:
                pass

            if not page.is_closed():
                await page.close()
            if page in self._pages:
                self._pages.remove(page)
        except Exception as e:
            logger.warning(f"Error closing page: {e}")

    async def goto(
        self,
        page: Page,
        url: str,
        wait_until: str = 'networkidle',
        timeout: int | None = None,
        capture_css: bool = False
    ) -> Response | None:
        """
        Navigate to URL with error handling.

        Args:
            page: Page instance
            url: URL to navigate to
            wait_until: Wait condition ('load', 'domcontentloaded', 'networkidle')
            timeout: Navigation timeout in ms
            capture_css: Whether to capture CSS focus rules during navigation

        Returns:
            Response object or None if failed
        """
        # Map Pyppeteer wait conditions to Playwright
        wait_map: dict[str, WaitUntilType] = {
            'networkidle0': 'networkidle',
            'networkidle2': 'networkidle',
            'networkidle': 'networkidle',
            'load': 'load',
            'domcontentloaded': 'domcontentloaded',
            'commit': 'commit',
        }
        resolved_wait: WaitUntilType = wait_map.get(wait_until, 'networkidle')

        css_capture: Any = None
        if capture_css:
            try:
                from auto_a11y.testing.css_focus_capture import (
                    CSSFocusCapture, set_css_capture_for_page, clear_css_capture_for_page
                )
                clear_css_capture_for_page(page)
                css_capture = CSSFocusCapture()
                await css_capture.start_capture(page)
                set_css_capture_for_page(page, css_capture)
            except Exception as e:
                logger.debug(f"CSS capture setup failed: {e}")
                css_capture = None

        try:
            response = await page.goto(
                url,
                wait_until=resolved_wait,
                timeout=timeout or self.config.get('timeout', 60000)
            )
            logger.debug(f"Navigated to: {url}")

            if css_capture:
                try:
                    await css_capture.stop_capture()
                    await css_capture.capture_inline_styles(page)
                    logger.debug(f"CSS capture complete: {len(css_capture.cache.focus_rules)} focus rules captured")
                except Exception as e:
                    logger.debug(f"CSS capture finalization failed: {e}")

            return response

        except PlaywrightTimeoutError:
            logger.warning(f"Navigation timeout for: {url}")
            if css_capture:
                try:
                    await css_capture.stop_capture()
                except Exception:
                    pass
            return None

        except PlaywrightError as e:
            logger.error(f"Navigation error for {url}: {e}")
            if css_capture:
                try:
                    await css_capture.stop_capture()
                except Exception:
                    pass
            return None

    async def take_screenshot(
        self,
        page: Page,
        path: Path | str | None = None,
        full_page: bool = True
    ) -> bytes:
        """
        Take screenshot of page.

        Args:
            page: Page instance
            path: Optional path to save screenshot
            full_page: Capture full page

        Returns:
            Screenshot bytes
        """
        try:
            screenshot = await page.screenshot(
                full_page=full_page,
                type='jpeg',
                quality=80,
                path=str(path) if path else None,
            )
            logger.debug(f"Screenshot taken{f' and saved to {path}' if path else ''}")
            return screenshot
        except Exception as e:
            logger.error(f"Screenshot error: {e}")
            raise

    async def inject_scripts(
        self,
        page: Page,
        script_paths: list[Path]
    ) -> None:
        """
        Inject JavaScript files into page.

        Args:
            page: Page instance
            script_paths: List of script file paths
        """
        for script_path in script_paths:
            if not script_path.exists():
                logger.warning(f"Script not found: {script_path}")
                continue

            try:
                await page.add_script_tag(path=str(script_path))
                logger.debug(f"Injected script: {script_path.name}")
            except Exception as e:
                logger.error(f"Failed to inject script {script_path.name}: {e}")
                raise

    async def execute_script(
        self,
        page: Page,
        script: str
    ) -> Any:
        """
        Execute JavaScript in page context.

        Args:
            page: Page instance
            script: JavaScript code to execute

        Returns:
            Script execution result
        """
        try:
            result = await page.evaluate(script)
            return result
        except Exception as e:
            logger.error(f"Script execution error: {e}")
            raise

    async def wait_for_selector(
        self,
        page: Page,
        selector: str,
        timeout: int | None = None,
        state: SelectorStateType = 'visible'
    ) -> bool:
        """
        Wait for element to appear.

        Args:
            page: Page instance
            selector: CSS selector or XPath (auto-detected)
            timeout: Wait timeout in ms
            state: Element state to wait for ('visible', 'attached', 'hidden', 'detached')

        Returns:
            True if element found, False otherwise
        """
        try:
            await page.wait_for_selector(
                selector,
                timeout=timeout or self.config.get('timeout', 60000),
                state=state
            )
            return True
        except PlaywrightTimeoutError:
            logger.debug(f"Selector not found: {selector}")
            return False
        except Exception as e:
            logger.error(f"Wait for selector error: {e}")
            return False

    async def get_page_content(self, page: Page) -> str:
        """
        Get page HTML content.

        Args:
            page: Page instance

        Returns:
            HTML content
        """
        try:
            content = await page.content()
            return content
        except Exception as e:
            logger.error(f"Error getting page content: {e}")
            raise

    async def get_page_title(self, page: Page) -> str:
        """
        Get page title.

        Args:
            page: Page instance

        Returns:
            Page title
        """
        try:
            title = await page.title()
            return title
        except Exception as e:
            logger.error(f"Error getting page title: {e}")
            return ""

    async def extract_links(self, page: Page) -> list[str]:
        """
        Extract all links from page.

        Args:
            page: Page instance

        Returns:
            List of URLs
        """
        try:
            links: list[str] = await page.evaluate('''
                () => {
                    const links = document.querySelectorAll('a[href]');
                    return Array.from(links).map(link => link.href);
                }
            ''')
            return links
        except Exception as e:
            logger.error(f"Error extracting links: {e}")
            return []

    async def is_running(self) -> bool:
        """Check if browser is running and connected."""
        try:
            if not self._browser:
                return False
            return self._browser.is_connected()
        except Exception as e:
            logger.error(f"Error checking browser status: {e}")
            return False

    async def ensure_running(self) -> None:
        """Ensure browser is running, restart if needed."""
        if not await self.is_running():
            logger.info("Browser not running, restarting...")
            await self.stop()  # Clean up any dead resources
            await self.start()
