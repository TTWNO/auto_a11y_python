// Keyboard navigation and hash-based deep-linking for the PDF audit
// report's findings list.
//
// Mirrors two behaviours from the original pdfMax CheckerReport.tsx:
//
//  1. Arrow-key navigation: with focus on a finding card, ArrowDown /
//     ArrowUp / Home / End move focus to the previous/next/first/last
//     *visible* card. Cards filtered out by the chip bar (hidden=true)
//     are skipped — matches the pdfMax behaviour of only stepping
//     through ``details:not(.checker-detail-hidden) > summary``.
//
//  2. Hash-based deep-linking: if the page loads with a ``#issue-N``
//     fragment, or the user clicks a TOC/WCAG-appendix link, the
//     matching card scrolls into view, takes focus, and gets a
//     short-lived highlight flash. Same approach as
//     ``CheckerReport.scrollToCheck``.
//
// Cards are made programmatically focusable via ``tabindex="-1"`` at
// startup so the HTML stays free of explicit tabindex annotations.

(function () {
    'use strict';

    var HIGHLIGHT_CLASS = 'pdf-violation-card-highlight';
    var HIGHLIGHT_DURATION_MS = 2000;

    function visibleCards() {
        var all = document.querySelectorAll('[data-pdf-violation-card]');
        var out = [];
        for (var i = 0; i < all.length; i += 1) {
            if (!all[i].hidden) {
                out.push(all[i]);
            }
        }
        return out;
    }

    function focusCard(card) {
        if (card === null || card === undefined) {
            return;
        }
        card.focus({ preventScroll: false });
    }

    function highlightCard(card) {
        if (card === null || card === undefined) {
            return;
        }
        // Re-apply by removing first so consecutive jumps to the same
        // card retrigger the animation.
        card.classList.remove(HIGHLIGHT_CLASS);
        // Force a reflow so removing + re-adding within one task
        // restarts the keyframes.
        // eslint-disable-next-line no-unused-expressions
        void card.offsetWidth;
        card.classList.add(HIGHLIGHT_CLASS);
        window.setTimeout(function () {
            card.classList.remove(HIGHLIGHT_CLASS);
        }, HIGHLIGHT_DURATION_MS);
    }

    function jumpToHash(hash) {
        if (typeof hash !== 'string' || hash.length === 0) {
            return;
        }
        var id = hash.charAt(0) === '#' ? hash.substring(1) : hash;
        if (id === '') {
            return;
        }
        var card = document.getElementById(id);
        if (card === null || !card.matches('[data-pdf-violation-card]')) {
            return;
        }
        // requestAnimationFrame defers the scroll until layout settles
        // — same approach as CheckerReport.tsx, where the rAF was
        // needed to wait for the tab to become visible.
        window.requestAnimationFrame(function () {
            card.scrollIntoView({ behavior: 'smooth', block: 'center' });
            focusCard(card);
            highlightCard(card);
        });
    }

    function bindArrowKeys(list) {
        list.addEventListener('keydown', function (event) {
            var target = event.target;
            if (target === null
                || typeof target.closest !== 'function') {
                return;
            }
            var card = target.closest('[data-pdf-violation-card]');
            if (card === null) {
                return;
            }
            // Only react when focus is on the card itself — leave
            // arrow-key behaviour intact for nested controls (the
            // "jump to page" button, the search input, etc).
            if (target !== card) {
                return;
            }
            var key = event.key;
            if (key !== 'ArrowDown'
                && key !== 'ArrowUp'
                && key !== 'Home'
                && key !== 'End') {
                return;
            }
            var cards = visibleCards();
            if (cards.length === 0) {
                return;
            }
            var idx = cards.indexOf(card);
            if (idx < 0) {
                return;
            }
            event.preventDefault();
            var next = idx;
            if (key === 'ArrowDown') {
                next = Math.min(idx + 1, cards.length - 1);
            } else if (key === 'ArrowUp') {
                next = Math.max(idx - 1, 0);
            } else if (key === 'Home') {
                next = 0;
            } else if (key === 'End') {
                next = cards.length - 1;
            }
            focusCard(cards[next]);
        });
    }

    function bindHashLinks() {
        // Click handler delegated on document — covers the TOC nav,
        // WCAG-appendix table links, and any in-page anchor pointing
        // at #issue-N. The intra-page hashchange event also fires for
        // these clicks, but registering both ensures the highlight
        // flash retriggers when the user clicks the *same* anchor
        // twice (where hashchange would otherwise no-op).
        document.addEventListener('click', function (event) {
            var anchor = event.target;
            while (anchor !== null
                && anchor !== document
                && anchor.tagName !== 'A') {
                anchor = anchor.parentNode;
            }
            if (anchor === null || anchor === document) {
                return;
            }
            var href = anchor.getAttribute('href');
            if (typeof href !== 'string' || href.indexOf('#issue-') !== 0) {
                return;
            }
            jumpToHash(href);
        });

        window.addEventListener('hashchange', function () {
            jumpToHash(window.location.hash);
        });
    }

    function bindFocusableCards() {
        var cards = document.querySelectorAll('[data-pdf-violation-card]');
        for (var i = 0; i < cards.length; i += 1) {
            // -1 = programmatically focusable but not in tab order;
            // arrow-key navigation moves focus between them, the user
            // doesn't tab onto each card separately.
            if (cards[i].getAttribute('tabindex') === null) {
                cards[i].setAttribute('tabindex', '-1');
            }
        }
    }

    function start() {
        var list = document.querySelector('[data-pdf-violation-list]');
        if (list === null) {
            return;
        }
        bindFocusableCards();
        bindArrowKeys(list);
        bindHashLinks();
        // Honour an initial #issue-N in the URL — same UX as pdfMax
        // CheckerReport's scrollToCheck prop.
        if (window.location.hash.indexOf('#issue-') === 0) {
            jumpToHash(window.location.hash);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
})();
