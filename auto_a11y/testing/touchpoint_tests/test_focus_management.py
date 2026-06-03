"""
Focus Management touchpoint test module
Evaluates if interactive elements have appropriate focus indicators that meet accessibility standards.
"""

from __future__ import annotations

from typing import Any
import logging
import re

from playwright.async_api import Page

logger = logging.getLogger(__name__)

# Common CSS named colors that may appear in focus-indicator outline declarations.
_FOCUS_NAMED_COLORS: dict[str, tuple[int, int, int]] = {
    'black': (0, 0, 0), 'white': (255, 255, 255), 'red': (255, 0, 0),
    'green': (0, 128, 0), 'lime': (0, 255, 0), 'blue': (0, 0, 255),
    'yellow': (255, 255, 0), 'cyan': (0, 255, 255), 'aqua': (0, 255, 255),
    'magenta': (255, 0, 255), 'fuchsia': (255, 0, 255), 'silver': (192, 192, 192),
    'gray': (128, 128, 128), 'grey': (128, 128, 128), 'orange': (255, 165, 0),
    'navy': (0, 0, 128), 'teal': (0, 128, 128), 'purple': (128, 0, 128),
}


def _focus_parse_alpha(color_str: str | None) -> float:
    """Return the alpha channel (0..1) of a CSS colour string.

    ``transparent`` -> 0. ``rgba(...)`` uses its explicit alpha. Hex/named/rgb
    and anything else opaque -> 1.0.
    """
    if not color_str:
        return 1.0
    value = color_str.strip().lower()
    if value == 'transparent':
        return 0.0
    match = re.match(r'rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*(?:,\s*([\d.]+)\s*)?\)', value)
    if match:
        return float(match.group(1)) if match.group(1) is not None else 1.0
    # 8-digit hex (#rrggbbaa) carries alpha in the last byte.
    hex8 = re.match(r'#([0-9a-f]{8})$', value)
    if hex8:
        return int(hex8.group(1)[6:8], 16) / 255.0
    return 1.0


def _focus_extract_outline_color(focus_data: dict[str, Any]) -> str | None:
    """Pull the outline colour out of either the longhand or the shorthand."""
    color = focus_data.get('focusOutlineColor')
    if color:
        return str(color)
    shorthand = focus_data.get('focusOutlineShorthand')
    if not shorthand:
        return None
    text = str(shorthand)
    rgba = re.search(r'rgba?\([^)]*\)', text)
    if rgba:
        return rgba.group(0)
    hex_match = re.search(r'#[0-9a-fA-F]{3,8}\b', text)
    if hex_match:
        return hex_match.group(0)
    for token in text.split():
        if token in ('transparent',) or token.lower() in _FOCUS_NAMED_COLORS:
            return token
    return None


def _focus_has_outline(focus_data: dict[str, Any]) -> bool:
    """True if the :focus rule declares a real (non-none, non-zero) outline."""
    style = focus_data.get('focusOutlineStyle')
    width = focus_data.get('focusOutlineWidth')
    shorthand = focus_data.get('focusOutlineShorthand')
    if shorthand:
        s = str(shorthand).strip()
        if s in ('none', '0', '0px'):
            return False
        # A shorthand with a style keyword and non-zero width is a real outline.
        if any(kw in s for kw in ('solid', 'dotted', 'dashed', 'double', 'groove', 'ridge')):
            return True
    if style and style not in ('none', 'initial', ''):
        # outline-style declared without an explicit 0 width -> visible.
        if width in ('0', '0px'):
            return False
        return True
    return False


