# Self-Test Accessibility Remediation Plan (Post-Bootstrap-Colour-Removal)

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all remaining accessibility violations and warnings from the 2026-04-15 self-test, bringing the app to 0 violations. Previous self-test fixes (commit fe6cf9e) and bootstrap colour removal (commits 3b768ad..bc4bec9) have already addressed many issues. This plan covers everything that remains.

**Architecture:** Fix shared CSS/base-template issues first (cascading to 72+ pages), then navigation components, then page-specific templates. Each task targets a specific error code or category, with one commit per task.

**Tech Stack:** CSS (tokens.css, style.css), Jinja2 templates (base.html + page templates), vanilla JavaScript, Flask route logic

---

## Issue Inventory (from selftest/index.html, 2026-04-15)

### Common Violations (32 unique, appearing across 2-72 pages)

| # | Description | Impact | Pages | Root Cause |
|---|------------|--------|-------|-----------|
| V1 | Button removes focus outline with no alternative | HIGH | 72 | CSS `outline:none` on `:focus:not(:focus-visible)` |
| V2 | Skip link target lacks tabindex | HIGH | 72 | `<main>` missing `tabindex="-1"` |
| V3 | Interactive element has no visible focus indicator | HIGH | 72 | Same as V1 + navbar dark background |
| V4 | Link styled as button doesn't respond to Space | LOW | 72 | `<a class="btn">` pattern everywhere |
| V5 | Time-based content lacks user controls | HIGH | 70 | Auto-dismiss notification setTimeout |
| V6 | Text size below 16px | MEDIUM | 63 | `.text-size-tiny` at 8px, small table text |
| V7 | Normal text contrast 2.77:1 on medium bg | HIGH | 62 | Dark text on `#7d6608` medium-severity bg |
| V8 | Large text contrast 2.77:1 on medium bg | HIGH | 49 | Same as V7 |
| V9 | Text contrast with overflow | HIGH | 41 | Same as V7 |
| V10 | Button focus relies solely on colour change | HIGH | 27 | No outline, only filter:brightness |
| V11 | Style tags define colour/font properties | HIGH | 16 | Inline `<style>` blocks in templates |
| V12 | Title attribute used | MEDIUM | 15 | `title=` without aria-label alternative |
| V13 | Dialog missing role="dialog" | HIGH | 14 | Native `<dialog>` needs explicit role |
| V14 | Link opens in new window without warning | MEDIUM | 12 | `target="_blank"` missing SR text |
| V15 | Element with onclick not keyboard accessible | HIGH | 13 | Non-button elements with onclick |
| V16 | aria-label doesn't match visible text | MEDIUM | 12 | Language selector, help icons |
| V17 | Accordion lacks ARIA markup | HIGH | 11 | Static report accordions |
| V18 | Heading levels skip | HIGH | 11 | h2→h4, h2→h5 jumps |
| V19 | Empty title attribute | LOW | 10 | `title=""` on elements |
| V20 | Fake list with icon bullets | MEDIUM | 8 | Icon items not in `<ul>/<li>` |
| V21 | Form lacks accessible name | LOW | 7 | `<form>` without aria-label |
| V22 | Interactive element has no accessible name | HIGH | 4 | Inputs without labels |
| V23 | Form input has no label | HIGH | 4 | Missing `<label>` or aria-label |
| V24 | Orphan label (no `for`) | MEDIUM | 3 | `<label>` without `for=` attribute |
| V25 | Duplicate form landmarks | MEDIUM | 3 | Multiple `<form role="form">` |
| V26 | Empty list items | MEDIUM | 3 | `<li>` with only whitespace |
| V27 | Nav lacks aria-current="page" | MEDIUM | 3 | Missing on dropdown items |
| V28 | Infinite spinner animation | HIGH | 2 | `spinner-border` with no pause |
| V29 | Alt text too long | MEDIUM | 2 | Screenshot alt > 150 chars |
| V30 | Nav lacks visual current page indicator | MEDIUM | 2 | No bold/underline on current |
| V31 | Inline style colour/font | LOW | 2 | `style="color:..."` on elements |
| V32 | Title with problematic patterns | MEDIUM | 2 | Vague title text |

### Common Warnings (19 unique)

| # | Description | Impact | Pages |
|---|------------|--------|-------|
| W1 | Spinner runs infinitely | MEDIUM | 74 |
| W2 | setTimeout/setInterval on load | MEDIUM | 74 |
| W3 | Focus outline extends beyond parent | MEDIUM | 72 |
| W4 | Line height 1.20 for 40px font | MEDIUM | 72 |
| W5 | Page missing banner landmark | LOW | 72 |
| W6 | Link has no custom focus styles | MEDIUM | 72 |
| W7 | Link styled as button uses anchor | LOW | 72 |
| W8 | Inline style layout properties | LOW | 19 |
| W9 | Heading over 60 chars | LOW | 11 |
| W10 | Visual hierarchy mismatch | LOW | 11 |
| W11 | Visual hierarchy mismatch (2) | LOW | 10 |
| W12 | Ambiguous tab order | MEDIUM | 7 |
| W13 | Breadcrumb custom bullet | LOW | 3 |
| W14 | Icon font bullets in list | LOW | 3 |
| W15 | Input default focus | MEDIUM | 2 |
| W16 | Required fields not indicated | MEDIUM | 2 |
| W17 | Title contains vague text | LOW | 2 |
| W18 | Heading near 60 char limit | LOW | 11 |
| W19 | Input focus outline exceeds parent | MEDIUM | 2 |

### Component Issues (not in common)

Components with violations: Main Nav (7V), Secondary Nav (6V), Mobile Nav (4V), Breadcrumb x2 (3V each), Latest Test Results (10V), Discovery Items x3 (4V each), Forms x2 (2-3V each), Issues Found (2V), Checks Performance (2V), Test History (2V), Test Coverage (1V), Quick Actions (1V), Test Check Details (1V).

---

## False Positives / Acceptable (EXCLUDED)

These issues require no code changes:

1. **"setTimeout/setInterval on page load" (W2, 74 pages)** — Background polling for test status with clearInterval on completion. Acceptable for monitoring dashboard.
2. **"Tab order ambiguity" (W12, 7 pages)** — Skip link at (0,0) before navbar is correct a11y pattern.
3. **"Icon font bullets in nav list" (W14, 3 pages)** — Bootstrap Icons in `<ul>/<li>` nav with `aria-hidden="true"`. Correct pattern.
4. **"Breadcrumb ::before separator" (W13, 3 pages)** — Bootstrap's standard breadcrumb pattern. Semantic `<ol>` provides structure.
5. **"Link styled as button uses anchor" (W7, 72 pages)** — Informational; addressed by V4 Space key fix.
6. **"Visual hierarchy mismatch" (W10/W11)** — Informational font-size warning. Low priority, no WCAG failure.
7. **"Heading near 60 chars" (W18, 11 pages)** — Informational only; headings under the limit.
8. **"Heading over 60 chars" (W9, 11 pages)** — Mostly dynamic content (page URLs in headings). Acceptable.
9. **"Spinner runs infinitely" (W1, 74 pages)** — Covered by `prefers-reduced-motion` CSS rule. Spinners are status indicators with programmatic completion.
10. **"Input focus outline exceeds parent" (W19, 2 pages)** — Minor cosmetic clipping; outline is still visible.
11. **"Inline style layout properties" (W8, 19 pages)** — Most are dynamic `display:none`, progress bar widths, or responsive sizing that must be inline. Low priority, no accessibility impact.
12. **"Fake list with icon bullets" (V20, 8 pages)** — The issue summary cards in pages/view.html use a `d-flex` layout with icon + text pattern. These are semantically statistics/metrics, not a list. Using `<dl>` (description list) would be over-engineering. The structure is clear with proper headings.

---

## File Structure

### Files to Create
- None (all changes modify existing files)

### Files to Modify (by task)

| File | Tasks |
|------|-------|
| `auto_a11y/web/static/css/style.css` | 1, 3, 7, 8 |
| `auto_a11y/web/static/css/mobile.css` | 1 |
| `auto_a11y/web/static/css/theme-toggle.css` | 7 |
| `auto_a11y/web/static/public/css/tokens.css` | (no changes needed) |
| `auto_a11y/web/templates/base.html` | 1, 2, 4, 5, 8, 9 |
| `auto_a11y/web/templates/auth/login.html` | 6, 10 |
| `auto_a11y/web/templates/auth/register.html` | 6 |
| `auto_a11y/web/templates/auth/forgot_password.html` | 6, 10 |
| `auto_a11y/web/templates/auth/profile.html` | 6, 10, 12 |
| `auto_a11y/web/templates/auth/reset_password.html` | 6 |
| `auto_a11y/web/templates/auth/user_create.html` | 6 |
| `auto_a11y/web/templates/pages/view.html` | 5, 10, 11, 12, 13 |
| `auto_a11y/web/templates/projects/create.html` | 6, 10, 12 |
| `auto_a11y/web/templates/projects/edit.html` | 6, 10, 12 |
| `auto_a11y/web/templates/projects/view.html` | 12 |
| `auto_a11y/web/templates/websites/view.html` | 9, 12 |
| `auto_a11y/web/templates/websites/edit.html` | 9 |
| `auto_a11y/web/templates/testing/dashboard.html` | 10, 11, 12 |
| `auto_a11y/web/templates/testing/configure.html` | 6 |
| `auto_a11y/web/templates/testing/fixture_status.html` | 10, 11 |
| `auto_a11y/web/templates/testing/trends.html` | 11 |
| `auto_a11y/web/templates/reports/dashboard.html` | 10, 12 |
| `auto_a11y/web/templates/schedules/dashboard.html` | 11, 12 |
| `auto_a11y/web/templates/schedules/form.html` | 6 |
| `auto_a11y/web/templates/pages/edit.html` | 6 |
| `auto_a11y/web/templates/drupal_sync/project_sync.html` | 9 |
| `auto_a11y/web/templates/groups/list.html` | 12 |
| `auto_a11y/web/templates/recordings/combined.html` | 9 |
| `auto_a11y/web/templates/recordings/detail.html` | 9 |
| `auto_a11y/web/templates/discovered_pages/view.html` | 9 |
| `auto_a11y/web/templates/static_report/dedup_component.html` | 13 |
| `auto_a11y/web/static/js/issue-filters.js` | 13 |
| `auto_a11y/web/translations/en/common.ftl` | 5, 10, 12 |
| `auto_a11y/web/translations/fr/common.ftl` | 5, 10, 12 |

---

## Task 1: Fix Focus Indicator System (V1, V3, V10, W6)

**Fixes:** V1 (button outline:none), V3 (no visible focus indicator), V10 (colour-only focus), W3 (outline extends beyond parent), W6 (link no custom focus), W15 (input default focus)
**Impact:** 72 pages

**Files:**
- Modify: `auto_a11y/web/static/css/style.css:110-133`
- Modify: `auto_a11y/web/static/css/mobile.css:41,45`

**Why:** The CSS rule `a:focus:not(:focus-visible), button:focus:not(:focus-visible) { outline: none; }` (style.css:129-133) removes focus outlines for non-`:focus-visible` states. Combined with navbar-specific styles that only change text colour on focus, interactive elements lack visible focus indicators. Bootstrap's `.form-control:focus { outline: 0; }` also suppresses form input outlines. The `overflow: hidden` on `.card` in mobile.css clips focus rings.

- [ ] **Step 1: Read current focus styles**

Read `auto_a11y/web/static/css/style.css` lines 100-140 and `auto_a11y/web/static/css/mobile.css` lines 35-50.

- [ ] **Step 2: Replace outline:none with outline-color:transparent**

In `style.css`, change lines 129-133 from:
```css
a:focus:not(:focus-visible),
button:focus:not(:focus-visible) {
    outline: none;
}
```
to:
```css
a:focus:not(:focus-visible),
button:focus:not(:focus-visible) {
    outline-color: transparent;
}
```

