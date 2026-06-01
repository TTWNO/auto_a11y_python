# Bug Audit — Auto A11y Python

**Date:** 2026-06-01
**Scope:** Comprehensive read-only audit of the Python codebase (`auto_a11y/**`, ~150k LOC) across 10 parallel reviewer passes.
**Status:** Documentation only — **no code was changed.** Each finding lists a file:line, the bug, why it matters, and a one-line fix direction.

> Severity legend: **CRITICAL** (exploitable / data loss now) · **HIGH** (likely broken behavior or security exposure) · **MEDIUM** (real bug, narrower trigger) · **LOW** (edge case, cosmetic, or known TODO).

---

## Executive summary

The single most important theme is a **systemic authorization gap in the legacy web blueprints**: the app's `before_request` enforces *authentication* but most legacy route blueprints (pages, websites, project_users, recordings, scripts, discovered_pages, reports, parts of projects) never received the per-project `@project_role_required` *authorization* checks that the newer code (`pdf.py`, `schedules.py`, `members.py`, `api.py` v1, `projects.view_project`) does have. The result is broad IDOR (Insecure Direct Object Reference) — any logged-in user can read/modify/delete other tenants' data by guessing IDs. **These should be triaged first.**

Other recurring patterns:
- **Naive vs. UTC datetimes** in security tokens and models (expiry skew, possible `TypeError` under `tz_aware` pymongo).
- **Un-guarded `Enum(value)` construction in `from_dict`** across many models (one bad/legacy value fails the whole load).
- **Unescaped user/site data in HTML report formatters** (stored XSS in generated reports).
- **`$inc: {}` / `$setOnInsert` MongoDB update mistakes** that silently break scheduled-run status and job timing.
- **Empty / dropped result arrays** between the JS layer and the Python processor (discovery/info findings lost).

### Counts by severity

| Area | CRITICAL | HIGH | MEDIUM | LOW |
|------|:---:|:---:|:---:|:---:|
| Web routes (security/IDOR) | — | 9 | 4 | 2 |
| Reporting | — | 3 | 3 | 4 |
| Models | — | 2 | 4 | 4 |
| Testing orchestration | — | 2 | 3 | 3 |
| AI / scoring / parsers | — | 2 | 3 | 7 |
| Core | — | 1 | 2 | 3 |
| Web app / Fluent i18n | — | 1 | 1 | 5 |
| PDF | — | 1 | 2 | 4 |
| Drupal / audio | — | 1 | 3 | 5 |
| Touchpoint tests | — | — | 3 | 8 |

---

## 1. Web routes — Authorization / Security (triage first)

Almost all legacy blueprints resolve a resource by ID and act on it with **no project-membership check**. `app.py:415` `before_request` only guarantees the user is logged in.

### [HIGH] IDOR: any logged-in user can view/edit/delete pages in other tenants' projects
- **File:** `auto_a11y/web/routes/pages.py:131,281,313,368,429,453` (and `view_violations:447`)
- **Bug:** `view_page`, `edit_page`, `test_page`, `cancel_test`, `delete_page`, `configure_test_matrix` have no per-project/website authorization.
- **Why:** A CLIENT/AUDITOR in project A can read full results (violations, screenshots, HTML), edit, test, or delete pages in project B by iterating IDs. `projects.view_project` gates with `@project_role_required`, proving authz is the intended model.
- **Fix hint:** Add `@project_role_required(...)` (resolves via `page_id`) to every page route.

### [HIGH] Missing authorization on project edit/delete/test and several project API endpoints
- **File:** `auto_a11y/web/routes/projects.py:600 (edit_project), 764 (delete_project), 819 (test_project), 100 (api_test_details), 944 (api_get_project_users), 967 (api_get_project), 992 (api_get_discovered_pages)`
- **Bug:** No `@project_role_required`/`@auditor_required`, unlike sibling `view_project` (521) and `add_website` (780).
- **Why:** Any authenticated user can rename/delete/launch testing on any project, and read project membership/tester PII/discovered pages they don't belong to.
- **Fix hint:** Decorate edit/delete with `ADMIN`, test with `ADMIN, AUDITOR`; add role checks to the API readers.

### [HIGH] IDOR across the entire websites blueprint
- **File:** `auto_a11y/web/routes/websites.py` — every route, e.g. `view_website:47`, `edit_website:182`, `delete_website:218`, `clear_test_results:236`, `discover_pages:273`, `add_page:517`, `test_all_pages:545`, `manual_session_*:678-919`, `view_documents:922`, `view_discovery_run:1064`
- **Bug:** No route performs any project-membership/role check.
- **Why:** Any authenticated user can read, edit, delete, discover, test, spawn manual browser sessions for, or clear results of any website by iterating IDs. Several are destructive; `manual_session_start` spawns a real browser process.
- **Fix hint:** Add `@project_role_required(...)` (resolves `website_id` → project) to all website routes.

### [HIGH] `api_list_websites` leaks every website in the system
- **File:** `auto_a11y/web/routes/websites.py:25`
- **Bug:** `GET /websites/api/list` returns name/url/project_id for **all** websites to any authenticated user, no membership filter.
- **Why:** Cross-tenant disclosure — a CLIENT enumerates every project's websites and URLs.
- **Fix hint:** Filter to the current user's accessible projects (mirror `projects.api_list_projects`).

### [HIGH] IDOR across project_users blueprint exposes/edits stored test-user credentials
- **File:** `auto_a11y/web/routes/project_users.py` — `list_users:23`, `create_user:43`, `view_user:104`, `edit_user:119`, `delete_user:182`, `test_login:202`, `toggle_user:269`, `clear_cache:290`
- **Bug:** No project-membership check. `ProjectUser` records hold login `username`/`password` for authenticated-site testing.
- **Why:** Any authenticated user can list/create/edit/delete other projects' test users and trigger `test_login` (drives a browser to a login URL with stored creds) — credential exposure + SSRF-adjacent abuse.
- **Fix hint:** Add `@project_role_required(ADMIN, AUDITOR)` resolving via `project_id`/`user.project_id`.

### [HIGH] IDOR in v1 API: `GET /test-results/<result_id>` and `/states` unauthorized
- **File:** `auto_a11y/web/routes/api.py:801 (get_test_result), 1436 (get_test_result_states)`
- **Bug:** Both fetch a result by ID with no `@project_role_required`, no `@api_endpoint`, no inline guard (confirmed by `@document(errors=[404])` only). The adjacent `/pages/<page_id>/test-results` (842) *is* guarded.
- **Why:** Any authenticated user can read any tenant's full `TestResult.to_dict()` (violations, AI findings, HTML/screenshots) by iterating result IDs.
- **Fix hint:** Resolve result → page → website → project and apply the same role gate.

