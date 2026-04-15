# Self-Test Accessibility Remediation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the accessibility violations and warnings discovered by running Auto A11y against itself (self-test), bringing the app from 85.5% to near-100% accessibility score.

**Architecture:** Fix shared base-level issues first (cascading to all pages), then sweep individual templates. Group by issue category for focused, testable changes. Each task produces a commit.

**Tech Stack:** Flask/Jinja2 templates, Bootstrap 5.3, vanilla JavaScript, CSS, HTML `<dialog>`

---

## Background

A self-test of the Auto A11y application (89 pages, 2026-04-14) found **301 violations** and **400 warnings**. The raw reports are in `selftest/`. This plan addresses the actionable issues found in the deduplicated report (`selftest/index.html`), organized from highest-impact (fixes cascading to most pages) to lowest.

### Issue Summary

| # | Issue | Severity | Pages | Category |
|---|-------|----------|-------|----------|
| 1 | `<dialog>` elements missing `aria-modal="true"` | Warn/MED | 13 | Dialogs |
| 2 | Clickable `<i>` icons not keyboard-accessible | Viol/HIGH | 2 | Event Handling |
| 3 | Empty dropdown divider `<li>` elements | Viol/MED | 68+ | Lists |
| 4 | `<legend>` styled as heading with `.h5` class | Viol/HIGH | 10 | Headings |
| 5 | Inline `<style>` blocks with hardcoded colors | Viol/HIGH | 16 | Styles |
| 6 | Inline `style=` attributes with color/font properties | Viol/LOW | 12 | Styles |
| 7 | French issue description text missing `lang="fr"` | Viol/MED | 9 | Language |
| 8 | Auto-firing timers without user pause controls | Viol/HIGH | 72 | Timers |

### Not Addressed (False Positives / Acceptable)

These issues from the self-test are **not actionable** and are excluded from this plan:

- **"Time-based content lacks user controls"** (72 pages) — The polling intervals (setInterval) are for live status updates during test/discovery operations. They already have stop mechanisms (clearInterval on completion, dismiss buttons). The 5-second base tracker has a pause button. This is acceptable for a monitoring dashboard.
- **"Tab order mismatch: `<a>` at (0,0) vs `<button>` at (474,9)"** (73 pages) — This is the skip-to-content link which is correctly positioned first in DOM/tab order but visually hidden at (0,0). This is the *correct* accessible pattern.
- **"List item uses icon font elements with list-style:none"** (73 pages) — The sidebar nav correctly uses `<ul>/<li>` with Bootstrap Icons as decorative elements (`aria-hidden="true"`). The `list-style: none` is standard for nav lists. Not a real issue.
- **"Breadcrumb uses ::before with content '/'"** (10 pages) — Bootstrap's default breadcrumb separator pattern. The `<nav aria-label="breadcrumb">` and `<ol class="breadcrumb">` provide correct semantics. Screen readers announce list structure, not the CSS separator.
- **"Inline style layout properties"** (61 pages) — Most are dynamic `display:none`, progress bar widths, or responsive sizing that must be inline. Low priority, no accessibility impact.
- **"setTimeout/setInterval on page load"** (73 pages) — Covered by the timer analysis above. The auto-dismiss of notifications (5s) and search debouncing (300ms) are acceptable UX patterns.

---

## Task 1: Add `aria-modal="true"` to All `<dialog>` Elements

**Files:**
- Modify: `auto_a11y/web/templates/drupal_sync/sync_card.html:26`
- Modify: `auto_a11y/web/templates/websites/view.html:460,491,524,549,583`
- Modify: `auto_a11y/web/templates/websites/edit.html:126`
- Modify: `auto_a11y/web/templates/reports/dashboard.html:291,1032,1079,1146,1302,1432,1513`
- Modify: `auto_a11y/web/templates/recordings/detail.html:691`
- Modify: `auto_a11y/web/templates/projects/view.html:470,501,567,617`
- Modify: `auto_a11y/web/templates/projects/edit.html:462,485`
- Modify: `auto_a11y/web/templates/projects/create.html:399`
- Modify: `auto_a11y/web/templates/automated_tests/list.html:164`
- Modify: `auto_a11y/web/templates/pages/view.html:3704,3725`
- Modify: `auto_a11y/web/templates/testing/fixture_status.html:559`
- Modify: `auto_a11y/web/templates/testing/configure.html:188`

