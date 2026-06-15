"""
Specific AI analysis modules for different accessibility aspects
"""

from __future__ import annotations

import logging
from typing import Any
from bs4 import BeautifulSoup
from bs4.element import Tag
from auto_a11y.ai.claude_client import ClaudeClient

logger = logging.getLogger(__name__)


def _xpath_literal(value: str) -> str:
    """
    Return a valid XPath 1.0 string literal for an arbitrary value.

    XPath has no escape mechanism inside string literals and does NOT interpret
    XML entities such as ``&apos;``. The only ways to embed a quote are to wrap
    the value in the *other* quote character, or — when both quote characters
    are present — to build the value with ``concat(...)``.

    Args:
        value: The raw string to embed in an XPath predicate.

    Returns:
        A string that is a syntactically valid XPath string expression
        evaluating to ``value`` (a quoted literal, or a ``concat(...)`` call).
    """
    if "'" not in value:
        # No single quotes -> safe to wrap in single quotes.
        return f"'{value}'"
    if '"' not in value:
        # Has single quotes but no double quotes -> wrap in double quotes.
        return f'"{value}"'

    # Both quote characters present: split into pieces around the single
    # quotes and concat() them, inserting each apostrophe as a double-quoted
    # "'" literal so no piece ever contains a single quote.
    parts: list[str] = []
    segments = value.split("'")
    for index, segment in enumerate(segments):
        if segment:
            parts.append(f"'{segment}'")
        if index < len(segments) - 1:
            parts.append("\"'\"")
    if not parts:
        # value was only single quotes
        parts = ["\"'\""] * value.count("'")
    return f"concat({', '.join(parts)})"


def find_element_xpath_by_text(html: str, text_sample: str) -> str | None:
    """
    Find an element in HTML by its text content and return a precise xpath.

    Args:
        html: The HTML content to search
        text_sample: The text to find

    Returns:
        XPath string or None if not found
    """
    if not html or not text_sample:
        return None

    soup = BeautifulSoup(html, 'html.parser')

    # Clean the text sample for comparison
    clean_text = text_sample.strip()

    # Find elements containing this text
    # First try exact match, then partial match
    for nav_str in soup.find_all(string=lambda t: bool(t and clean_text in t)):
        parent = nav_str.parent
        if parent is not None and parent.name:
            xpath = _build_xpath_for_element(parent, soup)
            if xpath:
                return xpath

    # Also try finding by normalized text in elements
    for tag_name in ['p', 'span', 'div', 'a', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'td', 'th', 'label', 'button']:
        for el in soup.find_all(tag_name):
            element_text = el.get_text(strip=True)
            if clean_text in element_text:
                xpath = _build_xpath_for_element(el, soup)
                if xpath:
                    return xpath

    return None


def _build_xpath_for_element(element: Tag, soup: BeautifulSoup) -> str | None:
    """
    Build a precise xpath for a BeautifulSoup element.

    Args:
        element: BeautifulSoup Tag element
        soup: Root soup object

    Returns:
        XPath string
    """
    if not element or not element.name:
        return None

    tag: str = element.name

    # Priority 1: ID (most specific)
    element_id = element.get('id')
    if element_id and isinstance(element_id, str):
        return f"//*[@id={_xpath_literal(element_id)}]"

    # Priority 2: Unique class combination
    raw_classes = element.get('class')
    classes: list[str] = raw_classes if isinstance(raw_classes, list) else ([str(raw_classes)] if raw_classes else [])
    if classes:
        class_str = ' '.join(classes)
        # Check if this class combo is unique
        matching = soup.find_all(tag, class_=classes)
        if len(matching) == 1:
            if len(classes) == 1:
                return f"//{tag}[@class={_xpath_literal(class_str)}]"
            else:
                conditions = " and ".join([f"contains(@class, {_xpath_literal(c)})" for c in classes])
                return f"//{tag}[{conditions}]"
        elif len(matching) > 1:
            # Find index among siblings with same class
            for idx, el in enumerate(matching, 1):
                if el == element:
                    if len(classes) == 1:
                        return f"(//{tag}[@class={_xpath_literal(class_str)}])[{idx}]"
                    else:
                        conditions = " and ".join([f"contains(@class, {_xpath_literal(c)})" for c in classes])
                        return f"(//{tag}[{conditions}])[{idx}]"

    # Priority 3: Text content (for short, unique text)
    element_text = element.get_text(strip=True)
    if element_text and len(element_text) <= 60:
        # Check uniqueness -- find all tags then filter by text content
        all_tags = soup.find_all(tag)
        text_matches = [t for t in all_tags if element_text in t.get_text()]
        if len(text_matches) <= 1:
            if len(element_text) <= 30:
                return f"//{tag}[normalize-space()={_xpath_literal(element_text)}]"
            else:
                return f"//{tag}[contains(normalize-space(), {_xpath_literal(element_text[:40])})]"

    # Priority 4: Position among all same tags
    all_same_tags = soup.find_all(tag)
    for idx, el in enumerate(all_same_tags, 1):
        if el == element:
            return f"(//{tag})[{idx}]"

    return f"//{tag}"


