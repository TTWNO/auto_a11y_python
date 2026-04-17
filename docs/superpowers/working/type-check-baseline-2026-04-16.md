# Type-Check Baseline — 2026-04-16

Captured on branch `remove-bootstrap-colours` before any stub or annotation work.
Raw output files: `/tmp/mypy-baseline.txt`, `/tmp/pyright-baseline.txt`, `/tmp/ty-baseline.txt`

## Tool Versions

| Tool    | Version  | Config file           |
|---------|----------|-----------------------|
| mypy    | 1.19.1   | `pyproject.toml [mypy]` |
| pyright | 1.1.408  | `pyproject.toml [tool.pyright]` |
| ty      | 0.0.31   | `pyproject.toml [tool.ty]` |

---

## 1. Summary

| Tool    | Errors  | Warnings | Notes |
|---------|---------|----------|-------|
| mypy    | 3 846   | 0        | `Found 3846 errors in 160 files (checked 179 source files)` |
| pyright | 20 934  | 0        | `20934 errors, 0 warnings, 0 informations` |
| ty      | 1 306   | 0        | `Found 1306 diagnostics` (all flagged as errors) |

### mypy error-code distribution

| Code              | Count | Category |
|-------------------|-------|----------|
| no-untyped-def    | 998   | Phase 3 – annotations |
| attr-defined      | 801   | Phase 2/3 – stubs + annotations |
| no-untyped-call   | 748   | Phase 3 – annotations |
| type-arg          | 225   | Phase 3 – annotations |
| arg-type          | 183   | Real bugs / Phase 3 |
| index             | 162   | Real bugs / Phase 3 |
| assignment        | 151   | Real bugs / Phase 3 |
| call-overload     | 114   | Real bugs |
| operator          | 90    | Real bugs / Phase 3 |
| no-any-return     | 83    | Phase 3 |
| var-annotated     | 81    | Phase 3 |
| union-attr        | 69    | Real bugs / Phase 3 |
| import-untyped    | 35    | Phase 2 – stubs |
| return-value      | 33    | Real bugs |
| import-not-found  | 1     | Phase 2 – stubs |

### pyright error-code distribution

| Code                        | Count  | Category |
|-----------------------------|--------|----------|
| reportUnknownMemberType     | 8 286  | Phase 3 – annotations (noise) |
| reportUnknownVariableType   | 3 889  | Phase 3 – annotations (noise) |
| reportUnknownArgumentType   | 3 166  | Phase 3 – annotations (noise) |
| reportUnknownParameterType  | 1 134  | Phase 3 – annotations |
| reportAttributeAccessIssue  | 1 099  | Real bugs / Phase 3 |
| reportMissingParameterType  | 850    | Phase 3 – annotations |
| reportArgumentType          | 667    | Real bugs |
| reportIndexIssue            | 318    | Real bugs |
| reportMissingTypeArgument   | 230    | Phase 3 |
| reportUnusedImport          | 199    | Cleanup |
| reportCallIssue             | 174    | Real bugs |
| reportPrivateUsage          | 130    | Real bugs |
| reportUnknownLambdaType     | 129    | Phase 3 |
| reportOperatorIssue         | 105    | Real bugs |
| reportUnusedVariable        | 87     | Cleanup |
| reportOptionalMemberAccess  | 76     | Real bugs |
| reportInvalidStringEscapeSequence | 58 | Cleanup |
| reportImplicitOverride      | 55     | Real bugs |
| reportOptionalSubscript     | 42     | Real bugs |
| reportReturnType            | 32     | Real bugs |
| reportMissingTypeStubs      | 28     | Phase 2 – stubs |
| reportMissingImports        | 1      | Phase 2 – stubs |

### ty error-code distribution

