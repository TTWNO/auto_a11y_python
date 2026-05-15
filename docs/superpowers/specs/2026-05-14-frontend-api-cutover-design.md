# Frontend → `/api/v1` Cutover (issue #21)

**Status:** phase 2 complete — only intentional carve-outs remain.
**Tracks:** [#21](https://github.com/CNIB-AccessLabs/auto_a11y/issues/21).
**Prereq:** OpenAPI rollout (commits `c9058d27` … `38121957`).

**2026-05-15 update.** Phase 2 cutover landed. All admin templates that
have a matching `/api/v1` surface now talk to it. The five intentional
carve-outs (polling status endpoints, Drupal-sync NDJSON streams,
automated_tests routes, and the two `testing.api_*` polling endpoints)
are documented below and stay on the legacy blueprint paths until they
get a streaming-friendly successor.

The auth-route blocker (issue noted in "Open issues #1") was resolved
upstream by the `e8f99f18` auth-REST commit's `body.session` flag — the
frontend HTML auth forms now go through `/api/v1/auth/{login,register,
forgot-password,reset-password}` and the `require_login` allow-list in
`auto_a11y/web/app.py` admits those four endpoints for the logged-out
path.

The issue-#51 blocker (catalog read + production-readiness PATCH) was
resolved by landing `GET/PATCH /api/v1/issues/<code>`. Both
`projects/create.html` and `projects/edit.html` now use the new
endpoint; the legacy `/projects/api/test-details/<id>(/production-ready)?`
routes carry an updated `@deprecated` marker pointing at the successor.

The recordings PATCH schema (`RecordingPatch`) was widened to accept
`page_urls` and `discovered_page_ids`, the two scope fields the legacy
`/recordings/<id>/edit` form has always written. `recordings/detail.html`
now PATCHes through `/api/v1/recordings/<id>`.

The `/api/v1` surface is now feature-documented across 163 endpoints. This
spec defines how the admin frontend stops talking to the legacy
blueprint-mounted JSON routes and HTML form-POST handlers, and starts
talking exclusively to `/api/v1`.

This is the issue-#21 follow-up the OpenAPI roadmap deferred (see
`docs/REST_API_ROADMAP.md` §2).

## Scope

In scope:
- Admin templates under `auto_a11y/web/templates/` (excluding `templates/public/`).
- Admin-side standalone JS in `auto_a11y/web/static/js/`.

Out of scope:
- The public share-token frontend (`templates/public/`, `public_bp`). Per
  the roadmap §2, those stay HTML-rendering.
- Legacy `@deprecated` JSON routes themselves. They have a published
  sunset (`2026-09-01`) and outlive the frontend cutover by design — they
  are kept alive for *external* clients during the deprecation window.
  This work makes the frontend stop calling them; their physical removal
  is a separate later PR.
- The auth `/api/v1/auth/login`/`register`/`reset-password` flows. These
  endpoints mint Bearer tokens (no Flask-Login session cookie). The HTML
  login form must remain on the session-establishing legacy route until
  the API grows a session-cookie variant. See "Open issues" below.

## Architecture

### Shared API client

`auto_a11y/web/static/js/api-client.js` — loaded in `base.html`. Two surfaces:

**Imperative**

    window.apiClient.get(path, opts)
    window.apiClient.post(path, body, opts)
    window.apiClient.put(path, body, opts)
    window.apiClient.patch(path, body, opts)
    window.apiClient.delete(path, opts)
    window.apiClient.request(method, path, body, opts)
    window.apiClient.ApiError       // throws on non-2xx; .status, .problem
    window.apiClient.showError(err) // surfaces RFC 7807 details

Paths starting with `/` (other than `/api/`) are auto-prefixed with `/api/v1`.
JSON bodies are stringified; `FormData` bodies pass through for multipart.
Credentials are `same-origin` (Flask-Login session cookie travels with the
request). The pre-existing CSRF wrapper in `base.html` attaches
`X-CSRFToken` to mutations; `api_bp` is `csrf.exempt`-ed so the header is
harmless.

