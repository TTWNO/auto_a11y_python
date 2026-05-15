# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Auto A11y Python is a comprehensive web accessibility testing platform that combines automated DOM testing with AI-powered visual analysis. It tests websites for WCAG 2.1 compliance using JavaScript test scripts executed in browser context (via Playwright), enhanced with Claude AI for visual accessibility analysis.

**Key Technologies:**
- Python 3.8+ (Flask web framework)
- MongoDB (database)
- Playwright (browser automation, replaced Pyppeteer)
- Claude AI by Anthropic (visual analysis)
- JavaScript test scripts (browser-executed accessibility tests)

## Common Development Commands

### Running the Application

```bash
# Initial setup (first time only)
python run.py --setup

# Start the web interface (default: http://127.0.0.1:5001)
python run.py

# Start with custom host/port
python run.py --host 0.0.0.0 --port 8080

# Start in debug mode
python run.py --debug

# Test database connection
python run.py --test-db

# Download Chromium browser for Playwright
python run.py --download-browser
# Or manually: python -m playwright install chromium
```

### Fixture Testing (Critical)

**Only accessibility tests that pass ALL their fixtures are enabled in production.** The fixture system validates test accuracy against ~900 known HTML test cases.

```bash
# Quick validation (~5 minutes) - USE THIS DURING DEVELOPMENT
python test_fixtures.py --type Disco

# Test specific category
python test_fixtures.py --category Forms
python test_fixtures.py --category Images

# Test specific error code
python test_fixtures.py --code ErrNoAlt

# Full test suite (~1 hour - only run before major releases)
python test_fixtures.py

# View results
# Web interface: http://localhost:5001/testing/fixture-status
# Results saved to: fixture_test_results.json and MongoDB
```

### Development Workflow

```bash
# 1. Activate virtual environment
source venv/bin/activate

# 2. Start MongoDB (if not running)
mongod

# 3. Start Flask app (with auto-reload)
python run.py --debug

# 4. After making changes to test code, validate with fixtures
python test_fixtures.py --category <YourCategory>
```

### Git Workflow

**CRITICAL: NEVER rewrite git history.**

- **NEVER use:** `git commit --amend`, `git rebase -i`, `git reset --hard`, `git push --force`, or any other commands that modify existing commits
- **Always create new commits** for changes, even if fixing mistakes from previous commits
- **Reason:** This project may have multiple collaborators and shared branches. Rewriting history breaks collaboration and can cause data loss
- If you need to undo changes, use `git revert` to create new commits that reverse previous changes
- If commits need to be reorganized, consult with the repository owner first
- **NEVER use `git commit --no-verify`** — bypassing the pre-commit hook circumvents required type-checking enforcement. CI re-runs the same checks, so bypassing locally only delays the failure. Fix the errors before committing.

## High-Level Architecture

### Testing Flow

```
User → Flask Web UI → Testing Engine → Playwright Browser
                            ↓
                    1. Load target page
                    2. Inject JavaScript dependencies
                    3. Inject test scripts
                    4. Execute tests in browser context
                    5. Capture screenshot
                    6. Run Claude AI analysis (optional)
                    7. Aggregate results → MongoDB
                    8. Generate reports (HTML/XLSX/JSON/PDF)
```

### Core Components

**1. Test Runner (`auto_a11y/testing/test_runner.py`)**
- Orchestrates the entire testing process
- Manages Playwright browser instances
- Injects and executes JavaScript test scripts
- Coordinates with Claude AI analyzer
- Main entry point: `TestRunner.test_page(page_id)`

**2. Script Executor (`auto_a11y/testing/script_executor.py`)**
- Executes JavaScript accessibility tests in browser context
- Scripts located in `auto_a11y/scripts/tests/`
- Dependencies in `auto_a11y/scripts/dependencies/`
- Each test returns: `{errors: [...], warnings: [...], passes: [...], disco: [...]}`

**3. Result Processor (`auto_a11y/testing/result_processor.py`)**
- Processes raw test results from JavaScript
- Applies touchpoint mapping (categories)
- Deduplicates issues
- Formats for database storage

**4. Browser Manager (`auto_a11y/core/browser_manager.py`)**
- Manages Playwright browser lifecycle
- Handles browser context and page creation
- Takes screenshots with element highlighting