| Code                      | Count | Category |
|---------------------------|-------|----------|
| unresolved-attribute      | 720   | Phase 2/3 (mostly stubs cascade) |
| invalid-argument-type     | 266   | Real bugs / Phase 3 |
| invalid-assignment        | 106   | Real bugs / Phase 3 |
| str                       | 52    | Phase 3 |
| invalid-parameter-default | 52    | Real bugs |
| possibly-unresolved-reference | 30 | Phase 3 |
| invalid-return-type       | 28    | Real bugs |
| unsupported-operator      | 25    | Real bugs |
| not-subscriptable         | 21    | Real bugs |
| unresolved-reference      | 10    | Phase 3 |
| unresolved-import         | 9     | Phase 2 – stubs |
| no-matching-overload      | 8     | Real bugs |
| invalid-method-override   | 8     | Real bugs |
| not-iterable              | 5     | Real bugs |
| deprecated                | 5     | Cleanup |
| division-by-zero          | 3     | Real bugs |
| unknown-argument          | 3     | Real bugs |
| missing-argument          | 2     | Real bugs |
| invalid-await             | 1     | Real bugs |

---

## 2. Missing-Stubs Errors (Phase 2 Target)

Every third-party package that generates a missing-stubs / untyped-import error. One row per distinct top-level package.

| Package | mypy code(s) | pyright code(s) | ty code(s) | Has py.typed | types-* on PyPI | Recommended Action |
|---------|-------------|----------------|-----------|-------------|----------------|-------------------|
| ~~`requests`~~ | import-untyped | — | — | No | `types-requests` 2.33.0 ✓ | `pip install types-requests` |
| ~~`openpyxl`~~ | import-untyped | — | — | No | `types-openpyxl` 3.1.5 ✓ | `pip install types-openpyxl` |
| ~~`flask-cors`~~ | import-untyped | — | — | No | `types-flask-cors` 6.0.0 ✓ | `pip install types-flask-cors` |
| ~~`flask-login`~~ | import-untyped | reportMissingTypeStubs | — | No | None on PyPI | ~~Write local stub in `stubs/flask_login/`~~ Done |
| ~~`flask-wtf`~~ | import-untyped | reportMissingTypeStubs | — | No | None on PyPI | ~~Write local stub in `stubs/flask_wtf/`~~ Done |
| ~~`apscheduler`~~ | import-untyped | reportMissingTypeStubs | — | No | None on PyPI | ~~Write local stub in `stubs/apscheduler/`~~ Done |
| ~~`msal`~~ | import-untyped | reportMissingTypeStubs | — | No | None on PyPI | ~~Write local stub in `stubs/msal/`~~ Done |
| ~~`nest-asyncio`~~ | import-untyped | reportMissingTypeStubs | — | No | None on PyPI | ~~Write local stub in `stubs/nest_asyncio/`~~ Done |
| ~~`weasyprint`~~ | import-untyped | reportMissingTypeStubs | — | No | None on PyPI | ~~Write local stub in `stubs/weasyprint/`~~ Done |
| ~~`fluent-compiler`~~ | import-untyped | reportMissingTypeStubs | — | No | None on PyPI | ~~Write local stub in `stubs/fluent_compiler/`~~ Done |
| ~~`google-auth-oauthlib`~~ | import-untyped | reportMissingTypeStubs | — | No | `google-auth-oauthlib-stubs` 1.2.0 ✓ | ~~`pip install google-auth-oauthlib-stubs`~~ Done (supplemented with local `flow.pyi`) |
| `PyPDF2` | import-not-found | reportMissingImports | unresolved-import | Not installed | None on PyPI | Add to requirements or guard import with `TYPE_CHECKING` |
| `polib` | — | — | unresolved-import | Not installed | Not checked | Used only in one-off `translate_*.py` scripts; add `# type: ignore` or install |

