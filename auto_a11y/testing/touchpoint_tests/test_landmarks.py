"""
Landmarks touchpoint test module
Evaluates webpage landmark structure to ensure proper semantic organization and navigation for screen reader users.
"""

from __future__ import annotations

from typing import Any
import logging

from playwright.async_api import Page

logger = logging.getLogger(__name__)

TEST_DOCUMENTATION = {
    "testName": "ARIA Landmark Analysis",
    "touchpoint": "landmarks",
    "description": "Evaluates webpage landmark structure to ensure proper semantic organization and navigation for screen reader users. This test checks for required landmarks, proper nesting, and appropriate labeling of content regions.",
    "version": "1.0.0",
    "wcagCriteria": ["1.3.1", "2.4.1"],
    "tests": [
        {
            "id": "main-landmark",
            "name": "Main Landmark Presence",
            "description": "Checks if the page has a main landmark that contains the primary content.",
            "impact": "high",
            "wcagCriteria": ["1.3.1", "2.4.1"],
        },
        {
            "id": "banner-landmark",
            "name": "Banner Landmark",
            "description": "Verifies that the page has a banner landmark for site-wide header content.",
            "impact": "medium",
            "wcagCriteria": ["1.3.1"],
        },
        {
            "id": "contentinfo-landmark",
            "name": "Contentinfo Landmark",
            "description": "Checks if the page has a contentinfo landmark for footer content.",
            "impact": "medium",
            "wcagCriteria": ["1.3.1"],
        },
        {
            "id": "content-in-landmarks",
            "name": "Content Within Landmarks",
            "description": "Evaluates whether all content on the page is contained within appropriate landmarks.",
            "impact": "medium",
            "wcagCriteria": ["1.3.1", "2.4.1"],
        },
        {
            "id": "duplicate-landmarks",
            "name": "Duplicate Landmark Identification",
            "description": "Checks if multiple landmarks of the same type have unique labels to distinguish them.",
            "impact": "high",
            "wcagCriteria": ["1.3.1", "2.4.1"],
        }
    ]
}

