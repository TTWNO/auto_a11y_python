"""
Page-level touchpoint test module
Tests page-level accessibility including page titles, responsive breakpoints, etc.
"""

from __future__ import annotations

from typing import Any
import logging

from playwright.async_api import Page

logger = logging.getLogger(__name__)

TEST_DOCUMENTATION = {
    "testName": "Page-Level Accessibility Tests",
    "touchpoint": "page",
    "description": "Tests page-level accessibility including page titles, responsive breakpoints, and other page-wide concerns.",
    "version": "2.1.0",
    "wcagCriteria": ["2.4.2", "1.4.10", "1.4.4", "1.3.4", "2.5.8", "1.4.12"],
    "tests": [
        {
            "id": "page-title",
            "name": "Page Title",
            "description": "Checks that the page has a descriptive title element.",
            "impact": "critical",
            "wcagCriteria": ["2.4.2"],
        },
        {
            "id": "responsive-breakpoints",
            "name": "CSS Media Query Breakpoints",
            "description": "Identifies responsive breakpoints defined in CSS @media rules to guide testing at different viewport widths.",
            "impact": "info",
            "wcagCriteria": ["1.4.10"],
        },
        {
            "id": "viewport-zoom",
            "name": "Viewport Zoom Disabled",
            "description": "Detects a viewport meta tag that disables or restricts pinch/zoom (user-scalable=no or maximum-scale<2).",
            "impact": "high",
            "wcagCriteria": ["1.4.4", "1.4.10"],
        },
        {
            "id": "orientation-locked",
            "name": "Orientation Locked",
            "description": "Detects CSS that hides primary content in one orientation, locking the page to a single orientation.",
            "impact": "high",
            "wcagCriteria": ["1.3.4"],
        },
        {
            "id": "target-size",
            "name": "Target Size Too Small",
            "description": "Detects interactive targets rendered smaller than the 24x24 CSS pixel minimum.",
            "impact": "high",
            "wcagCriteria": ["2.5.8"],
        },
        {
            "id": "text-spacing",
            "name": "Text Spacing Restricted",
            "description": "Detects negative letter/word spacing or clipped fixed-height text that breaks user text-spacing adjustments.",
            "impact": "medium",
            "wcagCriteria": ["1.4.12"],
        }
    ]
}