**Declarative**

    <form data-api-form
          data-api-method="DELETE"
          data-api-path="/api/v1/projects/{{ project.id }}"
          data-api-redirect="{{ url_for('projects.list_projects') }}"
          data-api-confirm="{{ ftl('common-are-you-sure') }}">
      ...
    </form>

Dataset keys:

| Attribute | Purpose |
|-----------|---------|
| `data-api-form` | presence marker — element listener intercepts submit |
| `data-api-method` | HTTP method override (`POST`/`PUT`/`PATCH`/`DELETE`); defaults to the form's `method` attribute |
| `data-api-path` | full path; if relative without `/api/` prefix, auto-prefixed |
| `data-api-on-success` | `redirect` (default), `reload`, `toast`, `none` |
| `data-api-redirect` | static URL to navigate to on success |
| `data-api-redirect-template` | templated URL with `{key}` interpolation against response body (e.g. `/projects/{id}`) |
| `data-api-confirm` | `confirm()` prompt before submit |
| `data-api-success-message` | message for toast mode |

A button (or `<a>`) carrying `[data-api-action]` triggers the same flow
without needing a wrapping form:

    <button data-api-action
            data-api-method="POST"
            data-api-path="/api/v1/jobs/{{ job_id }}/cancel"
            data-api-on-success="reload"
            data-api-confirm="{{ ftl('common-confirm-cancel') }}">
      Cancel
    </button>

File inputs trigger multipart submission automatically. The WTForms
`csrf_token` hidden field is dropped on serialisation (the API is
csrf-exempt and Pydantic would otherwise reject it under `extra='forbid'`).

### CSRF and session auth

- `app.py` does `csrf.exempt(api_bp)` already — `/api/v1` accepts requests
  without `X-CSRFToken`.
- Flask-Login session cookie travels via `credentials: 'same-origin'`.
- The existing CSRF wrapper still patches `fetch` / `$.ajax` to add
  `X-CSRFToken` on mutations. This is harmless on `/api/v1` and protects
  any remaining non-API mutation calls in the codebase.

### Error surfacing

`ApiError.problem` carries the RFC 7807 payload. `showError()` posts a
dismissable alert into the existing flash-messages region and writes the
text to the `#notification-live-region` live region for screen readers.

## Phase 1 — landed in this PR

### 1A — shared client + base.html wiring
- `auto_a11y/web/static/js/api-client.js` (new file).
- `auto_a11y/web/templates/base.html` — `<script src="…/api-client.js">`
  added next to `modal.js`.

### 1B — JSON GET fetches migrated
| Legacy path | `/api/v1` path | Shape adapter |
|-------------|----------------|---------------|
| `/projects/api/list` | `/api/v1/projects` | `project.id` → `project._id` (from `Project.to_dict()`) |
| `/projects/api/<id>/websites` | `/api/v1/projects/<id>/websites` | `data.websites` → `data.items` (cursor envelope) |
| `/projects/api/<id>/details` | `/api/v1/projects/<id>` | drops the `{success, project}` wrapper; project fields at top level |
| `/projects/api/<id>/users` | `/api/v1/projects/<id>/test-users` | `data.users` → `data.items`; project-test-users, NOT project members |
| `/projects/api/<id>/discovered-pages` | `/api/v1/projects/<id>/discovered-pages` | `data.discovered_pages` → `data.items`; `page._id` → `page.id` |
| `/projects/<id>/members` (GET) | `/api/v1/projects/<id>/members` | `member.groups[]` → `member.group_ids[]`; available groups gain `is_system` |
| `/members/api/search-users` | `/api/v1/users/search` | identical shape |
| `/reports/job/<id>/status` | `/api/v1/jobs/<id>` | 404 short-circuit preserved via `err.status === 404` branch |