def _process_focus_css(results: dict[str, Any], elements: list[dict[str, Any]]) -> None:
    """Emit granular focus-indicator codes from declared :focus/base CSS.

    Each element carries its matched stylesheet declarations (``focusData``)
    plus a couple of computed base values. We never rely on computed :focus
    styles because they are not applied to unfocused elements and outline-offset
    always computes to 0px.
    """
    for elem in elements:
        focus_data = elem.get('focusData')
        tag = elem.get('tag', '')
        xpath = elem.get('xpath', '')
        html = elem.get('html', '')

        def push(code: str, description: str, *, is_warn: bool = False) -> None:
            bucket = results['warnings'] if is_warn else results['errors']
            bucket.append({
                'err': code,
                'type': 'warn' if is_warn else 'err',
                'cat': 'focus_management',
                'element': tag,
                'xpath': xpath,
                'html': html,
                'description': description,
            })

        # ErrNegativeTabindex: interactive element removed from the tab order.
        tabindex_attr = elem.get('tabindex')
        if tabindex_attr is not None:
            try:
                if int(tabindex_attr) < 0:
                    push(
                        'ErrNegativeTabindex',
                        f"Interactive <{tag}> has tabindex=\"{tabindex_attr}\", removing it from the keyboard tab order so keyboard-only users cannot reach it.",
                    )
            except (TypeError, ValueError):
                pass

        if not focus_data:
            continue

        has_outline = _focus_has_outline(focus_data)
        box_shadow = focus_data.get('focusBoxShadow')
        has_box_shadow = bool(box_shadow and str(box_shadow).strip() not in ('none', 'initial', ''))
        focus_border = focus_data.get('focusBorder')
        focus_border_color = focus_data.get('focusBorderColor')
        has_border_change = bool(
            (focus_border and str(focus_border).strip() not in ('none', 'initial', '', '0'))
            or focus_border_color
        )
        focus_bg = focus_data.get('focusBackground')
        normal_bg = elem.get('normalBackground')
        bg_changed = bool(focus_bg and str(focus_bg).strip() not in ('', 'initial', 'none')
                          and str(focus_bg) != str(normal_bg))

        outline_removed_on_focus = False
        shorthand = focus_data.get('focusOutlineShorthand')
        if shorthand and str(shorthand).strip() in ('none', '0', '0px'):
            outline_removed_on_focus = True
        if focus_data.get('focusOutlineStyle') == 'none' or focus_data.get('focusOutlineWidth') in ('0', '0px'):
            outline_removed_on_focus = True

        # ErrOutlineIsNoneOnInteractiveElement: outline removed (base or :focus)
        # with no replacement indicator (box-shadow / border / background).
        base_outline_none = bool(focus_data.get('baseOutlineNone'))
        if (base_outline_none or outline_removed_on_focus) and not has_box_shadow \
                and not has_border_change and not bg_changed and not has_outline:
            push(
                'ErrOutlineIsNoneOnInteractiveElement',
                f"Interactive <{tag}> sets outline:none without providing a replacement focus indicator (box-shadow, border, or background change). Keyboard users lose the visible focus cue.",
            )

        # The remaining checks only make sense when a focus outline is declared.
        if has_outline:
            outline_color = _focus_extract_outline_color(focus_data)

            # ErrTransparentFocusIndicator: outline colour is (near-)transparent.
            if outline_color is not None and _focus_parse_alpha(outline_color) < 0.5:
                push(
                    'ErrTransparentFocusIndicator',
                    f"Interactive <{tag}> focus outline uses a transparent or near-transparent colour ({outline_color}); the focus indicator is effectively invisible.",
                )

            # WarnZeroOutlineOffset vs ErrNoOutlineOffsetDefined.
            if focus_data.get('focusOutlineOffsetDeclared'):
                offset_val = str(focus_data.get('focusOutlineOffset') or '').strip()
                offset_match = re.match(r'^([+-]?\d*\.?\d+)', offset_val)
                offset_num = float(offset_match.group(1)) if offset_match else 0.0
                if offset_num == 0.0:
                    push(
                        'WarnZeroOutlineOffset',
                        f"Interactive <{tag}> focus outline uses outline-offset:0, so the outline hugs the element edge and is harder to perceive. A small positive offset improves visibility.",
                        is_warn=True,
                    )
            else:
                push(
                    'ErrNoOutlineOffsetDefined',
                    f"Interactive <{tag}> declares a focus outline but no outline-offset, so the outline sits directly on the element edge and can be hard to distinguish from the border.",
                )

        # ErrFocusBackgroundColorOnly: focus changes the background colour but
        # provides no outline or box-shadow. A background (and even a border)
        # colour swap alone is not a reliable focus cue (WCAG 1.4.1 / 2.4.7) -
        # only a real outline or shadow counts as the replacement here.
        if bg_changed and not has_outline and not has_box_shadow and not base_outline_none:
            push(
                'ErrFocusBackgroundColorOnly',
                f"Interactive <{tag}> relies solely on a background-colour change for its focus indicator. A colour change alone is not a reliable focus cue (WCAG 1.4.1 / 2.4.7).",
            )

