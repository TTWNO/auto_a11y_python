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
     * The report is Markdown parsed in the browser, so its headings do
     * not exist until that render finishes. Watch the content element
     * and build the list the first time it gains children.
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

        sidebar.hidden = false;
        return true;
    }

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
}());
