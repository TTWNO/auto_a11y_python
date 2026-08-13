/**
 * Drives pdfMax's AuditingScreen while a standalone scan runs.
 *
 * The audit happens on a worker thread; this polls
 * `/pdf-scan/<id>/progress` and moves the bar, the step text and the
 * percentage to match. When the manifest says the scan is no longer
 * auditing, it loads the same URL again — which the server then answers
 * with the report instead of this screen.
 *
 * The server already rendered a truthful first frame, so nothing here is
 * needed for the page to be correct — only for it to keep up.
 */
(function () {
    'use strict';

    var root = document.getElementById('pdf-scan-auditing');
    if (!root) {
        return;
    }

    var bar = document.getElementById('pdf-scan-progress-bar');
    var fill = document.getElementById('pdf-scan-progress-fill');
    var stepEl = document.getElementById('pdf-scan-progress-step');
    var percentEl = document.getElementById('pdf-scan-progress-percent');
    var cancelForm = document.getElementById('pdf-scan-cancel-form');
    var cancelBtn = document.getElementById('pdf-scan-cancel-btn');

    var progressUrl = root.getAttribute('data-progress-url');
    var cancelUrl = root.getAttribute('data-cancel-url');
    var resultsUrl = root.getAttribute('data-results-url');
    var csrfToken = root.getAttribute('data-csrf-token') || '';

    /* The no-JS fallback reloads the whole page every five seconds. Now
     * that this is running, drop it — a reload mid-poll would throw away
     * the live region and re-announce the screen from scratch. */
    var refreshMeta = document.getElementById('pdf-scan-noscript-refresh');
    if (refreshMeta && refreshMeta.parentNode) {
        refreshMeta.parentNode.removeChild(refreshMeta);
    }

    var POLL_MS = 700;
    var stopped = false;

    /* aria-valuetext carries the same words the sighted reader sees, so
     * the announcement is "42%", not a bare number in an unknown unit. */
    function valueText(percent) {
        var template = bar ? bar.getAttribute('data-value-template') : null;
        if (template) {
            return template.replace('%s', String(percent));
        }
        return String(percent) + '%';
    }

    function render(percent, step) {
        if (fill) {
            fill.style.width = percent + '%';
        }
        if (bar) {
            bar.setAttribute('aria-valuenow', String(percent));
            bar.setAttribute('aria-valuetext', valueText(percent));
        }
        if (percentEl) {
            percentEl.textContent = percent + '%';
        }
        /* Only write when it changes: the live region is polite, but
         * rewriting identical text still queues an announcement in some
         * screen readers. */
        if (step && stepEl && stepEl.textContent.trim() !== step) {
            stepEl.textContent = step;
        }
    }

    function poll() {
        if (stopped) {
            return;
        }
        fetch(progressUrl, {
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json' }
        }).then(function (response) {
            if (!response.ok) {
                throw new Error('progress request failed: ' + response.status);
            }
            return response.json();
        }).then(function (data) {
            render(
                typeof data.percent === 'number' ? data.percent : 0,
                typeof data.step === 'string' ? data.step : ''
            );
            if (data.done) {
                stopped = true;
                /* The report lives at this same URL, so a plain load
                 * lands on it — and leaves a sane history entry rather
                 * than a screen the user can never get back to. */
                window.location.assign(resultsUrl);
                return;
            }
            window.setTimeout(poll, POLL_MS);
        }).catch(function (error) {
            /* A dropped poll is not a failed audit — the worker is
             * unaffected by whatever happened to this request. Back off
             * and keep trying; the audit will still finish. */
            if (window.console && window.console.warn) {
                window.console.warn('Scan progress poll failed', error);
            }
            window.setTimeout(poll, POLL_MS * 4);
        });
    }

    if (cancelForm) {
        cancelForm.addEventListener('submit', function (event) {
            /* Without JS this posts and the server redirects; with JS,
             * ask over fetch so the page keeps its live region and can
             * report that cancelling is under way. */
            event.preventDefault();
            if (cancelBtn) {
                cancelBtn.disabled = true;
            }
            if (stepEl) {
                stepEl.textContent = root.getAttribute('data-cancelling-text')
                    || stepEl.textContent;
            }
            fetch(cancelUrl, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'X-CSRFToken': csrfToken,
                    'Accept': 'application/json'
                }
            }).catch(function () {
                /* The next poll reports the real state either way. */
                if (cancelBtn) {
                    cancelBtn.disabled = false;
                }
            });
        });
    }

    window.setTimeout(poll, POLL_MS);
}());
