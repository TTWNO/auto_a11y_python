# Bug Audit Remediation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the ~60 bugs documented in [`BUG_AUDIT.md`](../../../BUG_AUDIT.md), ordered by real-world risk, with a regression test for each behavioural fix.

**Architecture:** Work proceeds in 10 phases that mirror the audit sections, sequenced by the audit's triage order. Phase 1 (web authorization / IDOR) is the highest priority and lands first; it begins by extending the existing central authorization resolver (`auto_a11y/core/permissions._resolve_project_id` + `auto_a11y/web/routes/auth.get_effective_role`) so the established `@project_role_required(*roles)` decorator can be applied uniformly across the legacy blueprints. Later phases are independent of each other and may be parallelised across worktrees if desired.

**Tech Stack:** Python 3.11/3.12, Flask, Flask-Login, MongoDB (pymongo), Playwright, Anthropic SDK, WeasyPrint/Typst, pytest. Type-checked under mypy/pyright/ty strict (see CLAUDE.md). Bilingual Fluent i18n for any user-visible string.

---

## Conventions for every task in this plan

- **Virtualenv is `.venv/`** (not `venv/`). Run tools as `.venv/bin/python -m pytest …`, `.venv/bin/python -m mypy`, etc.
- **TDD is mandatory** (@superpowers:test-driven-development): write the failing test, watch it fail, implement the minimal fix, watch it pass, then commit.
- **Type checking is mandatory and has zero escape hatches** (CLAUDE.md). After each implementation step run `.venv/bin/python -m mypy && .venv/bin/python -m pyright && .venv/bin/python -m ty check` on the touched files before committing. No `# type: ignore`, no `cast(Any, …)`, no `-> Any`.
- **Never rewrite git history** (CLAUDE.md). One new commit per task. Conventional-commit style messages.
- **MongoDB tests** follow the existing skip-if-unavailable pattern (`tests/api/test_auth.py` `mongo_check` fixture). **Run pytest single-process** for any suite that spins up `mongod`-backed fixtures — `mongod` core-dumps under `pytest -n auto` in this environment (known infra issue, not a code bug). Use `.venv/bin/python -m pytest <path> -p no:xdist` or omit `-n`.
- **Decorator-only behaviour** (does a 403/redirect fire?) can be tested with a lightweight Flask app + a `MagicMock` db and a stubbed `current_user`, avoiding Mongo entirely. Prefer this for the Phase 1 route-guard tests; reserve real-Mongo tests for data-layer fixes.
- **Bilingual strings**: any new flash/JSON message must use `ftl('…')` with entries added to both `auto_a11y/web/translations/en/*.ftl` and `fr/*.ftl`. The existing auth denial messages (`common-insufficient-permissions`, `common-authentication-required`, `common-you-do-not-have-permission-to-access-this-resource`) already exist — reuse them; no new strings are needed for Phase 1.
- **No Bootstrap colour classes** in any template touched (CLAUDE.md colour system).

### Reference: how authorization works today

- `auto_a11y/web/routes/auth.py:120` `project_role_required(*roles)` — decorator. Reads `project_id` / `website_id` / `page_id` from the **route kwargs**, calls `get_effective_role(...)`, and `abort(403)` (or 403 JSON) if the resolved role isn't in `roles`. Superadmin always passes. Sets `g.effective_role`.
- `auto_a11y/web/routes/auth.py:84` `get_effective_role(user, request_obj, project_id, website_id, page_id)` — resolves page→website→project, then maps group permissions to `UserRole.ADMIN/AUDITOR/CLIENT` or `None`.
- `auto_a11y/core/permissions.py:127` `_resolve_project_id(**kwargs)` — resolves `project_id` / `website_id` / `page_id` / `recording_id` to a project id. Used by `project_admin_required`.
- `app.py:415` `before_request` enforces **authentication** for all non-exempt blueprints, but **not authorization**.

**Role guidance used throughout Phase 1:**
- Read-only views/APIs → `@project_role_required(UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT)`
- Run tests / generate reports / discover / edit data / push to Drupal → `@project_role_required(UserRole.ADMIN, UserRole.AUDITOR)`
- Delete / destructive / membership / credentials → `@project_role_required(UserRole.ADMIN)`

---

## Phase 1 — Web authorization / IDOR (HIGHEST PRIORITY)

The §1 cluster. Goal: every legacy blueprint route resolves its resource to a project and enforces a role, closing the IDOR holes. We first make the central resolver able to map **every** ID kind these blueprints use, then apply the decorator route-by-route, blueprint-by-blueprint, with a guard test per blueprint.

### Task 1.1: Extend the central project-id resolver to all ID kinds

**Files:**
- Modify: `auto_a11y/core/permissions.py:127-153` (`_resolve_project_id`)
- Test: `tests/test_permissions.py` (existing) — add cases

The current resolver handles `project_id`, `website_id`, `page_id`, `recording_id`. The legacy blueprints additionally key on `user_id` (ProjectUser), `script_id` (PageSetupScript), `result_id` (TestResult), and a recording `issue_id`. Add resolution for these so one decorator works everywhere. **Do not** add `discovered-page ObjectId` here — that param is *also* named `page_id` and would collide with the regular-page branch; it gets a dedicated decorator in Task 1.9.

**Accessor names — confirmed to exist in `auto_a11y/core/database.py`:** `get_test_result` (`:950`), `get_project_user` (`:2454`), `get_recording_issue` (`:2605`), `get_page`, `get_website`. **Naming traps — use these exact names** (the obvious guess is wrong): the script accessor is **`get_page_setup_script(script_id)`** (`:1975`), *not* `get_page_script`; the discovered-page accessor (Task 1.9) is **`get_discovered_page_by_id(page_id)`** (`:2708`), *not* `get_discovered_page`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_permissions.py — add to the existing module
def test_resolve_project_id_from_user_id(monkeypatch):
    db = _FakeDb(project_users={"u1": _Obj(project_id="p1")})
    monkeypatch.setattr("auto_a11y.core.permissions._get_db", lambda: db)
    from auto_a11y.core.permissions import _resolve_project_id
    assert _resolve_project_id(user_id="u1") == "p1"