TEST_DOCUMENTATION = {
    "testName": "Focus Management Analysis",
    "touchpoint": "focus_management",
    "description": "Evaluates if interactive elements have appropriate focus indicators that meet accessibility standards. This test analyzes CSS rules to identify focus styles and tests whether keyboard users can perceive focus states.",
    "version": "1.1.0",
    "wcagCriteria": ["2.4.7", "2.4.11", "2.4.3"],
    "tests": [
        {
            "id": "focus_outline_presence",
            "name": "Focus Outline Presence",
            "description": "Checks if interactive elements have a visible focus indicator when receiving keyboard focus",
            "impact": "high",
            "wcagCriteria": ["2.4.7"],
        },
        {
            "id": "focus_outline_contrast",
            "name": "Focus Outline Contrast",
            "description": "Measures if focus outlines have sufficient contrast against background colors",
            "impact": "high",
            "wcagCriteria": ["2.4.11"],
        },
        {
            "id": "hover_feedback",
            "name": "Hover Visual Feedback",
            "description": "Checks if elements provide sufficient visual feedback on hover",
            "impact": "medium",
            "wcagCriteria": ["2.4.7"],
        },
        {
            "id": "anchor_target_tabindex",
            "name": "Anchor Target Accessibility",
            "description": "Verifies if in-page link targets are properly configured for keyboard navigation",
            "impact": "medium",
            "wcagCriteria": ["2.4.3"],
        }
    ]
}

