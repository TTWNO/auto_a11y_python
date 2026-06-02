"""
Regression tests for two code-audit findings in ScriptExecutor:

Bug A (MEDIUM): the SCROLL action ignored the computed Playwright selector
(``pw_selector``) and passed the raw selector to ``page.locator()``. For XPath
selectors Playwright needs the ``xpath=`` prefix, so XPath SCROLL steps always
errored.

Bug B (LOW): when a script cleared cookies/storage and re-authentication failed,
``execute_script`` only logged the error and continued running the steps against
an unauthenticated session, producing misleading results with no signal to the
caller.
"""

from __future__ import annotations

from collections.abc import Awaitable
from pathlib import Path
from typing import Any, Protocol, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

# Import auto_a11y.core before auto_a11y.testing to avoid a pre-existing
# circular import (auto_a11y.testing -> core.testing_job -> auto_a11y.testing)
# that only triggers when the testing package is imported first. The module is
# imported purely for its side effect of populating sys.modules in the right
# order, then deleted so it does not read as an unused binding.
import auto_a11y.core as _core

del _core
from auto_a11y.models import (
    ActionType,
    PageSetupScript,
    ScriptStep,
    WebsiteUser,
)
from auto_a11y.testing.script_executor import ScriptExecutor


class _ExecuteStep(Protocol):
    def __call__(
        self, page: Any, step: ScriptStep, env_vars: dict[str, str]
    ) -> Awaitable[None]: ...


def _execute_step(executor: ScriptExecutor) -> _ExecuteStep:
    """Typed shim around the private ``_execute_step``.

    Accessing the underscore-prefixed method directly trips pyright's
    ``reportPrivateUsage``; this is a deliberate unit test of the
    implementation, so we go through ``getattr`` to keep the access typed
    without a suppression comment (matching the pattern in
    ``tests/test_spa_click_discovery.py``).
    """
    return cast(_ExecuteStep, getattr(executor, "_execute_step"))


def _make_executor(tmp_path: Path) -> ScriptExecutor:
    return ScriptExecutor(screenshot_dir=tmp_path / "script_debug")


def _scroll_step(selector: str) -> ScriptStep:
    return ScriptStep(
        step_number=1,
        action_type=ActionType.SCROLL,
        description="scroll to element",
        selector=selector,
    )


class _RecordingLocator:
    """Mock Locator whose scroll_into_view_if_needed is awaitable and a no-op."""

    def __init__(self) -> None:
        self.scroll_into_view_if_needed = AsyncMock(return_value=None)


def _make_scroll_page() -> tuple[Any, list[str]]:
    """Build a mock page whose ``locator`` records the selector argument."""
    recorded: list[str] = []

    def locator(selector: str) -> _RecordingLocator:
        recorded.append(selector)
        return _RecordingLocator()

    page = MagicMock()
    page.locator = MagicMock(side_effect=locator)
    return page, recorded


# --------------------------------------------------------------------------- #
# Bug A: SCROLL must honour the xpath= prefix for XPath selectors.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scroll_xpath_selector_gets_xpath_prefix(tmp_path: Path) -> None:
    executor = _make_executor(tmp_path)
    page, recorded = _make_scroll_page()

    await _execute_step(executor)(
        page,
        _scroll_step("/html/body/div[2]/button"),
        {},
    )

    assert recorded == ["xpath=/html/body/div[2]/button"], (
        "XPath SCROLL selector must be passed to page.locator() with the "
        "xpath= prefix"
    )


@pytest.mark.asyncio
async def test_scroll_css_selector_unchanged(tmp_path: Path) -> None:
    executor = _make_executor(tmp_path)
    page, recorded = _make_scroll_page()

    await _execute_step(executor)(
        page,
        _scroll_step("#main .target"),
        {},
    )

    assert recorded == ["#main .target"], (
        "CSS SCROLL selector must be passed to page.locator() unchanged"
    )


# --------------------------------------------------------------------------- #
# Bug B: re-authentication failure must surface, not silently continue.
# --------------------------------------------------------------------------- #


def _script_clearing_state() -> PageSetupScript:
    return PageSetupScript(
        name="reauth script",
        description="clears state then runs a step",
        clear_cookies_before=True,
        steps=[
            ScriptStep(
                step_number=1,
                action_type=ActionType.CLICK,
                description="click something",
                selector="#go",
            )
        ],
    )


@pytest.mark.asyncio
async def test_reauth_failure_does_not_continue(tmp_path: Path) -> None:
    executor = _make_executor(tmp_path)

    # Page mock: clearing state + step execution would succeed if reached.
    page = MagicMock()
    page.context = MagicMock()
    page.context.clear_cookies = AsyncMock(return_value=None)
    page.click = AsyncMock(return_value=None)
    page.goto = AsyncMock(return_value=None)

    user = WebsiteUser(
        website_id="w1",
        username="tester",
        password="secret",
        display_name="Tester",
    )

    login_automation = MagicMock()
    login_automation.perform_login = AsyncMock(
        return_value={"success": False, "error": "bad credentials"}
    )

    result = await executor.execute_script(
        cast(Any, page),
        _script_clearing_state(),
        authenticated_user=user,
        login_automation=cast(Any, login_automation),
        page_url="https://example.test/page",
    )

    # The failure must be surfaced: result reports failure ...
    assert result["success"] is False
    assert "error" in result
    # ... and the normal step loop must NOT have run against the
    # unauthenticated session.
    page.click.assert_not_called()


@pytest.mark.asyncio
async def test_reauth_success_continues(tmp_path: Path) -> None:
    executor = _make_executor(tmp_path)

    page = MagicMock()
    page.context = MagicMock()
    page.context.clear_cookies = AsyncMock(return_value=None)
    page.click = AsyncMock(return_value=None)
    page.goto = AsyncMock(return_value=None)

    user = WebsiteUser(
        website_id="w1",
        username="tester",
        password="secret",
        display_name="Tester",
    )

    login_automation = MagicMock()
    login_automation.perform_login = AsyncMock(return_value={"success": True})

    result = await executor.execute_script(
        cast(Any, page),
        _script_clearing_state(),
        authenticated_user=user,
        login_automation=cast(Any, login_automation),
        page_url="https://example.test/page",
    )

    assert result["success"] is True
    page.click.assert_awaited_once()