def generate_xpath(
    element_tag: str,
    element_id: str | None = None,
    element_class: str | None = None,
    element_text: str | None = None,
    element_index: int | None = None,
    use_text: bool = False,
) -> str:
    """
    Generate an XPath selector from element attributes
    Similar to Chrome DevTools Elements.DOMPath.xPath for consistency

    Args:
        element_tag: HTML tag name
        element_id: Element ID attribute
        element_class: Element class attribute
        element_text: Text content (only used if use_text=True)
        element_index: Position index among siblings
        use_text: Whether to include text in XPath (default False for reliability)

    Returns:
        XPath selector string that can be used in Chrome DevTools
    """
    # Sanitize inputs
    if element_tag:
        element_tag = element_tag.strip().lower()
    if element_id:
        element_id = element_id.strip()
    if element_class:
        element_class = element_class.strip()

    # Priority 1: ID (most specific and reliable)
    if element_id:
        return f"//*[@id={_xpath_literal(element_id)}]"

    # Priority 2: Class name (without text to avoid duplicates)
    elif element_class:
        # Handle multiple classes
        classes = element_class.split()
        if len(classes) == 1:
            # Single class - exact match
            xpath = f"//{element_tag}[@class={_xpath_literal(element_class)}]"
        else:
            # Multiple classes - use contains for each
            class_conditions = " and ".join([f"contains(@class, {_xpath_literal(cls)})" for cls in classes])
            xpath = f"//{element_tag}[{class_conditions}]"

        # Add index if provided for more specificity
        if element_index is not None and element_index > 0:
            xpath = f"({xpath})[{element_index}]"

        return xpath

    # Priority 3: Position index (more reliable than text)
    elif element_index is not None and element_index > 0:
        return f"(//{element_tag})[{element_index}]"

    # Priority 4: Text content (only if explicitly requested and no other option)
    elif use_text and element_text:
        text_snippet = element_text[:50]

        # Special case for single character elements (like x for close buttons)
        if len(element_text) == 1:
            return f"//{element_tag}[text()={_xpath_literal(text_snippet)}]"
        elif len(element_text) <= 30:
            return f"//{element_tag}[normalize-space()={_xpath_literal(text_snippet)}]"
        else:
            return f"//{element_tag}[contains(normalize-space(), {_xpath_literal(text_snippet)})]"

    # Last resort: Tag with first position
    else:
        # Return first occurrence to be more specific
        logger.warning(f"Generating positional XPath for {element_tag}")
        return f"(//{element_tag})[1]"


