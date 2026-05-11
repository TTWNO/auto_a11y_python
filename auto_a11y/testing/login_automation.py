"""
Login automation for authenticated testing using Playwright
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Literal, cast, TYPE_CHECKING
from datetime import datetime

from playwright._impl._api_structures import SetCookieParam
from playwright.async_api import BrowserContext, Page

if TYPE_CHECKING:
    from auto_a11y.core.database import Database
    from auto_a11y.models import WebsiteUser, ProjectUser

logger = logging.getLogger(__name__)


def _session_cache_dir() -> Path:
    """Directory holding cached manual-login session state files."""
    from config import DATA_DIR
    cache_dir = Path(DATA_DIR) / 'manual_login_sessions'
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _session_cache_path(user: WebsiteUser | ProjectUser) -> Path:
    """Per-user cache file for a captured manual-login session."""
    uid = user.id or user.username  # id may be None on a fresh in-memory user
    safe = ''.join(ch if ch.isalnum() or ch in '-_' else '_' for ch in uid)
    return _session_cache_dir() / f"{safe}.json"


def _drop_none_fields(cookie: SetCookieParam) -> SetCookieParam:
    """Return a copy of ``cookie`` with ``None``-valued keys removed.

    Why: Playwright's wire-protocol validator (``tOptional`` in
    ``validatorPrimitives.js``) only short-circuits on JS ``undefined``.
    Python's ``None`` serializes to JSON ``null``, which falls through
    to the type-specific validator and fails with errors like
    ``cookies[0].url: expected string, got object`` (since
    ``typeof null === 'object'`` in JS). Optional fields without a
    value must therefore be omitted from the payload rather than sent
    as ``None``.
    """
    return cast(
        SetCookieParam,
        {k: v for k, v in cookie.items() if v is not None},
    )


def clear_session_cache(user: WebsiteUser | ProjectUser) -> bool:
    """Delete the cached manual-login session for ``user`` (if any).

    Returns True if a cache file existed and was removed, False if there
    was nothing to clear.
    """
    path = _session_cache_path(user)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def _session_cache_fresh(path: Path, max_age_minutes: int) -> bool:
    """True when ``path`` exists and was modified within ``max_age_minutes``."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False
    age_seconds = datetime.now().timestamp() - mtime
    return age_seconds < (max_age_minutes * 60)


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
        Perform manual (interactive) login with session caching.

        Flow:

        1. Look for a cached session file for this user. If it exists and is
           younger than ``session_timeout_minutes``, inject it into the test
           browser context and return immediately — no visible window opens.
        2. Otherwise launch a *separate* visible browser, navigate it to
           ``login_url``, and wait up to ``manual_login_wait_seconds`` (or
           until the operator closes the window) for the user to complete
           login (including 2FA, SSO, multi-step flows).
        3. Capture the full session — cookies, localStorage, and
           sessionStorage — from the visible context, persist it to the
           cache file, and inject it into the test browser context.
        4. Always close the visible window before returning.

        The test browser keeps whatever headless setting the project
        configured; only the temporary login window is visible.
        """
        # Imported lazily to avoid a hard dep at module import time.
        from auto_a11y.core.browser_manager import BrowserManager

        config = user.login_config
        wait_seconds = max(1, getattr(config, 'manual_login_wait_seconds', 120))
        session_timeout_minutes = max(1, config.session_timeout_minutes)
        cache_path = _session_cache_path(user)

        # 1. Try the cache first.
        if _session_cache_fresh(cache_path, session_timeout_minutes):
            try:
                with cache_path.open('r', encoding='utf-8') as f:
                    cached: dict[str, Any] = json.load(f)
                injected = await self._inject_session(browser_page.context, cached)
                summary = (
                    f"{injected['cookies']} cookie(s), "
                    + f"{injected['local_storage_origins']} localStorage origin(s), "
                    + f"{injected['session_storage_origins']} sessionStorage origin(s)"
                )
                logger.info(
                    f"Manual login: reused cached session for {user.username} "
                    + f"({summary}) — no visible window opened"
                )
                return {
                    'success': True,
                    'error': None,
                    'wait_seconds': 0,
                    'reused_cache': True,
                    'cookies_transferred': injected['cookies'],
                }
            except (OSError, json.JSONDecodeError) as cache_err:
                logger.warning(
                    f"Manual login: cached session at {cache_path} unusable "
                    + f"({cache_err}); falling back to interactive login"
                )

        # 2. Open a fresh visible browser for interactive login.
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
            # the login page early. Polling (rather than page.wait_for_event)
            # keeps the types reified in strict mode.
            logger.info(f"Manual login: waiting up to {wait_seconds}s for user to complete login")
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

            # 3. Capture full session state. sessionStorage is tab-scoped and
            # not in storage_state(), so read it directly from the page if
            # it's still open.
            captured: dict[str, Any] = {
                'cookies': [],
                'origins': [],
                'session_storage_by_origin': {},
            }

            try:
                storage = await visible_ctx.storage_state()
                captured['cookies'] = list(storage.get('cookies') or [])
                captured['origins'] = list(storage.get('origins') or [])
            except Exception as state_err:
                logger.error(f"Manual login: could not read storage_state: {state_err}")

            if not visible_page.is_closed():
                try:
                    page_origin = await visible_page.evaluate('() => window.location.origin')
                    if isinstance(page_origin, str) and page_origin and page_origin != 'null':
                        session_dump_script = (
                            "() => { const o = {};"
                            + " for (let i = 0; i < sessionStorage.length; i++) {"
                            + "   const k = sessionStorage.key(i);"
                            + "   if (k !== null) o[k] = sessionStorage.getItem(k);"
                            + " } return o; }"
                        )
                        session_dump = await visible_page.evaluate(session_dump_script)
                        if isinstance(session_dump, dict) and session_dump:
                            captured['session_storage_by_origin'][page_origin] = session_dump
                except Exception as ss_err:
                    logger.warning(f"Manual login: could not capture sessionStorage: {ss_err}")

            # Persist cache so subsequent runs (within session_timeout_minutes)
            # skip the visible window. Best-effort — failure here doesn't
            # break the current login.
            try:
                with cache_path.open('w', encoding='utf-8') as f:
                    json.dump(captured, f)
                logger.info(f"Manual login: cached session to {cache_path}")
            except OSError as cache_write_err:
                logger.warning(f"Manual login: could not write session cache: {cache_write_err}")

            # 4. Inject into the test browser context.
            injected = await self._inject_session(browser_page.context, captured)
            if injected['cookies'] == 0 and injected['local_storage_origins'] == 0:
                logger.warning(
                    "Manual login: no cookies or storage were established during the wait"
                )

            return {
                'success': True,
                'error': None,
                'wait_seconds': wait_seconds,
                'reused_cache': False,
                'cookies_transferred': injected['cookies'],
                'local_storage_origins': injected['local_storage_origins'],
                'session_storage_origins': injected['session_storage_origins'],
            }

        except Exception as e:
            error_msg = f"Manual login failed: {str(e)}"
            logger.error(error_msg)
            return {'success': False, 'error': error_msg}

        finally:
            try:
                await visible_bm.stop()
                logger.info("Manual login: closed visible login window")
            except Exception as stop_err:
                logger.warning(f"Manual login: error closing visible window: {stop_err}")

    async def _inject_session(
        self,
        target_ctx: BrowserContext,
        captured: dict[str, Any],
    ) -> dict[str, int]:
        """
        Inject cookies, localStorage, and sessionStorage into ``target_ctx``.

        - Cookies are added directly to the context.
        - localStorage / sessionStorage entries are scheduled via an init
          script: on every page load, the script checks ``window.location.origin``
          and populates storage for that origin. This means the test browser
          gets the right values whenever it navigates to one of the captured
          origins, without any pre-navigation hack.

        Returns a dict with counts of what was injected, for logging.
        """
        # Cookies. Build each SetCookieParam declaratively with whatever
        # fields the captured cookie has, using ``None`` for absent ones,
        # then pass through ``_drop_none_fields`` to omit the missing keys
        # before sending to Playwright.
        raw_cookies = list(captured.get('cookies') or [])
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
            same_site: Literal['Lax', 'None', 'Strict'] | None = (
                same_site_raw
                if same_site_raw in ('Lax', 'None', 'Strict')
                else None
            )
            cookies.append(_drop_none_fields({
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
            }))
        if cookies:
            try:
                await target_ctx.add_cookies(cookies)
            except Exception as add_err:
                logger.error(f"Manual login: failed to add cookies: {add_err}")

        # localStorage from storage_state().origins, sessionStorage captured
        # separately. Build a single map per origin.
        origins_data: list[dict[str, Any]] = []
        local_storage_count = 0
        origins_raw = cast(list[dict[str, Any]], captured.get('origins') or [])
        session_map = cast(
            dict[str, dict[str, Any]],
            captured.get('session_storage_by_origin') or {},
        )
        for origin in origins_raw:
            origin_url_raw = origin.get('origin')
            if not isinstance(origin_url_raw, str):
                continue
            origin_url: str = origin_url_raw
            local_items: list[dict[str, str]] = []
            local_raw = cast(list[dict[str, Any]], origin.get('localStorage') or [])
            for item in local_raw:
                k = item.get('name')
                v = item.get('value')
                if isinstance(k, str) and isinstance(v, str):
                    local_items.append({'name': k, 'value': v})
            session_for_origin: dict[str, Any] = session_map.get(origin_url) or {}
            session_items: dict[str, str] = {}
            for k_any, v_any in session_for_origin.items():
                if isinstance(v_any, str):
                    session_items[k_any] = v_any
            if local_items or session_items:
                origins_data.append({
                    'origin': origin_url,
                    'localStorage': local_items,
                    'sessionStorage': session_items,
                })
                local_storage_count += 1 if local_items else 0

        # Origins captured ONLY in session_storage_by_origin (no localStorage
        # entry from storage_state) still need an injection record.
        seen: set[str] = {str(entry['origin']) for entry in origins_data}
        session_storage_count = sum(
            1 for entry in origins_data if entry.get('sessionStorage')
        )
        for so_url, so_items_any in session_map.items():
            if so_url in seen:
                continue
            so_items: dict[str, Any] = so_items_any or {}
            session_items_only: dict[str, str] = {}
            for k_any, v_any in so_items.items():
                if isinstance(v_any, str):
                    session_items_only[k_any] = v_any
            if session_items_only:
                origins_data.append({
                    'origin': so_url,
                    'localStorage': [],
                    'sessionStorage': session_items_only,
                })
                session_storage_count += 1

        if origins_data:
            init_script = (
                "(() => { try { "
                f"const data = {json.dumps(origins_data)};"
                "for (const entry of data) {"
                "  if (window.location.origin !== entry.origin) continue;"
                "  for (const item of (entry.localStorage || [])) {"
                "    try { localStorage.setItem(item.name, item.value); } catch (e) {}"
                "  }"
                "  const ss = entry.sessionStorage || {};"
                "  for (const k of Object.keys(ss)) {"
                "    try { sessionStorage.setItem(k, ss[k]); } catch (e) {}"
                "  }"
                "} } catch (e) {} })();"
            )
            try:
                await target_ctx.add_init_script(init_script)
            except Exception as init_err:
                logger.error(f"Manual login: failed to add storage init script: {init_err}")

        return {
            'cookies': len(cookies),
            'local_storage_origins': local_storage_count,
            'session_storage_origins': session_storage_count,
        }

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