### 1C — Mutation fetches migrated (POST/PATCH/DELETE)
| Legacy | New | Notes |
|--------|-----|-------|
| `POST /scripts/<id>/delete` | `DELETE /api/v1/scripts/<id>` | |
| `POST /scripts/<id>/test` | `POST /api/v1/scripts/<id>/test-runs` | preserves empty body |
| `POST /scripts/<id>/toggle` | `PATCH /api/v1/scripts/<id>` `{enabled}` | new state computed via Jinja `{{ (not script.enabled) | tojson }}` |
| `POST /reports/<id>/delete` | `DELETE /api/v1/reports/<id>` | |
| `POST /reports/job/<id>/drop` | `POST /api/v1/jobs/<id>/cancel` | 404 + 409 silently treated as success |
| `POST /reports/job/<id>/restart` | `POST /api/v1/jobs/<id>/restart` | |
| `POST /share-tokens/share-tokens/<id>/revoke` | `DELETE /api/v1/share-tokens/<id>` | |
| `PATCH /recordings/api/issue/<id>/status` | `PATCH /api/v1/recording-issues/<id>` `{status}` | |
| `POST /projects/<pid>/participants/supervisors/<id>/delete` | `DELETE /api/v1/projects/<pid>/supervisors/<id>` | |
| `POST /projects/<pid>/participants/testers/<id>/delete` | `DELETE /api/v1/projects/<pid>/testers/<id>` | |
| `POST /projects/users/<id>/delete` | `DELETE /api/v1/project-test-users/<id>` | |
| `POST /projects/users/<id>/toggle` | `PATCH /api/v1/project-test-users/<id>` `{enabled}` | |
| `POST /projects/users/<id>/test-login` | `POST /api/v1/project-test-users/<id>/test-login` | preserved success/failure envelope |
| `POST /users/user/<id>/delete` | `DELETE /api/v1/website-test-users/<id>` | |
| `POST /users/user/<id>/toggle` | `PATCH /api/v1/website-test-users/<id>` `{enabled}` | |
| `POST /users/user/<id>/test-login` | `POST /api/v1/website-test-users/<id>/test-login` | |
| `POST /websites/<wid>/schedules/<id>/run-now` | `POST /api/v1/scheduled-tests/<id>/runs` | |
| `POST /websites/<wid>/schedules/<id>/toggle` | `PATCH /api/v1/scheduled-tests/<id>` `{enabled}` | |
| `POST /websites/<id>/discover` | `POST /api/v1/websites/<id>/discover` | |
| `POST /testing/configure` | `PUT /api/v1/testing/config` | |

### 1D — Simple HTML forms migrated to `data-api-form`
Ten forms across nine templates:
- delete-project (modal in `projects/view.html` + `projects/edit.html`)
- delete-group (table row + card view in `groups/list.html`)
- delete-recording (`recordings/list.html`, `recordings/detail.html`)
- delete-discovered-page (`discovered_pages/view.html`)
- delete-project-test-user (`project_users/view.html`)
- create/edit supervisor (`project_participants/create_supervisor.html`, `…/edit_supervisor.html`)
- forgot-password (`auth/forgot_password.html`)

All migrated forms drop the `csrf_token` hidden input. Redirects use
`{{ url_for('…') }}` so they stay route-name driven.

## Phase 2 — landed (2026-05-15)

Phase 2 migrated every remaining admin-template mutation that had a
matching `/api/v1` successor. The work fell into three buckets — they
are all done now.

### 2A — auth-form unblock
- `auto_a11y/web/app.py` `allowed_endpoints` opened up
  `api.auth_login_rest`, `api.auth_register_rest`,
  `api.auth_forgot_password_rest`, `api.auth_reset_password_rest`, and
  the two `api.auth_sso_*_rest` endpoints. The legacy
  `auth.login/register/logout/microsoft_*/google_*` allow-list is kept
  so existing share-link bookmarks and SSO redirects keep working.