class HeadingAnalyzer:
    """Analyzes heading structure visually and semantically"""

    def __init__(self, client: ClaudeClient) -> None:
        """
        Initialize heading analyzer

        Args:
            client: Claude client instance
        """
        self.client: ClaudeClient = client

    async def analyze(self, screenshot: bytes, html: str) -> dict[str, Any]:
        """
        Analyze headings for visual/semantic mismatches

        Args:
            screenshot: Page screenshot
            html: Page HTML

        Returns:
            Analysis results with specific issue codes
        """
        prompt = """Analyze heading structure in this web page screenshot and HTML.

Look for text that VISUALLY appears to be a heading (larger/bolder font, section title style) but is NOT using proper heading tags.

IMPORTANT: First check the HTML - if text is already inside <h1>, <h2>, <h3>, <h4>, <h5>, or <h6> tags, do NOT report it.

For each issue, find the element in HTML and extract its class and id attributes.

ISSUE CODES:

1. AI_ErrVisualHeadingNotMarked - Text looks like heading but uses <div>, <p>, <span>
   Required: visual_text (exact text), element_tag, element_class, element_id, suggested_level

2. AI_ErrHeadingLevelMismatch - Heading level wrong for visual prominence
   Required: heading_text, element_tag, current_level, suggested_level, element_class, element_id

3. AI_ErrSkippedHeading - Heading levels skip a level in the document outline (e.g. h2 followed by h4)
   Required: heading_text, element_tag, current_level, expected_level, element_class, element_id

Return JSON:
{
    "issues": [
        {
            "err": "AI_ErrVisualHeadingNotMarked",
            "type": "err",
            "visual_text": "exact text",
            "element_tag": "div",
            "element_class": "class-from-html",
            "element_id": "id-or-null",
            "suggested_level": 2,
            "description": "Text 'Example' uses <div> instead of heading"
        }
    ],
    "summary": "Brief summary"
}

RULES:
- Do NOT report text already in h1-h6 tags
- Extract element_class and element_id from the HTML
- Report only clear issues where heading markup is missing"""

        try:
            result: dict[str, Any] = await self.client.analyze_with_image_and_html(
                screenshot, html, prompt
            )

            # Ensure we have the expected structure
            if 'issues' not in result:
                result['issues'] = []

            return result

        except Exception as e:
            logger.error(f"Heading analysis failed: {e}")
            return {
                'error': str(e),
                'issues': []
            }


class ReadingOrderAnalyzer:
    """Analyzes reading order consistency"""

    def __init__(self, client: ClaudeClient) -> None:
        self.client: ClaudeClient = client

    async def analyze(self, screenshot: bytes, html: str) -> dict[str, Any]:
        """
        Check if visual reading order matches DOM order

        Args:
            screenshot: Page screenshot
            html: Page HTML

        Returns:
            Reading order analysis with specific issue codes
        """
        prompt = """Analyze the reading order of this web page.

Compare the VISUAL reading order (left-to-right, top-to-bottom) with the DOM order in HTML.

ISSUE CODES (use these exactly):
- AI_ErrReadingOrderMismatch: Content appears in different order visually vs DOM (screen readers read wrong order)
- AI_ErrVisualGroupingBroken: Related content appears grouped visually but is separated in DOM
- AI_WarnPossibleReadingOrderIssue: Visual order MAY differ from DOM order but it is ambiguous and needs human review (WARNING)
- AI_InfoContentOrder: Note about the content/reading order worth surfacing for manual confirmation (INFO)
- AI_InfoVisualCue: Information conveyed by a purely visual cue (position, proximity, colour) that may not be in the DOM (INFO)

Return ONLY valid JSON:
{
    "reading_order_matches": true/false,
    "issues": [
        {
            "err": "AI_ErrReadingOrderMismatch",
            "type": "err",
            "visual_first": "What appears first visually",
            "dom_first": "What comes first in DOM",
            "element_tag": "tag of misplaced element",
            "element_class": "class if present",
            "element_id": "id if present",
            "description": "Specific description of the mismatch"
        }
    ],
    "summary": "Brief summary"
}

IMPORTANT: Only report SIGNIFICANT mismatches that affect comprehension. Minor reordering within a section is usually fine. Set "type" to "warn" for AI_Warn* codes and "info" for AI_Info* codes; otherwise "err"."""

        try:
            result: dict[str, Any] = await self.client.analyze_with_image_and_html(
                screenshot, html, prompt
            )
            if 'issues' not in result:
                result['issues'] = []
            return result
        except Exception as e:
            logger.error(f"Reading order analysis failed: {e}")
            return {'error': str(e), 'issues': []}


