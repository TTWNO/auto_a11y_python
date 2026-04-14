# Proposal: Migration from gettext/Flask-Babel to Project Fluent

**Date:** 2026-04-10
**Status:** Approved (Option C — Full Migration)
**Audience:** Development team

---

## Table of Contents

1. [Current Translation System](#1-current-translation-system)
2. [Why Fluent](#2-why-fluent)
3. [Fluent Overview](#3-fluent-overview)
4. [Migration Scope](#4-migration-scope)
5. [Implementation Plan](#5-implementation-plan)
6. [Risks and Mitigations](#6-risks-and-mitigations)
7. [What Stays the Same](#7-what-stays-the-same)

---

## 1. Current Translation System

The project uses a three-layer translation system spanning ~50,000 lines of translation files.

### Layer 1: Flask-Babel (gettext)

The primary system for all UI strings — templates, Python code, and JavaScript (via workaround).

| Metric | Count |
|--------|-------|
| Total messages in `.po` catalog | ~2,700 |
| `{{ _('...') }}` calls in Jinja2 templates | ~4,000 |
| `gettext` / `lazy_gettext` / `ngettext` / `pgettext` usages in Python | ~145 |
| Translation file (`.po`) | 45,944 lines |

**File locations:**
- `babel.cfg` — extraction config
- `auto_a11y/web/translations/messages.pot` — extracted message template
- `auto_a11y/web/translations/fr/LC_MESSAGES/messages.po` — French translations
- `auto_a11y/web/translations/fr/LC_MESSAGES/messages.mo` — compiled binary (gitignored)

**How it works:**
1. `pybabel extract` scans `.py` and `.html` files for `_()` calls → produces `messages.pot`
2. `pybabel update` merges new strings into `messages.po`
3. `pybabel compile` (or runtime compilation in `run.py`) converts `.po` → `.mo` binary
4. Flask-Babel loads `.mo` at runtime and resolves `_()` calls to French strings
5. A custom `_escaped_gettext()` and `_escaped_ngettext()` wrapper in `app.py` applies `markupsafe.escape()` to all translations to prevent French apostrophes from breaking HTML/JS
6. `force_locale` context manager used in 9+ reporting modules to force a specific locale during report generation (e.g., generating a French PDF regardless of the current request's locale)
7. `pgettext` used in `static_html_generator.py` for contextual translations
8. `format_datetime` Jinja2 filter (11 usages across 5 templates) provides locale-aware date formatting via Flask-Babel

### Layer 2: Issue Descriptions (JSON)

Separate from gettext because these are dynamic content loaded at runtime, not static UI strings.

| Metric | Count |
|--------|-------|
| Issue codes translated | ~600+ |
| Fields per issue | 5 (title, what, why, who, remediation) |
| File size | ~3,500 lines |

**File:** `auto_a11y/reporting/issue_translations_fr.json`

### Layer 3: Typst + WCAG Dict

PDF report translations and WCAG criterion labels. Each has its own format.

| File | Format | Lines |
|------|--------|-------|
| `auto_a11y/reporting/typst_templates/lib/i18n.typ` | Typst dict | 280 |
| `auto_a11y/reporting/wcag_translations_fr.py` | Python dict | 102 |

### Known Pain Points

1. **`.mo` compilation step.** Forgetting to compile `.po` → `.mo` results in blank translations at runtime. Currently mitigated by runtime compilation in `run.py`, but this adds startup complexity and requires Babel to be installed.

2. **Fuzzy marker footgun.** When `pybabel update` detects a changed source string, it marks the translation `#, fuzzy`. Fuzzy messages silently display English instead of French. This broke a live French demo. Currently mitigated by a custom validation script (`scripts/validate_translations.py`) that rejects fuzzy entries.

3. **JavaScript translations require a workaround.** Standalone `.js` files cannot use Jinja2 `{{ _() }}` syntax. Instead, templates build `window.i18n` objects that pass translated strings to JS. This creates two sources of truth and risks drift.

4. **French apostrophe escaping.** French text contains apostrophes (`l'aide`, `d'attente`) that break HTML attributes and JS string literals. Required a custom `_escaped_gettext()` wrapper in `app.py` that applies `markupsafe.escape()` to every translation.

5. **Three separate translation formats.** gettext `.po` for UI, JSON for issue descriptions, Python dict for WCAG labels, Typst dict for PDF reports. No shared tooling or validation across them.

6. **Dynamic strings hardcoded separately.** Impact levels (Critical, High, Medium, Low) and similar runtime-generated strings are maintained in a separate `dynamic_translations` dict in `app.py` because `pybabel` marks them obsolete if they don't appear literally in source.

---

## 2. Why Fluent

The migration is evaluated against three project goals: accuracy, reliability, and portability.

### a) Accuracy

| Criterion | gettext (current) | Fluent |
|-----------|-------------------|--------|
| Plural handling | `ngettext()` — basic | Full CLDR selectors |
| Gender agreement | Not supported | First-class via term attributes |
| Translation invalidation | Any English edit → fuzzy (over-invalidates) | Only ID renames invalidate (precise) |
| Translator context | Bare source string, must grep for context | Comments + attributes group related strings |

Fluent's gender support and granular invalidation reduce inaccuracy risk. For EN/FR with primarily UI strings, the gains are incremental but real — particularly for French grammatical agreement.

### b) Reliability (No Bugs)

| Criterion | gettext (current) | Fluent |
|-----------|-------------------|--------|
| Binary compilation step | Required (`.po` → `.mo`), forgetting = blank translations | None — `.ftl` files read directly |
| Fuzzy markers | Silent failure, required custom validation | No fuzzy concept — messages exist or don't |
| HTML escaping | Custom `_escaped_gettext()` wrapper | Built-in `Escaper` system with MarkupSafe integration |
| Compile-time checks | None | Static analysis: infinite recursion, type errors, unknown functions |
| JS/Python format | Separate mechanisms, risk of drift | **Same `.ftl` files** shared between Python and JS |

Fluent eliminates three known bug sources: the `.mo` compilation step, the fuzzy marker footgun, and the JS/Python translation format split. The built-in escaper replaces custom code.

### c) Portability (No C Dependencies)

| Criterion | gettext (current) | Fluent |
|-----------|-------------------|--------|
| C dependencies | Pure Python at runtime, but `pybabel` CLI and system `gettext` tools (`msgfmt`) sometimes needed | **100% pure Python** — no C extensions, no system tools |
| Binary format | `.mo` requires compilation tools | `.ftl` is plain text |
| Desktop/Electron | `.mo` must be compiled before or during packaging | `.ftl` files ship as-is |

Fluent's pure-text `.ftl` format and pure-Python runtime mean no system-level dependencies anywhere in the chain. This is particularly valuable for the Electron desktop distribution.

---

## 3. Fluent Overview

[Project Fluent](https://projectfluent.org/) is Mozilla's localization system, created to replace gettext in Firefox. It uses `.ftl` (Fluent Translation List) files.

### FTL Syntax

```ftl
# Simple message
welcome = Welcome to Auto A11y

# Variables
page-count = { $count } pages tested

# Plural selectors (CLDR rules)
issues-found =
    { $count ->
        [one] { $count } issue found
       *[other] { $count } issues found
    }

# Attributes — group related strings for one UI element
search-input = Search pages
    .placeholder = Enter URL or page name
    .aria-label = Search accessibility test results

# Terms — reusable, private vocabulary
-app-name = Auto A11y

about = About { -app-name }.

# French grammatical gender via term attributes
-report = rapport
    .gender = masculine

report-generated =
    { -report.gender ->
        [masculine] Le { -report } a été généré.
        [feminine] La { -report } a été générée.
       *[other] Génération terminée.
    }

# Built-in formatting functions
score = Your score: { NUMBER($points, minimumFractionDigits: 2) }
```

**Note:** The gender agreement example above demonstrates Fluent's capability, but in practice the vast majority of messages in this project will be simple key-value pairs or messages with basic variable substitution. Complex selector patterns will be the exception, not the norm.

### Python Library: `fluent-compiler`

Two Python implementations exist. **`fluent-compiler` is the one to use.**

| | `fluent.runtime` (official) | `fluent-compiler` (recommended) |
|---|---|---|
| PyPI | `fluent.runtime` v0.4.0 | `fluent-compiler` v1.1 |
| Status | Alpha (3 - Alpha) | Feature-complete, mature |
| Approach | Interprets AST at runtime | Compiles FTL to Python bytecode |
| HTML escaping | None | Built-in `Escaper` system |
| Error detection | Runtime only | Compile-time static analysis |
| Speed | Slow (parser described as "painstakingly slow") | Fast (bytecode compilation, like Jinja2/Mako) |
| Maintenance | Low activity, alpha quality | Stable, no planned breaking changes |
| License | Apache 2.0 | Apache 2.0 |

**Dependencies:** `fluent-compiler` requires `babel`, `fluent-syntax`, `pytz`, and `typing-extensions` — all pure Python.

### JavaScript Library: `@fluent/bundle`

The `@fluent/bundle` npm package (~35K weekly downloads) provides a JS runtime that reads the same `.ftl` files. This means **Python and JavaScript share one set of translation files** — eliminating the `window.i18n` workaround entirely.

---

## 4. Migration Scope

### Component-by-Component Mapping

| Component | Current | After Migration | Effort |
|-----------|---------|-----------------|--------|
| ~4,000 template `{{ _('...') }}` calls | `{{ _('Text') }}` | `{{ ftl('message-id') }}` or `{{ ftl('message-id', var=val) }}` | **High** |
| ~145 Python `gettext`/`lazy_gettext`/`ngettext`/`pgettext` calls | `_('Text')`, `ngettext(...)`, `pgettext(...)` | `ftl('message-id')` with selectors for plurals | Medium |
| `force_locale` in 9+ reporting modules | `with force_locale('fr'):` | Custom Fluent locale override context manager | Medium |
| `format_datetime` Jinja2 filter (11 usages) | Flask-Babel `format_datetime()` | `babel.dates.format_datetime()` directly (Babel is still a dependency of fluent-compiler) | Low |
| `jinja2.ext.i18n` in standalone Jinja2 environments | `install_gettext_callables()` in `static_html_generator.py` | Register `ftl()` as a Jinja2 global in standalone environments | Low |
| `window.i18n` JS translation pattern | Dict built in template, consumed in `.js` | Load shared `.ftl` file via `@fluent/bundle` | Medium |
| `messages.po` (45,944 lines) | gettext `.po` format | Multiple `.ftl` files organized by feature area | **High** |
| `issue_translations_fr.json` | Custom JSON (5 fields per issue) | `.ftl` with attributes per issue code | Medium |
| `wcag_translations_fr.py` | Python dict | `.ftl` file | Low |
| `babel.cfg` + `pybabel` extraction workflow | Automatic extraction from templates/code | Manual `.ftl` management or FTL-Extract (Python only) | Ongoing workflow change |
| `app.py` Babel initialization (~30 lines) | `Babel(app, locale_selector=...)` | Custom Flask-Fluent integration (~50-100 lines) | One-time |
| `_escaped_gettext()` wrapper | Custom escape function in `app.py` | `fluent-compiler` built-in `Escaper` with MarkupSafe | Simplification |
| `scripts/validate_translations.py` | Checks `.po` for fuzzy/empty entries | Rewrite to validate `.ftl` message coverage | Medium |
| CI workflow (`.github/workflows/ci.yml`) | `pybabel compile` + validate | Validate `.ftl` coverage (no compilation step) | Simplification |
| `i18n.typ` (Typst PDF reports) | Typst dict | **No change** — Typst has no Fluent support | None |

### Message ID Strategy

Every `_('English text')` call becomes `ftl('message-id')`. IDs should be:
- Kebab-case: `page-list-empty-state`
- Scoped by feature: `auth-login-button`, `report-export-title`
- Descriptive enough to understand without reading the English value

The `.ftl` files should be organized by feature area to keep files manageable:

```
auto_a11y/web/translations/
├── en/
│   ├── auth.ftl
│   ├── common.ftl
│   ├── dashboard.ftl
│   ├── fixtures.ftl
│   ├── pages.ftl
│   ├── projects.ftl
│   ├── reports.ftl
│   ├── settings.ftl
│   ├── testing.ftl
│   └── issues.ftl        (migrated from issue_translations_fr.json)
├── fr/
│   ├── auth.ftl
│   ├── common.ftl
│   ├── ... (mirrors en/)
```

### Effort Estimate

| Task | Estimate |
|------|----------|
| Invent ~2,700 message IDs and write `.ftl` files (EN + FR) | 2-3 days |
| Update ~4,150 call sites in templates and Python | 3-4 days (partially automatable) |
| Write Flask-Fluent integration layer | 1 day |
| Migrate JS translations to `@fluent/bundle` | 1-2 days |
| Migrate issue/WCAG translations to `.ftl` | 1-2 days |
| Rewrite validation script and CI | 1 day |
| Testing, fixing regressions, QA | 2-3 days |
| **Total** | **~2-3 weeks** |

---

## 5. Implementation Plan

### Phase 1: Foundation (Days 1-2)

**Goal:** Flask-Fluent integration layer working with a proof-of-concept page.

1. Install `fluent-compiler` and `@fluent/bundle`
2. Write the Flask-Fluent integration module (`auto_a11y/web/fluent.py`):
   - `FluentBundle` loader that reads `.ftl` files per locale
   - Jinja2 global `ftl()` function (replaces `_()`)
   - MarkupSafe `Escaper` integration (replaces `_escaped_gettext()` and `_escaped_ngettext()`)
   - Locale selection (reuse existing `get_locale()` logic)
   - Fallback behavior: missing French message → English message → message ID
   - **`force_locale` context manager** — temporarily overrides which `FluentBundle` the `ftl()` function resolves against. Required by 9+ reporting modules that generate reports in a specific locale regardless of the current request. Implementation: a thread-local or context-var that `ftl()` checks before falling back to the request locale.
   - **`lazy_ftl()` function** — returns a lazy proxy that resolves the message at render time, not import time. Required for `login_manager.login_message` and touchpoint label dicts that are defined at module scope. Implementation: return a proxy object whose `__str__` calls `ftl()` in the current request context.
   - **`format_datetime` replacement** — register a Jinja2 filter that calls `babel.dates.format_datetime()` directly (Babel remains a dependency via fluent-compiler). This preserves locale-aware date formatting without Flask-Babel.
3. Create initial `.ftl` files for one feature area (e.g., `auth.ftl`)
4. Convert one template (e.g., login page) from `{{ _() }}` to `{{ ftl() }}`
5. Verify: page renders correctly in both EN and FR

**Exit criteria:** One page fully working with Fluent, both languages, escaping correct.

### Phase 2: Bulk UI Migration (Days 3-8)

**Goal:** All template and Python `_()` calls migrated to `ftl()`.

1. Write a migration script that:
   - Extracts all `_('...')` calls from templates and Python
   - Generates kebab-case message IDs from the English text
   - Produces `.ftl` files (EN) with the English values
   - Produces `.ftl` files (FR) by looking up the French translation from `messages.po`
   - Rewrites template/Python files to use `ftl('generated-id')`
2. Run the script, then manually review and fix:
   - ID naming (automated IDs may be awkward)
   - Parameterized strings (`_('Hello %s')` → `ftl('greeting', name=user)`)
   - **`ngettext` → Fluent selectors:** Convert `ngettext('%(num)d item', '%(num)d items', count)` to Fluent's `{ $count -> [one] ... *[other] ... }` pattern
   - **`pgettext` → distinct message IDs:** `pgettext('report', 'Title')` becomes `report-title` (Fluent IDs inherently provide context, so `pgettext` is a simplification, not a complication)
   - **`lazy_gettext` → `lazy_ftl()`:** Replace `lazy_gettext('...')` with `lazy_ftl('message-id')` from the integration layer. Used for `login_manager.login_message` and module-level touchpoint label dicts in `projects.py` and `pages.py`
   - Multi-line `.po` entries (774 continuation lines) — ensure the migration script correctly concatenates these
3. Update `static_html_generator.py` standalone Jinja2 environment:
   - Replace `jinja2.ext.i18n` / `install_gettext_callables()` with registering `ftl()` as a Jinja2 global
   - Ensure `force_locale` works outside Flask request context for report generation
4. Remove Flask-Babel initialization from `app.py`
5. Remove `_escaped_gettext()` and `_escaped_ngettext()` wrappers
6. Verify: full app renders correctly in both languages

**Dual-system operation during Phase 2:** Both `_()` (Flask-Babel) and `ftl()` (Fluent) can coexist as Jinja2 globals since they have different function names. Migrate one feature area at a time, test it, then move to the next. Flask-Babel continues to serve unmigrated pages until all call sites are converted.

**Exit criteria:** Zero `_()` calls remain in templates/Python. All pages render correctly.

### Phase 3: JavaScript Migration (Days 9-10)

**Goal:** JS translations loaded from shared `.ftl` files instead of `window.i18n`.

1. Add `@fluent/bundle` to the frontend build (or load via CDN/vendored file)
2. Create a JS helper module that:
   - Fetches the correct locale's `.ftl` file(s)
   - Exposes a `ftl('message-id', args)` function
3. Replace all `window.i18n.key` references in standalone `.js` files with `ftl()` calls
4. Remove `window.i18n` / `window.translations` objects from templates
5. Remove `dynamic_translations` dict from `app.py`
6. Verify: all JS-driven UI text renders correctly in both languages

**Exit criteria:** No `window.i18n` pattern remains. JS and Python share `.ftl` files.

### Phase 4: Issue and WCAG Translations (Days 11-12)

**Goal:** Consolidate issue descriptions and WCAG labels into `.ftl` format.

1. Write a conversion script for `issue_translations_fr.json` → `issues.ftl`:
   ```ftl
   AI_ErrDialogWithoutARIA-title = The { $element_tag } element "{ $element_text }" appears to be a dialog/modal but lacks appropriate ARIA markup
   AI_ErrDialogWithoutARIA-what = ...
   AI_ErrDialogWithoutARIA-why = ...
   AI_ErrDialogWithoutARIA-who = ...
   AI_ErrDialogWithoutARIA-remediation = ...
   ```
   Or use attributes:
   ```ftl
   AI_ErrDialogWithoutARIA =
       .title = The { $element_tag } element "{ $element_text }" appears to be a dialog/modal but lacks appropriate ARIA markup
       .what = ...
       .why = ...
       .who = ...
       .remediation = ...
   ```
2. Convert `wcag_translations_fr.py` → `wcag.ftl`
3. Update Python code that reads these translations to use the Fluent bundle
4. Delete `issue_translations_fr.json`, `issue_translations_inline.py`, `wcag_translations_fr.py`
5. Clean up imports: remove references to deleted files in `app.py`, `static_html_generator.py`, and any other modules that imported from the old translation files
6. Verify: issue descriptions and WCAG labels render correctly in reports

**Exit criteria:** Issue and WCAG translations served from `.ftl` files.

### Phase 5: Validation and CI (Day 13)

**Goal:** Automated checks ensure translation coverage.

1. Rewrite `scripts/validate_translations.py` to:
   - Parse all EN `.ftl` files to get the full set of message IDs
   - Parse all FR `.ftl` files and verify every EN message has a FR translation
   - Check for empty values
   - Report coverage percentage
2. Update `.github/workflows/ci.yml`:
   - Remove `pybabel compile` step
   - Run new Fluent validation script
3. Optionally integrate [FTL-Extract](https://github.com/andrew000/FTL-Extract) to detect `ftl()` calls in Python that reference nonexistent message IDs

**Exit criteria:** CI catches missing or empty translations.

### Phase 6: Cleanup (Day 14)

**Goal:** Remove all gettext artifacts.

1. Delete:
   - `babel.cfg`
   - `auto_a11y/web/translations/messages.pot`
   - `auto_a11y/web/translations/fr/LC_MESSAGES/messages.po`
   - `auto_a11y/web/translations/fr/LC_MESSAGES/messages.mo`
   - `.mo` entry from `.gitignore`
2. Remove `Flask-Babel` from `requirements.txt` / `pyproject.toml`
3. Remove runtime `.mo` compilation code from `run.py`
4. Remove `pybabel compile` commands from:
   - `render-build.sh`
   - `Dockerfile`
   - Any other build/deploy scripts
5. Update `CLAUDE.md` translation workflow section
6. Update `README.md` and `README.fr.md` (keep both in sync)
7. Final full-app smoke test in both languages

**Exit criteria:** No gettext references remain in the codebase. App passes full smoke test.

---

## 6. Risks and Mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| **No `pybabel extract` for templates.** New translatable strings must be manually added to `.ftl` files. Developers may forget. | High | Add a CI check that scans templates for untranslated literal strings (heuristic). Integrate FTL-Extract for Python source files. Document the workflow clearly. |
| **No Flask extension.** Custom integration layer is our code to maintain. | Medium | Keep the integration layer small (~50-100 lines) and well-tested. The API surface is minimal: bundle loading, `ftl()` function, escaper. |
| **`fluent-compiler` has a single maintainer** (Luke Plant). If abandoned, we own the dependency. | Medium | The library is feature-complete and stable (v1.1). Pin the version. The codebase is small enough to fork if needed. Apache 2.0 license. |
| **Regression during migration.** ~4,150 call sites changing simultaneously creates risk of broken translations. | High | Migrate one feature area at a time. Both `_()` and `ftl()` coexist as Jinja2 globals (different names). Test each area before moving to the next. |
| **Silent fallback on formatting errors.** When a Fluent message fails to format (e.g., missing variable), `fluent-compiler` returns the message ID as fallback instead of raising an exception. Unlike gettext where a missing `%s` raises an error. | Medium | This is generally a net positive (no crashes from translation errors), but developers should know that typos in variable names will produce visible message IDs in the UI rather than exceptions. Add logging in the integration layer for formatting failures. |
| **Message ID management overhead.** Every new UI string needs a unique, meaningful ID. Adds friction to frontend development. | Medium | Establish naming conventions (documented below). Consider a script that suggests IDs from English text. The trade-off is worth it for the reliability gains. |
| **Translators unfamiliar with FTL.** Professional translators universally know `.po` but `.ftl` is niche. | Low | For this project, translations are done internally, not by external translators. FTL syntax is simpler than `.po` (no header metadata, no escaping rules). |
| **Small Python ecosystem.** Fewer blog posts, Stack Overflow answers, and community examples than gettext. | Low | `fluent-compiler` has comprehensive documentation at fluent-compiler.readthedocs.io. The Fluent spec itself is well-documented at projectfluent.org. |
| **JS bundle size increase.** Adding `@fluent/bundle` to the frontend adds a dependency. | Low | `@fluent/bundle` is ~15KB minified. Can be loaded async. The current `window.i18n` pattern already sends translation data on every page load. |

### Message ID Naming Conventions

To keep IDs consistent and discoverable:

- **Format:** `{feature}-{element}-{descriptor}` in kebab-case
- **Examples:**
  - `auth-login-button` — Login button on the auth page
  - `report-export-title` — Title of the export report dialog
  - `pages-empty-state` — Empty state message on the pages list
  - `common-save` — Reusable "Save" label
  - `common-cancel` — Reusable "Cancel" label
- **Shared strings** go in `common.ftl`
- **Feature-specific strings** go in the feature's `.ftl` file
- **Issue descriptions** use the error code as the ID prefix: `ErrNoAlt-title`, `ErrNoAlt-what`

---

## 7. What Stays the Same

The following components are **not affected** by this migration:

| Component | Why |
|-----------|-----|
| **Typst i18n** (`i18n.typ`) | Typst has no Fluent support. PDF report translations remain in Typst dict format. |
| **Locale selection logic** | `get_locale()` in `app.py` stays the same — session → Accept-Language → default. Only the consumer changes (Fluent bundle instead of Babel). |
| **Language switching route** | `/set-language/<language>` route unchanged. |
| **Session-based locale storage** | `session['language']` continues to drive locale selection. |
| **Two supported locales** | EN and FR. Fluent makes adding more locales easier in the future, but this migration doesn't add any. |
| **Translation validation in CI** | The *concept* stays (CI blocks on missing translations). Only the *implementation* changes (new script checking `.ftl` coverage instead of `.po` fuzzy/empty checks). |

---

## Appendix A: Key Dependencies

| Package | Version | Purpose | Pure Python |
|---------|---------|---------|-------------|
| `fluent-compiler` | 1.1 | Python Fluent runtime (compiles FTL to bytecode) | Yes |
| `fluent-syntax` | >=0.14 | FTL parser/serializer | Yes |
| `babel` | >=2.12.0 | CLDR data for number/date formatting (already installed) | Yes |
| `pytz` | >=2025.2 | Timezone data (already installed) | Yes |
| `@fluent/bundle` | latest | JS Fluent runtime | N/A (JS) |

**Removed after migration:**
- `Flask-Babel`
- `pybabel` CLI usage

## Appendix B: Production Users of Fluent

- **Mozilla** — Firefox (the primary user; Fluent was created for Firefox), Firefox Send, Common Voice, Firefox Relay
- **Vox Media** — Coral commenting platform
- **Bevy** — game engine (Rust)
- **EditShare** — media asset management

## Appendix C: References

- [Project Fluent](https://projectfluent.org/) — official site and spec
- [Fluent Syntax Guide](https://projectfluent.org/fluent/guide/) — FTL language reference
- [fluent-compiler docs](https://fluent-compiler.readthedocs.io/) — Python library documentation
- [fluent-compiler on PyPI](https://pypi.org/project/fluent-compiler/)
- [@fluent/bundle on npm](https://www.npmjs.com/package/@fluent/bundle)
- [Fluent vs gettext](https://github.com/projectfluent/fluent/wiki/Fluent-vs-gettext) — official comparison
- [FTL-Extract](https://github.com/andrew000/FTL-Extract) — string extraction tool for Python
- [vscode-fluent](https://marketplace.visualstudio.com/items?itemName=macabeus.vscode-fluent) — VS Code extension
