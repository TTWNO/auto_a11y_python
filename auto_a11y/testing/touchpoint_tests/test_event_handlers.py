"""
Event Handlers touchpoint test module
Analyzes page for event handling accessibility issues and keyboard navigation patterns.
"""

from __future__ import annotations

from typing import Any
import logging
import re

from playwright.async_api import Page

logger = logging.getLogger(__name__)

# Common CSS named colors used in focus-indicator styling. Anything not listed
# (and not rgb()/rgba()/hex) falls back to opaque black -- see _parse_color.
_NAMED_COLORS: dict[str, tuple[int, int, int]] = {
    'black': (0, 0, 0),
    'white': (255, 255, 255),
    'red': (255, 0, 0),
    'green': (0, 128, 0),
    'lime': (0, 255, 0),
    'blue': (0, 0, 255),
    'yellow': (255, 255, 0),
    'cyan': (0, 255, 255),
    'aqua': (0, 255, 255),
    'magenta': (255, 0, 255),
    'fuchsia': (255, 0, 255),
    'silver': (192, 192, 192),
    'gray': (128, 128, 128),
    'grey': (128, 128, 128),
    'maroon': (128, 0, 0),
    'olive': (128, 128, 0),
    'purple': (128, 0, 128),
    'teal': (0, 128, 128),
    'navy': (0, 0, 128),
    'orange': (255, 165, 0),
}

# CSS length: leading number (int/float, optional sign) followed by an optional
# unit (px/em/rem/etc.). We only need the numeric magnitude for width/offset
# comparisons, so the unit itself is discarded.
_LENGTH_RE = re.compile(r'^\s*([+-]?\d*\.?\d+)')


def _parse_px(value: str | None) -> float:
    """Parse the leading numeric magnitude from a CSS length string.

    Handles px/em/rem and bare numbers. The unit is intentionally ignored --
    callers compare relative widths/offsets, not absolute pixels. Returns 0 for
    empty/None/unparseable input.
    """
    if not value:
        return 0
    match = _LENGTH_RE.match(value)
    if not match:
        return 0
    try:
        return float(match.group(1))
    except ValueError:
        return 0


def _parse_color(color_str: str | None) -> dict[str, float]:
    """Parse a CSS color string to an RGBA dict.

    Supports rgb()/rgba(), 6-digit and 3-digit hex, and a set of common named
    colors. ``transparent``/``initial`` map to fully transparent black. Truly
    unknown input falls back to opaque black ({r:0,g:0,b:0,a:1}).
    """
    if not color_str or color_str == 'transparent' or color_str == 'initial':
        return {'r': 0, 'g': 0, 'b': 0, 'a': 0}

    color_str = color_str.strip()

    match = re.match(r'rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)', color_str)
    if match:
        return {
            'r': int(match.group(1)),
            'g': int(match.group(2)),
            'b': int(match.group(3)),
            'a': float(match.group(4)) if match.group(4) else 1.0
        }

    match = re.match(r'#([0-9a-fA-F]{6})$', color_str)
    if match:
        hex_val = match.group(1)
        return {
            'r': int(hex_val[0:2], 16),
            'g': int(hex_val[2:4], 16),
            'b': int(hex_val[4:6], 16),
            'a': 1.0
        }

    # 3-digit shorthand hex: #abc -> #aabbcc
    match = re.match(r'#([0-9a-fA-F]{3})$', color_str)
    if match:
        hex_val = match.group(1)
        return {
            'r': int(hex_val[0] * 2, 16),
            'g': int(hex_val[1] * 2, 16),
            'b': int(hex_val[2] * 2, 16),
            'a': 1.0
        }

    named = _NAMED_COLORS.get(color_str.lower())
    if named is not None:
        return {'r': named[0], 'g': named[1], 'b': named[2], 'a': 1.0}

    # Unknown color: fall back to opaque black.
    return {'r': 0, 'g': 0, 'b': 0, 'a': 1}

TEST_DOCUMENTATION = {
    "testName": "Event Handler Accessibility Tests",
    "touchpoint": "event_handlers",
    "description": "Analyzes page for event handling accessibility issues and keyboard navigation patterns. Checks for proper implementation of keyboard access for interactive elements, correct tab order, and escape key functionality for modal dialogs.",
    "version": "1.0.0",
    "wcagCriteria": ["2.1.1", "2.1.2", "2.1.3", "2.4.3"],
    "tests": [
        {
            "id": "missing-tabindex",
            "name": "Non-interactive Elements with Event Handlers Missing Tabindex",
            "description": "Tests for non-interactive elements (div, span, etc.) that have event handlers but no tabindex attribute. These elements must have tabindex to be keyboard accessible.",
            "impact": "critical",
            "wcagCriteria": ["2.1.1", "2.1.3"],
        },
        {
            "id": "mouse-handler-keyboard-check",
            "name": "Mouse handler keyboard equivalence (three-state)",
            "description": "For each element with a mouse handler that is not intrinsically interactive: pass if the element has its own keyboard handler; warn if a focusable ancestor has a keyboard handler (manual verification needed); fail if no keyboard handler exists on the element or any focusable ancestor.",
            "impact": "high",
            "wcagCriteria": ["2.1.1"],
        },
        {
            "id": "global-keyboard-handler-discovery",
            "name": "Global Keyboard Handler Discovery",
            "description": "Detects keyboard handlers attached to document/window/body. Emits a warning because automated testing cannot tie a global handler to any specific mouse-driven widget; manual verification required.",
            "impact": "medium",
            "wcagCriteria": ["2.1.1"],
        },
        {
            "id": "modal-without-escape",
            "name": "Modal Dialogs without Keyboard Escape",
            "description": "Checks if modal dialogs provide keyboard escape functionality (ESC key)",
            "impact": "high",
            "wcagCriteria": ["2.1.2"],
        },
        {
            "id": "visual-order",
            "name": "Tab Order Doesn't Follow Visual Layout",
            "description": "Checks if the tab order of interactive elements follows their visual arrangement",
            "impact": "medium", 
            "wcagCriteria": ["2.4.3"],
        },
        {
            "id": "negative-tabindex",
            "name": "Elements with Negative Tabindex",
            "description": "Identifies elements using negative tabindex which removes them from the natural tab order but keeps them focusable programmatically",
            "impact": "medium",
            "wcagCriteria": ["2.4.3"],
        },
        {
            "id": "high-tabindex",
            "name": "Elements with Unusually High Tabindex",
            "description": "Identifies elements with tabindex values > 10, which is a poor practice that can create maintenance issues",
            "impact": "low",
            "wcagCriteria": ["2.4.3"],
        }
    ]
}