class ModalAnalyzer:
    """Analyzes modal dialogs and overlays"""

    def __init__(self, client: ClaudeClient) -> None:
        self.client: ClaudeClient = client

    async def analyze(self, screenshot: bytes, html: str) -> dict[str, Any]:
        """
        Detect and analyze modal dialogs

        Args:
            screenshot: Page screenshot
            html: Page HTML

        Returns:
            Modal analysis with specific issue codes
        """
        prompt = """Analyze any modal dialogs, popups, or overlays visible in this page.

ISSUE CODES (use these exactly):
- AI_ErrDialogMissingRole: Modal/dialog visible but lacks role="dialog" or role="alertdialog"
- AI_ErrDialogMissingLabel: Dialog has role but no aria-label or aria-labelledby
- AI_ErrDialogNoCloseButton: Modal has no visible close mechanism (button, X, etc.)
- AI_WarnDialogBackgroundNotInert: Content behind modal appears still interactive (not properly disabled)
- AI_ErrModalWithoutARIA: A modal overlay with no dialog ARIA at all (no role, no aria-modal, no label)
- AI_ErrDialogWithoutARIA: An element that looks like a dialog/modal but lacks the required ARIA markup
- AI_ErrModalFocusTrap: Modal is open but focus is not trapped inside it (tabbing reaches the page behind)
- AI_WarnModalMissingLabel: Modal likely needs an accessible name and none is evident (WARNING)
- AI_WarnModalWithoutFocusTrap: Focus trapping cannot be confirmed and needs manual review (WARNING)

Return ONLY valid JSON:
{
    "modals_found": true/false,
    "modals": [
        {
            "has_role": true/false,
            "has_label": true/false,
            "has_close": true/false,
            "element_tag": "div/dialog/etc",
            "element_class": "modal class",
            "element_id": "modal id"
        }
    ],
    "issues": [
        {
            "err": "AI_ErrDialogMissingRole",
            "type": "err",
            "element_tag": "div",
            "element_class": "modal-class",
            "element_id": "modal-id",
            "description": "Visible modal dialog lacks role='dialog'"
        }
    ]
}

IMPORTANT: Only analyze modals that are CURRENTLY VISIBLE in the screenshot. Set "type" to "warn" for AI_Warn* codes, otherwise "err"."""

        try:
            result: dict[str, Any] = await self.client.analyze_with_image_and_html(
                screenshot, html, prompt
            )
            if 'issues' not in result:
                result['issues'] = []
            return result
        except Exception as e:
            logger.error(f"Modal analysis failed: {e}")
            return {'error': str(e), 'modals_found': False, 'issues': []}


class LanguageAnalyzer:
    """Analyzes language declarations and changes"""

    def __init__(self, client: ClaudeClient) -> None:
        self.client: ClaudeClient = client

    async def analyze(self, screenshot: bytes, html: str) -> dict[str, Any]:
        """
        Detect language usage and proper markup

        Args:
            screenshot: Page screenshot
            html: Page HTML

        Returns:
            Language analysis with specific issue codes
        """
        # First check HTML for lang attribute
        soup = BeautifulSoup(html, 'html.parser')
        html_tag = soup.find('html')
        html_lang: str | list[str] | None = html_tag.get('lang') if isinstance(html_tag, Tag) else None
        # Normalise to str | None (bs4 .get() can return list for multi-valued attrs)
        lang_str: str | None = html_lang if isinstance(html_lang, str) else None

        prompt = f"""Analyze language usage in this web page screenshot and HTML.

The HTML tag has lang="{lang_str or 'not set'}".

Look for text in DIFFERENT languages than the page's primary language ({lang_str or 'unknown'}).
Use the HTML to find the EXACT text - do NOT guess or paraphrase text.

ISSUE CODES (use these exactly):
- AI_ErrPageLanguageMissing: No lang attribute on <html> element (only if html_lang is missing)
- AI_ErrPageLanguageWrong: Page lang attribute doesn't match visible content language
- AI_ErrForeignTextUnmarked: Foreign language text found without lang attribute (ERROR - screen readers will mispronounce)
- AI_WarnMixedLanguage: Page mixes languages and some passages may need their own lang attribute - needs review (WARNING, set "type":"warn")

For each foreign text found:
1. Extract the EXACT text from the HTML
2. Find the element's class or id attribute
3. If no class/id, find the nearest parent section/div with a class

Return ONLY valid JSON:
{{
    "detected_language": "en/fr/es/de/etc",
    "html_lang": "{lang_str or 'missing'}",
    "language_matches": true/false,
    "foreign_content": [
        {{
            "text_sample": "EXACT text from HTML",
            "detected_language": "language code",
            "has_lang_attr": true/false
        }}
    ],
    "issues": [
        {{
            "err": "AI_ErrForeignTextUnmarked",
            "type": "err",
            "text_sample": "EXACT foreign text from HTML",
            "detected_language": "de",
            "element_tag": "p/span/div/h2/section/a",
            "element_class": "class-from-element-or-parent",
            "element_id": "id-if-exists-or-null",
            "parent_class": "class-of-nearest-parent-with-class",
            "description": "German text found without lang='de' attribute"
        }}
    ]
}}

CRITICAL:
- Use the EXACT text from the HTML. Do NOT paraphrase or translate the text.
- Always try to find a class or id attribute for element identification."""

        try:
            result: dict[str, Any] = await self.client.analyze_with_image_and_html(screenshot, html, prompt)
            if 'issues' not in result:
                result['issues'] = []
            return result
        except Exception as e:
            logger.error(f"Language analysis failed: {e}")
            return {'error': str(e), 'issues': []}