### [HIGH] IDOR across recordings and automated_tests blueprints
- **File:** `recordings.py` (`view_recording:73`, `view_combined_recordings:161`, `process_recording:756`, `download_callouts_video:807`, `cancel_recording:835`, `delete_recording:859`, `api_recording_issues:920`, `api_update_issue_status:955`); `automated_tests.py` (`get_filter_options:33`, `project_automated_tests:93`, `filter_test_results:179`, `upload_to_drupal:255`)
- **Bug:** No project-membership/role checks.
- **Why:** Any authenticated user can view/download/delete recordings, edit issue statuses, and push results to Drupal for projects they don't belong to.
- **Fix hint:** Add `@project_role_required(...)`; gate `upload_to_drupal` to ADMIN/AUDITOR.

### [HIGH] IDOR across scripts blueprint (page setup scripts)
- **File:** `auto_a11y/web/routes/scripts.py:22 (list_page_scripts), 52 (create_page_script)` and remaining routes
- **Bug:** Script routes resolve page/website by ID with no authorization.
- **Why:** Setup scripts can contain `fill`/credential steps and arbitrary selectors; any user can read/create scripts on other tenants' pages (and influence what runs in the test browser).
- **Fix hint:** Add a project-role check to every scripts route.

### [HIGH] IDOR across discovered_pages blueprint
- **File:** `auto_a11y/web/routes/discovered_pages.py:22 (view), 78 (edit), 151 (delete)`
- **Bug:** All three load the discovered page by ObjectId with no membership check; `view` even instantiates a Drupal client with stored credentials.
- **Why:** Any authenticated user can read/edit/delete discovered-page records (incl. private notes) for any project.
- **Fix hint:** Resolve `page.project_id` and enforce a role check.

### [MEDIUM] reports blueprint has no authorization on generation/download/delete
- **File:** `auto_a11y/web/routes/reports.py` — `generate_report:111`, `generate_page_report:480`, `generate_website_report:522`, `generate_project_report:565`, `download_report:415`, `delete_report:435`
- **Bug:** No project-membership checks. `download_report`/`delete_report` operate on an arbitrary `filename` in `REPORTS_DIR`. (Contrast api.py:4606+ which uses `_authorize_report_record`.)
- **Why:** Any authenticated user can generate/download/delete reports of any project by ID or filename.
- **Fix hint:** Resolve project_id/website_id and check role; authorize downloads by the report's scope.

### [MEDIUM] `download_report`/`delete_report` check existence before traversal-containment check
- **File:** `auto_a11y/web/routes/reports.py:419-426, 439-446`
- **Bug:** `file_path = reports_dir / filename` then `if not file_path.exists()` runs *before* the `is_relative_to(reports_dir)` containment check; encoded traversal (`..%2f`) touches the resolved path first.
- **Why:** Leaks existence/timing of arbitrary filesystem paths and relies on `.exists()` ordering for safety.
- **Fix hint:** Verify containment first, then existence; ideally `werkzeug.utils.secure_filename`.

### [MEDIUM] `generate_page/website/project_report` crash on non-JSON / empty JSON body
- **File:** `auto_a11y/web/routes/reports.py:483, 525, 568`
- **Bug:** `request.form.get('format', request.json.get('format', 'html') if request.is_json else 'html')` — Python eagerly evaluates `request.json.get(...)` even when `'format'` is in the form; if the body is JSON but `request.json` is `None`/not a dict, it raises `AttributeError` → 500.
- **Why:** Unhandled 500 on malformed bodies.
- **Fix hint:** Two-step parse; `request.get_json(silent=True) or {}`.

### [MEDIUM] `schedules_dashboard` lists schedules with no scope filtering
- **File:** `auto_a11y/web/routes/schedules.py:29`
- **Bug:** No `@project_role_required` while every other route in the file has one.
- **Why:** Potential cross-tenant disclosure if the dashboard aggregates across all websites (needs template confirmation).
- **Fix hint:** Filter to the user's accessible projects, or restrict to superadmin.

### [LOW] `revoke_token` has no auth decorator (relies on implicit `None` role)
- **File:** `auto_a11y/web/routes/share_tokens.py:129`
- **Bug:** Only share-token route without `@project_role_required`; manually checks `is_superadmin` then `get_effective_role` (returns 403 for anonymous, so currently safe). Breaks if the global guard changes.
- **Fix hint:** Add `@project_role_required(ADMIN, AUDITOR)` for consistency.

### [LOW] Broad `except Exception` returns raw `str(e)` to clients in many routes
- **File:** e.g. `projects.py:73,153,199,242,963,988,1017`; `websites.py:319,508,669,719,888`; `project_users.py:198,265`; `discovered_pages.py:73,145,183`
- **Bug:** Generic catches put `str(e)` into the JSON/flash response.
- **Why:** Leaks internal error detail (paths, Mongo errors, stack fragments).
- **Fix hint:** Log server-side; return a generic message.

> **Reviewed and found secure:** `auth.py` (login `next` open-redirect protection, MSAL auth-code SSO, Google PKCE/state, single-use password reset), `pdf.py` (consistent `_require_pdf_role` + path-traversal guards), `members.py`, `admin_settings.py`, `recovery.py`.

---

## 2. Reporting

### [HIGH] AI findings crash project "All Issues" Excel sheet — `.upper()` on enum
- **File:** `auto_a11y/reporting/formatters.py:2331`
- **Bug:** `getattr(f, 'severity', 'MEDIUM').upper()` — `AIFinding.severity` is an `ImpactLevel` enum (`models/test_result.py:165`), which has no `.upper()`.
- **Why:** Any page with AI findings raises `AttributeError`, failing the whole Excel report. Sibling `_create_ai_findings_sheet` (2125-2128) handles it correctly via `.value`.
- **Fix hint:** Mirror `_create_ai_findings_sheet`: read `.value` if present before `.upper()`.

### [HIGH] Unescaped user/site data in `HTMLFormatter` (stored XSS in generated HTML/PDF reports)
- **File:** `auto_a11y/reporting/formatters.py:1271, 1367, 632-643, 1109` (HTMLFormatter throughout)
- **Bug:** HTML built entirely with raw f-strings; page titles/URLs, issue descriptions, xpath, project/website names never HTML-escaped. `_streaming_issue_row` emits `<td>{description}</td><td>{xpath}</td>` unescaped.
- **Why:** A page title/URL/alt-text/HTML-fragment captured from a tested (attacker-controlled) site can contain `<script>` and is injected verbatim; opening the report executes it. Sibling generators escape correctly — this one is the outlier.
- **Fix hint:** Run all interpolated dynamic values through `html.escape()`.

### [HIGH] Unescaped user data in `project_report.py` HTML
- **File:** `auto_a11y/reporting/project_report.py:136, 182, 184` (and the website card loop)
- **Bug:** `{self.project.name}`, `{self.project.description ...}`, website name/url interpolated as raw f-strings.
- **Why:** Same stored-XSS / markup corruption; these are user-entered free text.
- **Fix hint:** `html.escape()` project name, description, and website fields.

