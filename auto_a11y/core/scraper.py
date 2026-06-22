"""
Web scraping engine using Playwright browser automation
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal, Protocol, TYPE_CHECKING, cast
from collections.abc import Callable, Coroutine
from urllib.parse import urlparse, urljoin, urlunparse
from urllib.robotparser import RobotFileParser
from datetime import datetime
import re
from io import BytesIO

from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import Page as PlaywrightPage


class ClickablePage(Protocol):
    """Subset of the Playwright Page API used by SPA click-discovery.

    Defining a Protocol here lets unit tests pass an in-process fake
    that satisfies the same structural interface, without requiring a
    real Playwright Page instance and without any ``cast`` at the call
    site.

    Member signatures intentionally mirror Playwright's ``async_api`` so
    that ``PlaywrightPage`` satisfies this Protocol structurally and strict
    type-checkers (mypy, pyright, ty) accept passing a real page directly.
    """

    @property
    def url(self) -> str: ...

    async def evaluate(self, expression: str, arg: object = ...) -> object: ...

    async def click(
        self,
        selector: str,
        *,
        timeout: float | None = ...,
    ) -> None: ...

    async def query_selector(self, selector: str) -> object: ...

    async def wait_for_load_state(
        self,
        state: Literal['domcontentloaded', 'load', 'networkidle'] | None = ...,
        *,
        timeout: float | None = ...,
    ) -> None: ...

    async def wait_for_selector(
        self,
        selector: str,
        *,
        timeout: float | None = ...,
        state: Literal['attached', 'detached', 'hidden', 'visible'] | None = ...,
        strict: bool | None = ...,
    ) -> object: ...


from auto_a11y.models.page import Page, PageStatus
from auto_a11y.models.website import Website
from auto_a11y.models.discovery_run import DiscoveryRun, DiscoveryStatus
from auto_a11y.core.database import Database
from auto_a11y.core.browser_manager import BrowserManager

if TYPE_CHECKING:
    from auto_a11y.core.scraping_job import ScrapingJob
    from auto_a11y.testing.login_automation import LoginAutomation

# Note: ScrapingJob class has been moved to scraping_job.py for database-backed implementation

logger = logging.getLogger(__name__)


# Error-reason prefixes that mark a failed-page record as an "expected skip" —
# a valid outcome of visiting a URL (external redirect, outside base path) that
# is tracked for reporting but must NOT count toward the failure thresholds
# that can stop discovery early. Real scraper errors (timeouts, navigation
# failures, browser crashes) still count.
_EXPECTED_SKIP_REASON_PREFIXES = (
    "Redirected to external domain",
    "Redirected outside base path",
)

# SPA click-discovery constants
MAX_CLICK_CANDIDATES_PER_PAGE: int = 50
CLICK_TIMEOUT_MS: int = 5000
POST_CLICK_SETTLE_MS: int = 1500
DESTRUCTIVE_ANCHOR_PATTERN: re.Pattern[str] = re.compile(
    r"\b(log ?out|sign ?out|delete|remove|submit|unsubscribe)\b",
    re.IGNORECASE,
)

# Collects every <a> on the page and computes whether its href is
# usable (real navigation target) or not (#, empty, javascript:).
_CLICK_CANDIDATES_JS = """
() => {
  const here = window.location.href;
  function xpathOf(el) {
    if (!el || el.nodeType !== 1) return '';
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && cur !== document.documentElement) {
      let sib = cur, idx = 1;
      while ((sib = sib.previousElementSibling)) {
        if (sib.nodeName === cur.nodeName) idx++;
      }
      parts.unshift(cur.nodeName.toLowerCase() + '[' + idx + ']');
      cur = cur.parentElement;
    }
    return '/html/' + parts.join('/');
  }
  function isUsable(a) {
    const raw = a.getAttribute('href');
    if (raw === null || raw === '' || raw.startsWith('javascript:')) return false;
    // Anchors whose resolved href equals the current URL (typical of href="#")
    // are not directly navigable.
    return a.href !== here;
  }
  return Array.from(document.querySelectorAll('a')).map(a => ({
    hasUsableHref: isUsable(a),
    text: (a.textContent || '').trim(),
    ariaLabel: a.getAttribute('aria-label') || '',
    selector: xpathOf(a),
  }));
}
"""


def _is_expected_skip(page: Page | None) -> bool:
    """
    Return True if ``page`` represents an expected skip rather than a real
    scraper failure. Expected skips are tracked in ``failed_pages`` for
    reporting but should not increment the counters that can abort discovery.
    """
    if not page or page.status != PageStatus.DISCOVERY_FAILED:
        return False
    reason = page.error_reason or ""
    return reason.startswith(_EXPECTED_SKIP_REASON_PREFIXES)


class ScrapingEngine:
    """Web scraping engine for page discovery"""

    def __init__(self, database: Database, browser_config: dict[str, Any]) -> None:
        """
        Initialize scraping engine

        Args:
            database: Database connection
            browser_config: Browser configuration
        """
        self.db = database
        self.browser_manager = BrowserManager(browser_config)
        self.discovered_urls: set[str] = set()
        self.queued_urls: set[str] = set()
        self.robots_cache: dict[str, RobotFileParser] = {}

    async def discover_website(
        self,
        website: Website,
        progress_callback: Callable[..., Coroutine[Any, Any, Any]] | None = None,
        job: ScrapingJob | None = None,
        website_user_id: str | None = None,
        login_automation: LoginAutomation | None = None
    ) -> list[Page]:
        """
        Discover all pages in a website

        Args:
            website: Website to discover
            progress_callback: Optional callback for progress updates
            job: Optional scraping job for cancellation checks
            website_user_id: Optional website user ID for authenticated discovery (empty string for guest)
            login_automation: Optional LoginAutomation instance for performing login

        Returns:
            List of discovered pages
        """
        logger.info(f"Starting discovery for website: {website.url}")
        assert website.id is not None, "Website must have an ID"
        website_id: str = website.id

        # Create a new discovery run
        discovery_run = DiscoveryRun(
            website_id=website_id,
            started_at=datetime.now(),
            status=DiscoveryStatus.RUNNING,
            max_pages=website.scraping_config.max_pages,
            max_depth=website.scraping_config.max_depth,
            follow_external=website.scraping_config.follow_external,
            respect_robots=website.scraping_config.respect_robots,
            spa_click_discovery=website.scraping_config.spa_click_discovery,
            spa_ready_selector=website.scraping_config.spa_ready_selector,
            triggered_by=job.user_id if job and hasattr(job, 'user_id') else 'manual',
            job_id=job.job_id if job and hasattr(job, 'job_id') else None
        )
        discovery_run_id = self.db.create_discovery_run(discovery_run)
        logger.info(f"Created discovery run {discovery_run_id} for website {website.id}")
        
        # Get the previous latest run for comparison
        previous_run = None
        previous_runs = self.db.get_discovery_runs(website_id)
        if len(previous_runs) > 1:  # More than just the current run
            previous_run = previous_runs[1]  # Second item is the previous latest
        
        # Reset discovery state
        self.discovered_urls.clear()
        self.queued_urls.clear()
        
        # Parse base URL
        base_url = self._normalize_url(website.url)
        assert base_url is not None, "Website URL must be normalizable"
        parsed_base = urlparse(base_url)
        base_domain: str = parsed_base.netloc

        # Extract base path - if URL points to a file, get its directory
        base_path: str = parsed_base.path.rstrip('/')
        if base_path and '.' in base_path.split('/')[-1]:
            # Last component has an extension (like index.html), strip it to get directory
            base_path = '/'.join(base_path.split('/')[:-1])

        logger.info(f"Discovery starting from {base_url} (base_domain: {base_domain}, base_path: {base_path})")

        # Initialize queue with starting URL
        self.queued_urls.add(base_url)
        
        # Track discovered pages (successful only) and failed pages (for error reporting)
        discovered_pages: list[Page] = []
        failed_pages: list[Page] = []  # Track failed discoveries separately - these won't be saved to DB
        robots_blocked = 0  # URLs skipped due to robots.txt (not a scraper failure; surfaced to the user)
        
        # Start browser once for entire discovery session
        try:
            await self.browser_manager.start()
            logger.info("Browser started for discovery session")
        except Exception as e:
            error_msg = f"Failed to start browser: {e}. Make sure Chromium is installed (run: python run.py --download-browser)"
            logger.error(error_msg)
            if progress_callback:
                await progress_callback({
                    'status': 'failed',
                    'error': error_msg
                })
            raise RuntimeError(error_msg)

        # Interactive auth delay: pause so the user can sign in manually
        # in the visible browser window before discovery begins.
        _auth_delay: int = int(self.browser_manager.config.get('INTERACTIVE_AUTH_DELAY_SECONDS', 0))
        if _auth_delay > 0:
            _auth_page = await self.browser_manager.create_page()
            try:
                await self.browser_manager.goto(
                    page=_auth_page,
                    url=website.url,
                    wait_until='domcontentloaded',
                    timeout=30000,
                )
                logger.info(
                    "Interactive auth delay: waiting %ds for manual sign-in at %s. Sign in now.",
                    _auth_delay,
                    website.url,
                )
                await asyncio.sleep(_auth_delay)
                logger.info("Interactive auth delay: complete; proceeding with discovery.")
            finally:
                try:
                    await _auth_page.close()
                except Exception:
                    pass

        # Helper function to perform authentication
        async def perform_authentication(context_msg: str = "") -> Any:
            """Perform authentication and return authenticated user or None"""
            if not website_user_id or not login_automation:
                return None

            try:
                user = self.db.get_project_user(website_user_id)
                if not user:
                    logger.warning(f"Project user {website_user_id} not found for authentication")
                    return None

                if not user.enabled:
                    logger.warning(f"User {user.username} is disabled, skipping authentication{context_msg}")
                    return None

                logger.info(f"Authenticating as user: {user.username} (roles: {user.role_display}){context_msg}")

                # Ensure browser is running and connected
                await self.browser_manager.ensure_running()

                # Create a new page for login (handles viewport, user agent, stealth)
                browser_page = await self.browser_manager.create_page()

                # Perform login
                login_result = await login_automation.perform_login(
                    browser_page,
                    user,
                    timeout=30000
                )

                if login_result['success']:
                    logger.info(f"Successfully authenticated as {user.username} in {login_result['duration_ms']}ms{context_msg}")
                    # Keep the page alive for subsequent discovery - DON'T close it yet
                    return user
                else:
                    logger.error(f"Failed to authenticate as {user.username}: {login_result.get('error', 'Unknown error')}{context_msg}")
                    # Close the page on login failure
                    try:
                        await browser_page.close()
                        if browser_page in self.browser_manager.pages:
                            self.browser_manager.pages.remove(browser_page)
                    except:
                        pass
                    return None
            except Exception as e:
                logger.error(f"Error during authentication{context_msg}: {e}")
                return None

        # Perform initial authentication if user specified
        authenticated_user = await perform_authentication(" for initial discovery")

        # If authenticated, capture the post-login landing page and add to queue
        if authenticated_user:
            try:
                # Get the authenticated page from the browser manager's pages list
                if self.browser_manager.pages:
                    browser_page = self.browser_manager.pages[0]  # The authenticated page
                    post_login_url = browser_page.url
                    logger.info(f"Post-login page URL: {post_login_url}")
                    if post_login_url and post_login_url != base_url:
                        # Normalize and add the post-login page to discovery queue
                        normalized_post_login = self._normalize_url(post_login_url)
                        if normalized_post_login and normalized_post_login not in self.discovered_urls:
                            self.queued_urls.add(normalized_post_login)
                            logger.info(f"Added post-login page to discovery queue: {normalized_post_login}")

                    # Note: We leave the authenticated page open to avoid connection issues
                    # The browser will be cleaned up when discovery completes
                    # The cookies are stored at the browser level, so new pages will inherit them
                    logger.debug("Keeping authenticated browser page open to maintain session")
            except Exception as e:
                logger.warning(f"Error capturing post-login page: {e}")

        # Mark all existing pages as not in latest discovery up front
        # so each page can be individually saved as it's found
        self.db.mark_pages_not_in_latest_discovery(website_id)

        try:
            depth = 0
            max_pages_reached = False
            start_time = datetime.now()
            max_discovery_time = 7200  # 2 hours max (increased from 30 minutes)
            consecutive_failures = 0  # Track consecutive failures
            max_consecutive_failures = 10  # Restart browser after 10 consecutive failures
            total_failures = 0  # Track total failures
            max_total_failures = 50  # Stop if too many total failures
            pages_since_restart = 0  # Track pages processed since last browser restart
            max_pages_per_session = 500  # Restart browser periodically to prevent memory issues
            logger.info(f"Starting discovery with max_pages={website.scraping_config.max_pages}, max_depth={website.scraping_config.max_depth}")
            
            while self.queued_urls and depth <= website.scraping_config.max_depth and not max_pages_reached:
                # Check for cancellation
                if job and job.is_cancelled():
                    logger.info(f"Discovery cancelled by user for website {website.id}")
                    break
                    
                # Check if discovery has been running too long
                elapsed_time = (datetime.now() - start_time).total_seconds()
                if elapsed_time > max_discovery_time:
                    logger.warning(f"Discovery timeout reached after {elapsed_time:.0f} seconds")
                    break
                    
                # Memory management - clear discovered URLs set periodically to prevent excessive memory usage
                if len(self.discovered_urls) > 10000:
                    logger.info(f"Clearing discovered URLs cache (had {len(self.discovered_urls)} entries)")
                    # Keep only the last 5000 URLs to maintain some duplicate detection
                    recent_urls = list(self.discovered_urls)[-5000:]
                    self.discovered_urls = set(recent_urls)
                    
                # Get URLs at current depth
                current_batch = list(self.queued_urls)
                self.queued_urls.clear()
                
                logger.info(f"Processing depth {depth} with {len(current_batch)} URLs, discovered so far: {len(discovered_pages)}")
                logger.info(f"Elapsed time: {elapsed_time:.0f}s, Memory: {len(self.discovered_urls)} URLs cached")
                
                for i, url in enumerate(current_batch):
                    # Add small delay every 5 URLs to allow cancellation to be processed
                    if i > 0 and i % 5 == 0:
                        await asyncio.sleep(0.1)
                    
                    # Restart browser periodically to prevent memory issues
                    if pages_since_restart >= max_pages_per_session:
                        logger.info(f"Restarting browser after {pages_since_restart} pages to prevent memory issues")
                        try:
                            await self.browser_manager.stop()
                            await asyncio.sleep(2)
                            await self.browser_manager.start()
                            pages_since_restart = 0
                            logger.info("Browser restarted successfully (periodic restart)")

                            # Re-authenticate after restart
                            authenticated_user = await perform_authentication(" after periodic browser restart")
                        except Exception as e:
                            logger.error(f"Failed to restart browser (periodic): {e}")
                            max_pages_reached = True
                            break
                    
                    # Check for cancellation
                    if job and job.is_cancelled():
                        logger.info(f"Discovery cancelled during URL processing for website {website.id}")
                        max_pages_reached = True  # Use this flag to exit both loops
                        break
                    
                    # Check if browser is still running before each page
                    if not await self.browser_manager.is_running():
                        logger.warning("Browser stopped during discovery, attempting restart...")
                        try:
                            await self.browser_manager.ensure_running()
                            logger.info("Browser restarted successfully after connection loss")
                            pages_since_restart = 0

                            # Re-authenticate after restart
                            authenticated_user = await perform_authentication(" after connection loss restart")
                        except Exception as e:
                            logger.error(f"Failed to restart browser after connection loss: {e}")
                            max_pages_reached = True  # Use this flag to exit both loops
                            break
                    
                    # Check if we've hit page limit
                    if len(discovered_pages) >= website.scraping_config.max_pages:
                        logger.warning(f"Reached max pages limit: {website.scraping_config.max_pages} (discovered: {len(discovered_pages)})")
                        max_pages_reached = True
                        break
                    
                    # Skip if already discovered
                    if url in self.discovered_urls:
                        continue
                    
                    # Pre-filter URLs that are known to cause problems
                    # These often redirect to external sites or cause timeouts
                    problematic_params = ['?share=', '&share=', '?nb=', '&nb=', 'utm_', 'fbclid=', 'gclid=',
                                        '#disqus_thread', '#comments', '?print=', '&print=',
                                        'javascript:', 'mailto:', 'tel:']
                    # Document-file extensions are matched only against the URL path
                    # (not the hostname), so hostnames like host.docker.internal that
                    # happen to contain ".doc" as a substring are not rejected.
                    document_extensions = ('.pdf', '.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx')
                    url_path_lower = urlparse(url).path.lower()
                    if any(param in url.lower() for param in problematic_params) or url_path_lower.endswith(document_extensions):
                        logger.info(f"Skipping URL with problematic parameters: {url}")
                        # Track as failed for error reporting but don't save to DB
                        failed_page = Page(
                            website_id=website_id,
                            url=url,
                            title="Skipped: Problematic URL",
                            discovered_from=website.url if depth == 0 else None,
                            depth=depth,
                            status=PageStatus.DISCOVERY_FAILED,
                            error_reason="Skipped: URL contains parameters that typically cause problems"
                        )
                        failed_pages.append(failed_page)  # Track for error reporting only
                        self.discovered_urls.add(url)
                        continue
                    
                    # Check robots.txt
                    if website.scraping_config.respect_robots:
                        if not await self._can_fetch(url):
                            # Not a scraper failure -- the site simply disallows
                            # crawling. Count it so the run can tell the user why
                            # nothing was discovered (the silent skip used to look
                            # like a broken scan / user-agent rejection).
                            robots_blocked += 1
                            logger.info(f"Skipping {url} due to robots.txt (robots_blocked={robots_blocked})")
                            continue
                    
                    # Discover page
                    logger.info(f"[Page {len(discovered_pages) + 1}/{website.scraping_config.max_pages}] Starting discovery: {url}")
                    page = await self._discover_page(
                        url=url,
                        website=website,
                        depth=depth,
                        base_domain=base_domain,
                        base_path=base_path
                    )
                    if page:
                        status_msg = "SUCCESS" if page.status != PageStatus.DISCOVERY_FAILED else f"FAILED: {page.error_reason[:50] if page.error_reason else 'Unknown'}"
                        logger.info(f"[Page {len(discovered_pages)}/{website.scraping_config.max_pages}] {status_msg} - {url}")
                    else:
                        logger.warning(f"[Page {len(discovered_pages)}/{website.scraping_config.max_pages}] NULL RESPONSE - {url}")
                    
                    if page:
                        self.discovered_urls.add(url)
                        pages_since_restart += 1
                        
                        # Track failures separately - don't add to discovered_pages
                        if page.status == PageStatus.DISCOVERY_FAILED:
                            failed_pages.append(page)  # Track for error reporting only

                            if _is_expected_skip(page):
                                # Expected skip (external redirect, outside base path).
                                # Not a scraper error — don't count toward failure
                                # thresholds that can stop discovery. Reset the
                                # consecutive-failure counter since this wasn't a
                                # browser/connection problem.
                                logger.info(
                                    f"Expected skip (not counted as failure): {page.error_reason}"
                                )
                                consecutive_failures = 0
                            else:
                                consecutive_failures += 1
                                total_failures += 1
                                logger.warning(f"Failed pages: {total_failures} total, {consecutive_failures} consecutive")

                                # Stop if too many total failures
                                if total_failures >= max_total_failures:
                                    logger.error(f"Too many total failures ({total_failures}), stopping discovery")
                                    max_pages_reached = True
                                    break
                        else:
                            # Save page to database immediately
                            self.db.save_discovered_page(page, discovery_run_id)
                            discovered_pages.append(page)
                            # Reset consecutive counter on success
                            consecutive_failures = 0
                        
                        # Update progress
                        if progress_callback:
                            progress_data = {
                                'pages_found': len(discovered_pages),
                                'pages_failed': len(failed_pages),
                                'current_depth': depth,
                                'queue_size': len(self.queued_urls),
                                'current_url': url
                            }
                            # Include failure details if this was a failed page
                            if page.status == PageStatus.DISCOVERY_FAILED:
                                progress_data['last_failed_url'] = page.url
                                progress_data['last_failed_reason'] = page.error_reason or 'Unknown error'
                            logger.info(f"Starting progress update for page {len(discovered_pages)} (failed: {len(failed_pages)})")
                            try:
                                await progress_callback(progress_data)
                                logger.info(f"Progress update completed for page {len(discovered_pages)}")
                            except Exception as e:
                                logger.error(f"Progress callback failed: {e}")
                        else:
                            logger.warning("No progress_callback provided to ScrapingEngine")
                    else:
                        # Page discovery failed
                        consecutive_failures += 1
                        logger.warning(f"Page discovery failed, consecutive failures: {consecutive_failures}")
                        
                        # If too many consecutive failures, try restarting browser
                        if consecutive_failures >= max_consecutive_failures:
                            logger.warning(f"Too many consecutive failures ({consecutive_failures}), attempting browser restart")
                            try:
                                await self.browser_manager.stop()
                                await asyncio.sleep(2)  # Brief pause
                                await self.browser_manager.start()
                                logger.info("Browser restarted successfully after failures")
                                consecutive_failures = 0  # Reset counter
                                pages_since_restart = 0  # Reset page counter
                            except Exception as e:
                                logger.error(f"Failed to restart browser: {e}")
                                max_pages_reached = True
                                break
                        
                        # Check if browser is still running
                        if not await self.browser_manager.is_running():
                            logger.error("Browser failed during page discovery, stopping")
                            max_pages_reached = True
                            break
                    
                    # Respect rate limiting
                    await asyncio.sleep(website.scraping_config.request_delay)
                
                depth += 1
            
            # Check if discovery was cancelled
            was_cancelled = job and job.is_cancelled()
            
            # Log final statistics (discovered_pages now only contains successful ones)
            # Pages were already saved individually during discovery
            logger.info(f"Discovery finished: {len(discovered_pages)} successful, {len(failed_pages)} failed")
            
            # Compare with previous discovery if it exists
            if previous_run and previous_run.id:
                comparison = self.db.compare_discoveries(
                    website_id,
                    previous_run.id,
                    discovery_run_id
                )
                discovery_run.pages_added = int(comparison['added_count'])
                discovery_run.pages_removed = int(comparison['removed_count'])
                discovery_run.pages_unchanged = int(comparison['unchanged_count'])
                logger.info(f"Discovery comparison: +{discovery_run.pages_added} added, -{discovery_run.pages_removed} removed, {discovery_run.pages_unchanged} unchanged")
            
            # Update discovery run with final results
            discovery_run.completed_at = datetime.now()
            discovery_run.status = DiscoveryStatus.CANCELLED if was_cancelled else DiscoveryStatus.COMPLETED
            discovery_run.pages_discovered = len(discovered_pages)  # Only successful pages
            discovery_run.pages_failed = len(failed_pages)  # Failed pages tracked separately
            discovery_run.failed_pages_details = [
                {'url': p.url, 'error_reason': p.error_reason or 'Unknown error'}
                for p in failed_pages
            ]
            discovery_run.robots_blocked_count = robots_blocked
            discovery_run.documents_found = self.db.document_references.count_documents({'website_id': website_id})
            discovery_run.duration_seconds = int((discovery_run.completed_at - discovery_run.started_at).total_seconds())
            self.db.update_discovery_run(discovery_run)
            
            # Update website last scraped timestamp
            website.last_scraped = datetime.now()
            self.db.update_website(website)
            
        except Exception as e:
            logger.error(f"Error during discovery: {e}", exc_info=True)
            # Pages already saved individually during discovery

            # Update discovery run with error
            discovery_run.completed_at = datetime.now()
            discovery_run.status = DiscoveryStatus.FAILED
            discovery_run.error_message = str(e)[:500]
            discovery_run.pages_discovered = len(discovered_pages)  # Only successful pages
            discovery_run.pages_failed = len(failed_pages)  # Failed pages tracked separately
            discovery_run.failed_pages_details = [
                {'url': p.url, 'error_reason': p.error_reason or 'Unknown error'}
                for p in failed_pages
            ]
            discovery_run.robots_blocked_count = robots_blocked
            discovery_run.duration_seconds = int((discovery_run.completed_at - discovery_run.started_at).total_seconds())
            self.db.update_discovery_run(discovery_run)
            
            # Update website last scraped timestamp
            website.last_scraped = datetime.now()
            self.db.update_website(website)
            
            if progress_callback:
                await progress_callback({
                    'status': 'failed',
                    'error': str(e)
                })
            raise
        finally:
            await self.browser_manager.stop()
            logger.info("Browser stopped after discovery session")
        
        logger.info(f"Discovery complete. Found {len(discovered_pages)} pages")
        return discovered_pages
    
    async def _discover_page(
        self,
        url: str,
        website: Website,
        depth: int,
        base_domain: str,
        base_path: str = "",
        browser_page: PlaywrightPage | None = None
    ) -> Page | None:
        """
        Discover a single page and extract links
        
        Args:
            url: URL to discover
            website: Website object
            depth: Current crawl depth
            base_domain: Base domain for filtering
            browser_page: Optional existing page to reuse
            
        Returns:
            Page object or None if failed
        """
        assert website.id is not None
        _wid: str = website.id
        # Check if browser is still running, restart if needed
        if not await self.browser_manager.is_running():
            logger.warning(f"Browser not running before discovering {url}, attempting restart...")
            try:
                await self.browser_manager.ensure_running()
                logger.info("Browser restarted successfully")
            except Exception as e:
                logger.error(f"Failed to restart browser: {e}")
                # Return a failed page record
                return Page(
                    website_id=_wid,
                    url=url,
                    title="Failed: Browser crashed",
                    discovered_from=website.url if depth == 0 else None,
                    depth=depth,
                    status=PageStatus.DISCOVERY_FAILED,
                    error_reason="Browser not running and failed to restart"
                )
            
        page = None
        try:
            # Check browser is available
            if not await self.browser_manager.is_running():
                logger.error("Browser is not running")
                return Page(
                    website_id=_wid,
                    url=url,
                    title="Failed: Browser error",
                    discovered_from=website.url if depth == 0 else None,
                    depth=depth,
                    status=PageStatus.DISCOVERY_FAILED,
                    error_reason="Browser instance unavailable"
                )

            # Create a new page with error handling (handles viewport, user agent, stealth)
            try:
                page = await self.browser_manager.create_page()
            except Exception as e:
                logger.error(f"Failed to create new page: {e}")
                # Browser might be in bad state, try to restart
                try:
                    await self.browser_manager.ensure_running()
                    page = await self.browser_manager.create_page()
                except Exception as e2:
                    logger.error(f"Failed to create page even after restart: {e2}")
                    return Page(
                        website_id=website.id,
                        url=url,
                        title="Failed: Cannot create page",
                        discovered_from=website.url if depth == 0 else None,
                        depth=depth,
                        status=PageStatus.DISCOVERY_FAILED,
                        error_reason=f"Failed to create browser page: {str(e2)[:200]}"
                    )

            # Configure timeouts and wait conditions based on stealth mode
            stealth_mode = self.browser_manager.config.get('stealth_mode', False)

            if stealth_mode:
                # Slower, more thorough for Cloudflare-protected sites
                wait_until = 'networkidle'  # Wait for network to settle (Playwright compatible)
                nav_timeout = 40000  # 40 seconds
                post_nav_wait = 3  # Wait 3 seconds after navigation
            elif website.scraping_config.spa_click_discovery:
                # SPA mode: networkidle covers initial XHR/fetch on hydration; the
                # explicit readiness helper handles per-framework variation below.
                wait_until = 'networkidle'
                nav_timeout = 40000  # 40 seconds
                post_nav_wait = 0  # _wait_for_spa_ready handles all post-nav waiting
            else:
                # Faster for normal sites
                wait_until = 'domcontentloaded'  # Just wait for DOM
                nav_timeout = 20000  # 20 seconds
                post_nav_wait = 0  # No extra wait

            # Navigate to URL with proper error handling
            response = None
            try:
                logger.debug(f"Navigating to {url}")
                response = await self.browser_manager.goto(
                    page=page,
                    url=url,
                    wait_until=wait_until,
                    timeout=nav_timeout
                )

                if response:
                    logger.debug(f"Response received for {url}: status {response.status}")
                else:
                    logger.debug(f"No response object returned for {url}")

                # Wait additional time for JavaScript challenges if in stealth mode
                if post_nav_wait > 0:
                    await asyncio.sleep(post_nav_wait)

                # Give SPA frameworks time to render JS-injected anchors before
                # any DOM inspection (title check, Cloudflare check, redirect
                # check, and link extraction all run after this point).  A fixed
                # sleep proved unreliable — slow SPAs were still blank when the
                # DOM was read. ``_wait_for_spa_ready`` applies three layered,
                # bounded readiness checks instead.
                if website.scraping_config.spa_click_discovery:
                    await self._wait_for_spa_ready(page, website, url)

                # Check if we're stuck on a Cloudflare challenge page
                try:
                    page_title = await page.title()
                    page_content = await page.content()
                    logger.debug(f"Page title: {page_title}")

                    # Detect Cloudflare challenge indicators
                    if 'cloudflare' in page_title.lower() or 'checking your browser' in page_content.lower():
                        logger.warning(f"Cloudflare challenge detected on: {url}")
                        # Wait longer for challenge to complete
                        await asyncio.sleep(10)
                        page_title = await page.title()
                        page_content = await page.content()
                        if 'cloudflare' in page_title.lower() or 'checking your browser' in page_content.lower():
                            logger.error(f"Cloudflare challenge failed to complete for: {url}")
                            return Page(
                                website_id=website.id,
                                url=url,
                                title="Failed: Cloudflare challenge",
                                discovered_from=website.url if depth == 0 else None,
                                depth=depth,
                                status=PageStatus.DISCOVERY_FAILED,
                                error_reason="Stuck on Cloudflare challenge page"
                            )

                    logger.info(f"Successfully loaded page: {url} (title: {page_title[:50]})")
                except Exception as e:
                    logger.warning(f"Could not check page content for {url}: {e}")

                if not response:
                    logger.warning(f"Failed to load page: {url}")
                    # Return a failed page record instead of None
                    return Page(
                        website_id=website.id,
                        url=url,
                        title="Failed: No response",
                        discovered_from=website.url if depth == 0 else None,
                        depth=depth,
                        status=PageStatus.DISCOVERY_FAILED,
                        error_reason="No response from server"
                    )
                
                # Check if we were redirected to an external domain
                final_url = page.url
                if final_url and final_url != url:
                    final_parsed = urlparse(final_url)
                    final_domain = final_parsed.netloc
                    
                    # Log all redirects for debugging
                    logger.info(f"Page redirected: {url} -> {final_url}")
                    
                    if final_domain and final_domain != base_domain:
                        # Check if it's a subdomain of our base domain
                        if not final_domain.endswith(f'.{base_domain}'):
                            logger.warning(f"SKIPPING: Redirected to external domain {final_domain} from {url}")
                            # Create a failed page record
                            failed_page = Page(
                                website_id=website.id,
                                url=url,
                                title=f"Failed: External redirect",
                                discovered_from=website.url if depth == 0 else None,
                                depth=depth,
                                status=PageStatus.DISCOVERY_FAILED,
                                error_reason=f"Redirected to external domain: {final_domain}"
                            )
                            return failed_page
                        else:
                            logger.debug(f"Redirect to subdomain accepted: {final_domain}")
                    
                    # Also check if redirected outside base path
                    if base_path and final_domain == base_domain:
                        final_path = final_parsed.path
                        if not final_path.startswith(base_path + '/') and final_path != base_path:
                            logger.warning(f"SKIPPING: Redirected outside base path from {url} to {final_url}")
                            failed_page = Page(
                                website_id=website.id,
                                url=url,
                                title=f"Failed: Redirect outside scope",
                                discovered_from=website.url if depth == 0 else None,
                                depth=depth,
                                status=PageStatus.DISCOVERY_FAILED,
                                error_reason=f"Redirected outside base path: {final_path}"
                            )
                            return failed_page
                            
            except Exception as e:
                logger.warning(f"Navigation failed for {url}: {e}")
                # IMPORTANT: Close the page to prevent browser hanging
                if page:
                    try:
                        await page.close()
                    except:
                        pass
                    page = None  # Clear reference
                
                # Check if browser is still alive after navigation failure
                if not await self.browser_manager.is_running():
                    logger.warning("Browser connection lost after navigation failure")
                    # Try to restart browser for next page
                    try:
                        await self.browser_manager.ensure_running()
                        logger.info("Browser restarted after navigation failure")
                    except:
                        pass  # Will be handled on next page attempt
                
                # Create a failed page record
                failed_page = Page(
                    website_id=_wid,
                    url=url,
                    title="Failed: Navigation error",
                    discovered_from=website.url if depth == 0 else None,
                    depth=depth,
                    status=PageStatus.DISCOVERY_FAILED,
                    error_reason=f"Navigation failed: {str(e)[:200]}"
                )
                return failed_page
            
            # Get page title
            title = await self.browser_manager.get_page_title(page)
            
            # Extract links if not at max depth
            if depth < website.scraping_config.max_depth:
                try:
                    links = await self._extract_links(page, url, website, base_domain, base_path)
                    self.queued_urls.update(links)
                except Exception as e:
                    logger.warning(f"Failed to extract links from {url}: {e}")
                    # Continue without links rather than failing the whole page

            # Take screenshot during discovery for page preview
            screenshot_path = None
            try:
                # Check if page/browser is still connected before screenshot
                if page and await self.browser_manager.is_running():
                    # Small delay to let page stabilize before screenshot
                    await asyncio.sleep(0.5)
                    screenshot_path = await self._take_discovery_screenshot(page, _wid, url)
                    if screenshot_path:
                        logger.debug(f"Discovery screenshot saved: {screenshot_path}")
                else:
                    logger.warning(f"Page or browser not available for screenshot: {url}")
            except Exception as e:
                logger.warning(f"Failed to take discovery screenshot for {url}: {e}")
                # Continue without screenshot rather than failing the whole page

            # Close the page now that we're done with it
            try:
                if page:
                    # Small delay before closing to ensure pending operations complete
                    await asyncio.sleep(0.2)
                    await page.close()
                    page = None  # Mark as closed
            except Exception as e:
                logger.warning(f"Error closing page after successful discovery: {e}")
                page = None  # Mark as closed anyway to prevent double-close

            # Create page object
            page_obj = Page(
                website_id=_wid,
                url=url,
                title=title or "Untitled",
                discovered_from=website.url if depth == 0 else None,
                depth=depth,
                status=PageStatus.DISCOVERED,
                screenshot_path=screenshot_path  # Add screenshot from discovery
            )
            
            logger.debug(f"Discovered page: {url} - {title}")
            return page_obj
                
        except PlaywrightTimeout:
            logger.warning(f"Timeout loading page: {url}")
            # Check browser health after timeout
            if not await self.browser_manager.is_running():
                logger.warning("Browser died after timeout, will restart on next page")
            # Create a failed page record for timeout
            return Page(
                website_id=_wid,
                url=url,
                title="Failed: Timeout",
                discovered_from=website.url if depth == 0 else None,
                depth=depth,
                status=PageStatus.DISCOVERY_FAILED,
                error_reason="Page load timeout (25 seconds)"
            )
        except Exception as e:
            logger.error(f"Error discovering page {url}: {e}", exc_info=True)
            # Create a failed page record for other errors
            return Page(
                website_id=_wid,
                url=url,
                title="Failed: Error",
                discovered_from=website.url if depth == 0 else None,
                depth=depth,
                status=PageStatus.DISCOVERY_FAILED,
                error_reason=f"Discovery error: {str(e)[:200]}"
            )
        finally:
            # Close the page if it's still open (only happens on error paths)
            if page:
                try:
                    # Check if browser is still connected before trying to close
                    if await self.browser_manager.is_running():
                        await asyncio.sleep(0.1)  # Brief delay before close
                        await page.close()
                except Exception as e:
                    logger.warning(f"Error closing page: {e}")
    
    async def _extract_links(
        self,
        page: PlaywrightPage,
        current_url: str,
        website: Website,
        base_domain: str,
        base_path: str = ""
    ) -> set[str]:
        """
        Extract and filter links from a page
        
        Args:
            page: Pyppeteer page object
            current_url: Current page URL
            website: Website configuration
            base_domain: Base domain for filtering
            
        Returns:
            Set of valid URLs to crawl
        """
        assert website.id is not None
        _extract_wid: str = website.id
        try:
            # Extract all links with their text using JavaScript
            links_with_text = await page.evaluate('''
                () => {
                    const anchors = document.querySelectorAll('a[href]');
                    return Array.from(anchors).map(a => ({
                        href: a.href,
                        text: a.textContent.trim()
                    }));
                }
            ''')
            
            # Filter and normalize links
            valid_links: set[str] = set()
            document_refs: list[dict[str, Any]] = []  # Collect document references
            
            for link_data in links_with_text:
                link = link_data['href']
                link_text = link_data.get('text', '')
                # Skip empty or invalid links
                if not link or link.startswith('#') or link.startswith('javascript:'):
                    continue
                
                # Skip mailto, tel, and other non-HTTP protocols
                if link.startswith(('mailto:', 'tel:', 'ftp:', 'file:')):
                    continue
                
                # Normalize URL
                normalized = self._normalize_url(link, current_url)
                if not normalized:
                    continue
                
                # Parse URL
                parsed = urlparse(normalized)
                
                # Check if we should follow this link
                if not website.scraping_config.follow_external:
                    # Only follow links on same domain
                    if parsed.netloc != base_domain:
                        # Check subdomains if configured
                        if not (website.scraping_config.include_subdomains and
                               parsed.netloc.endswith(f'.{base_domain}')):
                            logger.warning(f"Skipping link (external domain): {normalized}")
                            continue

                    # If the base URL has a path component, ensure links stay within that path
                    if base_path:
                        # The link must start with the base path to be considered internal
                        if not parsed.path.startswith(base_path + '/') and parsed.path != base_path:
                            logger.warning(f"Skipping URL outside base path: {normalized} (path: {parsed.path}, base_path: {base_path})")
                            continue
                
                # Apply path filters
                path = parsed.path
                
                # Check for document files
                document_extensions = {
                    '.pdf': 'application/pdf',
                    '.doc': 'application/msword',
                    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    '.xls': 'application/vnd.ms-excel',
                    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.document',
                    '.ppt': 'application/vnd.ms-powerpoint',
                    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.document',
                    '.rtf': 'application/rtf',
                    '.txt': 'text/plain',
                    '.csv': 'text/csv',
                    '.zip': 'application/zip',
                    '.rar': 'application/zip',
                    '.7z': 'application/zip'
                }
                
                # Check if this is a document
                file_ext = None
                for ext in document_extensions:
                    if path.lower().endswith(ext):
                        file_ext = ext
                        break
                
                if file_ext:
                    # This is a document, capture it
                    is_internal = parsed.netloc == base_domain or (
                        website.scraping_config.include_subdomains and 
                        parsed.netloc.endswith(f'.{base_domain}')
                    )
                    
                    document_refs.append({
                        'url': normalized,
                        'mime_type': document_extensions[file_ext],
                        'is_internal': is_internal,
                        'link_text': link_text,
                        'file_extension': file_ext
                    })
                    continue  # Don't add to crawl queue
                
                # Skip common non-HTML resources (images, videos, etc.)
                if path.endswith(('.jpg', '.jpeg', '.png', '.gif', '.exe', '.dmg', '.mp4', '.mp3')):
                    continue
                
                # Check excluded paths
                if website.scraping_config.excluded_paths:
                    if any(path.startswith(exc) for exc in website.scraping_config.excluded_paths):
                        continue
                
                # Check allowed paths
                if website.scraping_config.allowed_paths:
                    if not any(path.startswith(allow) for allow in website.scraping_config.allowed_paths):
                        continue
                
                valid_links.add(normalized)
            
            logger.debug(f"Extracted {len(valid_links)} valid links and {len(document_refs)} documents from {current_url}")
            
            # Save document references to database
            if document_refs:
                await self._save_document_references(document_refs, _extract_wid, current_url)

            if website.scraping_config.spa_click_discovery:
                try:
                    click_links = await self._extract_links_via_clicking(
                        page=page,
                        current_url=current_url,
                        website=website,
                        base_domain=base_domain,
                        base_path=base_path,
                    )
                    valid_links.update(click_links)
                except Exception as e:
                    logger.warning(
                        f"Click-based discovery failed for {current_url}: {e}"
                    )

            return valid_links

        except Exception as e:
            logger.error(f"Error extracting links from {current_url}: {e}")
            return set()

    async def _wait_for_spa_ready(
        self,
        page: ClickablePage,
        website: Website,
        url: str,
    ) -> None:
        """Wait for an SPA page to be ready before reading the DOM.

        Three layered checks, each best-effort with a bounded timeout:

        1. ``networkidle`` (Playwright wait_for_load_state): no network activity
           for 500ms. Already implied by navigation wait_until='networkidle' but
           calling it again is cheap and handles cases where the initial wait
           returned early.
        2. Anchor-count stabilization: poll until ``a`` count is unchanged for
           1s. Strong signal the framework has finished hydrating nav.
        3. Optional ``spa_ready_selector`` from website config: if set, wait
           for that selector to become visible.

        Each phase logs success/timeout but never raises.
        """
        # Phase 1: network idle
        try:
            await page.wait_for_load_state('networkidle', timeout=15000)
            logger.info(f"SPA readiness [networkidle]: ok for {url}")
        except PlaywrightTimeout:
            logger.warning(
                f"SPA readiness [networkidle]: timeout after 15s for {url}, continuing"
            )
        except Exception as e:
            logger.warning(
                f"SPA readiness [networkidle]: error for {url}: {e}, continuing"
            )

        # Phase 2: anchor-count stabilization (poll in JS for efficiency)
        stabilization_js = """
        () => new Promise((resolve) => {
          const sampleEveryMs = 250;
          const requiredStableSamples = 4;  // 4 * 250ms = 1s
          const maxWaitMs = 10000;
          let last = document.querySelectorAll('a').length;
          let stable = 0;
          const interval = setInterval(() => {
            const now = document.querySelectorAll('a').length;
            if (now === last) {
              stable += 1;
              if (stable >= requiredStableSamples) {
                clearInterval(interval);
                clearTimeout(safety);
                resolve({stable: true, count: now});
              }
            } else {
              stable = 0;
              last = now;
            }
          }, sampleEveryMs);
          const safety = setTimeout(() => {
            clearInterval(interval);
            resolve({stable: false, count: document.querySelectorAll('a').length});
          }, maxWaitMs);
        });
        """
        try:
            raw_result: object = await page.evaluate(stabilization_js)
            # raw_result is {stable: bool, count: int} but typed as object
            if isinstance(raw_result, dict):
                raw_dict: dict[object, object] = cast(dict[object, object], raw_result)
                stable_obj: object = raw_dict.get("stable", False)
                stable = bool(stable_obj)
                count_obj: object = raw_dict.get("count", 0)
                count: int = count_obj if isinstance(count_obj, int) else 0
                if stable:
                    logger.info(
                        f"SPA readiness [anchor-count]: stable at {count} anchors for {url}"
                    )
                else:
                    logger.warning(
                        f"SPA readiness [anchor-count]: did not stabilize within 10s, saw {count} anchors at {url}"
                    )
            else:
                logger.warning(
                    f"SPA readiness [anchor-count]: unexpected result type for {url}"
                )
        except Exception as e:
            logger.warning(
                f"SPA readiness [anchor-count]: error for {url}: {e}, continuing"
            )

        # Phase 3: user-configured selector (if any)
        selector = website.scraping_config.spa_ready_selector.strip()
        if selector:
            try:
                await page.wait_for_selector(selector, state='visible', timeout=15000)
                logger.info(
                    f"SPA readiness [selector '{selector}']: visible for {url}"
                )
            except PlaywrightTimeout:
                logger.warning(
                    f"SPA readiness [selector '{selector}']: timeout after 15s for {url}, continuing"
                )
            except Exception as e:
                logger.warning(
                    f"SPA readiness [selector '{selector}']: error for {url}: {e}, continuing"
                )

    async def _extract_links_via_clicking(
        self,
        page: ClickablePage,
        current_url: str,
        website: Website,
        base_domain: str,
        base_path: str = "",
    ) -> set[str]:
        """
        Discover SPA links by clicking anchors with no usable href.

        For sites where navigation happens via JavaScript (href="#",
        href="javascript:..."), the standard href extraction misses real
        routes. This helper clicks each such anchor, reads the resulting
        page.url, and runs it through the same filter pipeline as the
        href path.

        Returns the set of newly discovered, in-scope URLs.
        """
        discovered: set[str] = set()
        try:
            raw_candidates: object = await page.evaluate(_CLICK_CANDIDATES_JS)
        except Exception as e:
            logger.warning(f"Failed to collect click candidates on {current_url}: {e}")
            return discovered

        if not isinstance(raw_candidates, list):
            return discovered
        # ``isinstance(x, list)`` narrows to ``list[Unknown]`` under pyright
        # strict, so re-bind via a direct cast to ``list[object]`` for
        # downstream typed handling.
        raw_list: list[object] = cast(list[object], raw_candidates)

        # Coerce JS-returned values to typed Python objects.
        candidates: list[dict[str, str]] = []
        for c in raw_list:
            if not isinstance(c, dict):
                continue
            c_dict: dict[object, object] = cast(dict[object, object], c)
            if bool(c_dict.get("hasUsableHref")):
                continue
            text_val: object = c_dict.get("text", "")
            aria_val: object = c_dict.get("ariaLabel", "")
            selector_val: object = c_dict.get("selector", "")
            text = str(text_val).strip()
            aria = str(aria_val).strip()
            selector = str(selector_val)
            if not selector or (not text and not aria):
                continue
            # Destructive-anchor filter
            if DESTRUCTIVE_ANCHOR_PATTERN.search(f"{text} {aria}"):
                label = text or aria
                logger.info(
                    f"Skipped destructive anchor '{label}' at {current_url} (matched filter)"
                )
                continue
            candidates.append({"text": text, "aria": aria, "selector": selector})

        # Effort cap
        if len(candidates) > MAX_CLICK_CANDIDATES_PER_PAGE:
            candidates = candidates[:MAX_CLICK_CANDIDATES_PER_PAGE]

        logger.info(
            f"Starting click-based discovery for {current_url}: {len(candidates)} candidates after filtering"
        )

        stealth_mode = bool(self.browser_manager.config.get("stealth_mode", False))
        wait_until = "networkidle" if stealth_mode else "domcontentloaded"
        nav_timeout = 40000 if stealth_mode else 20000

        # The Protocol ``ClickablePage`` is a structural subset suitable for
        # the inline calls (evaluate/click/query_selector). ``browser_manager.goto``
        # expects a full Playwright Page; in practice the helper is invoked with
        # one in production, and a MagicMock in tests. Cast for the typing layer.
        playwright_page = cast(PlaywrightPage, page)

        for c in candidates:
            selector = c["selector"]
            text = c["text"] or c["aria"]
            try:
                # 1) Restore parent state before each click.
                await self.browser_manager.goto(
                    page=playwright_page, url=current_url,
                    wait_until=wait_until, timeout=nav_timeout,
                )

                # SPA re-renders after re-navigation; wait for readiness signals
                # (the same helper used after initial navigation).
                await self._wait_for_spa_ready(playwright_page, website, current_url)

                # 2) Locate the anchor; skip if the DOM has changed.
                element = await page.query_selector(selector)
                if element is None:
                    logger.warning(
                        f"Could not locate anchor at {selector} after re-navigating to {current_url}; skipping"
                    )
                    continue

                # 3) Strip target so the click navigates in-place.
                await page.evaluate(
                    """(sel) => {
                        const el = document.evaluate(sel, document, null,
                            XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                        if (el) el.removeAttribute('target');
                    }""",
                    selector,
                )

                # 4) Click and settle.
                await page.click(selector, timeout=CLICK_TIMEOUT_MS)
                await asyncio.sleep(POST_CLICK_SETTLE_MS / 1000)

                # ``ClickablePage.url`` is typed ``str`` by the Protocol; no
                # narrowing needed here.
                final_url: str = page.url

                if final_url == current_url:
                    logger.warning(
                        f"Click on '{text}' at {current_url} produced no URL change — SPA may not be URL-routed"
                    )
                    continue

                # 5) Run through the same filters as the href path.
                filtered = self._filter_post_click_url(
                    final_url, current_url, website, base_domain, base_path,
                )
                if filtered is not None:
                    discovered.add(filtered)
            except Exception as e:
                logger.warning(
                    f"Error clicking anchor '{text}' on {current_url}: {e}"
                )
                continue

        return discovered

    def _filter_post_click_url(
        self,
        url: str,
        current_url: str,
        website: Website,
        base_domain: str,
        base_path: str,
    ) -> str | None:
        """Apply the standard scope/excluded/allowed filters to a click-discovered URL.

        Returns the normalized URL if it should be queued, else None.
        """
        normalized = self._normalize_url(url, current_url)
        if not normalized:
            return None
        parsed = urlparse(normalized)

        if not website.scraping_config.follow_external:
            if parsed.netloc != base_domain:
                if not (website.scraping_config.include_subdomains
                        and parsed.netloc.endswith(f".{base_domain}")):
                    return None
            if base_path:
                if (not parsed.path.startswith(base_path + "/")
                        and parsed.path != base_path):
                    return None

        path = parsed.path
        # Skip non-HTML resources the existing extraction also skips.
        path_lower = path.lower()
        if path_lower.endswith((
            ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx",
            ".jpg", ".jpeg", ".png", ".gif", ".exe", ".dmg", ".mp4", ".mp3",
        )):
            return None
        if website.scraping_config.excluded_paths:
            if any(path.startswith(p)
                   for p in website.scraping_config.excluded_paths):
                return None
        if website.scraping_config.allowed_paths:
            if not any(path.startswith(p)
                       for p in website.scraping_config.allowed_paths):
                return None
        return normalized

    async def _save_document_references(self, document_refs: list[dict[str, Any]], website_id: str, referring_page_url: str) -> None:
        """
        Save document references to database with language detection
        
        Args:
            document_refs: List of document reference data
            website_id: Website ID
            referring_page_url: URL of the page containing the links
        """
        from auto_a11y.models import DocumentReference
        
        for doc_data in document_refs:
            try:
                # Detect language from link text and URL
                language = await self._detect_document_language(
                    doc_data['url'],
                    doc_data.get('link_text'),
                    doc_data.get('file_extension')
                )
                
                doc_ref = DocumentReference(
                    website_id=website_id,
                    document_url=doc_data['url'],
                    referring_page_url=referring_page_url,
                    mime_type=doc_data['mime_type'],
                    is_internal=doc_data['is_internal'],
                    link_text=doc_data.get('link_text'),
                    file_extension=doc_data.get('file_extension'),
                    language=language.get('language') if language else None,
                    language_confidence=language.get('confidence') if language else None,
                    via_redirect=False  # Direct link, not via redirect
                )
                
                self.db.add_document_reference(doc_ref)
                lang_info = f" ({language['language']})" if language else ""
                logger.debug(f"Saved document reference: {doc_data['url']} ({'internal' if doc_data['is_internal'] else 'external'}){lang_info}")
            except Exception as e:
                logger.error(f"Error saving document reference {doc_data['url']}: {e}")
    
    async def _detect_document_language(self, doc_url: str, link_text: str | None = None, file_extension: str | None = None) -> dict[str, Any] | None:
        """
        Detect the language of a document using multiple methods
        
        Args:
            doc_url: URL of the document
            link_text: Text of the link to the document
            file_extension: File extension of the document
            
        Returns:
            Dictionary with 'language' code and 'confidence' score, or None
        """
        import aiohttp
        
        try:
            # Try to fetch document headers and content
            async with aiohttp.ClientSession() as session:
                async with session.head(doc_url, timeout=aiohttp.ClientTimeout(total=10), allow_redirects=True) as response:
                    # 1. Check Content-Language header
                    content_language = response.headers.get('Content-Language')
                    if content_language:
                        # Parse language code (e.g., "en-US" -> "en", "fr-CA" -> "fr")
                        lang_code = content_language.split('-')[0].lower()
                        logger.debug(f"Detected language from Content-Language header: {lang_code} for {doc_url}")
                        return {'language': lang_code, 'confidence': 0.95}
                    
                    # For small documents, download and analyze content
                    content_length = response.headers.get('Content-Length')
                    if content_length and int(content_length) < 5 * 1024 * 1024:  # Less than 5MB
                        # Download the document
                        async with session.get(doc_url, timeout=aiohttp.ClientTimeout(total=15)) as content_response:
                            content = await content_response.read()
                            
                            # 2. Extract metadata based on file type
                            if file_extension in ['.pdf', 'pdf']:
                                lang = await self._detect_pdf_language(content)
                                if lang:
                                    return lang
                            elif file_extension in ['.docx', 'docx']:
                                lang = await self._detect_docx_language(content)
                                if lang:
                                    return lang
                            
                            # 3. Analyze text content for language patterns
                            lang = await self._detect_language_from_content(content, file_extension)
                            if lang:
                                return lang
        
        except Exception as e:
            logger.debug(f"Error fetching document for language detection: {e}")
        
        # 4. Fallback: Analyze URL and link text patterns
        return self._detect_language_from_patterns(doc_url, link_text)
    
    async def _detect_pdf_language(self, content: bytes) -> dict[str, Any] | None:
        """Detect language from PDF metadata via the shared helper.

        Currently only inspects the catalog /Lang field. Word-frequency
        fallback for PDFs without declared language ports during Phase 3
        of the PDF audit engine port.
        """
        from io import BytesIO

        from auto_a11y.pdf.language import detect_pdf_language

        try:
            result = detect_pdf_language(BytesIO(content))
        except Exception as exc:
            logger.warning(f"PDF language detection failed: {exc}")
            return None

        if result.declared_lang is None and result.detected_lang is None:
            return None

        raw_lang = result.declared_lang or result.detected_lang
        # Match the existing "en-US" -> "en" normalisation pattern from the
        # old PyPDF2 path; downstream consumers expect a 2-letter code.
        lang_code = str(raw_lang).split('-')[0].lower() if raw_lang else None

        return {
            'language': lang_code,
            'confidence': result.confidence if result.confidence is not None else (0.9 if result.declared_lang else None),
            'method': result.method,
        }
    
    async def _detect_docx_language(self, content: bytes) -> dict[str, Any] | None:
        """
        Detect language from Word document metadata and content
        
        Args:
            content: DOCX file content as bytes
            
        Returns:
            Dictionary with language info or None
        """
        try:
            import zipfile
            import xml.etree.ElementTree as ET
            
            # DOCX files are ZIP archives
            with zipfile.ZipFile(BytesIO(content)) as docx:
                # Check core properties for language
                if 'docProps/core.xml' in docx.namelist():
                    core_xml = docx.read('docProps/core.xml')
                    root = ET.fromstring(core_xml)
                    
                    # Look for dc:language element
                    for elem in root.iter():
                        if 'language' in elem.tag.lower():
                            lang_code = elem.text
                            if lang_code:
                                lang_code = lang_code.split('-')[0].lower()
                                logger.debug(f"Detected language from DOCX metadata: {lang_code}")
                                return {'language': lang_code, 'confidence': 0.9}
                
                # Extract text for analysis
                if 'word/document.xml' in docx.namelist():
                    doc_xml = docx.read('word/document.xml')
                    root = ET.fromstring(doc_xml)
                    
                    # Extract text from document
                    text_sample = ""
                    for elem in root.iter():
                        if elem.text:
                            text_sample += elem.text + " "
                            if len(text_sample) > 1000:
                                break
                    
                    if text_sample:
                        return self._analyze_text_language(text_sample)
                        
        except Exception as e:
            logger.debug(f"Error detecting DOCX language: {e}")
        
        return None
    
    async def _detect_language_from_content(self, content: bytes, file_extension: str | None = None) -> dict[str, Any] | None:
        """
        Detect language from document content
        
        Args:
            content: Document content as bytes
            file_extension: File extension for text extraction
            
        Returns:
            Dictionary with language info or None
        """
        try:
            # For text-based files, decode and analyze
            if file_extension in ['.txt', '.csv', '.rtf', 'txt', 'csv', 'rtf']:
                # Try different encodings
                text = None
                for encoding in ['utf-8', 'latin-1', 'cp1252']:
                    try:
                        text = content.decode(encoding)
                        break
                    except:
                        continue
                
                if text:
                    return self._analyze_text_language(text[:5000])  # Analyze first 5000 chars
                    
        except Exception as e:
            logger.debug(f"Error detecting language from content: {e}")
        
        return None
    
    def _analyze_text_language(self, text: str) -> dict[str, Any] | None:
        """
        Analyze text to detect language using pattern matching
        
        Args:
            text: Text to analyze
            
        Returns:
            Dictionary with language info or None
        """
        if not text or len(text) < 50:
            return None
        
        # Clean text
        text = text.lower().strip()
        
        # Language-specific patterns and common words
        language_patterns: dict[str, dict[str, Any]] = {
            'en': {
                'words': ['the', 'and', 'of', 'to', 'in', 'is', 'for', 'with', 'that', 'this', 
                         'are', 'was', 'will', 'have', 'been', 'from', 'can', 'which', 'their', 'would'],
                'patterns': [r'\b(the|and|of|to|in)\b', r'\b(is|are|was|were)\b', r'\b(have|has|had)\b'],
                'weight': 1.0
            },
            'fr': {
                'words': ['le', 'la', 'les', 'de', 'et', 'est', 'pour', 'dans', 'avec', 'sur',
                         'une', 'des', 'que', 'qui', 'par', 'plus', 'sont', 'être', 'avoir', 'faire'],
                'patterns': [r'\b(le|la|les|un|une|des)\b', r'\b(de|du|des)\b', r'\b(est|sont|être)\b',
                           r'[àâäéèêëïîôùûü]'],  # French accented characters
                'weight': 1.2  # Slight boost for French since it's a priority
            },
            'es': {
                'words': ['el', 'la', 'de', 'y', 'en', 'que', 'es', 'por', 'con', 'para',
                         'los', 'las', 'una', 'se', 'del', 'al', 'más', 'pero', 'su', 'lo'],
                'patterns': [r'\b(el|la|los|las)\b', r'\b(de|del)\b', r'\b(es|son|está|están)\b',
                           r'[áéíóúñü]'],  # Spanish accented characters
                'weight': 1.0
            },
            'de': {
                'words': ['der', 'die', 'das', 'und', 'in', 'ist', 'mit', 'auf', 'für', 'von',
                         'den', 'des', 'ein', 'eine', 'sich', 'zu', 'werden', 'haben', 'sein', 'ihr'],
                'patterns': [r'\b(der|die|das|den|dem)\b', r'\b(ein|eine|einen)\b', r'\b(ist|sind|war|waren)\b',
                           r'[äöüß]'],  # German special characters
                'weight': 1.0
            }
        }
        
        scores: dict[str, float] = {}
        
        for lang, config in language_patterns.items():
            score = 0
            word_count = 0
            
            # Count occurrences of common words
            for word in config['words']:
                count = text.count(f' {word} ') + text.count(f' {word}.') + text.count(f' {word},')
                if count > 0:
                    word_count += count
                    score += count * config['weight']
            
            # Check patterns
            for pattern in config['patterns']:
                matches = len(re.findall(pattern, text))
                if matches > 0:
                    score += matches * 0.5 * config['weight']
            
            # Normalize score by text length
            scores[lang] = score / (len(text) / 100)
        
        # Get the language with highest score
        if scores:
            best_lang = max(scores, key=lambda k: scores[k])
            best_score = scores[best_lang]
            
            # Calculate confidence based on score differential
            sorted_scores = sorted(scores.values(), reverse=True)
            if len(sorted_scores) > 1:
                confidence = min(0.85, 0.5 + (sorted_scores[0] - sorted_scores[1]) / 10)
            else:
                confidence = 0.7
            
            # Only return if score is significant
            if best_score > 0.5:
                logger.debug(f"Detected language from text analysis: {best_lang} (confidence: {confidence:.2f})")
                return {'language': best_lang, 'confidence': confidence}
        
        return None
    
    def _detect_language_from_patterns(self, url: str, link_text: str | None = None) -> dict[str, Any] | None:
        """
        Detect language from URL patterns and link text as fallback
        
        Args:
            url: Document URL
            link_text: Link text
            
        Returns:
            Dictionary with language info or None
        """
        # URL patterns that indicate language
        url_patterns = {
            'fr': ['/fr/', '_fr', '-fr', 'french', 'francais', 'français'],
            'en': ['/en/', '_en', '-en', 'english', 'anglais'],
            'es': ['/es/', '_es', '-es', 'spanish', 'espanol', 'español'],
            'de': ['/de/', '_de', '-de', 'german', 'deutsch', 'allemand']
        }
        
        url_lower = url.lower()
        
        for lang, patterns in url_patterns.items():
            for pattern in patterns:
                if pattern in url_lower:
                    logger.debug(f"Detected language from URL pattern: {lang}")
                    return {'language': lang, 'confidence': 0.6}
        
        # Check link text for language indicators
        if link_text:
            link_lower = link_text.lower()
            
            # French indicators
            if any(word in link_lower for word in ['français', 'francais', 'french', 'version française']):
                return {'language': 'fr', 'confidence': 0.7}
            
            # English indicators
            if any(word in link_lower for word in ['english', 'anglais', 'version anglaise']):
                return {'language': 'en', 'confidence': 0.7}
            
            # Analyze link text itself
            lang_result = self._analyze_text_language(link_text)
            if lang_result:
                lang_result['confidence'] *= 0.7  # Lower confidence for short text
                return lang_result
        
        return None
    
    def _normalize_url(self, url: str, base_url: str | None = None) -> str | None:
        """
        Normalize and validate URL
        
        Args:
            url: URL to normalize
            base_url: Base URL for relative links
            
        Returns:
            Normalized URL or None if invalid
        """
        try:
            # Handle relative URLs
            if base_url and not url.startswith(('http://', 'https://')):
                url = urljoin(base_url, url)
            
            # Parse URL
            parsed = urlparse(url)
            
            # Ensure HTTP/HTTPS
            if parsed.scheme not in ['http', 'https']:
                return None
            
            # Remove fragment
            parsed = parsed._replace(fragment='')
            
            # Normalize path - for root, remove the path entirely or use empty string
            # This makes both "example.com" and "example.com/" normalize to "example.com"
            path = parsed.path
            if path == '/':
                # Root path - remove it for consistent normalization
                parsed = parsed._replace(path='')
            elif path.endswith('/'):
                # Non-root path with trailing slash - remove the slash
                path = path[:-1]
                parsed = parsed._replace(path=path)
            
            # Reconstruct URL
            normalized = urlunparse(parsed)
            
            return normalized
            
        except Exception as e:
            logger.debug(f"Failed to normalize URL {url}: {e}")
            return None
    
    async def _can_fetch(self, url: str) -> bool:
        """
        Check if URL can be fetched according to robots.txt

        Args:
            url: URL to check

        Returns:
            True if URL can be fetched
        """
        try:
            parsed = urlparse(url)
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

            # Fetch and parse the live robots.txt per-origin, caching the result.
            if robots_url not in self.robots_cache:
                rp = RobotFileParser()
                rp.set_url(robots_url)
                try:
                    # robots fetch is blocking I/O -- run it off the event loop
                    # so it does not stall concurrent discovery work.
                    await asyncio.to_thread(rp.read)
                except Exception as fetch_error:
                    # Standard behaviour: if robots.txt cannot be fetched,
                    # default to allowing everything.
                    logger.debug(
                        f"Could not fetch robots.txt at {robots_url}: {fetch_error} -- defaulting to allow"
                    )
                    rp.parse(['User-agent: *', 'Disallow:'])
                self.robots_cache[robots_url] = rp

            # Check if URL is fetchable
            return self.robots_cache[robots_url].can_fetch('*', url)

        except Exception as e:
            logger.debug(f"Error checking robots.txt for {url}: {e}")
            # Default to allow on error
            return True

    async def _take_discovery_screenshot(self, page: PlaywrightPage, website_id: str, url: str) -> str | None:
        """
        Take screenshot during page discovery for preview thumbnail

        Args:
            page: Pyppeteer page object
            website_id: Website ID
            url: Page URL being discovered

        Returns:
            Screenshot file path or None if failed
        """
        try:
            from pathlib import Path

            # Create screenshots directory if it doesn't exist
            screenshot_dir = Path('screenshots')
            screenshot_dir.mkdir(exist_ok=True, parents=True)

            # Generate filename using URL hash to keep it reasonable length
            import hashlib
            url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"discovery_{website_id}_{url_hash}_{timestamp}.jpg"
            filepath = screenshot_dir / filename

            # Take screenshot with browser manager
            await self.browser_manager.take_screenshot(
                page,
                path=str(filepath),
                full_page=True
            )

            logger.debug(f"Discovery screenshot saved: {filepath}")
            # Return just the filename, not the full path with screenshots/
            return filename

        except Exception as e:
            logger.warning(f"Failed to take discovery screenshot for {url}: {e}")
            return None

    async def cleanup(self) -> None:
        """Clean up browser resources"""
        if self.browser_manager:
            await self.browser_manager.stop()


# ScrapingJob class has been moved to scraping_job.py for database-backed implementation