- `auth/login.html`, `auth/register.html`, `auth/reset_password.html`,
  `auth/profile.html`, `auth/user_create.html`, `auth/user_edit.html`,
  `auth/user_list.html`, `auth/forgot_password.html` — all now POST
  through `apiClient` and rely on `body.session: true` on the API side
  to mint a Flask-Login cookie.

### 2B — issue-catalog endpoint pair (issue #51)
- New schema `auto_a11y/web/api/schemas/issues.py` with `IssueOut`,
  `IssueMessageTemplateOut`, `IssuePatch`.
- New routes `GET /api/v1/issues/<code>` and `PATCH /api/v1/issues/<code>`
  in `auto_a11y/web/routes/api.py`. Read returns the static catalog
  entry plus the database-managed `production_ready` flag; PATCH is
  partial and currently only mutates `production_ready`.
- `projects/create.html` and `projects/edit.html` — the four legacy
  `/projects/api/test-details/<id>(/production-ready)?` fetches are
  replaced with `apiClient.{get,patch}('/issues/<code>')`. The legacy
  Python routes stay in place with an updated `@deprecated` marker
  pointing at the successor; they will be removed in the cleanup PR.

### 2C — recordings PATCH widening
- `auto_a11y/web/api/schemas/recordings.py` `RecordingPatch` grew two
  optional fields: `page_urls: list[str] | str` and
  `discovered_page_ids: list[str] | str`. Both accept either a list or
  a legacy-shape string (newline-separated for URLs, comma-separated
  for ids) and normalise to a list in the handler.
- `recordings/detail.html` now PATCHes through
  `/api/v1/recordings/<id>` instead of POSTing to the legacy edit
  route.

### 2D — imperative-fetch sweep
The remaining `$.post` / `$.ajax` / bare-`fetch` calls in
`websites/view.html`, `projects/view.html`, `pages/view.html`,
`pages/view_enhanced.html`, `discovered_pages/view.html`,
`recordings/detail.html`, and `testing/dashboard.html` all moved to
`apiClient.{post,patch,delete}`. The new endpoints:

| Old call                                          | New `apiClient` call                                |
|---------------------------------------------------|------------------------------------------------------|
| `$.post('/pages/<id>/test')`                      | `apiClient.post('/pages/<id>/test-runs')`           |
| `$.post('/pages/<id>/cancel-test')`               | `apiClient.post('/pages/<id>/test-runs/latest/cancel')` |
| `$.post('/pages/<id>/delete')`                    | `apiClient.delete('/pages/<id>')`                   |
| `$.post('/websites/<id>/discover')`               | `apiClient.post('/websites/<id>/discover')`         |
| `$.post('/websites/<id>/cancel-discovery')`       | `apiClient.post('/jobs/<job_id>/cancel')`           |
| `$.post('/websites/<id>/test-all')`               | `apiClient.post('/websites/<id>/test-runs')`        |
| `$.post('/websites/<id>/cancel-testing')`         | `apiClient.post('/jobs/<job_id>/cancel')`           |
| `$.post('/websites/<id>/add_page')`               | `apiClient.post('/websites/<id>/pages')`            |
| `fetch('/reports/generate-page-structure')`       | `apiClient.post('/websites/<id>/reports', {type:'page-structure'})` |
| `fetch('/reports/generate-website-report')`       | `apiClient.post('/websites/<id>/reports', {type:'accessibility'})` |
| `fetch('/discovered-pages/<id>/edit')`            | `apiClient.patch('/discovered-pages/<id>')`         |
| `fetch('/recordings/<id>/edit')`                  | `apiClient.patch('/recordings/<id>')`               |
| `fetch('/testing/test-page')`                     | `apiClient.post('/pages/<id>/test-runs')`           |
| `fetch('testing.api_project_websites')`           | `apiClient.get('/projects/<id>/websites')`          |
| `fetch('testing.api_run_tests')`                  | `apiClient.post('/{websites|projects}/<id>/test-runs')` |
| `fetch('/drupal/audits/list')`                    | `apiClient.get('/drupal/audits')`                   |