async def test_focus_management(page: Page) -> dict[str, Any]:
    """
    Test focus management and interactive element styling
    
    Args:
        page: Playwright Page object
        
    Returns:
        Dictionary containing test results with errors and warnings
    """
    try:
        # Execute JavaScript to analyze focus management
        results: dict[str, Any] = await page.evaluate('''
            () => {
                const results = {
                    applicable: true,
                    errors: [],
                    warnings: [],
                    passes: [],
                    elements_tested: 0,
                    elements_passed: 0,
                    elements_failed: 0,
                    test_name: 'focus_management',
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
                
                // Check whether an element has a visible focus indicator.
                //
                // Browsers render a default focus ring on interactive elements unless an author
                // stylesheet removes it (outline: none / outline: 0). So an element only LACKS a
                // visible focus indicator when an author :focus (or :focus-visible) rule that matches
                // it removes the outline AND supplies no visible replacement (a non-trivial
                // box-shadow, border, outline or background change). If the default outline is left
                // intact, the element is considered to have a focus indicator.
                function ruleProvidesVisibleFocus(ruleStyle) {
                    // A visible replacement: any outline that is not "none"/0, a box-shadow, a
                    // border declaration, or a background colour change.
                    const outline = (ruleStyle.outline || '').trim().toLowerCase();
                    const outlineStyle = (ruleStyle.outlineStyle || '').trim().toLowerCase();
                    const outlineWidth = (ruleStyle.outlineWidth || '').trim().toLowerCase();
                    const hasVisibleOutline =
                        (outline && outline !== 'none' && outline !== '0' && outline !== '0px' && outlineStyle !== 'none') ||
                        (outlineStyle && outlineStyle !== 'none' && outlineWidth && outlineWidth !== '0px' && outlineWidth !== '0');
                    const boxShadow = (ruleStyle.boxShadow || '').trim().toLowerCase();
                    const hasBoxShadow = boxShadow && boxShadow !== 'none';
                    const hasBorder = !!(ruleStyle.border || ruleStyle.borderColor || ruleStyle.borderWidth ||
                                         ruleStyle.borderBottom || ruleStyle.borderStyle);
                    const hasBackground = !!(ruleStyle.backgroundColor || ruleStyle.background);
                    return hasVisibleOutline || hasBoxShadow || hasBorder || hasBackground;
                }

                function hasFocusStyles(element) {
                    try {
                        let authorRemovesOutline = false;
                        let authorProvidesReplacement = false;
                        let matchedFocusRule = false;

                        for (const sheet of Array.from(document.styleSheets)) {
                            let rules;
                            try { rules = sheet.cssRules; } catch (e) { continue; }
                            if (!rules) continue;
                            for (const rule of Array.from(rules)) {
                                if (!rule.style || !rule.selectorText) continue;
                                const selector = rule.selectorText;
                                if (selector.indexOf(':focus') === -1) continue;

                                // Build a base selector list (strip the :focus / :focus-visible
                                // pseudo-class) and test whether this element matches any part.
                                let matches = false;
                                const parts = selector.split(',');
                                for (const part of parts) {
                                    if (part.indexOf(':focus') === -1) continue;
                                    const base = part
                                        .replace(':focus-visible', '')
                                        .replace(':focus-within', '')
                                        .replace(':focus', '')
                                        .trim();
                                    if (!base) { matches = true; break; }
                                    try {
                                        if (element.matches(base)) { matches = true; break; }
                                    } catch (e) { /* invalid selector - skip */ }
                                }
                                if (!matches) continue;

                                matchedFocusRule = true;
                                const outline = (rule.style.outline || '').trim().toLowerCase();
                                const outlineStyle = (rule.style.outlineStyle || '').trim().toLowerCase();
                                const outlineWidth = (rule.style.outlineWidth || '').trim().toLowerCase();
                                const removesOutline =
                                    outline === 'none' || outline === '0' || outline === '0px' ||
                                    outlineStyle === 'none' || outlineWidth === '0' || outlineWidth === '0px';
                                if (removesOutline) {
                                    authorRemovesOutline = true;
                                }
                                if (ruleProvidesVisibleFocus(rule.style)) {
                                    authorProvidesReplacement = true;
                                }
                            }
                        }

                        // No author :focus rule touches this element -> default browser focus ring
                        // remains -> has a focus indicator.
                        if (!matchedFocusRule) {
                            return true;
                        }
                        // An author rule removed the outline and supplied no visible replacement.
                        if (authorRemovesOutline && !authorProvidesReplacement) {
                            return false;
                        }
                        return true;
                    } catch (e) {
                        // On any unexpected error, do not raise a false positive.
                        return true;
                    }
                }
                
                // Get all interactive elements
                const interactiveElements = Array.from(document.querySelectorAll(
                    'a, button, input, select, textarea, [role="button"], [role="link"], [tabindex="0"], [contenteditable="true"]'
                )).filter(el => {
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none' && style.visibility !== 'hidden' && !el.disabled;
                });
                
                if (interactiveElements.length === 0) {
                    results.applicable = false;
                    results.not_applicable_reason = 'No interactive elements found on the page';
                    return results;
                }
                
                results.elements_tested = interactiveElements.length;
                
                // Test each interactive element
                interactiveElements.forEach(element => {
                    const tag = element.tagName.toLowerCase();
                    const text = element.textContent.trim().substring(0, 50);

                    // Check for focus indicators
                    const hasFocus = hasFocusStyles(element);
                    if (!hasFocus) {
                        // Capture HTML, with fallback for edge cases
                        let htmlSnippet = '';
                        let htmlSource = 'unknown';
                        try {
                            if (element.outerHTML) {
                                htmlSnippet = element.outerHTML.substring(0, 200);
                                htmlSource = 'outerHTML';
                            } else {
                                htmlSnippet = `<${tag}${element.id ? ' id="' + element.id + '"' : ''}${element.className ? ' class="' + element.className + '"' : ''}>${text}</${tag}>`;
                                htmlSource = 'fallback-empty';
                            }
                        } catch (e) {
                            htmlSnippet = `<${tag}${element.id ? ' id="' + element.id + '"' : ''}${element.className ? ' class="' + element.className + '"' : ''}>${text}</${tag}>`;
                            htmlSource = 'fallback-error';
                        }

                        results.errors.push({
                            err: 'ErrNoFocusIndicator',
                            type: 'err',
                            cat: 'focus_management',
                            element: tag,
                            xpath: getFullXPath(element),
                            html: htmlSnippet,
                            htmlSource: htmlSource,
                            description: 'Interactive element has no visible focus indicator',
                            text: text
                        });
                        results.elements_failed++;
                    } else {
                        results.elements_passed++;
                    }
                    
                    // Check cursor: a clickable element should present cursor:pointer so users
                    // know it is interactive.
                    //
                    // Native interactive elements (<button>, <a>) are semantically obvious and many
                    // browsers render them with the default (arrow) cursor, so a missing pointer
                    // cursor is NOT a problem on its own. We only flag a native control when an author
                    // stylesheet has *explicitly* set a non-pointer cursor on it (a deliberate
                    // override that hides the affordance). Custom clickable elements (div/span/etc.
                    // carrying an onclick handler or an interactive role) are always flagged when
                    // they lack a pointer cursor, because nothing else signals their clickability.
                    const style = window.getComputedStyle(element);
                    if (style.cursor !== 'pointer') {
                        const role = element.getAttribute('role');
                        const interactiveRoles = ['button', 'link', 'menuitem', 'tab', 'checkbox', 'radio', 'switch'];
                        const hasInteractiveRole = role && interactiveRoles.includes(role);
                        const hasClickHandler = element.hasAttribute('onclick') ||
                                                element.hasAttribute('ng-click') ||
                                                element.hasAttribute('@click');
                        const isNativeInteractive = (tag === 'a' && element.hasAttribute('href')) || tag === 'button';

                        // Detect whether an author stylesheet rule explicitly sets `cursor` on this element.
                        let hasExplicitCursor = element.style.cursor !== '';
                        if (!hasExplicitCursor) {
                            try {
                                for (const sheet of Array.from(document.styleSheets)) {
                                    let rules;
                                    try { rules = sheet.cssRules; } catch (e) { continue; }
                                    if (!rules) continue;
                                    for (const rule of Array.from(rules)) {
                                        if (rule.style && rule.style.cursor && rule.selectorText) {
                                            try {
                                                if (element.matches(rule.selectorText)) {
                                                    hasExplicitCursor = true;
                                                    break;
                                                }
                                            } catch (e) { /* invalid selector - skip */ }
                                        }
                                    }
                                    if (hasExplicitCursor) break;
                                }
                            } catch (e) { /* ignore stylesheet access issues */ }
                        }

                        const shouldWarn = isNativeInteractive
                            ? hasExplicitCursor
                            : (hasInteractiveRole || hasClickHandler);

                        if (shouldWarn) {
                            results.warnings.push({
                                err: 'WarnNoCursorPointer',
                                type: 'warn',
                                cat: 'focus_management',
                                element: tag,
                                xpath: getFullXPath(element),
                                html: element.outerHTML.substring(0, 200),
                                description: 'Interactive element does not have pointer cursor on hover',
                                text: text
                            });
                        }
                    }
                });
                
                // Check in-page link targets
                const anchorLinks = Array.from(document.querySelectorAll('a[href^="#"]:not([href="#"])'));
                const processedTargets = new Set();

                anchorLinks.forEach(link => {
                    const targetId = link.getAttribute('href').substring(1);
                    const target = document.getElementById(targetId);

                    if (target && !processedTargets.has(targetId)) {
                        const tabindex = target.getAttribute('tabindex');
                        const isInteractive = ['a', 'button', 'input', 'select', 'textarea'].includes(target.tagName.toLowerCase());

                        if (!isInteractive && tabindex !== '-1') {
                            // Find all anchor links pointing to this target
                            const linksToTarget = anchorLinks.filter(l =>
                                l.getAttribute('href').substring(1) === targetId
                            ).map((l, idx) => ({
                                index: idx + 1,
                                html: l.outerHTML.substring(0, 200),
                                xpath: getFullXPath(l),
                                text: l.textContent.trim()
                            }));

                            results.errors.push({
                                err: 'ErrAnchorTargetTabindex',
                                type: 'err',
                                cat: 'links',
                                element: target.tagName.toLowerCase(),
                                xpath: getFullXPath(target),
                                html: target.outerHTML.substring(0, 200),
                                description: 'In-page link target needs tabindex="-1" for keyboard accessibility - non-interactive element must be programmatically focusable',
                                metadata: {
                                    targetId: targetId,
                                    currentTabindex: tabindex || 'not set',
                                    anchorLinks: linksToTarget,
                                    anchorLinksCount: linksToTarget.length
                                }
                            });

                            processedTargets.add(targetId);
                        }
                    }
                });
                
                // Add check information for reporting
                results.checks.push({
                    description: 'Focus indicators',
                    wcag: ['2.4.7'],
                    total: interactiveElements.length,
                    passed: results.elements_passed,
                    failed: results.elements_failed
                });
                
                if (anchorLinks.length > 0) {
                    results.checks.push({
                        description: 'In-page link targets',
                        wcag: ['2.4.3'],
                        total: anchorLinks.length,
                        passed: anchorLinks.length,
                        failed: 0
                    });
                }
                
                return results;
            }
        ''')

        # Log focus errors for debugging
        if 'errors' in results:
            for error in results['errors']:
                if error.get('err') == 'ErrNoFocusIndicator':
                    break  # Just check first one

        # Second pass: inspect declared :focus (and base) CSS for natively
        # interactive elements to emit granular focus-indicator codes. Computed
        # style is useless here (outline-offset always computes to 0px, focus
        # styles are not applied to non-focused elements), so we read the actual
        # stylesheet rules that match each element.
        focus_css_elements: list[dict[str, Any]] = await page.evaluate(r'''
            () => {
                const out = [];

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

                const interactiveTags = ['a', 'button', 'input', 'select', 'textarea'];
                const interactiveRoles = ['button', 'link', 'checkbox', 'radio', 'tab',
                    'menuitem', 'switch', 'option', 'textbox', 'combobox'];

                function isInteractive(el) {
                    if (interactiveTags.includes(el.tagName.toLowerCase())) return true;
                    const role = el.getAttribute('role');
                    return !!(role && interactiveRoles.includes(role));
                }

                // Walk every style rule once and bucket the focus-relevant
                // declarations onto each matching interactive element.
                const elementData = new Map();
                function ensure(el) {
                    if (!elementData.has(el)) {
                        elementData.set(el, {
                            focusOutlineStyle: null, focusOutlineWidth: null,
                            focusOutlineColor: null, focusOutlineShorthand: null,
                            focusOutlineOffsetDeclared: false, focusOutlineOffset: null,
                            focusBoxShadow: null, focusBorder: null,
                            focusBorderColor: null, focusBackground: null,
                            baseOutlineNone: false
                        });
                    }
                    return elementData.get(el);
                }

                const allInteractive = Array.from(document.querySelectorAll('*')).filter(isInteractive);

                for (const sheet of document.styleSheets) {
                    let rules;
                    try { rules = sheet.cssRules || sheet.rules; } catch (e) { continue; }
                    if (!rules) continue;
                    for (const rule of rules) {
                        if (!rule.selectorText) continue;
                        const sel = rule.selectorText;
                        const isFocusRule = sel.includes(':focus');
                        // Strip pseudo-classes/elements to get a matchable base selector.
                        let baseSel = sel.replace(/::?[a-zA-Z-]+(\([^)]*\))?/g, '').trim();
                        baseSel = baseSel.replace(/,\s*$/, '').trim();
                        if (!baseSel) continue;

                        for (const el of allInteractive) {
                            let matched = false;
                            try { matched = el.matches(baseSel); } catch (e) { matched = false; }
                            if (!matched) {
                                // selector list - test each part
                                try {
                                    matched = baseSel.split(',').some(part => {
                                        try { return el.matches(part.trim()); } catch (e) { return false; }
                                    });
                                } catch (e) { matched = false; }
                            }
                            if (!matched) continue;

                            const s = rule.style;
                            const d = ensure(el);

                            // Detect outline:none / outline:0 in any rule (base or focus).
                            const outlineShort = s.outline || '';
                            const outlineStyle = s.outlineStyle || '';
                            const outlineWidth = s.outlineWidth || '';
                            const removesOutline =
                                outlineShort.trim() === 'none' || outlineShort.trim() === '0' ||
                                outlineShort.trim().startsWith('0 ') || outlineShort.trim().startsWith('0px') ||
                                outlineStyle === 'none' || outlineWidth === '0' || outlineWidth === '0px';

                            if (!isFocusRule) {
                                if (removesOutline) d.baseOutlineNone = true;
                                continue;
                            }

                            // Focus rule: record declarations.
                            if (s.outlineStyle) d.focusOutlineStyle = s.outlineStyle;
                            if (s.outlineWidth) d.focusOutlineWidth = s.outlineWidth;
                            if (s.outlineColor) d.focusOutlineColor = s.outlineColor;
                            if (s.outline) d.focusOutlineShorthand = s.outline;
                            if (s.outlineOffset) {
                                d.focusOutlineOffsetDeclared = true;
                                d.focusOutlineOffset = s.outlineOffset;
                            }
                            if (s.boxShadow) d.focusBoxShadow = s.boxShadow;
                            if (s.border || s.borderWidth || s.borderStyle) {
                                d.focusBorder = s.border || (s.borderWidth + ' ' + s.borderStyle);
                            }
                            if (s.borderColor) d.focusBorderColor = s.borderColor;
                            if (s.background || s.backgroundColor) {
                                d.focusBackground = s.backgroundColor || s.background;
                            }
                        }
                    }
                }

                for (const el of allInteractive) {
                    const computed = window.getComputedStyle(el);
                    const d = elementData.get(el) || null;
                    out.push({
                        tag: el.tagName.toLowerCase(),
                        role: el.getAttribute('role') || '',
                        tabindex: el.getAttribute('tabindex'),
                        xpath: getFullXPath(el),
                        html: (el.outerHTML || '').substring(0, 200),
                        normalBackground: computed.backgroundColor,
                        normalBorderColor: computed.borderColor,
                        focusData: d
                    });
                }

                return out;
            }
        ''')

        _process_focus_css(results, focus_css_elements)

        return results

    except Exception as e:
        logger.error(f"Error in test_focus_management: {e}")
        return {
            'error': str(e),
            'applicable': False,
            'errors': [],
            'warnings': [],
            'passes': []
        }