### [MEDIUM] Unescaped AI-generated text in executive summary HTML
- **File:** `auto_a11y/reporting/ai_executive_summary.py:472, 480, 487, 519, 528, 538, 573, 601-602`
- **Bug:** All AI-derived strings interpolated into HTML with no escaping.
- **Why:** Model output containing `<`/`&`/markup breaks the report and is a content-injection vector.
- **Fix hint:** `html.escape()` each value.

### [MEDIUM] Average page score excludes zero-score pages, inflating the average
- **File:** `auto_a11y/reporting/static_html_generator.py:1890-1891` (also `1629`)
- **Bug:** `scores = [p['score'] for p in pages_data if p['score'] > 0]` — pages scoring exactly 0 (the worst pages) are dropped from the denominator.
- **Why:** Site-wide average is biased upward; the most broken pages vanish from the compliance summary, and it feeds `compliance_level` thresholds (1894-1899).
- **Fix hint:** Gate on `total_tests > 0` if 0 means "not scored," else include `if p['score'] is not None`.

### [MEDIUM] Hardcoded absolute fallback path leaks a developer's machine path
- **File:** `auto_a11y/reporting/page_structure_report.py:945`
- **Bug:** `os.environ.get('REPORTS_DIR', '/Users/bob3/Desktop/auto_a11y_python/reports')`.
- **Why:** On any machine where `REPORTS_DIR` is unset and `current_app` lookup fails, `mkdir` targets a foreign/nonexistent path.
- **Fix hint:** Default to a repo-relative `Path('reports')`.

### [LOW] Bare `except:` swallows all errors in Flask config lookup
- **File:** `auto_a11y/reporting/page_structure_report.py:940` (also `formatters.py:3261` in `_auto_adjust_columns`)
- **Bug:** `try: ... except: pass` with no exception type — catches `KeyboardInterrupt`/`SystemExit`.
- **Fix hint:** Use `except Exception:` (or narrower) and log.

### [LOW] Debug `print()` left in production report path
- **File:** `auto_a11y/reporting/comprehensive_report.py:635-636`
- **Bug:** `print("DEBUG COMPLIANCE: ...")` and dumps full `stats` dict on every comprehensive report.
- **Fix hint:** Remove; adjacent `logger.info` already records it.

### [LOW] `deduplication_service.py` xpath lookup assumes object, not dict
- **File:** `auto_a11y/reporting/deduplication_service.py:492`
- **Bug:** `getattr(violation, 'xpath', None) or getattr(violation, 'metadata', {}).get('xpath')` returns `None` for dict-shaped violations even when xpath is present.
- **Why:** Component-vs-page linkage silently fails for dict violations.
- **Fix hint:** Normalize via a dict/attr helper before reading `xpath`/`metadata`.

### [LOW] `_translate_impact` upper-cases untranslated 'info'/'discovery' impacts
- **File:** `auto_a11y/reporting/formatters.py:368-375`
- **Bug:** For impacts outside high/medium/low, falls to `impact_raw.upper()` → raw `INFO`/`UNKNOWN` untranslated in FR reports.
- **Fix hint:** Map additional impact values or fall back to a translated 'unknown'.

---

## 3. Models

### [HIGH] `Website.to_dict()` drops `discovery_history`, silently wiping it on save
- **File:** `auto_a11y/models/website.py:90-105`
- **Bug:** `to_dict()` omits `discovery_history` even though `from_dict()` reads it (122) and it's declared (66).
- **Why:** Any load → mutate → save round-trip loses all accumulated discovery history. Data loss on every write.
- **Fix hint:** Add `'discovery_history': self.discovery_history` to `to_dict()`.

### [HIGH] `DiscoveryRun.from_dict()` defaults `is_latest` to `False`, contradicting the dataclass/`to_dict`
- **File:** `auto_a11y/models/discovery_run.py:152`
- **Bug:** Dataclass default is `True` (58) and `to_dict` always writes it, but `from_dict` uses `data.get('is_latest', False)`.
- **Why:** Legacy/missing-key docs deserialize as not-latest, hiding the most recent discovery from UI/queries.
- **Fix hint:** `data.get('is_latest', True)`.

### [MEDIUM] Token expiry/validity uses naive `datetime.now()` (local time), not UTC
- **File:** `auto_a11y/models/api_token.py:83-87,109`, `auto_a11y/models/share_token.py:34,56-60`
- **Bug:** `created_at`/`expires_at` set with naive local-time `datetime.now()`; `is_expired`/`is_valid` compare against `datetime.now()`. Mongo returns naive UTC on read.
- **Why:** Tokens written in local time but checked against UTC-from-DB → expiry skew equal to the UTC offset; breaks entirely across timezones. Security-relevant.
- **Fix hint:** Use `datetime.now(timezone.utc)` consistently, store tz-aware.

### [MEDIUM] Token validity can raise `TypeError` under a `tz_aware` pymongo client
- **File:** `auto_a11y/models/share_token.py:60`, `auto_a11y/models/api_token.py:109`
- **Bug:** If pymongo is `tz_aware=True`, `expires_at` is tz-aware while `datetime.now()` is naive → `datetime.now() > self.expires_at` raises `TypeError`.
- **Why:** Token validation crashes rather than returning a bool — denying/erroring all token-authenticated requests.
- **Fix hint:** Normalize both sides to tz-aware UTC before comparing.

### [MEDIUM] `Project.from_dict` rejects unknown/legacy `status` values (no fallback)
- **File:** `auto_a11y/models/project.py:267`
- **Bug:** `ProjectStatus(data.get('status', 'active'))` is not wrapped in try/except, unlike `project_type` directly above (249-252). Same pattern in `Page.from_dict` (`PageStatus`, `DrupalSyncStatus`), `DiscoveryRun`, `Recording`, `Issue`/`RecordingIssue`, `PageSetupScript`.
- **Why:** A legacy/unexpected `status` raises `ValueError` and the whole object fails to load — the author already demonstrated the intended fallback for `project_type`.
- **Fix hint:** Wrap enum construction in try/except with a sensible default everywhere.

### [MEDIUM] `Violation.to_dict()` mutates the instance as a side effect
- **File:** `auto_a11y/models/test_result.py:79-82`
- **Bug:** Assigns `self.unique_id = str(uuid.uuid4())` when `None` — serialization changes state.
- **Why:** A "read" method changes observable state; concurrency hazard, and a logging call before persist fixes the id by whichever call ran first.
- **Fix hint:** Generate `unique_id` in `__post_init__` / `field(default_factory=...)`.

