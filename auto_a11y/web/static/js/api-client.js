/*
 * Auto A11y — frontend ⇄ /api/v1 client.
 *
 * The frontend talks to the platform exclusively through /api/v1. Legacy
 * server-rendered POST routes were retired; HTML pages stay, but mutations
 * round-trip through the documented JSON contract.
 *
 * Three surfaces:
 *
 *   1. Imperative (window.apiClient.{get,post,put,patch,delete,request}).
 *      Auto-prefixes /api/v1 when the path doesn't already start with /api/.
 *      Returns parsed JSON on 2xx, throws ApiError(status, problem) otherwise.
 *
 *   2. Declarative <form data-api-form ...>:
 *        data-api-method, data-api-path
 *        data-api-on-success="redirect" | "reload" | "toast" | "none"
 *        data-api-redirect, data-api-redirect-template (interpolated with response)
 *        data-api-confirm="prompt before submit"
 *        data-api-success-message
 *      File inputs trigger multipart; otherwise FormData → JSON.
 *
 *   3. Declarative <button data-api-action ...> / <a data-api-action ...>:
 *      Same dataset keys (defaults to method=POST, on-success=reload).
 *      Optional data-api-body='{"json": "literal"}'.
 *
 * RFC 7807 problem details from the API are surfaced via the
 * live alert region; the imperative path callers may catch and present
 * their own UI.
 */