**Why:** Native `<dialog>` elements opened with `.showModal()` already trap focus and make the background inert, but older screen readers (JAWS < 2023, NVDA < 2023.1) don't fully recognize `<dialog>` semantics. Adding `aria-modal="true"` ensures backwards compatibility. All 27 dialog elements are missing this attribute.

- [ ] **Step 1: Add `aria-modal="true"` to every `<dialog>` element**

The pattern is the same for all 27 dialogs. Change:
```html
<dialog class="modal" id="XModal" aria-labelledby="XModalLabel">
```
to:
```html
<dialog class="modal" id="XModal" aria-labelledby="XModalLabel" aria-modal="true">
```

Apply to all 27 `<dialog>` elements listed above. A quick way to verify you got them all:

```bash
grep -rn '<dialog' auto_a11y/web/templates/ | grep -v 'aria-modal'
```

Expected: **no output** (all dialogs now have aria-modal).

- [ ] **Step 2: Verify no regressions**

```bash
grep -c 'aria-modal="true"' auto_a11y/web/templates/**/*.html
```

Expected: 27 matches across 12 files.

Open the app, verify a modal still opens/closes correctly (e.g., Add Website modal on a project view page).

- [ ] **Step 3: Commit**

```bash
git add auto_a11y/web/templates/
git commit -m "a11y: add aria-modal=true to all 27 dialog elements for screen reader backwards compatibility"
```

---

## Task 2: Convert Clickable Icons to Accessible Buttons

**Files:**
- Modify: `auto_a11y/web/templates/testing/fixture_status.html:114,123,132`
- Modify: `auto_a11y/web/templates/testing/trends.html:1290`

**Why:** Four interactive elements use `onclick` on non-focusable elements (`<i>` and `<span>`). The `<i>` icons in fixture_status.html also have `aria-hidden="true"`, which contradicts their interactive purpose — screen readers can't see them at all. These need to be proper `<button>` elements.

- [ ] **Step 1: Read the current fixture_status.html icon code**

Read `auto_a11y/web/templates/testing/fixture_status.html` around lines 110-135 to see the current pattern.

- [ ] **Step 2: Convert the three help icons in fixture_status.html to buttons**

Replace each clickable `<i>` icon pattern. There are 3 instances (lines ~114, ~123, ~132) that look like:

```html
<i class="bi bi-question-circle-fill text-primary" aria-hidden="true" style="cursor: pointer;" onclick="showHelp('all-pass')"></i>
```

Replace each with:

```html
<button type="button" class="btn btn-link p-0 border-0" onclick="showHelp('all-pass')" aria-label="{{ ftl('testing-help-all-pass') }}">
    <i class="bi bi-question-circle-fill text-primary" aria-hidden="true"></i>
</button>
```

Repeat for `'partial-pass'` and `'all-fail'` variants, using appropriate `aria-label` translation keys:
- `showHelp('all-pass')` → `aria-label="{{ ftl('testing-help-all-pass') }}"`
- `showHelp('partial-pass')` → `aria-label="{{ ftl('testing-help-partial-pass') }}"`
- `showHelp('all-fail')` → `aria-label="{{ ftl('testing-help-all-fail') }}"`

- [ ] **Step 3: Add translation keys for the help button labels**

Add to `auto_a11y/web/translations/en/testing.ftl`:
```ftl
testing-help-all-pass = Help: All fixtures pass
testing-help-partial-pass = Help: Partial fixture pass
testing-help-all-fail = Help: All fixtures fail
```

Add to `auto_a11y/web/translations/fr/testing.ftl`:
```ftl
testing-help-all-pass = Aide : Tous les tests réussissent
testing-help-partial-pass = Aide : Réussite partielle des tests
testing-help-all-fail = Aide : Tous les tests échouent
```

- [ ] **Step 4: Read the trends.html filter removal span**