### [LOW] `TargetType` constructed without fallback in `TestResult.from_dict`
- **File:** `auto_a11y/models/test_result.py:377`
- **Bug:** `TargetType(data['target_type'])` raises `ValueError` on an unrecognized stored value.
- **Fix hint:** try/except → `TargetType.PAGE`.

### [LOW] `AppUser` re-locks immediately after lockout expiry
- **File:** `auto_a11y/models/app_user.py:140-147`
- **Bug:** `failed_login_count` is never decayed when `locked_until` expires, so the next single failed attempt re-locks (count still ≥5).
- **Fix hint:** Reset/decay `failed_login_count` once `locked_until` has passed.

### [LOW] `ProjectUser` / `WebsiteUser` store plaintext `password`
- **File:** `auto_a11y/models/project_user.py:100,163`, `auto_a11y/models/website_user.py:100,163`
- **Bug:** `password` persisted in plaintext (code flags `# TODO: Encrypt`).
- **Why:** Test-account credentials for sites under test stored unencrypted in MongoDB.
- **Fix hint:** Encrypt at rest (e.g. Fernet key from config) in `to_dict`/`from_dict`.

### [LOW] `Recording.from_dict` `recording_type` has no invalid-value fallback
- **File:** `auto_a11y/models/recording.py:317-318`
- **Bug:** `RecordingType(recording_type_value)` raises on unknown non-empty values (only the falsy case is guarded).
- **Fix hint:** try/except → `RecordingType.AUDIT`.

---

## 4. Testing orchestration

### [HIGH] Discovery and info arrays from touchpoint tests are silently dropped
- **File:** `auto_a11y/testing/result_processor.py:167-211`
- **Bug:** `process_test_results` only reads `errors`/`warnings`/`passes` per test result; it never reads the separate `discovery`/`info` arrays that tests emit (e.g. `test_page.py:217` `DiscoResponsiveBreakpoints`, `test_images.py:64`). `script_injector.run_all_tests` even filters those keys (312-322) before handing off.
- **Why:** Discovery/info items placed in their dedicated arrays never reach the final `TestResult`. Reports under-report.
- **Fix hint:** Also iterate `test_result.get('discovery', [])` / `get('info', [])`, routing each through `_process_violation`.

### [HIGH] `result['violation'].message` references a non-existent attribute
- **File:** `auto_a11y/testing/test_runner.py:452`
- **Bug:** `logger.warning(f"... {result['violation'].message}")` — the `Violation` dataclass has `description`, not `message`.
- **Why:** Every script-condition violation raises `AttributeError`; caught by the per-script try/except (474), so the real violation-handling path is aborted and mislogged as a generic error.
- **Fix hint:** Use `.description` (or `.failure_summary`).

### [MEDIUM] SCROLL action ignores XPath prefix, breaking XPath selectors
- **File:** `auto_a11y/testing/script_executor.py:291-297`
- **Bug:** Builds `pw_selector` with the `xpath=` prefix (293) but then calls `page.locator(selector)` with the *raw* `selector`.
- **Why:** XPath SCROLL steps are treated as CSS selectors and always error.
- **Fix hint:** Use `page.locator(pw_selector)`.

### [MEDIUM] Dead code: "AI ran but found no issues" passing-check branch never executes
- **File:** `auto_a11y/testing/result_processor.py:292-333`
- **Bug:** `if ai_analysis_results and not ai_findings:` is nested inside `if ai_findings:` (215), so `not ai_findings` is always False.
- **Why:** AI-clean pages never record a passing check; they're under-counted in the applicability-aware score (`passed/applicable`).
- **Fix hint:** Dedent the block to be a sibling/`elif` of `if ai_findings:`.

### [MEDIUM] `inject_all_scripts` writes invalid JS `{{}}` for `DOCUMENT_METADATA` fallback
- **File:** `auto_a11y/testing/test_runner.py:554-556`
- **Bug:** The fallback string `window.DOCUMENT_METADATA = {{}};` is **not** an f-string, so the doubled braces reach the browser literally → JS syntax error.
- **Why:** If metadata injection fails, the fallback throws instead of setting `{}`, leaving `DOCUMENT_METADATA` undefined.
- **Fix hint:** Use single braces `{}` (this string is not an f-string).

### [LOW] `id()`-keyed CSS-capture registry can leak or alias across pages
- **File:** `auto_a11y/testing/css_focus_capture.py:294-313`
- **Bug:** `_page_css_cache` keyed by `id(page)`; CPython reuses `id()` after GC, so a new `Page` can read a stale `CSSFocusCapture`. Leaks if a page is dropped without `close`.
- **Fix hint:** Use a `WeakKeyDictionary`/attach to the page instead of `id(page)`.

### [LOW] Script violations appended after the touchpoint summary is built
- **File:** `auto_a11y/testing/test_runner.py:696-698` (with `result_processor.py:341-403`)
- **Bug:** `script_violations` appended to `test_result.violations` *after* the per-touchpoint `checks` summary is computed.
- **Why:** Those violations count in `violation_count` but are missing from the check breakdown.
- **Fix hint:** Pass `script_violations` into `process_test_results` (or recompute the summary after extending).

### [LOW] Re-authentication failure in `execute_script` proceeds unauthenticated
- **File:** `auto_a11y/testing/script_executor.py:87-94`
- **Bug:** A failed `perform_login` after clearing state only logs an error, then continues the step loop.
- **Why:** The script then runs unauthenticated, producing misleading results with no caller signal.
- **Fix hint:** Raise `ScriptExecutionError` / return a failure result on re-auth failure.

---

## 5. AI / scoring / parsers

### [HIGH] System prompt dropped when extended thinking is enabled (the default)
- **File:** `auto_a11y/ai/claude_client.py:114-142`
- **Bug:** The `if self.config.use_extended_thinking:` streaming branch calls `messages.stream(...)` **without** `system=self.system_prompt`; only the non-streaming `else` passes it. `use_extended_thinking` defaults to `True`.
- **Why:** All real analyses run without the accessibility system prompt (role, WCAG expertise, "respond with valid JSON"), degrading quality and JSON reliability.
- **Fix hint:** Add `system=self.system_prompt` to the `messages.stream(...)` call.