class AnimationAnalyzer:
    """Analyzes animations and motion"""

    def __init__(self, client: ClaudeClient) -> None:
        self.client: ClaudeClient = client

    async def analyze(self, html: str) -> dict[str, Any]:
        """
        Detect animations and motion in HTML/CSS

        Args:
            html: Page HTML including styles

        Returns:
            Animation analysis with specific issue codes
        """
        prompt = """Analyze this HTML for animations, transitions, and motion.

ISSUE CODES (use these exactly):
- AI_ErrInfiniteAnimationNoPause: Infinite CSS animation without pause control
- AI_ErrAutoPlayingMedia: Auto-playing video/audio without controls
- AI_WarnNoReducedMotion: Animations don't respect prefers-reduced-motion
- AI_WarnPotentialFlashing: Animation may cause flashing (seizure risk)
- AI_ErrFlashingContent: Content flashes more than 3 times per second (seizure risk, WCAG 2.3.1)
- AI_ErrMotionWithoutControl: Continuous/large motion (auto-scroll, parallax, marquee) with no mechanism to pause, stop, or hide it

Return ONLY valid JSON:
{
    "has_animations": true/false,
    "respects_reduced_motion": true/false,
    "issues": [
        {
            "err": "AI_ErrInfiniteAnimationNoPause",
            "type": "err",
            "element_tag": "div",
            "element_class": "animated-element",
            "animation_name": "spin",
            "description": "Infinite animation 'spin' has no pause control"
        }
    ]
}

IMPORTANT: Only flag animations that are CLEARLY problematic. Brief hover transitions are fine."""

        try:
            result: dict[str, Any] = await self.client.analyze_html(html, prompt)
            if 'issues' not in result:
                result['issues'] = []
            return result
        except Exception as e:
            logger.error(f"Animation analysis failed: {e}")
            return {'error': str(e), 'has_animations': False, 'issues': []}


class InteractiveAnalyzer:
    """Analyzes interactive elements for keyboard accessibility"""

    def __init__(self, client: ClaudeClient) -> None:
        self.client: ClaudeClient = client

    async def analyze(self, screenshot: bytes, html: str) -> dict[str, Any]:
        """
        Analyze interactive elements

        Args:
            screenshot: Page screenshot
            html: Page HTML

        Returns:
            Interactive element analysis with specific issue codes
        """
        prompt = """Analyze interactive elements in this web page for keyboard accessibility.

Look for elements that APPEAR clickable/interactive but may not be properly accessible.

ISSUE CODES (use these exactly):
- AI_ErrNonSemanticButton: Element looks like a button but uses <div>/<span> with onclick instead of <button>
- AI_ErrNonSemanticLink: Element looks like a link but uses <div>/<span> instead of <a>
- AI_ErrCustomControlNoARIA: Custom widget (tabs, accordion, dropdown) lacks proper ARIA roles/states
- AI_ErrClickableNotFocusable: Element has click handler but no tabindex (not keyboard accessible)
- AI_WarnFocusIndicatorWeak: Focus indicator appears too subtle or missing
- AI_ErrMissingFocusIndicator: Interactive element shows NO visible focus indicator at all (outline removed with no replacement)

Return ONLY valid JSON:
{
    "interactive_elements_found": true/false,
    "issues": [
        {
            "err": "AI_ErrNonSemanticButton",
            "type": "err",
            "element_tag": "div",
            "element_class": "btn custom-button",
            "element_id": "submit-btn",
            "element_text": "Submit",
            "description": "Div styled as button with onclick - should use <button>"
        }
    ]
}

IMPORTANT:
- Only report elements that are CLEARLY meant to be interactive
- Don't flag <div> with click if it also has proper role="button" and tabindex
- Focus on HIGH-CONFIDENCE issues only"""

        try:
            result: dict[str, Any] = await self.client.analyze_with_image_and_html(
                screenshot, html, prompt
            )
            if 'issues' not in result:
                result['issues'] = []
            return result
        except Exception as e:
            logger.error(f"Interactive analysis failed: {e}")
            return {'error': str(e), 'interactive_elements_found': False, 'issues': []}