async def test_event_handlers(page: Page) -> dict[str, Any]:
    """
    Test event handlers and tab order accessibility requirements
    
    Args:
        page: Playwright Page object
        
    Returns:
        Dictionary containing test results with errors and warnings
    """
    try:
        # Execute JavaScript to analyze event handlers
        results: dict[str, Any] = await page.evaluate(r'''
            () => {
                const results = {
                    applicable: true,
                    errors: [],
                    warnings: [],
                    discovery: [],
                    passes: [],
                    elements_tested: 0,
                    elements_passed: 0,
                    elements_failed: 0,
                    test_name: 'event_handlers',
                    checks: []
                };
                
                // Function to generate XPath for elements
                function getFullXPath(element) {
                    if (!element) return '';
                    
                    function getElementIdx(el) {
                        let count = 1;
                        for (let sib = el.previousSibling; sib; sib = sib.previousSibling) {
                            if (sib.nodeType === 1 && sib.tagName === el.tagName) {
                                count++;
                            }
                        }
                        return count;
                    }
                    
                    let path = '';
                    while (element && element.nodeType === 1) {
                        const idx = getElementIdx(element);
                        const tagName = element.tagName.toLowerCase();
                        path = `/${tagName}[${idx}]${path}`;
                        element = element.parentNode;
                    }
                    return path;
                }
                
                // Check if element is intrinsically interactive
                function isIntrinsicInteractive(element) {
                    const interactiveTags = ['a', 'button', 'input', 'select', 'textarea', 'details', 'summary'];
                    const interactiveRoles = ['button', 'link', 'menuitem', 'tab', 'checkbox', 'radio', 'switch'];
                    
                    return interactiveTags.includes(element.tagName.toLowerCase()) ||
                           (element.getAttribute('role') && 
                            interactiveRoles.includes(element.getAttribute('role')));
                }
                
                // Find all focusable elements
                const focusableElements = Array.from(document.querySelectorAll(
                    'a, button, input, select, textarea, [tabindex], [contentEditable=true], audio[controls], video[controls]'
                )).filter(el => {
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none' && style.visibility !== 'hidden';
                });
                
                results.elements_tested = focusableElements.length;

                // Check tab order (only if there are focusable elements)
                let tabOrderViolations = 0;
                let previousRect = null;
                
                focusableElements.forEach((element, index) => {
                    const rect = element.getBoundingClientRect();
                    const tabindex = element.getAttribute('tabindex');
                    const tabindexValue = tabindex ? parseInt(tabindex) : 0;
                    
                    // Check for negative tabindex
                    // Only warn for interactive elements: removing an interactive control from the
                    // tab order makes it keyboard-inaccessible. A non-interactive container (e.g. a
                    // <div> or <section> with tabindex="-1" for programmatic focus, such as a modal
                    // target or skip-link destination) is a correct, recommended pattern and must
                    // not be flagged.
                    if (tabindexValue < 0 && isIntrinsicInteractive(element)) {
                        results.warnings.push({
                            err: 'WarnNegativeTabindex',
                            type: 'warn',
                            cat: 'event_handling',
                            element: element.tagName,
                            xpath: getFullXPath(element),
                            html: element.outerHTML.substring(0, 200),
                            description: 'Element has negative tabindex, removing it from tab order',
                            tabindex: tabindexValue
                        });
                    }
                    
                    // Check for high tabindex values
                    if (tabindexValue > 10) {
                        results.warnings.push({
                            err: 'WarnHighTabindex',
                            type: 'warn',
                            cat: 'event_handling',
                            element: element.tagName,
                            xpath: getFullXPath(element),
                            html: element.outerHTML.substring(0, 200),
                            description: `Element has unusually high tabindex (${tabindexValue}), which may cause navigation issues`,
                            tabindex: tabindexValue
                        });
                    }
                    
                    // Check visual tab order
                    if (previousRect && index > 0) {
                        const previousElement = focusableElements[index - 1];
                        const previousElementRect = previousElement.getBoundingClientRect();

                        // Calculate vertical alignment
                        const verticalDiff = Math.abs(rect.top - previousElementRect.top);
                        const clearlyDifferentRows = verticalDiff > 10;
                        const definitelySameRow = verticalDiff <= 5;
                        const ambiguousOverlap = verticalDiff > 5 && verticalDiff <= 10;

                        // Only check left/right position if there's potential for same-row issue
                        if (!clearlyDifferentRows && rect.left < previousElementRect.left - 50) {
                            // Get readable descriptions of both elements
                            const currentDesc = element.tagName.toLowerCase() +
                                (element.id ? `#${element.id}` : '') +
                                (element.textContent ? ` ("${element.textContent.trim().substring(0, 30)}")` : '');
                            const previousDesc = previousElement.tagName.toLowerCase() +
                                (previousElement.id ? `#${previousElement.id}` : '') +
                                (previousElement.textContent ? ` ("${previousElement.textContent.trim().substring(0, 30)}")` : '');

                            const sharedData = {
                                cat: 'event_handling',
                                element: element.tagName.toLowerCase(),
                                xpath: getFullXPath(element),
                                html: element.outerHTML.substring(0, 200),
                                currentElement: {
                                    tag: element.tagName.toLowerCase(),
                                    id: element.id || null,
                                    text: element.textContent.trim().substring(0, 50),
                                    position: { x: Math.round(rect.left), y: Math.round(rect.top) },
                                    tabIndex: index + 1
                                },
                                previousElement: {
                                    tag: previousElement.tagName.toLowerCase(),
                                    id: previousElement.id || null,
                                    text: previousElement.textContent.trim().substring(0, 50),
                                    html: previousElement.outerHTML.substring(0, 200),
                                    xpath: getFullXPath(previousElement),
                                    position: { x: Math.round(previousElementRect.left), y: Math.round(previousElementRect.top) },
                                    tabIndex: index
                                },
                                verticalDiff: Math.round(verticalDiff)
                            };

                            if (definitelySameRow) {
                                // Clear same-row violation - this is an ERROR
                                tabOrderViolations++;
                                results.errors.push({
                                    err: 'ErrTabOrderViolation',
                                    type: 'err',
                                    description: `Tab order diverges from visual layout: ${currentDesc} appears visually left of ${previousDesc} but comes after it in tab order`,
                                    ...sharedData
                                });
                                results.elements_failed++;
                            } else if (ambiguousOverlap) {
                                // Ambiguous overlap - this is a WARNING
                                results.warnings.push({
                                    err: 'WarnAmbiguousTabOrder',
                                    type: 'warn',
                                    description: `Possible tab order issue: ${currentDesc} appears left of ${previousDesc} but comes after it. Elements overlap vertically (${Math.round(verticalDiff)}px difference) - verify visual layout matches intended tab order`,
                                    ...sharedData
                                });
                            }
                        } else {
                            results.elements_passed++;
                        }
                    }
                    
                    previousRect = rect;
                });

                // Positive-tabindex tab-order check (WCAG 2.4.3 Focus Order).
                // A positive tabindex value forces an element to the front of the tab sequence in
                // ascending tabindex order, ahead of every tabindex=0/implicit element regardless of
                // where it sits in the document. This almost always makes the keyboard focus order
                // diverge from the visual reading order. We compare the order produced by the
                // positive tabindex values against those same elements' document order; if they
                // disagree, the focus order does not match the visual/DOM order and we flag it.
                const positiveTabindexElements = focusableElements
                    .map((el, domIndex) => ({ el, domIndex, ti: parseInt(el.getAttribute('tabindex') || '0') }))
                    .filter(item => item.ti > 0);

                if (positiveTabindexElements.length > 0) {
                    // Order the positive-tabindex elements as the browser would visit them:
                    // ascending tabindex, ties broken by document order.
                    const tabSequence = positiveTabindexElements.slice().sort((a, b) => {
                        if (a.ti !== b.ti) return a.ti - b.ti;
                        return a.domIndex - b.domIndex;
                    });
                    // Their document order, for comparison.
                    const domSequence = positiveTabindexElements.slice().sort((a, b) => a.domIndex - b.domIndex);

                    for (let k = 0; k < tabSequence.length; k++) {
                        if (tabSequence[k].el !== domSequence[k].el) {
                            const el = tabSequence[k].el;
                            const desc = el.tagName.toLowerCase() +
                                (el.id ? `#${el.id}` : '') +
                                (el.textContent ? ` ("${el.textContent.trim().substring(0, 30)}")` : '');
                            tabOrderViolations++;
                            results.errors.push({
                                err: 'ErrTabOrderViolation',
                                type: 'err',
                                cat: 'event_handling',
                                element: el.tagName.toLowerCase(),
                                xpath: getFullXPath(el),
                                html: el.outerHTML.substring(0, 200),
                                description: `Tab order diverges from document/visual order: ${desc} has tabindex="${tabSequence[k].ti}", forcing a focus sequence that does not match the order in which elements appear on the page`,
                                tabindex: tabSequence[k].ti
                            });
                            results.elements_failed++;
                        }
                    }
                }

                // Check for modals without escape handlers
                // Collect inline JS and external script URLs for analysis
                let inlineJsCode = '';
                const externalScriptUrls = [];
                const allScripts = Array.from(document.querySelectorAll('script'));
                allScripts.forEach(script => {
                    if (script.src) {
                        externalScriptUrls.push(script.src);
                    } else if (script.textContent) {
                        inlineJsCode += script.textContent + '\\n';
                    }
                });
                
                // Store for Python to fetch external scripts and complete the check
                results._inlineJsCode = inlineJsCode;
                results._externalScriptUrls = externalScriptUrls;
                
                // Placeholder - will be recalculated in Python after fetching external scripts
                let pageHasEscapeHandler = false;

                // Collect modal info for Python to check after fetching external scripts
                const modals = Array.from(document.querySelectorAll('dialog, [role="dialog"], [class*="modal"]'))
                    .filter(modal => {
                        // Exclude nested modal content containers - only check outermost modal
                        const hasModalAncestor = Array.from(document.querySelectorAll('dialog, [role="dialog"], [class*="modal"]'))
                            .some(otherModal => otherModal !== modal && otherModal.contains(modal));
                        return !hasModalAncestor;
                    });

                results._modals = modals.map(modal => {
                    const onkeydown = modal.getAttribute('onkeydown');
                    const hasInlineEscapeHandler = onkeydown &&
                                           (onkeydown.includes('Escape') ||
                                            onkeydown.includes('Esc') ||
                                            onkeydown.includes('27'));
                    return {
                        tagName: modal.tagName,
                        xpath: getFullXPath(modal),
                        html: modal.outerHTML.substring(0, 200),
                        hasInlineEscapeHandler: hasInlineEscapeHandler
                    };
                });
                
                // Add check information for reporting
                results.checks.push({
                    description: 'Interactive elements accessibility',
                    wcag: ['2.1.1', '2.1.3'],
                    total: focusableElements.length,
                    passed: results.elements_passed,
                    failed: results.elements_failed
                });
                
                if (tabOrderViolations > 0) {
                    results.checks.push({
                        description: 'Tab order violations',
                        wcag: ['2.4.3'],
                        total: focusableElements.length,
                        passed: focusableElements.length - tabOrderViolations,
                        failed: tabOrderViolations
                    });
                }

                // DISCOVERY: Report each script element and inline event handler individually
                const scriptElements = Array.from(document.querySelectorAll('script[src], script:not([src])'));
                scriptElements.forEach(script => {
                    const src = script.getAttribute('src');
                    const isInline = !src;
                    const scriptContent = isInline ? script.textContent.substring(0, 100) : '';

                    results.warnings.push({
                        err: 'DiscoFoundJS',
                        type: 'disco',
                        cat: 'event_handling',
                        element: 'script',
                        xpath: getFullXPath(script),
                        html: script.outerHTML.substring(0, 200),
                        description: isInline
                            ? `Inline <script> tag detected - ensure progressive enhancement and that functionality works without JavaScript`
                            : `External script "${src}" detected - ensure progressive enhancement and that functionality works without JavaScript`,
                        scriptType: isInline ? 'inline' : 'external',
                        src: src || null
                    });
                });

                // DISCOVERY: Report elements with inline event handler attributes
                const elementsWithHandlers = Array.from(document.querySelectorAll('*'))
                    .filter(el => Array.from(el.attributes).some(attr => attr.name.startsWith('on')));

                elementsWithHandlers.forEach(element => {
                    const handlers = Array.from(element.attributes)
                        .filter(attr => attr.name.startsWith('on'))
                        .map(attr => attr.name)
                        .join(', ');

                    results.warnings.push({
                        err: 'DiscoFoundJS',
                        type: 'disco',
                        cat: 'event_handling',
                        element: element.tagName.toLowerCase(),
                        xpath: getFullXPath(element),
                        html: element.outerHTML.substring(0, 200),
                        description: `Element has inline event handler attributes (${handlers}) - ensure keyboard accessibility and progressive enhancement`,
                        eventHandlers: handlers
                    });
                });

                return results;
            }
        ''')

        # Fetch external scripts and check for escape handlers
        import re
        import aiohttp
        
        all_js_code = results.get('_inlineJsCode', '')
        external_urls = results.get('_externalScriptUrls', [])
        
        # Fetch external scripts
        async with aiohttp.ClientSession() as session:
            for url in external_urls:
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                        if response.status == 200:
                            external_code = await response.text()
                            all_js_code += external_code + '\n'
                except Exception as e:
                    logger.debug(f"Could not fetch external script {url}: {e}")
        
        # Strip comments from combined JS code
        all_js_code = re.sub(r'//.*$', '', all_js_code, flags=re.MULTILINE)
        all_js_code = re.sub(r'/\*[\s\S]*?\*/', '', all_js_code)

        # Phase 2: handler map + ErrMissingTabindex + Test 1 + Test 2.
        # Receives combined inline + external JS text so detection sees handlers
        # registered from external scripts.
        phase2_results: dict[str, Any] = await page.evaluate(r'''
            (combinedScriptText) => {
                const out = {errors: [], warnings: [], elements_passed: 0, elements_failed: 0,
                             globalHandlers: {document: [], window: [], body: []}};

                function getFullXPath(element) {
                    if (!element) return '';
                    function getElementIdx(el) {
                        let count = 1;
                        for (let sib = el.previousSibling; sib; sib = sib.previousSibling) {
                            if (sib.nodeType === 1 && sib.tagName === el.tagName) count++;
                        }
                        return count;
                    }
                    let path = '';
                    while (element && element.nodeType === 1) {
                        const idx = getElementIdx(element);
                        path = `/${element.tagName.toLowerCase()}[${idx}]${path}`;
                        element = element.parentNode;
                    }
                    return path;
                }
                function isIntrinsicInteractive(element) {
                    const interactiveTags = ['button', 'input', 'select', 'textarea', 'details', 'summary'];
                    const interactiveRoles = ['button', 'link', 'menuitem', 'tab', 'checkbox', 'radio', 'switch'];
                    const tag = element.tagName.toLowerCase();
                    // An <a> without href is not focusable or keyboard-operable, so it
                    // is NOT intrinsically interactive (a fake button needing checks).
                    if (tag === 'a') return element.hasAttribute('href');
                    return interactiveTags.includes(tag) ||
                           (element.getAttribute('role') &&
                            interactiveRoles.includes(element.getAttribute('role')));
                }

                const MOUSE_EVENTS = ['click', 'mousedown', 'mouseup', 'mouseover', 'mouseout', 'dblclick', 'contextmenu'];
                const KEY_EVENTS = ['keydown', 'keyup', 'keypress'];

                // collectHandlerMap — Approach 3 hinge point.
                // Heuristic regex parsing of combined inline + external scripts. Limitations:
                //   - querySelectorAll handlers are ignored (ambiguous which element)
                //   - framework handlers (React, Vue, Svelte) are not detected
                //   - var reassignment: last assignment wins, scope is not modeled
                function collectHandlerMap(text) {
                    const elementHandlers = new Map();
                    const globalHandlers = {document: new Set(), window: new Set(), body: new Set()};
                    function ensureEntry(el) {
                        if (!elementHandlers.has(el)) {
                            elementHandlers.set(el, {mouseEvents: new Set(), keyEvents: new Set()});
                        }
                        return elementHandlers.get(el);
                    }
                    function recordEvent(target, eventType) {
                        const t = eventType.toLowerCase();
                        if (MOUSE_EVENTS.includes(t)) target.mouseEvents.add(t);
                        else if (KEY_EVENTS.includes(t)) target.keyEvents.add(t);
                    }

                    // 1. Inline on* attributes on every element.
                    Array.from(document.querySelectorAll('*')).forEach(el => {
                        const entry = ensureEntry(el);
                        Array.from(el.attributes).forEach(attr => {
                            if (!attr.name.startsWith('on')) return;
                            recordEvent(entry, attr.name.slice(2));
                        });
                        if (el === document.body) {
                            entry.keyEvents.forEach(e => globalHandlers.body.add(e));
                        }
                    });

                    // 2. Build var-to-element table.
                    // Reassignment policy: the regex scans left-to-right and each
                    // varTable.set() overwrites prior entries, implementing the
                    // spec's "last assignment wins, scope is not modeled" rule.
                    const varTable = new Map();
                    const declRe = /(?:const|let|var)\s+(\w+)\s*=\s*document\.(getElementById|querySelector)\s*\(\s*['"]([^'"]+)['"]\s*\)/g;
                    let m;
                    while ((m = declRe.exec(text)) !== null) {
                        const [, varName, fn, arg] = m;
                        let el = null;
                        if (fn === 'getElementById') {
                            el = document.getElementById(arg);
                        } else {
                            try { el = document.querySelector(arg); } catch (_) { el = null; }
                        }
                        if (el) varTable.set(varName, el);
                    }

                    // 3. Parse addEventListener calls.
                    const addRe = /(\w+(?:\.\w+)?)\.addEventListener\s*\(\s*['"](\w+)['"]/g;
                    while ((m = addRe.exec(text)) !== null) {
                        const target = m[1];
                        const eventType = m[2];
                        if (target === 'document') {
                            const t = eventType.toLowerCase();
                            if (KEY_EVENTS.includes(t)) globalHandlers.document.add(t);
                        } else if (target === 'window') {
                            const t = eventType.toLowerCase();
                            if (KEY_EVENTS.includes(t)) globalHandlers.window.add(t);
                        } else if (target === 'document.body') {
                            const t = eventType.toLowerCase();
                            if (KEY_EVENTS.includes(t)) globalHandlers.body.add(t);
                        } else if (varTable.has(target)) {
                            recordEvent(ensureEntry(varTable.get(target)), eventType);
                        }
                    }
                    return {elementHandlers, globalHandlers,
                            getEntry(el) { return elementHandlers.get(el); }};
                }

                const handlerMap = collectHandlerMap(combinedScriptText);

                // Helpers for Test 1.
                function isFocusable(el) {
                    if (!el || el === document.body) return false;
                    const tag = el.tagName.toLowerCase();
                    if (['a', 'button', 'input', 'select', 'textarea', 'details', 'summary'].includes(tag)) return true;
                    const tabindex = el.getAttribute('tabindex');
                    if (tabindex !== null && parseInt(tabindex) >= 0) return true;
                    const role = el.getAttribute('role');
                    if (role && ['button', 'link', 'menuitem', 'tab', 'checkbox', 'radio', 'switch'].includes(role)) return true;
                    return false;
                }
                function findFocusableAncestor(el) {
                    let cur = el.parentElement;
                    while (cur && cur !== document.body) {
                        if (isFocusable(cur)) return cur;
                        cur = cur.parentElement;
                    }
                    return null;
                }

                // ErrMissingTabindex — preserved logic, sourced from handlerMap.
                const flaggedMissingTabindex = new Set();
                Array.from(document.querySelectorAll('*')).forEach(element => {
                    const entry = handlerMap.getEntry(element);
                    if (!entry) return;
                    if (entry.mouseEvents.size === 0 && entry.keyEvents.size === 0) return;
                    if (isIntrinsicInteractive(element) || element.hasAttribute('tabindex')) return;
                    const tagName = element.tagName.toLowerCase();
                    flaggedMissingTabindex.add(element);
                    out.errors.push({
                        err: 'ErrMissingTabindex',
                        type: 'err',
                        cat: 'event_handling',
                        element: tagName,
                        xpath: getFullXPath(element),
                        html: element.outerHTML.substring(0, 200),
                        description: `<${tagName}> with event handler is not keyboard accessible - missing tabindex`,
                        elementTag: tagName,
                        hasOnclick: element.hasAttribute('onclick'),
                        hasOtherHandlers: element.hasAttribute('onmousedown') ||
                                          element.hasAttribute('onmouseup') ||
                                          element.hasAttribute('ondblclick'),
                    });
                    out.elements_failed++;
                });

                // ErrMissingTabindex — role-based custom controls.
                // A non-native element that advertises an interactive role (role="button",
                // "link", "menuitem", "checkbox", "radio", "switch", "tab", "slider", ...) is
                // announced to assistive technology as operable, but an ARIA role does NOT make
                // an element focusable. Without tabindex="0" (or another focusable host) it cannot
                // receive keyboard focus, so keyboard and switch users cannot operate it. Native
                // controls (<button>, <a href>, <input>, ...) are intrinsically focusable and are
                // not flagged. Inline onclick handlers are caught here too, since the script-text
                // handler map only sees addEventListener registrations.
                const focusableNativeTags = ['a', 'button', 'input', 'select', 'textarea', 'details', 'summary'];
                const keyboardOperableRoles = [
                    'button', 'link', 'menuitem', 'menuitemcheckbox', 'menuitemradio',
                    'checkbox', 'radio', 'switch', 'tab', 'slider', 'spinbutton',
                    'option', 'treeitem'
                ];
                Array.from(document.querySelectorAll('[role]')).forEach(element => {
                    if (flaggedMissingTabindex.has(element)) return;
                    if (element.hasAttribute('tabindex')) return;
                    const tagName = element.tagName.toLowerCase();
                    if (focusableNativeTags.includes(tagName)) return;
                    const role = (element.getAttribute('role') || '').trim().toLowerCase();
                    if (!keyboardOperableRoles.includes(role)) return;
                    flaggedMissingTabindex.add(element);
                    out.errors.push({
                        err: 'ErrMissingTabindex',
                        type: 'err',
                        cat: 'event_handling',
                        element: tagName,
                        xpath: getFullXPath(element),
                        html: element.outerHTML.substring(0, 200),
                        description: `<${tagName}> with role="${role}" is not keyboard focusable - missing tabindex="0"`,
                        elementTag: tagName,
                        role: role,
                        hasOnclick: element.hasAttribute('onclick'),
                    });
                    out.elements_failed++;
                });

                // Test 1 — three-state per-element check.
                Array.from(document.querySelectorAll('*')).forEach(element => {
                    const entry = handlerMap.getEntry(element);
                    if (!entry || entry.mouseEvents.size === 0) return;
                    if (isIntrinsicInteractive(element) || element.hasAttribute('tabindex')) return;

                    if (entry.keyEvents.size > 0) {
                        out.elements_passed++;
                        return;
                    }
                    const ancestor = findFocusableAncestor(element);
                    const ancEntry = ancestor ? handlerMap.getEntry(ancestor) : null;
                    if (ancestor && ancEntry && ancEntry.keyEvents.size > 0) {
                        out.warnings.push({
                            err: 'WarnMouseHandlerKeyboardOnAncestor',
                            type: 'warn',
                            cat: 'event_handling',
                            element: element.tagName,
                            xpath: getFullXPath(element),
                            html: element.outerHTML.substring(0, 200),
                            description: 'Element has mouse handler but no keyboard handler; nearest focusable ancestor has a keyboard handler — manual verification required',
                            ancestorTag: ancestor.tagName.toLowerCase(),
                            ancestorXpath: getFullXPath(ancestor),
                            ancestorKeyEvents: Array.from(ancEntry.keyEvents),
                        });
                    } else {
                        out.errors.push({
                            err: 'ErrMouseOnlyHandler',
                            type: 'err',
                            cat: 'event_handling',
                            element: element.tagName,
                            xpath: getFullXPath(element),
                            html: element.outerHTML.substring(0, 200),
                            description: 'Element has mouse handler, no keyboard handler on itself, and no focusable ancestor with a keyboard handler',
                        });
                        out.elements_failed++;
                    }
                });

                // Test 2 — global keyboard handler discovery.
                out.globalHandlers = {
                    document: Array.from(handlerMap.globalHandlers.document).filter(e => KEY_EVENTS.includes(e)),
                    window: Array.from(handlerMap.globalHandlers.window).filter(e => KEY_EVENTS.includes(e)),
                    body: Array.from(handlerMap.globalHandlers.body).filter(e => KEY_EVENTS.includes(e)),
                };

                return out;
            }
        ''', all_js_code)

        # Merge phase-2 results into the main results dict.
        results['errors'].extend(phase2_results.get('errors', []))
        results['warnings'].extend(phase2_results.get('warnings', []))
        results['elements_passed'] = results.get('elements_passed', 0) + phase2_results.get('elements_passed', 0)
        results['elements_failed'] = results.get('elements_failed', 0) + phase2_results.get('elements_failed', 0)

        # Test 2 emission — single deduped warn per page.
        global_handlers = phase2_results.get('globalHandlers', {'document': [], 'window': [], 'body': []})
        targets_with_keys: list[str] = []
        events_seen: set[str] = set()
        for target_name in ('document', 'window', 'body'):
            evs = global_handlers.get(target_name, [])
            if evs:
                targets_with_keys.append(target_name)
                events_seen.update(evs)

        if targets_with_keys:
            results['warnings'].append({
                'err': 'WarnGlobalKeyboardHandlerPresent',
                'type': 'warn',
                'cat': 'event_handling',
                'element': 'html',
                'xpath': '/html[1]',
                'html': '<html>',
                'description': f"Page-level keyboard handler(s) detected on {', '.join(targets_with_keys)}. Manual verification required.",
                'targets': targets_with_keys,
                'events': sorted(events_seen),
            })

        # Check if JS has escape handler
        has_keydown_listener = bool(re.search(r'addEventListener\s*\(\s*[\'"]keydown[\'"]', all_js_code, re.IGNORECASE)) or \
                               bool(re.search(r'onkeydown', all_js_code, re.IGNORECASE))
        has_escape_check = bool(re.search(r'[\'"]Escape[\'"]', all_js_code, re.IGNORECASE)) or \
                          bool(re.search(r'\.key\s*===?\s*[\'"]Esc[\'"]', all_js_code, re.IGNORECASE)) or \
                          bool(re.search(r'keyCode\s*===?\s*27', all_js_code)) or \
                          bool(re.search(r'which\s*===?\s*27', all_js_code))
        
        page_has_escape_handler = has_keydown_listener and has_escape_check
        
        # Check modals for escape handler
        modals = results.get('_modals', [])
        for modal in modals:
            # Skip native <dialog> elements - browser handles Escape
            if modal['tagName'].lower() == 'dialog':
                continue
            
            # Skip if modal has inline escape handler or page has escape handler in JS
            if modal.get('hasInlineEscapeHandler') or page_has_escape_handler:
                continue
            
            results['errors'].append({
                'err': 'ErrModalWithoutEscape',
                'type': 'err',
                'cat': 'event_handling',
                'element': modal['tagName'],
                'xpath': modal['xpath'],
                'html': modal['html'],
                'description': 'Modal element without keyboard escape handler'
            })
            results['elements_failed'] = results.get('elements_failed', 0) + 1
        
        # Clean up internal fields
        results.pop('_inlineJsCode', None)
        results.pop('_externalScriptUrls', None)
        results.pop('_modals', None)

        # Additional check: Focus indicators for interactive elements (tabindex/event handlers)
        # Extract elements with tabindex or inline event handlers and check their focus styles
        focus_elements = await page.evaluate('''
            () => {
                const elements = [];

                // Function to generate XPath for elements
                function getFullXPath(element) {
                    if (!element) return '';

                    function getElementIdx(el) {
                        let count = 1;
                        for (let sib = el.previousSibling; sib; sib = sib.previousSibling) {
                            if (sib.nodeType === 1 && sib.tagName === el.tagName) {
                                count++;
                            }
                        }
                        return count;
                    }

                    let path = '';
                    while (element && element.nodeType === 1) {
                        const idx = getElementIdx(element);
                        const tagName = element.tagName.toLowerCase();
                        path = `/${tagName}[${idx}]${path}`;
                        element = element.parentNode;
                    }
                    return path;
                }

                // Helper to parse color values to RGBA
                function parseColor(colorStr) {
                    if (!colorStr || colorStr === 'transparent') {
                        return { r: 0, g: 0, b: 0, a: 0 };
                    }
                    const rgbaMatch = colorStr.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)(?:,\\s*([\\d.]+))?\\)/);
                    if (rgbaMatch) {
                        return {
                            r: parseInt(rgbaMatch[1]),
                            g: parseInt(rgbaMatch[2]),
                            b: parseInt(rgbaMatch[3]),
                            a: rgbaMatch[4] !== undefined ? parseFloat(rgbaMatch[4]) : 1
                        };
                    }
                    const hexMatch = colorStr.match(/^#([0-9a-f]{6})$/i);
                    if (hexMatch) {
                        const hex = hexMatch[1];
                        return {
                            r: parseInt(hex.substr(0, 2), 16),
                            g: parseInt(hex.substr(2, 2), 16),
                            b: parseInt(hex.substr(4, 2), 16),
                            a: 1
                        };
                    }
                    return { r: 0, g: 0, b: 0, a: 1 };
                }

                // Calculate relative luminance
                function getLuminance(color) {
                    const rsRGB = color.r / 255;
                    const gsRGB = color.g / 255;
                    const bsRGB = color.b / 255;
                    const r = rsRGB <= 0.03928 ? rsRGB / 12.92 : Math.pow((rsRGB + 0.055) / 1.055, 2.4);
                    const g = gsRGB <= 0.03928 ? gsRGB / 12.92 : Math.pow((gsRGB + 0.055) / 1.055, 2.4);
                    const b = bsRGB <= 0.03928 ? bsRGB / 12.92 : Math.pow((bsRGB + 0.055) / 1.055, 2.4);
                    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
                }

                // Interactive tags to exclude (already natively interactive)
                const interactiveTags = ['a', 'button', 'input', 'select', 'textarea', 'summary', 'details'];
                const interactiveRoles = [
                    'button', 'link', 'checkbox', 'radio', 'tab', 'menuitem',
                    'menuitemcheckbox', 'menuitemradio', 'option', 'switch',
                    'textbox', 'searchbox', 'slider', 'spinbutton', 'combobox',
                    'scrollbar', 'gridcell', 'treeitem'
                ];

                // Find all elements with tabindex >= -1
                const allElements = document.querySelectorAll('[tabindex]');
                allElements.forEach((element) => {
                    const tabindex = parseInt(element.getAttribute('tabindex'));
                    if (tabindex < -1) return; // Skip non-focusable

                    const tagName = element.tagName.toLowerCase();
                    const role = element.getAttribute('role');

                    // Skip semantic interactive elements
                    if (interactiveTags.includes(tagName)) return;
                    if (role && interactiveRoles.includes(role)) return;

                    // Extract styles
                    const computed = window.getComputedStyle(element);

                    // Extract :focus styles from stylesheets
                    let focusOutlineStyle = null;
                    let focusOutlineWidth = null;
                    let focusOutlineColor = null;
                    let focusBoxShadow = null;
                    let focusBorderWidth = null;
                    let focusBackgroundColor = null;

                    try {
                        for (let sheet of document.styleSheets) {
                            try {
                                const rules = sheet.cssRules || sheet.rules;
                                if (!rules) continue;

                                for (let rule of rules) {
                                    if (!rule.selectorText || !rule.selectorText.includes(':focus')) continue;

                                    const testSelector = rule.selectorText.replace(/:focus.*?(?=[,\\s]|$)/g, '').trim();
                                    if (!testSelector) continue;

                                    try {
                                        if (element.matches(testSelector)) {
                                            if (rule.style.outlineStyle !== undefined && rule.style.outlineStyle !== '') {
                                                focusOutlineStyle = rule.style.outlineStyle;
                                            }
                                            if (rule.style.outlineWidth !== undefined && rule.style.outlineWidth !== '') {
                                                focusOutlineWidth = rule.style.outlineWidth;
                                            }
                                            if (rule.style.outlineColor !== undefined && rule.style.outlineColor !== '') {
                                                focusOutlineColor = rule.style.outlineColor;
                                            }
                                            if (rule.style.outline !== undefined && rule.style.outline !== '') {
                                                const outlineValue = rule.style.outline;
                                                if (outlineValue === 'none' || outlineValue === '0') {
                                                    focusOutlineStyle = 'none';
                                                    focusOutlineWidth = '0px';
                                                } else {
                                                    const parts = outlineValue.split(' ');
                                                    for (let part of parts) {
                                                        if (part.includes('px') || part.includes('em') || part.includes('rem')) {
                                                            focusOutlineWidth = part;
                                                        } else if (['solid', 'dotted', 'dashed', 'double'].includes(part)) {
                                                            focusOutlineStyle = part;
                                                        }
                                                    }
                                                    const colorParts = parts.filter(p =>
                                                        !p.match(/^\\d+(\\.\\d+)?(px|em|rem)$/) &&
                                                        !['solid', 'dotted', 'dashed', 'double'].includes(p)
                                                    );
                                                    if (colorParts.length > 0) {
                                                        focusOutlineColor = colorParts.join(' ');
                                                    }
                                                }
                                            }
                                            if (rule.style.boxShadow !== undefined && rule.style.boxShadow !== '') {
                                                focusBoxShadow = rule.style.boxShadow;
                                            }
                                            if (rule.style.borderWidth !== undefined && rule.style.borderWidth !== '') {
                                                focusBorderWidth = rule.style.borderWidth;
                                            }
                                            if (rule.style.backgroundColor !== undefined && rule.style.backgroundColor !== '') {
                                                focusBackgroundColor = rule.style.backgroundColor;
                                            }
                                        }
                                    } catch (e) {
                                        // Invalid selector
                                    }
                                }
                            } catch (e) {
                                // Cross-origin stylesheet
                            }
                        }
                    } catch (e) {
                        // Error accessing stylesheets
                    }

                    // Check for inline event handlers
                    const eventAttrs = ['onclick', 'onkeydown', 'onkeyup', 'onkeypress', 'onmousedown', 'onmouseup'];
                    const hasInlineHandler = eventAttrs.some(attr => element.hasAttribute(attr));

                    // Check if parent is an interactive element (for detecting redundant focusable children)
                    let parentIsInteractive = false;
                    let parentTag = '';
                    if (element.parentElement) {
                        const parent = element.parentElement;
                        parentTag = parent.tagName.toLowerCase();
                        const parentTabindex = parseInt(parent.getAttribute('tabindex') || '-1');
                        const interactiveTags = ['button', 'a', 'summary'];
                        parentIsInteractive = interactiveTags.includes(parentTag) || parentTabindex >= 0;
                    }

                    // Check for aria-hidden
                    const ariaHidden = element.getAttribute('aria-hidden');

                    elements.push({
                        tag: tagName,
                        id: element.id || '',
                        className: element.className || '',
                        tabindex: tabindex,
                        role: role || '',
                        xpath: getFullXPath(element),
                        html: element.outerHTML.substring(0, 300),  // Capture HTML snippet for display
                        hasInlineHandler: hasInlineHandler,
                        parentTag: parentTag,
                        parentIsInteractive: parentIsInteractive,
                        ariaHidden: ariaHidden,
                        normalOutlineStyle: computed.outlineStyle,
                        normalOutlineWidth: computed.outlineWidth,
                        normalBorderWidth: computed.borderWidth,
                        normalBoxShadow: computed.boxShadow,
                        backgroundColor: computed.backgroundColor,
                        backgroundImage: computed.backgroundImage,
                        focusOutlineStyle,
                        focusOutlineWidth,
                        focusOutlineColor,
                        focusBoxShadow,
                        focusBorderWidth,
                        focusBackgroundColor
                    });
                });

                return elements;
            }
        ''')

        # Process focus indicator data in Python
        if focus_elements:
            # Module-level helpers (tested directly); aliased for readability.
            parse_px = _parse_px
            parse_color = _parse_color

            def get_luminance(color: dict[str, float]) -> float:
                """Calculate relative luminance"""
                r = color['r'] / 255.0
                g = color['g'] / 255.0
                b = color['b'] / 255.0
                r = r / 12.92 if r <= 0.03928 else ((r + 0.055) / 1.055) ** 2.4
                g = g / 12.92 if g <= 0.03928 else ((g + 0.055) / 1.055) ** 2.4
                b = b / 12.92 if b <= 0.03928 else ((b + 0.055) / 1.055) ** 2.4
                return 0.2126 * r + 0.7152 * g + 0.0722 * b

            def get_contrast_ratio(color1: dict[str, float], color2: dict[str, float]) -> float:
                """Calculate contrast ratio between two colors"""
                l1 = get_luminance(color1)
                l2 = get_luminance(color2)
                lighter = max(l1, l2)
                darker = min(l1, l2)
                return (lighter + 0.05) / (darker + 0.05)

            # Process each element
            for elem in focus_elements:
                element_id = f"#{elem.get('id')}" if elem.get('id') else f"{elem['tag']}.{elem.get('className', '')}"
                elem_type = 'tabindex' if elem.get('tabindex') is not None else 'handler'

                # Determine error code prefix
                code_prefix = 'ErrTabindex' if elem_type == 'tabindex' else 'ErrHandler'
                warn_prefix = 'WarnTabindex' if elem_type == 'tabindex' else 'WarnHandler'

                # Parse focus styles
                focus_outline_style = elem.get('focusOutlineStyle')
                focus_outline_width = parse_px(elem.get('focusOutlineWidth'))
                focus_outline_color = elem.get('focusOutlineColor')
                focus_box_shadow = elem.get('focusBoxShadow')

                # Check if gradient background
                bg_image = elem.get('backgroundImage', 'none')
                has_gradient = bg_image and 'gradient' in bg_image

                # Determine if element has custom focus styles
                outline_is_none = focus_outline_style == 'none'
                has_outline = focus_outline_style and focus_outline_style not in ['none', 'initial', ''] and focus_outline_width > 0
                box_shadow_changed = focus_box_shadow and focus_box_shadow not in ['none', 'initial', '']

                # Check for border/bg changes
                normal_border_width = parse_px(elem.get('normalBorderWidth', '0px'))
                focus_border_width = parse_px(elem.get('focusBorderWidth')) if elem.get('focusBorderWidth') else normal_border_width
                border_width_changed = abs(focus_border_width - normal_border_width) > 0.5

                normal_bg_color = elem.get('backgroundColor')
                focus_bg_color = elem.get('focusBackgroundColor')
                bg_color_changed = focus_bg_color and focus_bg_color not in ['', 'initial'] and focus_bg_color != normal_bg_color

                # Run all checks independently
                issues_found: list[tuple[str, str]] = []

                # Check 0: Focusable child inside interactive parent
                if elem.get('parentIsInteractive', False):
                    parent_tag = elem.get('parentTag', 'unknown')
                    desc = f"Child element with tabindex={elem.get('tabindex')} inside interactive <{parent_tag}> parent"
                    issues_found.append((f'{code_prefix}ChildOfInteractive', desc))

                # Check 0b: aria-hidden on focusable element
                aria_hidden = elem.get('ariaHidden', '')
                if aria_hidden == 'true':
                    desc = f"Element with {'tabindex' if elem_type == 'tabindex' else 'event handler'} has aria-hidden='true', which is invalid"
                    issues_found.append((f'{code_prefix}AriaHiddenFocusable', desc))

                # Check 1: No visible focus indicator
                if not has_outline and not box_shadow_changed and not border_width_changed and not bg_color_changed:
                    tag = elem.get('tag', '')
                    role = elem.get('role', '')
                    has_handler = elem.get('hasInlineHandler', False)
                    
                    # Interactive roles that require visible focus
                    interactive_roles = [
                        'button', 'link', 'checkbox', 'radio', 'tab', 'menuitem',
                        'menuitemcheckbox', 'menuitemradio', 'option', 'switch',
                        'textbox', 'searchbox', 'slider', 'spinbutton', 'combobox',
                        'scrollbar', 'gridcell', 'treeitem'
                    ]
                    
                    is_interactive = role in interactive_roles or has_handler

                    # An explicit tabindex makes the element focusable on purpose,
                    # so the missing focus indicator is a hard failure regardless of
                    # whether we can also prove interactivity (a click handler may be
                    # attached via addEventListener, which this static check cannot
                    # see). Event-handler elements without a detectable interactive
                    # signal stay a warning, matching the softer "handler" path.
                    if is_interactive:
                        desc = f"Interactive element (role='{role}') with tabindex lacks visible focus indicator"
                        issues_found.append((f'{code_prefix}NoVisibleFocus', desc))
                    elif elem_type == 'tabindex':
                        desc = f"Focusable <{tag}> with tabindex lacks a visible focus indicator. Keyboard users cannot tell when this element has focus. Add a visible :focus style (outline, box-shadow, or border), or use tabindex='-1' if the element does not need to be in the tab order."
                        issues_found.append((f'{code_prefix}NoVisibleFocus', desc))
                    else:
                        desc = f"Non-interactive <{tag}> with tabindex lacks visible focus indicator. Adding tabindex to non-interactive elements makes them focusable but may confuse users expecting interactivity. Consider: (1) removing tabindex if focus is not needed, (2) adding a visible focus style if focus is intentional, or (3) using tabindex='-1' for programmatic focus only."
                        issues_found.append((f'{warn_prefix}NoVisibleFocus', desc))

                # Check 2: outline:none without alternative
                if outline_is_none and not box_shadow_changed and not border_width_changed and not bg_color_changed:
                    desc = f"Element sets outline:none without alternative focus indicator"
                    issues_found.append((f'{code_prefix}OutlineNoneNoBoxShadow', desc))

                # Check 3: Color-only change
                if bg_color_changed and not has_outline and not box_shadow_changed and not border_width_changed:
                    desc = f"Element focus relies solely on color change (violates WCAG 1.4.1)"
                    issues_found.append((f'{code_prefix}ColorChangeOnly', desc))

                # Check 4: Single-sided box-shadow
                if box_shadow_changed and focus_box_shadow and focus_box_shadow != 'none':
                    shadow_str = re.sub(r'rgba?\([^)]+\)', '', focus_box_shadow)
                    shadow_str = re.sub(r'#[0-9a-fA-F]{3,6}', '', shadow_str)
                    shadow_values = shadow_str.strip().split()
                    if len(shadow_values) >= 2:
                        try:
                            h_offset = parse_px(shadow_values[0])
                            v_offset = parse_px(shadow_values[1])
                            if (h_offset != 0 and v_offset == 0) or (h_offset == 0 and v_offset != 0):
                                desc = f"Element uses single-sided box-shadow for focus (violates CR 5.2.4)"
                                issues_found.append((f'{code_prefix}SingleSideBoxShadow', desc))
                        except:
                            pass

                # Check 5: Outline width insufficient (AAA)
                if has_outline and focus_outline_width < 2.0:
                    desc = f"Element focus outline is {focus_outline_width:.1f}px (WCAG 2.4.11 recommends ≥2px)"
                    issues_found.append((f'{code_prefix}OutlineWidthInsufficient', desc))

                # Check 6: Contrast and transparency checks
                if not has_gradient:
                    bg_color = parse_color(elem.get('backgroundColor'))

                    if has_outline and focus_outline_color:
                        outline_color = parse_color(focus_outline_color)

                        # Check transparency
                        if outline_color['a'] < 0.5:
                            desc = f"Element focus outline is semi-transparent (alpha={outline_color['a']:.2f})"
                            issues_found.append((f'{code_prefix}TransparentOutline', desc))

                        # Check contrast
                        contrast = get_contrast_ratio(outline_color, bg_color)
                        if contrast < 3.0:
                            desc = f"Element focus outline has insufficient contrast ({contrast:.2f}:1, needs ≥3:1)"
                            issues_found.append((f'{code_prefix}FocusContrastFail', desc))

                    elif box_shadow_changed and focus_box_shadow:
                        shadow_color_match = re.search(r'rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([0-9.]+))?\)', focus_box_shadow)
                        if shadow_color_match:
                            shadow_color = {
                                'r': int(shadow_color_match.group(1)),
                                'g': int(shadow_color_match.group(2)),
                                'b': int(shadow_color_match.group(3)),
                                'a': float(shadow_color_match.group(4)) if shadow_color_match.group(4) else 1.0
                            }

                            # Check transparency
                            if shadow_color['a'] < 0.5:
                                desc = f"Element focus box-shadow is semi-transparent (alpha={shadow_color['a']:.2f})"
                                issues_found.append((f'{code_prefix}TransparentOutline', desc))

                            # Check contrast
                            contrast = get_contrast_ratio(shadow_color, bg_color)
                            if contrast < 3.0:
                                desc = f"Element focus box-shadow has insufficient contrast ({contrast:.2f}:1)"
                                issues_found.append((f'{code_prefix}FocusContrastFail', desc))

                # Check 7: Default browser focus (no custom styles)
                if not has_outline and not outline_is_none and not box_shadow_changed and not border_width_changed and not bg_color_changed:
                    desc = f"Element relies on default browser focus styles (inconsistent across browsers)"
                    issues_found.append((f'{warn_prefix}DefaultFocus', desc))

                # Check 8: Outline-only (screen magnifier concern)
                if has_outline and not box_shadow_changed and not border_width_changed and not bg_color_changed:
                    desc = f"Element uses outline-only focus (screen magnifier users may not see it when zoomed)"
                    issues_found.append((f'{warn_prefix}NoBorderOutline', desc))

                # Report all issues found for this element
                for error_code, violation_reason in issues_found:
                    result_type = 'warn' if error_code.startswith('Warn') else 'err'
                    result_list = results['warnings'] if result_type == 'warn' else results['errors']
                    result_list.append({
                        'err': error_code,
                        'type': result_type,
                        'cat': 'event_handling',
                        'element': elem['tag'],
                        'xpath': elem.get('xpath', ''),
                        'html': elem.get('html', ''),  # Include HTML snippet for code display
                        'selector': element_id,
                        'metadata': {
                            'what': violation_reason,
                            'element_type': f"{elem['tag']}[tabindex='{elem.get('tabindex')}']" if elem_type == 'tabindex' else f"{elem['tag']}[event]",
                            'identifier': element_id,
                            'has_inline_handler': elem.get('hasInlineHandler', False)
                        }
                    })

        return results

    except Exception as e:
        logger.error(f"Error in test_event_handlers: {e}")
        return {
            'error': str(e),
            'applicable': False,
            'errors': [],
            'warnings': [],
            'passes': []
        }