### [HIGH] `_extract_json` uses first-`{` to last-`}`, mangling responses with stray braces
- **File:** `auto_a11y/ai/claude_client.py:160-172`
- **Bug:** Slices `response_text[first '{' : last '}'+1]`; any prose/thinking aside or second JSON block containing braces makes the slice span unrelated content → `json.loads` fails → silent `{'raw_response': ...}`.
- **Why:** Findings parsing depends entirely on this; a stray brace discards the entire analysis with only a warning.
- **Fix hint:** Balanced-brace scan, or parse fenced ```json blocks first.

### [MEDIUM] `budget_tokens` default can exceed/approach `max_tokens` and is inconsistent
- **File:** `auto_a11y/ai/claude_analyzer.py:53-54`, `auto_a11y/ai/claude_client.py:34-35`
- **Bug:** Analyzer defaults `budget_tokens=10000` (Config defaults 5000); no validation that `budget_tokens < max_tokens`. The thinking budget must be strictly less than `max_tokens`.
- **Why:** A user setting `CLAUDE_BUDGET_TOKENS >= CLAUDE_MAX_TOKENS` causes an opaque runtime API error and leaves little room for JSON output.
- **Fix hint:** Clamp `budget_tokens = min(budget_tokens, max_tokens - 1)` in `__post_init__`.

### [MEDIUM] No retry/backoff on Anthropic API failures
- **File:** `auto_a11y/ai/claude_client.py:201-222, 224-250, 252-341`
- **Bug:** All three analyze methods just `except Exception: logger.error; raise`; no handling for 429/529/timeouts and `max_retries` not configured on the client.
- **Why:** A single transient failure aborts the whole page analysis.
- **Fix hint:** Pass `max_retries=` to `(Async)Anthropic`, or add backoff for `RateLimitError`/`APIStatusError`.

### [MEDIUM] XPath single-quote escaping uses `&apos;`, producing invalid XPath literals
- **File:** `auto_a11y/ai/analysis_modules.py:104, 157, 166, 170, 186`
- **Bug:** Single quotes replaced with `&apos;` inside a single-quoted XPath literal (e.g. `//*[@id='it&apos;s']`); XPath doesn't interpret the entity.
- **Why:** Any id/class/text with an apostrophe yields a locator that matches the wrong/no element.
- **Fix hint:** Use `concat('it', "'", 's')`, or double-quote when the value has only single quotes.

### [MEDIUM] Aggregate compliance score: failed criteria not intersected with applicable set
- **File:** `auto_a11y/scoring.py:314-325`
- **Bug:** `failed_criteria_set` built from every issue's criterion `if criterion:` without checking membership in `total_applicable_set`. The per-recording path (180) does filter correctly.
- **Why:** `passed = total_applicable - failed` can undercount or go negative; compliance score skewed below true value.
- **Fix hint:** Only add to `failed_criteria_set` if `criterion.id in total_applicable_set`.

### [LOW] Aggregate accessibility score averages clamped per-recording scores
- **File:** `auto_a11y/scoring.py:291`
- **Bug:** Unweighted mean of already-saturated (0–100, clamped at 0) per-recording scores masks total issue volume.
- **Fix hint:** Compute the deductive score over the pooled issue list, or document the averaging.

### [LOW] WCAG level lookup raises `ValueError` on unexpected `target_level`
- **File:** `auto_a11y/wcag_parser.py:118-120`
- **Bug:** `level_hierarchy.index(target_level)` raises on lowercase/empty levels; used in scoring while `_is_within_level` validates and this one doesn't.
- **Fix hint:** Normalize (uppercase + membership) before `.index`.

### [LOW] `extract_criterion_number` `\b...\b` anchoring drops 4-part criteria
- **File:** `auto_a11y/scoring.py:244`
- **Bug:** `\b(\d+\.\d+\.\d+)\b` against `"2.1.1.1"` fails the trailing `\b` and returns None, silently dropping a valid-looking criterion.
- **Fix hint:** `(?<!\d)(\d+\.\d+\.\d+)(?!\d)`.

### [LOW] Fixture cache freshness uses `timedelta.seconds`, ignoring the day component
- **File:** `auto_a11y/utils/fixture_validator.py:37`
- **Bug:** `.seconds` returns only the 0–86399 within-day part; a ~24h-old entry reports near-0 and looks fresh.
- **Fix hint:** Use `.total_seconds()`.

### [LOW] `_find_atom` (MP4) can bail early on a size-0 atom
- **File:** `auto_a11y/utils/media_duration.py:130-143`
- **Bug:** A size-0 ("to end of file") atom preceding others makes the search return None ("no mvhd") rather than continuing.
- **Fix hint:** Treat size-0 as last-atom only after checking type match.

### [LOW] `_count_by_type` groups AI findings by touchpoint, not analysis type
- **File:** `auto_a11y/ai/claude_analyzer.py:416-422`
- **Bug:** Comment says "analyzer name" but reads `finding.touchpoint`; multiple analysis types collapse into one key.
- **Fix hint:** Count on the stored analysis type, or rename to `by_touchpoint`.

### [LOW] `parse_user_assertions` start/end label conditions are contradictory
- **File:** `auto_a11y/parsers/recording_content_parser.py:300-303`
- **Bug:** The second disjunct requires `'start'` present and `'time'` absent, contradicting the first `'start time' in` clause; labels like `"Start/End time"` misroute.
- **Fix hint:** Match explicit labels (`startswith`) rather than overlapping substrings.

### [LOW] Dictaphone importer impact-count diverges from `ImpactLevel` mapping
- **File:** `auto_a11y/importers/dictaphone_importer.py:178-180`
- **Bug:** Counts only literal `high/critical`, `medium/moderate`, `low`, but `map_dictaphone_impact` (374-382) also maps `serious`→HIGH, `minor`→LOW; those are excluded from the tallies.
- **Fix hint:** Derive counts from parsed `RecordingIssue.impact` values (as `import_pipeline_output` does).

### [LOW] Dictaphone importer: per-issue parse failures inflate `total_issues`
- **File:** `auto_a11y/importers/dictaphone_importer.py:239-242`
- **Bug:** Counts computed from `len(issues_data)` (209) before parsing; issues that fail to parse are skipped, so stored counts overstate persisted issues.
- **Fix hint:** Recompute counts from successfully-parsed `issues` after the loop.

---

## 6. Core

### [HIGH] Empty `$inc: {}` in schedule run-status update breaks every successful scheduled run
- **File:** `auto_a11y/core/database.py:3328-3334`
- **Bug:** `update_test_schedule_run_status` sends `"$inc": {}` for any non-RUNNING status (SUCCESS/FAILED). MongoDB rejects an empty `$inc`.
- **Why:** `execute_scheduled_test` (scheduler.py:631) calls it with `SUCCESS` inside the try block; the write raises, the outer `except` (640) marks the run FAILED, so **every successful scheduled run is recorded as a failure** and SUCCESS/last_run never persists. (High confidence on Mongo semantics; not executed against a live Mongo here.)
- **Fix hint:** Only include `$inc` when status is RUNNING; never pass `$inc: {}`.

### [MEDIUM] `get_next_run_times` returns the probe time and can raise `ValueError`
- **File:** `auto_a11y/core/scheduler.py:456-461`
- **Bug:** Computes `fire_time` but appends `next_time` (probe), then advances via `next_time.replace(second=next_time.second + 1)` — raises `ValueError` when second ≥ 59.
- **Why:** Returned list is wrong, and any probe landing on second 59 crashes the function.
- **Fix hint:** Append `fire_time`; advance with `fire_time + timedelta(seconds=1)`.