This preserves the "no visible outline on mouse click" behaviour for modern browsers while ensuring older browsers that don't support `:focus-visible` still show an outline (they'll ignore the `:not(:focus-visible)` part entirely and not match the rule).

- [ ] **Step 3: Add navbar-specific focus styles**

After the existing `.navbar-dark .nav-link:focus` rule (around line 55), add:
```css
.navbar-dark .nav-link:focus-visible,
.navbar-dark .navbar-brand:focus-visible,
.navbar-dark .dropdown-toggle:focus-visible {
    outline: 2px solid var(--color-text-inverse);
    outline-offset: 2px;
}
```

This ensures focus rings are white (visible against the dark brand background) for all navbar interactive elements.

- [ ] **Step 4: Add form input focus override**

After the existing generic focus rules (around line 133), add:
```css
.form-control:focus-visible,
.form-select:focus-visible,
.form-check-input:focus-visible {
    outline: 2px solid var(--color-focus-ring);
    outline-offset: 2px;
    box-shadow: none;
}
```

This overrides Bootstrap's `box-shadow` focus with a proper `outline` that has higher contrast and works in Windows High Contrast Mode.

- [ ] **Step 5: Fix overflow clipping of focus rings**

In `mobile.css`, change lines 41 and 45 from:
```css
.card { overflow: hidden; }
.card-body { overflow: hidden; }
```
to:
```css
.card { overflow: clip; }
.card-body { overflow: clip; }
```

`overflow: clip` clips content overflow but does NOT clip CSS outlines, allowing focus rings to be visible.

- [ ] **Step 6: Verify in browser**

Open the app. Tab through the navbar, forms, and buttons. Confirm visible focus rings on all interactive elements. Check both light and dark mode.

- [ ] **Step 7: Commit**

```bash
git add auto_a11y/web/static/css/style.css auto_a11y/web/static/css/mobile.css
git commit -m "a11y: fix focus indicator system - replace outline:none, add navbar/form focus styles"
```

---

## Task 2: Fix Skip Link Target (V2)

**Fixes:** V2 (skip link target lacks tabindex)
**Impact:** 72 pages

**Files:**
- Modify: `auto_a11y/web/templates/base.html:202`

- [ ] **Step 1: Read the main element**

Read `auto_a11y/web/templates/base.html` around line 202.

- [ ] **Step 2: Add tabindex="-1" to the main element**

Change:
```html
<main id="main-content" class="container-fluid mt-4">
```
to:
```html
<main id="main-content" tabindex="-1" class="container-fluid mt-4">
```

This ensures that when the skip link is activated, keyboard focus moves to the `<main>` element, not just the scroll position.

- [ ] **Step 3: Commit**

```bash
git add auto_a11y/web/templates/base.html
git commit -m "a11y: add tabindex=-1 to main element for skip link focus management"
```

---

## Task 3: Fix Medium Severity Contrast Failure (V7, V8, V9)

**Fixes:** V7 (normal text 2.77:1), V8 (large text 2.77:1), V9 (overflow text 2.77:1)
**Impact:** 62 pages

**Files:**
- Modify: `auto_a11y/web/static/css/style.css` (btn-medium, severity-header-medium, toast-medium, progress-medium, table-medium, card-header-medium)

**Why:** Dark text (`#212529` / `var(--color-text)`) on `#7d6608` (`var(--color-severity-medium)`) background gives 2.77:1 contrast. White text (`#ffffff` / `var(--color-text-inverse)`) on `#7d6608` gives 5.56:1 which passes AA.

- [ ] **Step 1: Read the medium severity button and related classes**

Read `auto_a11y/web/static/css/style.css` and search for all rules using `--color-severity-medium` as a background colour where the text colour is `var(--color-text)`.

- [ ] **Step 2: Change text colour to white for medium-severity backgrounds**

For each class that sets `background-color: var(--color-severity-medium)` with `color: var(--color-text)`, change the text colour to `var(--color-text-inverse)`:

Classes to update: `.btn-medium`, `.severity-header-medium`, `.toast-medium`, `.progress-medium`, `.bg-medium`, `.table-medium`, `.card-header-medium`.

For `.btn-medium`:
```css
.btn-medium {
    color: var(--color-text-inverse);  /* was var(--color-text) */
    background-color: var(--color-severity-medium);
    border-color: var(--color-severity-medium);
}
```

Apply the same `color: var(--color-text-inverse)` change to `:hover`, `:active`, and `:focus` states for each class.

- [ ] **Step 3: Add dark mode override**

In dark mode, `--color-severity-medium` is `#f9a825` (bright yellow). White text on bright yellow gives ~1.6:1 — catastrophically bad. Dark text on bright yellow is fine. Add a dark-mode-specific override:

```css
[data-bs-theme="dark"] .btn-medium,
[data-bs-theme="dark"] .severity-header-medium,
[data-bs-theme="dark"] .toast-medium,
[data-bs-theme="dark"] .progress-medium,
[data-bs-theme="dark"] .bg-medium,
[data-bs-theme="dark"] .table-medium,
[data-bs-theme="dark"] .card-header-medium {
    color: var(--color-text);  /* dark text on bright yellow in dark mode */
}
```

Check if `style.css` already has dark-mode overrides for `.stat-card.bg-medium` (around lines 1907-1914) and follow the same pattern.

- [ ] **Step 4: Verify in browser**

Open the Dashboard page which has stat cards with medium severity. Verify text is now white on the medium background. Check both light and dark mode. Verify buttons, alerts, badges, toasts, progress bars, and table rows with medium severity are all readable.

- [ ] **Step 5: Commit**

```bash
git add auto_a11y/web/static/css/style.css
git commit -m "a11y: fix contrast failure on medium severity backgrounds - use white text (5.56:1 ratio)"
```

---

## Task 4: Add Banner Landmark and Space Key for Link-Buttons (V4, W5)

**Fixes:** V4 (link-button Space key), W5 (missing banner landmark)
**Impact:** 72 pages

**Files:**
- Modify: `auto_a11y/web/templates/base.html`

- [ ] **Step 1: Read the nav and main structure in base.html**