## Intentional carve-outs (staying on legacy routes)

These endpoints do not have a `/api/v1` successor that fits their wire
shape. Migrating them would require either re-shaping the legacy clients
(many) or building a streaming-friendly REST variant (Server-Sent Events
or chunked JSON arrays). Both are out of scope for the cutover.

### Polling/status endpoints (no `/api/v1` shadow)

- `GET /websites/<id>/test-status` — used in `websites/view.html`,
  `projects/view.html`, `base.html`.
- `GET /websites/<id>/discovery-status` — same callers.
- `GET /pages/<id>/test-status` — used in `pages/view.html`,
  `websites/view.html`.
- `GET /pdfs/<id>/audit-status` — polled by `pdf_audit_progress.js`.

### Drupal-sync NDJSON streams

The `/api/v1/drupal/projects/<id>/upload` and `/import-pages` endpoints
exist but return summary JSON only. The legacy
`/drupal/projects/<id>/sync/upload` and `/sync/import-pages` stream
NDJSON for the progress UI. Migrating requires SSE-style streaming on
the REST surface.

- `drupal_sync/sync_card.html` — sync status, upload, discovered-pages,
  recordings.
- `drupal_sync/project_sync.html` — sync status, upload, import-pages,
  filter-options.

### `automated_tests` (no successor route)

- `automated_tests/list.html` — `/automated_tests/projects/<id>/filter`
  and `/upload`.
- `drupal_sync/project_sync.html` —
  `/automated_tests/projects/<id>/filter-options` (shares the same
  blueprint).

### testing.api_* polling endpoints

- `testing.api_active_tests` and `testing.api_stats` — used in
  `testing/dashboard.html`. The `/api/v1/jobs/active` and
  `/api/v1/jobs/stats` routes exist but project a different shape
  (generic job records vs. the per-website UI shape). Wiring the
  dashboard to the new shape is a future UI-rework task.

## Phase 2 — earlier deferred follow-ups (now resolved or carve-outs)

The remaining work is *not* mechanical and is explicitly out of scope for
this PR. Templates carrying `{# TODO(issue-21): migrate form to /api/v1 — needs custom body builder #}`:

### Complex config forms (Jinja-rendered nested config)

| Template | Reason | Suggested approach |
|----------|--------|--------------------|
| `projects/create.html` | touchpoint-by-touchpoint checkboxes pack into nested `config.touchpoints[id]={enabled, tests:{…}}`, plus `ai_tests`, `stealth_mode`, `font_accessibility` | Inline JS pre-submit handler that walks the form into the right `config` dict, then `apiClient.post('/projects', body)` |
| `projects/edit.html` | same + `titleLengthLimit` / `headingLengthLimit` validation, `font_accessibility` textarea-line parsing | same |
| `websites/edit.html` | `scraping_config` nested fields | inline body builder, `apiClient.patch('/websites/{id}', body)` |
| `admin_settings/index.html` | per-section env-var dynamic forms | per-section JS handler, `apiClient.patch('/admin/settings/{section_id}', body)` |
| `schedules/form.html` | cron + scope + targets composition | inline body builder, `apiClient.post('/scheduled-tests', body)` / `apiClient.put('/scheduled-tests/{id}', body)` |

### Forms with field-shape mismatches

| Template | Mismatch | Suggested fix |
|----------|----------|---------------|
| `groups/edit.html` | `perm_<resource>` flat fields → `permissions: {resource: level}` | JS pre-submit packs perms |
| `project_participants/create_tester.html` / `edit_tester.html` | `assistive_tech` string → `list[str]` | split on `,` in JS |
| `project_users/create.html` / `edit.html` | `login_config_*` flat fields → nested `login_config: {…}`; `roles` string → `list[str]` | JS pre-submit |
| `website_users/create.html` / `edit.html` | same | same |
| `auth/profile.html` | two-action form (`update_profile`/`change_password`); current-password gate not in API | split into two `<form data-api-form>` and decide on current-password verification |