**5. Database (`auto_a11y/core/database.py`)**
- MongoDB operations for all collections
- Collections: `projects`, `websites`, `pages`, `test_results`, `fixture_tests`
- Handles large documents (16MB MongoDB limit considerations)

**6. Claude AI Integration (`auto_a11y/ai/`)**
- Visual accessibility analysis beyond DOM testing
- Detects: visual headings, reading order, modal issues, animations
- Uses Claude Opus 4 model with vision capabilities
- Controlled by `RUN_AI_ANALYSIS` config flag

### JavaScript Test Scripts Architecture

**Location:** `auto_a11y/scripts/tests/`

**Dependencies (loaded first):**
- `accessibleName.js` - W3C accessible name calculation
- `ariaRoles.js` - ARIA role definitions
- `colorContrast.js` - Color contrast calculations
- `xpath.js` - XPath utilities

**Test Execution Pattern:**
Each test script exports a function that returns results:
```javascript
function testScrape() {
    return {
        errors: [{url, code, cat, msg, html, xpath, ...}],
        warnings: [{...}],
        passes: [{...}],
        disco: [{...}]  // Discovery results
    };
}
```

**Python-Side Mapping:**
Python touchpoint tests (`auto_a11y/testing/touchpoint_tests/`) process JavaScript results:
- Extract specific error codes
- Apply business logic and filtering
- Map to WCAG criteria and touchpoints
- Return structured `TestResult` objects

### Fixture Testing System

**Purpose:** Validates that accessibility tests work correctly before production use.

**Architecture:**
- Fixtures: `Fixtures/` directory (~900 HTML files)
- Structure: `Fixtures/{Category}/{TestType}_{ErrorCode}/{fixture}.html`
- Test runner: `test_fixtures.py`
- Each fixture file contains metadata in HTML comment:
  ```html
  <!--
  Category: Forms
  Code: ErrNoAlt
  Type: Err
  ExpectedResult: This field has no label
  -->
  ```

**Fixture Types:**
- `Err` - High severity violations (must detect)
- `Warn` - Medium severity issues (must detect)
- `Info` - Informational notices (must detect)
- `Disco` - Element discovery (must find elements)
- `AI` - AI-powered detection (requires Claude API)

**Production Gates:**
- A test is ONLY enabled in production if ALL its fixtures pass
- Partial pass = disabled (prevents false positives)
- Check status: Web UI at `/testing/fixture-status`

### Touchpoint System

**Touchpoints** are accessibility categories/themes that group related issues.

**Implementation:**
- Defined in: `auto_a11y/core/touchpoints.py`
- Mapping: Error codes → Touchpoints → WCAG criteria
- Examples: Forms, Images, ColorAndContrast, Headings, Focus, etc.
- Used for: Report organization, filtering, analytics

**Key Functions:**
- `get_touchpoint_for_code(error_code)` - Map error code to touchpoint
- `get_all_touchpoints()` - List all touchpoints with metadata
- `get_wcag_criteria_for_touchpoint(touchpoint)` - Get related WCAG rules

### Database Schema

**Key Collections:**
- `projects` - Top-level project organization
- `websites` - Websites within projects
- `pages` - Individual pages to test (discovered or manual)
- `test_results` - Test results per page (timestamped)
- `fixture_tests` - Fixture validation results
- `scheduled_tests` - APScheduler test schedules
- `drupal_config` - Drupal integration settings (if enabled)

**Important:** MongoDB has 16MB document size limit. Large test results use reference pattern or GridFS.

## Key Development Patterns

### Adding a New Accessibility Test

1. **Create/Update JavaScript test** in `auto_a11y/scripts/tests/`
2. **Create Python touchpoint test** in `auto_a11y/testing/touchpoint_tests/`
3. **Create fixture HTML files** in `Fixtures/{Category}/{Type}_{Code}/`
4. **Run fixture tests** to validate: `python test_fixtures.py --code YourCode`
5. **Only enable in production** after all fixtures pass

### Modifying Existing Tests

1. **Update JavaScript** if DOM logic changes
2. **Update Python touchpoint test** if processing logic changes
3. **Validate with fixtures:** `python test_fixtures.py --code ExistingCode`
4. **If fixtures fail:** Fix code OR update fixture expectations
5. **Never enable tests with failing fixtures**

### Working with Browser Automation