Read `auto_a11y/web/templates/base.html` around lines 34-40 and 200-210.

- [ ] **Step 2: Wrap the nav in a header element**

Change:
```html
<nav class="navbar navbar-expand-lg navbar-dark bg-brand">
```
to:
```html
<header>
<nav class="navbar navbar-expand-lg navbar-dark bg-brand">
```

And after the closing `</nav>` tag (before `<main>`), add:
```html
</header>
```

This provides the `banner` landmark that assistive technology expects.

- [ ] **Step 3: Add Space key handler for link-buttons**

Add a script in base.html (in the existing `<script>` block at the bottom) that makes `<a class="btn">` elements activate on Space:

```javascript
// Make link-styled-as-button elements respond to Space key (a11y)
document.addEventListener('keydown', function(e) {
    if (e.key === ' ' && e.target.matches('a.btn')) {
        e.preventDefault();
        e.target.click();
    }
});
```

- [ ] **Step 4: Verify**

Tab to any `<a class="btn">` element. Press Space. Verify it activates the link. Verify the `<header>` wraps the navbar in the DOM. Check with a screen reader that "banner" landmark is announced.

- [ ] **Step 5: Commit**

```bash
git add auto_a11y/web/templates/base.html
git commit -m "a11y: add banner landmark, enable Space key for link-styled-as-button elements"
```

---

## Task 5: Fix Auto-Dismiss Notification Timer (V5)

**Fixes:** V5 (time-based content lacks user controls)
**Impact:** 70 pages

**Files:**
- Modify: `auto_a11y/web/templates/base.html:578`

**Why:** The `setTimeout(() => { alertDiv.remove(); }, 5000)` auto-dismisses flash notifications after 5 seconds. WCAG 2.2.1 requires user controls for time-limited content. The existing dismiss button (`data-bs-dismiss="alert"`) provides manual control, but auto-removal violates the timing requirement.

- [ ] **Step 1: Read the notification code**

Read `auto_a11y/web/templates/base.html` around line 570-590.

- [ ] **Step 2: Remove auto-dismiss or extend to 20 seconds**

Option A (remove auto-dismiss entirely — let user dismiss manually):
Remove the `setTimeout` line that calls `alertDiv.remove()`.

Option B (extend significantly — 20 seconds):
Change `5000` to `20000`.

Option A is recommended since the dismiss button already exists.

- [ ] **Step 3: Commit**

```bash
git add auto_a11y/web/templates/base.html
git commit -m "a11y: remove auto-dismiss on flash notifications for WCAG 2.2.1 timing compliance"
```

---

## Task 6: Add Accessible Names to All Forms (V21, V25)

**Fixes:** V21 (form lacks accessible name), V25 (duplicate form landmarks)
**Impact:** 7+ pages

**Files:**
- Modify: `auto_a11y/web/templates/auth/login.html`
- Modify: `auto_a11y/web/templates/auth/register.html`
- Modify: `auto_a11y/web/templates/auth/forgot_password.html`
- Modify: `auto_a11y/web/templates/auth/reset_password.html`
- Modify: `auto_a11y/web/templates/auth/profile.html`
- Modify: `auto_a11y/web/templates/auth/user_create.html`
- Modify: `auto_a11y/web/templates/testing/configure.html`
- Modify: `auto_a11y/web/templates/projects/create.html`
- Modify: `auto_a11y/web/templates/projects/edit.html`
- Modify: `auto_a11y/web/templates/websites/edit.html`
- Modify: `auto_a11y/web/templates/schedules/form.html`
- Modify: `auto_a11y/web/templates/pages/edit.html`

**Why:** `<form>` is a landmark role. When multiple forms exist on a page, each must have a unique accessible name for screen reader navigation.

- [ ] **Step 1: Find all forms without accessible names**

```bash
grep -rn '<form' auto_a11y/web/templates/ --include="*.html" | grep -v 'aria-label'
```

- [ ] **Step 2: Add aria-labelledby to each form**

For each `<form>`, add `aria-labelledby` pointing to the nearest heading above it. If the heading doesn't have an `id`, add one.

Pattern: If the form's section has `<h1 class="mb-4">{{ ftl('auth-login-title') }}</h1>`, add `id="page-heading"` to the h1 and `aria-labelledby="page-heading"` to the form.

For pages with multiple forms (e.g., `auth/profile.html` with profile form and password form), each form gets a distinct `aria-labelledby` pointing to its own card heading.

- [ ] **Step 3: Commit**

```bash
git add auto_a11y/web/templates/
git commit -m "a11y: add aria-labelledby to all form elements for landmark navigation"
```

---

## Task 7: Fix Line Height and Font Size Issues (V6, W4)

**Fixes:** V6 (text below 16px), W4 (line height 1.20)
**Impact:** 63-72 pages

**Files:**
- Modify: `auto_a11y/web/static/css/style.css`
- Modify: `auto_a11y/web/static/css/theme-toggle.css:10`

- [ ] **Step 1: Fix tiny text sizes**

In `style.css`, find `.text-size-tiny` and change from `8px` to `0.75rem` (12px minimum):
```css
.text-size-tiny {
    font-size: 0.75rem;  /* was 8px */
}
```

- [ ] **Step 2: Override Bootstrap heading line-height**

Add to `style.css`:
```css
h1, h2, h3, h4, h5, h6,
.h1, .h2, .h3, .h4, .h5, .h6,
.display-1, .display-2, .display-3,
.display-4, .display-5, .display-6 {
    line-height: 1.4;
}
```

This overrides Bootstrap's default 1.2 heading line-height with 1.4 which is closer to the 1.5 recommendation while maintaining reasonable heading density.

- [ ] **Step 3: Fix theme toggle line-height**

In `theme-toggle.css` line 10, change:
```css
.theme-toggle {
    line-height: 1;
}
```
to:
```css
.theme-toggle {
    line-height: 1.5;
}
```

- [ ] **Step 4: Commit**

```bash
git add auto_a11y/web/static/css/style.css auto_a11y/web/static/css/theme-toggle.css
git commit -m "a11y: fix line height and minimum font sizes for WCAG text spacing compliance"
```