class WidgetARIAAnalyzer:
    """Detects custom UI widgets that lack the ARIA roles/states their pattern requires."""

    def __init__(self, client: ClaudeClient) -> None:
        self.client: ClaudeClient = client

    async def analyze(self, screenshot: bytes, html: str) -> dict[str, Any]:
        """Identify visually-recognisable widgets implemented without proper ARIA.

        Args:
            screenshot: Page screenshot
            html: Page HTML

        Returns:
            Analysis results with specific issue codes
        """
        prompt = """Analyze this page for custom interactive WIDGETS that are missing the ARIA
roles, states, and properties their design pattern requires. Use the screenshot to recognise the
widget visually and the HTML to confirm the markup is missing the required ARIA.

Only flag a widget when it VISUALLY presents as the pattern but the HTML lacks the required ARIA
(or a native element that would supply it). If the correct role/state is already present, do NOT report it.

ISSUE CODES (use the one that matches the widget; report at the widget's root element):
- AI_ErrTabsWithoutARIA: Tabbed interface without role="tablist"/"tab"/"tabpanel" + aria-selected/aria-controls
- AI_ErrToggleWithoutARIA: On/off switch/toggle without role="switch" (or checkbox) semantics
- AI_ErrToggleWithoutState: Toggle/switch has a role but no aria-checked/aria-pressed reflecting its state
- AI_ErrDisclosureWithoutARIA: Show/hide disclosure trigger without aria-expanded + aria-controls
- AI_ErrDatePickerWithoutARIA: Date picker without grid/dialog roles and labelled, operable controls
- AI_ErrCheckboxGroupWithoutARIA: Group of checkboxes without a group/fieldset and accessible group name
- AI_ErrRadioGroupWithoutARIA: Custom radio group without role="radiogroup"/"radio" + aria-checked
- AI_ErrCardWithoutARIA: Interactive card (whole-card click target) without a clear role/name for the action
- AI_ErrBreadcrumbsWithoutARIA: Breadcrumb trail without nav + aria-label and aria-current on the current item
- AI_ErrAutocompleteWithoutARIA: Autocomplete/combobox without role="combobox" + aria-expanded/aria-controls/aria-activedescendant
- AI_ErrFeedWithoutARIA: Infinite/streaming feed without role="feed" and articles with aria-posinset/aria-setsize
- AI_ErrSliderWithoutARIA: Custom slider without role="slider" + aria-valuenow/valuemin/valuemax
- AI_ErrSpinbuttonWithoutARIA: Number spinner without role="spinbutton" + aria-valuenow/valuemin/valuemax
- AI_ErrMeterWithoutARIA: Meter/gauge without role="meter" (or <meter>) + aria-valuenow/valuemin/valuemax
- AI_ErrProgressBarWithoutARIA: Progress indicator without role="progressbar" (or <progress>) + aria-valuenow
- AI_ErrTreeViewWithoutARIA: Tree view without role="tree"/"treeitem" + aria-expanded/aria-selected
- AI_ErrPaginationWithoutARIA: Pagination without nav + aria-label and aria-current on the current page
- AI_ErrSearchWithoutARIA: Search region without role="search" (or <search>) and a labelled search input
- AI_WarnSearchRoleOnForm: A form is a search form but uses role="search" on the wrong element / redundantly

Return ONLY valid JSON:
{
    "issues": [
        {
            "err": "AI_ErrTabsWithoutARIA",
            "type": "err",
            "element_tag": "div",
            "element_class": "tabs",
            "element_id": "id-or-null",
            "element_text": "short label of the widget",
            "description": "Tabbed interface lacks role=tablist/tab/tabpanel and aria-selected"
        }
    ],
    "summary": "Brief summary"
}

IMPORTANT: type is "warn" for AI_Warn* codes, otherwise "err". Report HIGH-CONFIDENCE issues only."""

        try:
            result: dict[str, Any] = await self.client.analyze_with_image_and_html(
                screenshot, html, prompt
            )
            if 'issues' not in result:
                result['issues'] = []
            return result
        except Exception as e:
            logger.error(f"Widget ARIA analysis failed: {e}")
            return {'error': str(e), 'issues': []}