**Notes:**
- ~~`flask-login`, `flask-wtf`, `apscheduler`, `msal`, `nest-asyncio`, `weasyprint`, `fluent-compiler` all require hand-written local stubs (no upstream types-* package exists).~~ All local stubs written.
- ~~`google-auth-oauthlib-stubs` is available but not yet installed.~~ Installed; supplemented with local `flow.pyi` stub.
- `PyPDF2` appears to be unused/optional; the import in `scraper.py:1090` is inside a try block. Consider removing or guarding.
- `polib` is used only in legacy `translate_*.py` root scripts not part of the production package.

**mypy import-untyped errors by package (total occurrences):**
- `flask-login`: 10 occurrences (most routes)
- `openpyxl`: 5 occurrences
- `requests`: 3 occurrences
- `apscheduler`: 6 occurrences
- `weasyprint`: 3 occurrences
- `flask-cors`: 1 occurrence
- `flask-wtf.csrf`: 1 occurrence
- `msal`: 1 occurrence
- `google-auth-oauthlib.flow`: 1 occurrence
- `nest-asyncio`: 1 occurrence
- `fluent-compiler.bundle`: 1 occurrence

---

## 3. Missing-Annotations Errors (Phase 3 Target)

### 3a. Mypy — errors per file (all error codes, top 30)

| File | mypy errors |
|------|-------------|
| `auto_a11y/reporting/static_html_generator.py` | 632 |
| `auto_a11y/reporting/formatters.py` | 226 |
| `auto_a11y/web/routes/reports.py` | 170 |
| `auto_a11y/reporting/discovery_report.py` | 150 |
| `tests/test_static_html_streaming.py` | 135 |
| `auto_a11y/web/routes/auth.py` | 130 |
| `tests/test_formatter_streaming.py` | 118 |
| `tests/test_streaming_reports.py` | 111 |
| `auto_a11y/web/routes/testing.py` | 108 |
| `auto_a11y/web/routes/api.py` | 107 |
| `auto_a11y/core/database.py` | 87 |
| `auto_a11y/testing/touchpoint_tests/test_language.py` | 84 |
| `tests/test_database_generators.py` | 80 |
| `auto_a11y/ai/claude_client.py` | 80 |
| `auto_a11y/web/routes/websites.py` | 75 |
| `auto_a11y/reporting/report_generator.py` | 68 |
| `auto_a11y/web/routes/projects.py` | 66 |
| `auto_a11y/web/app.py` | 62 |
| `auto_a11y/web/routes/pages.py` | 60 |
| `auto_a11y/web/routes/public.py` | 57 |
| `auto_a11y/web/routes/drupal_sync.py` | 53 |
| `auto_a11y/testing/touchpoint_tests/test_forms.py` | 53 |
| `auto_a11y/testing/test_runner.py` | 51 |
| `auto_a11y/web/routes/schedules.py` | 49 |
| `tests/test_report_template_rendering.py` | 48 |
| `auto_a11y/core/scraper.py` | 46 |
| `auto_a11y/web/routes/scripts.py` | 45 |
| `auto_a11y/drupal/taxonomy.py` | 44 |
| `auto_a11y/web/routes/recordings.py` | 43 |
| `auto_a11y/web/routes/automated_tests.py` | 42 |

### 3b. Mypy — module-group aggregate (annotation work estimate)

| Module group | mypy errors | Primary cause |
|-------------|-------------|---------------|
| `auto_a11y/web/` | 1 258 | Unannotated route functions, untyped calls |
| `auto_a11y/reporting/` | 1 240 | Unannotated helpers, dict/list misuse |
| `auto_a11y/testing/` | 411 | Unannotated touchpoint functions |
| `auto_a11y/core/` | 354 | Untyped database/browser helpers |
| `tests/` | 675 | Unannotated test functions |
| `auto_a11y/models/` | 159 | `str | None` vs `str` in `.id`/`get_id()` |
| `auto_a11y/ai/` | 132 | Claude API streaming types |
| `auto_a11y/drupal/` | 110 | Unannotated client functions |
| `auto_a11y/parsers/` | 31 | Minor |
| Root scripts (`run.py`, `test_fixtures.py`, etc.) | 50 | Minor |