---

## Task 8: Fix Navigation ARIA and Current Page Indicators (V15, V16, V26, V27, V30)

**Fixes:** V15 (onclick not keyboard accessible in nav), V16 (aria-label mismatch), V26 (empty list items), V27 (missing aria-current), V30 (no visual current page)
**Impact:** 3-72 pages (varies by sub-issue)

**Files:**
- Modify: `auto_a11y/web/templates/base.html` (main nav, mobile nav, dropdowns)
- Modify: `auto_a11y/web/static/css/style.css` (current page visual style)

- [ ] **Step 1: Read the full navbar in base.html**

Read `auto_a11y/web/templates/base.html` lines 35-170 (main nav) and lines 217-262 (mobile nav).

- [ ] **Step 2: Add aria-haspopup to dropdown toggles**

For each dropdown toggle button in the navbar, add `aria-haspopup="true"`:
- Testing dropdown toggle (around line 60)
- Reports dropdown toggle (around line 77)
- Language dropdown toggle (around line 98)
- Settings dropdown toggle (around line 108)
- User dropdown toggle (around line 128)

- [ ] **Step 3: Add role="menu" to dropdown menus**

For each `<ul class="dropdown-menu">` inside the navbar, add `role="menu"`. On each `<li>` inside, add `role="none"`. On each `<a class="dropdown-item">`, add `role="menuitem"`. On `<li>` dividers, use `role="separator"`.

- [ ] **Step 4: Add aria-current="page" to all nav items**

Add conditional `aria-current="page"` to each nav link based on the current route. Use `startswith()` patterns for route groups:

For main nav dropdown items:
```html
<a class="dropdown-item" role="menuitem" href="{{ url_for('testing.testing_dashboard') }}"
   {% if request.endpoint and request.endpoint.startswith('testing.') %}aria-current="page"{% endif %}>
```

Apply this pattern to:
- Testing dropdown items (testing.*)
- Reports dropdown items (reports.*)
- Help (help.*)
- Configure (testing.configure_testing)
- Fixture Status (testing.fixture_status)
- Profile (auth.profile)
- User Management (auth.list_users, auth.create_user)
- Groups (groups.*)

For mobile nav, update existing `aria-current` to use `startswith()` patterns instead of exact endpoint matches.

- [ ] **Step 5: Fix language selector aria-label (V16)**

Change the language selector button `aria-label` to include the visible text:
```html
aria-label="{{ ftl('common-language') }}: {{ get_locale().upper() }}"
```

- [ ] **Step 6: Add visual current-page indicator CSS**

Add to `style.css`:
```css
.navbar-dark .nav-link[aria-current="page"],
.navbar-dark .dropdown-item[aria-current="page"] {
    font-weight: 700;
    text-decoration: underline;
    text-underline-offset: 4px;
}
```

- [ ] **Step 7: Commit**

```bash
git add auto_a11y/web/templates/base.html auto_a11y/web/static/css/style.css
git commit -m "a11y: add ARIA menu roles, aria-current page indicators, and visual current-page styling to navigation"
```

---

## Task 9: Fix Missing Breadcrumb aria-current (V27)

**Fixes:** V27 (missing aria-current on breadcrumbs), V30 (no visual current page on breadcrumb)
**Impact:** 6+ templates

**Files:**
- Modify: `auto_a11y/web/templates/websites/view.html:12`
- Modify: `auto_a11y/web/templates/websites/edit.html:15`
- Modify: `auto_a11y/web/templates/recordings/combined.html:14`
- Modify: `auto_a11y/web/templates/recordings/detail.html:15`
- Modify: `auto_a11y/web/templates/discovered_pages/view.html:13`
- Modify: `auto_a11y/web/templates/drupal_sync/project_sync.html:13`

- [ ] **Step 1: Find breadcrumbs missing aria-current**

```bash
grep -rn 'breadcrumb-item active' auto_a11y/web/templates/ | grep -v 'aria-current'
```

- [ ] **Step 2: Add aria-current="page" to each active breadcrumb**

For each `<li class="breadcrumb-item active">`, add `aria-current="page"`:
```html
<li class="breadcrumb-item active" aria-current="page">
```

- [ ] **Step 3: Commit**

```bash
git add auto_a11y/web/templates/
git commit -m "a11y: add aria-current=page to active breadcrumb items"
```

---

## Task 10: Fix Missing Form Labels and Required Indicators (V22, V23, V24, W16)

**Fixes:** V22 (no accessible name), V23 (no label), V24 (orphan label), W16 (required fields not indicated)
**Impact:** 4-15 pages

**Files:**
- Modify: `auto_a11y/web/templates/auth/login.html:36,40`
- Modify: `auto_a11y/web/templates/auth/forgot_password.html:19`
- Modify: `auto_a11y/web/templates/auth/profile.html:56-67`
- Modify: `auto_a11y/web/templates/testing/dashboard.html:122,133`
- Modify: `auto_a11y/web/templates/testing/fixture_status.html:186`
- Modify: `auto_a11y/web/templates/reports/dashboard.html:347,350`
- Modify: `auto_a11y/web/templates/pages/view.html:156,182`
- Modify: `auto_a11y/web/translations/en/common.ftl`
- Modify: `auto_a11y/web/translations/fr/common.ftl`

- [ ] **Step 1: Add required field indicators to login form**

In `auth/login.html`, add `<span class="text-severity-high">*</span>` after the text in labels for email and password fields. Also add a form instruction:
```html
<p class="form-text mb-3"><span class="text-severity-high">*</span> {{ ftl('common-required-field') }}</p>
```

- [ ] **Step 2: Add aria-label to select elements without labels**

In `testing/dashboard.html`:
```html
<select id="projectSelect" class="form-select" aria-label="{{ ftl('common-project') }}">
<select id="websiteSelect" class="form-select" aria-label="{{ ftl('common-website-2') }}">
```

In `testing/fixture_status.html`, add `aria-label` to the search input:
```html
<input type="text" class="form-control" id="search-tests" aria-label="{{ ftl('testing-search-error-codes') }}" placeholder="{{ ftl('testing-search-error-codes') }}">
```