async def test_landmarks(page: Page) -> dict[str, Any]:
    """
    Test page landmark structure and requirements
    
    Args:
        page: Playwright Page object
        
    Returns:
        Dictionary containing test results with errors and warnings
    """
    try:
        # Execute JavaScript to analyze landmarks
        results: dict[str, Any] = await page.evaluate(r'''
            () => {
                const results = {
                    applicable: true,
                    errors: [],
                    warnings: [],
                    passes: [],
                    elements_tested: 0,
                    elements_passed: 0,
                    elements_failed: 0,
                    test_name: 'landmarks',
                    checks: []
                };

                // Detect page language for component tagging
                // Default to 'en' if no lang attribute is found
                const htmlElement = document.documentElement;
                const pageLang = (htmlElement.getAttribute('lang') || 'en').substring(0, 2).toLowerCase();

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
                
                // Get accessible name for landmarks
                function getLandmarkName(element) {
                    const ariaLabel = element.getAttribute('aria-label');
                    if (ariaLabel) return ariaLabel.trim();

                    const labelledBy = element.getAttribute('aria-labelledby');
                    if (labelledBy) {
                        const labelElements = labelledBy.split(' ')
                            .map(id => document.getElementById(id))
                            .filter(el => el);
                        if (labelElements.length > 0) {
                            return labelElements.map(el => el.textContent.trim()).join(' ');
                        }
                    }

                    // Check for heading as label for forms
                    if (element.tagName.toLowerCase() === 'form' || element.getAttribute('role') === 'form') {
                        const heading = element.querySelector('h1, h2, h3, h4, h5, h6');
                        if (heading) {
                            return heading.textContent.trim();
                        }
                    }

                    return null;
                }
                
                // Check if element is at top level for header/footer role detection
                function isTopLevelElement(element) {
                    if (element.parentElement === document.body) return true;

                    let parent = element.parentElement;
                    while (parent && parent !== document.body) {
                        const parentTag = parent.tagName.toLowerCase();
                        if (['article', 'aside', 'main', 'nav', 'section'].includes(parentTag)) {
                            return false;
                        }
                        parent = parent.parentElement;
                    }
                    return true;
                }
                
                // Find all landmarks
                const landmarkElements = [
                    ...document.querySelectorAll(
                        'main, [role="main"], header, [role="banner"], footer, [role="contentinfo"], ' +
                        'nav, [role="navigation"], [role="search"], search, form, [role="form"], ' +
                        'aside, [role="complementary"], section[aria-label], section[aria-labelledby], ' +
                        '[role="region"]'
                    )
                ];
                
                if (landmarkElements.length === 0) {
                    results.applicable = false;
                    results.not_applicable_reason = 'No landmarks found on the page';
                    return results;
                }
                
                results.elements_tested = landmarkElements.length;
                
                // Track landmarks found
                const landmarkCounts = {};
                const landmarkNames = {};
                const landmarkElements_byType = {}; // Track all elements by type
                let hasMain = false;
                let hasBanner = false;
                let hasContentinfo = false;

                landmarkElements.forEach(element => {
                    const tag = element.tagName.toLowerCase();
                    let role = element.getAttribute('role');
                    const isTopLevel = isTopLevelElement(element);
                    
                    // Handle implicit roles with special cases for header and footer
                    if (!role) {
                        switch (tag) {
                            case 'main': 
                                role = 'main'; 
                                break;
                            case 'header': 
                                role = isTopLevel ? 'banner' : 'region';
                                break;
                            case 'footer': 
                                role = isTopLevel ? 'contentinfo' : 'region';
                                break;
                            case 'nav': 
                                role = 'navigation'; 
                                break;
                            case 'aside': 
                                role = 'complementary'; 
                                break;
                            case 'form': 
                                role = 'form'; 
                                break;
                            case 'section': 
                                role = 'region'; 
                                break;
                        }
                    }
                    
                    const name = getLandmarkName(element);

                    // Track landmark counts and names
                    landmarkCounts[role] = (landmarkCounts[role] || 0) + 1;
                    if (name) {
                        if (!landmarkNames[role]) landmarkNames[role] = new Set();
                        landmarkNames[role].add(name);
                    }

                    // Store elements by type for later reporting
                    if (!landmarkElements_byType[role]) {
                        landmarkElements_byType[role] = [];
                    }
                    landmarkElements_byType[role].push({
                        element: element,
                        name: name,
                        xpath: getFullXPath(element),
                        html: element.outerHTML.substring(0, 200),
                        tag: tag
                    });

                    // Update summary flags
                    if (role === 'main') hasMain = true;
                    if (role === 'banner' || (tag === 'header' && isTopLevel)) hasBanner = true;
                    if (role === 'contentinfo' || (tag === 'footer' && isTopLevel)) hasContentinfo = true;
                });

                // Now check for duplicate landmarks without unique names
                Object.keys(landmarkElements_byType).forEach(role => {
                    const elements = landmarkElements_byType[role];

                    // Check if there are multiple of this type without unique names
                    if (elements.length > 1) {
                        const namedElements = elements.filter(el => el.name);
                        const unnamedElements = elements.filter(el => !el.name);

                        // If there are multiple and any lack names, report each unnamed one
                        if (unnamedElements.length > 0) {
                            unnamedElements.forEach(el => {
                                // Build array of all instances for context
                                const allInstancesHtml = elements.map((instance, idx) => {
                                    return {
                                        index: idx + 1,
                                        html: instance.html,
                                        xpath: instance.xpath,
                                        name: instance.name || '(no accessible name)',
                                        tag: instance.tag
                                    };
                                });

                                results.errors.push({
                                    err: 'ErrDuplicateLandmarkWithoutName',
                                    type: 'err',
                                    cat: 'landmarks',
                                    element: el.tag,
                                    xpath: el.xpath,
                                    html: el.html,
                                    description: `Found ${elements.length} ${role} landmarks, but this one lacks a unique accessible name`,
                                    role: role,
                                    totalCount: elements.length,
                                    allInstances: allInstancesHtml
                                });
                                results.elements_failed++;
                            });
                        } else {
                            // All are named, count as passed
                            elements.forEach(() => results.elements_passed++);
                        }
                    } else {
                        // Only one of this type, count as passed
                        results.elements_passed++;
                    }

                    // Check for regions without labels (forms are checked separately below)
                    elements.forEach(el => {
                        if (role === 'region' && !el.name) {
                            results.warnings.push({
                                err: 'WarnUnlabelledRegion',
                                type: 'warn',
                                cat: 'landmarks',
                                element: el.tag,
                                xpath: el.xpath,
                                html: el.html,
                                description: 'Region landmark should have an accessible name'
                            });
                        }
                    });
                });

                // Check for duplicate region labels (multiple regions with same accessible name)
                if (landmarkElements_byType['region'] && landmarkElements_byType['region'].length > 1) {
                    const regionElements = landmarkElements_byType['region'];
                    const regionsByLabel = {};

                    // Group regions by their accessible name
                    regionElements.forEach(region => {
                        if (region.name) {
                            if (!regionsByLabel[region.name]) {
                                regionsByLabel[region.name] = [];
                            }
                            regionsByLabel[region.name].push(region);
                        }
                    });

                    // Check for duplicates
                    Object.keys(regionsByLabel).forEach(label => {
                        const regions = regionsByLabel[label];
                        if (regions.length > 1) {
                            // Multiple regions with same label - report each as error
                            regions.forEach(region => {
                                results.errors.push({
                                    err: 'ErrDuplicateLabelForRegionLandmark',
                                    type: 'err',
                                    cat: 'landmarks',
                                    element: region.tag,
                                    xpath: region.xpath,
                                    html: region.html,
                                    description: `Multiple region landmarks share the same accessible name "${label}" (${regions.length} total)`,
                                    regionLabel: label,
                                    duplicateCount: regions.length
                                });
                                results.elements_failed++;
                            });
                        } else {
                            // Single region with unique label - report as pass
                            results.passes.push({
                                check: 'region_unique_label',
                                element: regions[0].tag,
                                xpath: regions[0].xpath,
                                wcag: ['1.3.1', '2.4.6'],
                                reason: `Region landmark has unique label: "${label}"`
                            });
                            results.elements_passed++;
                        }
                    });
                }

                // Get body start for context when landmarks are missing
                const bodyElement = document.body;
                const bodyStart = bodyElement ? bodyElement.outerHTML.substring(0, 500) : '<body>';

                // Check for missing required landmarks
                if (!hasMain) {
                    results.errors.push({
                        err: 'ErrMissingMainLandmark',
                        type: 'err',
                        cat: 'landmarks',
                        element: 'body',
                        xpath: '/html/body',
                        html: bodyStart,
                        description: 'Page is missing a main landmark - add <main> element or role="main" to identify primary content'
                    });
                    results.elements_failed++;
                }

                if (!hasBanner) {
                    results.warnings.push({
                        err: 'WarnMissingBannerLandmark',
                        type: 'warn',
                        cat: 'landmarks',
                        element: 'body',
                        xpath: '/html/body',
                        html: bodyStart,
                        description: 'Page is missing a banner landmark - add <header> element at top level or role="banner"'
                    });
                }

                if (!hasContentinfo) {
                    results.warnings.push({
                        err: 'WarnMissingContentinfoLandmark',
                        type: 'warn',
                        cat: 'landmarks',
                        element: 'body',
                        xpath: '/html/body',
                        html: bodyStart,
                        description: 'Page is missing a contentinfo landmark - add <footer> element at top level or role="contentinfo"'
                    });
                }
                
                // Find content outside landmarks
                const walker = document.createTreeWalker(
                    document.body,
                    NodeFilter.SHOW_TEXT,
                    {
                        acceptNode: function(node) {
                            if (node.textContent.trim().length === 0) return NodeFilter.FILTER_REJECT;

                            // Check if node is within a landmark
                            let current = node.parentElement;
                            while (current && current !== document.body) {
                                // Exclude if within a landmark
                                if (current.matches(
                                    'main, [role="main"], header, [role="banner"], ' +
                                    'footer, [role="contentinfo"], nav, [role="navigation"], ' +
                                    '[role="search"], form, [role="form"], aside, ' +
                                    '[role="complementary"], section[aria-label], ' +
                                    'section[aria-labelledby], [role="region"][aria-label], ' +
                                    '[role="region"][aria-labelledby]'
                                )) {
                                    return NodeFilter.FILTER_REJECT;
                                }

                                // Exclude if within hidden content
                                if (current.getAttribute('aria-hidden') === 'true') {
                                    return NodeFilter.FILTER_REJECT;
                                }
                                const style = window.getComputedStyle(current);
                                if (style.display === 'none' || style.visibility === 'hidden') {
                                    return NodeFilter.FILTER_REJECT;
                                }

                                // Exclude if within script, noscript, or style elements
                                if (current.matches('script, noscript, style')) {
                                    return NodeFilter.FILTER_REJECT;
                                }

                                // Exclude if within skip link (typically first link in body with skip/jump class)
                                if (current.matches('a[href^="#"][class*="skip"], a[href^="#"][class*="jump"]')) {
                                    // Only exclude if it's one of the first few elements in body
                                    const bodyChildren = Array.from(document.body.children);
                                    const linkIndex = bodyChildren.findIndex(child => child.contains(current));
                                    if (linkIndex >= 0 && linkIndex < 3) {
                                        return NodeFilter.FILTER_REJECT;
                                    }
                                }

                                current = current.parentElement;
                            }
                            return NodeFilter.FILTER_ACCEPT;
                        }
                    }
                );
                
                let contentOutsideLandmarks = 0;
                const elementsOutsideLandmarks = new Map(); // Track unique immediate body children
                
                while (walker.nextNode()) {
                    contentOutsideLandmarks++;
                    
                    // Find the immediate child of body that contains this text node
                    let current = walker.currentNode.parentElement;
                    while (current && current.parentElement !== document.body) {
                        current = current.parentElement;
                    }
                    
                    if (current && current.parentElement === document.body) {
                        // Track this immediate body child
                        if (!elementsOutsideLandmarks.has(current)) {
                            const tagName = current.tagName.toLowerCase();
                            const id = current.id ? `#${current.id}` : '';
                            const className = current.className ? `.${current.className.toString().split(' ').join('.')}` : '';
                            
                            // Get xpath for this element
                            let xpath = '';
                            let el = current;
                            while (el && el !== document) {
                                let idx = 1;
                                for (let sib = el.previousElementSibling; sib; sib = sib.previousElementSibling) {
                                    if (sib.tagName === el.tagName) idx++;
                                }
                                xpath = `/${el.tagName.toLowerCase()}[${idx}]${xpath}`;
                                el = el.parentElement;
                            }
                            xpath = '/html' + xpath.substring(xpath.indexOf('/body'));
                            
                            // Get text preview (first 100 chars of text content)
                            const textPreview = current.textContent.trim().substring(0, 100);
                            
                            // Get HTML preview (opening tag only)
                            const htmlPreview = current.outerHTML.substring(0, Math.min(200, current.outerHTML.indexOf('>') + 1));
                            
                            elementsOutsideLandmarks.set(current, {
                                element: tagName,
                                selector: `${tagName}${id}${className}`.substring(0, 50),
                                xpath: xpath,
                                text: textPreview,
                                html: htmlPreview
                            });
                        }
                    }
                }
                
                if (contentOutsideLandmarks > 0) {
                    const instances = Array.from(elementsOutsideLandmarks.values()).map((inst, idx) => ({
                        index: idx + 1,
                        ...inst
                    }));
                    
                    results.errors.push({
                        err: 'ErrContentOutsideLandmarks',
                        type: 'err',
                        cat: 'landmarks',
                        element: 'body',
                        description: `${instances.length} element(s) with content outside landmark regions`,
                        metadata: {
                            textNodeCount: contentOutsideLandmarks,
                            elementCount: instances.length,
                            allInstances: instances
                        }
                    });
                    results.elements_failed++;

                    // GRANULAR: emit a per-element issue for each top-level body child whose
                    // content sits outside any landmark. These mirror ErrContentOutsideLandmarks
                    // but at element granularity so the count matches the expected per-element
                    // violation counts in the fixtures.
                    instances.forEach(inst => {
                        results.errors.push({
                            err: 'ErrElementNotContainedInALandmark',
                            type: 'err',
                            cat: 'landmarks',
                            element: inst.element,
                            xpath: inst.xpath,
                            html: inst.html,
                            description: 'Element with meaningful content is not contained within any landmark region'
                        });
                        results.warnings.push({
                            err: 'WarnElementNotInLandmark',
                            type: 'warn',
                            cat: 'landmarks',
                            element: inst.element,
                            xpath: inst.xpath,
                            html: inst.html,
                            description: 'Element with content sits outside any landmark region, reducing navigability for landmark users'
                        });
                        results.warnings.push({
                            err: 'WarnContentOutsideLandmarks',
                            type: 'warn',
                            cat: 'landmarks',
                            element: inst.element,
                            xpath: inst.xpath,
                            html: inst.html,
                            description: 'Content exists outside of landmark regions'
                        });
                    });
                }

                // ============================================================
                // GRANULAR PER-LANDMARK-TYPE CHECKS
                // Generalises the per-type labelling logic (previously only
                // implemented for Form / Region) to every landmark type:
                // Banner, Complementary, Contentinfo, Main, Nav, Region, Search.
                // ============================================================

                // Resolve the accessible-name details of a landmark element.
                // Returns: { hasAriaLabel, ariaLabelBlank, hasLabelledby,
                //            labelledbyBlank, name, hasName }
                function getNameDetails(el) {
                    const ariaLabel = el.getAttribute('aria-label');
                    const ariaLabelledby = el.getAttribute('aria-labelledby');
                    const hasAriaLabel = ariaLabel !== null;
                    const hasLabelledby = ariaLabelledby !== null && ariaLabelledby.trim() !== '';
                    let ariaLabelBlank = false;
                    let labelledbyBlank = false;
                    let name = '';

                    if (hasAriaLabel) {
                        if (ariaLabel.trim()) {
                            name = ariaLabel.trim();
                        } else {
                            ariaLabelBlank = true;
                        }
                    }

                    if (!name && hasLabelledby) {
                        const refIds = ariaLabelledby.trim().split(/\s+/);
                        const texts = refIds
                            .map(id => document.getElementById(id))
                            .filter(node => node)
                            .map(node => node.textContent.trim())
                            .filter(t => t);
                        if (texts.length > 0) {
                            name = texts.join(' ');
                        } else {
                            labelledbyBlank = true;
                        }
                    }

                    return {
                        hasAriaLabel: hasAriaLabel,
                        ariaLabelBlank: ariaLabelBlank,
                        hasLabelledby: hasLabelledby,
                        labelledbyBlank: labelledbyBlank,
                        name: name,
                        hasName: name.length > 0
                    };
                }

                // Determine the landmark role of an element using the same
                // implicit-role rules as the main collector above.
                function resolveRole(el) {
                    const explicit = el.getAttribute('role');
                    if (explicit) return explicit;
                    const tag = el.tagName.toLowerCase();
                    switch (tag) {
                        case 'main': return 'main';
                        case 'header': return isTopLevelElement(el) ? 'banner' : null;
                        case 'footer': return isTopLevelElement(el) ? 'contentinfo' : null;
                        case 'nav': return 'navigation';
                        case 'aside': return 'complementary';
                        case 'form': return 'form';
                        case 'search': return 'search';
                        case 'section':
                            // A <section> is a region landmark only when it has an accessible
                            // name (aria-label/aria-labelledby); otherwise it is generic.
                            return (el.hasAttribute('aria-label') || el.hasAttribute('aria-labelledby')) ? 'region' : null;
                        default: return null;
                    }
                }

                // Is this element nested inside another landmark of any type?
                // Returns the nearest landmark ancestor element, or null.
                const landmarkRoleSelector =
                    'main, [role="main"], header, [role="banner"], footer, [role="contentinfo"], ' +
                    'nav, [role="navigation"], [role="search"], search, form, [role="form"], ' +
                    'aside, [role="complementary"], [role="region"], ' +
                    'section[aria-label], section[aria-labelledby]';

                function nearestLandmarkAncestor(el) {
                    let parent = el.parentElement;
                    while (parent && parent !== document.body) {
                        if (parent.matches(landmarkRoleSelector)) {
                            // A <header>/<footer> only counts as a landmark when top-level.
                            const ptag = parent.tagName.toLowerCase();
                            if ((ptag === 'header' || ptag === 'footer') && !parent.getAttribute('role')) {
                                if (!isTopLevelElement(parent)) {
                                    parent = parent.parentElement;
                                    continue;
                                }
                            }
                            // A bare <section> with no label is not a landmark.
                            if (ptag === 'section' && !parent.getAttribute('role') &&
                                !parent.getAttribute('aria-label') && !parent.getAttribute('aria-labelledby')) {
                                parent = parent.parentElement;
                                continue;
                            }
                            return parent;
                        }
                        parent = parent.parentElement;
                    }
                    return null;
                }

                function isHiddenElement(el) {
                    if (el.getAttribute('aria-hidden') === 'true') return true;
                    if (el.hasAttribute('hidden')) return true;
                    const style = window.getComputedStyle(el);
                    return style.display === 'none' || style.visibility === 'hidden';
                }

                // Per-type configuration. landmarkType is the canonical {Type} used
                // in the issue codes. role is the ARIA role key. genericWords are
                // the redundant words that trigger AccessibleNameUses* warnings.
                const landmarkTypeConfigs = [
                    { type: 'Banner', role: 'banner', selector: 'header, [role="banner"]', mustBeTopLevel: true, mustBeUnique: true, genericWords: ['banner'] },
                    { type: 'Complementary', role: 'complementary', selector: 'aside, [role="complementary"]', mustBeTopLevel: true, mustBeUnique: false, genericWords: ['complementary'] },
                    { type: 'Contentinfo', role: 'contentinfo', selector: 'footer, [role="contentinfo"]', mustBeTopLevel: true, mustBeUnique: true, genericWords: ['contentinfo', 'content info'] },
                    { type: 'Main', role: 'main', selector: 'main, [role="main"]', mustBeTopLevel: true, mustBeUnique: true, genericWords: [] },
                    { type: 'Nav', role: 'navigation', selector: 'nav, [role="navigation"]', mustBeTopLevel: false, mustBeUnique: false, genericWords: ['navigation'] },
                    { type: 'Region', role: 'region', selector: '[role="region"], section[aria-label], section[aria-labelledby]', mustBeTopLevel: false, mustBeUnique: false, genericWords: ['region', 'navigation'] },
                    { type: 'Search', role: 'search', selector: 'search, [role="search"]', mustBeTopLevel: false, mustBeUnique: false, genericWords: [] }
                ];

                landmarkTypeConfigs.forEach(cfg => {
                    // Collect the candidate elements for this type, filtering out
                    // header/footer that are not top-level (those are not banner/contentinfo).
                    let candidates = Array.from(document.querySelectorAll(cfg.selector));
                    candidates = candidates.filter(el => resolveRole(el) === cfg.role);

                    // Build per-element name details once.
                    const items = candidates.map(el => {
                        const details = getNameDetails(el);
                        return {
                            el: el,
                            tag: el.tagName.toLowerCase(),
                            xpath: getFullXPath(el),
                            html: el.outerHTML.substring(0, 200),
                            details: details
                        };
                    });

                    const total = items.length;
                    const labeledItems = items.filter(it => it.details.hasName);
                    const unlabeledItems = items.filter(it => !it.details.hasName);

                    items.forEach(it => {
                        const d = it.details;

                        // ERROR: aria-label present but blank/whitespace, or
                        // aria-labelledby that resolves to blank text.
                        if (d.ariaLabelBlank || (d.labelledbyBlank && !d.hasAriaLabel)) {
                            results.errors.push({
                                err: 'Err' + cfg.type + 'LandmarkAccessibleNameIsBlank',
                                type: 'err',
                                cat: 'landmarks',
                                element: it.tag,
                                xpath: it.xpath,
                                html: it.html,
                                description: cfg.type + ' landmark has an empty or whitespace-only accessible name'
                            });
                            results.elements_failed++;
                        }

                        // ERROR: both aria-label and aria-labelledby present.
                        if (d.hasAriaLabel && d.hasLabelledby) {
                            results.errors.push({
                                err: 'Err' + cfg.type + 'LandmarkHasAriaLabelAndAriaLabelledByAttrs',
                                type: 'err',
                                cat: 'landmarks',
                                element: it.tag,
                                xpath: it.xpath,
                                html: it.html,
                                description: cfg.type + ' landmark has both aria-label and aria-labelledby. aria-labelledby takes precedence, making aria-label redundant.'
                            });
                            results.elements_failed++;
                        }

                        // ERROR: landmark nested inside another landmark. Only flag the
                        // types that are required to be top-level (banner, main, contentinfo,
                        // complementary); nav/region/search may validly nest inside others,
                        // and emitting Err{Nav,Region,Search}LandmarkMayNotBeChildOfAnotherLandmark
                        // would be a false positive on an uncatalogued code.
                        const ancestor = nearestLandmarkAncestor(it.el);
                        if (cfg.mustBeTopLevel && ancestor) {
                            results.errors.push({
                                err: 'Err' + cfg.type + 'LandmarkMayNotBeChildOfAnotherLandmark',
                                type: 'err',
                                cat: 'landmarks',
                                element: it.tag,
                                xpath: it.xpath,
                                html: it.html,
                                description: cfg.type + ' landmark must not be nested inside another landmark (found inside <' + ancestor.tagName.toLowerCase() + '>)'
                            });
                            results.elements_failed++;
                        }

                        // WARNING: accessible name redundantly repeats the landmark word.
                        if (d.hasName) {
                            const lowerName = d.name.toLowerCase();
                            const usesGeneric = cfg.genericWords.some(w => lowerName.indexOf(w) !== -1);
                            if (usesGeneric) {
                                // The "uses {Word}" code is per-type; build its suffix.
                                let word = cfg.type;
                                if (cfg.type === 'Nav') word = 'Navigation';
                                results.warnings.push({
                                    err: 'Warn' + cfg.type + 'LandmarkAccessibleNameUses' + word,
                                    type: 'warn',
                                    cat: 'landmarks',
                                    element: it.tag,
                                    xpath: it.xpath,
                                    html: it.html,
                                    description: cfg.type + ' landmark accessible name "' + d.name + '" redundantly includes the landmark type. Screen readers already announce the role.'
                                });
                            }
                        }

                        // HEADING relationship warnings (nav / region / complementary etc.)
                        // Only meaningful when the landmark carries an explicit label.
                        const headingEl = it.el.querySelector('h1, h2, h3, h4, h5, h6');
                        if (headingEl) {
                            if (d.hasLabelledby) {
                                // Does aria-labelledby reference this heading?
                                const refIds = (it.el.getAttribute('aria-labelledby') || '').trim().split(/\s+/);
                                const headingId = headingEl.getAttribute('id');
                                const referencesHeading = headingId && refIds.indexOf(headingId) !== -1;
                                if (!referencesHeading) {
                                    results.warnings.push({
                                        err: 'WarnHeadingFoundInLandmarkButIsLabelledByAnAriaLabelledBy',
                                        type: 'warn',
                                        cat: 'landmarks',
                                        element: it.tag,
                                        xpath: it.xpath,
                                        html: it.html,
                                        description: cfg.type + ' landmark contains a heading but its aria-labelledby references a different element, creating a mismatch between the visible heading and the announced label'
                                    });
                                }
                            } else if (d.hasAriaLabel && !d.ariaLabelBlank) {
                                results.warnings.push({
                                    err: 'WarnHeadingFoundInsideLandmarkButDoesntLabelLandmark',
                                    type: 'warn',
                                    cat: 'landmarks',
                                    element: it.tag,
                                    xpath: it.xpath,
                                    html: it.html,
                                    description: cfg.type + ' landmark contains a heading but uses aria-label instead. Consider aria-labelledby referencing the heading for consistency.'
                                });
                            }
                        }
                    });

                    // ERROR: more than one of a must-be-unique landmark type
                    // (Banner, Main, Contentinfo) — flag each instance.
                    if (cfg.mustBeUnique && total > 1) {
                        items.forEach(it => {
                            results.errors.push({
                                err: 'ErrMultiple' + cfg.type + 'Landmarks',
                                type: 'err',
                                cat: 'landmarks',
                                element: it.tag,
                                xpath: it.xpath,
                                html: it.html,
                                description: 'Page has ' + total + ' ' + cfg.role + ' landmarks; there should be exactly one'
                            });
                            results.elements_failed++;
                        });
                    }

                    // ERROR: duplicate accessible names among same-type landmarks.
                    if (total > 1) {
                        const byName = {};
                        labeledItems.forEach(it => {
                            const key = it.details.name;
                            if (!byName[key]) byName[key] = [];
                            byName[key].push(it);
                        });
                        Object.keys(byName).forEach(nameKey => {
                            const group = byName[nameKey];
                            if (group.length > 1) {
                                group.forEach(it => {
                                    results.errors.push({
                                        err: 'ErrDuplicateLabelFor' + cfg.type + 'Landmark',
                                        type: 'err',
                                        cat: 'landmarks',
                                        element: it.tag,
                                        xpath: it.xpath,
                                        html: it.html,
                                        description: 'Multiple ' + cfg.role + ' landmarks share the same accessible name "' + nameKey + '"'
                                    });
                                    results.elements_failed++;
                                });
                            }
                        });
                    }

                    // WARNING family for unlabelled landmarks when multiples exist.
                    if (total > 1) {
                        const someLabeled = labeledItems.length > 0;
                        unlabeledItems.forEach(it => {
                            // Warn{Type}LandmarkHasNoLabel: only when 2+ are unlabelled
                            // (a single unlabelled landmark among labelled ones is acceptable).
                            if ((cfg.type === 'Nav' || cfg.type === 'Complementary' || cfg.type === 'Contentinfo') &&
                                unlabeledItems.length >= 2) {
                                results.warnings.push({
                                    err: 'Warn' + cfg.type + 'LandmarkHasNoLabel',
                                    type: 'warn',
                                    cat: 'landmarks',
                                    element: it.tag,
                                    xpath: it.xpath,
                                    html: it.html,
                                    description: 'Multiple ' + cfg.role + ' landmarks exist but this one has no label to distinguish it'
                                });
                            }

                            // WarnMultiple{Type}LandmarksButNotAllHaveLabels: some labelled, some not.
                            if (someLabeled &&
                                ['Banner', 'Complementary', 'Contentinfo', 'Nav', 'Region'].indexOf(cfg.type) !== -1) {
                                results.warnings.push({
                                    err: 'WarnMultiple' + cfg.type + 'LandmarksButNotAllHaveLabels',
                                    type: 'warn',
                                    cat: 'landmarks',
                                    element: it.tag,
                                    xpath: it.xpath,
                                    html: it.html,
                                    description: 'Multiple ' + cfg.role + ' landmarks exist but not all have labels; this one is unlabelled'
                                });
                            }

                            // WarnMultipleNavNeedsLabel: any unlabelled nav when 2+ navs exist.
                            if (cfg.type === 'Nav') {
                                results.warnings.push({
                                    err: 'WarnMultipleNavNeedsLabel',
                                    type: 'warn',
                                    cat: 'landmarks',
                                    element: it.tag,
                                    xpath: it.xpath,
                                    html: it.html,
                                    description: 'Multiple navigation landmarks exist; this one needs a unique label'
                                });
                            }
                        });
                    }

                    // Region-specific: an explicit role="region" without an accessible
                    // name must have one, and is not exposed as a landmark.
                    if (cfg.type === 'Region') {
                        items.forEach(it => {
                            const isExplicitRegion = it.el.getAttribute('role') === 'region';
                            if (isExplicitRegion && !it.details.hasName) {
                                results.errors.push({
                                    err: 'ErrRegionLandmarkMustHaveAccessibleName',
                                    type: 'err',
                                    cat: 'landmarks',
                                    element: it.tag,
                                    xpath: it.xpath,
                                    html: it.html,
                                    description: 'Region landmark must have an accessible name via aria-label or aria-labelledby'
                                });
                                results.elements_failed++;
                                results.warnings.push({
                                    err: 'WarnRegionLandmarkHasNoLabelSoIsNotConsideredALandmark',
                                    type: 'warn',
                                    cat: 'landmarks',
                                    element: it.tag,
                                    xpath: it.xpath,
                                    html: it.html,
                                    description: 'Region has no accessible name, so it is not exposed as a landmark to assistive technology'
                                });
                            }
                        });
                    }

                    // Nav-specific: empty / whitespace-only / nested navigation landmarks.
                    if (cfg.type === 'Nav') {
                        items.forEach(it => {
                            const hasElementChildren = it.el.children.length > 0;
                            const textContent = (it.el.textContent || '');
                            if (!hasElementChildren) {
                                if (textContent.length === 0) {
                                    results.errors.push({
                                        err: 'ErrCompletelyEmptyNavLandmark',
                                        type: 'err',
                                        cat: 'landmarks',
                                        element: it.tag,
                                        xpath: it.xpath,
                                        html: it.html,
                                        description: 'Navigation landmark is completely empty with no child elements or text'
                                    });
                                    results.elements_failed++;
                                }
                                if (textContent.length > 0 && textContent.trim().length === 0) {
                                    results.errors.push({
                                        err: 'ErrNavLandmarkContainsOnlyWhiteSpace',
                                        type: 'err',
                                        cat: 'landmarks',
                                        element: it.tag,
                                        xpath: it.xpath,
                                        html: it.html,
                                        description: 'Navigation landmark contains only whitespace and no functional content'
                                    });
                                    results.elements_failed++;
                                }
                            }

                            // Nested nav: flag a nav that has a nav landmark ancestor OR descendant.
                            const navAncestor = it.el.parentElement
                                ? it.el.parentElement.closest('nav, [role="navigation"]')
                                : null;
                            const navDescendant = it.el.querySelector('nav, [role="navigation"]');
                            if (navAncestor || navDescendant) {
                                results.errors.push({
                                    err: 'ErrNestedNavLandmarks',
                                    type: 'err',
                                    cat: 'landmarks',
                                    element: it.tag,
                                    xpath: it.xpath,
                                    html: it.html,
                                    description: 'Navigation landmark is nested with another navigation landmark, creating an ambiguous hierarchy'
                                });
                                results.elements_failed++;
                            }
                        });
                    }

                    // Main-specific: hidden main, and main with focusable tabindex.
                    if (cfg.type === 'Main') {
                        items.forEach(it => {
                            if (isHiddenElement(it.el)) {
                                results.errors.push({
                                    err: 'ErrMainLandmarkIsHidden',
                                    type: 'err',
                                    cat: 'landmarks',
                                    element: it.tag,
                                    xpath: it.xpath,
                                    html: it.html,
                                    description: 'Main landmark is hidden (display:none, visibility:hidden, or aria-hidden), making the primary content inaccessible'
                                });
                                results.elements_failed++;
                            }
                            const tabindexAttr = it.el.getAttribute('tabindex');
                            if (tabindexAttr !== null) {
                                const tabindexValue = parseInt(tabindexAttr, 10);
                                if (!isNaN(tabindexValue) && tabindexValue >= 0) {
                                    results.errors.push({
                                        err: 'ErrMainLandmarkHasTabindexOfZeroCanOnlyHaveMinusOneAtMost',
                                        type: 'err',
                                        cat: 'landmarks',
                                        element: it.tag,
                                        xpath: it.xpath,
                                        html: it.html,
                                        description: 'Main landmark has tabindex="' + tabindexAttr + '"; landmarks must not be in the tab order (only tabindex="-1" is acceptable)'
                                    });
                                    results.elements_failed++;
                                }
                            }
                        });
                    }
                });

                // Missing-landmark granular codes (alongside the existing
                // ErrMissingMainLandmark / WarnMissing* codes above).
                const hasNavLandmark = document.querySelector('nav, [role="navigation"]') !== null;
                if (!hasMain) {
                    results.errors.push({
                        err: 'ErrNoMainLandmark',
                        type: 'err',
                        cat: 'landmarks',
                        element: 'body',
                        xpath: '/html/body',
                        html: bodyStart,
                        description: 'Page is missing a main landmark'
                    });
                    results.elements_failed++;
                }
                if (!hasBanner) {
                    results.errors.push({
                        err: 'ErrNoBannerLandmarkOnPage',
                        type: 'err',
                        cat: 'landmarks',
                        element: 'body',
                        xpath: '/html/body',
                        html: bodyStart,
                        description: 'Page has no banner landmark'
                    });
                    results.errors.push({
                        err: 'WarnNoBannerLandmark',
                        type: 'warn',
                        cat: 'landmarks',
                        element: 'body',
                        xpath: '/html/body',
                        html: bodyStart,
                        description: 'Page has no banner landmark'
                    });
                }
                if (!hasContentinfo) {
                    results.warnings.push({
                        err: 'WarnNoContentinfoLandmark',
                        type: 'warn',
                        cat: 'landmarks',
                        element: 'body',
                        xpath: '/html/body',
                        html: bodyStart,
                        description: 'Page has no contentinfo landmark'
                    });
                }
                if (!hasNavLandmark) {
                    results.warnings.push({
                        err: 'WarnNoNavLandmark',
                        type: 'warn',
                        cat: 'landmarks',
                        element: 'body',
                        xpath: '/html/body',
                        html: bodyStart,
                        description: 'Page has no navigation landmark'
                    });
                }

                // Form landmark: aria-labelledby that references a non-heading element.
                const formLabelledbyLandmarks = Array.from(document.querySelectorAll('form, [role="form"]'));
                formLabelledbyLandmarks.forEach(form => {
                    const labelledby = form.getAttribute('aria-labelledby');
                    if (labelledby && labelledby.trim()) {
                        const refIds = labelledby.trim().split(/\s+/);
                        let referencesHeading = false;
                        let referencesAny = false;
                        refIds.forEach(id => {
                            const ref = document.getElementById(id);
                            if (ref) {
                                referencesAny = true;
                                if (/^h[1-6]$/.test(ref.tagName.toLowerCase())) {
                                    referencesHeading = true;
                                }
                            }
                        });
                        if (referencesAny && !referencesHeading) {
                            results.errors.push({
                                err: 'ErrFormAriaLabelledByReferenceDoesNotReferenceAHeading',
                                type: 'err',
                                cat: 'landmarks',
                                element: form.tagName.toLowerCase(),
                                xpath: getFullXPath(form),
                                html: form.outerHTML.substring(0, 200),
                                description: 'Form landmark aria-labelledby references a non-heading element; referencing a heading provides clearer structure'
                            });
                            results.elements_failed++;
                        }
                    }
                });

                // Form landmark: duplicate accessible names among form landmarks.
                // A <form> is only a landmark when it has an accessible name supplied
                // by aria-label or aria-labelledby (title / implicit heading do not
                // make a <form> a landmark for this check).
                const formNameGroups = {};
                formLabelledbyLandmarks.forEach(form => {
                    const fd = getNameDetails(form);
                    if (fd.hasName && (fd.hasAriaLabel || fd.hasLabelledby)) {
                        if (!formNameGroups[fd.name]) formNameGroups[fd.name] = [];
                        formNameGroups[fd.name].push(form);
                    }
                });
                Object.keys(formNameGroups).forEach(nameKey => {
                    const group = formNameGroups[nameKey];
                    if (group.length > 1) {
                        group.forEach(form => {
                            results.errors.push({
                                err: 'ErrDuplicateLabelForFormLandmark',
                                type: 'err',
                                cat: 'landmarks',
                                element: form.tagName.toLowerCase(),
                                xpath: getFullXPath(form),
                                html: form.outerHTML.substring(0, 200),
                                description: 'Multiple form landmarks share the same accessible name "' + nameKey + '"'
                            });
                            results.elements_failed++;
                        });
                    }
                });

                // Add check information for reporting
                results.checks.push({
                    description: 'Required landmarks',
                    wcag: ['1.3.1', '2.4.1'],
                    total: 3, // main, banner, contentinfo
                    passed: [hasMain, hasBanner, hasContentinfo].filter(Boolean).length,
                    failed: [hasMain, hasBanner, hasContentinfo].filter(x => !x).length
                });
                
                results.checks.push({
                    description: 'Landmark accessibility',
                    wcag: ['1.3.1'],
                    total: landmarkElements.length,
                    passed: results.elements_passed,
                    failed: results.elements_failed
                });

                // Check for form landmarks without accessible names
                // Both <form> elements and elements with role="form" need accessible names
                // Note: <form> without accessible name is NOT a landmark, but <form> WITH
                // accessible name becomes a form landmark, so we check all forms
                const formLandmarks = Array.from(document.querySelectorAll('form, [role="form"]'));
                formLandmarks.forEach(element => {
                    const ariaLabel = element.getAttribute('aria-label');
                    const ariaLabelledby = element.getAttribute('aria-labelledby');

                    // ERROR: Check if form has BOTH aria-label and aria-labelledby
                    // Having both attributes is problematic because:
                    // - aria-labelledby takes precedence and aria-label is ignored by screen readers
                    // - Creates confusion for developers who may think aria-label is being used
                    // - Violates WCAG 4.1.2 by having redundant/conflicting attributes
                    if (ariaLabel !== null && ariaLabel !== undefined &&
                        ariaLabelledby !== null && ariaLabelledby !== undefined) {
                        results.errors.push({
                            err: 'ErrFormLandmarkHasAriaLabelAndAriaLabelledByAttrs',
                            type: 'err',
                            cat: 'landmarks',
                            element: element.tagName.toLowerCase(),
                            xpath: getFullXPath(element),
                            html: element.outerHTML.substring(0, 200),
                            description: 'Form landmark has both aria-label and aria-labelledby attributes. Aria-labelledby takes precedence making aria-label ignored. Only one labeling method should be used.',
                            ariaLabelValue: ariaLabel,
                            ariaLabelledbyValue: ariaLabelledby
                        });
                        results.elements_failed++;
                    }

                    let hasAccessibleName = false;
                    let labelText = '';

                    // Check aria-label first
                    if (ariaLabel !== null && ariaLabel !== undefined) {
                        // aria-label attribute exists, check if it's blank
                        if (ariaLabel.trim()) {
                            hasAccessibleName = true;
                            labelText = ariaLabel.trim();

                            // ERROR: Form uses aria-label instead of visible element
                            // aria-label is only available to screen readers, not all users
                            // Should use visible heading with aria-labelledby instead
                            results.errors.push({
                                err: 'ErrFormUsesAriaLabelInsteadOfVisibleElement',
                                type: 'err',
                                cat: 'landmarks',
                                element: element.tagName.toLowerCase(),
                                xpath: getFullXPath(element),
                                html: element.outerHTML.substring(0, 200),
                                description: 'Form uses aria-label which is only available to screen readers. Use a visible heading with aria-labelledby instead to make the label available to all users',
                                ariaLabelValue: ariaLabel.trim()
                            });
                            results.elements_failed++;
                        } else {
                            // ERROR: aria-label exists but is blank or whitespace only
                            results.errors.push({
                                err: 'ErrFormLandmarkAccessibleNameIsBlank',
                                type: 'err',
                                cat: 'landmarks',
                                element: element.tagName.toLowerCase(),
                                xpath: getFullXPath(element),
                                html: element.outerHTML.substring(0, 200),
                                description: 'Form has aria-label attribute but it is empty or contains only whitespace',
                                ariaLabelValue: ariaLabel
                            });
                            results.elements_failed++;
                        }
                    }
                    // Check aria-labelledby
                    else if (ariaLabelledby) {
                        const labelledByTrimmed = ariaLabelledby.trim();

                        if (labelledByTrimmed) {
                            // aria-labelledby can reference multiple IDs (space-separated)
                            const refIds = labelledByTrimmed.split(/\s+/);
                            let labelTexts = [];
                            let hasError = false;
                            let sawBlankRef = false;

                            refIds.forEach(refId => {
                                const labelElement = document.getElementById(refId);

                                if (labelElement) {
                                    // Strip zero-width / invisible characters that trim() leaves
                                    // behind (U+200B/C/D, word joiner, BOM) so an invisible-character
                                    // label resolves to an empty accessible name.
                                    const elementText = labelElement.textContent
                                        .replace(/[​‌‍⁠﻿]/g, '').trim();

                                    // Check if element is hidden
                                    const computedStyle = window.getComputedStyle(labelElement);
                                    const isHidden = computedStyle.display === 'none' ||
                                                   computedStyle.visibility === 'hidden' ||
                                                   labelElement.hasAttribute('hidden') ||
                                                   labelElement.getAttribute('aria-hidden') === 'true';

                                    if (isHidden) {
                                        // ERROR: aria-labelledby references hidden element
                                        results.errors.push({
                                            err: 'ErrFormAriaLabelledByReferenceDIsHidden',
                                            type: 'err',
                                            cat: 'landmarks',
                                            element: element.tagName.toLowerCase(),
                                            xpath: getFullXPath(element),
                                            html: element.outerHTML.substring(0, 200),
                                            description: `Form aria-labelledby references hidden element (id="${refId}")`,
                                            referencedId: refId,
                                            referencedElement: labelElement.tagName
                                        });
                                        results.elements_failed++;
                                        hasError = true;
                                    } else if (!elementText) {
                                        // Visible but empty reference. Defer judgement: flag the
                                        // form only if the ENTIRE resolved name turns out blank
                                        // (an empty ref alongside text-bearing siblings is fine).
                                        sawBlankRef = true;
                                    } else {
                                        labelTexts.push(elementText);
                                    }
                                } else {
                                    // ERROR: aria-labelledby references non-existent element
                                    results.errors.push({
                                        err: 'ErrFormAriaLabelledByReferenceDoesNotExist',
                                        type: 'err',
                                        cat: 'landmarks',
                                        element: element.tagName.toLowerCase(),
                                        xpath: getFullXPath(element),
                                        html: element.outerHTML.substring(0, 200),
                                        description: `Form aria-labelledby references non-existent element ID: "${refId}"`,
                                        missingId: refId
                                    });
                                    results.elements_failed++;
                                    hasError = true;
                                }
                            });

                            // The resolved accessible name is the concatenation of the text-
                            // bearing references. Flag it blank only when NONE contributed text.
                            if (sawBlankRef && labelTexts.length === 0 && !hasError) {
                                results.errors.push({
                                    err: 'ErrFormAriaLabelledByIsBlank',
                                    type: 'err',
                                    cat: 'landmarks',
                                    element: element.tagName.toLowerCase(),
                                    xpath: getFullXPath(element),
                                    html: element.outerHTML.substring(0, 200),
                                    description: 'Form aria-labelledby resolves to a blank or empty accessible name'
                                });
                                results.elements_failed++;
                                hasError = true;
                            }

                            // Only consider it as having an accessible name if we found at least one valid label
                            if (labelTexts.length > 0 && !hasError) {
                                hasAccessibleName = true;
                                labelText = labelTexts.join(' ');
                            }
                        }
                    }

                    // ERROR: Check if form uses title attribute
                    // title attribute is unreliable for accessibility:
                    // - Not consistently announced by screen readers
                    // - Not available on touch devices (no hover)
                    // - Should use aria-label or aria-labelledby instead
                    const titleAttr = element.getAttribute('title');
                    if (titleAttr && titleAttr.trim()) {
                        results.errors.push({
                            err: 'ErrFormUsesTitleAttribute',
                            type: 'err',
                            cat: 'landmarks',
                            element: element.tagName.toLowerCase(),
                            xpath: getFullXPath(element),
                            html: element.outerHTML.substring(0, 200),
                            description: 'Form uses title attribute which is unreliable for accessibility. Use aria-label or aria-labelledby with a visible heading instead',
                            titleValue: titleAttr.trim()
                        });
                        results.elements_failed++;
                    }

                    // Only report error if element has role="form" OR is a <form> element
                    // <form> elements only become landmarks when they have accessible names,
                    // but if they SHOULD be landmarks, they need names
                    const isFormElement = element.tagName.toLowerCase() === 'form';
                    const hasFormRole = element.getAttribute('role') === 'form';

                    if ((isFormElement || hasFormRole) && !hasAccessibleName) {
                        results.errors.push({
                            err: 'ErrFormLandmarkMustHaveAccessibleName',
                            type: 'err',
                            cat: 'landmarks',
                            element: element.tagName.toLowerCase(),
                            xpath: getFullXPath(element),
                            html: element.outerHTML.substring(0, 200),
                            description: `${isFormElement ? '<form>' : 'Element with role="form"'} must have an accessible name via aria-label or aria-labelledby to be a proper form landmark`,
                            role: hasFormRole ? 'form' : 'implicit',
                            isFormElement: isFormElement
                        });
                        results.elements_failed++;
                    } else if (hasAccessibleName) {
                        results.elements_passed++;
                    }
                });

                // DISCOVERY: Report all navigation landmarks for tracking across pages
                const navElements = Array.from(document.querySelectorAll('nav, [role="navigation"]'));
                navElements.forEach(nav => {
                    // Get all links in this navigation
                    const links = Array.from(nav.querySelectorAll('a'));
                    const linkCount = links.length;

                    // Get accessible name if present
                    let navLabel = '';
                    const ariaLabel = nav.getAttribute('aria-label');
                    if (ariaLabel) {
                        navLabel = ariaLabel.trim();
                    } else {
                        const labelledBy = nav.getAttribute('aria-labelledby');
                        if (labelledBy) {
                            const labelEl = document.getElementById(labelledBy);
                            if (labelEl) {
                                navLabel = labelEl.textContent.trim();
                            }
                        }
                    }

                    // Generate signature from nav structure (content-based, NOT xpath-based)
                    // XPaths vary between pages due to DOM differences above the nav,
                    // so we use link hrefs + structure for cross-page matching
                    const navStructure = links.map(link => {
                        const href = (link.getAttribute('href') || '').replace(/^https?:\/\/[^\/]+/, '');
                        return href + ':' + link.textContent.trim();
                    }).sort().join('|');
                    const navXPath = getFullXPath(nav);
                    const navLabel_sig = nav.getAttribute('aria-label') || '';
                    const signatureString = navStructure + '|' + navLabel_sig;

                    // Simple hash function (same as forms)
                    let hash = 0;
                    for (let i = 0; i < signatureString.length; i++) {
                        const char = signatureString.charCodeAt(i);
                        hash = ((hash << 5) - hash) + char;
                        hash = hash & hash; // Convert to 32bit integer
                    }
                    const navSignature = Math.abs(hash).toString(16).padStart(8, '0');

                    // Build description
                    let description = `Navigation region detected (signature: ${navSignature})`;
                    if (navLabel) {
                        description += ` with label "${navLabel}"`;
                    }
                    description += ` containing ${linkCount} link${linkCount !== 1 ? 's' : ''} - requires manual accessibility review`;

                    results.warnings.push({
                        err: 'DiscoNavFound',
                        type: 'disco',
                        cat: 'landmarks',
                        element: nav.tagName.toLowerCase(),
                        xpath: navXPath,
                        html: nav.outerHTML.substring(0, 500),
                        description: description,
                        navSignature: navSignature,
                        linkCount: linkCount,
                        navLabel: navLabel,
                        pageLang: pageLang
                    });
                });

                // DISCOVERY: Report all <aside> or role="complementary" for tracking across pages
                const asideElements = Array.from(document.querySelectorAll('aside, [role="complementary"]'));
                asideElements.forEach(aside => {
                    let asideLabel = '';
                    const ariaLabel = aside.getAttribute('aria-label');
                    if (ariaLabel) {
                        asideLabel = ariaLabel.trim();
                    } else {
                        const labelledBy = aside.getAttribute('aria-labelledby');
                        if (labelledBy) {
                            const labelEl = document.getElementById(labelledBy);
                            if (labelEl) asideLabel = labelEl.textContent.trim();
                        }
                    }

                    // Use label + child structure for cross-page matching (NOT xpath or text content)
                    const asideXPath = getFullXPath(aside);
                    const asideChildren = Array.from(aside.children).map(c => c.tagName.toLowerCase()).join(',');
                    const signatureString = 'aside|' + asideLabel + '|' + asideChildren;

                    // Simple hash function
                    let hash = 0;
                    for (let i = 0; i < signatureString.length; i++) {
                        const char = signatureString.charCodeAt(i);
                        hash = ((hash << 5) - hash) + char;
                        hash = hash & hash;
                    }
                    const asideSignature = Math.abs(hash).toString(16).padStart(8, '0');

                    results.warnings.push({
                        err: 'DiscoAsideFound',
                        type: 'disco',
                        cat: 'landmarks',
                        element: aside.tagName.toLowerCase(),
                        xpath: asideXPath,
                        html: aside.outerHTML.substring(0, 500),
                        asideSignature: asideSignature,
                        asideLabel: asideLabel,
                        pageLang: pageLang
                    });
                });

                // DISCOVERY: Report all <section> with role="region" or explicit labels
                const sectionElements = Array.from(document.querySelectorAll('section[role="region"], section[aria-label], section[aria-labelledby]'));
                sectionElements.forEach(section => {
                    let sectionLabel = '';
                    const ariaLabel = section.getAttribute('aria-label');
                    if (ariaLabel) {
                        sectionLabel = ariaLabel.trim();
                    } else {
                        const labelledBy = section.getAttribute('aria-labelledby');
                        if (labelledBy) {
                            const labelEl = document.getElementById(labelledBy);
                            if (labelEl) sectionLabel = labelEl.textContent.trim();
                        }
                    }

                    // Use label + child structure for cross-page matching (NOT xpath or text content)
                    const sectionXPath = getFullXPath(section);
                    const sectionChildren = Array.from(section.children).map(c => c.tagName.toLowerCase()).join(',');
                    const signatureString = 'section|' + sectionLabel + '|' + sectionChildren;

                    // Simple hash function
                    let hash = 0;
                    for (let i = 0; i < signatureString.length; i++) {
                        const char = signatureString.charCodeAt(i);
                        hash = ((hash << 5) - hash) + char;
                        hash = hash & hash;
                    }
                    const sectionSignature = Math.abs(hash).toString(16).padStart(8, '0');

                    const description = `Section region detected (signature: ${sectionSignature}) - requires manual accessibility review`;

                    results.warnings.push({
                        err: 'DiscoSectionFound',
                        type: 'disco',
                        cat: 'landmarks',
                        element: section.tagName.toLowerCase(),
                        xpath: sectionXPath,
                        html: section.outerHTML.substring(0, 500),
                        description: description,
                        sectionSignature: sectionSignature,
                        sectionLabel: sectionLabel,
                        pageLang: pageLang
                    });
                });

                // DISCOVERY: Report all <header> at top level or role="banner"
                const headerElements = Array.from(document.querySelectorAll('header, [role="banner"]'));
                headerElements.forEach(header => {
                    // Only report top-level headers or explicit role="banner"
                    const hasExplicitRole = header.getAttribute('role') === 'banner';
                    const isTopLevel = !header.closest('article, section, aside, nav, main');

                    if (hasExplicitRole || isTopLevel) {
                        let headerLabel = '';
                        const ariaLabel = header.getAttribute('aria-label');
                        if (ariaLabel) {
                            headerLabel = ariaLabel.trim();
                        } else {
                            const labelledBy = header.getAttribute('aria-labelledby');
                            if (labelledBy) {
                                const labelEl = document.getElementById(labelledBy);
                                if (labelEl) headerLabel = labelEl.textContent.trim();
                            }
                        }

                        // Use label + child element structure for cross-page matching (NOT xpath or text content)
                        const headerXPath = getFullXPath(header);
                        const headerChildren = Array.from(header.children).map(c => c.tagName.toLowerCase()).join(',');
                        const signatureString = 'header|' + headerLabel + '|' + headerChildren;

                        // Simple hash function
                        let hash = 0;
                        for (let i = 0; i < signatureString.length; i++) {
                            const char = signatureString.charCodeAt(i);
                            hash = ((hash << 5) - hash) + char;
                            hash = hash & hash;
                        }
                        const headerSignature = Math.abs(hash).toString(16).padStart(8, '0');

                        const description = `Banner region detected (signature: ${headerSignature}) - requires manual accessibility review`;

                        results.warnings.push({
                            err: 'DiscoHeaderFound',
                            type: 'disco',
                            cat: 'landmarks',
                            element: header.tagName.toLowerCase(),
                            xpath: headerXPath,
                            html: header.outerHTML.substring(0, 500),
                            description: description,
                            headerSignature: headerSignature,
                            headerLabel: headerLabel,
                            pageLang: pageLang
                        });
                    }
                });

                // DISCOVERY: Report all <footer> at top level or role="contentinfo"
                const footerElements = Array.from(document.querySelectorAll('footer, [role="contentinfo"]'));
                footerElements.forEach(footer => {
                    // Only report top-level footers or explicit role="contentinfo"
                    const hasExplicitRole = footer.getAttribute('role') === 'contentinfo';
                    const isTopLevel = !footer.closest('article, section, aside, nav, main');

                    if (hasExplicitRole || isTopLevel) {
                        let footerLabel = '';
                        const ariaLabel = footer.getAttribute('aria-label');
                        if (ariaLabel) {
                            footerLabel = ariaLabel.trim();
                        } else {
                            const labelledBy = footer.getAttribute('aria-labelledby');
                            if (labelledBy) {
                                const labelEl = document.getElementById(labelledBy);
                                if (labelEl) footerLabel = labelEl.textContent.trim();
                            }
                        }

                        // Use label + child element structure for cross-page matching (NOT xpath or text content)
                        const footerXPath = getFullXPath(footer);
                        const footerChildren = Array.from(footer.children).map(c => c.tagName.toLowerCase()).join(',');
                        const signatureString = 'footer|' + footerLabel + '|' + footerChildren;

                        // Simple hash function
                        let hash = 0;
                        for (let i = 0; i < signatureString.length; i++) {
                            const char = signatureString.charCodeAt(i);
                            hash = ((hash << 5) - hash) + char;
                            hash = hash & hash;
                        }
                        const footerSignature = Math.abs(hash).toString(16).padStart(8, '0');

                        const description = `Contentinfo region detected (signature: ${footerSignature}) - requires manual accessibility review`;

                        results.warnings.push({
                            err: 'DiscoFooterFound',
                            type: 'disco',
                            cat: 'landmarks',
                            element: footer.tagName.toLowerCase(),
                            xpath: footerXPath,
                            html: footer.outerHTML.substring(0, 500),
                            description: description,
                            footerSignature: footerSignature,
                            footerLabel: footerLabel,
                            pageLang: pageLang
                        });
                    }
                });

                // DISCOVERY: Report all <search> or role="search"
                const searchElements = Array.from(document.querySelectorAll('search, [role="search"]'));
                searchElements.forEach(search => {
                    let searchLabel = '';
                    const ariaLabel = search.getAttribute('aria-label');
                    if (ariaLabel) {
                        searchLabel = ariaLabel.trim();
                    } else {
                        const labelledBy = search.getAttribute('aria-labelledby');
                        if (labelledBy) {
                            const labelEl = document.getElementById(labelledBy);
                            if (labelEl) searchLabel = labelEl.textContent.trim();
                        }
                    }

                    // Use label + child element structure for cross-page matching (NOT xpath or text content)
                    const searchXPath = getFullXPath(search);
                    const searchChildren = Array.from(search.children).map(c => c.tagName.toLowerCase()).join(',');
                    const signatureString = 'search|' + searchLabel + '|' + searchChildren;

                    // Simple hash function
                    let hash = 0;
                    for (let i = 0; i < signatureString.length; i++) {
                        const char = signatureString.charCodeAt(i);
                        hash = ((hash << 5) - hash) + char;
                        hash = hash & hash;
                    }
                    const searchSignature = Math.abs(hash).toString(16).padStart(8, '0');

                    const description = `Search region detected (signature: ${searchSignature}) - requires manual accessibility review`;

                    results.warnings.push({
                        err: 'DiscoSearchFound',
                        type: 'disco',
                        cat: 'landmarks',
                        element: search.tagName.toLowerCase(),
                        xpath: searchXPath,
                        html: search.outerHTML.substring(0, 500),
                        description: description,
                        searchSignature: searchSignature,
                        searchLabel: searchLabel,
                        pageLang: pageLang
                    });
                });

                return results;
            }
        ''')

        return results
        
    except Exception as e:
        logger.error(f"Error in test_landmarks: {e}")
        return {
            'error': str(e),
            'applicable': False,
            'errors': [],
            'warnings': [],
            'passes': []
        }