**Total annotation-producing files:** 132

### 3c. Pyright — errors per file (all codes, top 20)

| File | pyright errors |
|------|----------------|
| `auto_a11y/reporting/formatters.py` | 1 943 |
| `auto_a11y/reporting/static_html_generator.py` | 1 669 |
| `auto_a11y/reporting/discovery_report.py` | 1 159 |
| `auto_a11y/core/database.py` | 886 |
| `auto_a11y/web/routes/testing.py` | 880 |
| `auto_a11y/web/routes/reports.py` | 697 |
| `auto_a11y/web/routes/auth.py` | 507 |
| `auto_a11y/web/routes/api.py` | 495 |
| `auto_a11y/web/routes/drupal_sync.py` | 426 |
| `auto_a11y/web/routes/pages.py` | 416 |
| `auto_a11y/web/routes/websites.py` | 390 |
| `auto_a11y/web/routes/projects.py` | 355 |
| `tests/test_static_html_streaming.py` | 310 |
| `auto_a11y/web/routes/automated_tests.py` | 256 |
| `auto_a11y/web/app.py` | 248 |
| `test_fixtures.py` | 245 |
| `auto_a11y/web/routes/recordings.py` | 239 |
| `auto_a11y/reporting/recordings_report.py` | 236 |
| `auto_a11y/web/routes/public.py` | 232 |
| `auto_a11y/testing/touchpoint_tests/test_forms.py` | 232 |

Note: pyright's high counts are dominated by `reportUnknownMemberType` / `reportUnknownVariableType` cascade noise from unannotated functions. These will largely vanish once Phase 3 annotations are added.

### 3d. ty — errors per file (top 20)

| File | ty errors |
|------|-----------|
| `auto_a11y/ai/claude_client.py` | 78 |
| `auto_a11y/web/routes/reports.py` | 73 |
| `auto_a11y/core/browser_manager.py` | 72 |
| `auto_a11y/web/routes/websites.py` | 57 |
| `auto_a11y/web/routes/auth.py` | 56 |
| `auto_a11y/web/routes/api.py` | 54 |
| `auto_a11y/reporting/project_report.py` | 54 |
| `auto_a11y/web/routes/projects.py` | 44 |
| `auto_a11y/web/routes/testing.py` | 41 |
| `auto_a11y/core/scheduler.py` | 38 |
| `auto_a11y/web/routes/pages.py` | 37 |
| `auto_a11y/web/routes/scripts.py` | 34 |
| `auto_a11y/web/routes/schedules.py` | 30 |
| `auto_a11y/web/routes/recordings.py` | 30 |
| `auto_a11y/web/routes/public.py` | 30 |
| `auto_a11y/web/app.py` | 29 |
| `auto_a11y/web/routes/website_users.py` | 25 |
| `auto_a11y/drupal/recording_exporter.py` | 22 |
| `auto_a11y/core/scraper.py` | 22 |
| `auto_a11y/web/routes/project_users.py` | 21 |

---

## 4. Real Type Bugs

These are genuine code correctness problems discovered — not annotation noise. All three tools either agree, or at least one flags something worth fixing.

### 4a. `str | None` returned where `str` is declared (nullability violations)

All three tools flag this. Affects every model class that wraps a MongoDB `_id` field.

| Files | Pattern | Impact |
|-------|---------|--------|
| `auto_a11y/models/app_user.py:51,60` | `.id` and `get_id()` return `str | None` declared as `str` | Flask-Login may break if `get_id()` returns `None` |
| `auto_a11y/models/page.py:73` | `.id` returns `str | None` | |
| `auto_a11y/models/project.py:42,81` | `.id` and others | |
| `auto_a11y/models/website.py:62` | `.id` | |
| `auto_a11y/models/discovered_page.py:67` | `.id` | |
| `auto_a11y/models/document_reference.py:71` | `.id` | |
| `auto_a11y/models/discovery_run.py:61` | `.id` | |
| `auto_a11y/models/schedule.py:162` | `.id` | |
| `auto_a11y/models/share_token.py:41` | `.id` | |
| `auto_a11y/models/test_result.py:227` | `.id` | |
| `auto_a11y/models/test_state_matrix.py:134` | `.id` | |
| `auto_a11y/models/website_user.py:115` | `.id` | |
| `auto_a11y/models/page_setup_script.py:188,402` | `.id` | |
| `auto_a11y/models/project_user.py:115` | `.id` | |

