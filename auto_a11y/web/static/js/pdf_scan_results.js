/*
 * Results-view behaviour for a standalone PDF scan.
 *
 * Transcribed from pdfMax's ResultsView.tsx:
 *
 *   - the WAI-ARIA Tabs pattern its handleTabKeyDown implements — roving
 *     tabindex, ArrowRight/ArrowDown forward, ArrowLeft/ArrowUp back,
 *     Home/End to the ends, wrapping, with activation following focus;
 *   - the section sidebar its onSectionsReady/setSections pair builds
 *     from the rendered report's own headings.
 *
 * pdfMax kept both panels mounted and toggled `display`. This uses
 * `hidden` instead, so the inactive panel leaves the accessibility tree
 * and the tab sequence rather than merely going invisible.
 */
(function () {
    'use strict';

    var TABS = ['report', 'viewer'];

    var tabs = {
        report: document.getElementById('tab-report'),
        viewer: document.getElementById('tab-viewer')
    };
    var panels = {
        report: document.getElementById('panel-report'),
        viewer: document.getElementById('panel-viewer')
    };

    if (!tabs.report || !tabs.viewer || !panels.report || !panels.viewer) {
        return;
    }

    var activeTab = 'report';
    var viewerShown = false;

    function selectTab(name, moveFocus) {
        if (TABS.indexOf(name) === -1) {
            return;
        }
        activeTab = name;

        TABS.forEach(function (key) {
            var isActive = (key === name);
            tabs[key].setAttribute('aria-selected', isActive ? 'true' : 'false');
            tabs[key].tabIndex = isActive ? 0 : -1;
            panels[key].hidden = !isActive;
        });

        if (moveFocus) {
            tabs[name].focus();
        }

        /* The viewer draws its connector lines from getBoundingClientRect,
         * which reads zero for everything while the panel is hidden. The
         * first time the panel becomes visible, ask for the redraw the
         * viewer already performs on resize. */
        if (name === 'viewer' && !viewerShown) {
            viewerShown = true;
            window.dispatchEvent(new Event('resize'));
        }
    }

    TABS.forEach(function (name) {
        tabs[name].addEventListener('click', function () {
            selectTab(name, false);
        });
    });

    var tablist = tabs.report.parentElement;
    if (tablist) {
        tablist.addEventListener('keydown', function (event) {
            var index = TABS.indexOf(activeTab);
            var next = null;

            if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
                next = TABS[(index + 1) % TABS.length];
            } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
                next = TABS[(index - 1 + TABS.length) % TABS.length];
            } else if (event.key === 'Home') {
                next = TABS[0];
            } else if (event.key === 'End') {
                next = TABS[TABS.length - 1];
            }

            if (next !== null) {
                event.preventDefault();
                selectTab(next, true);
            }
        });
    }

    /* ---- Section sidebar -------------------------------------------
     *
     * The inventory entries are rendered server-side; these are the
     * verdict headings from the Markdown report, which is parsed in the
     * browser and so has no headings until that render finishes. Watch
     * the content element and prepend them once it gains children.
     */
    var reportContent = document.getElementById('pdfmax-report-content');
    var sidebar = document.getElementById('results-sidebar');
    var sectionList = document.getElementById('results-sections-list');

    function buildSections() {
        if (!reportContent || !sidebar || !sectionList) {
            return false;
        }
        var headings = reportContent.querySelectorAll('h2');
        if (headings.length === 0) {
            return false;
        }
        if (sectionList.childElementCount > 0) {
            return true;  /* already built */
        }

        for (var i = 0; i < headings.length; i += 1) {
            var heading = headings[i];
            if (!heading.id) {
                heading.id = 'report-section-' + i;
            }

            var item = document.createElement('li');
            var link = document.createElement('a');
            link.className = 'results-section-link';
            link.href = '#' + heading.id;
            link.textContent = heading.textContent;
            /* Anchor navigation alone leaves focus on the link, so the
             * next Tab resumes from the sidebar rather than from the
             * section just jumped to. Move focus to the heading. */
            link.addEventListener('click', wireSectionJump(heading));
            item.appendChild(link);
            sectionList.appendChild(item);
        }

        return true;
    }

    /* An inventory section is a collapsed <details>; navigating to one has
     * to open it, or the anchor lands on a closed block and looks broken. */
    function openTargetDetails(hash) {
        if (!hash || hash.length < 2) {
            return;
        }
        var target = document.getElementById(hash.slice(1));
        while (target) {
            if (target.tagName === 'DETAILS') {
                target.open = true;
            }
            target = target.parentElement;
        }
    }

    document.addEventListener('click', function (event) {
        var link = event.target.closest
            ? event.target.closest('a.results-section-link')
            : null;
        if (link) {
            openTargetDetails(link.getAttribute('href'));
        }
    });

    function wireSectionJump(heading) {
        return function (event) {
            event.preventDefault();
            heading.setAttribute('tabindex', '-1');
            heading.scrollIntoView({ behavior: 'smooth', block: 'start' });
            heading.focus();
        };
    }

    if (reportContent && !buildSections()) {
        var observer = new MutationObserver(function () {
            if (buildSections()) {
                observer.disconnect();
            }
        });
        observer.observe(reportContent, { childList: true });
    }

    /* ------------------------------------------------------------------
     * Result filter — pdfMax's applyResultFilter (CheckerReport.tsx ~50)
     * ------------------------------------------------------------------
     * Hides check blocks whose verdict is switched off, then hides the
     * headings left with nothing under them. Without that second pass a
     * filtered report is a list of headings above empty space, which
     * reads as a rendering fault.
     */
    var filterBar = document.getElementById('pdf-scan-filter-bar');

    function activeFilters() {
        var active = {};
        var buttons = filterBar.querySelectorAll('[data-result-filter]');
        for (var i = 0; i < buttons.length; i += 1) {
            if (buttons[i].getAttribute('aria-pressed') === 'true') {
                active[buttons[i].getAttribute('data-result-filter')] = true;
            }
        }
        return active;
    }

    /* A heading is empty when every sibling up to the next heading of the
     * same or higher rank is hidden. Walking siblings rather than nesting
     * because the Markdown render produces a flat document. */
    function hideEmptyHeadings(selector, stopTags) {
        var headings = reportContent.querySelectorAll(selector);
        for (var i = 0; i < headings.length; i += 1) {
            var heading = headings[i];
            var sibling = heading.nextElementSibling;
            var hasVisible = false;
            while (sibling && stopTags.indexOf(sibling.tagName) === -1) {
                if (sibling.tagName !== 'HR'
                    && !sibling.classList.contains('checker-detail-hidden')) {
                    hasVisible = true;
                    break;
                }
                sibling = sibling.nextElementSibling;
            }
            heading.classList.toggle('checker-detail-hidden', !hasVisible);
        }
    }

    function applyFilter() {
        if (!filterBar || !reportContent) {
            return 0;
        }
        var active = activeFilters();
        var blocks = reportContent.querySelectorAll('details[data-check-result]');
        var shown = 0;
        for (var i = 0; i < blocks.length; i += 1) {
            var verdict = blocks[i].getAttribute('data-check-result');
            var visible = active[verdict] === true;
            blocks[i].classList.toggle('checker-detail-hidden', !visible);
            if (visible) {
                shown += 1;
            }
        }
        /* h3 first, then h2: an h2 is only empty once the h3s beneath it
         * have been judged. */
        hideEmptyHeadings('h3', ['H2', 'H3']);
        hideEmptyHeadings('h2', ['H2']);
        return shown;
    }

    /* The sidebar lists the report's own headings; one that now points at
     * a hidden section is a link to nowhere. */
    function syncSectionLinks() {
        var links = document.querySelectorAll('#results-sections-list a');
        for (var i = 0; i < links.length; i += 1) {
            var id = (links[i].getAttribute('href') || '').slice(1);
            var heading = id ? document.getElementById(id) : null;
            var hidden = !!heading
                && heading.classList.contains('checker-detail-hidden');
            links[i].parentElement.hidden = hidden;
        }
    }

    function announce(message) {
        var region = document.getElementById('notification-live-region');
        if (region) {
            region.textContent = message;
        }
    }

    if (filterBar && reportContent) {
        filterBar.addEventListener('click', function (event) {
            var button = event.target.closest
                ? event.target.closest('[data-result-filter]')
                : null;
            if (!button) {
                return;
            }
            var wasOn = button.getAttribute('aria-pressed') === 'true';
            button.setAttribute('aria-pressed', wasOn ? 'false' : 'true');
            button.classList.toggle('active', !wasOn);

            var shown = applyFilter();
            syncSectionLinks();

            var label = button.getAttribute('data-filter-label') || '';
            var template = wasOn
                ? filterBar.getAttribute('data-filter-removed')
                : filterBar.getAttribute('data-filter-applied');
            var message = (template || '').replace('%s', label);
            if (shown === 0) {
                message = filterBar.getAttribute('data-filter-none') || message;
            }
            announce(message);
        });
    }
}());