def test_resolve_project_id_from_script_id(monkeypatch):
    db = _FakeDb(scripts={"s1": _Obj(page_id="pg1")},
                 pages={"pg1": _Obj(website_id="w1")},
                 websites={"w1": _Obj(project_id="p1")})
    monkeypatch.setattr("auto_a11y.core.permissions._get_db", lambda: db)
    from auto_a11y.core.permissions import _resolve_project_id
    assert _resolve_project_id(script_id="s1") == "p1"

def test_resolve_project_id_from_result_id(monkeypatch):
    db = _FakeDb(results={"r1": _Obj(page_id="pg1")},
                 pages={"pg1": _Obj(website_id="w1")},
                 websites={"w1": _Obj(project_id="p1")})
    monkeypatch.setattr("auto_a11y.core.permissions._get_db", lambda: db)
    from auto_a11y.core.permissions import _resolve_project_id
    assert _resolve_project_id(result_id="r1") == "p1"
```

`_FakeDb`/`_Obj` do **not** exist in `tests/test_permissions.py` yet — write them from scratch (the existing tests use `MagicMock` + `patch`, so there is nothing to "mirror"). `_FakeDb` exposes `get_project_user`, `get_page_setup_script`, `get_test_result`, `get_page`, `get_website` returning the seeded objects or `None`; `_Obj` is a trivial attribute holder (e.g. `types.SimpleNamespace`). **Monkeypatch target is `auto_a11y.core.permissions._get_db`** (the resolver's db source) — note this differs from the existing tests, which patch `auth._get_db`.

- [ ] **Step 2: Run the tests; verify they fail**

Run: `.venv/bin/python -m pytest tests/test_permissions.py -k "resolve_project_id_from" -v`
Expected: FAIL (resolver returns `None`).

- [ ] **Step 3: Implement the resolution branches**

Add to `_resolve_project_id`, after the `recording_id` branch and before `return None`. Use the actual DB accessor names — verify them with `grep -n "def get_project_user\|def get_page_script\|def get_test_result" auto_a11y/core/database.py`:

```python
    user_id: str | None = kwargs.get('user_id')
    if user_id:
        pu = db.get_project_user(user_id)
        return pu.project_id if pu else None

    script_id: str | None = kwargs.get('script_id')
    if script_id:
        script = db.get_page_setup_script(script_id)  # NB: not get_page_script
        if script and getattr(script, 'page_id', None):
            page = db.get_page(script.page_id)
            if page:
                website = db.get_website(page.website_id)
                return website.project_id if website else None
        if script and getattr(script, 'website_id', None):
            website = db.get_website(script.website_id)
            return website.project_id if website else None
        return None
    # NB: PageSetupScript is page/website/test-run scoped via a `scope` enum.
    # The branch above covers page- and website-scoped scripts. There is no
    # test-run→project chain, so a purely test-run-scoped script resolves to
    # None here. Before relying on that, confirm via `scripts.py` that no
    # <script_id> route can target a test-run-only script; if one can, add a
    # test_run_id→project branch (or fall back to website via the script's
    # website_id) so a legitimately-authorized admin is not 403'd.

    result_id: str | None = kwargs.get('result_id')
    if result_id:
        result = db.get_test_result(result_id)
        if result:
            page = db.get_page(result.page_id)
            if page:
                website = db.get_website(page.website_id)
                return website.project_id if website else None
        return None
```

If `PageSetupScript` can be page- or website-scoped (it can — see `scripts.py` routes), the dual `page_id`/`website_id` handling above is required.

- [ ] **Step 4: Mirror the new branches in `get_effective_role`**

`project_role_required` calls `get_effective_role` (auth.py:84), **not** `_resolve_project_id`. `get_effective_role` only takes `project_id/website_id/page_id`. Rather than duplicate logic, refactor `get_effective_role` to resolve the project id via `resolve_project_id(**{...})` when the direct args are absent. Concretely, in `auth.py`, change the decorator (`project_role_required`, lines 135-141) to also pass through the extra kwargs:

```python
            from auto_a11y.core.permissions import resolve_project_id
            project_id = resolve_project_id(**kwargs)  # handles every ID kind
            effective_role = get_effective_role(current_user, request, project_id=project_id)
```

This makes `project_role_required` rely on the single resolver. Confirm `get_effective_role` still works when only `project_id` is supplied (it does — lines 101-117). **Why this matters for Task 1.9:** routing through `resolve_project_id(**kwargs)` makes the `page_id` kwarg resolve as a *regular* page. `discovered_pages` reuses the `page_id` name for a *DiscoveredPage* ObjectId, so it cannot use this decorator and needs its own (Task 1.9). Leave a comment to that effect so a future maintainer doesn't "simplify" the dedicated decorator away.

- [ ] **Step 5: Run tests + type checks; verify pass**

Run: `.venv/bin/python -m pytest tests/test_permissions.py -v && .venv/bin/python -m mypy && .venv/bin/python -m pyright && .venv/bin/python -m ty check`
Expected: PASS / no type errors.

- [ ] **Step 6: Commit**

```bash
git add auto_a11y/core/permissions.py auto_a11y/web/routes/auth.py tests/test_permissions.py
git commit -m "fix(auth): resolve project id from user/script/result ids for authz"
```

---

### Task 1.2: Build the reusable route-guard test harness

**Files:**
- Create: `tests/web/test_route_authz.py`
- Create (if missing): `tests/web/_authz_helpers.py`

A single lightweight harness used by Tasks 1.3–1.11 to assert that a route aborts 403 for a non-member and passes for a member, without Mongo. It builds a tiny Flask app, registers the blueprint under test, installs Flask-Login with a stub user, and monkeypatches `auto_a11y.web.routes.auth.get_db` / `_get_db` to a `MagicMock` whose `user_has_permission` returns the desired role.

- [ ] **Step 1: Write the harness + one smoke test against an already-guarded route**

```python
# tests/web/_authz_helpers.py
from __future__ import annotations
from typing import Any
from unittest.mock import MagicMock
from flask import Flask
from flask_login import LoginManager

def make_app_with_blueprint(blueprint: Any, *, role: str | None) -> Flask:
    """role: 'admin'|'auditor'|'client'|None — what user_has_permission grants."""
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="t", WTF_CSRF_ENABLED=False)
    # ... install LoginManager with an always-authenticated non-superadmin stub user
    # ... monkeypatch db.user_has_permission to grant the requested role tier
    app.register_blueprint(blueprint)
    return app