async def test_page(page: Page) -> dict[str, Any]:
    """
    Test page-level accessibility including page title and responsive breakpoints

    Args:
        page: Playwright Page object

    Returns:
        Dictionary containing test results
    """
    results: dict[str, Any] = {
            'applicable': True,
        'errors': [],
        'warnings': [],
        'passes': [],
        'discovery': [],
        'elements_tested': 0,
        'elements_passed': 0,
        'elements_failed': 0,
        'test_name': 'page',
        'checks': []
    }

    try:
        # Get title length limit from config (default 60)
        title_length_limit = 60
        try:
            config_limit = await page.evaluate('() => window.a11yConfig && window.a11yConfig.titleLengthLimit')
            if config_limit:
                title_length_limit = config_limit
        except Exception as config_error:
            # Narrow to Exception so KeyboardInterrupt/SystemExit are not
            # swallowed; fall back to the default title-length limit.
            logger.debug(f"Could not read titleLengthLimit from page config: {config_error}")

        # Execute JavaScript to get page title info
        title_data = await page.evaluate('''
            () => {
                const titleElement = document.querySelector('title');
                const allTitles = document.querySelectorAll('head > title');
                
                return {
                    hasTitleElement: !!titleElement,
                    titleText: titleElement ? titleElement.textContent.trim() : '',
                    titleCount: allTitles.length
                };
            }
        ''')

        results['elements_tested'] += 1

        # Test 1: ErrNoPageTitle - No title element
        if not title_data['hasTitleElement']:
            results['errors'].append({
                'err': 'ErrNoPageTitle',
                'type': 'err',
                'cat': 'page',
                'element': 'head',
                'xpath': '/html/head',
                'html': '<head>',
                'description': 'Page is missing a title element'
            })
            results['elements_failed'] += 1

        # Test 2: ErrEmptyPageTitle - Empty title
        elif not title_data['titleText']:
            results['errors'].append({
                'err': 'ErrEmptyPageTitle',
                'type': 'err',
                'cat': 'page',
                'element': 'title',
                'xpath': '/html/head/title',
                'html': '<title></title>',
                'description': 'Page title element is empty'
            })
            results['elements_failed'] += 1

        # Test 3: WarnPageTitleTooShort - Title too short (< 5 chars)
        elif len(title_data['titleText']) < 5:
            results['warnings'].append({
                'err': 'WarnPageTitleTooShort',
                'type': 'warn',
                'cat': 'page',
                'element': 'title',
                'xpath': '/html/head/title',
                'html': f'<title>{title_data["titleText"]}</title>',
                'description': f'Page title is too short ({len(title_data["titleText"])} characters)',
                'found': title_data['titleText'],
                'length': len(title_data['titleText'])
            })
            # A present-but-short title is the same situation as a present-but-long
            # title (Test 4): the element exists, so count it as passed for both
            # branches to keep pass/fail accounting consistent.
            results['elements_passed'] += 1
            results['passes'].append({
                'check': 'page_title',
                'title': title_data['titleText'],
                'xpath': '/html/head/title',
                'wcag': ['2.4.2'],
                'reason': 'Page has title (but too short)'
            })

        # Test 4: WarnPageTitleTooLong - Title too long
        elif len(title_data['titleText']) > title_length_limit:
            results['warnings'].append({
                'err': 'WarnPageTitleTooLong',
                'type': 'warn',
                'cat': 'page',
                'element': 'title',
                'xpath': '/html/head/title',
                'html': f'<title>{title_data["titleText"][:50]}...</title>',
                'description': f'Page title exceeds {title_length_limit} characters ({len(title_data["titleText"])} characters)',
                'found': title_data['titleText'],
                'length': len(title_data['titleText']),
                'limit': title_length_limit
            })
            results['elements_passed'] += 1
            results['passes'].append({
                'check': 'page_title',
                'title': title_data['titleText'][:title_length_limit] + '...',
                'xpath': '/html/head/title',
                'wcag': ['2.4.2'],
                'reason': 'Page has title (but too long)'
            })

        # Title is valid
        else:
            results['elements_passed'] += 1
            results['passes'].append({
                'check': 'page_title',
                'title': title_data['titleText'],
                'xpath': '/html/head/title',
                'wcag': ['2.4.2'],
                'reason': 'Page has descriptive title'
            })

        # Test 5: ErrMultiplePageTitles - Multiple title elements
        if title_data['titleCount'] > 1:
            results['errors'].append({
                'err': 'ErrMultiplePageTitles',
                'type': 'err',
                'cat': 'page',
                'element': 'head',
                'xpath': '/html/head',
                'html': '<head>',
                'description': f'Page has {title_data["titleCount"]} title elements (should have exactly one)',
                'count': title_data['titleCount']
            })

        # Add check summary
        results['checks'].append({
            'description': 'Page title',
            'wcag': ['2.4.2'],
            'total': 1,
            'passed': results['elements_passed'],
            'failed': results['elements_failed']
        })

        # Discovery: Responsive breakpoints
        breakpoint_data = await page.evaluate('''
            () => {
                const breakpoints = new Set();

                for (const sheet of document.styleSheets) {
                    try {
                        if (sheet.cssRules) {
                            for (const rule of sheet.cssRules) {
                                if (rule instanceof CSSMediaRule) {
                                    const media = rule.media.mediaText;
                                    const widthMatches = media.match(/(?:min-width|max-width):\\s*(\\d+)px/g);

                                    if (widthMatches) {
                                        widthMatches.forEach(match => {
                                            const value = parseInt(match.match(/(\\d+)px/)[1]);
                                            breakpoints.add(value);
                                        });
                                    }
                                }
                            }
                        }
                    } catch (e) {
                        // Skip stylesheets we can't access (CORS)
                    }
                }

                return Array.from(breakpoints).sort((a, b) => a - b);
            }
        ''')

        if breakpoint_data and len(breakpoint_data) > 0:
            results['discovery'].append({
                'err': 'DiscoResponsiveBreakpoints',
                'type': 'disco',
                'cat': 'page',
                'element': 'html',
                'xpath': '/html',
                'html': '<html>',
                'description': f'Page defines {len(breakpoint_data)} responsive breakpoint(s): {", ".join(map(str, breakpoint_data))}px',
                'metadata': {
                    'breakpointCount': len(breakpoint_data),
                    'breakpoints': ', '.join(map(str, breakpoint_data)),
                    'minBreakpoint': breakpoint_data[0],
                    'maxBreakpoint': breakpoint_data[-1]
                }
            })

        # Test: ErrViewportZoomDisabled (WCAG 1.4.4 Resize Text, 1.4.10 Reflow)
        # A viewport meta that disables pinch/zoom prevents low-vision users from enlarging content.
        zoom_data: dict[str, Any] = await page.evaluate('''
            () => {
                const meta = document.querySelector('meta[name="viewport"]');
                if (!meta) return {disabled: false};
                const content = (meta.getAttribute('content') || '');
                const lc = content.toLowerCase();
                const reasons = [];
                if (/user-scalable\\s*=\\s*(no|0)\\b/.test(lc)) reasons.push('user-scalable=no');
                const ms = lc.match(/maximum-scale\\s*=\\s*([0-9.]+)/);
                if (ms && parseFloat(ms[1]) < 2) reasons.push('maximum-scale=' + ms[1]);
                return {disabled: reasons.length > 0, reason: reasons.join(', '), content: content};
            }
        ''')
        if zoom_data.get('disabled'):
            results['errors'].append({
                'err': 'ErrViewportZoomDisabled',
                'type': 'err',
                'cat': 'page',
                'element': 'meta',
                'xpath': '/html/head/meta[@name="viewport"]',
                'html': f'<meta name="viewport" content="{zoom_data.get("content", "")[:120]}">',
                'description': f'Viewport meta tag disables or restricts zoom ({zoom_data.get("reason", "")})',
            })
            results['elements_failed'] += 1

        # Test: ErrOrientationLocked (WCAG 1.3.4 Orientation)
        # CSS that hides primary content in one orientation locks the page to a single orientation.
        orientation_data: dict[str, Any] = await page.evaluate('''
            () => {
                function hidesContent(styleRule) {
                    const sel = (styleRule.selectorText || '').toLowerCase();
                    const targetsRoot = /(^|[\\s,>+~])(\\*|html|body|main|:root)\\b/.test(sel)
                        || sel.includes('[role="main"]');
                    if (!targetsRoot) return false;
                    const d = (styleRule.style.display || '').toLowerCase();
                    const v = (styleRule.style.visibility || '').toLowerCase();
                    return d === 'none' || v === 'hidden';
                }
                for (const sheet of document.styleSheets) {
                    try {
                        for (const rule of (sheet.cssRules || [])) {
                            if (rule instanceof CSSMediaRule && /orientation/i.test(rule.media.mediaText)) {
                                for (const inner of (rule.cssRules || [])) {
                                    if (inner instanceof CSSStyleRule && hidesContent(inner)) {
                                        return {locked: true, media: rule.media.mediaText, selector: inner.selectorText};
                                    }
                                }
                            }
                        }
                    } catch (e) { /* CORS-restricted sheet */ }
                }
                return {locked: false};
            }
        ''')
        if orientation_data.get('locked'):
            results['errors'].append({
                'err': 'ErrOrientationLocked',
                'type': 'err',
                'cat': 'page',
                'element': 'body',
                'xpath': '/html/body',
                'html': '<body>',
                'description': (
                    "Content is hidden in one orientation via "
                    f"@media ({orientation_data.get('media', '')}) -> {orientation_data.get('selector', '')}; "
                    "the page is effectively locked to a single orientation"
                ),
            })
            results['elements_failed'] += 1

        # Test: ErrTargetSizeTooSmall (WCAG 2.5.8 Target Size (Minimum), 24x24 CSS px)
        small_targets: list[dict[str, Any]] = await page.evaluate('''
            () => {
                const MIN = 24;
                function xpath(el) {
                    if (el.id) return "//*[@id='" + el.id + "']";
                    const parts = [];
                    while (el && el.nodeType === 1 && el.tagName.toLowerCase() !== 'html') {
                        let ix = 1, sib = el.previousElementSibling;
                        while (sib) { if (sib.tagName === el.tagName) ix++; sib = sib.previousElementSibling; }
                        parts.unshift(el.tagName.toLowerCase() + '[' + ix + ']');
                        el = el.parentElement;
                    }
                    return '/html/' + parts.join('/');
                }
                const sel = 'a[href], button, input:not([type="hidden"]), select, textarea,'
                          + '[role="button"], [role="link"], [onclick], [tabindex]:not([tabindex="-1"])';
                const out = [];
                document.querySelectorAll(sel).forEach(el => {
                    const cs = getComputedStyle(el);
                    if (cs.display === 'none' || cs.visibility === 'hidden') return;
                    const r = el.getBoundingClientRect();
                    if (r.width === 0 && r.height === 0) return;
                    if (r.width < MIN || r.height < MIN) {
                        out.push({
                            tag: el.tagName.toLowerCase(),
                            w: Math.round(r.width), h: Math.round(r.height),
                            xpath: xpath(el),
                            text: (el.textContent || '').trim().slice(0, 40)
                        });
                    }
                });
                return out;
            }
        ''')
        for tgt in small_targets:
            results['errors'].append({
                'err': 'ErrTargetSizeTooSmall',
                'type': 'err',
                'cat': 'page',
                'element': tgt.get('tag', 'element'),
                'xpath': tgt.get('xpath', ''),
                'html': f'<{tgt.get("tag", "element")}>{tgt.get("text", "")}</{tgt.get("tag", "element")}>',
                'description': (
                    f'Interactive target is {tgt.get("w", 0)}x{tgt.get("h", 0)}px, '
                    'below the 24x24px minimum (WCAG 2.5.8)'
                ),
            })
            results['elements_failed'] += 1

        # Test: ErrTextSpacingRestricted (WCAG 1.4.12 Text Spacing) -- grouped under the fonts touchpoint
        # Negative letter/word spacing or clipped fixed-height text breaks user spacing adjustments.
        spacing_issues: list[dict[str, Any]] = await page.evaluate('''
            () => {
                function xpath(el) {
                    if (el.id) return "//*[@id='" + el.id + "']";
                    const parts = [];
                    while (el && el.nodeType === 1 && el.tagName.toLowerCase() !== 'html') {
                        let ix = 1, sib = el.previousElementSibling;
                        while (sib) { if (sib.tagName === el.tagName) ix++; sib = sib.previousElementSibling; }
                        parts.unshift(el.tagName.toLowerCase() + '[' + ix + ']');
                        el = el.parentElement;
                    }
                    return '/html/' + parts.join('/');
                }
                const out = [];
                document.querySelectorAll('body *').forEach(el => {
                    if (!(el.textContent || '').trim()) return;
                    const cs = getComputedStyle(el);
                    const ls = parseFloat(cs.letterSpacing);
                    const ws = parseFloat(cs.wordSpacing);
                    if ((!isNaN(ls) && ls < 0) || (!isNaN(ws) && ws < 0)) {
                        out.push({tag: el.tagName.toLowerCase(), xpath: xpath(el),
                                  reason: 'negative letter/word spacing prevents user spacing overrides'});
                        return;
                    }
                    const overflow = (cs.overflow + ' ' + cs.overflowY).toLowerCase();
                    if (/hidden|clip/.test(overflow) && el.clientHeight > 0 && el.scrollHeight > el.clientHeight + 2) {
                        out.push({tag: el.tagName.toLowerCase(), xpath: xpath(el),
                                  reason: 'fixed height with overflow:hidden clips text when spacing increases'});
                    }
                });
                return out;
            }
        ''')
        seen_spacing: set[str] = set()
        for issue in spacing_issues:
            key = issue.get('xpath', '')
            if key in seen_spacing:
                continue
            seen_spacing.add(key)
            results['errors'].append({
                'err': 'ErrTextSpacingRestricted',
                'type': 'err',
                'cat': 'fonts',
                'element': issue.get('tag', 'element'),
                'xpath': issue.get('xpath', ''),
                'html': f'<{issue.get("tag", "element")}>',
                'description': f'Text spacing cannot be adjusted: {issue.get("reason", "")}',
            })
            results['elements_failed'] += 1

        return results

    except Exception as e:
        logger.error(f"Error in test_page: {e}")
        return {
            'error': str(e),
            'applicable': False,
            'errors': [],
            'warnings': [],
            'discovery': [],
            'passes': [],
            'test_name': 'page'
        }