### [MEDIUM] `update_job_status` never sets `started_at` for RUNNING jobs without progress
- **File:** `auto_a11y/core/job_manager.py:234-237, 257-260`
- **Bug:** Uses `$setOnInsert` for `started_at` but `update_one` has no `upsert=True`, so it's silently ignored. Report jobs (`report_job.py:51` RUNNING with no progress) get null `started_at`.
- **Why:** Breaks duration math in `get_job_statistics` (`$subtract` yields null/garbage).
- **Fix hint:** Use `$set` for `started_at` in this branch.

### [LOW] SMTP TLS-disabled path sends credentials in cleartext; no implicit-SSL support
- **File:** `auto_a11y/core/email.py:43-47`
- **Bug:** Both branches use `smtplib.SMTP`; no `SMTP_SSL` (port 465) path, and the no-TLS branch still `login()`s over plaintext.
- **Fix hint:** Add an implicit-TLS path; refuse `login()` over a non-TLS socket.

### [LOW] `request_cancellation` has a check-then-update race
- **File:** `auto_a11y/core/job_manager.py:335-359`
- **Bug:** Separate `find_one` status check then `update_one`; not atomic.
- **Fix hint:** Fold the status guard into the `update_one` filter and act on `modified_count`.

### [LOW] `robots.txt` enforcement is a no-op despite `respect_robots`
- **File:** `auto_a11y/core/scraper.py:1833-1842`
- **Bug:** `_can_fetch` hardcodes `rp.parse(['User-agent: *', 'Allow: /'])`, never fetching real robots.txt; `can_fetch` always True even when `respect_robots` is enabled (checked at 472). (Has a TODO.)
- **Fix hint:** Fetch/parse live robots.txt (off-loop via `asyncio.to_thread`), or surface that it's unimplemented.

---

## 7. Web app / Fluent i18n

### [HIGH] `_resolve` swallows every exception, defeating strict-mode error reporting
- **File:** `auto_a11y/web/fluent.py:404`
- **Bug:** `except (KeyError, Exception): return None` (KeyError redundant). Any `bundle.format()` failure (real format-time errors, bad param names) becomes "message not found."
- **Why:** Strict mode (86-111) relies on distinguishing "missing" (`None`) from "found-with-errors" (`(value, errors)`); a raising `format()` is reported as missing-from-locale, and in non-strict mode it silently falls back, hiding real i18n bugs.
- **Fix hint:** Catch only the lookup miss (`LookupError`/`KeyError`); let real format exceptions surface.

### [MEDIUM] `set_language` reports success for unsupported languages without applying them
- **File:** `auto_a11y/web/app.py:325-330`
- **Bug:** Stores only `en`/`fr` but always returns `{'status':'success','language':language}` with 200, even for `de`.
- **Why:** Caller can't tell the request was ignored; confusing UX.
- **Fix hint:** Return 400/`status:error` when the language isn't supported.

### [LOW] `force_recovery` interceptor doesn't exempt `public.`/`/health`
- **File:** `auto_a11y/web/app.py:103-112`
- **Bug:** Recovery mode redirects everything except `/recovery`/`/static`; health checks and public share routes get 302'd to an HTML page.
- **Fix hint:** Also allow `/health` (or return 503) so monitors distinguish recovery from healthy.

### [LOW] `ftl_enum` returns empty string for falsy-but-valid enum values
- **File:** `auto_a11y/web/fluent.py:226-227`
- **Bug:** `if not value:` short-circuits before the `enum-` lookup; an enum backed by `0`/empty bypasses translation.
- **Fix hint:** Guard on `value is None` explicitly, not generic falsiness.

### [LOW] `_datetimeformat_filter` crashes on non-datetime input
- **File:** `auto_a11y/web/fluent.py:408-414`
- **Bug:** Only guards `value is None`; a non-datetime (e.g. a string/int) raises inside Babel `format_datetime` → 500 during render.
- **Fix hint:** Validate it's a datetime (or catch) before formatting.

### [LOW] `@document` docstring vs `_serialize` disagree on `exclude_none`
- **File:** `auto_a11y/web/api/openapi/document.py:20-21` vs `232/239`
- **Bug:** Module docstring claims `exclude_none=True`; `_serialize` deliberately omits it. Pure doc drift.
- **Fix hint:** Update the module docstring to match `_serialize`.

### [LOW] API token timestamps use naive `datetime.now()`
- **File:** `auto_a11y/web/api/tokens.py:77, 97`
- **Bug:** `last_used_at`/`revoked_at` use naive local time while the rest of the code uses `datetime.now(timezone.utc)`.
- **Fix hint:** Use `datetime.now(timezone.utc)` consistently.

---

## 8. PDF

### [HIGH] Content-stream MCID text is lost when marked-content regions nest
- **File:** `auto_a11y/pdf/audit/content_streams.py:361-391`
- **Bug:** A nested `BDC` overwrites/resets the single shared `current_text_parts`; on the inner `EMC` the parent's accumulated text is discarded (resets again at 386/391).
- **Why:** Nested marked-content sequences are routine in Word/InDesign tagged PDFs; the result is truncated/empty `text_content`, cascading into false positives in reading-order matching, alt-text fallbacks, table/heading previews.
- **Fix hint:** Push/pop `current_text_parts` on the stack alongside `current_mcid` (or accumulate per-MCID in a dict that survives nesting).

### [MEDIUM] `/ToUnicode` bfchar/bfrange parser only reads the first entry per physical line
- **File:** `auto_a11y/pdf/audit/content_streams.py:184-193, 198-232`
- **Bug:** Blocks split on `"\n"`; per line only `parts[0..2]` consumed. Multiple `<src> <dst>` pairs per line (valid CMap), or a single-line block, drop all but the first mapping.
- **Why:** Fonts with packed CMaps decode text incorrectly, affecting the Matterhorn invalid-Unicode scan too.
- **Fix hint:** Tokenize each block by `<…>` groups across the whole block (pairs/triples), not per-`\n` first-match.

### [MEDIUM] `detect_columns` applies one global boundary set to every page
- **File:** `auto_a11y/pdf/audit/reading_order.py:292-343`
- **Bug:** Computes gaps/boundaries from all blocks across all pages, then applies them per page. A doc mixing single- and two-column pages mis-assigns blocks.
- **Why:** Wrong column assignment scrambles computed visual order → spurious WCAG 1.3.2 mismatches.
- **Fix hint:** Compute boundaries per page (group blocks by `page` before clustering).

### [LOW] `_find_interactive_descendants` recurses without a depth/cycle guard
- **File:** `auto_a11y/pdf/audit/checks/images_alt_text.py:232-250`
- **Bug:** Unlike the table/list BFS helpers (which carry `visited`), no cycle protection; a malformed/duplicated index could recurse unboundedly → `RecursionError` on hostile input.
- **Fix hint:** Add a `visited: set[int]` or a depth cap.