Read `auto_a11y/web/templates/testing/trends.html` around line 1290 to see the current pattern for the remove-filter span.

- [ ] **Step 5: Convert the remove-filter span in trends.html to a button**

The dynamically-generated filter removal element looks like:

```javascript
<span class="remove-filter" onclick="removeFilter('${type}', '${filterValue}')">&times;</span>
```

Replace with:

```javascript
<button type="button" class="remove-filter btn btn-link p-0 border-0" onclick="removeFilter('${type}', '${filterValue}')" aria-label="Remove filter: ${filterValue}">&times;</button>
```

Note: This is inside a JavaScript template literal, so Jinja2 `ftl()` won't work here. The English-only label is acceptable for this admin-only developer tool. If translation is needed, use the `window.i18n` pattern.

- [ ] **Step 6: Add CSS for the button reset (if needed)**

If the button appearance changes, add to `auto_a11y/web/static/css/style.css`:

```css
.remove-filter.btn-link {
    font-size: inherit;
    line-height: inherit;
    vertical-align: baseline;
    text-decoration: none;
    color: inherit;
}
```

- [ ] **Step 7: Verify in browser**

Open `/testing/fixture-status` — confirm the help icons are keyboard-focusable and announce their purpose via screen reader.

Open `/testing/trends` — confirm filter removal buttons are keyboard-accessible.

- [ ] **Step 8: Commit**

```bash
git add auto_a11y/web/templates/testing/fixture_status.html auto_a11y/web/templates/testing/trends.html auto_a11y/web/translations/ auto_a11y/web/static/css/style.css
git commit -m "a11y: convert clickable icons and spans to accessible button elements"
```

---

## Task 3: Fix Empty Dropdown Divider List Items

**Files:**
- Modify: `auto_a11y/web/templates/base.html:116,137,151`
- Modify: `auto_a11y/web/templates/websites/view.html:36,54,75,93,142`

