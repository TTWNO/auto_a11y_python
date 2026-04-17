"""
CSS Focus Rules Capture

Captures CSS stylesheets as they load via page.on('response') and extracts
:focus rules for efficient focus indicator testing.

This avoids the expensive runtime iteration through document.styleSheets
which can cause browser timeouts on pages with many stylesheets.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Callable
from dataclasses import dataclass, field

from playwright.async_api import Page, Response

logger = logging.getLogger(__name__)


@dataclass
class FocusRule:
    """Represents a CSS focus rule"""
    selector: str
    properties: dict[str, str]
    source_url: str = ""


@dataclass
class CSSFocusCache:
    """Cache for focus rules extracted from stylesheets"""
    focus_rules: list[FocusRule] = field(default_factory=lambda: [])
    selectors_with_focus: set[str] = field(default_factory=lambda: set())
    captured_urls: set[str] = field(default_factory=lambda: set())
    is_complete: bool = False


class CSSFocusCapture:
    """Captures and parses CSS focus rules during page load"""

    def __init__(self) -> None:
        self.cache: CSSFocusCache = CSSFocusCache()
        self._response_handler: Callable[[Response], None] | None = None
        self._page: Page | None = None
        self._pending_responses: list[Response] = []
        self._loop: asyncio.AbstractEventLoop | None = None
    
    async def start_capture(self, page: Page) -> None:
        """
        Start capturing CSS responses on the page.
        Call this BEFORE navigating to the page.

        Args:
            page: Playwright Page object
        """
        self._page = page
        self.cache = CSSFocusCache()
        self._pending_responses = []
        
        try:
            self._loop = asyncio.get_event_loop()
        except RuntimeError:
            self._loop = None
        
        def handle_response(response: Response) -> None:
            """Synchronous handler that queues CSS responses for later processing"""
            try:
                content_type = response.headers.get('content-type', '')
                url = response.url
                
                if 'text/css' in content_type or url.endswith('.css'):
                    self._pending_responses.append(response)
            except Exception as e:
                logger.debug(f"Error in CSS response handler: {e}")
        
        self._response_handler = handle_response
        page.on('response', handle_response)
        logger.debug("CSS focus capture started")
    
    async def stop_capture(self) -> None:
        """Stop capturing CSS responses and process pending ones"""
        if self._page and self._response_handler:
            try:
                self._page.remove_listener('response', self._response_handler)
            except Exception as e:
                logger.debug(f"Error removing response listener: {e}")
        
        for response in self._pending_responses:
            try:
                url = response.url
                css_text = await asyncio.wait_for(response.text(), timeout=2.0)
                self._parse_focus_rules(css_text, url)
                self.cache.captured_urls.add(url)
            except asyncio.TimeoutError:
                logger.debug(f"Timeout reading CSS from {response.url}")
            except Exception as e:
                logger.debug(f"Could not read CSS from {response.url}: {e}")
        
        self._pending_responses = []
        self._response_handler = None
        self.cache.is_complete = True
        logger.debug(
            f"CSS focus capture stopped. Captured {len(self.cache.captured_urls)} stylesheets, "
            + f"found {len(self.cache.focus_rules)} focus rules"
        )
    
    async def capture_inline_styles(self, page: Page) -> None:
        """
        Capture focus rules from inline <style> tags.
        Call this AFTER the page has loaded.

        Args:
            page: Playwright Page object
        """
        try:
            inline_styles = await asyncio.wait_for(
                page.evaluate('''
                    () => {
                        const styles = [];
                        document.querySelectorAll('style').forEach((style, idx) => {
                            if (style.textContent) {
                                styles.push({
                                    content: style.textContent,
                                    index: idx
                                });
                            }
                        });
                        return styles;
                    }
                '''),
                timeout=5.0
            )
            
            for style_data in inline_styles:
                self._parse_focus_rules(
                    style_data['content'],
                    f"inline-style-{style_data['index']}"
                )
                
        except asyncio.TimeoutError:
            logger.warning("Timeout capturing inline styles")
        except Exception as e:
            logger.warning(f"Error capturing inline styles: {e}")
    
    def _parse_focus_rules(self, css_text: str, source_url: str) -> None:
        """
        Parse CSS text and extract :focus rules.
        
        Args:
            css_text: CSS stylesheet content
            source_url: URL or identifier of the stylesheet
        """
        if not css_text:
            return
        
        focus_pattern = re.compile(
            r'([^{}]+):focus(?:-visible|-within)?([^{]*)\s*\{([^}]+)\}',
            re.IGNORECASE | re.MULTILINE
        )
        
        for match in focus_pattern.finditer(css_text):
            try:
                base_selector = match.group(1).strip()
                pseudo_suffix = match.group(2).strip() if match.group(2) else ""
                properties_text = match.group(3).strip()
                
                full_selector = f"{base_selector}:focus{pseudo_suffix}".strip()
                
                properties = self._parse_properties(properties_text)
                
                if properties:
                    rule = FocusRule(
                        selector=full_selector,
                        properties=properties,
                        source_url=source_url
                    )
                    self.cache.focus_rules.append(rule)
                    
                    clean_selector = base_selector.split(',')[-1].strip()
                    self.cache.selectors_with_focus.add(clean_selector)
                    
            except Exception as e:
                logger.debug(f"Error parsing focus rule: {e}")
    
    def _parse_properties(self, properties_text: str) -> dict[str, str]:
        """
        Parse CSS properties from a rule body.
        
        Args:
            properties_text: CSS properties string (e.g., "outline: 2px solid blue; color: red")
            
        Returns:
            Dictionary of property name to value
        """
        properties: dict[str, str] = {}

        for prop in properties_text.split(';'):
            prop = prop.strip()
            if ':' in prop:
                name, value = prop.split(':', 1)
                name = name.strip().lower()
                value = value.strip()
                
                if name and value:
                    properties[name] = value
        
        return properties
    
    def get_focus_styles_for_selector(self, selector: str) -> dict[str, str] | None:
        """
        Get focus styles that might apply to an element matching the selector.
        
        Args:
            selector: CSS selector to check (e.g., "a", ".btn", "#nav-link")
            
        Returns:
            Dictionary of focus properties or None if no rules match
        """
        merged_properties: dict[str, str] = {}

        for rule in self.cache.focus_rules:
            rule_selector = rule.selector.replace(':focus', '').replace(':focus-visible', '').replace(':focus-within', '')
            rule_selector = rule_selector.strip()
            
            if self._selector_might_match(rule_selector, selector):
                merged_properties.update(rule.properties)
        
        return merged_properties if merged_properties else None
    
    def _selector_might_match(self, rule_selector: str, element_selector: str) -> bool:
        """
        Check if a rule selector might match an element.
        This is a heuristic - actual matching happens in the browser.
        
        Args:
            rule_selector: The selector from the CSS rule
            element_selector: The element's tag/class/id info
            
        Returns:
            True if the rule might apply to the element
        """
        if rule_selector in ['a', 'button', 'input', 'select', 'textarea', '*', ':focus']:
            return True
        
        if ',' in rule_selector:
            parts = [p.strip() for p in rule_selector.split(',')]
            return any(self._selector_might_match(p, element_selector) for p in parts)
        
        element_selector_lower = element_selector.lower()
        rule_selector_lower = rule_selector.lower()
        
        if element_selector_lower in rule_selector_lower:
            return True
        
        if rule_selector_lower.startswith('a') or rule_selector_lower.endswith(' a'):
            if element_selector_lower == 'a' or element_selector_lower.startswith('a.') or element_selector_lower.startswith('a#'):
                return True
        
        return False
    
    def has_focus_rules(self) -> bool:
        """Check if any focus rules were captured"""
        return len(self.cache.focus_rules) > 0
    
    def get_all_focus_rules(self) -> list[FocusRule]:
        """Get all captured focus rules"""
        return self.cache.focus_rules.copy()
    
    def to_dict(self) -> dict[str, Any]:
        """Convert cache to dictionary for passing to page.evaluate()"""
        rules_dict: dict[str, dict[str, str]] = {}
        
        for rule in self.cache.focus_rules:
            base_selector = rule.selector.replace(':focus-visible', ':focus').replace(':focus-within', ':focus')
            base_selector = base_selector.replace(':focus', '').strip()
            
            if base_selector not in rules_dict:
                rules_dict[base_selector] = {}
            
            rules_dict[base_selector].update(rule.properties)
        
        return {
            'rules': rules_dict,
            'selectors': list(self.cache.selectors_with_focus),
            'stylesheet_count': len(self.cache.captured_urls)
        }


_page_css_cache: dict[int, CSSFocusCapture] = {}


def get_css_capture_for_page(page: Page) -> CSSFocusCapture | None:
    """Get the CSS capture instance for a page"""
    page_id = id(page)
    return _page_css_cache.get(page_id)


def set_css_capture_for_page(page: Page, capture: CSSFocusCapture) -> None:
    """Store CSS capture instance for a page"""
    page_id = id(page)
    _page_css_cache[page_id] = capture


def clear_css_capture_for_page(page: Page) -> None:
    """Remove CSS capture instance for a page"""
    page_id = id(page)
    if page_id in _page_css_cache:
        del _page_css_cache[page_id]