(function () {
    'use strict';

    const API_BASE = '/api/v1';

    class ApiError extends Error {
        constructor(status, problem) {
            const detail =
                (problem && (problem.detail || problem.title)) ||
                `HTTP ${status}`;
            super(detail);
            this.name = 'ApiError';
            this.status = status;
            this.problem = problem || {};
        }
    }

    function resolveUrl(path) {
        if (!path) throw new Error('apiClient: path is required');
        if (/^https?:\/\//.test(path)) return path;
        if (path.startsWith('/api/')) return path;
        return `${API_BASE}${path.startsWith('/') ? path : '/' + path}`;
    }

    async function request(method, path, body, options) {
        const opts = options || {};
        const init = {
            method: String(method).toUpperCase(),
            headers: Object.assign({}, opts.headers || {}),
            credentials: 'same-origin',
        };

        if (body instanceof FormData) {
            init.body = body;
        } else if (body !== undefined && body !== null) {
            init.headers['Content-Type'] = 'application/json';
            init.body = typeof body === 'string' ? body : JSON.stringify(body);
        }

        if (opts.accept) init.headers['Accept'] = opts.accept;

        const res = await fetch(resolveUrl(path), init);
        const contentType = res.headers.get('content-type') || '';

        let payload = null;
        if (res.status !== 204) {
            if (
                contentType.includes('application/json') ||
                contentType.includes('application/problem+json')
            ) {
                try {
                    payload = await res.json();
                } catch (e) {
                    payload = null;
                }
            } else if (contentType) {
                try {
                    payload = await res.text();
                } catch (e) {
                    payload = null;
                }
            }
        }

        if (!res.ok) {
            const problem =
                payload && typeof payload === 'object'
                    ? payload
                    : { detail: typeof payload === 'string' ? payload : null };
            throw new ApiError(res.status, problem);
        }
        return payload;
    }

    function isFormFieldEmpty(value) {
        return value instanceof File && value.size === 0 && value.name === '';
    }

    function serializeForm(form) {
        const fd = new FormData(form);

        let hasFile = false;
        for (const value of fd.values()) {
            if (value instanceof File && !isFormFieldEmpty(value)) {
                hasFile = true;
                break;
            }
        }
        if (hasFile) {
            // Drop the WTForms CSRF token field; /api/v1 is csrf-exempt and the
            // field would arrive as an unexpected JSON property under Pydantic.
            fd.delete('csrf_token');
            return fd;
        }

        const obj = {};
        for (const [key, value] of fd.entries()) {
            if (key === 'csrf_token') continue;
            if (value instanceof File) continue; // empty file inputs
            if (Object.prototype.hasOwnProperty.call(obj, key)) {
                if (!Array.isArray(obj[key])) obj[key] = [obj[key]];
                obj[key].push(value);
            } else {
                obj[key] = value;
            }
        }
        // Surface unchecked checkboxes as false rather than omitting them, so
        // PATCH/PUT requests can flip a flag off. We discover them by walking
        // the form's checkbox elements.
        const checkboxes = form.querySelectorAll('input[type="checkbox"][name]');
        checkboxes.forEach((cb) => {
            if (!cb.checked && !Object.prototype.hasOwnProperty.call(obj, cb.name)) {
                obj[cb.name] = false;
            } else if (cb.checked && obj[cb.name] === cb.value) {
                // Coerce common "true"/"on"/"1" strings to a real boolean
                // when the field has no explicit value attribute.
                if (!cb.hasAttribute('value') || cb.value === 'on') {
                    obj[cb.name] = true;
                }
            }
        });
        return obj;
    }

    function interpolate(template, data) {
        return template.replace(/\{([^}]+)\}/g, (_, expr) => {
            const parts = expr.split('.');
            let value = data;
            for (const part of parts) {
                if (value == null) return '';
                value = value[part];
            }
            return value == null ? '' : String(value);
        });
    }

    function announce(message) {
        const live = document.getElementById('notification-live-region');
        if (live) {
            live.textContent = '';
            // Force re-announcement on repeated identical messages.
            window.setTimeout(() => {
                live.textContent = message;
            }, 50);
        }
    }

    function showAlert(message, severity) {
        // The notifications region is always in the document now, so target it by
        // id rather than by matching on its classes.
        const container = document.getElementById('notification-region');
        const alertClass =
            severity === 'success'
                ? 'alert-pass'
                : severity === 'warning'
                ? 'alert-medium'
                : severity === 'info'
                ? 'alert-info'
                : 'alert-high';

        const div = document.createElement('div');
        div.className = `alert ${alertClass} alert-dismissible fade show`;
        div.setAttribute('role', severity === 'error' ? 'alert' : 'status');
        div.textContent = message;
        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'btn-close';
        close.setAttribute('data-bs-dismiss', 'alert');
        // This is the accessible name of the only control that clears the message,
        // so it must not be hardcoded English. base.html publishes the translation.
        close.setAttribute('aria-label', window.i18nDismiss || 'Dismiss');
        div.appendChild(close);

        if (container) {
            container.appendChild(div);
        } else {
            const newSection = document.createElement('section');
            newSection.className = 'container-fluid mt-3';
            newSection.appendChild(div);
            const main = document.getElementById('main-content');
            if (main && main.parentElement) {
                main.parentElement.insertBefore(newSection, main);
            } else {
                document.body.insertBefore(newSection, document.body.firstChild);
            }
        }
        announce(message);
    }

    function showError(err) {
        const message =
            (err && err.problem && (err.problem.detail || err.problem.title)) ||
            (err && err.message) ||
            'Request failed';
        showAlert(message, 'error');
    }

    function showSuccess(message) {
        if (message) showAlert(message, 'success');
    }

    function dataFlag(el, key, fallback) {
        const value = el.dataset[key];
        if (value === undefined) return fallback;
        return value;
    }

    function resolveSuccess(target, data) {
        const onSuccess = dataFlag(target, 'apiOnSuccess', 'redirect');
        const successMsg = dataFlag(target, 'apiSuccessMessage', '');

        if (onSuccess === 'none') {
            if (successMsg) showSuccess(successMsg);
            return;
        }
        if (onSuccess === 'toast') {
            showSuccess(successMsg || 'Done');
            return;
        }
        if (onSuccess === 'reload') {
            location.reload();
            return;
        }
        // redirect (default)
        const tpl = dataFlag(target, 'apiRedirectTemplate', '');
        const plain = dataFlag(target, 'apiRedirect', '');
        let url = '';
        if (tpl && data) url = interpolate(tpl, data);
        if (!url) url = plain;
        if (url) {
            location.assign(url);
        } else {
            location.reload();
        }
    }

    function disableSubmitter(form) {
        const submit = form.querySelector(
            '[type="submit"]:not([disabled]), button[type="submit"]:not([disabled])'
        );
        if (submit) {
            submit.disabled = true;
            submit.dataset._origAriaBusy = submit.getAttribute('aria-busy') || '';
            submit.setAttribute('aria-busy', 'true');
        }
        return submit;
    }

    function reenableSubmitter(submit) {
        if (!submit) return;
        submit.disabled = false;
        if (submit.dataset._origAriaBusy) {
            submit.setAttribute('aria-busy', submit.dataset._origAriaBusy);
        } else {
            submit.removeAttribute('aria-busy');
        }
        delete submit.dataset._origAriaBusy;
    }

    async function submitApiForm(form, ev) {
        ev.preventDefault();
        const method = (
            form.dataset.apiMethod ||
            form.getAttribute('method') ||
            'POST'
        ).toUpperCase();
        const path = form.dataset.apiPath || form.getAttribute('action');
        if (!path) {
            console.error('apiClient: form is missing data-api-path / action', form);
            return;
        }
        const confirmMsg = form.dataset.apiConfirm;
        if (confirmMsg && !window.confirm(confirmMsg)) return;

        const submit = disableSubmitter(form);
        try {
            const body = method === 'GET' || method === 'DELETE'
                ? undefined
                : serializeForm(form);
            const data = await request(method, path, body);
            resolveSuccess(form, data);
        } catch (err) {
            showError(err);
            reenableSubmitter(submit);
        }
    }

    async function executeApiAction(target, ev) {
        ev.preventDefault();
        const method = (target.dataset.apiMethod || 'POST').toUpperCase();
        const path = target.dataset.apiPath;
        if (!path) {
            console.error('apiClient: action element is missing data-api-path', target);
            return;
        }
        const confirmMsg = target.dataset.apiConfirm;
        if (confirmMsg && !window.confirm(confirmMsg)) return;

        const wasDisabled = target.disabled;
        target.disabled = true;
        target.setAttribute('aria-busy', 'true');
        try {
            let body;
            if (method !== 'GET' && method !== 'DELETE' && target.dataset.apiBody) {
                try {
                    body = JSON.parse(target.dataset.apiBody);
                } catch (e) {
                    body = target.dataset.apiBody;
                }
            }
            const data = await request(method, path, body);
            resolveSuccess(target, data);
        } catch (err) {
            showError(err);
            target.disabled = wasDisabled;
            target.removeAttribute('aria-busy');
        }
    }

    document.addEventListener('submit', function (ev) {
        const form = ev.target;
        if (!(form instanceof HTMLFormElement)) return;
        if (!form.hasAttribute('data-api-form')) return;
        submitApiForm(form, ev);
    });

    document.addEventListener('click', function (ev) {
        const trigger =
            ev.target instanceof Element
                ? ev.target.closest('[data-api-action]')
                : null;
        if (!trigger) return;
        // If the trigger is a submit button inside a data-api-form, the form
        // handler will deal with it.
        if (trigger.tagName === 'BUTTON' && trigger.type === 'submit') {
            const form = trigger.form;
            if (form && form.hasAttribute('data-api-form')) return;
        }
        executeApiAction(trigger, ev);
    });

    window.apiClient = {
        request,
        get: (path, opts) => request('GET', path, undefined, opts),
        post: (path, body, opts) => request('POST', path, body, opts),
        put: (path, body, opts) => request('PUT', path, body, opts),
        patch: (path, body, opts) => request('PATCH', path, body, opts),
        delete: (path, opts) => request('DELETE', path, undefined, opts),
        ApiError,
        showError,
        showSuccess,
    };
})();