- Playwright replaced Pyppeteer (note: some old docs mention Pyppeteer)
- Browser manager handles context/page lifecycle
- Always use `async/await` for browser operations
- Screenshots are base64 encoded for Claude AI
- Browser is headless by default (`BROWSER_HEADLESS` config)

### Claude AI Integration

- Located in: `auto_a11y/ai/`
- Requires: `CLAUDE_API_KEY` in config/env
- Enable: `RUN_AI_ANALYSIS = True` in config
- Model: Claude Opus 4 (configurable via `CLAUDE_MODEL`)
- Uses: Extended thinking mode for complex analysis
- Budget: Configurable token budget per analysis

### Report Generation

- Located in: `auto_a11y/reporting/`
- Formats: HTML, Excel (XLSX), JSON, CSV, PDF (via WeasyPrint)
- Static HTML reports: Self-contained, no database dependency
- Report types: Project, Website, Page, Comparison
- Custom styling: Templates in `auto_a11y/web/templates/reports/`

## Important Configuration

**Location:** `config.py` (loads from `.env`)

**Critical Settings:**
- `MONGODB_URI` - Database connection
- `CLAUDE_API_KEY` - AI features (optional but recommended)
- `RUN_AI_ANALYSIS` - Enable/disable Claude AI
- `PORT` - Default 5001 (avoid macOS AirPlay Receiver on 5000)
- `BROWSER_HEADLESS` - Headless browser mode
- `SHOW_ERROR_CODES` - Developer mode for debugging

## Testing Philosophy

**Two-Layer Testing:**
1. **JavaScript tests in browser** - Fast, accurate DOM testing
2. **Claude AI visual analysis** - Catches what DOM testing misses

**Fixture-Driven Development:**
- Write fixtures FIRST (TDD approach)
- Test against fixtures CONTINUOUSLY
- Only enable tests that pass ALL fixtures
- This prevents false positives in production

**Why JavaScript + Python:**
- JavaScript: Direct DOM access, W3C spec compliance, browser APIs
- Python: Orchestration, AI integration, data processing, web framework

## Bilingual Translation Requirements (MANDATORY)

**All user-visible frontend text MUST be translatable to both English and French.** This is a hard requirement, not optional.

