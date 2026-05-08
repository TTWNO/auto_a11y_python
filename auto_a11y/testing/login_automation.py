"""
Login automation for authenticated testing using Playwright
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast, TYPE_CHECKING
from datetime import datetime

from playwright._impl._api_structures import SetCookieParam
from playwright.async_api import Page

if TYPE_CHECKING:
    from auto_a11y.core.database import Database
    from auto_a11y.models import WebsiteUser, ProjectUser

logger = logging.getLogger(__name__)


class LoginAutomation:
    """Handles automated login for authenticated testing"""

    def __init__(self, database: Database) -> None:
        """
        Initialize login automation

        Args:
            database: Database connection
        """
        self.db = database

    async def perform_login(
        self,
        browser_page: Page,
        user: WebsiteUser | ProjectUser,
        timeout: int = 30000
    ) -> dict[str, Any]:
        """
        Perform automated login for a user

        Args:
            browser_page: Playwright page object
            user: WebsiteUser object with credentials and login config
            timeout: Maximum time to wait for login (milliseconds)

        Returns:
            Dictionary with success status, error message, and timing
        """
        start_time = datetime.now()

        try:
            login_config = user.login_config

            if login_config.authentication_method.value == 'form_login':
                result = await self._perform_form_login(browser_page, user, timeout)
            elif login_config.authentication_method.value == 'basic_auth':
                result = await self._perform_basic_auth(browser_page, user)
            elif login_config.authentication_method.value == 'manual_login':
                result = await self._perform_manual_login(browser_page, user)
            else:
                return {
                    'success': False,
                    'error': f'Authentication method {login_config.authentication_method.value} not yet implemented',
                    'duration_ms': 0
                }

            # Update user's login status in database
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            user.mark_login_attempt(result['success'], result.get('error'))
            self.db.update_project_user(cast(Any, user))

            result['duration_ms'] = duration_ms
            return result

        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            error_msg = f"Login automation error: {str(e)}"
            logger.error(error_msg)

            # Update user's login status
            user.mark_login_attempt(False, error_msg)
            self.db.update_project_user(cast(Any, user))

            return {
                'success': False,
                'error': error_msg,
                'duration_ms': duration_ms
            }

    async def _perform_form_login(
        self,
        browser_page: Page,
        user: WebsiteUser | ProjectUser,
        timeout: int
    ) -> dict[str, Any]:
        """
        Perform form-based login

        Args:
            browser_page: Playwright page object
            user: WebsiteUser with login configuration
            timeout: Timeout in milliseconds

        Returns:
            Result dictionary
        """
        config = user.login_config

        # Validate configuration
        if not config.login_url:
            return {'success': False, 'error': 'Login URL not configured'}
        if not config.username_field_selector:
            return {'success': False, 'error': 'Username field selector not configured'}
        if not config.password_field_selector:
            return {'success': False, 'error': 'Password field selector not configured'}

        try:
            logger.info(f"Navigating to login page: {config.login_url}")

            # Navigate to login page (Playwright API)
            await browser_page.goto(config.login_url, wait_until='networkidle', timeout=timeout)

            # Wait for login form to be visible (Playwright API)
            logger.info(f"Waiting for username field: {config.username_field_selector}")
            await browser_page.wait_for_selector(
                config.username_field_selector,
                state='visible',
                timeout=timeout
            )

            # Fill username (Playwright API)
            logger.info(f"Entering username: {user.username}")
            await browser_page.fill(config.username_field_selector, user.username)

            # Fill password (Playwright API)
            logger.info("Entering password")
            await browser_page.fill(config.password_field_selector, user.password)

            # Click submit button if specified
            if config.submit_button_selector:
                logger.info(f"Clicking submit button: {config.submit_button_selector}")
                await browser_page.click(config.submit_button_selector)
            else:
                # Try submitting the form by pressing Enter
                logger.info("Pressing Enter to submit")
                await browser_page.keyboard.press('Enter')

            # Wait for navigation after login (Playwright API)
            try:
                await browser_page.wait_for_load_state('networkidle', timeout=timeout)
            except Exception as nav_error:
                logger.warning(f"Navigation wait timed out, checking success indicator: {nav_error}")

            # Check for success indicator
            if config.success_indicator_selector:
                logger.info(f"Checking for success indicator: {config.success_indicator_selector}")
                try:
                    await browser_page.wait_for_selector(
                        config.success_indicator_selector,
                        state='visible',
                        timeout=5000
                    )
                    logger.info("Login success indicator found")
                    return {'success': True, 'error': None}
                except Exception as e:
                    error_msg = f"Success indicator not found: {config.success_indicator_selector}"
                    logger.error(error_msg)
                    return {'success': False, 'error': error_msg}
            else:
                # No success indicator - assume success if we got here
                logger.info("No success indicator configured, assuming login successful")
                return {'success': True, 'error': None}

        except Exception as e:
            error_msg = f"Form login failed: {str(e)}"
            logger.error(error_msg)
            return {'success': False, 'error': error_msg}

    async def _perform_basic_auth(
        self,
        browser_page: Page,
        user: WebsiteUser | ProjectUser
    ) -> dict[str, Any]:
        """
        Perform HTTP Basic Authentication

        Args:
            browser_page: Playwright page object
            user: WebsiteUser with credentials

        Returns:
            Result dictionary
        """
        try:
            # Set authentication credentials (Playwright API - set on context)
            await browser_page.context.set_extra_http_headers({
                'Authorization': 'Basic ' + __import__('base64').b64encode(f'{user.username}:{user.password}'.encode()).decode()
            })

            logger.info(f"Basic auth credentials set for user: {user.username}")
            return {'success': True, 'error': None}

        except Exception as e:
            error_msg = f"Basic auth failed: {str(e)}"
            logger.error(error_msg)
            return {'success': False, 'error': error_msg}

    async def _perform_manual_login(
        self,
        browser_page: Page,
        user: WebsiteUser | ProjectUser,
    ) -> dict[str, Any]:
        """
        Perform manual (interactive) login.

        Launches a *separate* visible browser window for the human operator to
        log in (including 2FA, SSO, multi-page flows). The test-run browser
        ``browser_page`` belongs to keeps whatever headless setting the project
        configured — we don't touch it. After ``manual_login_wait_seconds``
        elapses (or the operator closes the login window), cookies captured in
        the visible context are transferred into ``browser_page.context`` so
        subsequent test navigation is authenticated. The visible browser is
        always closed before this method returns.

        Limitations: only cookies are transferred. Site-specific
        ``localStorage`` / ``sessionStorage`` are not — most auth flows use
        cookies, but token-in-localStorage SPAs may need additional handling.
        """
        # Imported lazily to avoid a hard dep at module import time.
        from auto_a11y.core.browser_manager import BrowserManager

        config = user.login_config
        wait_seconds = max(1, getattr(config, 'manual_login_wait_seconds', 120))

        visible_config: dict[str, Any] = {
            'headless': False,
            'BROWSER_HEADLESS': False,
            'timeout': 60000,
            'viewport_width': 1280,
            'viewport_height': 800,
        }
        visible_bm = BrowserManager(visible_config)

        try:
            await visible_bm.start()
            visible_ctx = await visible_bm.create_context()
            visible_page = await visible_ctx.new_page()

            if config.login_url:
                logger.info(f"Manual login: opening visible window at {config.login_url}")
                try:
                    await visible_page.goto(
                        config.login_url,
                        wait_until='domcontentloaded',
                        timeout=30000,
                    )
                except Exception as nav_error:
                    logger.warning(
                        f"Manual login: initial navigation issue (continuing): {nav_error}"
                    )
            else:
                logger.info("Manual login: no login_url configured, opened blank visible window")

            # Wait either for the configured timeout OR the operator closing
            # the login page early — whichever comes first. Closing early lets
            # finished users skip the rest of the wait.
            logger.info(f"Manual login: waiting up to {wait_seconds}s for user to complete login")

            # Poll for window closure so finished operators can skip the
            # remaining wait by closing the tab. Polling (rather than
            # ``page.wait_for_event``) keeps the types fully reified in strict
            # type-check mode.
            poll_interval = 0.5
            elapsed = 0.0
            closed_early = False
            while elapsed < wait_seconds:
                if visible_page.is_closed():
                    closed_early = True
                    break
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval
            if closed_early:
                logger.info("Manual login: operator closed the login window — proceeding early")
            else:
                logger.info("Manual login: wait elapsed")

            # Capture cookies from the visible context, even if the page was
            # closed (the context still holds them).
            raw_cookies: list[Any] = []
            try:
                storage = await visible_ctx.storage_state()
                raw_cookies = list(storage.get('cookies') or [])
            except Exception as state_err:
                logger.error(f"Manual login: could not read storage_state: {state_err}")

            # storage_state() returns StorageStateCookie objects; add_cookies()
            # expects SetCookieParam. The shapes overlap but Playwright types
            # them distinctly (and StorageStateCookie marks fields as
            # NotRequired), so reproject explicitly and skip any cookie missing
            # the required identity fields.
            cookies: list[SetCookieParam] = []
            for c in raw_cookies:
                name = c.get('name')
                value = c.get('value')
                domain = c.get('domain')
                path = c.get('path')
                if not (isinstance(name, str) and isinstance(value, str)
                        and isinstance(domain, str) and isinstance(path, str)):
                    continue
                expires_raw = c.get('expires')
                expires: float | None = (
                    float(expires_raw)
                    if isinstance(expires_raw, (int, float)) and expires_raw > 0
                    else None
                )
                http_only = c.get('httpOnly')
                secure = c.get('secure')
                same_site_raw = c.get('sameSite')
                same_site: Any = same_site_raw if same_site_raw in ('Lax', 'None', 'Strict') else None
                cookie_param: SetCookieParam = {
                    'name': name,
                    'value': value,
                    'url': None,
                    'domain': domain,
                    'path': path,
                    'expires': expires,
                    'httpOnly': http_only if isinstance(http_only, bool) else None,
                    'secure': secure if isinstance(secure, bool) else None,
                    'sameSite': same_site,
                    'partitionKey': None,
                }
                cookies.append(cookie_param)

            # Transfer cookies into the test browser's context.
            target_ctx = browser_page.context
            if cookies:
                try:
                    await target_ctx.add_cookies(cookies)
                    logger.info(
                        f"Manual login: transferred {len(cookies)} cookie(s) from login window to test browser"
                    )
                except Exception as add_err:
                    error_msg = f"Manual login: failed to transfer cookies: {add_err}"
                    logger.error(error_msg)
                    return {'success': False, 'error': error_msg}
            else:
                logger.warning(
                    "Manual login: no cookies were established during the wait window"
                )

            return {
                'success': True,
                'error': None,
                'wait_seconds': wait_seconds,
                'cookies_transferred': len(cookies),
            }

        except Exception as e:
            error_msg = f"Manual login failed: {str(e)}"
            logger.error(error_msg)
            return {'success': False, 'error': error_msg}

        finally:
            # Always close the visible login window, even on error.
            try:
                await visible_bm.stop()
                logger.info("Manual login: closed visible login window")
            except Exception as stop_err:
                logger.warning(f"Manual login: error closing visible window: {stop_err}")

    async def perform_logout(
        self,
        browser_page: Page,
        user: WebsiteUser | ProjectUser,
        timeout: int = 30000
    ) -> dict[str, Any]:
        """
        Perform automated logout for a user

        Args:
            browser_page: Playwright page object
            user: WebsiteUser object with logout configuration
            timeout: Maximum time to wait for logout (milliseconds)

        Returns:
            Dictionary with success status, error message, and timing
        """
        start_time = datetime.now()

        try:
            login_config = user.login_config

            # Check if logout is configured
            if not login_config.logout_url and not login_config.logout_button_selector:
                logger.info(f"No logout configuration for user {user.username}, clearing cookies instead")
                # Clear all cookies to ensure clean logout (Playwright API)
                await browser_page.context.clear_cookies()
                return {
                    'success': True,
                    'error': None,
                    'duration_ms': int((datetime.now() - start_time).total_seconds() * 1000),
                    'method': 'cookie_clear'
                }

            # Perform configured logout
            if login_config.logout_url:
                # Navigate to logout URL (Playwright API)
                logger.info(f"Navigating to logout URL: {login_config.logout_url}")
                await browser_page.goto(login_config.logout_url, wait_until='networkidle', timeout=timeout)

            # Click logout button if specified
            if login_config.logout_button_selector:
                logger.info(f"Clicking logout button: {login_config.logout_button_selector}")
                try:
                    await browser_page.wait_for_selector(
                        login_config.logout_button_selector,
                        state='visible',
                        timeout=5000
                    )
                    await browser_page.click(login_config.logout_button_selector)

                    # Wait for navigation if logout triggers redirect (Playwright API)
                    try:
                        await browser_page.wait_for_load_state('networkidle', timeout=5000)
                    except:
                        pass  # Navigation may not happen for AJAX logouts

                except Exception as e:
                    logger.warning(f"Could not click logout button: {e}")

            # Check for logout success indicator
            if login_config.logout_success_indicator_selector:
                logger.info(f"Checking for logout success indicator: {login_config.logout_success_indicator_selector}")
                try:
                    await browser_page.wait_for_selector(
                        login_config.logout_success_indicator_selector,
                        state='visible',
                        timeout=5000
                    )
                    logger.info("Logout success indicator found")
                except Exception as e:
                    logger.warning(f"Logout success indicator not found: {e}")
                    # Still return success - we tried our best

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            logger.info(f"Logout completed for user {user.username} in {duration_ms}ms")

            return {
                'success': True,
                'error': None,
                'duration_ms': duration_ms,
                'method': 'configured_logout'
            }

        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            error_msg = f"Logout error: {str(e)}"
            logger.error(error_msg)

            # Try to clear cookies as fallback (Playwright API)
            try:
                await browser_page.context.clear_cookies()
                logger.info("Cleared cookies as logout fallback")
            except:
                pass

            return {
                'success': False,
                'error': error_msg,
                'duration_ms': duration_ms
            }

    def is_session_valid(self, user: WebsiteUser) -> bool:
        """
        Check if a user's session is still valid based on timeout

        Args:
            user: WebsiteUser to check

        Returns:
            True if session is still valid
        """
        if not user.last_used:
            return False

        # Check if session has timed out
        elapsed_minutes = (datetime.now() - user.last_used).total_seconds() / 60
        return elapsed_minutes < user.login_config.session_timeout_minutes
