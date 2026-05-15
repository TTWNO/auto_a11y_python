// Poll-based progress watcher for the in-flight PDF audit.
//
// The audit pipeline (auto_a11y/pdf/audit/pipeline.py) calls a progress
// callback at every stage and per-page within long collectors; that
// callback writes to JobManager via PdfAuditJob._on_progress. This file
// fetches the latest job state from /pdfs/<id>/audit-status and renders
// the result into the existing _audit_progress.html partial, which would
// otherwise sit at 0% / "Waiting for the next audit stage…" forever.
//
// The page reloads itself when the audit terminates (audited / failed /
// cancelled) so the operator sees the new state without manual refresh.

(function () {
    'use strict';

    var POLL_INTERVAL_MS = 2000;

    function findHosts() {
        return document.querySelectorAll('[data-pdf-audit-progress]');
    }

    function fetchStatus(pdfId) {
        // TODO: no /api/v1 equivalent yet — /api/v1/pdf-documents/<id> GET
        // returns metadata only, not job progress. The legacy
        // /pdfs/<id>/audit-status endpoint exposes the in-flight job state
        // via JobManager which has no REST counterpart. Verify before
        // sunset 2026-09-01.
        return fetch('/pdfs/' + encodeURIComponent(pdfId) + '/audit-status', {
            headers: { 'Accept': 'application/json' },
            credentials: 'same-origin'
        }).then(function (resp) {
            if (!resp.ok) {
                throw new Error('audit-status HTTP ' + resp.status);
            }
            return resp.json();
        });
    }

    function applyState(host, state) {
        var bar = host.querySelector('[data-pdf-progress-bar]');
        var stage = host.querySelector('[data-pdf-progress-stage]');
        var job = state && state.job;
        var progress = (job && job.progress) || {};
        var current = typeof progress.current === 'number' ? progress.current : null;
        var total = typeof progress.total === 'number' && progress.total > 0
            ? progress.total
            : 100;
        var message = progress.message || progress.stage || null;

        if (bar !== null && current !== null) {
            var percent = Math.max(0, Math.min(100, Math.round((current / total) * 100)));
            bar.style.width = percent + '%';
            bar.setAttribute('aria-valuenow', String(percent));
            var sr = bar.querySelector('.visually-hidden');
            if (sr !== null) {
                sr.textContent = percent + '%';
            }
        }
        if (stage !== null && message !== null) {
            stage.textContent = message;
        }
    }

    // Doc-status values that indicate the audit has finished. Only used
    // when we actually observe a transition away from the initial
    // "auditing" status — never on the first poll, to avoid reload-loops
    // when the page is rendered against a stale or already-terminal
    // record.
    var TERMINAL_DOC_STATUSES = ['audited', 'audit_failed'];

    function watch(host) {
        var pdfId = host.getAttribute('data-pdf-audit-progress');
        if (!pdfId) {
            return;
        }
        var initialStatus = host.getAttribute('data-pdf-initial-status') || '';
        // The progress partial only renders when status === 'auditing',
        // so any other initial value is a render race we shouldn't react
        // to with a reload — bail out and let the user refresh manually.
        if (initialStatus !== 'auditing') {
            return;
        }
        var stopped = false;

        function tick() {
            if (stopped) {
                return;
            }
            fetchStatus(pdfId).then(function (state) {
                applyState(host, state);
                var docStatus = state && state.doc_status;
                // Only reload when doc_status genuinely transitioned out
                // of 'auditing' — ignore stale job.status fields, ignore
                // the very first poll if it somehow already shows a
                // terminal value.
                if (
                    docStatus
                    && docStatus !== initialStatus
                    && TERMINAL_DOC_STATUSES.indexOf(docStatus) !== -1
                ) {
                    stopped = true;
                    window.location.reload();
                    return;
                }
                setTimeout(tick, POLL_INTERVAL_MS);
            }).catch(function () {
                // Transient errors (server restart, brief network blip)
                // are non-fatal — back off slightly and retry rather
                // than aborting the watch.
                if (!stopped) {
                    setTimeout(tick, POLL_INTERVAL_MS * 2);
                }
            });
        }

        // Kick off the first poll quickly so the bar moves off 0%
        // shortly after the page paints.
        setTimeout(tick, 250);
    }

    function start() {
        var hosts = findHosts();
        for (var i = 0; i < hosts.length; i += 1) {
            watch(hosts[i]);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
})();
