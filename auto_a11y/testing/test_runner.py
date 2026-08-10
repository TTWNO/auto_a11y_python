"""
Main test runner for accessibility testing
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable, Awaitable, Literal, TYPE_CHECKING, cast
from datetime import datetime
from pathlib import Path
import time

from bson import ObjectId
from auto_a11y.models import Page, PageStatus, TestResult, Violation
from auto_a11y.web.fluent import ftl
from auto_a11y.core.async_compat import wait_for
from auto_a11y.core.database import Database
from auto_a11y.core.browser_manager import BrowserManager
from auto_a11y.pdf.errors import NotAPdf, PdfTooLarge
from auto_a11y.testing.script_injector import ScriptInjector
from auto_a11y.testing.result_processor import ResultProcessor
from auto_a11y.testing.script_executor import ScriptExecutor
from auto_a11y.testing.script_session_manager import ScriptSessionManager
from auto_a11y.testing.multi_state_test_runner import MultiStateTestRunner
from auto_a11y.testing.login_automation import LoginAutomation

if TYPE_CHECKING:
    from auto_a11y.models import WebsiteUser, ProjectUser
    from auto_a11y.testing.pdf_runner import PdfRunner

logger = logging.getLogger(__name__)


async def fetch_pdf_with_playwright_cookies(
    browser_page: Any,
    url: str,
) -> bytes:
    """Fallback fetch when Playwright's ``response.body()`` is unavailable.

    Re-fetches via :mod:`aiohttp` using cookies extracted from the
    Playwright browsing context. Handles edge cases where Playwright
    streams the body or where the response object has been consumed.

    Module-level so it can be monkey-patched in unit tests without
    going through ``TestRunner`` private machinery.
    """
    import aiohttp

    cookies_raw: list[dict[str, Any]] = await browser_page.context.cookies()
    cookie_jar: dict[str, str] = {
        str(c["name"]): str(c["value"]) for c in cookies_raw
    }
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(
        cookies=cookie_jar, timeout=timeout
    ) as session:
        async with session.get(url) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"Fetch failed: HTTP {resp.status}")
            return await resp.read()


def url_looks_like_pdf(url: str) -> bool:
    """Heuristic: does this URL likely serve a PDF?

    Used as a fallback signal when Playwright's :meth:`goto` returns
    ``None`` (Chromium aborts navigation when the response is a
    download — the PDF case). True if the URL path ends in ``.pdf``,
    optionally followed by a query or fragment.
    """
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return parsed.path.lower().endswith(".pdf")


def merge_violations_into_checks(
    metadata: dict[str, Any],
    violations: list[Violation],
) -> None:
    """Fold extra violations into the per-touchpoint ``checks`` summary in-place.

    ``ResultProcessor.process_test_results`` builds ``metadata['checks']`` --
    the per-touchpoint breakdown shown in *Test Check Details* -- before the
    test runner appends script-condition violations to the result. Without
    this reconciliation those violations are counted in ``violation_count``
    but missing from the touchpoint summary, so the breakdown disagrees with
    the headline count.

    For each violation this either increments the matching touchpoint row
    (matched by its title-cased ``test_name``) or appends a new row, keeping
    the same shape the processor emits (``violations``/``failed``/``total``
    counters and a sorted, de-duplicated ``wcag`` list).
    """
    checks_obj: object = metadata.get('checks')
    checks_list: list[dict[str, Any]] = []
    if isinstance(checks_obj, list):
        entries: list[object] = cast(list[object], checks_obj)
        for entry in entries:
            if isinstance(entry, dict):
                checks_list.append(cast(dict[str, Any], entry))
    metadata['checks'] = checks_list

    # Index existing rows by their display name for O(1) lookup.
    by_name: dict[str, dict[str, Any]] = {}
    for existing_row in checks_list:
        name = existing_row.get('test_name')
        if isinstance(name, str):
            by_name[name] = existing_row

    def _as_int(value: object) -> int:
        return value if isinstance(value, int) else 0

    for violation in violations:
        touchpoint = violation.touchpoint or ''
        display_name = touchpoint.replace('_', ' ').title()
        row: dict[str, Any] | None = by_name.get(display_name)
        if row is None:
            touchpoint_label = touchpoint.replace('_', ' ').lower()
            description: str = str(
                ftl(
                    'common-accessibility-checks-for-touchpoint',
                    touchpoint=touchpoint_label,
                )
            )
            row = {
                'test_name': display_name,
                'description': description,
                'wcag': [],
                'total': 0,
                'passed': 0,
                'failed': 0,
                'violations': 0,
                'warnings': 0,
                'info': 0,
                'discovery': 0,
            }
            checks_list.append(row)
            by_name[display_name] = row

        row['violations'] = _as_int(row.get('violations')) + 1
        row['failed'] = _as_int(row.get('failed')) + 1
        row['total'] = _as_int(row.get('total')) + 1

        if violation.wcag_criteria:
            existing_wcag = row.get('wcag')
            wcag_values: set[str] = set()
            if isinstance(existing_wcag, list):
                existing_criteria: list[object] = cast(list[object], existing_wcag)
                for criterion in existing_criteria:
                    if isinstance(criterion, str):
                        wcag_values.add(criterion)
            wcag_values.update(violation.wcag_criteria)
            row['wcag'] = sorted(wcag_values)

    # Keep the rows sorted the same way the processor does.
    def _row_name(row: dict[str, Any]) -> str:
        name = row.get('test_name', '')
        return name if isinstance(name, str) else ''

    checks_list.sort(key=_row_name)


async def _audit_pdf_bytes(
    *,
    db: Database,
    pdf_runner: "PdfRunner",
    page: Page,
    pdf_bytes: bytes,
    run_ai_analysis: bool,
    ai_api_key: str | None,
) -> TestResult:
    """Common tail used by both the in-flight (`handle_opportunistic_pdf`)
    and the navigation-aborted (`handle_pdf_url_after_navigation_failed`)
    paths. Validates / dedups via the runner, marks the page IS_PDF,
    and triggers the audit.
    """
    website = db.get_website(page.website_id)
    if website is None:
        page.status = PageStatus.ERROR
        page.error_reason = f"Website {page.website_id} not found"
        db.update_page(page)
        raise RuntimeError(page.error_reason)

    try:
        pdf_doc = await pdf_runner.create_or_find_pdf_document(
            pdf_bytes,
            website_id=page.website_id,
            project_id=website.project_id,
            source_type="opportunistic",
            discovered_from_page_id=page.id,
            discovered_from_user_id=None,
            original_filename=page.url.rsplit("/", 1)[-1] or "document.pdf",
            source_url=page.url,
        )
    except (NotAPdf, PdfTooLarge) as exc:
        page.status = PageStatus.ERROR
        page.error_reason = f"PDF rejected: {exc}"
        db.update_page(page)
        raise

    page.status = PageStatus.IS_PDF
    page.linked_pdf_document_id = pdf_doc.id
    db.update_page(page)

    return await pdf_runner.audit_pdf_document(
        pdf_doc.id or "",
        run_ai=run_ai_analysis,
        ai_api_key=ai_api_key,
        wcag_level="AA",
    )


async def handle_opportunistic_pdf(
    *,
    db: Database,
    pdf_runner: "PdfRunner | None",
    page: Page,
    browser_page: Any,
    response: Any,
    run_ai_analysis: bool,
    ai_api_key: str | None,
) -> TestResult:
    """Handle a PDF response detected during :meth:`TestRunner.test_page` navigation.

    Per the audit-engine port spec: read bytes from the Playwright response
    (preferred — avoids a re-fetch round-trip and cookie edge cases),
    validate magic bytes / size via
    :meth:`PdfRunner.create_or_find_pdf_document`, mark
    ``Page.status=IS_PDF``, audit, and return the :class:`TestResult`
    attached to the :class:`PdfDocument`.

    On any failure (download, magic bytes, size, audit) the page is
    marked :data:`PageStatus.ERROR` with a descriptive ``error_reason``;
    no :class:`PdfDocument` is created on download / magic-byte failures.
    The underlying exception is re-raised so the caller treats it like
    any other test failure.

    Module-level (rather than a private method) so unit tests can target
    it directly without leaning on protected-member access.
    """
    if pdf_runner is None:
        page.status = PageStatus.ERROR
        page.error_reason = (
            "PDF response detected but no PdfRunner configured."
        )
        db.update_page(page)
        raise RuntimeError(page.error_reason)

    try:
        try:
            pdf_bytes: bytes = await response.body()
        except Exception as exc:
            logger.warning(
                f"response.body() failed for {page.url}: {exc}; "
                + "falling back to aiohttp fetch with seeded cookies"
            )
            pdf_bytes = await fetch_pdf_with_playwright_cookies(
                browser_page, page.url
            )
    except Exception as exc:
        page.status = PageStatus.ERROR
        page.error_reason = f"PDF download failed: {exc}"
        db.update_page(page)
        raise

    return await _audit_pdf_bytes(
        db=db,
        pdf_runner=pdf_runner,
        page=page,
        pdf_bytes=pdf_bytes,
        run_ai_analysis=run_ai_analysis,
        ai_api_key=ai_api_key,
    )


async def handle_pdf_url_after_navigation_failed(
    *,
    db: Database,
    pdf_runner: "PdfRunner | None",
    page: Page,
    run_ai_analysis: bool,
    ai_api_key: str | None,
) -> TestResult:
    """Fallback when Playwright's ``goto`` returns ``None`` on a PDF URL.

    Chromium aborts navigation when the response is a download (the
    default behaviour for ``application/pdf``), so the response object
    we'd ordinarily inspect is unavailable. We re-fetch directly via
    aiohttp through :meth:`PdfRunner.fetch_pdf_from_url`, then run the
    same opportunistic-audit tail.
    """
    if pdf_runner is None:
        page.status = PageStatus.ERROR
        page.error_reason = (
            "PDF URL detected but no PdfRunner configured."
        )
        db.update_page(page)
        raise RuntimeError(page.error_reason)

    try:
        pdf_bytes = await pdf_runner.fetch_pdf_from_url(page.url)
    except (NotAPdf, PdfTooLarge) as exc:
        page.status = PageStatus.ERROR
        page.error_reason = f"PDF rejected: {exc}"
        db.update_page(page)
        raise
    except Exception as exc:
        page.status = PageStatus.ERROR
        page.error_reason = f"PDF download failed: {exc}"
        db.update_page(page)
        raise

    return await _audit_pdf_bytes(
        db=db,
        pdf_runner=pdf_runner,
        page=page,
        pdf_bytes=pdf_bytes,
        run_ai_analysis=run_ai_analysis,
        ai_api_key=ai_api_key,
    )


class TestRunner:
    """Runs accessibility tests on web pages"""
    
    def __init__(
        self,
        database: Database,
        browser_config: dict[str, Any],
        pdf_runner: "PdfRunner | None" = None,
    ) -> None:
        """
        Initialize test runner

        Args:
            database: Database connection
            browser_config: Browser configuration
            pdf_runner: Optional PdfRunner. Required for the opportunistic
                PDF detection branch in :meth:`test_page` and the
                :meth:`test_pdf` delegate; both raise ``RuntimeError`` when
                ``None``. Defaults to ``None`` so existing call sites that
                only audit HTML pages continue to work unchanged.
        """
        self.db: Database = database
        self.browser_manager: BrowserManager = BrowserManager(browser_config)
        self.script_injector: ScriptInjector = ScriptInjector()  # Will use test_config from project
        self.result_processor: ResultProcessor = ResultProcessor()
        self.script_executor: ScriptExecutor = ScriptExecutor()  # For executing page setup scripts
        self.session_manager: ScriptSessionManager = ScriptSessionManager(database)  # For tracking script execution
        self.multi_state_runner: MultiStateTestRunner = MultiStateTestRunner(self.script_executor)  # For multi-state testing
        self.login_automation: LoginAutomation = LoginAutomation(database)  # For authenticated testing
        self.screenshot_dir: Path = Path(browser_config.get('SCREENSHOTS_DIR', 'screenshots'))
        self.screenshot_dir.mkdir(exist_ok=True, parents=True)
        self._current_website_id: str | None = None  # Track current website for session management
        self._logged_in_user: WebsiteUser | ProjectUser | None = None  # Track currently logged in user
        self._pdf_runner: "PdfRunner | None" = pdf_runner
    
    async def test_page(
        self,
        page: Page,
        take_screenshot: bool = True,
        run_ai_analysis: bool = False,
        ai_api_key: str | None = None,
        website_user_id: str | None = None
    ) -> TestResult:
        """
        Run accessibility tests on a single page

        Args:
            page: Page to test
            take_screenshot: Whether to capture screenshot
            run_ai_analysis: Whether to run AI analysis
            ai_api_key: API key for AI analysis
            website_user_id: Optional ID of user to authenticate as before testing

        Returns:
            Test result
        """
        # Testing page
        start_time = time.time()

        # Move the page out of QUEUED now that it is actually being tested. The
        # single-page path queued it and nothing ever advanced it, so a page read
        # "Queued" for the whole run and only jumped to "Tested" at the end -
        # which reads as a stuck job rather than a working one. The batch path in
        # testing_job.py already does this; doing it here covers every caller.
        page.status = PageStatus.TESTING
        self.db.update_page(page)

        # Start browser if needed
        if not await self.browser_manager.is_running():
            await self.browser_manager.start()
        
        try:
            async with self.browser_manager.get_page() as browser_page:
                # Get wait strategy from project config (defaults to networkidle2 for complete content)
                # Valid options:
                #   - 'networkidle2': Wait for network to be mostly idle (default, best for slow/dynamic sites)
                #   - 'networkidle0': Wait for network to be completely idle (very thorough but slowest)
                #   - 'domcontentloaded': Wait only for DOM to be ready (fast, for sites with heavy background activity)
                #   - 'load': Wait for load event (faster than networkidle, slower than domcontentloaded)
                wait_strategy = 'networkidle2'  # Default: wait for network to be mostly idle

                try:
                    website = self.db.get_website(page.website_id)
                    if website:
                        project = self.db.get_project(website.project_id)
                        if project and project.config:
                            # Allow project to override wait strategy for sites with heavy background activity
                            wait_strategy = project.config.get('page_load_strategy', 'networkidle2')
                            logger.info(f"Using page load strategy: {wait_strategy}")
                except Exception as e:
                    logger.warning(f"Could not get project config for wait strategy: {e}")

                # Perform authentication FIRST if user specified
                authenticated_user = None
                if website_user_id:
                    logger.debug(f"DEBUG: Testing page {page.url} with user_id: {website_user_id}")
                    # Try project user first, then website user
                    user: Any = self.db.get_project_user(website_user_id)
                    if not user:
                        user = self.db.get_website_user(website_user_id)
                    if user:
                        logger.debug(f"DEBUG: Found user: {user.username} (id: {user.id})")
                        if not user.enabled:
                            logger.warning(f"User {user.username} is disabled, skipping authentication")
                        else:
                            # Check if already logged in as this user
                            if self._logged_in_user:
                                logger.debug(f"DEBUG: Already have logged_in_user: {self._logged_in_user.username} (id: {self._logged_in_user.id})")
                                if self._logged_in_user.id == user.id:
                                    logger.debug(f"DEBUG: IDs match, reusing session")
                                    authenticated_user = self._logged_in_user
                                else:
                                    logger.debug(f"DEBUG: IDs don't match ({self._logged_in_user.id} != {user.id}), will re-authenticate")
                            else:
                                logger.debug(f"DEBUG: No logged_in_user set, will authenticate")

                            if not authenticated_user:
                                logger.info(f"Authenticating as user: {user.username} (roles: {user.role_display})")
                                login_result = await self.login_automation.perform_login(
                                    browser_page,
                                    user,
                                    timeout=30000
                                )

                                if login_result['success']:
                                    logger.info(f"Successfully authenticated as {user.username} in {login_result['duration_ms']}ms")
                                    authenticated_user = user
                                    self._logged_in_user = user
                                else:
                                    logger.error(f"Authentication failed: {login_result['error']}")
                                    # Continue with testing even if login fails, but record the failure
                    else:
                        logger.warning(f"User ID {website_user_id} not found")

                # Navigate to test page (after authentication if applicable)
                logger.info(f"Navigating to test page: {page.url}")
                response = await self.browser_manager.goto(
                    browser_page,
                    page.url,
                    wait_until=wait_strategy,
                    timeout=30000
                )

                if not response:
                    # Playwright's goto() returns None on download responses
                    # (Chromium's default behaviour for application/pdf is to
                    # download, which aborts the navigation). If the URL
                    # looks like a PDF, route through the opportunistic
                    # audit flow via a direct fetch.
                    if url_looks_like_pdf(page.url):
                        return await handle_pdf_url_after_navigation_failed(
                            db=self.db,
                            pdf_runner=self._pdf_runner,
                            page=page,
                            run_ai_analysis=run_ai_analysis,
                            ai_api_key=ai_api_key,
                        )
                    raise RuntimeError(f"Failed to load page: {page.url}")

                # Opportunistic PDF detection — must happen before wait_for_selector('body')
                # because a PDF response has no <body>; the wait would error.
                content_type = (response.headers.get("content-type") or "").lower()
                url_path = browser_page.url.lower()
                is_pdf_response = (
                    content_type.startswith("application/pdf")
                    or (
                        content_type.startswith("application/octet-stream")
                        and url_path.endswith(".pdf")
                    )
                )
                if is_pdf_response:
                    return await self._handle_opportunistic_pdf(
                        page=page,
                        browser_page=browser_page,
                        response=response,
                        run_ai_analysis=run_ai_analysis,
                        ai_api_key=ai_api_key,
                    )

                # Wait for content to be ready
                await browser_page.wait_for_selector('body', timeout=5000)

                # If using domcontentloaded, give the page a moment to stabilize
                # This prevents the browser from closing the session too early
                if wait_strategy == 'domcontentloaded':
                    import asyncio
                    await asyncio.sleep(2)  # Wait 2 seconds for JS to initialize

                # Start script session if not already started for this website
                if self._current_website_id != page.website_id:
                    # End previous session if exists
                    if self._current_website_id is not None:
                        self.session_manager.end_session()

                    # Start new session for this website
                    self.session_manager.start_session(page.website_id)
                    self._current_website_id = page.website_id
                    logger.info(f"Started script session for website {page.website_id}")

                # Get all applicable scripts for this page (website-level + page-level)
                scripts_to_execute = self.db.get_scripts_for_page_v2(
                    page_id=page.id or '',
                    website_id=page.website_id,
                    enabled_only=True
                )

                # Execute scripts with session awareness
                script_violations: list[Violation] = []
                for script in scripts_to_execute:
                    logger.info(f"Processing script: {script.name} (scope={script.scope.value}, trigger={script.trigger.value})")
                    try:
                        result = await self.script_executor.execute_with_session(
                            browser_page,
                            script,
                            page.id or '',
                            self.session_manager
                        )

                        # Check for violations reported by scripts
                        if 'violation' in result:
                            violation_item: Violation = result['violation']
                            script_violations.append(violation_item)
                            logger.warning(f"Script reported violation: {violation_item.description}")

                        # Log result
                        if result.get('skipped'):
                            logger.info(f"Script '{script.name}' skipped: {result.get('skip_reason')}")
                        elif result['success']:
                            logger.info(f"Script '{script.name}' completed successfully in {result['duration_ms']}ms")
                            # Update execution stats
                            self.db.update_script_execution_stats(
                                script.id or '',
                                success=True,
                                duration_ms=result['duration_ms']
                            )
                        else:
                            logger.warning(f"Script '{script.name}' failed: {result.get('error', 'Unknown error')}")
                            # Update execution stats
                            self.db.update_script_execution_stats(
                                script.id or '',
                                success=False,
                                duration_ms=result['duration_ms']
                            )

                    except Exception as e:
                        logger.error(f"Error executing script '{script.name}': {e}")
                        # Continue with testing even if script crashes
                
                # Get project configuration including WCAG level and touchpoint settings
                wcag_level = 'AA'  # Default to AA
                project_config = None
                test_config = None
                
                try:
                    # Get website to find project
                    website = self.db.get_website(page.website_id)
                    if website:
                        project = self.db.get_project(website.project_id)
                        if project and project.config:
                            project_config = project.config
                            wcag_level = project_config.get('wcag_level', 'AA')
                            logger.info(f"Using WCAG {wcag_level} compliance level for testing")
                            
                            # Create test configuration from project settings
                            from auto_a11y.config.test_config import TestConfiguration
                            test_config = TestConfiguration(database=self.db, debug_mode=True)

                            # Apply touchpoint settings from project
                            if 'touchpoints' in project_config:
                                test_config.config['touchpoints'] = project_config['touchpoints']
                            
                            # Apply AI settings from project
                            test_config.config['global']['run_ai_tests'] = project_config.get('enable_ai_testing', False)
                            if 'ai_tests' in project_config:
                                for test_name in ['headings', 'reading_order', 'modals', 'language', 'animations', 'interactive', 'widgets', 'landmarks', 'media', 'live_regions', 'structure']:
                                    enabled = test_name in project_config['ai_tests']
                                    test_config.set_ai_test_enabled(test_name, enabled)
                        else:
                            logger.info(f"No config found in project, using defaults")
                    else:
                        logger.warning(f"Could not find website for page {page.website_id}")
                except Exception as e:
                    logger.warning(f"Could not get project config: {e}, using defaults")
                
                # Set test configuration for Python tests
                if test_config:
                    self.script_injector.test_config = test_config

                # Pass project config separately for JavaScript config injection
                self.script_injector.project_config = project_config

                # Inject test scripts
                await self.script_injector.inject_script_files(browser_page)
                
                # Set WCAG level in page context
                await browser_page.evaluate(f'''
                    window.WCAG_LEVEL = "{wcag_level}";
                    console.log("Testing with WCAG Level:", window.WCAG_LEVEL);
                ''')

                # Inject document metadata for document link language testing
                try:
                    # Get all document references for this website
                    document_refs = self.db.get_document_references(page.website_id)

                    # Build a map of document URL to language metadata
                    doc_metadata: dict[str, dict[str, Any]] = {}
                    for doc_ref in document_refs:
                        if doc_ref.language:
                            # Store with both full URL and just the filename for flexible matching
                            doc_metadata[doc_ref.document_url] = {
                                'language': doc_ref.language,
                                'confidence': doc_ref.language_confidence
                            }

                    # Inject into page context as JSON
                    import json
                    metadata_json = json.dumps(doc_metadata)
                    await browser_page.evaluate(f'''
                        window.DOCUMENT_METADATA = {metadata_json};
                    ''')
                    logger.debug(f"Injected metadata for {len(doc_metadata)} documents")
                except Exception as e:
                    logger.warning(f"Could not inject document metadata: {e}")
                    await browser_page.evaluate('''
                        window.DOCUMENT_METADATA = {};
                    ''')
                
                # Store original viewport before running tests
                # Some tests (like text_contrast and floating_dialogs) change viewport for breakpoint testing
                original_viewport = await browser_page.evaluate('() => ({ width: window.innerWidth, height: window.innerHeight })')
                logger.debug(f"Stored original viewport: {original_viewport}")

                # Run all tests
                # Running JavaScript tests
                raw_results = await self.script_injector.run_all_tests(browser_page)

                # Restore original viewport after tests complete
                # This is critical because some tests change viewport for responsive breakpoint testing
                if original_viewport:
                    try:
                        await browser_page.set_viewport_size({
                            'width': original_viewport['width'],
                            'height': original_viewport['height']
                        })
                        logger.debug(f"Restored viewport to original size: {original_viewport}")
                        # Give page a moment to reflow after viewport change
                        import asyncio
                        await asyncio.sleep(0.1)
                    except Exception as e:
                        logger.warning(f"Could not restore viewport: {e}")

                # Take screenshot if requested
                screenshot_path = None
                screenshot_bytes = None
                if take_screenshot:
                    # Take screenshot once and reuse bytes for AI analysis
                    screenshot_path, screenshot_bytes = await self._take_screenshot_with_bytes(browser_page, page.id or '')
                
                # Check if project has AI testing enabled
                ai_findings: list[Any] = []
                ai_analysis_results: dict[str, Any] = {}

                # Get AI testing configuration from project
                run_ai = False
                ai_tests_to_run = []
                
                try:
                    website = self.db.get_website(page.website_id)
                    if website:
                        project = self.db.get_project(website.project_id)
                        if project and project.config:
                            # Check if AI testing is enabled at project level
                            if project.config.get('enable_ai_testing', False):
                                run_ai = True
                                ai_tests_to_run = project.config.get('ai_tests', [])
                                logger.info(f"AI testing enabled for project '{project.name}', will run tests: {ai_tests_to_run}")
                            else:
                                logger.info(f"AI testing disabled for project '{project.name}'")
                        else:
                            logger.info(f"No config found for project, AI testing disabled")
                    else:
                        logger.warning(f"Could not find website with id {page.website_id}")
                except Exception as e:
                    logger.warning(f"Could not get AI config from project: {e}")
                
                # Override with explicit parameter if provided (for backward compatibility)
                if run_ai_analysis:
                    logger.info(f"Overriding AI setting with explicit parameter: run_ai_analysis={run_ai_analysis}")
                    run_ai = run_ai_analysis
                    if run_ai and not ai_tests_to_run:
                        # Default tests if not specified
                        ai_tests_to_run = ['headings', 'reading_order', 'modals', 'language', 'animations', 'interactive', 'widgets', 'landmarks', 'media', 'live_regions', 'structure']
                        logger.info(f"Using default AI tests: {ai_tests_to_run}")
                
                # Get API key from config if not provided
                if not ai_api_key:
                    try:
                        from config import config
                        ai_api_key = getattr(config, 'CLAUDE_API_KEY', None)
                        if ai_api_key:
                            logger.info("Using CLAUDE_API_KEY from config")
                    except Exception as e:
                        logger.warning(f"Could not get CLAUDE_API_KEY from config: {e}")
                
                # Log decision factors
                
                # Run AI analysis if enabled
                if run_ai and ai_api_key and screenshot_bytes and ai_tests_to_run:
                    analyzer = None
                    try:
                        from auto_a11y.ai import ClaudeAnalyzer
                        
                        # Get page HTML
                        page_html = await browser_page.content()
                        
                        # Initialize analyzer
                        analyzer = ClaudeAnalyzer(ai_api_key)
                        
                        # Run only the selected AI tests
                        logger.info(f"Running AI accessibility analysis with tests: {ai_tests_to_run}")
                        ai_results = await analyzer.analyze_page(
                            screenshot=screenshot_bytes,
                            html=page_html,
                            analyses=ai_tests_to_run,
                            test_config=test_config
                        )
                        
                        findings_list: list[Any] = ai_results.get('findings', [])
                        ai_findings = findings_list
                        raw_results_dict: dict[str, Any] = ai_results.get('raw_results', {})
                        ai_analysis_results = raw_results_dict

                    except Exception as e:
                        logger.error(f"AI analysis failed: {e}")
                    finally:
                        # Clean up Claude client to avoid event loop errors
                        if analyzer and hasattr(analyzer, 'client'):
                            try:
                                await analyzer.client.aclose()
                            except Exception as cleanup_error:
                                logger.debug(f"Error cleaning up AI analyzer: {cleanup_error}")

                # Free screenshot bytes and page HTML now that AI analysis is done
                del screenshot_bytes
                screenshot_bytes = None

                # Calculate duration
                duration_ms = int((time.time() - start_time) * 1000)

                # Process results (including AI findings)
                test_result = self.result_processor.process_test_results(
                    page_id=page.id or '',
                    raw_results=raw_results,
                    screenshot_path=screenshot_path,
                    duration_ms=duration_ms,
                    ai_findings=ai_findings,
                    ai_analysis_results=ai_analysis_results
                )

                # Free raw results and AI data now that they've been processed
                del raw_results
                del ai_findings
                del ai_analysis_results

                # Add script violations to test result
                if script_violations:
                    logger.info(f"Adding {len(script_violations)} script violations to test result")
                    test_result.violations.extend(script_violations)
                    # violation_count is a read-only property; violations already extended above
                    # Keep the per-touchpoint checks summary consistent with the
                    # headline violation_count: process_test_results built the
                    # summary before these script violations existed, so fold
                    # them into metadata['checks'] now.
                    merge_violations_into_checks(test_result.metadata, script_violations)

                # Add test user information to metadata (Guest or authenticated user)
                if authenticated_user:
                    user_info: dict[str, Any] = {
                        'user_id': authenticated_user.id,
                        'username': authenticated_user.username,
                        'display_name': authenticated_user.display_name,
                        'roles': authenticated_user.roles
                    }
                    logger.info(f"Test completed as authenticated user: {authenticated_user.username}")
                else:
                    user_info = {
                        'user_id': None,
                        'username': 'guest',
                        'display_name': 'Guest',
                        'roles': []
                    }

                # Add to test result metadata
                test_result.metadata['authenticated_user'] = user_info

                # Add to each violation's metadata
                for violation in test_result.violations:
                    violation.metadata['authenticated_user'] = user_info

                # Add to each warning's metadata
                for warning in test_result.warnings:
                    warning.metadata['authenticated_user'] = user_info

                # Add to each info item's metadata
                for info in test_result.info:
                    info.metadata['authenticated_user'] = user_info

                # Add to each discovery item's metadata
                for discovery in test_result.discovery:
                    discovery.metadata['authenticated_user'] = user_info

                # Save test result to database
                result_id = self.db.create_test_result(test_result)
                test_result.mongo_id = ObjectId(result_id)

                # Free heavy data from memory now that it's persisted to DB
                test_result.js_test_results = {}
                test_result.ai_analysis_results = {}

                # Update page with test results
                page.status = PageStatus.TESTED
                page.last_tested = datetime.now()
                page.violation_count = test_result.violation_count
                page.warning_count = test_result.warning_count
                page.info_count = test_result.info_count
                page.discovery_count = test_result.discovery_count
                page.pass_count = test_result.pass_count
                page.test_duration_ms = duration_ms
                page.screenshot_path = screenshot_path  # Save screenshot path to page
                self.db.update_page(page)
                
                # Update website's last_tested timestamp atomically
                # (avoids read-modify-write race with parallel workers)
                self.db.websites.update_one(
                    {"_id": ObjectId(page.website_id)},
                    {"$set": {"last_tested": datetime.now()}}
                )

                # Page test completed successfully
                
                return test_result
                
        except Exception as e:
            logger.error(f"Error testing page {page.url}: {e}")
            
            # Update page status
            page.status = PageStatus.ERROR
            self.db.update_page(page)
            
            # Create error result
            test_result = TestResult(
                page_id=page.id or '',
                test_date=datetime.now(),
                duration_ms=int((time.time() - start_time) * 1000),
                error=str(e),
                violations=[],
                warnings=[],
                passes=[]
            )
            
            # Save error result
            result_id = self.db.create_test_result(test_result)
            test_result.mongo_id = ObjectId(result_id)
            
            return test_result

    async def test_loaded_browser_page(
        self,
        page: Page,
        browser_page: Any,
        *,
        take_screenshot: bool = True,
        run_ai_analysis: bool = False,
        ai_api_key: str | None = None,
    ) -> TestResult:
        """Run accessibility tests against an already-loaded Playwright page.

        Used by the manual-testing flow: the user has navigated themselves in a
        visible browser, and now wants the existing JS test suite + result
        processor + DB persistence applied to whatever is currently on screen.
        No navigation, no automated authentication, no PDF detection, no
        multi-state stepping; the page is taken exactly as-is.

        Args:
            page: ``Page`` model record this run is attributed to. Its URL
                does not need to match ``browser_page.url`` — the live
                browser state always wins for the actual test execution.
            browser_page: Live Playwright ``Page`` owned by the manual
                session. The caller is responsible for keeping it open.
            take_screenshot: Capture a screenshot for the report and AI
                analysis.
            run_ai_analysis: Force-enable AI analysis. Project config can
                also turn it on.
            ai_api_key: Anthropic API key. Falls back to ``CLAUDE_API_KEY``
                from app config.
        """
        start_time = time.time()
        try:
            wcag_level: str = "AA"
            project_config: dict[str, Any] | None = None
            test_config: Any = None
            try:
                website = self.db.get_website(page.website_id)
                if website:
                    project = self.db.get_project(website.project_id)
                    if project and project.config:
                        project_config = project.config
                        wcag_level = project_config.get("wcag_level", "AA")
                        from auto_a11y.config.test_config import (
                            TestConfiguration,
                        )

                        test_config = TestConfiguration(
                            database=self.db, debug_mode=True
                        )
                        if "touchpoints" in project_config:
                            test_config.config["touchpoints"] = project_config[
                                "touchpoints"
                            ]
                        test_config.config["global"]["run_ai_tests"] = (
                            project_config.get("enable_ai_testing", False)
                        )
                        if "ai_tests" in project_config:
                            for test_name in (
                                "headings",
                                "reading_order",
                                "modals",
                                "language",
                                "animations",
                                "interactive",
                                "widgets",
                                "landmarks",
                                "media",
                                "live_regions",
                                "structure",
                            ):
                                test_config.set_ai_test_enabled(
                                    test_name,
                                    test_name in project_config["ai_tests"],
                                )
            except Exception as e:
                logger.warning(
                    f"Manual test: could not load project config: {e}"
                )

            if test_config:
                self.script_injector.test_config = test_config
            self.script_injector.project_config = project_config

            await self.script_injector.inject_script_files(browser_page)
            await browser_page.evaluate(
                f'window.WCAG_LEVEL = "{wcag_level}";'
            )

            try:
                document_refs = self.db.get_document_references(
                    page.website_id
                )
                doc_metadata: dict[str, dict[str, Any]] = {}
                for doc_ref in document_refs:
                    if doc_ref.language:
                        doc_metadata[doc_ref.document_url] = {
                            "language": doc_ref.language,
                            "confidence": doc_ref.language_confidence,
                        }
                import json

                await browser_page.evaluate(
                    f"window.DOCUMENT_METADATA = {json.dumps(doc_metadata)};"
                )
            except Exception as e:
                logger.warning(
                    f"Manual test: could not inject document metadata: {e}"
                )
                await browser_page.evaluate("window.DOCUMENT_METADATA = {};")

            original_viewport: dict[str, int] | None = await browser_page.evaluate(
                "() => ({ width: window.innerWidth, height: window.innerHeight })"
            )

            raw_results = await self.script_injector.run_all_tests(
                browser_page
            )

            if original_viewport:
                try:
                    await browser_page.set_viewport_size(
                        {
                            "width": original_viewport["width"],
                            "height": original_viewport["height"],
                        }
                    )
                    await asyncio.sleep(0.1)
                except Exception as e:
                    logger.warning(
                        f"Manual test: could not restore viewport: {e}"
                    )

            screenshot_path: str | None = None
            screenshot_bytes: bytes | None = None
            if take_screenshot:
                screenshot_path, screenshot_bytes = (
                    await self._take_screenshot_with_bytes(
                        browser_page, page.id or ""
                    )
                )

            ai_findings: list[Any] = []
            ai_analysis_results: dict[str, Any] = {}
            run_ai: bool = bool(run_ai_analysis)
            ai_tests_to_run: list[str] = []
            if project_config and project_config.get(
                "enable_ai_testing", False
            ):
                run_ai = True
                ai_tests_to_run = project_config.get("ai_tests", [])
            if run_ai and not ai_tests_to_run:
                ai_tests_to_run = [
                    "headings",
                    "reading_order",
                    "modals",
                    "language",
                    "animations",
                    "interactive",
                    "widgets",
                    "landmarks",
                    "media",
                    "live_regions",
                    "structure",
                ]

            if not ai_api_key:
                try:
                    from config import config as _cfg

                    ai_api_key = getattr(_cfg, "CLAUDE_API_KEY", None)
                except Exception:
                    ai_api_key = None

            if run_ai and ai_api_key and screenshot_bytes and ai_tests_to_run:
                analyzer = None
                try:
                    from auto_a11y.ai import ClaudeAnalyzer

                    page_html = await browser_page.content()
                    analyzer = ClaudeAnalyzer(ai_api_key)
                    ai_results = await analyzer.analyze_page(
                        screenshot=screenshot_bytes,
                        html=page_html,
                        analyses=ai_tests_to_run,
                        test_config=test_config,
                    )
                    findings_val: list[Any] = ai_results.get("findings", [])
                    ai_findings = findings_val
                    raw_val: dict[str, Any] = ai_results.get("raw_results", {})
                    ai_analysis_results = raw_val
                except Exception as e:
                    logger.error(f"Manual test: AI analysis failed: {e}")
                finally:
                    if analyzer and hasattr(analyzer, "client"):
                        try:
                            await analyzer.client.aclose()
                        except Exception:
                            pass

            del screenshot_bytes
            screenshot_bytes = None

            duration_ms = int((time.time() - start_time) * 1000)

            test_result = self.result_processor.process_test_results(
                page_id=page.id or "",
                raw_results=raw_results,
                screenshot_path=screenshot_path,
                duration_ms=duration_ms,
                ai_findings=ai_findings,
                ai_analysis_results=ai_analysis_results,
            )
            del raw_results

            user_info: dict[str, Any] = {
                "user_id": None,
                "username": "manual",
                "display_name": "Manual test",
                "roles": [],
            }
            test_result.metadata["authenticated_user"] = user_info
            test_result.metadata["manual_test"] = True
            for violation in test_result.violations:
                violation.metadata["authenticated_user"] = user_info
            for warning in test_result.warnings:
                warning.metadata["authenticated_user"] = user_info
            for info in test_result.info:
                info.metadata["authenticated_user"] = user_info
            for discovery in test_result.discovery:
                discovery.metadata["authenticated_user"] = user_info

            result_id = self.db.create_test_result(test_result)
            test_result.mongo_id = ObjectId(result_id)
            test_result.js_test_results = {}
            test_result.ai_analysis_results = {}

            page.status = PageStatus.TESTED
            page.last_tested = datetime.now()
            page.violation_count = test_result.violation_count
            page.warning_count = test_result.warning_count
            page.info_count = test_result.info_count
            page.discovery_count = test_result.discovery_count
            page.pass_count = test_result.pass_count
            page.test_duration_ms = duration_ms
            page.screenshot_path = screenshot_path
            self.db.update_page(page)

            self.db.websites.update_one(
                {"_id": ObjectId(page.website_id)},
                {"$set": {"last_tested": datetime.now()}},
            )

            return test_result

        except Exception as e:
            logger.error(f"Manual test failed for {page.url}: {e}")
            page.status = PageStatus.ERROR
            self.db.update_page(page)
            test_result = TestResult(
                page_id=page.id or "",
                test_date=datetime.now(),
                duration_ms=int((time.time() - start_time) * 1000),
                error=str(e),
                violations=[],
                warnings=[],
                passes=[],
            )
            result_id = self.db.create_test_result(test_result)
            test_result.mongo_id = ObjectId(result_id)
            return test_result

    async def test_pdf(
        self,
        pdf_document_id: str,
        *,
        run_ai_analysis: bool = False,
        ai_api_key: str | None = None,
        wcag_level: Literal["AA", "AAA"] = "AA",
        locale: str = "en",
    ) -> TestResult:
        """Audit an existing :class:`PdfDocument` by ID.

        Thin delegate to :meth:`PdfRunner.audit_pdf_document`. Used by manual
        "test this PDF" UI actions and the opportunistic branch in
        :meth:`test_page`.

        Raises:
            RuntimeError: if no :class:`PdfRunner` was attached at construction.
        """
        if self._pdf_runner is None:
            raise RuntimeError(
                "TestRunner has no PdfRunner attached; cannot audit PDF documents."
            )
        return await self._pdf_runner.audit_pdf_document(
            pdf_document_id,
            run_ai=run_ai_analysis,
            ai_api_key=ai_api_key,
            wcag_level=wcag_level,
            locale=locale,
        )

    async def _handle_opportunistic_pdf(
        self,
        *,
        page: Page,
        browser_page: Any,
        response: Any,
        run_ai_analysis: bool,
        ai_api_key: str | None,
    ) -> TestResult:
        """Instance-method shim around :func:`handle_opportunistic_pdf`.

        Kept so the ``test_page`` call site stays a one-liner; the real
        logic lives in the module-level function so it can be tested
        directly without crossing the protected-member boundary.
        """
        return await handle_opportunistic_pdf(
            db=self.db,
            pdf_runner=self._pdf_runner,
            page=page,
            browser_page=browser_page,
            response=response,
            run_ai_analysis=run_ai_analysis,
            ai_api_key=ai_api_key,
        )

    async def test_page_multi_state(
        self,
        page: Page,
        enable_multi_state: bool = True,
        take_screenshot: bool = True,
        run_ai_analysis: bool = False,
        ai_api_key: str | None = None,
        website_user_id: str | None = None
    ) -> list[TestResult]:
        """
        Run accessibility tests on a single page across multiple states

        This method tests the page in multiple states by executing setup scripts
        and testing before/after each script execution as configured.

        Args:
            page: Page to test
            enable_multi_state: Whether to use multi-state testing
            take_screenshot: Whether to capture screenshots
            run_ai_analysis: Whether to run AI analysis
            ai_api_key: API key for AI analysis
            website_user_id: Optional ID of user to authenticate as

        Returns:
            List of test results (one per state)
        """
        if not enable_multi_state:
            # Fall back to single-state testing
            result = await self.test_page(page, take_screenshot, run_ai_analysis, ai_api_key, website_user_id)
            return [result]

        # Same transition as test_page: this branch does not delegate to it, so
        # without this a multi-state run would sit at QUEUED throughout.
        page.status = PageStatus.TESTING
        self.db.update_page(page)

        # Start browser if needed
        if not await self.browser_manager.is_running():
            await self.browser_manager.start()

        results = []
        browser_page = None

        try:
            # Don't use get_page() context manager for multi-state testing
            # because _prepare_browser_for_state restarts the browser and creates new pages
            # The context manager would try to close a stale page reference
            browser_page = await self.browser_manager.create_page()
            
            # Get wait strategy from project config
            wait_strategy = 'networkidle2'
            try:
                website = self.db.get_website(page.website_id)
                if website:
                    project = self.db.get_project(website.project_id)
                    if project and project.config:
                        wait_strategy = project.config.get('page_load_strategy', 'networkidle2')
            except Exception as e:
                logger.warning(f"Could not get project config: {e}")

            # STEP 1: Perform authentication FIRST if user specified
            authenticated_user = None
            logger.debug(f"DEBUG multi-state: website_user_id={website_user_id}")
            if website_user_id:
                # Try project user first, then website user
                user: Any = self.db.get_project_user(website_user_id)
                logger.debug(f"DEBUG multi-state: get_project_user returned: {user}")
                if not user:
                    user = self.db.get_website_user(website_user_id)
                    logger.debug(f"DEBUG multi-state: get_website_user returned: {user}")
                logger.debug(f"DEBUG multi-state: user={user}, user.enabled={user.enabled if user else 'N/A'}")
                if user and user.enabled:
                    # Check if already logged in as this user (cookies persist in shared context)
                    if self._logged_in_user and self._logged_in_user.id == user.id:
                        logger.debug(f"DEBUG multi-state: Already logged in as {user.username}, reusing session")
                        authenticated_user = self._logged_in_user
                    else:
                        logger.debug(f"DEBUG multi-state: About to authenticate as user: {user.username}")
                        login_result = await self.login_automation.perform_login(
                            browser_page,
                            user,
                            timeout=30000
                        )

                        if login_result['success']:
                            logger.info(f"Successfully authenticated as {user.username}")
                            authenticated_user = user
                            self._logged_in_user = user
                        else:
                            logger.error(f"Authentication failed: {login_result['error']}")

            # STEP 2: Navigate to test page (after authentication)
            logger.info(f"Navigating to test page: {page.url}")
            response = await self.browser_manager.goto(
                browser_page,
                page.url,
                wait_until=wait_strategy,
                timeout=30000
            )

            if not response:
                raise RuntimeError(f"Failed to load page: {page.url}")

            await browser_page.wait_for_selector('body', timeout=5000)

            # STEP 3: Now detect scripts (after we're on the test page, authenticated)
            # Start script session if not already started for this website
            if self._current_website_id != page.website_id:
                # End previous session if exists
                if self._current_website_id is not None:
                    self.session_manager.end_session()

                # Start new session for this website
                self.session_manager.start_session(page.website_id)
                self._current_website_id = page.website_id
                logger.info(f"Started script session for website {page.website_id}")

            # Get session ID
            session_id = self.session_manager.current_session.session_id if self.session_manager.current_session else None

            # Get all applicable scripts for this page
            scripts_to_execute = self.db.get_scripts_for_page_v2(
                page_id=page.id or '',
                website_id=page.website_id,
                enabled_only=True
            )

            # Filter scripts that have multi-state testing configured
            multi_state_scripts = [
                script for script in scripts_to_execute
                if script.test_before_execution or script.test_after_execution
            ]

            logger.debug(f"DEBUG: Found {len(scripts_to_execute)} scripts, filtering for multi-state")
            for script in scripts_to_execute:
                logger.debug(f"DEBUG: Script ID={script.id} '{script.name}' - test_before={script.test_before_execution}, test_after={script.test_after_execution}, clear_cookies={script.clear_cookies_before}, clear_localStorage={script.clear_local_storage_before}")

            if not multi_state_scripts:
                logger.info(f"No multi-state scripts configured for page {page.url}, using single-state testing")
                # Fall back to single test but we're already authenticated and on the page
                result = await self.test_page(page, take_screenshot, run_ai_analysis, ai_api_key, website_user_id)
                return [result]

            logger.debug(f"DEBUG: Testing page {page.url} with {len(multi_state_scripts)} multi-state scripts")

            # Create a test function that can be called multiple times
            async def run_single_test(browser_page: Any, page_id: str) -> TestResult:
                """Run accessibility tests and return TestResult"""
                # Verify browser connection before starting tests
                try:
                    ready_state = await wait_for(
                        browser_page.evaluate('() => document.readyState'),
                        timeout=5.0
                    )
                    logger.debug(f"DEBUG run_single_test: page readyState={ready_state}")
                except Exception as conn_err:
                    logger.error(f"Browser connection lost at start of run_single_test: {conn_err}")
                    raise

                # Get project config
                test_config = None
                project_config = None
                wcag_level = 'AA'

                try:
                    website = self.db.get_website(page.website_id)
                    if website:
                        project = self.db.get_project(website.project_id)
                        if project and project.config:
                            project_config = project.config
                            wcag_level = project_config.get('wcag_level', 'AA')

                            from auto_a11y.config.test_config import TestConfiguration
                            test_config = TestConfiguration(database=self.db, debug_mode=True)

                            if 'touchpoints' in project_config:
                                test_config.config['touchpoints'] = project_config['touchpoints']

                            test_config.config['global']['run_ai_tests'] = project_config.get('enable_ai_testing', False)
                except Exception as e:
                    logger.warning(f"Could not get project config: {e}")

                # Set test configuration
                if test_config:
                    self.script_injector.test_config = test_config
                self.script_injector.project_config = project_config

                # Inject test scripts
                logger.debug(f"DEBUG run_single_test: about to inject scripts")
                await self.script_injector.inject_script_files(browser_page)
                logger.debug(f"DEBUG run_single_test: scripts injected")

                # Verify connection still alive after injection
                try:
                    await wait_for(
                        browser_page.evaluate('() => true'),
                        timeout=2.0
                    )
                    logger.debug(f"DEBUG run_single_test: connection verified after injection")
                except Exception as conn_err:
                    logger.error(f"Connection lost after script injection: {conn_err}")
                    raise

                # Set WCAG level
                logger.debug(f"DEBUG run_single_test: setting WCAG level")
                await browser_page.evaluate(f'window.WCAG_LEVEL = "{wcag_level}";')
                logger.debug(f"DEBUG run_single_test: WCAG level set")

                # Store original viewport before running tests (some tests change it)
                logger.debug(f"DEBUG run_single_test: getting viewport")
                original_viewport = await browser_page.evaluate('() => ({ width: window.innerWidth, height: window.innerHeight })')
                logger.debug(f"DEBUG run_single_test: viewport stored, about to run tests")
                logger.debug(f"Stored original viewport: {original_viewport}")

                # Run tests
                raw_results = await self.script_injector.run_all_tests(browser_page)
                
                # Verify browser connection after all tests complete
                logger.debug("DEBUG run_single_test: all tests complete, verifying connection")
                try:
                    await wait_for(
                        browser_page.evaluate('() => document.readyState'),
                        timeout=5.0
                    )
                    logger.debug("DEBUG run_single_test: connection verified after tests")
                except Exception as conn_err:
                    logger.error(f"Browser connection lost after tests: {conn_err}")
                    raise

                # Restore original viewport after tests complete
                if original_viewport:
                    try:
                        await browser_page.set_viewport_size({
                            'width': original_viewport['width'],
                            'height': original_viewport['height']
                        })
                        logger.debug(f"Restored viewport to original size: {original_viewport}")
                        await asyncio.sleep(0.1)
                    except Exception as e:
                        logger.warning(f"Could not restore viewport: {e}")

                # Take screenshot if requested
                screenshot_path = None
                screenshot_bytes = None
                if take_screenshot:
                    # Verify browser is still connected before taking screenshot
                    try:
                        await wait_for(
                            browser_page.evaluate('() => document.readyState'),
                            timeout=5.0
                        )
                        logger.debug("DEBUG run_single_test: browser connected, taking screenshot")
                    except Exception as conn_err:
                        logger.error(f"Browser connection lost before screenshot: {conn_err}")
                        raise
                    
                    # Take screenshot once and save to file - also capture bytes for AI analysis
                    screenshot_path, screenshot_bytes = await self._take_screenshot_with_bytes(browser_page, page_id)
                    logger.debug(f"DEBUG run_single_test: screenshot done, path={screenshot_path}, bytes={len(screenshot_bytes) if screenshot_bytes else 0}")

                # Check if project has AI testing enabled
                ai_findings2: list[Any] = []
                ai_analysis_results2: dict[str, Any] = {}

                run_ai = False
                ai_tests_to_run: list[str] = []
                
                if project_config:
                    if project_config.get('enable_ai_testing', False):
                        run_ai = True
                        ai_tests_to_run = project_config.get('ai_tests', [])
                        logger.info(f"AI testing enabled, will run tests: {ai_tests_to_run}")
                
                # Get API key from config
                ai_api_key = None
                try:
                    from config import config
                    ai_api_key = getattr(config, 'CLAUDE_API_KEY', None)
                except Exception as e:
                    logger.warning(f"Could not get CLAUDE_API_KEY: {e}")
                
                
                # Run AI analysis if enabled
                if run_ai and ai_api_key and screenshot_bytes and ai_tests_to_run:
                    analyzer = None
                    try:
                        from auto_a11y.ai import ClaudeAnalyzer
                        
                        page_html = await browser_page.content()
                        analyzer = ClaudeAnalyzer(ai_api_key)
                        
                        logger.warning(f"Running AI accessibility analysis with tests: {ai_tests_to_run}")
                        ai_results = await analyzer.analyze_page(
                            screenshot=screenshot_bytes,
                            html=page_html,
                            analyses=ai_tests_to_run,
                            test_config=test_config
                        )
                        
                        findings_val: list[Any] = ai_results.get('findings', [])
                        ai_findings2 = findings_val
                        raw_val: dict[str, Any] = ai_results.get('raw_results', {})
                        ai_analysis_results2 = raw_val

                    except Exception as e:
                        logger.error(f"AI analysis failed: {e}")
                    finally:
                        if analyzer and hasattr(analyzer, 'client'):
                            try:
                                await analyzer.client.aclose()
                            except Exception:
                                pass

                # Free screenshot bytes and page HTML now that AI analysis is done
                del screenshot_bytes
                screenshot_bytes = None

                # Process results
                duration_ms = 0  # Will be set by caller
                test_result = self.result_processor.process_test_results(
                    page_id=page_id,
                    raw_results=raw_results,
                    screenshot_path=screenshot_path,
                    duration_ms=duration_ms,
                    ai_findings=ai_findings2,
                    ai_analysis_results=ai_analysis_results2
                )

                # Free raw results and AI data now that they've been processed
                del raw_results
                del ai_findings2
                del ai_analysis_results2

                return test_result

            # Run multi-state testing with fresh pages for stability
            results = await self.multi_state_runner.test_page_multi_state(
                page=browser_page,
                page_id=page.id or '',
                scripts=multi_state_scripts,
                test_function=run_single_test,
                session_id=session_id,
                browser_manager=self.browser_manager,
                page_url=page.url,
                authenticated_user=authenticated_user,
                login_automation=self.login_automation
            )

            # Add test user information to all results (Guest or authenticated user)
            if authenticated_user:
                user_info: dict[str, Any] = {
                    'user_id': authenticated_user.id,
                    'username': authenticated_user.username,
                    'display_name': authenticated_user.display_name,
                    'roles': authenticated_user.roles
                }
                logger.info(f"Multi-state tests completed as authenticated user: {authenticated_user.username}")
            else:
                user_info = {
                    'user_id': None,
                    'username': 'guest',
                    'display_name': 'Guest',
                    'roles': []
                }

            for result in results:
                # Add to result metadata
                result.metadata['authenticated_user'] = user_info

                # Add to each violation's metadata
                for violation in result.violations:
                    violation.metadata['authenticated_user'] = user_info

                # Add to each warning's metadata
                for warning in result.warnings:
                    warning.metadata['authenticated_user'] = user_info

                # Add to each info item's metadata
                for info in result.info:
                    info.metadata['authenticated_user'] = user_info

                # Add to each discovery item's metadata
                for discovery in result.discovery:
                    discovery.metadata['authenticated_user'] = user_info

            # Save all results to database and free heavy data from memory
            for result in results:
                result_id = self.db.create_test_result(result)
                result.mongo_id = ObjectId(result_id)
                result.js_test_results = {}
                result.ai_analysis_results = {}

            # Update page with results from final state
            if results:
                final_result = results[-1]
                page.status = PageStatus.TESTED
                page.last_tested = datetime.now()
                page.violation_count = final_result.violation_count
                page.warning_count = final_result.warning_count
                page.info_count = final_result.info_count
                page.discovery_count = final_result.discovery_count
                page.pass_count = final_result.pass_count
                page.test_duration_ms = sum(r.duration_ms for r in results)
                page.screenshot_path = final_result.screenshot_path
                self.db.update_page(page)

            # Update website's last_tested timestamp atomically
            # (avoids read-modify-write race with parallel workers)
            self.db.websites.update_one(
                {"_id": ObjectId(page.website_id)},
                {"$set": {"last_tested": datetime.now()}}
            )

            logger.info(f"Multi-state testing complete: {len(results)} test results generated")

            return results

        except Exception as e:
            logger.error(f"Error in multi-state testing for page {page.url}: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")

            # Update page status
            page.status = PageStatus.ERROR
            self.db.update_page(page)

            # Create error result
            test_result = TestResult(
                page_id=page.id or '',
                test_date=datetime.now(),
                duration_ms=0,
                error=str(e),
                violations=[],
                warnings=[],
                passes=[]
            )

            # Save error result
            result_id = self.db.create_test_result(test_result)
            test_result.mongo_id = ObjectId(result_id)

            return [test_result]
        
        finally:
            # Close the initial browser page created at the top of this method.
            # The multi_state_runner closes contexts it creates internally, but the
            # initial browser_page from create_page() must be closed here to avoid
            # leaking a Chromium renderer process per page tested.
            if browser_page is not None:
                try:
                    await self.browser_manager.close_page(browser_page)
                except Exception as e:
                    logger.debug(f"Error closing browser page after multi-state test: {e}")

    async def test_pages(
        self,
        pages: list[Page],
        parallel: int = 1,
        take_screenshots: bool = True,
        progress_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        website_user_id: str | None = None
    ) -> dict[str, Any]:
        """
        Test multiple pages with multi-state support.

        Results are saved to the database as each page completes. This method
        returns a lightweight summary instead of accumulating all TestResult
        objects in memory, which is critical for large runs (10,000+ pages).

        Args:
            pages: Pages to test
            parallel: Number of parallel tests
            take_screenshots: Whether to capture screenshots
            progress_callback: Progress callback function
            website_user_id: Optional user ID for authenticated testing

        Returns:
            Summary dict with pages_tested, total_violations, total_warnings,
            total_passes, total_duration_ms counts
        """
        total = len(pages)
        completed = 0

        # Track summary stats incrementally instead of accumulating results
        total_results = 0
        total_violations = 0
        total_warnings = 0
        total_passes = 0
        total_duration_ms = 0

        # Process pages in batches
        for i in range(0, total, parallel):
            batch = pages[i:i+parallel]

            # Test batch in parallel - use test_page_multi_state for each page
            tasks = [
                self.test_page_multi_state(
                    page=page,
                    enable_multi_state=True,
                    take_screenshot=take_screenshots,
                    website_user_id=website_user_id
                )
                for page in batch
            ]

            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            # Accumulate summary stats, then discard result objects
            for result_list in batch_results:
                if isinstance(result_list, BaseException):
                    logger.error(f"Test failed with exception: {result_list}")
                else:
                    for result in result_list:
                        total_results += 1
                        total_violations += result.violation_count
                        total_warnings += result.warning_count
                        total_passes += result.pass_count
                        total_duration_ms += result.duration_ms

            # Explicitly free batch results
            del batch_results

            completed += len(batch)

            # Update progress
            if progress_callback:
                await progress_callback({
                    'completed': completed,
                    'total': total,
                    'percentage': (completed / total) * 100
                })

        return {
            'pages_tested': total_results,
            'total_violations': total_violations,
            'total_warnings': total_warnings,
            'total_passes': total_passes,
            'average_duration_ms': total_duration_ms / total_results if total_results else 0
        }
    
    async def test_website(
        self,
        website_id: str,
        page_filter: dict[str, Any] | None = None,
        parallel: int = 1
    ) -> dict[str, Any]:
        """
        Test all pages in a website
        
        Args:
            website_id: Website ID
            page_filter: Optional filter for pages
            parallel: Number of parallel tests
            
        Returns:
            Test summary
        """
        website = self.db.get_website(website_id)
        if not website:
            raise ValueError(f"Website {website_id} not found")
        
        # Get pages to test
        pages = self.db.get_pages(website_id)
        
        # Apply filter if provided
        if page_filter:
            if page_filter.get('untested_only'):
                pages = [p for p in pages if p.needs_testing]
            if page_filter.get('priority'):
                pages = [p for p in pages if p.priority == page_filter['priority']]
        
        if not pages:
            return {
                'website_id': website_id,
                'pages_tested': 0,
                'message': 'No pages to test'
            }
        
        logger.info(f"Testing {len(pages)} pages for website {website_id}")

        # Test pages - returns summary dict (results are saved to DB as each page completes)
        summary = await self.test_pages(pages, parallel=parallel)

        # Update website last_tested timestamp atomically
        self.db.websites.update_one(
            {"_id": ObjectId(website.mongo_id)},
            {"$set": {"last_tested": datetime.now()}}
        )

        summary['website_id'] = website_id
        return summary
    
    def _save_screenshot_bytes(self, screenshot_bytes: bytes, page_id: str) -> str | None:
        # Write to a sibling .tmp file, fsync, then os.replace into final name.
        # This guarantees the serve route only ever sees a complete file at the
        # final path — closing the race window where a partial write could be
        # served while the screenshot was still being captured.
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"page_{page_id}_{timestamp}.jpg"
        final_path = self.screenshot_dir / filename
        tmp_path = final_path.with_name(final_path.name + '.tmp')

        try:
            with open(tmp_path, 'wb') as f:
                f.write(screenshot_bytes)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, final_path)
        except OSError as e:
            logger.error(f"Failed to write screenshot {final_path}: {e}")
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            return None

        if not final_path.is_file():
            logger.error(f"Screenshot rename completed but file not found at {final_path}")
            return None

        try:
            relative_path = os.path.relpath(final_path, os.getcwd())
            logger.debug(f"Screenshot saved: {final_path} (relative: {relative_path})")
            return relative_path
        except ValueError:
            # Different drives on Windows — fall back to a path the templates can still split.
            logger.debug(f"Screenshot saved: {final_path} (returning: {self.screenshot_dir.name}/{filename})")
            return f"{self.screenshot_dir.name}/{filename}"

    async def _take_screenshot(self, browser_page: Any, page_id: str) -> str | None:
        """
        Take screenshot of page

        Args:
            browser_page: Playwright Page object
            page_id: Page ID for filename

        Returns:
            Screenshot file path (relative to project root for Flask static serving)
        """
        try:
            screenshot_bytes = await self.browser_manager.take_screenshot(
                browser_page,
                path=None,
                full_page=True
            )
        except Exception as e:
            logger.error(f"Failed to take screenshot: {e}")
            return None

        if not screenshot_bytes:
            logger.error("Screenshot capture returned no bytes")
            return None

        return self._save_screenshot_bytes(screenshot_bytes, page_id)

    async def _take_screenshot_with_bytes(self, browser_page: Any, page_id: str) -> tuple[str | None, bytes | None]:
        """
        Take screenshot of page and return both path and bytes.

        Args:
            browser_page: Playwright Page object
            page_id: Page ID for filename

        Returns:
            Tuple of (screenshot file path, screenshot bytes)
        """
        try:
            screenshot_bytes = await browser_page.screenshot(
                full_page=True,
                type='jpeg',
                quality=80,
            )
        except Exception as e:
            logger.error(f"Failed to take screenshot: {e}")
            return None, None

        if not screenshot_bytes:
            logger.error("Screenshot capture returned no bytes")
            return None, None

        path = self._save_screenshot_bytes(screenshot_bytes, page_id)
        if path is None:
            # File didn't make it to disk; do not advertise a path the serve
            # route cannot satisfy. AI analysis can still use the in-memory bytes.
            return None, screenshot_bytes
        return path, screenshot_bytes
    
    async def cleanup(self) -> None:
        """Clean up resources"""
        await self.browser_manager.stop()


class TestJob:
    """Represents a testing job"""
    
    def __init__(self, job_id: str, pages: list[Page]) -> None:
        """
        Initialize test job

        Args:
            job_id: Job ID
            pages: Pages to test
        """
        self.job_id: str = job_id
        self.pages: list[Page] = pages
        self.status: str = 'pending'
        self.progress: dict[str, Any] = {
            'total': len(pages),
            'completed': 0,
            'failed': 0,
            'current_page': None,
            'started_at': None,
            'completed_at': None,
            'results': []
        }
    
    async def run(self, test_runner: TestRunner) -> None:
        """
        Run the test job

        Args:
            test_runner: Test runner instance
        """
        self.status = 'running'
        self.progress['started_at'] = datetime.now()
        
        try:
            for page in self.pages:
                self.progress['current_page'] = page.url
                
                try:
                    result = await test_runner.test_page(page)
                    self.progress['results'].append(result)
                    self.progress['completed'] += 1
                except Exception as e:
                    logger.error(f"Failed to test page {page.url}: {e}")
                    self.progress['failed'] += 1
            
            self.status = 'completed'
            
        except Exception as e:
            logger.error(f"Test job {self.job_id} failed: {e}")
            self.status = 'failed'
            
        finally:
            self.progress['completed_at'] = datetime.now()