**Fix:** Change return type to `str | None` (or `Optional[str]`) — these are MongoDB ObjectId fields that may legitimately be absent before a document is persisted.

### 4b. Missing required arguments at call sites (confirmed by mypy + pyright + ty)

| File:line | Issue |
|-----------|-------|
| `auto_a11y/reporting/recordings_report.py:458` | `self._generate_html_report()` called with no args; requires `include_summary`, `include_timecodes`, `include_wcag`, `group_by_touchpoint` (all three tools agree) |
| `auto_a11y/testing/script_session_manager.py:215` | `Violation()` called with wrong kwargs `message`, `selector`, `context` — not in the `Violation` constructor; mypy also flags wrong `impact` type (`str` vs `ImpactLevel`) |

### 4c. `str = None` parameter defaults (implicit Optional violation)

`auto_a11y/ai/analysis_modules.py:118-119` — `generate_xpath()` annotates parameters as `str` and `int` but defaults to `None`. All three tools flag this (mypy: `[assignment]`, pyright: implicit in unknown-type cascade, ty: `[invalid-parameter-default]`). Fix: change annotations to `str | None` and `int | None`.

**Fix:** Change parameter annotations to `Optional[str]` / `Optional[int]`.

### 4d. Division-by-zero false positive (ty-only)

ty flags three sites where code is guarded by `if total > 0` but ty cannot see the guard narrows the literal type away from `0`. Pyright and mypy do not flag these.

| File:line | Note |
|-----------|------|
| `auto_a11y/reporting/static_html_generator.py:1843` | `total_tests > 0` guard present |
| `auto_a11y/web/routes/pages.py:208` | `total_tests > 0` guard present |
| `auto_a11y/web/routes/testing.py:44` | `total_pages > 0` guard present |

These are ty false positives — the guards are correct. May be resolved in a future ty release or by extracting to a local variable.

### 4e. List subscripted with string key (genuine bug)

`auto_a11y/reporting/static_html_generator.py:957–962` — code does `item['title_en'] = ...` but `item` may be `list[str]`. This is a genuine type union problem (all three tools flag it). The variable comes from a function returning `list[str] | dict[str, Any] | ImpactLevel | str | None`.

**Fix:** Narrow the type with an `isinstance(item, dict)` check.

### 4f. asyncio.gather return type mismatch

`auto_a11y/ai/claude_analyzer.py:426` and `auto_a11y/ai/claude_client.py:470` — `asyncio.gather(*tasks, return_exceptions=True)` returns `list[Unknown | BaseException]` but the declared return type is `list[dict[str, Any]]`. If any task raises, the caller receives a `BaseException` object where a dict is expected — a real runtime risk.

**Fix:** Declare return as `list[dict[str, Any] | BaseException]` or handle exceptions before returning.

### 4g. `DrupalConfig` constructed with `str | None` for required str fields

`auto_a11y/drupal/config.py:53–55, 111–113` — `base_url`, `username`, `password` passed as `str | None` where `str` is required. If these come back `None` from config, Drupal calls will fail at runtime with `AttributeError`.

**Fix:** Add null guards or make `DrupalConfig` fields accept `str | None`.

### 4h. `invalid-await` on `_AsyncGeneratorContextManager`