In `reports/dashboard.html`, add individual labels for date range inputs:
```html
<label for="startDate" class="form-label">{{ ftl('reports-start-date') }}</label>
<label for="endDate" class="form-label">{{ ftl('reports-end-date') }}</label>
```

In `pages/view.html`, add `aria-label` to the test-user-select:
```html
<select id="test-user-select" aria-label="{{ ftl('pages-test-as-user') }}">
```

- [ ] **Step 3: Add translation keys**

Add to `en/common.ftl`:
```ftl
common-required-field = Required field
```

Add to `fr/common.ftl`:
```ftl
common-required-field = Champ obligatoire
```

Add additional translation keys for new labels:

In `en/reports.ftl`:
```ftl
reports-start-date = Start date
reports-end-date = End date
reports-generation-progress = Report generation progress
```

In `fr/reports.ftl`:
```ftl
reports-start-date = Date de début
reports-end-date = Date de fin
reports-generation-progress = Progression de la génération du rapport
```

In `en/pages.ftl`:
```ftl
pages-test-as-user = Test as user
pages-page-thumbnail = Page thumbnail
```

In `fr/pages.ftl`:
```ftl
pages-test-as-user = Tester en tant qu'utilisateur
pages-page-thumbnail = Miniature de la page
```

- [ ] **Step 4: Commit**

```bash
git add auto_a11y/web/templates/ auto_a11y/web/translations/
git commit -m "a11y: add missing form labels, required indicators, and aria-labels to form controls"
```

---

## Task 11: Fix Inline Style Colours and Hardcoded CSS (V11, V31)

**Fixes:** V11 (style tags define colour/font), V31 (inline style colour/font)
**Impact:** 16+ pages

**Files:**
- Modify: `auto_a11y/web/templates/testing/dashboard.html` (line 48: `color: #6c757d`)
- Modify: `auto_a11y/web/templates/schedules/dashboard.html` (lines 32-43: hardcoded colours)
- Modify: `auto_a11y/web/templates/testing/fixture_status.html` (lines 63,67: `--bs-*-rgb` refs)
- Modify: `auto_a11y/web/templates/testing/trends.html` (inline style colours)
- Modify: `auto_a11y/web/static/css/style.css` (add utility classes if needed)

**Why:** Inline `<style>` blocks with hardcoded hex colours bypass the design token system, break in dark mode, and prevent user stylesheet overrides.

- [ ] **Step 1: Read each template's inline style block**

Read the `<style>` block at the top of each template listed above.

- [ ] **Step 2: Replace hardcoded colours with token references**

In each template's `<style>` block, replace:
- `#6c757d` → `var(--color-text-muted)`
- `#f8f9fa` → `var(--color-bg-subtle)`
- `#0d6efd` → `var(--color-brand)`
- `#ffc107` → `var(--color-severity-medium)`
- `#dc3545` → `var(--color-severity-high)`
- `rgba(var(--bs-primary-rgb), ...)` → `rgba(var(--color-brand-rgb, 26, 82, 118), ...)`
- `rgba(var(--bs-warning-rgb), ...)` → `rgba(var(--color-severity-medium-rgb, 125, 102, 8), ...)`

Also fix any remaining Bootstrap colour class usage:
- `schedules/dashboard.html` line 250: `btn-outline-success` → `btn-outline-pass`, `btn-outline-secondary` → `btn-outline-neutral`
- `btn-outline-info` in ~13 templates: There is no custom `btn-outline-info` class. Replace with `btn-outline-brand` (for primary info actions) or `btn-info` depending on context. Search with:
```bash
grep -rn 'btn-outline-info\|btn-info' auto_a11y/web/templates/ --include="*.html"
```

- [ ] **Step 3: Commit**

```bash
git add auto_a11y/web/templates/ auto_a11y/web/static/css/style.css
git commit -m "a11y: replace hardcoded colours in inline styles with design token references"
```

---

## Task 12: Fix Title Attributes and Icon-Only Buttons (V12, V16, V19, V32)

**Fixes:** V12 (title attribute used), V16 (aria-label mismatch), V19 (empty title), V32 (vague title)
**Impact:** 15+ pages

**Files:**
- Modify: Multiple templates (see list below)

**Why:** `title` attributes are not reliably exposed to assistive technology. Icon-only buttons using `title` as their sole accessible name are invisible to screen readers. Replace with `aria-label`.

- [ ] **Step 1: Find all icon-only interactive elements with title but no aria-label**

```bash
grep -rn 'title=' auto_a11y/web/templates/ --include="*.html" | grep -v 'aria-label' | grep -E '<a |<button ' | head -40
```

- [ ] **Step 2: Replace title with aria-label on icon-only buttons/links**

For each icon-only button/link pattern like:
```html
<a href="..." class="btn btn-outline-neutral" title="{{ ftl('testing-configure-testing') }}">
    <i class="bi bi-gear" aria-hidden="true"></i>
</a>
```

Change to:
```html
<a href="..." class="btn btn-outline-neutral" aria-label="{{ ftl('testing-configure-testing') }}">
    <i class="bi bi-gear" aria-hidden="true"></i>
</a>
```

Key files with icon-only buttons:
- `testing/dashboard.html`: configure link (~line 146), view buttons (~line 397)
- `schedules/dashboard.html`: toggle, view, run buttons (~lines 246, 252, 257)
- `websites/view.html`: delete page button (~line 401)
- `pages/view.html`: copy XPath buttons (~lines 934, 974, 987, 1019 — also translate these)
- `projects/view.html`: various action buttons

Also add `aria-hidden="true"` to any `<i class="bi bi-*">` icons inside buttons that are missing it.

- [ ] **Step 3: Remove empty title attributes**

```bash
grep -rn 'title=""' auto_a11y/web/templates/ --include="*.html"
```

Remove all `title=""` attributes found.

- [ ] **Step 4: Fix duplicate accessible names**

In `groups/list.html`, remove `aria-label` from the `<table>` since it also has a `<caption>`:
```html
<table class="table table-hover">  <!-- removed aria-label, caption provides name -->
```