class LandmarkAIAnalyzer:
    """Detects redundant ARIA roles on native landmarks and unlabelled duplicate landmarks."""

    def __init__(self, client: ClaudeClient) -> None:
        self.client: ClaudeClient = client

    async def analyze(self, screenshot: bytes, html: str) -> dict[str, Any]:
        """Find redundant landmark roles and landmarks that need a distinguishing label.

        Args:
            screenshot: Page screenshot
            html: Page HTML

        Returns:
            Analysis results with specific issue codes
        """
        prompt = """Analyze the landmark structure of this page using the HTML (and the screenshot
for layout). Report two kinds of issue.

REDUNDANT ROLE ON A NATIVE LANDMARK — a native element already has an implicit landmark role, so an
explicit matching role is redundant (a warning, not an error). Report on the element carrying the role:
- AI_WarnBannerRoleOnHeader: role="banner" on a top-level <header>
- AI_WarnComplementaryRoleOnAside: role="complementary" on an <aside>
- AI_WarnContentinfoRoleOnFooter: role="contentinfo" on a top-level <footer>
- AI_WarnFormRoleOnForm: role="form" on a <form>
- AI_WarnMainRoleOnMain: role="main" on a <main>
- AI_WarnNavigationRoleOnNav: role="navigation" on a <nav>

MISSING DISTINGUISHING LABEL:
- AI_ErrLandmarkWithoutLabel: Multiple landmarks of the same type with no aria-label/aria-labelledby to tell them apart
- AI_WarnRegionWithoutLabel: An element with role="region" (or <section> acting as a region) that has no accessible name

Return ONLY valid JSON:
{
    "issues": [
        {
            "err": "AI_WarnBannerRoleOnHeader",
            "type": "warn",
            "element_tag": "header",
            "element_class": "site-header",
            "element_id": "id-or-null",
            "description": "role=banner is redundant on a top-level <header>"
        }
    ],
    "summary": "Brief summary"
}

IMPORTANT: type is "warn" for AI_Warn* codes, "err" for AI_Err* codes. Only report clear cases."""

        try:
            result: dict[str, Any] = await self.client.analyze_with_image_and_html(
                screenshot, html, prompt
            )
            if 'issues' not in result:
                result['issues'] = []
            return result
        except Exception as e:
            logger.error(f"Landmark AI analysis failed: {e}")
            return {'error': str(e), 'issues': []}


class MediaAnalyzer:
    """Detects video/audio media that lacks captions, transcripts, or autoplay controls."""

    def __init__(self, client: ClaudeClient) -> None:
        self.client: ClaudeClient = client

    async def analyze(self, screenshot: bytes, html: str) -> dict[str, Any]:
        """Find media elements missing required alternatives.

        Args:
            screenshot: Page screenshot
            html: Page HTML

        Returns:
            Analysis results with specific issue codes
        """
        prompt = """Analyze audio and video media on this page using the HTML (and screenshot for
context). Look at <video>, <audio>, and embedded players (iframes from video/audio providers).

ISSUE CODES (use these exactly; report at the media element):
- AI_ErrVideoWithoutCaptions: Video with speech/audio content and no captions track (<track kind="captions">) — ERROR
- AI_WarnVideoWithoutCaptions: Video where captions cannot be confirmed and need manual review — WARNING
- AI_WarnVideoWithoutTranscript: Video without a text transcript available near it — WARNING
- AI_ErrAudioWithoutTranscript: Audio content (podcast, recording) with no text transcript — ERROR
- AI_ErrAutoplayMedia: Audio or video that autoplays without an obvious pause/stop/mute control — ERROR

Return ONLY valid JSON:
{
    "issues": [
        {
            "err": "AI_ErrVideoWithoutCaptions",
            "type": "err",
            "element_tag": "video",
            "element_class": "class-or-null",
            "element_id": "id-or-null",
            "description": "Video has spoken content but no captions track"
        }
    ],
    "summary": "Brief summary"
}

IMPORTANT: type is "warn" for AI_Warn* codes, otherwise "err". Decorative/muted background video without speech is not an error."""

        try:
            result: dict[str, Any] = await self.client.analyze_with_image_and_html(
                screenshot, html, prompt
            )
            if 'issues' not in result:
                result['issues'] = []
            return result
        except Exception as e:
            logger.error(f"Media analysis failed: {e}")
            return {'error': str(e), 'issues': []}