### Forms blocked on missing API features

| Template | Blocker | Action |
|----------|---------|--------|
| `auth/login.html` | `/api/v1/auth/login` issues Bearer tokens, does not call `login_user()` | leave on legacy route until API grows a session-cookie variant |
| `auth/register.html` | same | same |
| `auth/reset_password.html` | same | same |
| `auth/user_create.html` / `user_edit.html` / `user_list.html` | admin user-management — fields need verification | per-form audit pending |
| `pages/edit.html` | not yet examined | follow-up |
| `pages/test_matrix.html` / `test_matrix_v2.html` | complex state matrix UI | follow-up |
| `pdf/add.html` / `pdf/detail.html` | multipart file upload + scope semantics | follow-up |
| `scripts/create.html` | not yet examined | follow-up |
| `recordings/upload.html` | multipart with multi-language JSON files + content files + complex metadata | follow-up |

### View-template fetches not yet migrated

In `projects/view.html`:
- Discovery form (`#discoveryForm`) — submits to `/websites/<id>/discover`; partially migrated as a fetch but still uses inline `$.ajax`. Suggested: drop jQuery for `apiClient.post`.
- Cancel-discovery — `POST /websites/<id>/cancel-discovery`. The /api/v1 alternative is `POST /api/v1/jobs/<job_id>/cancel`.
- Test-all-websites form — semantic mismatch: legacy `take_screenshot` / `run_ai` checkboxes are not surfaced on `POST /api/v1/projects/<id>/test-runs` (now project-level config). Product decision needed: remove the checkboxes or wire them through project config.
- Add-website form — flat fields; could be migrated to `data-api-form` with body builder packing `scraping_config: ScrapingConfig().to_dict()` (it's not in the form, so API would receive empty config — verify default behaviour).
- Website status polling (`$.get('/websites/<id>/test-status')` every 3s) — no `/api/v1` equivalent. Could use `GET /api/v1/jobs/<id>` if the row tracks job ids; otherwise leave.

### Polling endpoints with no `/api/v1` equivalent (leave alone)

- `/websites/<id>/test-status` (3-second poll)
- `/websites/<id>/discovery-status` (3-second poll)
- `/pdfs/<id>/audit-status` (polled by `pdf_audit_progress.js`)
These are blueprint-internal status endpoints; they aren't `@deprecated`
and don't have shadowing in `/api/v1`. Leaving them is consistent with the
"no more dual-routing" directive — there's no `/api/v1` route to be
dual-routed against.

### `@deprecated` routes with no successor yet
Per the existing decorator comments:
- `/projects/api/test-details/<id>` and `…/production-ready` — blocked on
  issue #51 (`PATCH /api/v1/issues/<code>`).
- `/automated_tests/projects/<id>/{filter,filter-options,upload}` — no successor exists yet; not `@deprecated` either.
- `/drupal/projects/<id>/sync/upload` and `…/sync/import-pages` — `/api/v1`
  variants exist but return summary shapes; the legacy versions stream
  NDJSON to drive a progress bar. Migration requires UI rework.

## Coding patterns

### Imperative fetch migration

Old:

    fetch(`/scripts/${id}/delete`, { method: 'POST' })
        .then(r => r.json())
        .then(data => { if (!data.success) alert(data.error); });

New:

    try {
        await window.apiClient.delete(`/scripts/${id}`);
    } catch (err) {
        window.apiClient.showError(err);
    }

### Form migration

Old:

    <form method="POST" action="{{ url_for('groups.delete_group', group_id=g.id) }}">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}"/>
        <button type="submit" class="btn btn-high">{{ ftl('common-delete') }}</button>
    </form>

New:

    <form data-api-form
          data-api-method="DELETE"
          data-api-path="/api/v1/groups/{{ g.id }}"
          data-api-redirect="{{ url_for('groups.list_groups') }}"
          data-api-confirm="{{ ftl('common-are-you-sure') }}">
        <button type="submit" class="btn btn-high">{{ ftl('common-delete') }}</button>
    </form>

