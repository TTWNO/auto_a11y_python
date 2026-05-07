# REST API Audit & Roadmap

**Status:** draft, awaiting review
**Tracks:** [#21](https://github.com/CNIB-AccessLabs/auto_a11y/issues/21) (umbrella) — [#26](https://github.com/CNIB-AccessLabs/auto_a11y/issues/26) (this doc) — [#27](https://github.com/CNIB-AccessLabs/auto_a11y/issues/27) (implementation)

This document is the design output of the planning sub-task of #21. It catalogs every server route in `auto_a11y/web/routes/`, maps each to a target REST endpoint, marks current coverage in `auto_a11y/web/routes/api.py`, and proposes a phased rollout. **No code is changed in the same PR as this doc.**

## 1. Goals

1. Make every feature reachable through a versioned REST API (`/api/v1`).
2. Use the API from the frontend so the API surface is exercised by real users, not just integrators.
3. Land it incrementally — never one mega-PR.

## 2. Non-goals

- Migrating the frontend to consume the API in this stream of work. That is its own follow-up tracked under #21.
- Re-architecting authentication. The API will keep using Flask-Login session cookies; token-based auth is a separate future concern.
- Replacing the public share-token routes (`/t/<token>/...`). They remain HTML-rendering routes.

## 3. Inventory at a glance

| Source | Routes | Has REST equivalent? |
|---|---|---|
| `api.py` (`/api/v1`) | 31 | n/a — these *are* the API |
| `projects.py` | 16 (6 mixed in as `/projects/api/...`) | partial |
| `websites.py` | 15 (1 as `/websites/api/list`) | partial |
| `pages.py` | 8 | partial |
| `testing.py` | 17 (8 as `/testing/api/...`) | partial |
| `reports.py` | 19 | none |
| `recordings.py` | 9 (3 as `/recordings/api/...`) | none |
| `schedules.py` | 9 | none |
| `scripts.py` | 9 | none |
| `pdf.py` | 15 | none |
| `share_tokens.py` | 5 | none |
| `members.py` | 5 (1 as `/members/api/search-users`) | none |
| `groups.py` | 4 | none |
| `project_users.py` | 7 | none |
| `project_participants.py` | 7 | none |
| `website_users.py` | 7 | none |
| `discovered_pages.py` | 3 | none |
| `auth.py` | 15 | none — session/HTML only |
| `admin_settings.py` | 3 | none |
| `drupal_sync.py` | 10 | none |
| `automated_tests.py` | 0 (helper module) | n/a |
| `desktop.py` | 1 | n/a (desktop-only sidecar) |
| `demo.py` | 6 | n/a (demo sandbox) |
| `public.py` | 7 | n/a (token-protected HTML) |

There are **~228 user-facing endpoints in total**; 31 are already in `api.py`, ~18 are ad-hoc JSON endpoints living inside HTML blueprints (`/projects/api/...`, `/testing/api/...`, `/recordings/api/...`, `/websites/api/list`, `/members/api/search-users`) that should be folded into `/api/v1`.

## 4. Conventions (locked in for #27 and beyond)

### 4.1 URL prefix and versioning

- Mounted at `/api/v1` (already configured in `auto_a11y/web/app.py:228`).
- Future breaking changes go to `/api/v2`. Within a major version, only additive changes.

### 4.2 Resource naming

- Plural, kebab-cased: `/projects`, `/websites`, `/pages`, `/test-results`, `/share-tokens`, `/scheduled-tests`.
- Nested only one level deep, at most: `/projects/<id>/websites`, `/websites/<id>/pages`. Deeper relationships go through top-level resources with filter query params: `/test-results?page_id=<id>`, not `/projects/<id>/websites/<id>/pages/<id>/test-results`.

### 4.3 Verbs and status codes

| Action | Verb | Success status |
|---|---|---|
| List | `GET /resource` | 200 |
| Get one | `GET /resource/<id>` | 200 |
| Create | `POST /resource` | 201 (with `Location` header) |
| Replace | `PUT /resource/<id>` | 200 |
| Patch | `PATCH /resource/<id>` | 200 |
| Delete | `DELETE /resource/<id>` | 204 |
| Action verb | `POST /resource/<id>/<action>` | 202 if async, 200 if sync |

### 4.4 Response shape

The current API uses `{"success": true, ...}` envelopes. **#27 will move to bare resource bodies** for 2xx responses (no `success` key, no top-level wrapping), because:

- It's idiomatic REST and lets clients use `response.json()` directly as the resource.
- `success: true` is redundant with the HTTP status code.

**Migration plan:** #27 introduces new endpoints with the bare-body shape, but does **not** remove the existing `success`-wrapped responses. Wrapped endpoints get `Deprecation: true` and `Sunset: <date>` HTTP headers (RFC 8594) pointing at the frontend-migration cut-over. The frontend-migration PR is what actually deletes them, once nothing on our side calls them anymore.

Errors use [RFC 7807 Problem Details](https://www.rfc-editor.org/rfc/rfc7807):

```json
{
  "type": "https://auto-a11y/errors/validation",
  "title": "Validation failed",
  "status": 400,
  "detail": "name: must be ≤ 200 characters",
  "instance": "/api/v1/projects",
  "errors": [{"field": "name", "code": "too_long", "message": "..."}]
}
```

### 4.5 Pagination

- Cursor-based for any endpoint that can return >100 rows (test results, pages, recordings).
- Query params: `?limit=50&cursor=<opaque>`; response includes `next_cursor` (null when exhausted).
- Page-number pagination is permitted only where the existing UI already pages by number (e.g. discovered-pages tables) and only with a hard cap of 1000 rows.

### 4.6 Auth

- Same Flask-Login session cookie as the HTML routes. No new auth scheme in #27.
- `current_user` checked at the top of every endpoint via existing decorators (`@login_required`, `@project_role_required`, etc.).
- 401 if no session, 403 if wrong role/scope, 404 if the resource exists but the caller can't see it (avoids leaking existence).

### 4.7 CSRF

The `api` blueprint is already CSRF-exempt (`auto_a11y/web/app.py` registers it under `csrf.exempt(...)`). New endpoints inherit this. **Same-origin browser callers must send `X-Requested-With: XMLHttpRequest` or use `fetch` with `credentials: 'same-origin'`** — when the frontend migrates, it will rely on this. Cross-origin calls remain blocked by CORS.

### 4.8 Serialization

A single `auto_a11y/web/api/serializers.py` module per resource, returning `dict[str, JsonValue]`. No model objects leak into JSON. Datetimes are ISO 8601 UTC. ObjectIds are stringified.

### 4.9 Idempotency

`PUT` and `DELETE` are naturally idempotent. For `POST /resource/<id>/<action>` that triggers async work (test runs, discovery, report generation), accept an optional `Idempotency-Key` header; if a job with the same key is already in flight, return its existing job id with 202 instead of starting a new one.

**Storage:** a new MongoDB collection `idempotency_keys` with a TTL index (24h retention). In-memory storage was rejected because it produces duplicate jobs as soon as the app runs with more than one worker.

## 5. Route inventory and target REST mapping

Symbols:
- ✅ already in `/api/v1`
- 🟡 partial — exists but inside a non-API blueprint (e.g. `/projects/api/list`) or wrong shape
- ❌ not exposed as JSON anywhere
- 🚫 intentionally not migrated (HTML-only or out of scope)

### 5.1 Projects (`projects.py` + `api.py`)

| Current | Target | Status |
|---|---|---|
| `GET /projects/api/list` | `GET /api/v1/projects` | ✅ (canonical exists at `/api/v1/projects`) — drop the duplicate |
| `GET /projects/api/<id>/details` | `GET /api/v1/projects/<id>` | ✅ canonical exists — drop the duplicate |
| `GET /projects/api/<id>/websites` | `GET /api/v1/projects/<id>/websites` | ✅ canonical exists — drop the duplicate |
| `GET /projects/api/<id>/discovered-pages` | `GET /api/v1/projects/<id>/discovered-pages` | ❌ |
| `GET /projects/api/<id>/users` | `GET /api/v1/projects/<id>/members` | 🟡 (folds with members.py) |
| `GET /projects/api/test-details/<test_id>` | `GET /api/v1/test-results/<id>` | ✅ canonical exists |
| `POST /projects/api/test-details/<test_id>/production-ready` | `PATCH /api/v1/test-results/<id>` (set `production_ready`) | ❌ |
| `GET /projects/api/issue-documentation-stats` | `GET /api/v1/issues/documentation-stats` | ❌ |
| `GET /projects/` | 🚫 HTML index | — |
| `POST /projects/create` | `POST /api/v1/projects` | ✅ |
| `GET /projects/<id>` | 🚫 HTML detail | — |
| `POST /projects/<id>/edit` | `PUT /api/v1/projects/<id>` | ✅ |
| `POST /projects/<id>/delete` | `DELETE /api/v1/projects/<id>` | ✅ |
| `POST /projects/<id>/add-website` | `POST /api/v1/projects/<id>/websites` | ✅ |
| `POST /projects/<id>/test-all` | `POST /api/v1/projects/<id>/test-runs` | 🟡 (`/api/v1/projects/<id>/reports` exists for reports; need a separate test-runs endpoint) |
| `GET/POST /projects/<id>/report` | `POST /api/v1/projects/<id>/reports` | ✅ |

### 5.2 Websites (`websites.py` + `api.py`)

| Current | Target | Status |
|---|---|---|
| `GET /websites/api/list` | `GET /api/v1/websites` (filter by `?project_id=`) | 🟡 (only project-scoped list exists) |
| `GET /websites/<id>` | 🚫 HTML detail | — |
| `POST /websites/<id>/edit` | `PUT /api/v1/websites/<id>`, `PATCH /api/v1/websites/<id>` | ✅ |
| `POST /websites/<id>/delete` | `DELETE /api/v1/websites/<id>` | ✅ |
| `POST /websites/<id>/clear-test-results` | `DELETE /api/v1/websites/<id>/test-results` | ❌ |
| `POST /websites/<id>/discover` | `POST /api/v1/websites/<id>/discoveries` | ✅ |
| `GET /websites/<id>/discovery-status` | `GET /api/v1/websites/<id>/discoveries/latest` | ❌ |
| `POST /websites/<id>/cancel-discovery` | `POST /api/v1/websites/<id>/discoveries/latest/cancel` | ❌ |
| `POST /websites/<id>/add-page` | `POST /api/v1/websites/<id>/pages` | ✅ |
| `POST /websites/<id>/test-all` | `POST /api/v1/websites/<id>/test-runs` | 🟡 (`POST /api/v1/websites/<id>/test` exists, action-style; rename to `/test-runs` and return job id) |
| `POST /websites/<id>/cancel-testing` | `POST /api/v1/websites/<id>/test-runs/latest/cancel` | ❌ |
| `GET /websites/<id>/documents` | `GET /api/v1/websites/<id>/documents` | ❌ |
| `GET /websites/<id>/test-status` | `GET /api/v1/websites/<id>/test-runs/latest` | ❌ |
| `GET /websites/<id>/discovery-history` | `GET /api/v1/websites/<id>/discoveries` | ❌ |
| `GET /websites/<id>/discovery/<run_id>` | `GET /api/v1/discoveries/<run_id>` | ❌ |

### 5.3 Pages (`pages.py` + `discovered_pages.py` + `api.py`)

| Current | Target | Status |
|---|---|---|
| `GET /pages/<id>` | `GET /api/v1/pages/<id>` | ✅ |
| `POST /pages/<id>/edit` | `PUT /api/v1/pages/<id>`, `PATCH /api/v1/pages/<id>` | ✅ |
| `POST /pages/<id>/test` | `POST /api/v1/pages/<id>/test-runs` | 🟡 (`POST /api/v1/pages/<id>/test` exists; rename) |
| `GET /pages/<id>/test-status` | `GET /api/v1/pages/<id>/test-runs/latest` | 🟡 (`GET /api/v1/pages/<id>/test-results` exists but lists results, not run status) |
| `POST /pages/<id>/cancel-test` | `POST /api/v1/pages/<id>/test-runs/latest/cancel` | ❌ |
| `POST /pages/<id>/delete` | `DELETE /api/v1/pages/<id>` | ✅ |
| `GET /pages/<id>/violations` | `GET /api/v1/pages/<id>/violations` | ❌ |
| `GET/POST /pages/<id>/matrix` | `GET/PUT /api/v1/pages/<id>/matrix` | ❌ |
| `GET /discovered-pages/<id>` | `GET /api/v1/discovered-pages/<id>` | ❌ |
| `POST /discovered-pages/<id>/edit` | `PATCH /api/v1/discovered-pages/<id>` | ❌ |
| `POST /discovered-pages/<id>/delete` | `DELETE /api/v1/discovered-pages/<id>` | ❌ |

### 5.4 Test runs and results (`testing.py` + `api.py`)

| Current | Target | Status |
|---|---|---|
| `GET /testing/result/<id>` | 🚫 HTML detail | — |
| `GET /testing/dashboard` | 🚫 HTML | — |
| `POST /testing/run-test` | `POST /api/v1/test-runs` (single-page) | ❌ |
| `POST /testing/batch-test` | `POST /api/v1/test-runs/batch` | ❌ |
| `GET /testing/job/<id>/status` | `GET /api/v1/jobs/<id>` | ❌ (collection-level `/jobs/active` exists) |
| `POST /testing/job/<id>/cancel` | `POST /api/v1/jobs/<id>/cancel` | ❌ |
| `GET /testing/fixture-status` | 🚫 HTML | — |
| `GET/POST /testing/configure` | `GET/PUT /api/v1/testing/config` | ❌ |
| `GET /testing/api/stats` | `GET /api/v1/testing/stats` | 🟡 (move) |
| `GET /testing/api/active-tests` | `GET /api/v1/test-runs?status=active` | 🟡 (move + filter convention) |
| `POST /testing/api/run-tests` | `POST /api/v1/test-runs` | 🟡 (move) |
| `GET /testing/api/trends` | `GET /api/v1/test-runs/trends` | 🟡 (move) |
| `GET /testing/api/trends/detailed` | `GET /api/v1/test-runs/trends?detail=full` | 🟡 (move + collapse) |
| `GET /testing/api/trends/compare` | `GET /api/v1/test-runs/trends/compare` | 🟡 (move) |
| `GET /testing/api/trends/progress` | `GET /api/v1/test-runs/trends/progress` | 🟡 (move) |
| `GET /testing/trends` | 🚫 HTML | — |
| `GET /testing/api/websites/<project_id>` | `GET /api/v1/projects/<id>/websites` | ✅ duplicate — delete |
| `GET /api/v1/test-results/<id>` | (target) | ✅ |
| `GET /api/v1/pages/<id>/test-results` | (target) | ✅ |
| `GET /api/v1/test-results/<id>/states` | (target) | ✅ |
| `GET /api/v1/pages/<id>/test-states` | (target) | ✅ |
| `GET /api/v1/pages/<id>/test-sessions` | (target) | ✅ |
| `POST /api/v1/test-results/compare` | `GET /api/v1/test-results/compare?a=<id>&b=<id>` | 🟡 (verb-fix only) |

### 5.5 Reports (`reports.py`)

| Current | Target |
|---|---|
| `GET /reports/dashboard` | 🚫 HTML |
| `POST /reports/generate` | `POST /api/v1/reports` (body specifies what to generate) |
| `GET /reports/job/<id>/status` | `GET /api/v1/jobs/<id>` |
| `POST /reports/job/<id>/drop` | `DELETE /api/v1/jobs/<id>` |
| `POST /reports/job/<id>/restart` | `POST /api/v1/jobs/<id>/restart` |
| `GET /reports/download/<filename>` | `GET /api/v1/reports/<id>/file` — opaque id; `Content-Disposition: attachment; filename="..."` carries the human filename |
| `POST /reports/<filename>/delete` | `DELETE /api/v1/reports/<id>` |
| `GET /reports/project/<id>/summary` | `GET /api/v1/projects/<id>/report-summary` |
| `POST /reports/export-csv` | `POST /api/v1/reports` (with `format=csv`) |
| `POST /reports/generate/page/<id>` | `POST /api/v1/pages/<id>/reports` |
| `POST /reports/generate/website/<id>` | `POST /api/v1/websites/<id>/reports` |
| `POST /reports/generate/project/<id>` | `POST /api/v1/projects/<id>/reports` ✅ |
| `POST /reports/generate/page-structure/<website_id>` | `POST /api/v1/websites/<id>/reports` (body: `type=page-structure`) |
| `POST /reports/generate/page-structure` | `POST /api/v1/reports` (body: `type=page-structure`) |
| `POST /reports/generate/discovery/website/<id>` | `POST /api/v1/websites/<id>/reports` (body: `type=discovery`) |
| `POST /reports/generate/discovery/project/<id>` | `POST /api/v1/projects/<id>/reports` (body: `type=discovery`) |
| `POST /reports/generate/static-html` | `POST /api/v1/reports` (body: `type=static-html`) |
| `POST /reports/generate/deduplicated` | `POST /api/v1/reports` (body: `type=deduplicated`) |
| `POST /reports/generate/recordings/<project_id>` | `POST /api/v1/projects/<id>/reports` (body: `type=recordings`) |

The report endpoints sprawl because each format/scope has its own URL. The roadmap collapses them into `POST /api/v1/{scope}/reports` with a `type` discriminator in the body, plus `GET /api/v1/reports/<id>/file` for downloads.

A report record stores `(id, filename, project_id, created_at, type, format)` so the opaque id maps back to a human-friendly download filename via `Content-Disposition`. Existing filename-based URLs do not leak into the new API surface.

### 5.6 Recordings (`recordings.py`)

| Status |
|---|
| Read/update/delete shipped on recordings + recording-issues with cursor pagination, RFC 7807 errors, project-role auth. The legacy ``recordings_bp`` HTML routes (`/recordings/`, `/recordings/<id>`, `/recordings/upload`) still serve the admin frontend. POST `/api/v1/recordings` (multipart upload + DictaphoneImporter parsing + bulk RecordingIssue creation) is **deferred to a follow-up** alongside other multipart-bodied endpoints. |


| Current | Target |
|---|---|
| `GET /recordings/` | 🚫 HTML |
| `GET /recordings/<id>` | 🚫 HTML |
| `GET /recordings/combined/<project_id>` | 🚫 HTML |
| `GET/POST /recordings/upload` | `POST /api/v1/recordings` (multipart) |
| `POST /recordings/<id>/edit` | `PATCH /api/v1/recordings/<id>` |
| `POST /recordings/<id>/delete` | `DELETE /api/v1/recordings/<id>` |
| `GET /recordings/api/list` | `GET /api/v1/recordings` 🟡 (move) |
| `GET /recordings/api/<id>/issues` | `GET /api/v1/recordings/<id>/issues` 🟡 (move) |
| `POST /recordings/api/issue/<id>/status` | `PATCH /api/v1/recording-issues/<id>` 🟡 (move) |

### 5.7 Schedules (`schedules.py`)

| Current | Target |
|---|---|
| `GET /schedules` | 🚫 HTML index |
| `GET /websites/<id>/schedules` | `GET /api/v1/websites/<id>/scheduled-tests` |
| `GET/POST /websites/<id>/schedules/create` | `POST /api/v1/websites/<id>/scheduled-tests` |
| `GET /websites/<id>/schedules/<sid>` | `GET /api/v1/scheduled-tests/<id>` |
| `GET/POST /websites/<id>/schedules/<sid>/edit` | `PUT /api/v1/scheduled-tests/<id>` |
| `POST /websites/<id>/schedules/<sid>/delete` | `DELETE /api/v1/scheduled-tests/<id>` |
| `POST /websites/<id>/schedules/<sid>/toggle` | `PATCH /api/v1/scheduled-tests/<id>` (body: `{enabled}`) |
| `POST /websites/<id>/schedules/<sid>/run-now` | `POST /api/v1/scheduled-tests/<id>/runs` |
| `GET /websites/<id>/schedules/<sid>/preview` | `GET /api/v1/scheduled-tests/<id>/preview` |

### 5.8 Scripts (`scripts.py`)

| Current | Target | Status |
|---|---|---|
| `GET /scripts/page/<id>/scripts` | `GET /api/v1/pages/<id>/scripts` | ✅ |
| `GET/POST /scripts/page/<id>/scripts/create` | `POST /api/v1/pages/<id>/scripts` | ✅ |
| `GET/POST /scripts/website/<id>/scripts/create` | `POST /api/v1/websites/<id>/scripts` | ✅ |
| `GET /scripts/website/<id>/scripts` | `GET /api/v1/websites/<id>/scripts` | ✅ |
| `GET /scripts/<id>` | `GET /api/v1/scripts/<id>` | ✅ |
| `GET/POST /scripts/<id>/edit` | `PUT /api/v1/scripts/<id>` | ✅ |
| `POST /scripts/<id>/delete` | `DELETE /api/v1/scripts/<id>` | ✅ |
| `POST /scripts/<id>/toggle` | `PATCH /api/v1/scripts/<id>` (e.g. `{"enabled": false}`) | ✅ |
| `POST /scripts/<id>/test` | `POST /api/v1/scripts/<id>/test-runs` | ❌ (deferred to test-runs PR) |

### 5.9 PDFs (`pdf.py`)

| Current | Target |
|---|---|
| `GET /projects/<id>/pdfs` | `GET /api/v1/projects/<id>/pdfs` |
| `GET /websites/<id>/pdfs` | `GET /api/v1/websites/<id>/pdfs` |
| `GET /projects/<id>/pdfs/add` | 🚫 HTML form |
| `POST /projects/<id>/pdfs` | `POST /api/v1/projects/<id>/pdfs` (multipart) |
| `GET /pdfs/<id>` | `GET /api/v1/pdf-documents/<id>` |
| `POST /pdfs/<id>/audit` | `POST /api/v1/pdf-documents/<id>/audits` |
| `GET /pdfs/<id>/audit-status` | `GET /api/v1/pdf-documents/<id>/audits/latest` |
| `POST /pdfs/<id>/cancel` | `POST /api/v1/pdf-documents/<id>/audits/latest/cancel` |
| `GET /pdfs/<id>/file` | `GET /api/v1/pdf-documents/<id>/file` |
| `GET /pdfs/<id>/images/<name>` | `GET /api/v1/pdf-documents/<id>/images/<name>` |
| `GET /pdfs/<id>/export.<fmt>` | `GET /api/v1/pdf-documents/<id>/export?format=<fmt>` |
| `GET /pdfs/<id>/issue-map` | `GET /api/v1/pdf-documents/<id>/issue-map` |
| `GET /pdfs/<id>/pdfmax-report` | `GET /api/v1/pdf-documents/<id>/reports/pdfmax` |
| `POST /pdfs/<id>/delete` | `DELETE /api/v1/pdf-documents/<id>` |

### 5.10 Share tokens (`share_tokens.py`)

| Current | Target | Status |
|---|---|---|
| `POST /share-tokens/projects/<id>/share-tokens` | `POST /api/v1/projects/<id>/share-tokens` | ✅ |
| `POST /share-tokens/websites/<id>/share-tokens` | `POST /api/v1/websites/<id>/share-tokens` | ✅ |
| `GET /share-tokens/projects/<id>/share-tokens` | `GET /api/v1/projects/<id>/share-tokens` | ✅ |
| `GET /share-tokens/websites/<id>/share-tokens` | `GET /api/v1/websites/<id>/share-tokens` | ✅ |
| `POST /share-tokens/share-tokens/<id>/revoke` | `DELETE /api/v1/share-tokens/<id>` | ✅ |
| (new) | `GET /api/v1/share-tokens/<id>` (metadata-only single-resource read) | ✅ |

### 5.11 Members, groups, project users, project participants, website users

These are five overlapping flavors of "user-membership-of-thing". The audit recommends collapsing them under three top-level resources in #27:

- `/api/v1/users` — system users (admin)
- `/api/v1/projects/<id>/members` (covers `members.py`, `project_users.py`, `project_participants.py`)
- `/api/v1/websites/<id>/members` (covers `website_users.py`)
- `/api/v1/groups` (covers `groups.py`)

Each follows standard CRUD. The HTML routes stay; the JSON routes consolidate.

### 5.12 Drupal sync (`drupal_sync.py`)

10 routes, mostly HTML pages with embedded forms. **Out of scope for #27** — Drupal integration is its own concern with its own follow-up issue if/when needed. Mentioned here only to acknowledge they were not forgotten.

### 5.13 Auth (`auth.py`)

Login, logout, register, password reset, profile, user CRUD, Microsoft/Google SSO callbacks. **Out of scope for #27.** These are session-establishing flows that don't fit a JSON-API model cleanly. A future session-token API would replace them.

### 5.14 Admin settings (`admin_settings.py`)

| Current | Target | Status |
|---|---|---|
| `GET /admin/settings` | `GET /api/v1/admin/settings` | ✅ |
| `POST /admin/settings/drupal` | `PATCH /api/v1/admin/settings/drupal` | ✅ |
| `POST /admin/settings/section/<id>` | `PATCH /api/v1/admin/settings/<section>` | ✅ |
| (new) | `DELETE /api/v1/admin/settings/drupal` (revert to env-var fallback) | ✅ |
| (new) | `DELETE /api/v1/admin/settings/<section>` (revert to env-var fallback) | ✅ |

### 5.15 Health and jobs (`api.py` already)

- ✅ `GET /api/v1/health`
- ✅ `GET /api/v1/health/pdf`
- ✅ `GET /api/v1/jobs/active`
- ✅ `GET /api/v1/jobs/stats`
- ✅ `POST /api/v1/jobs/clear-all`
- ✅ `POST /api/v1/jobs/clear-stale`
- ✅ `POST /api/v1/jobs/cleanup-page-counts`
- ❌ `GET /api/v1/jobs/<id>` (per-job status — needed by reports/test-runs alignment)
- ❌ `POST /api/v1/jobs/<id>/cancel`
- ❌ `POST /api/v1/jobs/<id>/restart`

### 5.16 Fixture tests (`api.py` already)

- ✅ `GET /api/v1/fixture-tests/status`
- ✅ `GET /api/v1/fixture-tests/check/<error_code>`

## 6. Phasing for #27 (and future PRs)

#27 is still a large PR but bounded. To stay reviewable, it lands in **three commits on one branch** (or three stacked PRs if reviewers prefer):

**Commit A — conventions and shared scaffolding (~400 LOC):**
- New `auto_a11y/web/api/` package with `serializers.py`, `errors.py` (RFC 7807), `pagination.py`, and `responses.py`.
- Decorator `@api_endpoint` that wraps handlers with consistent error → Problem-Details translation.
- Rewire the existing `success`-wrapped endpoints to also accept the new `Accept: application/problem+json` content negotiation. **No behavior change for existing callers.**

**Commit B — fill in the gaps (the bulk of the work):**
- Add every ❌-marked endpoint above for resources in scope: projects, websites, pages, discovered-pages, test-runs/results, jobs, scripts, schedules, recordings, share-tokens, members/groups, pdfs, reports, admin-settings.
- Move the 🟡 routes living in HTML blueprints (`/projects/api/...`, `/testing/api/...`, `/recordings/api/...`, `/websites/api/...`) into `/api/v1` as canonical, leaving redirect shims in the old locations for one release.
- Drop ✅-duplicate routes only after frontend migration (handled in a later PR per #21).

**Commit C — tests (see §7).**

Out of scope for #27 (deferred to follow-ups under #21):
- Drupal sync API
- Auth API (login/SSO)
- Frontend migration to consume `/api/v1`
- Removing the redirect shims

## 7. Testing strategy (mandatory per #27 acceptance)

### 7.1 Framework

`pytest` + Flask's built-in test client (`app.test_client()`). Already in use across `tests/` (e.g. `test_csrf_tokens.py`, `test_fluent.py`). No new dependencies.

### 7.2 Database

A real `mongod` is already required for the existing `tests/check_*.py` scripts. The API tests adopt the same approach with one tightening:

- A pytest fixture creates a uniquely-named test DB per session: `mongodb://localhost:27017/auto_a11y_test_<run_id>`.
- Per-test cleanup truncates collections; per-session cleanup drops the DB.
- `MONGODB_URI` for tests is overridden via `monkeypatch.setenv` in the session fixture.
- CI must have `mongod` available — `.github/workflows/ci.yml` already starts it for the existing pytest job, so no CI change needed.

We do **not** mock the DB. The CLAUDE.md guidance against mocking is strong precedent (mocks pass while prod migrations break).

### 7.3 Layout

```
tests/api/
├── __init__.py
├── conftest.py            # app, client, db, auth fixtures
├── test_projects.py
├── test_websites.py
├── test_pages.py
├── test_discovered_pages.py
├── test_test_runs.py
├── test_test_results.py
├── test_jobs.py
├── test_scripts.py
├── test_schedules.py
├── test_recordings.py
├── test_share_tokens.py
├── test_members.py
├── test_groups.py
├── test_pdfs.py
├── test_reports.py
├── test_admin_settings.py
├── test_health.py
├── test_errors.py         # RFC 7807 envelope correctness
└── test_pagination.py     # cursor format, limit clamps, empty pages
```

### 7.4 Coverage target per resource

For every resource that supports full CRUD, `tests/api/test_<resource>.py` MUST contain:

1. **Happy path round-trip**: `POST → GET (one) → GET (list, finds it) → PUT/PATCH → GET (sees update) → DELETE → GET (404)`.
2. **Auth matrix**: 401 unauthenticated; 403 wrong role; 404 cross-tenant (caller in project A asks for resource in project B).
3. **Validation**: 400 with RFC 7807 body for at least one malformed input per write verb.
4. **Pagination** (where applicable): `limit=1` returns one item plus a `next_cursor`; following the cursor returns the next page; final page has `next_cursor: null`.

For action endpoints (`POST /resource/<id>/action`):

1. Triggers the expected side effect (job created, status changes).
2. Idempotency-Key honored — repeated POST with same key returns same job id.
3. 409 if the resource is in a state that disallows the action.

### 7.5 Auth fixtures

`conftest.py` provides factory fixtures:

- `make_user(role: UserRole, projects: list[Project] = ...)` — creates a `User`, returns `(user, client)` where `client` already has a valid Flask-Login session.
- `as_admin`, `as_auditor`, `as_project_member`, `as_anon` — preset variants for the common matrix.

### 7.6 What NOT to test in this suite

- JavaScript test scripts (covered by fixture tests, `test_fixtures.py`).
- WCAG correctness (covered by fixture tests).
- HTML template rendering (covered by `test_csrf_tokens.py` and `test_report_template_rendering.py`).
- Browser automation / Playwright (covered by `test_dialog_accessibility.py`).

The API suite is purely `request → JSON response` shape, status code, and side-effect assertions.

### 7.7 CI

Add a step in `.github/workflows/ci.yml` after the existing `pytest` invocation (or relax the existing one's path) so `tests/api/` runs as part of the same matrix (`python 3.11` and `3.12`). Required status check on `main`.

### 7.8 Type checking

`tests/api/` is in scope per CLAUDE.md's mypy/pyright/ty configuration. New test files MUST pass strict type checking with no escape hatches.

## 8. Resolved decisions

These were open questions during drafting; they are now locked in for #27.

1. **Idempotency-Key storage:** MongoDB collection `idempotency_keys` with a 24h TTL index. In-memory was rejected because it produces duplicate jobs under multi-worker deployments. (See §4.9.)
2. **Report download URLs:** opaque report ids, with `Content-Disposition: attachment; filename="..."` carrying the human filename. Filenames in URLs were rejected to avoid leaking project info and to make URLs unguessable. (See §5.5.)
3. **`success`-envelope deprecation:** keep the wrapped responses through #27, mark them with RFC 8594 `Deprecation` / `Sunset` HTTP headers, and remove them in the frontend-migration PR once nothing on our side calls them. Pulling the envelope inside #27 was rejected as too risky to couple with the surface expansion. (See §4.4.)

## 9. Definition of done for this doc (#26)

- [x] Doc committed under `docs/`
- [x] Open questions resolved
- [ ] PR opened and reviewed by repo owner