- [ ] **Step 5: Commit**

```bash
git add auto_a11y/web/templates/
git commit -m "a11y: replace title attributes with aria-label on icon-only buttons, remove empty titles"
```

---

## Task 13: Fix Static Report Template Issues (V17, V18, V22, V23, V24, V29)

**Fixes:** V17 (accordion ARIA), V18 (heading skip), V22/V23/V24 (form labels), V29 (alt too long)
**Impact:** Static report pages (10+ pages)

**Files:**
- Modify: `auto_a11y/web/templates/static_report/dedup_component.html`
- Modify: `auto_a11y/web/static/js/issue-filters.js`
- Modify: `auto_a11y/web/templates/pages/view.html`

**Why:** The static report templates (rendered as standalone HTML files) have their own issues: the filter form lacks proper labels, the heading hierarchy jumps from h2 to h5, and the quickSearch input has no accessible name. These are generated reports viewed outside the main app, so they need self-contained fixes.

- [ ] **Step 1: Fix heading hierarchy in issue-filters.js**

In `issue-filters.js`, change the filter heading from `<h5>` to `<h3>`:
```javascript
// Change: <h5 class="mb-0"><i class="bi bi-funnel"></i> ${translate('Filter Test Results')}</h5>
// To:     <h3 class="mb-0"><i class="bi bi-funnel" aria-hidden="true"></i> ${translate('Filter Test Results')}</h3>
```

- [ ] **Step 2: Add aria-label to quickSearch input in issue-filters.js**

```javascript
// Add aria-label to the search input:
<input type="text" class="form-control" id="quickSearch"
       aria-label="${translate('Search in issue descriptions, IDs, or code...')}"
       placeholder="${translate('Search in issue descriptions, IDs, or code...')}">
```

- [ ] **Step 3: Add aria-hidden="true" to all icons in issue-filters.js**

Find all `<i class="bi bi-...">` in the JS template literals and add `aria-hidden="true"`.

- [ ] **Step 4: Fix label `for` attributes in dedup_component.html**

Add `for="touchpointFilter"` to the touchpoint filter label. Change labels for button groups (Issue Type, Impact Level) to `<span>` or use `<fieldset>/<legend>`:
```html
<label class="form-label fw-bold" for="touchpointFilter">...</label>
```

For button group labels, change from `<label>` to `<span class="form-label fw-bold" id="typeFilterLabel">` and add `aria-labelledby="typeFilterLabel"` to the button group container.

- [ ] **Step 5: Add focus-visible styles to static report template**

In `dedup_component.html`'s `<style>` block, add:
```css
*:focus-visible {
    outline: 2px solid var(--color-focus-ring, #0d6efd);
    outline-offset: 2px;
}
```

- [ ] **Step 6: Fix alt text length for screenshots**

In `pages/view.html`, change the screenshot alt text to truncate the URL:
```html
alt="{{ ftl('pages-thumbnail-of') }} {{ page.url[:100] }}"
```

Or use a shorter, generic alt text for thumbnails:
```html
alt="{{ ftl('pages-page-thumbnail') }}"
```

Add the translation key if needed.

- [ ] **Step 7: Commit**

```bash
git add auto_a11y/web/templates/static_report/ auto_a11y/web/static/js/issue-filters.js auto_a11y/web/templates/pages/view.html
git commit -m "a11y: fix static report heading hierarchy, form labels, focus styles, and alt text"
```

---

## Task 14: Add role="dialog" to Native Dialog Elements (V13)

**Fixes:** V13 (dialog missing role)
**Impact:** 14 pages

**Files:**
- Modify: All templates with `<dialog>` elements

**Why:** Native `<dialog>` has implicit `dialog` role, but older screen readers may not recognize it. Adding explicit `role="dialog"` is belt-and-suspenders for backwards compatibility.

- [ ] **Step 1: Find all dialog elements**

```bash
grep -rn '<dialog' auto_a11y/web/templates/ | grep -v 'role="dialog"'
```

- [ ] **Step 2: Add role="dialog" to each**

For each `<dialog>` without `role="dialog"`, add it:
```html
<dialog class="modal" id="XModal" aria-labelledby="XModalLabel" aria-modal="true" role="dialog">
```

- [ ] **Step 3: Commit**

```bash
git add auto_a11y/web/templates/
git commit -m "a11y: add explicit role=dialog to native dialog elements for screen reader compatibility"
```

---

## Task 15: Add New Window Warnings to External Links (V14)

**Fixes:** V14 (link opens in new window without warning)
**Impact:** 12 pages

**Files:**
- Modify: `auto_a11y/web/static/css/style.css` (add visually-hidden class if not present)
- Modify: `auto_a11y/web/templates/base.html` (add global JS to inject warning text)
- Modify: `auto_a11y/web/translations/en/common.ftl`
- Modify: `auto_a11y/web/translations/fr/common.ftl`

**Why:** Screen reader users need to know when a link will open in a new window/tab before activating it.

- [ ] **Step 1: Add a global approach via JavaScript in base.html**

Rather than updating ~30 individual links across many templates, add a script in base.html that automatically adds screen-reader-only warning text to all `target="_blank"` links:

```javascript
// Add new-window warning to all external links
document.querySelectorAll('a[target="_blank"]').forEach(function(link) {
    if (!link.querySelector('.visually-hidden') && !link.getAttribute('aria-label')?.includes('new')) {
        var span = document.createElement('span');
        span.className = 'visually-hidden';
        span.textContent = ' (' + (window.i18nNewWindow || 'opens in new tab') + ')';
        link.appendChild(span);
    }
});
```

Add `window.i18nNewWindow = {{ ftl('common-opens-in-new-tab') | tojson }};` in the base template's script initialization.

- [ ] **Step 2: Add translation keys**

In `en/common.ftl`:
```ftl
common-opens-in-new-tab = opens in new tab
```

In `fr/common.ftl`:
```ftl
common-opens-in-new-tab = s'ouvre dans un nouvel onglet
```

- [ ] **Step 3: Commit**