`auto_a11y/core/scraping_job.py:286` (ty only) — `await scraper.browser_manager.get_page()` but `get_page()` appears to be an async context manager, not a coroutine. If the caller is using `await` instead of `async with`, the context manager will not be properly entered/exited.

### 4i. `touchpoints.get_touchpoint_by_id()` may return `None`

`auto_a11y/core/touchpoints.py:668` — declared return `Touchpoint` but `dict.get()` can return `None`. All three tools flag this. Callers that don't check for `None` may crash.

**Fix:** Return `Touchpoint | None` or raise `KeyError` on unknown IDs.

---

## 5. Tool Disagreements

### 5a. Division-by-zero

Only **ty** flags the `/ 0` pattern when guarded by `> 0` (3 sites). Both mypy and pyright are silent. This is a ty false positive (narrow literal analysis). Track but do not fix aggressively.

### 5b. `invalid-parameter-default` (`str = None`)

- **mypy** reports as `[assignment]` with a note about `no_implicit_optional`
- **ty** reports as `[invalid-parameter-default]`
- **pyright** is silent (treats the implicit Optional permissively)

All are correct — pyright is just more lenient here by default.

### 5c. `reportMissingTypeStubs` vs `import-untyped`

- **pyright** flags `flask-login`, `flask-wtf.csrf`, `apscheduler`, `msal`, `nest-asyncio`, `weasyprint`, `fluent-compiler` as `reportMissingTypeStubs`
- **mypy** flags the same as `import-untyped`
- **ty** does not flag these at all (treats missing stubs as silent unknowns)

All agree on the root cause; ty is least noisy about it.

### 5d. `requests`, `openpyxl`, `flask-cors` — mypy only

- **mypy** flags `requests`, `openpyxl`, `flask-cors` as `import-untyped`
- **pyright** does NOT flag these (likely because pyright uses bundled stubs or finds partial type info)
- **ty** does not flag these

This means after installing `types-requests`, `types-openpyxl`, `types-flask-cors`, mypy will improve but pyright counts remain unchanged (already resolved internally).

### 5e. `reportUnusedImport` — pyright only

pyright flags 199 unused imports across the codebase. mypy and ty are silent on these. These are cleanup targets, not type safety issues.

### 5f. `recordings_report.py:458` — all three agree

All three tools independently catch the missing required arguments bug at `recordings_report.py:458`. This is a confirmed genuine bug.

### 5g. `asyncio.gather` return type

- **ty** flags `invalid-return-type` at `claude_analyzer.py:426` and `claude_client.py:470`
- **mypy** does not flag (likely because `asyncio.gather` with `return_exceptions=True` has complex overload handling)
- **pyright** does not flag

ty is correct here — this is a real latent bug.

---

## Appendix: Stubs Packages Summary for Phase 2

| Package to stub | pip install candidate | Source |
|----------------|----------------------|--------|
| ~~`requests`~~ | `types-requests` | typeshed / PyPI ✓ |
| ~~`openpyxl`~~ | `types-openpyxl` | typeshed / PyPI ✓ |
| ~~`flask-cors`~~ | `types-flask-cors` | typeshed / PyPI ✓ |
| ~~`google-auth-oauthlib`~~ | `google-auth-oauthlib-stubs` | PyPI ✓ |
| ~~`flask-login`~~ | ~~hand-write `stubs/flask_login/`~~ | Done |
| ~~`flask-wtf`~~ | ~~hand-write `stubs/flask_wtf/`~~ | Done |
| ~~`apscheduler`~~ | ~~hand-write `stubs/apscheduler/`~~ | Done |
| ~~`msal`~~ | ~~hand-write `stubs/msal/`~~ | Done |
| ~~`nest-asyncio`~~ | ~~hand-write `stubs/nest_asyncio/`~~ | Done |
| ~~`weasyprint`~~ | ~~hand-write `stubs/weasyprint/`~~ | Done |
| ~~`fluent-compiler`~~ | ~~hand-write `stubs/fluent_compiler/`~~ | Done |
