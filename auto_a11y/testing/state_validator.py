"""
State validator for page setup scripts

Validates that page state matches expectations after script execution.
"""

from __future__ import annotations

from typing import Any

from playwright.async_api import Page

from auto_a11y.models import Violation, ImpactLevel, PageTestState


class StateValidator:
    """Validates page state after script execution"""

    async def _find_element(self, page: Page, selector: str) -> Any:
        """
        Find element by CSS selector or XPath

        Args:
            page: Playwright Page object
            selector: CSS selector or XPath (XPath must start with / or //)

        Returns:
            Locator first element or None
        """
        if selector.startswith('/'):
            locator = page.locator(f"xpath={selector}")
        else:
            locator = page.locator(selector)
        count = await locator.count()
        return locator.first if count > 0 else None

    async def _is_element_visible(self, page: Page, selector: str) -> bool:
        """
        Check if element is visible (supports both CSS and XPath)

        Args:
            page: Playwright Page object
            selector: CSS selector or XPath

        Returns:
            True if element exists and is visible
        """
        if selector.startswith('/'):
            # XPath selector
            result: bool = await page.evaluate('''(xpath) => {
                const result = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                const el = result.singleNodeValue;
                if (!el) return false;
                const style = window.getComputedStyle(el);
                return style.display !== 'none' &&
                       style.visibility !== 'hidden' &&
                       style.opacity !== '0' &&
                       el.offsetParent !== null;
            }''', selector)
            return result
        else:
            # CSS selector
            result = await page.evaluate('''(selector) => {
                const el = document.querySelector(selector);
                if (!el) return false;
                const style = window.getComputedStyle(el);
                return style.display !== 'none' &&
                       style.visibility !== 'hidden' &&
                       style.opacity !== '0' &&
                       el.offsetParent !== null;
            }''', selector)
            return bool(result)

    async def validate_state(  # noqa: C901
        self,
        page: Page,
        expected_state: PageTestState
    ) -> list[Violation]:
        """
        Validate that page is in expected state

        Args:
            page: Pyppeteer page object
            expected_state: Expected page state

        Returns:
            List of violations if expectations not met
        """
        violations: list[Violation] = []

        # Check elements that should be visible
        for selector in expected_state.elements_visible:
            try:
                element = await self._find_element(page, selector)
                if not element:
                    violations.append(Violation(
                        id='ErrExpectedElementNotVisible',
                        impact=ImpactLevel.HIGH,
                        touchpoint='page_state',
                        description=f'Expected element "{selector}" to be visible but it was not found',
                        xpath=None,
                        element=selector,
                        html=None,
                        failure_summary=f'State validation failed: expected visible element not found',
                        metadata={'expected_selector': selector, 'state_id': expected_state.state_id}
                    ))
                else:
                    # Check if element is actually visible (not hidden by CSS)
                    is_visible = await self._is_element_visible(page, selector)

                    if not is_visible:
                        violations.append(Violation(
                            id='ErrExpectedElementNotVisible',
                            impact=ImpactLevel.HIGH,
                            touchpoint='page_state',
                            description=f'Expected element "{selector}" to be visible but it is hidden',
                            xpath=None,
                            element=selector,
                            html=None,
                            failure_summary=f'State validation failed: expected visible element is hidden',
                            metadata={'expected_selector': selector, 'state_id': expected_state.state_id}
                        ))
            except Exception as e:
                violations.append(Violation(
                    id='ErrStateValidationFailed',
                    impact=ImpactLevel.MEDIUM,
                    touchpoint='page_state',
                    description=f'Failed to check visibility of element "{selector}": {str(e)}',
                    xpath=None,
                    element=selector,
                    html=None,
                    failure_summary='State validation error',
                    metadata={'expected_selector': selector, 'state_id': expected_state.state_id, 'error': str(e)}
                ))

        # Check elements that should be hidden
        for selector in expected_state.elements_hidden:
            try:
                element = await self._find_element(page, selector)
                if element:
                    # Check if element is actually visible
                    is_visible = await self._is_element_visible(page, selector)

                    if is_visible:
                        violations.append(Violation(
                            id='ErrExpectedElementStillVisible',
                            impact=ImpactLevel.HIGH,
                            touchpoint='page_state',
                            description=f'Expected element "{selector}" to be hidden but it is still visible',
                            xpath=None,
                            element=selector,
                            html=None,
                            failure_summary=f'State validation failed: expected hidden element is visible',
                            metadata={'expected_selector': selector, 'state_id': expected_state.state_id}
                        ))
            except Exception as e:
                violations.append(Violation(
                    id='ErrStateValidationFailed',
                    impact=ImpactLevel.MEDIUM,
                    touchpoint='page_state',
                    description=f'Failed to check hidden state of element "{selector}": {str(e)}',
                    xpath=None,
                    element=selector,
                    html=None,
                    failure_summary='State validation error',
                    metadata={'expected_selector': selector, 'state_id': expected_state.state_id, 'error': str(e)}
                ))

        return violations

    async def capture_current_state(
        self,
        page: Page,
        state_id: str,
        description: str,
        scripts_executed: list[str] | None = None,
        elements_clicked: list[dict[str, Any]] | None = None
    ) -> PageTestState:
        """
        Capture the current state of the page

        Args:
            page: Pyppeteer page object
            state_id: Unique identifier for this state
            description: Human-readable description
            scripts_executed: List of script IDs that were executed
            elements_clicked: List of elements that were clicked

        Returns:
            PageTestState object representing current state
        """
        state = PageTestState(
            state_id=state_id,
            description=description,
            scripts_executed=scripts_executed or [],
            elements_clicked=elements_clicked or []
        )

        # Note: elements_visible and elements_hidden are expectations set by scripts
        # Not captured automatically - would require expensive DOM analysis

        return state

    def create_expected_state(
        self,
        state_id: str,
        description: str,
        scripts_executed: list[str],
        expect_visible: list[str],
        expect_hidden: list[str],
        elements_clicked: list[dict[str, Any]] | None = None
    ) -> PageTestState:
        """
        Create an expected page state from script configuration

        Args:
            state_id: Unique identifier for this state
            description: Human-readable description
            scripts_executed: List of script IDs that will be executed
            expect_visible: Selectors that should be visible
            expect_hidden: Selectors that should be hidden
            elements_clicked: Elements that were clicked

        Returns:
            PageTestState object with expectations
        """
        return PageTestState(
            state_id=state_id,
            description=description,
            scripts_executed=scripts_executed,
            elements_clicked=elements_clicked or [],
            elements_visible=expect_visible,
            elements_hidden=expect_hidden
        )