**Why:** `<li><hr class="dropdown-divider"></li>` creates list items that are semantically empty from a screen reader perspective. Adding `role="separator"` to the `<hr>` and `role="none presentation"` to the `<li>` wrapper tells assistive technology these are visual dividers, not content items. This is the [WAI-ARIA menu pattern](https://www.w3.org/WAI/ARIA/apd/example/menubar/menubar-navigation.html).

- [ ] **Step 1: Read base.html dropdown sections**

Read `auto_a11y/web/templates/base.html` around lines 110-155 to see the dropdown dividers.

- [ ] **Step 2: Update all dropdown dividers in base.html**

Change each instance of:
```html
<li><hr class="dropdown-divider"></li>
```
to:
```html
<li role="none"><hr class="dropdown-divider" role="separator"></li>
```

There are 3 instances in base.html (lines ~116, ~137, ~151).

- [ ] **Step 3: Read websites/view.html dropdown sections**

Read `auto_a11y/web/templates/websites/view.html` around lines 30-95 and 140-145 to see the dropdown dividers.

- [ ] **Step 4: Update all dropdown dividers in websites/view.html**

Same pattern change. There are 5 instances (lines ~36, ~54, ~75, ~93, ~142).

- [ ] **Step 5: Search for any other dropdown dividers across all templates**

```bash
grep -rn '<li><hr class="dropdown-divider">' auto_a11y/web/templates/
```

Fix any additional instances found with the same pattern.

- [ ] **Step 6: Verify in browser**

Open any page with dropdown menus. Confirm dividers still appear visually. With a screen reader, confirm the dividers are announced as separators (or skipped), not as empty list items.

- [ ] **Step 7: Commit**

```bash
git add auto_a11y/web/templates/
git commit -m "a11y: add role=separator to dropdown dividers, role=none to wrapper li"
```

---

## Task 4: Fix Legend Elements Styled as Headings

**Files:**
- Modify: `auto_a11y/web/templates/groups/edit.html:21,36`
- Modify: `auto_a11y/web/templates/projects/create.html:92,153,211,323`
- Modify: `auto_a11y/web/templates/projects/edit.html:58,94,294,364`

**Why:** `<legend class="h5">` makes text look like a heading but isn't announced as one by screen readers. Users navigating by headings will miss these section labels. The fix is to nest a heading inside the legend — `<legend><h3 class="h5">...</h3></legend>` — which is valid HTML and provides both the fieldset label and heading semantics.

Note: `testing/configure.html` already uses the correct `<legend><h3>...</h3></legend>` pattern. We're bringing the other templates into alignment.

- [ ] **Step 1: Read groups/edit.html fieldset structure**

Read `auto_a11y/web/templates/groups/edit.html` around lines 18-40.

- [ ] **Step 2: Fix legends in groups/edit.html**

Change:
```html
<legend class="h5">{{ ftl('groups-group-details') }}</legend>
```
to:
```html
<legend><h3 class="h5 mb-0">{{ ftl('groups-group-details') }}</h3></legend>
```

And:
```html
<legend class="h5">{{ ftl('groups-permissions') }}</legend>
```
to:
```html
<legend><h3 class="h5 mb-0">{{ ftl('groups-permissions') }}</h3></legend>
```

- [ ] **Step 3: Read projects/create.html fieldset structure**

Read `auto_a11y/web/templates/projects/create.html` around lines 88-100, 150-160, 208-215, 320-330.

- [ ] **Step 4: Fix legends in projects/create.html**

Apply the same pattern to all 4 legends:
```html
<legend class="h5">{{ ftl('...') }}</legend>
```
→
```html
<legend><h3 class="h5 mb-0">{{ ftl('...') }}</h3></legend>
```

The 4 translation keys are:
- `projects-project-details`
- `projects-compliance-settings`
- `projects-touchpoint-tests`
- `projects-ai-assisted-testing`

- [ ] **Step 5: Read projects/edit.html fieldset structure**

Read `auto_a11y/web/templates/projects/edit.html` around lines 55-65, 90-100, 290-300, 360-370.

- [ ] **Step 6: Fix legends in projects/edit.html**

Same pattern change for 4 legends with the same translation keys.

- [ ] **Step 7: Verify heading structure**

Open a project create/edit page in the browser. Use a heading navigation tool (or browser devtools accessibility tree) to verify the legend text now appears in the heading hierarchy.

- [ ] **Step 8: Commit**

```bash
git add auto_a11y/web/templates/groups/edit.html auto_a11y/web/templates/projects/create.html auto_a11y/web/templates/projects/edit.html
git commit -m "a11y: nest headings inside legend elements for proper heading hierarchy"
```

---

## Task 5: Move Inline `<style>` Block Colors to CSS Variables

**Files:**
- Modify: `auto_a11y/web/templates/help.html:7-50`
- Modify: `auto_a11y/web/templates/testing/fixture_status.html:6-73`
- Modify: `auto_a11y/web/static/css/style.css` (add new classes)

**Why:** Inline `<style>` blocks with hardcoded hex/rgb colors override user stylesheets and prevent users with low vision from customizing visual presentation (WCAG 1.4.12 Text Spacing / general user override capability). Moving these to the main stylesheet using CSS custom properties ensures consistency with the dark mode system and allows user overrides.

**Note:** `help.html` already loads a dedicated `auto_a11y/web/static/css/help-system.css` file (289 lines). Extracted styles for the help page should go there, not in `style.css`.

- [ ] **Step 1: Read help.html inline styles**

Read `auto_a11y/web/templates/help.html` lines 1-55 to see the inline style block.

- [ ] **Step 2: Extract help.html colors to CSS classes in help-system.css**

Add to `auto_a11y/web/static/css/help-system.css` (which already exists and is loaded by the help page):

```css
/* Help page styles */
.help-section {
    border-left: 4px solid var(--bs-primary);
    padding: 15px;
    margin-bottom: 20px;
    background-color: var(--bs-body-bg);
}

.help-section .text-muted-custom {
    color: var(--color-text-muted);
}

.help-section a {
    color: var(--bs-primary);
}

.help-section a:hover {
    border-left-color: var(--bs-primary);
}

.help-hero {
    background: linear-gradient(135deg, var(--bs-primary) 0%, var(--bs-purple, #764ba2) 100%);
    color: #fff;
}
```

Then remove the entire `<style>` block from `help.html` and replace the affected elements with the new CSS classes.

- [ ] **Step 3: Read fixture_status.html inline styles**

Read `auto_a11y/web/templates/testing/fixture_status.html` lines 1-75 to see the inline style block.

- [ ] **Step 4: Extract fixture_status.html colors to CSS classes**

Add to `auto_a11y/web/static/css/style.css`:

```css
/* Fixture status page styles */
.fixture-stat-card {
    background-color: var(--bs-tertiary-bg);
}

.fixture-card-hover:hover {
    background-color: rgba(var(--bs-primary-rgb), 0.05);
}

.fixture-warning-hover:hover {
    background-color: rgba(var(--bs-warning-rgb), 0.1);
}
```

Remove the hardcoded rgba colors from the inline `<style>` block in `fixture_status.html` and apply the new classes.

- [ ] **Step 5: Verify dark mode compatibility**

Open both pages in dark mode (toggle theme). Verify colors adapt properly via CSS variables rather than showing hardcoded light-mode colors.

- [ ] **Step 6: Commit**

```bash
git add auto_a11y/web/templates/help.html auto_a11y/web/templates/testing/fixture_status.html auto_a11y/web/static/css/style.css auto_a11y/web/static/css/help-system.css
git commit -m "a11y: move inline style block colors to CSS variables in stylesheet"
```

---

## Task 6: Move Inline `style=` Font/Color Attributes to CSS Classes

**Files:**
- Modify: `auto_a11y/web/templates/project_users/list.html:177`
- Modify: `auto_a11y/web/templates/website_users/list.html:180`
- Modify: `auto_a11y/web/templates/scripts/list.html:159`
- Modify: `auto_a11y/web/templates/project_participants/list.html:122,208`
- Modify: `auto_a11y/web/templates/projects/view.html:224,227,356,365`
- Modify: `auto_a11y/web/templates/pages/view.html:299,302,658,974`
- Modify: `auto_a11y/web/templates/schedules/dashboard.html:270`
- Modify: `auto_a11y/web/templates/schedules/list.html:198`
- Modify: `auto_a11y/web/static/css/style.css` (add utility classes)

**Why:** Inline `style` attributes with font/color properties override user stylesheets. Moving to CSS classes allows users to customize visual presentation. This is a WCAG 1.4.12 (Text Spacing) / general user override concern.

- [ ] **Step 1: Add utility classes to style.css**

Add to `auto_a11y/web/static/css/style.css`:

```css
/* Utility classes to replace inline styles */
.empty-state-icon {
    font-size: 3rem;
}

.text-size-sm {
    font-size: 0.875rem;
}

.text-size-xs {
    font-size: 0.75rem;
}

.text-size-card-title {
    font-size: 0.9rem;
}

.text-size-card-badge {
    font-size: 0.7rem;
}

.text-size-tiny {
    font-size: 8px;
}
```

- [ ] **Step 2: Replace inline font-size: 3rem on empty state icons**

In each of these files, find `style="font-size: 3rem;"` on empty-state icon `<i>` elements and replace the inline style with the class:
```html
class="empty-state-icon"
```

Note: Some icons may already have other classes — merge the new class with existing ones. If the element has a separate `opacity` or uses a Bootstrap text utility class for its muted appearance, preserve that.

Files: `project_users/list.html`, `website_users/list.html`, `scripts/list.html`, `project_participants/list.html`, `schedules/dashboard.html`, `schedules/list.html`.

- [ ] **Step 3: Replace inline font-size on project cards**

In `projects/view.html`, replace:
- `style="font-size: 0.9rem;"` → add class `text-size-card-title`
- `style="font-size: 0.7rem;"` → add class `text-size-card-badge`

- [ ] **Step 4: Replace inline font/color on pages/view.html**

In `pages/view.html`, replace:
- `style="...font-size: 0.9em; color: var(--color-text-muted);"` → add classes `text-size-card-title text-muted`
- `style="font-size: 8px;"` → add class `text-size-tiny`
- `style="font-size: 0.875rem;"` → add class `text-size-sm`

- [ ] **Step 5: Verify visual appearance unchanged**

Open affected pages, compare layout before/after. The visual result should be identical.

- [ ] **Step 6: Commit**

```bash
git add auto_a11y/web/templates/ auto_a11y/web/static/css/style.css
git commit -m "a11y: replace inline font/color style attributes with CSS classes"
```

---

## Task 7: Add `lang="fr"` to All French Text Elements in Static Reports

**Files:**
- Modify: `auto_a11y/web/templates/static_report/dedup_unassigned.html`
- Modify: `auto_a11y/web/templates/static_report/page_detail.html`
- Modify: `auto_a11y/web/templates/static_report/base.html`
- Modify: `auto_a11y/web/templates/static_report/recordings_report_standalone.html`
- Modify: `auto_a11y/web/templates/static_report/comprehensive_report_standalone.html`
- Modify: `auto_a11y/web/templates/static_report/dedup_component.html`
- Modify: `auto_a11y/web/templates/static_report/index.html`
- Modify: `auto_a11y/web/templates/static_report/dedup_index.html`
- Modify: `auto_a11y/web/templates/static_report/recordings_report.html`

**Why:** The static report templates use `data-lang="fr"` as a CSS toggle for language switching, but none of these elements also have the HTML `lang="fr"` attribute. Without it, screen readers will pronounce French text using English phonetics. There are ~380 elements with `data-lang="fr"` across 9 files that all need the HTML `lang` attribute added.

- [ ] **Step 1: Verify the scope**

```bash
grep -c 'data-lang="fr"' auto_a11y/web/templates/static_report/*.html
```

Expected: ~380 total across 9 files.

Also verify that none already have `lang="fr"`:

```bash
grep -c 'data-lang="fr".*lang="fr"\|lang="fr".*data-lang="fr"' auto_a11y/web/templates/static_report/*.html
```

Expected: 0 (none have both attributes yet).

- [ ] **Step 2: Add `lang="fr"` to every element with `data-lang="fr"`**

This is a mechanical find-and-replace across all 9 files. In each file, replace:

```
data-lang="fr"
```

with:

```
data-lang="fr" lang="fr"
```

This can be done with a single sed command for verification, but apply via your editor for safety:

```bash
# Preview (dry run):
grep -rn 'data-lang="fr"' auto_a11y/web/templates/static_report/ | grep -v 'lang="fr"' | wc -l

# Apply:
# For each file, find all occurrences of data-lang="fr" and add lang="fr"
```

- [ ] **Step 3: Update the `switchLanguage()` JavaScript function**

In `auto_a11y/web/templates/static_report/base.html`, the `switchLanguage()` function toggles CSS classes for language visibility. It should also update the `<html>` element's `lang` attribute:

Find the `switchLanguage` function and add at the beginning:

```javascript
document.documentElement.lang = lang;
```

This ensures the document-level language declaration matches the active language.

- [ ] **Step 4: Also check for `data-lang="en"` elements that might need `lang="en"`**

For completeness, verify that English elements on a French page also get proper lang attributes:

```bash
grep -c 'data-lang="en"' auto_a11y/web/templates/static_report/*.html
```

If there are matching English elements, add `lang="en"` to them as well (same mechanical replacement).

- [ ] **Step 5: Verify**

Open a static report in the browser. Switch to French. Inspect a French text element and confirm it has both `data-lang="fr"` and `lang="fr"` attributes. Use a screen reader to verify French text is pronounced with French phonetics.

- [ ] **Step 6: Commit**

```bash
git add auto_a11y/web/templates/static_report/
git commit -m "a11y: add lang=fr to all French text elements in static report templates"
```

---

## Task 8: Investigate and Fix "Icon Font Bullets Not in List" Issue

**Files:**
- Investigate: `auto_a11y/web/templates/base.html` (sidebar, footer, other repeating elements)
- Investigate: `auto_a11y/scripts/tests/` (the test that detects this issue)

**Why:** The self-test reports *"Element contains 3 list-like items using icon font bullets but does not use proper `<ul>/<ol>` and `<li>` markup"* on 69 pages. The sidebar navigation already uses proper list markup, so this must be another element. This task requires investigation to identify the specific element.

- [ ] **Step 1: Identify the detection test**

Search for the error code or message in the JavaScript test scripts:

```bash
grep -rn 'icon font bullets\|list-like items\|icon.*bullet' auto_a11y/scripts/tests/
```

Also check the touchpoint test mapping:

```bash
grep -rn 'icon.*font.*bullet\|ErrIconList\|WarnIconList' auto_a11y/testing/touchpoint_tests/
```

- [ ] **Step 2: Run the app and inspect the flagged element**

Start the app (`python run.py`) and navigate to a page that would trigger this issue. Use browser devtools to find elements with 3+ siblings that each have an icon font element (classes like `bi-*`, `fa-*`, `material-icons`).

Common suspects:
- Footer content with icon-decorated items
- Card groups with icon headers
- Status indicator groups
- Breadcrumb items (but these use proper `<ol>`)

- [ ] **Step 3: Fix the identified element**

Once found, either:
- Wrap the items in a proper `<ul>/<li>` structure, or
- If the items are not semantically a list, add `role="list"` and `role="listitem"` if appropriate, or
- If the detection is a false positive (the items aren't actually a list), document why and consider tuning the test script

- [ ] **Step 4: Validate with fixture test**

If the detection is a true positive:
```bash
python test_fixtures.py --type Disco
```

If the detection needs tuning, update the JavaScript test to avoid false positives on proper `<ul>/<li>` nav structures.

- [ ] **Step 5: Commit**

```bash
git add <affected files>
git commit -m "a11y: fix icon font items to use proper list markup"
```

---

## Task 9: Add `prefers-reduced-motion` Coverage for Remaining Pages

**Files:**
- Modify: `auto_a11y/web/static/css/style.css`
- Investigate: Templates with animations not covered by the global rule

**Why:** The self-test flagged *"Animations detected but no @media (prefers-reduced-motion) query found"* on 21 pages. The main `style.css` already has a comprehensive `prefers-reduced-motion` rule (lines 480-497) that sets `animation-duration: 0.01ms !important` on all elements. The 21 pages likely have additional `<style>` blocks or inline animations not covered.

- [ ] **Step 1: Check which templates have their own animation CSS**

```bash
grep -rn 'animation\|@keyframes\|transition' auto_a11y/web/templates/ --include="*.html" | grep -v '{#\|<!--'
```

- [ ] **Step 2: Add prefers-reduced-motion to any template-level style blocks**

For any template with its own `<style>` block that defines animations (e.g., `help.html`), add within that style block:

```css
@media (prefers-reduced-motion: reduce) {
    * {
        animation-duration: 0.01ms !important;
        transition-duration: 0.01ms !important;
    }
}
```

Or, better: if Task 5 moves the styles to the main stylesheet, this is already covered by the global rule.

- [ ] **Step 3: Verify**

Toggle `prefers-reduced-motion: reduce` in browser devtools (Rendering tab). Confirm all spinner and pulse animations stop.

- [ ] **Step 4: Commit**

```bash
git add auto_a11y/web/static/css/style.css auto_a11y/web/templates/
git commit -m "a11y: ensure prefers-reduced-motion covers all animation contexts"
```

---

## Execution Order

Tasks are ordered by impact (number of pages affected and severity):

1. **Task 1** — aria-modal (quick, 27 elements, backwards compat)
2. **Task 3** — dropdown dividers (quick, fixes 68+ pages)
3. **Task 2** — clickable icons (important, keyboard access)
4. **Task 4** — legend headings (important, heading navigation)
5. **Task 5** — inline style blocks (medium effort, color overridability)
6. **Task 7** — French lang attribute (medium effort, investigation needed)
7. **Task 6** — inline style attributes (low effort, low severity)
8. **Task 9** — prefers-reduced-motion (low effort, may be covered by Task 5)
9. **Task 8** — icon font list investigation (effort varies, needs investigation)

Tasks 1-4 are independent and can be parallelized. Tasks 5 and 9 are related (moving styles to stylesheet covers animation issues). Task 8 requires investigation first.