```bash
git add auto_a11y/web/templates/base.html auto_a11y/web/translations/
git commit -m "a11y: add screen reader warning for links that open in new windows"
```

---

## Task 16: Fix Heading Hierarchy in Templates (V18)

**Fixes:** V18 (heading levels skip)
**Impact:** 11 pages

**Files:**
- Modify: `auto_a11y/web/templates/auth/profile.html` (missing h1)
- Modify: `auto_a11y/web/templates/projects/create.html` (h1→h3 skip, if not fixed by previous plan)
- Modify: `auto_a11y/web/templates/projects/edit.html` (h1→h3 skip, if not fixed by previous plan)

- [ ] **Step 1: Add missing h1 to profile page**

In `auth/profile.html`, add an `<h1>` before the content:
```html
<h1>{{ ftl('common-profile') }}</h1>
```

Then change the existing `<h2>` card headers to remain as `<h2>` (which is now correct under the new `<h1>`).

- [ ] **Step 2: Verify projects create/edit heading hierarchy**

Check if the previous plan's Task 4 (legend heading fix) has been applied. If `<legend><h3 class="h5">` already changed to `<legend><h2 class="h5">`, verify the hierarchy is h1→h2. If not, apply that fix now.

- [ ] **Step 3: Commit**

```bash
git add auto_a11y/web/templates/
git commit -m "a11y: fix heading hierarchy - add missing h1, correct heading level sequences"
```

---

## Task 17: Add Spinner Accessible Text and Progress Labels (V28)

**Fixes:** V28 (infinite spinner without controls)
**Impact:** 2-10 pages

**Files:**
- Modify: `auto_a11y/web/templates/pages/view.html:207,225,230`
- Modify: `auto_a11y/web/templates/reports/dashboard.html:210,496`

- [ ] **Step 1: Add visually-hidden text to spinners**

For each `<div class="spinner-border" role="status">` without a child `<span class="visually-hidden">`, add one:
```html
<div class="spinner-border spinner-border-sm me-2" role="status">
    <span class="visually-hidden">{{ ftl('common-loading') }}</span>
</div>
```

- [ ] **Step 2: Add aria-label to progress elements**

In `reports/dashboard.html`, add `aria-label` to `<progress>` elements:
```html
<progress value="..." max="..." aria-label="{{ ftl('reports-generation-progress') }}"></progress>
```

Add translation keys if needed.

- [ ] **Step 3: Commit**

```bash
git add auto_a11y/web/templates/
git commit -m "a11y: add accessible text to spinners and labels to progress elements"
```

---

## Task 18: Fix Table Header Scope Attributes

**Fixes:** Component issue `tables_ErrHeaderMissingScope`
**Impact:** 10 pages

**Files:**
- Modify: `auto_a11y/web/templates/pages/view.html:588,597,598,599`

- [ ] **Step 1: Read the table footer**

Read `pages/view.html` around lines 580-600.

- [ ] **Step 2: Fix tfoot th elements**

The `<tfoot>` row has `<th>` elements that should either have `scope="col"` or be changed to `<td>` since they're summary data, not headers. The "Totals" label cell should keep `scope="row"`. Change the data cells to `<td>`:

```html
<tfoot>
<tr>
    <th scope="row">Totals</th>
    <td class="text-center">{{ total_tested }}</td>
    <td class="text-center text-severity-pass">{{ total_passed }}</td>
    <td class="text-center text-severity-high">{{ total_failed }}</td>
    <td class="text-end">...</td>
</tr>
</tfoot>
```

- [ ] **Step 3: Commit**

```bash
git add auto_a11y/web/templates/pages/view.html
git commit -m "a11y: fix table footer cells - change data th to td, keep scope on row header"
```

---

## Execution Order

Tasks are ordered by impact (number of pages affected × severity):

| Priority | Task | Pages Fixed | Severity |
|----------|------|-------------|----------|
| 1 | **Task 1** — Focus indicator system | 72 | CRITICAL |
| 2 | **Task 2** — Skip link tabindex | 72 | HIGH |
| 3 | **Task 3** — Medium contrast fix | 62 | HIGH |
| 4 | **Task 4** — Banner landmark + Space key | 72 | MEDIUM |
| 5 | **Task 5** — Auto-dismiss timer | 70 | HIGH |
| 6 | **Task 7** — Line height + font sizes | 63 | MEDIUM |
| 7 | **Task 8** — Navigation ARIA + current page | 72 | HIGH |
| 8 | **Task 9** — Breadcrumb aria-current | 6 | MEDIUM |
| 9 | **Task 6** — Form accessible names | 7+ | MEDIUM |
| 10 | **Task 10** — Form labels + required | 15 | HIGH |
| 11 | **Task 14** — Dialog role | 14 | MEDIUM |
| 12 | **Task 15** — New window warnings | 12 | MEDIUM |
| 13 | **Task 12** — Title → aria-label | 15 | MEDIUM |
| 14 | **Task 13** — Static report fixes | 10 | MEDIUM |
| 15 | **Task 11** — Inline style colours | 16 | LOW |
| 16 | **Task 16** — Heading hierarchy | 11 | MEDIUM |
| 17 | **Task 17** — Spinner/progress labels | 10 | LOW |
| 18 | **Task 18** — Table header scope | 10 | LOW |

**Parallelizable groups (no shared files):**
- Group A: Task 1 (style.css + mobile.css), Task 2 (base.html only), Task 7 (style.css + theme-toggle.css) — **Task 1 and 7 share style.css, run sequentially**; Task 2 can run in parallel with either
- Group B: Task 6 (auth templates), Task 9 (breadcrumb templates), Task 14 (dialog templates), Task 18 (pages/view.html table) — all touch different files, can run in parallel
- Group C: Tasks 3, 4, 5, 8 all modify base.html and/or style.css — **must run sequentially**
- Group D: Tasks 10, 12, 13, 15, 16, 17 touch different template groups — can mostly run in parallel (but check for overlapping files like pages/view.html)

**Recommended sequential order for shared-file tasks:** 1 → 3 → 7 (all modify style.css), then 2 → 4 → 5 → 8 (all modify base.html)