### Complex form with body builder

Old (Jinja-rendered fields, server-side parsing):

    <form method="POST" action="{{ url_for('projects.create_project') }}">
        ...
        <input type="checkbox" name="touchpoint_headings"/>
        <input type="checkbox" name="test_headings_ErrFoo"/>
        ...
    </form>

New:

    <form id="createProjectForm" aria-labelledby="...">
        ... same fields ...
    </form>
    <script>
    document.getElementById('createProjectForm').addEventListener('submit', async (ev) => {
        ev.preventDefault();
        const fd = new FormData(ev.target);
        const body = {
            name: fd.get('name'),
            description: fd.get('description') || '',
            config: buildProjectConfig(fd),
        };
        try {
            const created = await window.apiClient.post('/projects', body);
            location.assign(`/projects/${created.id}`);
        } catch (err) {
            window.apiClient.showError(err);
        }
    });

    function buildProjectConfig(fd) {
        const touchpoints = {};
        for (const id of TOUCHPOINT_IDS) {
            const tests = {};
            for (const testId of TEST_IDS[id] || []) {
                tests[testId] = fd.get(`test_${id}_${testId}`) === 'on';
            }
            touchpoints[id] = { enabled: fd.get(`touchpoint_${id}`) === 'on', tests };
        }
        return {
            wcag_level: fd.get('wcag_level') || 'AA',
            page_load_strategy: fd.get('page_load_strategy') || 'networkidle2',
            headless_browser: fd.get('headless_browser') || 'default',
            touchpoints,
            // ...etc
        };
    }
    </script>

## Open issues / followups

1. ~~**Auth API session cookie.** `/api/v1/auth/login` mints a Bearer
   token instead of calling `login_user()`.~~ **Resolved** — the
   `body.session: true` opt-in (commit `e8f99f18`) was already there
   when this doc was first written; the actual blocker was the
   `require_login` allow-list in `app.py`. The 2026-05-15 cutover
   opened the gate for the four auth endpoints (login, register,
   forgot-password, reset-password) plus the two SSO endpoints.

2. ~~**`/projects/api/test-details/<id>` semantic mismatch.**~~
   **Resolved** — the `GET/PATCH /api/v1/issues/<code>` endpoints
   landed in the 2026-05-15 cutover. Both legacy routes now carry an
   updated `@deprecated` marker pointing at the successor.

3. **Discovery streaming.** `/drupal/projects/<id>/sync/upload` and
   `/sync/import-pages` stream NDJSON for progress display. The `/api/v1`
   equivalents are summary-only. Either keep streaming on legacy
   (current state), or design a streaming `/api/v1` variant (Server-Sent
   Events or chunked JSON arrays).

4. **Test-all checkbox semantics.** `projects/view.html` test-all modal
   has `take_screenshot` and `run_ai` checkboxes that no longer fit the
   `/api/v1/projects/<id>/test-runs` body shape. Product decision needed:
   either move these to per-project config and remove from the modal, or
   add them to the API contract.

5. **Legacy mutation route removal.** After this PR, the legacy form-POST
   handlers in `routes/projects.py` / `routes/websites.py` / etc. for the
   migrated forms have no callers. They can be removed in a separate
   cleanup PR (keep them through the sunset window if there's any chance
   of external callers — internal HTML form POSTs are not external API
   consumers, so safe to remove sooner).

## Testing

- `mypy`, `pyright`, `ty` all pass (no Python changes that would shift types).
- Manual smoke test of each migrated flow recommended before merging:
  delete-project, delete-group, delete-recording, member add/remove,
  member group toggle, project test user delete/toggle/test-login,
  schedule run-now/toggle, script delete/toggle/test, report
  delete/cancel/restart, share-token revoke.
- `api-client.js` itself has no unit tests yet. Suggested: add Playwright
  smoke tests in `tests/playwright/` that exercise one form per category
  (delete, mutation-button, declarative-form).