```

Model the LoginManager + stub-user wiring on `tests/api/test_auth.py:flask_app`. The role tiering must mirror `get_effective_role`: ADMIN needs `project_members:delete`, AUDITOR needs `test_results:create`, CLIENT needs `projects:read`.

- [ ] **Step 2: Smoke test** — assert that the *already-guarded* `pdf` or `schedules` blueprint returns 403 for `role=None` on a known route. Run it; it should PASS (proving the harness models the real decorator correctly). If it doesn't, fix the harness before proceeding.

Run: `.venv/bin/python -m pytest tests/web/test_route_authz.py -v -p no:xdist`

- [ ] **Step 3: Commit**

```bash
git add tests/web/test_route_authz.py tests/web/_authz_helpers.py
git commit -m "test(web): add reusable route authorization harness"
```

---

### Tasks 1.3–1.11: Apply `@project_role_required` per blueprint

Each task follows the identical TDD shape:
1. **Write a failing guard test** in `tests/web/test_route_authz.py`: for a representative route in the blueprint, assert `role=None` → 403 (or 401 JSON for API routes), and `role='admin'` → not 403.
2. **Run it; confirm it fails** (route currently has no guard → returns 200/redirect, not 403).
3. **Add the decorator** to every route in the blueprint per the role guidance, placing it directly above the function (below `@<bp>.route(...)`), and ensure the route's URL param name is one the resolver understands (`project_id`/`website_id`/`page_id`/`recording_id`/`user_id`/`script_id`/`result_id`).
4. **Run guard test + full blueprint import; confirm pass + type-clean.**
5. **Commit.**

> When a route's URL param is none of the resolvable kinds (e.g. `filename`, `issue_id`, `job_id`), do **not** rely on the kwarg auto-resolution — resolve inside the handler and call `get_effective_role` explicitly, or add the needed branch to `_resolve_project_id` first (extend Task 1.1). These exceptions are called out per task.

- [ ] **Task 1.3 — `pages.py`** (`auto_a11y/web/routes/pages.py:131,281,313,368,429,447,453`)
  - `view_page`, `view_violations` → `(ADMIN, AUDITOR, CLIENT)`
  - `edit_page`, `test_page`, `cancel_test`, `configure_test_matrix` → `(ADMIN, AUDITOR)`
  - `delete_page` → `(ADMIN)`
  - All use `page_id` → resolver works directly.
  - Commit: `fix(pages): enforce per-project authorization (IDOR)`

- [ ] **Task 1.4 — `websites.py`** (every route: `view_website:47`, `edit_website:182`, `delete_website:218`, `clear_test_results:236`, `discover_pages:273`, `add_page:517`, `test_all_pages:545`, `manual_session_*:678-919`, `view_documents:922`, `view_discovery_run:1064`, **and** `api_list_websites:25`)
  - Read views → `(ADMIN, AUDITOR, CLIENT)`; edit/discover/test/manual-session → `(ADMIN, AUDITOR)`; `delete_website`, `clear_test_results` → `(ADMIN)`.
  - **Exception — `api_list_websites:25`** has no `website_id`; it lists all. Replace its body to filter to the user's accessible projects (mirror `projects.api_list_projects` — `grep -n "def api_list_projects" auto_a11y/web/routes/projects.py` and copy the membership-filter approach), or gate to superadmin. Add a test asserting a CLIENT sees only their websites.
  - Commit: `fix(websites): enforce per-project authorization + scope api/list (IDOR)`

- [ ] **Task 1.5 — `project_users.py`** (`list_users:23`, `create_user:43`, `view_user:104`, `edit_user:119`, `delete_user:182`, `test_login:202`, `toggle_user:269`, `clear_cache:290`)
  - All → `(ADMIN, AUDITOR)` except `delete_user`/`toggle_user`/`clear_cache` → `(ADMIN)`. These hold test-site credentials, so default to the stricter tier.
  - `/projects/<project_id>/users*` resolve via `project_id`; `/projects/users/<user_id>*` resolve via `user_id` (added in Task 1.1).
  - Commit: `fix(project_users): enforce authorization on test-credential routes (IDOR)`

- [ ] **Task 1.6 — `recordings.py`** (`view_recording:73`, `view_combined_recordings:161`, `process_recording:756`, `download_callouts_video:807`, `cancel_recording:835`, `delete_recording:859`, `api_recording_issues:920`, `api_update_issue_status:955`; plus `api_list:888`)
  - Read/download → `(ADMIN, AUDITOR, CLIENT)`; process/cancel/update-status → `(ADMIN, AUDITOR)`; `delete_recording` → `(ADMIN)`.
  - `<recording_id>` resolves via `recording_id`; `combined/<project_id>` via `project_id`.
  - **Exception — `api_update_issue_status:955`** keys on `issue_id`. Add an `issue_id`→project branch to `_resolve_project_id` (RecordingIssue → recording → project; verify accessor with `grep -n "def get_recording_issue" auto_a11y/core/database.py`) as a sub-step here, with its own resolver test.
  - **Exception — `api_list:888`** lists recordings: filter to accessible projects like Task 1.4.
  - Commit: `fix(recordings): enforce per-project authorization (IDOR)`

- [ ] **Task 1.7 — `automated_tests.py`** (`get_filter_options:33`, `project_automated_tests:93`, `filter_test_results:179`, `upload_to_drupal:255`)
  - Reads → `(ADMIN, AUDITOR, CLIENT)`; `upload_to_drupal` → `(ADMIN, AUDITOR)`.
  - Confirm URL param names (`grep -nE "_bp.route\(" auto_a11y/web/routes/automated_tests.py`) and resolve accordingly.
  - Commit: `fix(automated_tests): enforce authorization incl. drupal upload (IDOR)`

- [ ] **Task 1.8 — `scripts.py`** (`list_page_scripts:22`, `create_page_script:52`, `create_website_script:154`, `list_website_scripts:261`, `view_script:287`, `edit_script:307`, `delete_script:425`, `toggle_script:452`, `test_script:494`)
  - View/list → `(ADMIN, AUDITOR, CLIENT)`; create/edit/toggle/test → `(ADMIN, AUDITOR)`; `delete_script` → `(ADMIN)`.
  - `page/<page_id>/*` via `page_id`; `website/<website_id>/*` via `website_id`; `<script_id>*` via `script_id` (added in Task 1.1).
  - Commit: `fix(scripts): enforce per-project authorization (IDOR)`

- [ ] **Task 1.9 — `discovered_pages.py`** (`view_discovered_page:22`, `edit_discovered_page:78`, `delete_discovered_page:151`)
  - These use a `page_id` URL param that is a **DiscoveredPage ObjectId**, not a regular page — the generic resolver would wrongly call `db.get_page`. Create a dedicated decorator `discovered_page_role_required(*roles)` (in `auth.py`) that resolves via **`db.get_discovered_page_by_id(page_id)`** (NB: not `get_discovered_page`) → `project_id` and otherwise mirrors `project_role_required`. View → `(ADMIN, AUDITOR, CLIENT)`; edit → `(ADMIN, AUDITOR)`; delete → `(ADMIN)`.
  - Add a guard test for each of the three routes.
  - Commit: `fix(discovered_pages): enforce per-project authorization (IDOR)`

- [ ] **Task 1.10 — `reports.py`** (`generate_report:111`, `download_report:415`, `delete_report:435`, `generate_page_report:480`, `generate_website_report:522`, `generate_project_report:565`, and the `page-structure`/`discovery`/`static-html`/`deduplicated`/`recordings` generators)
  - Scope-param routes (`/generate/page/<page_id>`, `/generate/website/<website_id>`, `/generate/project/<project_id>`, `/generate/recordings/<project_id>`, etc.) → `(ADMIN, AUDITOR, CLIENT)` for generation (reading their own project's data).
  - **Exception — `download_report:415` / `delete_report:435`** key on `filename`, not a resource id. Authorize by the **report record**: resolve the report's scope (mirror the v1 API's `_authorize_report_record` — `grep -n "_authorize_report_record" auto_a11y/web/routes/api.py`) and check role; `delete` → `(ADMIN)`.
  - **Also fix the traversal-order bug here (audit §1):** in `download_report`/`delete_report`, perform the `is_relative_to(reports_dir)` containment check on the resolved path **before** the `.exists()` probe. Add a test that a `filename` resolving outside `REPORTS_DIR` aborts 403/404 without an existence check.
  - **Also fix the eager-`request.json` 500 (audit §1):** the `request.form.get('format', request.json.get(...) if request.is_json else 'html')` pattern appears at **seven** sites in `reports.py` — lines **483, 525, 568, 610, 723, 765, 987** (`grep -n "request.json.get" auto_a11y/web/routes/reports.py` to confirm). Fix **all seven**: replace each with a two-step parse using `data = request.get_json(silent=True) or {}` then `fmt = request.form.get('format') or data.get('format', 'html')`. Add a test posting a JSON content-type with an empty body → expect 200/normal handling, not 500.
  - Commit: `fix(reports): authorize generation/download/delete; fix traversal order and json parsing`

- [ ] **Task 1.11 — `projects.py` gaps + v1 API `test-results`** (`edit_project:600`, `delete_project:764`, `test_project:819`, `api_test_details:100`, `api_get_project_users:944`, `api_get_project:967`, `api_get_discovered_pages:992`; `api.py:get_test_result:801`, `get_test_result_states:1436`)
  - `edit_project`/`delete_project` → `(ADMIN)`; `test_project` → `(ADMIN, AUDITOR)`; the project API readers → `(ADMIN, AUDITOR, CLIENT)`. All use `project_id`.
  - `api.py:801,1436` use `result_id` → resolver handles it after Task 1.1. Add `@project_role_required(UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT)` to both, matching the sibling `get_page_test_results:842`.
  - `share_tokens.py:revoke_token:129` — add `@project_role_required(UserRole.ADMIN, UserRole.AUDITOR)` for consistency (audit §1 LOW).
  - `schedules.py:schedules_dashboard:29` — add a scope filter or `@project_role_required`; confirm what data the template renders first.
  - Add guard tests for `delete_project` (403 for CLIENT) and `api.py:get_test_result` (403 for non-member).
  - Commit: `fix(projects,api): close remaining authorization gaps (IDOR)`

---

### Task 1.12: Harden client-facing error responses (audit §1 LOW)

**Files:** `projects.py`, `websites.py`, `project_users.py`, `discovered_pages.py` (the `except Exception … str(e)` sites listed in audit §1)
**Test:** assert a forced internal error returns a generic message, not `str(e)`.

- [ ] Replace `return jsonify({'error': str(e)})` / `flash(str(e))` patterns with `logger.exception(...)` server-side + a generic `ftl('common-an-error-occurred')`-style message to the client. Add the FTL string to en/fr if it doesn't exist. Write one representative test (monkeypatch a manager method to raise) asserting the response body contains the generic message and not the raised text. Commit: `fix(web): stop leaking internal error detail to clients`.

---

## Phase 2 — Reporting

### Task 2.1: Fix `.upper()` on `ImpactLevel` enum in the all-issues Excel sheet (HIGH)
**Files:** Modify `auto_a11y/reporting/formatters.py:2331`; Test `tests/test_formatter_streaming.py` (or new `tests/test_formatters_excel.py`).
- [ ] Failing test: build a minimal page result containing one `AIFinding` (severity = `ImpactLevel.HIGH`), call the all-issues sheet builder, assert no exception and the severity cell == `"HIGH"`.
- [ ] Fix: mirror `_create_ai_findings_sheet:2125-2128` — `sev = getattr(f, 'severity', None); val = sev.value if hasattr(sev, 'value') else str(sev or 'MEDIUM'); cell = val.upper()`.
- [ ] Commit: `fix(reporting): handle ImpactLevel enum in all-issues excel sheet`

### Task 2.2: HTML-escape user/site data in `HTMLFormatter` (HIGH — stored XSS)
**Files:** Modify `auto_a11y/reporting/formatters.py` (HTMLFormatter: `1271,1367,632-643,1109` + `_streaming_issue_row`); Test new `tests/test_html_escaping.py`.
- [ ] Failing test: feed a page with title/description/xpath/issue text containing `<script>alert(1)</script>` and `"><img>`; render the HTML report; assert the raw `<script>` substring is **absent** and the escaped `&lt;script&gt;` is present.
- [ ] Fix: route every interpolated dynamic value through `html.escape(...)` (import `html`). Apply to titles, URLs (escape text; keep `href` attribute-escaped), descriptions, xpath, project/website names, element fragments. Be careful not to double-escape values that are already safe-static.
- [ ] Run the existing report rendering tests too (`tests/test_report_template_rendering.py`, `tests/test_streaming_reports.py`) to ensure no regression.
- [ ] Commit: `fix(reporting): html-escape dynamic data in HTMLFormatter (stored XSS)`

### Task 2.3: HTML-escape project/website fields in `project_report.py` (HIGH — stored XSS)
**Files:** Modify `auto_a11y/reporting/project_report.py:136,182,184` + website-card loop; Test `tests/test_html_escaping.py`.
- [ ] Failing test: project name `<script>…`, assert escaped in output. Fix with `html.escape`. Commit: `fix(reporting): html-escape project/website fields (stored XSS)`.

### Task 2.4: HTML-escape AI executive-summary text (MEDIUM)
**Files:** `auto_a11y/reporting/ai_executive_summary.py:472,480,487,519,528,538,573,601-602`; Test `tests/test_html_escaping.py`.
- [ ] Failing test with markup in `assessment.explanation` / list items; fix with `html.escape`; commit `fix(reporting): html-escape AI executive summary output`.

### Task 2.5: Fix average-score zero-page exclusion (MEDIUM)
**Files:** `auto_a11y/reporting/static_html_generator.py:1890-1891` (and `1629`); Test new `tests/test_static_html_scoring.py`.
- [ ] Failing test: three pages with scores `[0, 50, 100]` → assert average is `50.0`, not `75.0`. Decide intent: 0 = real score → include `if p['score'] is not None`; "not scored" → gate on `total_tests > 0`. Document the choice in a comment.
- [ ] Commit: `fix(reporting): stop excluding zero-score pages from site average`

### Task 2.6: Remove hardcoded `/Users/bob3/...` fallback path (MEDIUM)
**Files:** `auto_a11y/reporting/page_structure_report.py:945` (+ bare `except:` at `:940`).
- [ ] Replace fallback with repo-relative `Path('reports')` (match `report_generator.py`); change `except:` → `except Exception:` with a debug log. Test: with `REPORTS_DIR` unset and no app context, the resolved dir is repo-relative. Commit: `fix(reporting): repo-relative reports dir fallback; narrow bare except`.

### Task 2.7: Reporting LOW cleanups
**Files:** `comprehensive_report.py:635-636` (remove debug `print`s), `formatters.py:3261` (narrow bare `except:`), `deduplication_service.py:492` (normalize dict/attr xpath read via a helper), `formatters.py:368-375` (`_translate_impact` fallback for `info`/`unknown`).
- [ ] One commit per file or a single `chore(reporting): low-severity cleanups` commit with a short test for the `_translate_impact` and `deduplication_service` behavioural ones.

---

## Phase 3 — Models

### Task 3.1: `Website.to_dict()` drops `discovery_history` (HIGH — data loss)
**Files:** `auto_a11y/models/website.py:90-105`; Test new `tests/core/test_model_roundtrip.py`.
- [ ] Failing test: construct `Website` with a non-empty `discovery_history`, `Website.from_dict(w.to_dict())`, assert `discovery_history` survives. Fix: add `'discovery_history': self.discovery_history` to `to_dict()`. Commit: `fix(models): persist Website.discovery_history in to_dict`.

### Task 3.2: `DiscoveryRun.from_dict` default `is_latest` mismatch (HIGH)
**Files:** `auto_a11y/models/discovery_run.py:152`; Test `tests/core/test_model_roundtrip.py`.
- [ ] Failing test: `DiscoveryRun.from_dict({...without is_latest...}).is_latest is True`. Fix: `data.get('is_latest', True)`. Commit: `fix(models): default DiscoveryRun.is_latest to True in from_dict`.

### Task 3.3: UTC-normalize token datetimes (MEDIUM — security/correctness)
**Files:** `auto_a11y/models/api_token.py:83-87,109`, `auto_a11y/models/share_token.py:34,56-60`; Test new `tests/core/test_token_expiry.py`.
- [ ] Failing test: create a token with `TOKEN_LIFETIME_DAYS`-based expiry, assert `created_at`/`expires_at` are tz-aware UTC and `is_valid`/`is_expired` behave correctly across a simulated tz-aware-from-DB read (construct an aware `expires_at` and assert no `TypeError`).
- [ ] Fix: replace `datetime.now()` with `datetime.now(timezone.utc)` for creation; in `is_expired`/`is_valid`, normalize `expires_at` to aware UTC before comparison (treat naive as UTC). Apply the same to `auto_a11y/web/api/tokens.py:77,97` (audit §7 LOW) in this commit.
- [ ] Commit: `fix(models): use tz-aware UTC for token timestamps and expiry checks`

### Task 3.4: Guard `Enum(value)` in `from_dict` across models (MEDIUM)
**Files:** `project.py:267` (`ProjectStatus`), `page.py` (`PageStatus`, `DrupalSyncStatus`), `discovery_run.py` (`DiscoveryStatus`), `recording.py:317-318` (`RecordingType`), `test_result.py:377` (`TargetType`), `issue`/`recording_issue` (`DrupalSyncStatus`), `page_setup_script.py` (`ScriptScope`, `ExecutionTrigger`); Test `tests/core/test_model_roundtrip.py`.
- [ ] Failing test (parametrised): `Project.from_dict({'status': 'bogus', ...})` should not raise and should fall back to a sensible default (`ProjectStatus.ACTIVE`). Add cases per model.
- [ ] Fix: wrap each `Enum(data[...])` in a small helper, e.g. `def _enum_or(enum_cls, value, default): try: return enum_cls(value) except ValueError: return default`, placed in a shared models util, and use it everywhere — matching the intent already shown by `Project.project_type` (project.py:249-252).
- [ ] Commit: `fix(models): fall back on unknown enum values in from_dict`

### Task 3.5: `Violation.to_dict()` side-effect (MEDIUM)
**Files:** `auto_a11y/models/test_result.py:79-82`; Test `tests/core/test_model_roundtrip.py`.
- [ ] Failing test: two calls to `to_dict()` on a `Violation` with `unique_id=None` return the **same** id, and the id is assigned at construction. Fix: generate `unique_id` in `__post_init__` (or `field(default_factory=lambda: str(uuid.uuid4()))`) and make `to_dict` pure. Commit: `fix(models): assign Violation.unique_id at construction, not in to_dict`.

### Task 3.6: Model LOW cleanups
**Files:** `app_user.py:140-147` (decay `failed_login_count` once `locked_until` passes), `project_user.py`/`website_user.py` (encrypt `password` at rest — **scope check:** this needs a key-management decision; if out of scope for this pass, leave the `# TODO` and add a tracking note rather than a half-fix).
- [ ] For the lockout decay: failing test → wait-out-lockout then a single failure must not immediately re-lock. Implement decay in `record_login`. Commit `fix(models): reset failed_login_count after lockout expiry`.
- [ ] For credential encryption: if pursuing, brainstorm key management first (@superpowers:brainstorming) — do **not** improvise crypto inline. Otherwise document as deferred.

---

## Phase 4 — Testing orchestration

### Task 4.1: Process `discovery`/`info` arrays in the result processor (HIGH)
**Files:** `auto_a11y/testing/result_processor.py:167-211`; Test new `tests/test_result_processor.py`.
- [ ] Failing test: feed a fake per-test result dict containing a populated `discovery` array (and `info` array); assert the produced `TestResult` contains those discovery/info items. Fix: iterate `test_result.get('discovery', [])` and `get('info', [])`, routing through `_process_violation` into the discovery/info lists.
- [ ] This also resolves the §10 `test_page` `DiscoResponsiveBreakpoints` drop (Task 10.1) at the processor level. Commit: `fix(testing): route discovery/info arrays through result processor`.

### Task 4.2: `Violation.message` → `.description` (HIGH)
**Files:** `auto_a11y/testing/test_runner.py:452`; Test: unit test the log path or assert the attribute access doesn't raise.
- [ ] Failing test: construct a `Violation`, exercise the script-violation log path, assert no `AttributeError`. Fix: `.description`. Commit: `fix(testing): log Violation.description, not non-existent .message`.

### Task 4.3: SCROLL uses raw selector instead of `pw_selector` (MEDIUM)
**Files:** `auto_a11y/testing/script_executor.py:291-297`.
- [ ] Fix: `await page.locator(pw_selector).scroll_into_view_if_needed()`. Test via a Playwright-backed test if the suite has one (`tests/test_spa_click_discovery.py` shows the pattern); otherwise a focused unit test asserting the selector passed to a mocked `page.locator` carries the `xpath=` prefix for `/`-selectors. Commit: `fix(testing): honour xpath prefix in SCROLL action`.

### Task 4.4: Dead-code AI passing-check branch (MEDIUM)
**Files:** `auto_a11y/testing/result_processor.py:292-333`; Test `tests/test_result_processor.py`.
- [ ] Failing test: AI analysis ran with zero findings → assert a passing check is recorded and `total_passed_checks`/`total_applicable_checks` incremented. Fix: dedent the `if ai_analysis_results and not ai_findings:` block to be a sibling/`elif` of `if ai_findings:` (line 215). Commit: `fix(testing): record passing check when AI analysis finds nothing`.

### Task 4.5: Invalid `{{}}` JS literal in metadata fallback (MEDIUM)
**Files:** `auto_a11y/testing/test_runner.py:554-556`.
- [ ] Fix: change the (non-f-string) fallback to `window.DOCUMENT_METADATA = {};`. Test: assert the emitted JS string equals valid JS (string comparison). Commit: `fix(testing): emit valid empty object in DOCUMENT_METADATA fallback`.

### Task 4.6: Testing LOW cleanups
**Files:** `css_focus_capture.py:294-313` (key by `WeakKeyDictionary`/page attribute instead of `id(page)`), `test_runner.py:696-698` (pass `script_violations` into `process_test_results` so the touchpoint summary stays consistent), `script_executor.py:87-94` (raise/return failure on re-auth failure instead of silently continuing).
- [ ] One focused test + commit each, or grouped `fix(testing): low-severity orchestration fixes` with tests for the re-auth and summary-consistency behaviours.

---

## Phase 5 — AI / scoring / parsers

### Task 5.1: Pass `system=self.system_prompt` in the streaming branch (HIGH)
**Files:** `auto_a11y/ai/claude_client.py:114-142`; Test new `tests/test_claude_client.py`.
- [ ] Failing test: monkeypatch the Anthropic client so `messages.stream(...)` records its kwargs; call an analyze method with `use_extended_thinking=True`; assert `system` kwarg equals `self.system_prompt`. Fix: add `system=self.system_prompt` to the `messages.stream(...)` call. Commit: `fix(ai): include system prompt in extended-thinking streaming path`.

### Task 5.2: Robust JSON extraction (HIGH)
**Files:** `auto_a11y/ai/claude_client.py:160-172`; Test `tests/test_claude_client.py`.
- [ ] Failing test (parametrised): responses with (a) a ```json fenced block surrounded by prose containing `{`/`}`, (b) thinking prose then one JSON object, (c) two JSON blocks → `_extract_json` returns the correct object, not `{'raw_response': ...}`. Fix: try fenced ```json blocks first, then a balanced-brace scan; fall back to raw only when truly unparseable. Commit: `fix(ai): robust JSON extraction from model responses`.

### Task 5.3: Clamp `budget_tokens < max_tokens` (MEDIUM)
**Files:** `auto_a11y/ai/claude_client.py:34-35` (ClaudeConfig `__post_init__`), `auto_a11y/ai/claude_analyzer.py:53-54`.
- [ ] Failing test: config with `budget_tokens >= max_tokens` → after init, `budget_tokens == max_tokens - 1` (and a warning logged). Reconcile the analyzer default (10000) with config (5000) — pick one source of truth (config). Commit: `fix(ai): clamp thinking budget below max_tokens`.

### Task 5.4: Anthropic retry/backoff (MEDIUM)
**Files:** `auto_a11y/ai/claude_client.py` (client construction).
- [ ] Pass `max_retries=` to `AsyncAnthropic`/`Anthropic` (read from config with a sane default, e.g. 3). Test: assert the constructed client received `max_retries`. Commit: `fix(ai): configure SDK retries for transient API failures`.

### Task 5.5: Valid XPath escaping for quotes (MEDIUM)
**Files:** `auto_a11y/ai/analysis_modules.py:104,157,166,170,186`; Test new `tests/test_analysis_modules_xpath.py`.
- [ ] Failing test: id/class/text containing an apostrophe → generated XPath does not contain the literal `&apos;` and is a valid locator (e.g. uses `concat(...)` or double-quotes). Fix accordingly. Commit: `fix(ai): generate valid XPath for values containing quotes`.

### Task 5.6: Intersect failed criteria with applicable set in aggregate scoring (MEDIUM)
**Files:** `auto_a11y/scoring.py:314-325`; Test new `tests/test_scoring.py`.
- [ ] Failing test: a failed criterion outside the applicable set must not reduce `passed_criteria` below 0 / must not be counted. Fix: only add to `failed_criteria_set` if `criterion.id in total_applicable_set` (mirror line 180). Commit: `fix(scoring): only count applicable failed criteria in project aggregate`.

### Task 5.7: AI/scoring/parser LOW cleanups
**Files:** `scoring.py:291` (document or change the averaging), `wcag_parser.py:118-120` (normalize/validate `target_level` before `.index`), `scoring.py:244` (`(?<!\d)(\d+\.\d+\.\d+)(?!\d)`), `utils/fixture_validator.py:37` (`.total_seconds()`), `utils/media_duration.py:130-143` (size-0 atom handling), `claude_analyzer.py:416-422` (count by analysis type or rename), `parsers/recording_content_parser.py:300-303` (label matching), `importers/dictaphone_importer.py:178-180` and `:239-242` (derive counts from parsed issues).
- [ ] Each with a focused unit test where behavioural (`wcag_parser`, `scoring` regex, `fixture_validator`, importer counts). Commit grouped as `fix(ai,scoring,parsers): low-severity correctness fixes` or per-file.

---

## Phase 6 — Core

### Task 6.1: Never send `$inc: {}` to MongoDB on scheduled-run status (HIGH)
**Files:** `auto_a11y/core/database.py:3328-3334`; Test new `tests/core/test_schedule_run_status.py` (real-Mongo, skip-if-unavailable).
- [ ] Failing test: call `update_test_schedule_run_status(..., status=ScheduleRunStatus.SUCCESS)` against a seeded schedule run; assert it succeeds and the run is recorded SUCCESS (currently raises / gets marked FAILED). Fix: build the update dict conditionally — include `$inc` only when `status == RUNNING`.
- [ ] Add a non-Mongo unit assertion too: the constructed update dict has no `$inc` key for SUCCESS/FAILED.
- [ ] Commit: `fix(core): never send empty $inc when recording scheduled-run status`

### Task 6.2: `get_next_run_times` returns fire_time and avoids ValueError (MEDIUM)
**Files:** `auto_a11y/core/scheduler.py:456-461`; Test new `tests/core/test_scheduler_next_runs.py`.
- [ ] Failing test: for a cron trigger, assert returned times are the actual fire times (strictly increasing, matching the trigger) and that a probe landing on second 59 doesn't raise. Fix: `run_times.append(fire_time)` and advance via `next_time = fire_time + timedelta(seconds=1)`. Commit: `fix(scheduler): return real fire times and avoid second-overflow`.

### Task 6.3: `update_job_status` sets `started_at` for RUNNING-without-progress (MEDIUM)
**Files:** `auto_a11y/core/job_manager.py:234-237,257-260`; Test `tests/core/` (real-Mongo).
- [ ] Failing test: report-style RUNNING update with no progress → `started_at` is set. Fix: use `$set` (not `$setOnInsert`) in that branch. Verify duration math in `get_job_statistics`. Commit: `fix(core): set started_at for running jobs without progress`.

### Task 6.4: Core LOW cleanups
**Files:** `email.py:43-47` (add `SMTP_SSL` implicit-TLS path; refuse `login()` over plaintext), `job_manager.py:335-359` (atomic cancel via filter + `modified_count`), `scraper.py:1833-1842` (fetch real robots.txt off-loop, or clearly surface unimplemented).
- [ ] Tests where feasible (email path with a mocked SMTP; cancel race via two sequential calls). The robots.txt change touches network behaviour — gate behind the existing `respect_robots` flag and add a unit test with a mocked fetch. Commit per concern.

---

## Phase 7 — Web app / Fluent i18n

### Task 7.1: `_resolve` must not swallow real format errors (HIGH)
**Files:** `auto_a11y/web/fluent.py:404`; Test `tests/test_fluent.py` (existing).
- [ ] Failing test: a message that exists but raises a genuine format-time error must be distinguishable from a missing message (strict mode should surface it, not report "missing"). Fix: catch only the lookup miss (`LookupError`/`KeyError`); let real format exceptions propagate (or be returned as errors per the strict-mode contract at lines 86-111). Re-run the full `tests/test_fluent.py` + `tests/test_translations.py` to ensure no regression. Commit: `fix(i18n): stop swallowing Fluent format errors in _resolve`.

### Task 7.2: `set_language` rejects unsupported languages (MEDIUM)
**Files:** `auto_a11y/web/app.py:325-330`; Test new `tests/web/test_set_language.py`.
- [ ] Failing test: `POST /set-language/de` → 400 / `status:error`; `…/fr` → 200 and session updated. Fix accordingly. Commit: `fix(i18n): reject unsupported languages in set-language`.

### Task 7.3: Web-app/Fluent LOW cleanups
**Files:** `app.py:103-112` (allow `/health` during recovery), `fluent.py:226-227` (`ftl_enum` guard on `value is None`), `fluent.py:408-414` (`_datetimeformat_filter` validates/catches non-datetime), `api/openapi/document.py:20-21` (doc string vs `_serialize` `exclude_none`).
- [ ] Tests for `ftl_enum` (int-0-backed enum) and the datetime filter (non-datetime input degrades gracefully). Commit grouped `fix(web): low-severity app/fluent fixes`.

---

## Phase 8 — PDF

### Task 8.1: Preserve text across nested marked-content (HIGH)
**Files:** `auto_a11y/pdf/audit/content_streams.py:361-391`; Test `tests/pdf/` (add a fixture content stream with nested BDC/EMC).
- [ ] Failing test: a synthetic content stream where an MCID region contains a nested BDC and draws text both before and after the nested region → assert the outer MCID's `text_content` contains **all** its text. Fix: push/pop `current_text_parts` on the stack alongside `current_mcid` (or accumulate per-MCID in a dict surviving nesting). Commit: `fix(pdf): preserve MCID text across nested marked-content`.

### Task 8.2: ToUnicode parser reads all entries per block (MEDIUM)
**Files:** `auto_a11y/pdf/audit/content_streams.py:184-193,198-232`; Test `tests/pdf/`.
- [ ] Failing test: a bfchar block with multiple `<src> <dst>` pairs on one line (and a single-line block) → all mappings parsed. Fix: tokenize each block by `<…>` groups across the whole block (pairs for bfchar, triples for bfrange). Commit: `fix(pdf): parse all ToUnicode bfchar/bfrange entries per block`.

### Task 8.3: Per-page column detection (MEDIUM)
**Files:** `auto_a11y/pdf/audit/reading_order.py:292-343`; Test `tests/pdf/`.
- [ ] Failing test: a doc whose page 1 is single-column and page 2 two-column → column assignment is correct per page. Fix: compute gaps/boundaries per page (group blocks by `page` before clustering). Commit: `fix(pdf): detect columns per page, not globally`.

### Task 8.4: PDF LOW cleanups
**Files:** `checks/images_alt_text.py:232-250` (add `visited`/depth cap to `_find_interactive_descendants`), `checks/tagging_structure.py:1260-1275` + `reading_order.py:696-700` (clamp correlation ≥ 0 / same common set), `checks/images_alt_text.py:154` (`e.index + 1`), `pdfmax_runner.py:208-219` (content-hash cache key per docstring, or strict `>` + stored hash).
- [ ] Tests where behavioural (index labelling, negative-correlation clamp). Commit grouped `fix(pdf): low-severity correctness/consistency fixes`.

---

## Phase 9 — Drupal / audio

### Task 9.1: Parse Drupal ISO-8601 timestamps (HIGH)
**Files:** `auto_a11y/drupal/issue_importer.py:280-281`; Test new `tests/test_drupal_issue_importer.py`.
- [ ] Failing test: `convert_to_issue_model` with `created`/`changed` as ISO-8601 strings (e.g. `"2024-01-15T10:30:00+00:00"`) → no `TypeError`, correct datetimes. Fix: use `datetime.fromisoformat(...)` (handle trailing `Z`). Commit: `fix(drupal): parse ISO-8601 issue timestamps`.

### Task 9.2: Drupal/audio MEDIUM fixes
**Files:** `drupal/client.py:177` (guard `hasattr(e, 'response')` like POST/PATCH), `audio/cost.py:77-80` (fall back to a default rate + warning instead of `NotImplementedError`), `audio/runner.py:122-142` (apply progress to `fresh`, not stale `rec`).
- [ ] Tests: client error-handler unit test; cost calculator with an unknown model returns a number + warning; runner progress callback test asserting out-of-band fields survive a heartbeat. Commit per concern.

### Task 9.3: Drupal/audio LOW cleanups
**Files:** `audio/segmenter.py:157-232` + `callouts.py:359` (`subprocess.run(timeout=...)`), pagination loops (`issue_importer.py:52-81`, `discovered_page_importer.py:69-88`, `taxonomy.py:202-222,539-557` — follow `links.next` or add an iteration cap), `transcription.py:181-186` + `vtt_processor.py:21-25` (ms-first timestamp formatting), `discovered_page_importer.py:251-252` (handle list/dict note field), `discovered_page_exporter.py:219-224` (`isinstance` discriminator), remove emoji debug `logger.warning`s in `issue_exporter.py`.
- [ ] Tests for the VTT `:60.000` boundary and the pagination cap. Commit grouped `fix(drupal,audio): low-severity robustness fixes`.

---

## Phase 10 — Touchpoint tests

> Note: Task 4.1 already fixes the processor-side drop of `discovery` arrays. Task 10.1 is the test-side alignment.

### Task 10.1: `rem` parsing returns 0 (MEDIUM)
**Files:** `auto_a11y/testing/touchpoint_tests/test_forms.py:1070-1072`, `test_event_handlers.py:883`; Test new `tests/test_touchpoint_parse_px.py`.
- [ ] Failing test: `parse_px('2rem')` returns the numeric value, not 0. Fix: replace `rem` before `em`, or regex the numeric portion. Commit: `fix(touchpoints): parse rem CSS values correctly`.

### Task 10.2: Floating-dialogs dedup + counts (MEDIUM)
**Files:** `auto_a11y/testing/touchpoint_tests/test_floating_dialogs.py:662-666,694`.
- [ ] Failing test: across N breakpoints, the same dialog yields one warning/pass and element totals count it once; `total == passed + failed`. Fix: apply xpath+code dedup to warnings/passes; count elements once; drop the `* 3`. Commit: `fix(touchpoints): dedup floating-dialog warnings/passes and fix counts`.

### Task 10.3: Touchpoint LOW cleanups
**Files:** `test_page.py:127,143` (consistent title pass/fail), `test_links.py:540-550` (count new-window failures; don't double-count as pass), `test_links.py:462` (Space-key regex `(['"]) \1`), `test_tabindex.py:287` (separate counter), `test_title_attribute.py:154` (dedup iframes), bare `except:` → typed (`test_links.py`, `test_buttons.py`, `test_page.py:69`), `parse_color` 3-digit/named-color handling, `test_language.py:133-137` (uppercase region-less code).
- [ ] Add focused unit tests for the regex and `validate_language_code` cases; the count-consistency ones can be asserted via small synthetic inputs. Commit grouped `fix(touchpoints): low-severity counting and parsing fixes`.

---

## Done criteria

- [ ] All HIGH and MEDIUM findings have a regression test that fails before the fix and passes after.
- [ ] `.venv/bin/python -m pytest` is green (run Mongo-backed suites single-process per the conventions note).
- [ ] `.venv/bin/python -m mypy && .venv/bin/python -m pyright && .venv/bin/python -m ty check` are clean.
- [ ] `bun run scripts/check-css-a11y.ts` passes for any template touched.
- [ ] `.venv/bin/python tests/validate_translations.py` passes if any FTL strings were added.
- [ ] Per @superpowers:verification-before-completion, paste the actual command output proving the above before claiming completion.

## Sequencing notes for the executor

- **Phase 1 is the priority and is internally ordered** (1.1 → 1.2 → 1.3…). Do not start 1.3+ before 1.1/1.2 land — every guard test depends on them.
- **Phases 2–10 are mutually independent** and may be executed in any order or in parallel worktrees (@superpowers:using-git-worktrees). Within a phase, do HIGH tasks before MEDIUM before LOW.
- **Task 4.1 and Task 10.1's discovery concern overlap** — 4.1 fixes the root cause; do 4.1 first, then verify 10.1's `DiscoResponsiveBreakpoints` flows through.
- A few findings (model credential encryption in 3.6; robots.txt in 6.4) carry design decisions — if they balloon, split them into their own brainstorm+plan rather than improvising here.