### [LOW] `_correlation_from_mismatches` can return a negative "correlation"
- **File:** `auto_a11y/pdf/audit/checks/tagging_structure.py:1260-1275`, `reading_order.py:696-700`
- **Bug:** `mismatch_count` (from a different common set) can exceed `max_inversions`, yielding a negative ratio shown as e.g. "-40%". Verdict still FAIL, so cosmetic.
- **Fix hint:** `max(0.0, …)` and/or derive `mismatch_count` from the same common set.

### [LOW] `check_alt_text_on_figure_art` reports 0-based indices vs 1-based siblings
- **File:** `auto_a11y/pdf/audit/checks/images_alt_text.py:154`
- **Bug:** Emits `[{e.index}]` while every sibling check uses `e.index + 1`.
- **Fix hint:** Use `e.index + 1`.

### [LOW] `cached_or_run_pdfmax` mtime cache can serve a stale report
- **File:** `auto_a11y/pdf/pdfmax_runner.py:208-219`
- **Bug:** Cache validity `report.st_mtime >= pdf.st_mtime`; an in-place overwrite within the same mtime granularity reuses the stale `.md`. The content-hash key the docstring claims is not implemented.
- **Fix hint:** Key on PDF content hash (per the docstring), or use strict `>` plus a stored source hash.

> **Reviewed and found sound:** `ghostscript.render_page_to_png` temp-file handling, `storage.write_pdf_bytes` fsync+rename, `_median_rgb` empty guard, `walk_structure_tree`/`resolve_tag` cycle protection, `/ParentTree` MCID handling, `pikepdf_helpers.get_int`. No mutable-default-argument bugs.

---

## 9. Drupal / audio

### [HIGH] Drupal timestamps parsed as Unix epoch instead of ISO 8601
- **File:** `auto_a11y/drupal/issue_importer.py:280-281`
- **Bug:** `datetime.fromtimestamp(drupal_issue['created_timestamp'])` but those values come from `_parse_issue_node` (156-157, 172-173) as ISO 8601 strings (`attributes.get('created')`, typed `str | None`).
- **Why:** `fromtimestamp` requires a numeric POSIX timestamp; a string raises `TypeError`, so importing any issue with a non-null created/changed (every real node) fails.
- **Fix hint:** Use `datetime.fromisoformat(...)`, or request the `*_timestamp` fields.

### [MEDIUM] GET error handler accesses `e.response` without a guard (unlike POST/PATCH)
- **File:** `auto_a11y/drupal/client.py:177`
- **Bug:** `get()` does `if e.response is not None and ...` while `post()` (229) and `patch()` (273) correctly use `hasattr(e, 'response')` first.
- **Why:** On a connection-level error, `e.response` access can itself raise `AttributeError`, masking the original.
- **Fix hint:** Mirror POST/PATCH: `if hasattr(e, 'response') and e.response is not None`.

### [MEDIUM] Cost calculator hard-fails for any model other than `claude-opus-4-7`
- **File:** `auto_a11y/audio/cost.py:77-80` (via `analysis.py:123`)
- **Bug:** `cost_for_anthropic_call` raises `NotImplementedError` unless `model == "claude-opus-4-7"`; called unconditionally after a successful (paid) Claude response. `claude_model` comes from `CLAUDE_MODEL` env.
- **Why:** Setting any other model throws *after* the API call — losing the result and failing the recording purely over cost bookkeeping.
- **Fix hint:** Fall back to a default rate + warning instead of raising.

### [MEDIUM] `progress()` callback persists a stale Recording (lost-update race)
- **File:** `auto_a11y/audio/runner.py:122-142`
- **Bug:** Re-fetches `fresh` only to check the cancel flag, then mutates and saves the start-of-run `rec`, not `fresh`.
- **Why:** Out-of-band field changes by the web layer are overwritten on the next heartbeat.
- **Fix hint:** Apply the progress update to `fresh` (or a targeted `$set`).

### [LOW] ffmpeg/ffprobe subprocesses run with no timeout
- **File:** `auto_a11y/audio/segmenter.py:157-168, 181-192, 216-232` (also `callouts.py:359`)
- **Bug:** `subprocess.run(...)` with no `timeout=`; a corrupt source or ffmpeg hang blocks the worker indefinitely.
- **Fix hint:** Add a generous `timeout=`; translate `TimeoutExpired` into a pipeline error.

### [LOW] JSON:API pagination loops can run forever if the server ignores `page[offset]`
- **File:** `auto_a11y/drupal/issue_importer.py:52-81`, `discovered_page_importer.py:69-88`, `taxonomy.py:202-222, 539-557`
- **Bug:** Terminate only on empty/short pages; a server returning a full first page every time loops forever and accumulates duplicates.
- **Fix hint:** Follow `links.next`, or add a hard offset/iteration cap.

### [LOW] `_fmt_ts` / `_seconds_to_ts` can emit invalid `:60.xxx` seconds
- **File:** `auto_a11y/audio/transcription.py:181-186`, `auto_a11y/audio/vtt_processor.py:21-25`
- **Bug:** `s = seconds % 60` then `f"{s:06.3f}"`; `59.9999` rounds to `60.000` → invalid WebVTT `00:01:60.000`.
- **Fix hint:** Compute integer total-ms first, then derive h/m/s/ms.

### [LOW] `_convert_drupal_page` assumes `field_public_note_on_page` is a dict
- **File:** `auto_a11y/drupal/discovered_page_importer.py:251-252`
- **Bug:** Handled as a single dict (`.get('value')`) while the sibling notes field is handled as a list; Drupal text fields are often list-serialized → `AttributeError` if it's a list.
- **Fix hint:** Handle both dict and list shapes (as `issue_importer.py:142-147` does).

### [LOW] `batch_export` discriminates Page vs DiscoveredPage by `hasattr`
- **File:** `auto_a11y/drupal/discovered_page_exporter.py:219-224`
- **Bug:** Routes by `hasattr(page, 'discovery_reasons')`; a model exposing that attribute unexpectedly is misrouted to the wrong exporter/dedup source.
- **Fix hint:** Use `isinstance` against the actual model classes.

> **Note:** `issue_exporter.py` has many leftover emoji `logger.warning` debug lines (83-85, 110-113, 121, 129, 276-299, 399-401) that log full payloads (incl. possibly-sensitive descriptions) at WARNING on every export — worth removing. No `shell=True` / shell-injection found (all subprocess calls use argv lists; `drawtext` is escaped).

---

## 10. Touchpoint tests