The project uses [Project Fluent](https://projectfluent.org/) for i18n via the `fluent-compiler` Python library. Translations are stored in `.ftl` (Fluent Translation List) files.

### Rules

1. **Every user-visible string in templates** MUST use Fluent: `{{ ftl('message-id') }}` for static strings, `{{ ftl('message-id', var=value) }}` for parameterized strings, `{{ ftl_enum(value) }}` for enum values
2. **Every user-visible string in JavaScript** embedded in Jinja2 templates MUST use `{{ ftl('message-id') | tojson }}` for safe JS escaping
3. **Standalone JS files** (`auto_a11y/web/static/js/`) cannot use Jinja2 syntax. Use `window.i18n` objects built in templates with `{{ ftl('id') | tojson }}` values
4. **After adding new strings**, add entries to both `auto_a11y/web/translations/en/{feature}.ftl` and `fr/{feature}.ftl`
5. **No compilation step** — `.ftl` files are read directly at runtime (unlike the old `.po`/`.mo` system)
6. **Message IDs** use kebab-case with feature prefix: `auth-login-button`, `common-save`, `pages-empty-state`
7. **Enum values** use `enum-` prefix: `enum-discovered`, `enum-high`, `enum-active`
8. **Verify translations are accurate** — the French value must match the meaning of the English value

### Translation Workflow

```bash
# 1. Add the English string to the appropriate .ftl file
# e.g., auto_a11y/web/translations/en/pages.ftl
# pages-new-message = Your new message here

# 2. Add the French translation to the matching .ftl file
# e.g., auto_a11y/web/translations/fr/pages.ftl
# pages-new-message = Votre nouveau message ici

# 3. Use in template:
# {{ ftl('pages-new-message') }}

# 4. Validate coverage:
python tests/validate_translations.py
```

### FTL Syntax Quick Reference

```ftl
# Simple message
welcome = Welcome to Auto A11y

# Variable substitution
greeting = Hello, { $name }!

# Plural selectors
items-found =
    { $count ->
        [one] { $count } item found
       *[other] { $count } items found
    }

# Attributes (group related strings)
search-input = Search pages
    .placeholder = Enter URL or page name
    .aria-label = Search accessibility test results
```

### Translation Files

- **UI strings:** `auto_a11y/web/translations/{en,fr}/*.ftl` (14 files per locale)
- **Issue descriptions:** `auto_a11y/web/translations/{en,fr}/issues.ftl` and `inline-issues.ftl`
- **WCAG criteria:** `auto_a11y/web/translations/{en,fr}/wcag.ftl`
- **Typst reports:** `auto_a11y/reporting/typst_templates/lib/i18n.typ` (separate, not Fluent)
- **Integration layer:** `auto_a11y/web/fluent.py`

### Key Functions (from `auto_a11y/web/fluent.py`)

| Function | Usage |
|----------|-------|
| `ftl('message-id')` | Resolve a simple message |
| `ftl('message-id', var=value)` | Resolve with variables |
| `ftl_attr('message-id', 'attr')` | Resolve an attribute |
| `ftl_enum(value)` | Translate an enum value |
| `lazy_ftl('message-id')` | Lazy proxy for module-scope strings |
| `force_locale('fr')` | Context manager to override locale |

### README

The project maintains a French README (`README.fr.md`) alongside the English `README.md`. **Any change to `README.md` MUST be reflected in `README.fr.md`** — they must stay in sync. When editing the README, update both files in the same change.

## Colour System (MANDATORY)

**Bootstrap colour classes are PROHIBITED.** Do not use `btn-primary`, `bg-danger`, `text-warning`, `alert-success`, `badge bg-info`, `border-secondary`, or any other Bootstrap colour utility class. The application uses a custom colour system built on design tokens for full control over all colours in both light and dark mode.

### Architecture

```
tokens.css (design tokens)  →  style.css (utility classes)  →  templates/JS/Python (usage)
   Defines colours               Maps tokens to classes           Uses custom classes
   Light + dark mode              No Bootstrap overrides           Never Bootstrap colours
```

### Token File

- **Design tokens:** `auto_a11y/web/static/public/css/tokens.css` — all colour values live here
- Defines light mode, dark mode (OS preference + manual toggle), and print overrides
- All text colours meet WCAG 2.2 AA (4.5:1+), all non-text UI meets 3:1+ (SC 1.4.11)

### Custom Class Reference

| Purpose | Custom Class | Replaces (DO NOT USE) |
|---------|-------------|----------------------|
| **Buttons** | `btn-brand`, `btn-neutral`, `btn-pass`, `btn-high`, `btn-medium`, `btn-info`, `btn-dark` | ~~btn-primary, btn-secondary, btn-success, btn-danger, btn-warning~~ |
| **Outline Buttons** | `btn-outline-brand`, `btn-outline-neutral`, `btn-outline-pass`, `btn-outline-high`, `btn-outline-medium` | ~~btn-outline-primary, btn-outline-secondary~~ |
| **Alerts** | `alert-info`, `alert-pass`, `alert-high`, `alert-medium`, `alert-neutral` | ~~alert-success, alert-danger, alert-warning, alert-secondary~~ |
| **Badges** | `badge-brand`, `badge-neutral`, `badge-pass`, `badge-high`, `badge-medium`, `badge-info`, `badge-subtle`, `badge-discovery` | ~~badge bg-primary, badge bg-danger~~ |
| **Text** | `text-brand`, `text-severity-high`, `text-severity-medium`, `text-severity-pass`, `text-info-custom`, `text-muted`, `text-inverse`, `text-discovery` | ~~text-primary, text-danger, text-warning, text-success, text-white~~ |
| **Backgrounds** | `bg-brand`, `bg-neutral`, `bg-pass`, `bg-high`, `bg-medium`, `bg-info`, `bg-subtle`, `bg-header`, `bg-elevated` | ~~bg-primary, bg-danger, bg-dark, bg-light~~ |
| **Borders** | `border-brand`, `border-neutral`, `border-pass`, `border-high`, `border-medium`, `border-info` | ~~border-primary, border-danger, border-warning~~ |
| **Tables** | `table-subtle`, `table-info`, `table-medium`, `table-high`, `table-pass`, `table-neutral` | ~~table-light, table-warning, table-danger~~ |
| **Progress Bars** | `progress-pass`, `progress-medium`, `progress-high`, `progress-neutral` | ~~bg-success on .progress-bar~~ |
| **Card Headers** | `card-header-medium`, `card-header-info`, `card-header-pass`, `card-header-neutral` | ~~card-header bg-warning text-dark~~ |
| **Toasts** | `toast-pass`, `toast-high`, `toast-medium`, `toast-info` | ~~text-bg-success, text-bg-danger~~ |

### Rules

1. **NEVER use Bootstrap colour classes** — they bypass the design token system and break in dark mode
2. **To change a colour**, edit the token in `tokens.css` — all classes update automatically
3. **New severity/status colours** should be added as tokens first, then as utility classes in `style.css`
4. **Bootstrap structural classes are fine** — `btn`, `badge`, `alert`, `card`, `table`, `form-control`, layout utilities (`d-flex`, `row`, `col-*`, `mb-3`), etc. Only the **colour** variants are prohibited
5. **In Jinja2 dynamic patterns**, use dictionary lookups that map to custom class names (e.g., `badge-{{ {'high': 'high', 'medium': 'medium', 'low': 'info'}[impact] }}`)
6. **In standalone JS files**, use the custom class names in `classList.add()`, `querySelector()`, and template literals
7. **Every colour change MUST meet WCAG 2.2 AA contrast requirements** — text: 4.5:1 minimum (SC 1.4.3), large text: 3:1 minimum (SC 1.4.3), non-text UI components and graphical objects: 3:1 minimum (SC 1.4.11). Verify contrast ratios before committing any colour modification

### Automated Check (pre-commit)

A bun-based linter at `scripts/check-css-a11y.ts` runs on every commit (after the type checks) and blocks if it finds:

- **Contrast failures** — any rule that declares both `color:` and `background[-color]:` where the pair is below 4.5:1 (SC 1.4.3). Colour values may be hex literals, `rgb()` / `rgba()` (legacy comma and modern space + slash syntax, with int or percentage channels), `hsl()` / `hsla()` (deg / rad / grad / turn, optional alpha), `var(--name)` resolved against `tokens.css` and any `:root` rules in the file, `var(--name, fallback)`, or CSS named colours. Values with alpha < 1 are composited (foreground over the rule's background; background over white) so semi-transparent colours still produce a real contrast number.
- **Prohibited Bootstrap colour classes** in HTML/JS/Python — `btn-primary`, `bg-danger`, `text-warning`, etc.
- **`outline: none` / `outline: 0`** without a replacement focus style (`box-shadow`, `border`, `outline-offset`, or SVG `fill`/`stroke`) — SC 2.4.7
- **font-size below 12px / 0.75rem / 0.75em** — blocks the commit. Decorative icon glyphs (`aria-hidden="true"`) sized below 12px are an accepted exception; suppress them inline with an `@a11y-ignore` comment that names the reason.

Run manually:

```bash
bun run scripts/check-css-a11y.ts            # whole repo
bun run scripts/check-css-a11y.ts <files>    # specific files
bun run scripts/check-css-a11y.ts --json     # machine-readable
```

**Inline suppression** is allowed via the `@a11y-ignore` marker on the offending line (or the line directly above) when the rule has a documented false positive — e.g. when focus is rendered on a child element and the host legitimately needs `outline: none`:

```css
[role="treeitem"]:focus {
    /* @a11y-ignore: focus indicator is on the child, not the host */
    outline: none;
}
```

The hook will be skipped if `bun` is not installed, but CI will still run it — install bun from https://bun.sh before contributing.

## Type Checking (MANDATORY)

**All in-scope Python code MUST pass `mypy` strict mode, `pyright` strict mode, and `ty` in its strictest available mode.** This is a hard requirement, not optional. Type errors are fixed in code — never suppressed.

### Scope

Enforced on:
- `auto_a11y/**/*.py`
- `tests/**/*.py`
- `stubs/**/*.pyi`
- `config.py`, `run.py`, `wsgi.py`, `test_fixtures.py`

Excluded: `archive/`, `demo_site/`, `fixture_generation/`, `electron/`, top-level one-off migration/debug/translation scripts, and `auto_a11y/scripts/` (JavaScript).

### Setup (one time per clone)

```bash
python run.py --install-hooks
# or equivalently:
git config core.hooksPath .githooks
```

### Zero-escape-hatch policy

- **No `# type: ignore`** (any tool). `warn_unused_ignores`/equivalent is on, so leftover ignores are themselves errors.
- **No `# pyright: ignore`**.
- **No `# ty: ignore`**.
- **No `cast(Any, ...)`** as a workaround for a type error.
- **No `-> Any` return types**. Use `object`, a `TypeVar`, or an explicit union.
- **No `git commit --no-verify`**. CI re-runs the same checks; bypassing locally just delays the failure.
- **No `# type: ignore[...]` even with a code**. If a checker has a bug, the workaround is a code refactor, a stub patch, or a tool version pin — never a suppression comment.

### Adding a new dependency

If a new library lacks type information, you MUST (in the same commit that introduces the dependency):
1. Check for upstream `py.typed` in a newer version.
2. Check for a `types-<package>` or `<package>-stubs` PyPI package.
3. Check `typeshed`.
4. If none of the above, write a minimal fully-typed `.pyi` stub under `stubs/<package>/`. Only the symbols you import. No `Any`.

Commits that add an untyped import without stubs will fail the hook and CI.

### Modifying the checked scope

Adding a new module to the enforced set requires updating the `files`/`include` lists in `pyproject.toml` for **all three tools consistently**. Keep the lists in sync.

### Tool disagreement

If two checkers disagree:
1. First try to satisfy all three by refactoring or adding narrowing annotations.
2. If impossible, the authority order is `mypy` > `pyright` > `ty`. The less-authoritative tool's objection is treated as its bug; work around it in code.
3. If a three-way irreconcilable conflict emerges, surface it to the repo owner — do not land the code.

### `ty` recovery procedure

`ty` is pre-alpha. In the narrow case of a `ty` bug that no refactor or version pin can resolve, set `[tool.auto_a11y_typecheck] ty_enabled = false` in `pyproject.toml`. This is a repo-wide, review-visible configuration downgrade — **not** a suppression comment, and **not** permitted for `mypy` or `pyright`. File an upstream issue; flip the flag back on the next `ty` release.

### Running checks manually

```bash
.venv/bin/python -m mypy
.venv/bin/python -m pyright
.venv/bin/python -m ty check
```

### Branch protection

The `typecheck` CI job (both `3.11` and `3.12` matrix variants) must be a required status check on `main`. This is configured in the GitHub repo settings by the repo owner.

## Frontend Accessibility (MANDATORY)

**Every frontend change must be designed with accessibility in mind from the start, not bolted on at the end.** This product is an accessibility-testing platform — shipping an inaccessible UI is incoherent.

Automated tests for accessibility (especially screen-reader interaction patterns, focus trap behaviour, ARIA semantics in motion) are hard to write and harder to keep meaningful. We do not ship a generic "a11y test passed" gate. Instead, **accessibility lives in the design phase**: before you write the markup, think through the points below; while you implement, keep checking yourself against them; before you commit, walk through them once more.

### Things to think through during design

For any new or modified UI element, work through this list as part of the design conversation. If the answer to any question is "I'm not sure," stop and figure it out before coding.

- **Semantic HTML first.** Is there a native element (`<button>`, `<a>`, `<input>`, `<select>`, `<details>`, `<dialog>`) that already does this? Use it. Reach for ARIA only when no native element fits.
- **Keyboard reachability.** Can every interactive element be reached and operated using only the keyboard? Tab/Shift-Tab to move, Enter/Space to activate, arrow keys for composite widgets (menus, listboxes, radio groups, tabs).
- **Focus management.** When something opens (modal, popover, drawer), where does focus go? When it closes, where does focus return? Is focus visibly indicated at all times (3:1 contrast minimum on the focus ring per SC 1.4.11)?
- **Screen-reader name & role.** What does a screen reader announce when this element is focused? Does the accessible name match what a sighted user would call it? Does the role match the behaviour?
- **State changes are announced.** Loading spinners, validation errors, toast notifications, items appearing/disappearing — does a screen reader user know it happened? Use `aria-live`, `role="status"`, `role="alert"` as appropriate.
- **Colour is not the only signal.** Errors red and successes green — but is there also a text label, icon, or pattern? (SC 1.4.1 Use of Color.)
- **Contrast.** Text 4.5:1 minimum, large text and non-text UI 3:1 minimum. Verified against the design tokens, not eyeballed. (Already enforced for the colour system; this is a reminder it applies to *every* change.)
- **Reflow & zoom.** Does the layout still work at 200% zoom and at 320 CSS pixels wide without horizontal scrolling? (SC 1.4.10.)
- **Motion & autoplay.** Anything moving, blinking, or auto-advancing — is there a pause/stop control, and is `prefers-reduced-motion` respected?
- **Forms.** Every input has a programmatic label. Errors are tied to their field via `aria-describedby`/`aria-invalid`. Required fields are marked both visually and programmatically.
- **Headings & landmarks.** Heading levels are hierarchical (no skipping). Page regions use landmarks (`<main>`, `<nav>`, `<header>`, `<footer>` — or ARIA role equivalents).
- **Translatable strings.** New UI text follows the bilingual rules above (English + French via Fluent).

### Working alongside existing patterns

The custom colour system (the section above) already locks in WCAG 2.2 AA contrast for the design tokens. New UI work should:

- Use the existing utility classes (`btn-brand`, `text-severity-high`, etc.) rather than inventing new colour combinations.
- Match the keyboard and focus patterns of similar existing components (look at how the closest existing widget handles focus before designing a new one).
- Reuse existing dialog/modal/toast patterns rather than building a one-off — those already have focus traps, escape handling, and live-region wiring sorted out.

### When in doubt

Test with a real screen reader (VoiceOver, NVDA, or Orca) on the actual change before claiming it's done. If you cannot test that way in the current environment, say so explicitly in the PR description rather than asserting WCAG compliance you haven't verified — the project's whole purpose makes a hand-wavy claim worse than a clear "untested manually."

The fixture tests in this repo validate that the *testing engine* catches issues correctly. They do not validate that the *UI of this app* is itself accessible. That responsibility lives in the design and review phase of every frontend PR.

## Common Gotchas

1. **Port Conflict:** macOS AirPlay Receiver uses 5000 → We use 5001
2. **MongoDB Document Size:** 16MB limit → Large results use references
3. **Playwright vs Pyppeteer:** Codebase uses Playwright (some docs outdated)
4. **Fixture Failures:** NEVER enable tests with partial fixture pass
5. **Browser Download:** First run requires: `python -m playwright install chromium`
6. **AI Analysis:** Costs money per request → Test with `RUN_AI_ANALYSIS=False` first
7. **Async Operations:** Most browser/AI operations use async/await
8. **Bootstrap Colours:** NEVER use Bootstrap colour classes — see Colour System section above

## File Organization

```
auto_a11y/
├── ai/                      # Claude AI integration
├── core/                    # Core functionality (browser, database, scraper)
├── testing/                 # Test runners and processors
│   ├── touchpoint_tests/   # Python test implementations
│   └── test_runner.py      # Main test orchestrator
├── scripts/                # JavaScript test scripts
│   ├── dependencies/       # Required JS libraries
│   ├── tests/              # JS test modules (BROWSER-EXECUTED)
│   └── utilities/          # JS helper functions
├── reporting/              # Report generation
├── web/                    # Flask application
│   ├── routes/            # API endpoints
│   └── templates/         # Jinja2 templates
└── models/                # Data models

Fixtures/                   # Test fixtures (~900 HTML files)
├── {Category}/            # E.g., Forms, Images, Headings
│   └── {Type}_{Code}/     # E.g., Err_NoAlt, Disco_FormOnPage
│       └── *.html         # Individual test cases

docs/                      # Architecture documentation
config.py                  # Configuration management
run.py                     # Application entry point
test_fixtures.py           # Fixture test runner
```

## Useful Resources

- **Architecture:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **Fixture Testing:** [docs/FIXTURE_TESTING.md](docs/FIXTURE_TESTING.md)
- **Quick Reference:** [FIXTURE_TESTING_QUICKREF.md](FIXTURE_TESTING_QUICKREF.md)
- **JavaScript Integration:** [docs/JAVASCRIPT_INTEGRATION.md](docs/JAVASCRIPT_INTEGRATION.md)
- **Claude AI:** [docs/CLAUDE_AI_INTEGRATION.md](docs/CLAUDE_AI_INTEGRATION.md)
- **Issue Catalog:** [ISSUE_CATALOG.md](ISSUE_CATALOG.md)
- **Fixture Web UI:** http://localhost:5001/testing/fixture-status
