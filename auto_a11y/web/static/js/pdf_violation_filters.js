// Client-side filtering, search, and TOC highlighting for the PDF
// audit report.
//
// The detail template renders the right pane as a multi-section
// report: an overview, a filter bar, touchpoint-grouped finding
// cards, and appendices. This script:
//
//  * Toggles cards by severity / impact / touchpoint via chip groups
//    inside [data-pdf-violation-filters]. Each dimension defaults to
//    "all"; selections combine with AND.
//  * Free-text searches across each card's data-pdf-search blob,
//    which the template pre-flattens (title + description + what +
//    why + who) at render time.
//  * Hides empty touchpoint groups (and their <details> wrappers'
//    chrome) when no children pass the filters.
//  * Highlights the section in the TOC nav matching the user's
//    current scroll position via IntersectionObserver.

(function () {
    'use strict';

    function findHosts() {
        return document.querySelectorAll('[data-pdf-violation-filters]');
    }

    function readActiveChips(filterRoot) {
        var activeChips = filterRoot.querySelectorAll('.pdf-violation-chip.is-active');
        var activeFilters = {};
        for (var i = 0; i < activeChips.length; i += 1) {
            var dim = activeChips[i].getAttribute('data-pdf-filter');
            var val = activeChips[i].getAttribute('data-pdf-filter-value');
            if (dim !== null && val !== null) {
                activeFilters[dim] = val;
            }
        }
        return activeFilters;
    }

    function readSearchTerm(filterRoot) {
        var input = filterRoot.querySelector('[data-pdf-violation-search]');
        if (input === null) {
            return '';
        }
        return (input.value || '').trim().toLowerCase();
    }

    function applyFilters(filterRoot, list, emptyNotice) {
        var f = readActiveChips(filterRoot);
        var search = readSearchTerm(filterRoot);
        var cards = list.querySelectorAll('[data-pdf-violation-card]');
        var totalVisible = 0;
        for (var i = 0; i < cards.length; i += 1) {
            var card = cards[i];
            var levelMatch = (
                f.level === undefined
                || f.level === 'all'
                || card.getAttribute('data-pdf-result-level') === f.level
            );
            var impactMatch = (
                f.impact === undefined
                || f.impact === 'all'
                || card.getAttribute('data-pdf-impact') === f.impact
            );
            var touchpointMatch = (
                f.touchpoint === undefined
                || f.touchpoint === 'all'
                || card.getAttribute('data-pdf-touchpoint') === f.touchpoint
            );
            var searchBlob = card.getAttribute('data-pdf-search') || '';
            var searchMatch = (
                search === ''
                || searchBlob.indexOf(search) !== -1
            );
            var visible = levelMatch && impactMatch && touchpointMatch && searchMatch;
            card.hidden = !visible;
            if (visible) {
                totalVisible += 1;
            }
        }
        var groups = list.querySelectorAll('[data-pdf-group]');
        for (var g = 0; g < groups.length; g += 1) {
            var group = groups[g];
            var visibleInGroup = group.querySelectorAll(
                '[data-pdf-violation-card]:not([hidden])'
            ).length;
            var emptyMsg = group.querySelector('[data-pdf-group-empty]');
            if (emptyMsg !== null) {
                emptyMsg.hidden = visibleInGroup !== 0;
            }
            // When no card matches the active filters, hide the entire
            // group — chrome and all — instead of leaving the summary
            // header dangling above an empty body.
            group.hidden = visibleInGroup === 0;
        }
        if (emptyNotice !== null) {
            emptyNotice.hidden = totalVisible !== 0;
        }
    }

    function setActive(filterRoot, dim, value) {
        var chips = filterRoot.querySelectorAll(
            '.pdf-violation-chip[data-pdf-filter="' + dim + '"]'
        );
        for (var i = 0; i < chips.length; i += 1) {
            var chip = chips[i];
            if (chip.getAttribute('data-pdf-filter-value') === value) {
                chip.classList.add('is-active');
                chip.setAttribute('aria-pressed', 'true');
            } else {
                chip.classList.remove('is-active');
                chip.setAttribute('aria-pressed', 'false');
            }
        }
    }

    function bindChips(filterRoot, list, emptyNotice) {
        var chips = filterRoot.querySelectorAll('.pdf-violation-chip');
        for (var i = 0; i < chips.length; i += 1) {
            (function (chip) {
                chip.setAttribute(
                    'aria-pressed',
                    chip.classList.contains('is-active') ? 'true' : 'false'
                );
                chip.addEventListener('click', function () {
                    var dim = chip.getAttribute('data-pdf-filter');
                    var value = chip.getAttribute('data-pdf-filter-value');
                    if (dim === null || value === null) {
                        return;
                    }
                    setActive(filterRoot, dim, value);
                    applyFilters(filterRoot, list, emptyNotice);
                });
            })(chips[i]);
        }
    }

    function bindSearch(filterRoot, list, emptyNotice) {
        var input = filterRoot.querySelector('[data-pdf-violation-search]');
        if (input === null) {
            return;
        }
        var debounceHandle = null;
        input.addEventListener('input', function () {
            if (debounceHandle !== null) {
                window.clearTimeout(debounceHandle);
            }
            debounceHandle = window.setTimeout(function () {
                applyFilters(filterRoot, list, emptyNotice);
                debounceHandle = null;
            }, 100);
        });
    }

    function bindTocHighlight() {
        var nav = document.querySelector('[data-pdf-report-nav]');
        if (nav === null || typeof window.IntersectionObserver !== 'function') {
            return;
        }
        var links = nav.querySelectorAll('[data-pdf-nav-target]');
        var targets = [];
        var linkByTargetId = {};
        for (var i = 0; i < links.length; i += 1) {
            var targetId = links[i].getAttribute('data-pdf-nav-target');
            if (targetId === null) {
                continue;
            }
            var target = document.getElementById(targetId);
            if (target !== null) {
                targets.push(target);
                linkByTargetId[targetId] = links[i];
            }
        }
        if (targets.length === 0) {
            return;
        }
        var setActiveLink = function (id) {
            for (var key in linkByTargetId) {
                if (Object.prototype.hasOwnProperty.call(linkByTargetId, key)) {
                    if (key === id) {
                        linkByTargetId[key].classList.add('is-active');
                    } else {
                        linkByTargetId[key].classList.remove('is-active');
                    }
                }
            }
        };
        var observer = new window.IntersectionObserver(function (entries) {
            // Pick the first entry that's intersecting near the top of
            // the viewport. Falls back to the last entry seen if none
            // are currently intersecting (keeps highlight steady when
            // the user scrolls past all sections).
            var picked = null;
            for (var j = 0; j < entries.length; j += 1) {
                if (entries[j].isIntersecting) {
                    if (picked === null
                        || entries[j].boundingClientRect.top
                        < picked.boundingClientRect.top) {
                        picked = entries[j];
                    }
                }
            }
            if (picked !== null) {
                setActiveLink(picked.target.id);
            }
        }, {
            rootMargin: '-20% 0px -55% 0px',
            threshold: 0
        });
        for (var k = 0; k < targets.length; k += 1) {
            observer.observe(targets[k]);
        }
        // Initial state: mark the first section active.
        setActiveLink(targets[0].id);
    }

    function bind(filterRoot) {
        var list = document.querySelector('[data-pdf-violation-list]');
        if (list === null) {
            return;
        }
        var emptyNotice = document.querySelector('[data-pdf-violation-empty]');
        bindChips(filterRoot, list, emptyNotice);
        bindSearch(filterRoot, list, emptyNotice);
        applyFilters(filterRoot, list, emptyNotice);
    }

    function start() {
        var hosts = findHosts();
        for (var i = 0; i < hosts.length; i += 1) {
            bind(hosts[i]);
        }
        bindTocHighlight();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
})();