### [MEDIUM] Discovery array silently dropped by result processor (test_page)
- **File:** `auto_a11y/testing/touchpoint_tests/test_page.py:217` (with `result_processor.py:168-174`)
- **Bug:** `test_page` is the only test that puts discovery into `results['discovery']` (`DiscoResponsiveBreakpoints`); the processor only reads `errors`/`warnings`. Other tests smuggle disco into `warnings` with `type:'disco'`.
- **Why:** The responsive-breakpoints discovery finding is computed then thrown away. (Same root cause as the HIGH finding in §4.)
- **Fix hint:** Push the item into `warnings` with `type:'disco'`, or make the processor iterate `discovery`.

### [MEDIUM] `rem` CSS values parse to 0 in `parse_px` helpers
- **File:** `auto_a11y/testing/touchpoint_tests/test_forms.py:1070-1072`, `test_event_handlers.py:883`
- **Bug:** `.replace('em','').replace('rem','')` strips `em` first, so `'2rem'` → `'2r'` → `float('2r')` raises → returns 0. (`test_links.py:120-125` is correct.)
- **Why:** Focus outline/border widths in `rem` treated as 0px → false positives/missed contrast checks.
- **Fix hint:** Replace `rem` before `em`, or regex the numeric part.

### [MEDIUM] Floating-dialogs warnings/passes duplicated and counts inflated across breakpoints
- **File:** `auto_a11y/testing/touchpoint_tests/test_floating_dialogs.py:662-666`
- **Bug:** Errors are deduped across breakpoints (650-660) but `all_warnings`/`all_passes`/element totals are accumulated raw per breakpoint (N×).
- **Why:** Duplicate warnings and inflated tested/pass/fail counts; inconsistent with the error dedup.
- **Fix hint:** Apply the same xpath+code dedup to warnings/passes; count elements once.

### [LOW] Floating-dialogs check `total` uses hardcoded `* 3`
- **File:** `auto_a11y/testing/touchpoint_tests/test_floating_dialogs.py:694`
- **Bug:** `'total': total_elements_tested * 3` while passed/failed are un-multiplied → `passed + failed != total`.
- **Fix hint:** Derive `total` from actual checks or `passed + failed`.

### [LOW] `test_page` inconsistent pass/fail accounting for title warnings
- **File:** `auto_a11y/testing/touchpoint_tests/test_page.py:127, 143`
- **Bug:** `WarnPageTitleTooShort` increments `elements_failed`; `WarnPageTitleTooLong` increments `elements_passed` — opposite buckets for the same single element.
- **Fix hint:** Treat both title warnings consistently.

### [LOW] `test_links` target="_blank" violation neither counts nor blocks a later pass
- **File:** `auto_a11y/testing/touchpoint_tests/test_links.py:540-550`
- **Bug:** `ErrLinkOpensNewWindowNoWarning` appended without `elements_failed++`; falls through to focus logic that may count the same link as passed.
- **Fix hint:** Increment `elements_failed` and avoid double-counting as passed.

### [LOW] `test_links` Space-key regex requires a space between two separate quote pairs
- **File:** `auto_a11y/testing/touchpoint_tests/test_links.py:462`
- **Bug:** `key\s*===?\s*["\'] ["\']` doesn't match the common `e.key === ' '` (single space inside one quote pair).
- **Fix hint:** `key\s*===?\s*(['"]) \1`.

### [LOW] `test_tabindex` `ErrAnchorTargetTabindex` inflates `elements_failed` beyond `elements_tested`
- **File:** `auto_a11y/testing/touchpoint_tests/test_tabindex.py:287`
- **Bug:** `elements_tested` counts `[tabindex]` elements but the in-page-target loop iterates `*[id]` and increments `elements_failed` for targets never in `elements_tested`.
- **Fix hint:** Include in-page targets in `elements_tested`, or use a separate counter.

### [LOW] `test_title_attribute` double-counts titled iframes
- **File:** `auto_a11y/testing/touchpoint_tests/test_title_attribute.py:154`
- **Bug:** `elements_tested = len(elementsWithTitle) + len(allIframes)`; a titled iframe is in both collections.
- **Fix hint:** Dedup the set, or count iframes only once.

### [LOW] Bare `except:` clauses swallow all exceptions in numeric parsers / config read
- **File:** `test_links.py:110,117,124,136,666`; `test_buttons.py:452,460,468,482,531`; `test_page.py:69`
- **Bug:** Bare `except:` (no type) catches `KeyboardInterrupt`/`SystemExit`; `test_page.py:69` wraps an `await page.evaluate(...)` in `except: pass`, hiding browser errors.
- **Fix hint:** `except (ValueError, TypeError):` for parsers; `except Exception:` + log for the evaluate.

### [LOW] `parse_color` falls back to opaque black for 3-digit hex / named colors
- **File:** `test_links.py:49-73` (also `test_buttons.py:386-412`, `test_event_handlers.py:887-908`, `test_forms.py:1075-1082`)
- **Bug:** Only `rgb()/rgba()` and 6-digit hex handled; `#abc`, 8-digit hex, named colors → `{0,0,0,1}` (black), which can flip a contrast result. (Mitigated by `getComputedStyle` usually returning `rgb()`.)
- **Fix hint:** Expand 3-digit hex + named-color lookup, or log unparseable colors.

### [LOW] `validate_language_code` doesn't flag uppercase region-less codes
- **File:** `auto_a11y/testing/touchpoint_tests/test_language.py:133-137`
- **Bug:** Case-correctness check only runs `if '-' in lang_code`; with `re.IGNORECASE`, `lang="EN"` passes silently.
- **Fix hint:** Also verify `parts[0] == parts[0].lower()` in the no-hyphen branch.

> **Note:** Dead/backup files (`test_maps_old.py`, `test_maps_old_backup.py`, `test_maps.py.backup`) are not wired into `__init__.py` and were not audited. Inconsistent `cat:` field values across tests are cosmetic — routing is by error code via `TouchpointMapper`, not `cat`.

---

## Suggested triage order

1. **Web authorization (§1)** — the IDOR cluster is the highest real-world risk; the same `@project_role_required` pattern already exists and just needs to be applied to the legacy blueprints.
2. **`$inc: {}` scheduled-run bug (§6)** — silently turns every successful scheduled run into a recorded failure.
3. **Report XSS + Excel enum crash (§2)** and **AI system-prompt drop / JSON extraction (§5)** — user-facing correctness/security in the product's core output.
4. **Datetime/UTC token handling (§3, §7)** and **`Website.discovery_history` data loss (§3)**.
5. **Dropped discovery/info arrays (§4)**, **Drupal timestamp import crash (§9)**, **PDF nested-MCID text loss (§8)**.
6. The remaining MEDIUM/LOW items as cleanup.

*All findings are read-only observations. None were verified by running the code against live data unless noted; a few rely on documented MongoDB / library semantics. Confirm before fixing where the agent flagged uncertainty.*