class LiveRegionAnalyzer:
    """Detects dynamic content (alerts, notifications, status, errors) that is not announced."""

    def __init__(self, client: ClaudeClient) -> None:
        self.client: ClaudeClient = client

    async def analyze(self, screenshot: bytes, html: str) -> dict[str, Any]:
        """Find dynamic UI that updates without an appropriate live region/role.

        Args:
            screenshot: Page screenshot
            html: Page HTML

        Returns:
            Analysis results with specific issue codes
        """
        prompt = """Analyze this page for DYNAMIC content that updates without being announced to
screen readers. Use the screenshot to recognise the component and the HTML to confirm the live
region / role is missing.

ISSUE CODES (use these exactly; report at the component's container):
- AI_ErrAlertWithoutARIA: An alert/error banner that should interrupt the user but lacks role="alert"
- AI_ErrNotificationWithoutARIA: A toast/notification without role="status"/"alert" or aria-live
- AI_ErrLoadingStateNotAnnounced: A loading/spinner/progress state with no aria-live/role="status" announcement
- AI_ErrMissingLiveRegion: Content that changes dynamically (results, counters, chat) with no live region at all
- AI_ErrFormErrorNotAnnounced: Form validation errors shown visually but not tied to fields / not in a live region

Return ONLY valid JSON:
{
    "issues": [
        {
            "err": "AI_ErrNotificationWithoutARIA",
            "type": "err",
            "element_tag": "div",
            "element_class": "toast",
            "element_id": "id-or-null",
            "description": "Toast notification has no role=status/alert or aria-live"
        }
    ],
    "summary": "Brief summary"
}

IMPORTANT: type is "err" for all of these. Only report components that clearly update dynamically."""

        try:
            result: dict[str, Any] = await self.client.analyze_with_image_and_html(
                screenshot, html, prompt
            )
            if 'issues' not in result:
                result['issues'] = []
            return result
        except Exception as e:
            logger.error(f"Live region analysis failed: {e}")
            return {'error': str(e), 'issues': []}


class StructuralAnalyzer:
    """Detects page-level structural gaps: missing skip link, time limits, complex tables."""

    def __init__(self, client: ClaudeClient) -> None:
        self.client: ClaudeClient = client

    async def analyze(self, screenshot: bytes, html: str) -> dict[str, Any]:
        """Find page-structure issues that need visual + structural judgement.

        Args:
            screenshot: Page screenshot
            html: Page HTML

        Returns:
            Analysis results with specific issue codes
        """
        prompt = """Analyze this page for structural accessibility gaps that need both the rendered
view and the HTML to judge.

ISSUE CODES (use these exactly):
- AI_ErrMissingSkipLink: Page has repeated navigation before the main content but no "skip to main content" link as the first focusable element
- AI_ErrTimeLimitNoWarning: A countdown / session time limit is visible with no way to turn off, adjust, or extend it
- AI_WarnTableWithComplexStructure: A data table has a complex structure (multi-level/merged headers, irregular spans) that likely needs scope/id+headers associations a screen reader can follow — needs review

Return ONLY valid JSON:
{
    "issues": [
        {
            "err": "AI_ErrMissingSkipLink",
            "type": "err",
            "element_tag": "body",
            "element_class": "class-or-null",
            "element_id": "id-or-null",
            "description": "Repeated nav precedes main content with no skip link"
        }
    ],
    "summary": "Brief summary"
}

IMPORTANT: type is "warn" for AI_Warn* codes, otherwise "err". Only report clear cases."""

        try:
            result: dict[str, Any] = await self.client.analyze_with_image_and_html(
                screenshot, html, prompt
            )
            if 'issues' not in result:
                result['issues'] = []
            return result
        except Exception as e:
            logger.error(f"Structural analysis failed: {e}")
            return {'error': str(e), 'issues': []}
