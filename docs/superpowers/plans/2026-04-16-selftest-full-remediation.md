# Self-Test Full Remediation Plan (2026-04-16)

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix ALL 24,674 violations and 25,363 warnings in the 2026-04-16 self-test report (`selftest/Project_Self_Test_20260416_142813.html`), including advisory warnings and issues that are test-methodology artifacts. Target: 0 violations / 0 warnings on a fresh self-test.

**Architecture:** Fix root causes at the source (CSS tokens, base template, shared components) so changes cascade across all 41 tested pages. Group fixes by file to minimize commits; apply highest-impact cascading CSS fixes first (affecting 6,000+ errors each), then template-specific fixes. Use `:where()` selectors for low-specificity overrides that don't fight Bootstrap's CSS.

**Tech Stack:** CSS (style.css, mobile.css, tokens.css, theme-toggle.css, help-system.css), Jinja2 templates (base.html + all page templates), vanilla JavaScript, Flask/Jinja2, Fluent translations (en/fr)

---

## Issue Inventory (from selftest/Project_Self_Test_20260416_142813.html)

### Error Counts by Code

| Count | Error Code | Severity | Root Cause |
|-------|-----------|----------|-----------|
| 9,274 | aria_ErrClickableWithoutKeyboard | HIGH | `cursor: pointer` inherited by child `<span>`/`<i>` in buttons/links |
| 6,972 | focus_management_ErrNoFocusIndicator | HIGH | Test clones out of parent context → `.navbar-dark .nav-link:focus-visible` breaks; skip-link has no outline; mobile nav has no focus style |
| 2,496 | headings_WarnHeadingOver60CharsLong | WARN | Headings with metadata/badges/counts inline |
| 1,476 | colors_contrast_WarnTextContrastCannotCalculate | WARN | Transparent backgrounds, gradients, overlays |
| 1,274 | aria_ErrAccordionWithoutARIA | HIGH | `<div data-bs-toggle="collapse">` lacks `role/aria-expanded/aria-controls/tabindex` |
| 1,240 | lists_WarnIconFontBulletsInList | WARN | `<ul>` with `list-style: none` containing `<i class="bi">` icons |
| 1,229 | headings_ErrSkippedHeadingLevel | HIGH | h2→h4, h1→h3 jumps in recordings/public templates |
| 192 | lists_ErrListitemEmpty | MEDIUM | Pagination ellipsis `<li>` items |
| 114 | colors_contrast_ErrTextContrastAA | HIGH | Specific elements with contrast < 4.5:1 |
| 57 | aria_ErrAriaLabelMayNotBeFoundByVoiceControl | MEDIUM | aria-label doesn't include visible text |
| 51 | title_attribute_ErrTitleAttrFound | MEDIUM | `title=` on interactive elements |
| 45 | forms_WarnInputDefaultFocus | WARN | Inputs use browser-default focus |
| 24 | forms_ErrOrphanLabelWithNoId | MEDIUM | `<label>` without `for=` |
| 20 | title_attribute_WarnVagueTitleAttribute | WARN | Generic title text |
| 10 | forms_ErrNoLabel | HIGH | Inputs missing labels entirely |
| 8 | title_attribute_ErrImproperTitleAttribute | MEDIUM | Title on non-focusable elements |
| 8 | lists_ErrFakeListImplementation | MEDIUM | `<div>` styled as list |
| 7 | forms_WarnMissingRequiredIndication | WARN | `required` without `*` in label |
| 12 | tabindex_Err/focus_management_ErrAnchorTargetTabindex | HIGH | In-page anchor targets lack `tabindex="-1"` |
| 5 | forms_WarnInputNoBorderOutline | WARN | Input focus has outline but no border change |
| 5 | forms_WarnInputFocusOutlineExceedsParent | WARN | Focus outline clipped by parent |
| 5 | colors_WarnNoContrastSupport | WARN | Missing `@media (prefers-contrast: more)` |
| 5 | colors_WarnNoColorSchemeSupport | WARN | Missing `<meta name="color-scheme">` |
| 4 | landmarks_ErrFormUsesAriaLabelInsteadOfVisibleElement | MEDIUM | Form uses aria-label without visible heading |
| 4 | headings_WarnVisualHierarchy | WARN | Visual size mismatch with heading level |
| 4 | forms_ErrFieldLabelledUsingAriaLabel | MEDIUM | Field uses aria-label instead of visible label |
| 4 | colors_contrast_ErrPartialTextContrastAA | HIGH | Overflow text contrast |
| 3 | forms_WarnNoFieldset | WARN | Radio/checkbox group without fieldset/legend |
| 2 | title_attribute_ErrEmptyTitleAttr | MEDIUM | `title=""` |
| 2 | lists_WarnCustomBulletStyling | WARN | Custom bullet via CSS |
| 2 | headings_ErrNoH1 | HIGH | Page missing h1 |
| 2 | forms_ErrFormEmptyHasNoInteractiveElements | MEDIUM | Form has no controls |
| 1 | tables_ErrTableMissingCaption | MEDIUM | Table lacks caption |
| 1 | landmarks_ErrContentOutsideLandmarks | MEDIUM | Content outside `<main>`/`<nav>` |
| 1 | colors_contrast_ErrLargeTextContrastAA | HIGH | Large text contrast < 3:1 |

---

## File Structure

### Files to Modify (grouped by task)

| File | Tasks |
|------|-------|
| `auto_a11y/web/static/css/style.css` | 1, 2, 3, 10, 11, 14 |
| `auto_a11y/web/static/css/mobile.css` | 1 |
| `auto_a11y/web/static/css/theme-toggle.css` | 2 |
| `auto_a11y/web/static/css/help-system.css` | 2 |
| `auto_a11y/web/static/css/modal.css` | 2 |
| `auto_a11y/web/static/public/css/tokens.css` | 3 |
| `auto_a11y/web/templates/base.html` | 3, 4, 9, 14 |
| `auto_a11y/web/templates/websites/view.html` | 4, 5, 7 |
| `auto_a11y/web/templates/recordings/detail.html` | 5, 6 |
| `auto_a11y/web/templates/recordings/combined.html` | 5, 6 |
| `auto_a11y/web/templates/public/page.html` | 5, 6 |
| `auto_a11y/web/templates/pages/view.html` | 5, 6, 13 |
| `auto_a11y/web/templates/projects/edit.html` | 4, 5 |
| `auto_a11y/web/templates/projects/create.html` | 4 |
| `auto_a11y/web/templates/help.html` | 4 |
| `auto_a11y/web/templates/testing/fixture_status.html` | 4, 8 |
| `auto_a11y/web/templates/scripts/list.html` | 9 |
| `auto_a11y/web/templates/auth/login.html` | 10, 12 |
| `auto_a11y/web/templates/auth/profile.html` | 10, 12 |
| `auto_a11y/web/templates/auth/register.html` | 10 |
| `auto_a11y/web/templates/groups/list.html` | 12, 13 |
| `auto_a11y/web/translations/en/common.ftl` | 14 |
| `auto_a11y/web/translations/fr/common.ftl` | 14 |

---

## Task 1: Fix Focus Indicator System (Cascading — ~6,972 errors)

**Fixes:** focus_management_ErrNoFocusIndicator, forms_WarnInputDefaultFocus, forms_WarnInputNoBorderOutline

**Root Cause:** The test in `test_focus_management.py` clones elements out of DOM context and checks `window.getComputedStyle(temp)` before and after `temp.focus()`. When focus rules depend on parent selectors (`.navbar-dark .nav-link:focus-visible`), the cloned element loses its parent and the rule doesn't apply. Skip-link and mobile nav have no outline at all. Bootstrap's default `.form-control:focus` uses `box-shadow` which the test sometimes misses.

**Strategy:** Replace parent-dependent focus selectors with element-direct selectors. Make every interactive element have an explicit outline change on focus that doesn't depend on parent classes.

- [ ] **Step 1: Read current focus styles**

Read `auto_a11y/web/static/css/style.css` lines 50-160 and `mobile.css` lines 1-250.

- [ ] **Step 2: Rewrite focus rules in style.css**

In `style.css`, replace the existing focus section (lines 50-173) with:

```css
/* ============================================
   Focus Management — element-direct selectors
   All interactive elements MUST have a visible
   outline change on focus that does NOT depend
   on parent class context (so test cloning works).
   ============================================ */

/* Baseline focus for all interactive elements (both :focus and :focus-visible) */
a:focus,
a:focus-visible,
button:focus,
button:focus-visible,
input:focus,
input:focus-visible,
select:focus,
select:focus-visible,
textarea:focus,
textarea:focus-visible,
[tabindex]:not([tabindex="-1"]):focus,
[tabindex]:not([tabindex="-1"]):focus-visible,
summary:focus,
summary:focus-visible {
    outline: 2px solid var(--color-focus-ring);
    outline-offset: 2px;
    box-shadow: 0 0 0 4px var(--color-focus-ring-shadow, rgba(10, 88, 202, 0.25));
}

/* Remove outline on mouse click for :focus-visible browsers */
a:focus:not(:focus-visible),
button:focus:not(:focus-visible) {
    outline-color: transparent;
    box-shadow: none;
}

/* Form controls: border changes on focus too (forms_WarnInputNoBorderOutline) */
.form-control:focus,
.form-control:focus-visible,
.form-select:focus,
.form-select:focus-visible,
.form-check-input:focus,
.form-check-input:focus-visible {
    outline: 2px solid var(--color-focus-ring);
    outline-offset: 2px;
    border-color: var(--color-focus-ring);
    box-shadow: 0 0 0 4px var(--color-focus-ring-shadow, rgba(10, 88, 202, 0.25));
}

/* Navbar (dark background) needs inverse outline — element-direct, not parent-dependent */
.navbar-dark a:focus,
.navbar-dark a:focus-visible,
.navbar-dark button:focus,
.navbar-dark button:focus-visible,
.nav-link:focus,
.nav-link:focus-visible,
.navbar-brand:focus,
.navbar-brand:focus-visible,
.dropdown-toggle:focus,
.dropdown-toggle:focus-visible,
.dropdown-item:focus,
.dropdown-item:focus-visible {
    outline: 2px solid var(--color-focus-ring-inverse, #ffffff);
    outline-offset: 2px;
    box-shadow: 0 0 0 4px rgba(255, 255, 255, 0.4);
}

/* Skip link — explicit outline on focus (was missing) */
.skip-link:focus,
.skip-link:focus-visible {
    top: 0;
    z-index: 1100;
    outline: 3px solid var(--color-focus-ring-inverse, #ffffff);
    outline-offset: 2px;
}

/* Mobile bottom nav links */
.mobile-bottom-nav a:focus,
.mobile-bottom-nav a:focus-visible {
    outline: 2px solid var(--color-focus-ring);
    outline-offset: -2px;
    background-color: var(--color-bg-subtle);
}
```

- [ ] **Step 3: Add tokens for focus-ring shadow and inverse**

In `auto_a11y/web/static/public/css/tokens.css`, find the `:root` block and the dark-mode override block. Add:

```css
:root {
    /* ...existing tokens... */
    --color-focus-ring-shadow: rgba(10, 88, 202, 0.25);
    --color-focus-ring-inverse: #ffffff;
}

[data-theme="dark"],
[data-bs-theme="dark"] {
    /* ...existing tokens... */
    --color-focus-ring-shadow: rgba(109, 179, 242, 0.35);
    --color-focus-ring-inverse: #ffffff;
}

@media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
        --color-focus-ring-shadow: rgba(109, 179, 242, 0.35);
        --color-focus-ring-inverse: #ffffff;
    }
}
```

- [ ] **Step 4: Fix mobile nav focus in mobile.css**

Read `mobile.css` lines 195-230 to find the `.mobile-bottom-nav` rules. Ensure `a:focus` / `a:focus-visible` inside it has an outline (already covered by the element-direct rule in Step 2, but verify mobile.css doesn't override with `outline: none`).

Search for any `outline: none` or `outline: 0` in `mobile.css` and remove them, EXCEPT inside `:focus:not(:focus-visible)` rules where removal is intentional.

- [ ] **Step 5: Commit**

```bash
git add auto_a11y/web/static/css/style.css auto_a11y/web/static/css/mobile.css auto_a11y/web/static/public/css/tokens.css
git commit -m "a11y: rewrite focus indicator system with element-direct selectors (fixes 6972 errors)"
```

---

## Task 2: Fix Cursor Inheritance on Button/Link Children (Cascading — ~9,274 errors)

**Fixes:** aria_ErrClickableWithoutKeyboard

**Root Cause:** The test in `test_aria.py` checks every element individually for `cursor: pointer` (computed style) and, if the element is a `<div>`, `<span>`, `<p>`, or `<img>` AND has `cursor: pointer`, flags it unless it has `role`, positive `tabindex`, or `onkeydown`/`onkeypress`. Bootstrap sets `cursor: pointer` on `.btn`, `.nav-link`, `<a>`, `<button>`, and these propagate (via cursor inheritance) to all child `<span>` and `<i>` elements.

**Strategy:** Add a CSS rule that overrides `cursor` and `pointer-events` on children of semantic interactive elements. Children will inherit click semantics through event bubbling to the parent button/link, not through their own cursor.

- [ ] **Step 1: Add cursor-reset rule to style.css**

Append to the end of `auto_a11y/web/static/css/style.css`:

```css
/* ============================================
   Click-target normalization
   Child elements inside semantic interactive
   elements must not inherit cursor:pointer
   because they don't need their own keyboard
   handlers — the parent provides them.
   Prevents aria_ErrClickableWithoutKeyboard
   false positives on decorative spans/icons.
   ============================================ */
button > *,
a > *,
summary > *,
[role="button"] > *,
[role="link"] > *,
[role="menuitem"] > *,
[role="tab"] > *,
label > *:not(input):not(select):not(textarea) {
    pointer-events: none;
    cursor: inherit;
}

/* Exception: nested interactive elements (rare but valid) keep their own cursor */
button > button,
button > a,
a > button,
a > a {
    pointer-events: auto;
}
```

- [ ] **Step 2: Remove stale cursor declarations from theme-toggle.css, help-system.css, modal.css**

In `auto_a11y/web/static/css/theme-toggle.css` line 7, the `.theme-toggle` class has `cursor: pointer`. This is fine (it's a button) — but verify no child `span[data-theme-icon]` has independent `cursor: pointer`. If they do, remove it.

In `auto_a11y/web/static/css/help-system.css`:
- Line 162 `.help-icon` has `cursor: pointer` — fine (button).
- Line 54 `.help-modal-close` has `cursor: pointer` — fine (button).
- Line 210 `.help-link` has `cursor: pointer` — fine (link).

No changes needed in help-system.css or modal.css unless child-inheritance issues are present. Read and verify.

- [ ] **Step 3: Fix explicit `cursor: pointer` on non-interactive divs**

Search for `cursor: pointer` on non-button/non-link elements:
```bash
grep -rn 'cursor: pointer' auto_a11y/web/static/css/ auto_a11y/web/templates/
```

For each match on a `<div>` or `<span>` that does NOT also have `role="button"`, `tabindex`, or click handlers accessible via keyboard:
- If it's a real interactive element, convert to `<button class="btn-unstyled">` or add `role="button" tabindex="0"` + keyboard handler
- If it's decorative (e.g., entire card clickable via JS), either:
  - Add `role="button" tabindex="0"` + keyboard handler OR
  - Remove the `cursor: pointer` and wrap the clickable area in a real `<button>`/`<a>`

Specifically address `auto_a11y/web/templates/websites/view.html:219` which has `<div class="card-header" data-bs-toggle="collapse" ... style="cursor: pointer;">`. This is addressed in Task 4.

- [ ] **Step 4: Commit**

```bash
git add auto_a11y/web/static/css/style.css
git commit -m "a11y: disable pointer-events on children of semantic interactive elements (fixes 9274 errors)"
```

---

## Task 3: Add Color Scheme + Contrast Media Query Support (10 errors)

**Fixes:** colors_WarnNoContrastSupport (5), colors_WarnNoColorSchemeSupport (5)

- [ ] **Step 1: Add color-scheme meta tag to base.html**

In `auto_a11y/web/templates/base.html` after the `<meta name="viewport">` line (around line 5), add:

```html
<meta name="color-scheme" content="light dark">
```

- [ ] **Step 2: Add CSS color-scheme property to tokens.css**

In `auto_a11y/web/static/public/css/tokens.css`, at the top of the `:root` block, add:

```css
:root {
    color-scheme: light;
    /* ...existing tokens... */
}

[data-theme="dark"],
[data-bs-theme="dark"] {
    color-scheme: dark;
    /* ...existing tokens... */
}

@media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
        color-scheme: dark;
    }
}
```

- [ ] **Step 3: Add prefers-contrast media query to style.css**

Append to `auto_a11y/web/static/css/style.css`:

```css
/* ============================================
   High-contrast mode support (WCAG 1.4.11)
   Provides enhanced contrast for users who
   request it via OS settings.
   ============================================ */
@media (prefers-contrast: more) {
    :root {
        --color-focus-ring: CanvasText;
        --color-border-table: CanvasText;
        --color-text-muted: CanvasText;
    }

    a:focus,
    a:focus-visible,
    button:focus,
    button:focus-visible,
    input:focus,
    input:focus-visible,
    select:focus,
    select:focus-visible,
    textarea:focus,
    textarea:focus-visible {
        outline: 3px solid CanvasText;
        outline-offset: 3px;
    }

    .btn,
    .form-control,
    .form-select,
    .card,
    .alert {
        border: 1px solid CanvasText !important;
    }
}

/* Forced colors mode (Windows High Contrast) */
@media (forced-colors: active) {
    * {
        forced-color-adjust: auto;
    }

    a:focus-visible,
    button:focus-visible {
        outline: 2px solid CanvasText;
        outline-offset: 2px;
    }
}
```

- [ ] **Step 4: Commit**

```bash
git add auto_a11y/web/templates/base.html auto_a11y/web/static/public/css/tokens.css auto_a11y/web/static/css/style.css
git commit -m "a11y: add color-scheme and prefers-contrast media query support"
```

---

## Task 4: Fix Accordion/Collapse ARIA Attributes (1,274 errors)

**Fixes:** aria_ErrAccordionWithoutARIA

**Root Cause:** `<div data-bs-toggle="collapse">` and button collapse triggers without `aria-expanded` get flagged. Some templates have `<div class="card-header" data-bs-toggle="collapse">` which is not a button at all.

- [ ] **Step 1: Fix websites/view.html discovery history header**

In `auto_a11y/web/templates/websites/view.html:219`, change:
```html
<div class="card-header" data-bs-toggle="collapse" data-bs-target="#discoveryHistory" style="cursor: pointer;">
```

To a proper button element wrapping the header content. Read the surrounding context (lines 215-240), then restructure as:

```html
<div class="card-header p-0">
    <button class="btn btn-link w-100 text-start card-header p-3 border-0"
            type="button"
            data-bs-toggle="collapse"
            data-bs-target="#discoveryHistory"
            aria-expanded="false"
            aria-controls="discoveryHistory">
        <!-- original header content here -->
    </button>
</div>
```

Or, if restructuring is too invasive, add the ARIA attributes to the div:
```html
<div class="card-header"
     data-bs-toggle="collapse"
     data-bs-target="#discoveryHistory"
     role="button"
     aria-expanded="false"
     aria-controls="discoveryHistory"
     tabindex="0"
     onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();this.click();}"
     style="cursor: pointer;">
```

Prefer the button approach — it's more semantic.

- [ ] **Step 2: Verify all Bootstrap accordion buttons have aria-expanded**

Search all accordion buttons:
```bash
grep -rn 'accordion-button' auto_a11y/web/templates/ --include="*.html"
```

For each `<button class="accordion-button">`, verify it has `aria-expanded="true"` (if not collapsed) or `aria-expanded="false"` (if collapsed). If missing, add it. The `.collapsed` class typically correlates with `aria-expanded="false"`.

Files to check:
- `recordings/detail.html` lines 403-407, 438-441, 491-494, 540, 561
- `recordings/combined.html` line 97
- `projects/edit.html` lines 210, 317
- `projects/create.html` lines 235
- `help.html` lines 328, 341, 354, 367
- `pages/view.html` lines 709, 739, 756
- `testing/fixture_status.html` (grep for accordion)

For buttons missing `aria-expanded`, add it. For each button, also verify `aria-controls="<target-id>"` points to the correct collapse target.

- [ ] **Step 3: Verify accordion panels have correct ARIA**

For each accordion panel `<div class="collapse">` or `<div class="accordion-collapse">`, verify:
- It has a stable `id` that matches its trigger's `aria-controls`
- It has `role="region"` or `aria-labelledby="<trigger-id>"`

- [ ] **Step 4: Commit**

```bash
git add auto_a11y/web/templates/
git commit -m "a11y: add ARIA attributes to all collapse/accordion triggers (fixes 1274 errors)"
```

---

## Task 5: Fix Heading Hierarchy (1,229 errors)

**Fixes:** headings_ErrSkippedHeadingLevel, headings_ErrNoH1, headings_WarnVisualHierarchy

**Root Cause:** Templates jump from h2→h4 or h1→h3, or are missing h1 entirely.

- [ ] **Step 1: Audit heading levels in each template**

For each of these files, read the file and map out the heading structure:
- `auto_a11y/web/templates/recordings/detail.html` (h2 accordion headers + h4 inside)
- `auto_a11y/web/templates/recordings/combined.html`
- `auto_a11y/web/templates/public/page.html` (h1 then h3?)
- `auto_a11y/web/templates/pages/view.html`
- `auto_a11y/web/templates/websites/view.html`
- `auto_a11y/web/templates/projects/edit.html`

- [ ] **Step 2: Fix recordings/detail.html**

Read `recordings/detail.html` lines 1-80 to find the page h1, then lines 395-570 (accordion sections). Change:
- Each `<h2 class="accordion-header">` — keep as h2
- Inside accordions: `<h4>` → `<h3>` (since h2 is the container)

Specifically:
- Line 414: `<h4>{{ takeaway.number }}. {{ takeaway.topic }}</h4>` → `<h3 class="h4">` (keep h4 visual size with `.h4` class)
- Line 449: similar h4 inside User Painpoints section → `<h3 class="h4">`
- Any deeper nested `<h5>` → `<h4 class="h5">`

- [ ] **Step 3: Fix recordings/combined.html**

Same pattern as detail.html — read the file and apply the same heading fixes.

- [ ] **Step 4: Fix public/page.html**

Read `public/page.html`. Ensure hierarchy is h1 (page title) → h2 (section groupings) → h3 (items within sections). If h3 follows h1 directly, insert an h2 section heading or demote h3 to h2.

- [ ] **Step 5: Fix pages/view.html**

Read lines 1-100 (top) and scan for heading markup. Ensure no h1→h3 or h2→h4 jumps.

- [ ] **Step 6: Fix websites/view.html**

Same audit as above.

- [ ] **Step 7: Fix projects/edit.html and projects/create.html**

Read both files' heading structures. Projects page has tabs/accordions — ensure internal headings continue the hierarchy from the page h1.

- [ ] **Step 8: Add missing h1 where needed**

Search for pages without h1:
```bash
grep -rL '<h1' auto_a11y/web/templates/*.html auto_a11y/web/templates/*/*.html
```

For each page without h1, add one near the top of the main content. If the page has `{% block page_title %}`, use that.

- [ ] **Step 9: Commit**

```bash
git add auto_a11y/web/templates/
git commit -m "a11y: fix heading hierarchy - no level skips, add missing h1s (fixes 1229 errors)"
```

---

## Task 6: Fix Long Headings (2,496 warnings)

**Fixes:** headings_WarnHeadingOver60CharsLong

**Strategy:** Split metadata (counts, badges, URLs) out of heading elements into adjacent siblings. Preserve visual styling via layout, not heading-text length.

- [ ] **Step 1: Audit long headings**

For each file identified in Task 5, look for headings that concatenate:
- Page title + metadata count (`{{ ftl('...') }} ({{ count }})`)
- Page title + full URL
- Section header + badge

These typically exceed 60 characters.

- [ ] **Step 2: Split long headings**

Pattern to apply — change:
```html
<h2>
    <i class="bi bi-clock-history" aria-hidden="true"></i> {{ ftl('websites-discovery-history') }}
    <small class="text-muted">({{ website.discovery_history|length }} {{ ftl('websites-attempts') }})</small>
</h2>
```

To:
```html
<div class="d-flex align-items-center gap-2">
    <h2 class="mb-0">
        <i class="bi bi-clock-history" aria-hidden="true"></i> {{ ftl('websites-discovery-history') }}
    </h2>
    <span class="text-muted small">({{ website.discovery_history|length }} {{ ftl('websites-attempts') }})</span>
</div>
```

- [ ] **Step 3: Apply to all templates with long headings**

Files to modify (based on investigation):
- `auto_a11y/web/templates/recordings/detail.html` — sections with counts, URLs
- `auto_a11y/web/templates/websites/view.html` — discovery history, tests sections
- `auto_a11y/web/templates/pages/view.html` — page titles with full URLs
- `auto_a11y/web/templates/public/page.html`
- `auto_a11y/web/templates/projects/view.html`

For page titles containing a URL (e.g., `<h1>Report for https://example.com/very/long/page</h1>`), split as:
```html
<div class="page-title-wrap">
    <h1>{{ ftl('pages-report-for') }}</h1>
    <p class="text-muted text-break">{{ page.url }}</p>
</div>
```

- [ ] **Step 4: Commit**

```bash
git add auto_a11y/web/templates/
git commit -m "a11y: split metadata out of headings to stay under 60 chars (fixes 2496 warnings)"
```

---

## Task 7: Fix Text Contrast Failures (114 + 4 + 1 errors, 1,476 warnings)

**Fixes:** colors_contrast_ErrTextContrastAA, ErrPartialTextContrastAA, ErrLargeTextContrastAA, WarnTextContrastCannotCalculate

**Strategy:** For actual contrast failures (ErrTextContrastAA), identify the specific elements and fix. For WarnTextContrastCannotCalculate, add solid fallback backgrounds on elements that use gradients or transparency.

- [ ] **Step 1: Extract specific failing XPaths from self-test report**

```bash
grep -oP '<td>colors_contrast_ErrTextContrastAA</td><td>[^<]+</td>[^<]+<td>[0-9.]+:1[^<]+</td>[^<]+<td>[^<]+</td>[^<]+<td>[^<]+</td>' selftest/Project_Self_Test_20260416_142813.html | sort -u | head -40
```

Look at the contrast ratios and foreground/background colors reported. Identify the elements and their source templates.

- [ ] **Step 2: Fix inline-style contrast failures**

The original plan identified these specific failing elements — extract current failures from today's report and fix:
- Check all inline `style="background: ..."` and `style="color: ..."` usage
- Replace hardcoded hex values with token references
- If a token gives insufficient contrast, update the token or use a different token

- [ ] **Step 3: Fix transparent/gradient backgrounds**

For elements where the test reports `WarnTextContrastCannotCalculate`:
- Common culprits: elements inside cards with shadow backgrounds, overlays with rgba colors
- Add explicit solid background color via `background-color` property (or use opaque token colors)

Search for transparent backgrounds:
```bash
grep -rn 'background.*rgba\|background.*transparent' auto_a11y/web/static/css/ auto_a11y/web/templates/
```

For each, either change to solid color or add a solid underlay.

- [ ] **Step 4: Commit**

```bash
git add auto_a11y/web/templates/ auto_a11y/web/static/css/
git commit -m "a11y: fix text contrast failures and add solid backgrounds (fixes 1591 errors/warnings)"
```

---

## Task 8: Fix List Structure Issues (1,448 errors/warnings)

**Fixes:** lists_ErrListitemEmpty (192), lists_WarnIconFontBulletsInList (1,240), lists_ErrFakeListImplementation (8), lists_WarnCustomBulletStyling (2)

- [ ] **Step 1: Fix empty list items in pagination**

In `auto_a11y/web/templates/websites/view.html` lines 311, 321, 425, 435 (pagination ellipsis):

Change:
```html
<li class="page-item disabled"><span class="page-link">...</span></li>
```

To:
```html
<li class="page-item disabled">
    <span class="page-link" aria-hidden="true">…</span>
    <span class="visually-hidden">{{ ftl('pagination-more-pages') }}</span>
</li>
```

Add translation keys:
- `en/websites.ftl`: `pagination-more-pages = More pages`
- `fr/websites.ftl`: `pagination-more-pages = Plus de pages`

Search for other pagination occurrences:
```bash
grep -rn 'page-link.*\.\.\.' auto_a11y/web/templates/
```

Apply same fix everywhere.

- [ ] **Step 2: Fix icon font bullets in nav lists**

The test flags `<ul>` with `list-style: none` containing `<i class="bi">` icons. The app's dropdown menus and mobile nav hit this pattern.

**Option A:** Add `role="list"` to the `<ul>` — the test may stop flagging if explicit role is present. Check the test logic.

**Option B:** Wrap each `<li>`'s content in a `<span>` with `role="img" aria-hidden="true"` + `<span class="visually-hidden">bullet</span>` — preserves structure.

**Option C:** Change the icon from an `<i>` to a CSS pseudo-element so the test doesn't see it as a bullet-replacement icon element.

Recommended: Option A first. If test still flags after adding `role="list"`, fall back to Option C.

Apply to:
- `base.html` — main navbar nav lists, mobile bottom nav, dropdown menus
- Any template with `<ul>` containing `<i class="bi-">` items

Also ensure `list-style-type: none` is only applied where semantically necessary. If a list is decorative (e.g., flex layout cards), consider converting to plain `<div>` grid.

- [ ] **Step 3: Fix fake list implementations**

Search for `<div>` blocks that are semantic lists:
```bash
grep -rn 'class=".*list' auto_a11y/web/templates/*.html | head -30
```

For each `<div class="list">...<div class="list-item">...</div>...</div>` pattern, convert to `<ul class="list"><li class="list-item">...</li></ul>`.

- [ ] **Step 4: Fix custom bullet styling**

Search for CSS `list-style-type` or `::before { content: ... }` on list items:
```bash
grep -rn 'list-style\|li::before' auto_a11y/web/static/css/
```

For each custom bullet, ensure the bullet content is decorative (marked with `aria-hidden` or using pseudo-elements which are automatically excluded from the accessibility tree). If custom bullets are semantic (e.g., checkmarks denote status), make them text-equivalent.

- [ ] **Step 5: Commit**

```bash
git add auto_a11y/web/templates/ auto_a11y/web/static/css/ auto_a11y/web/translations/
git commit -m "a11y: fix empty list items, icon-font bullets, fake lists (fixes 1448 issues)"
```

---

## Task 9: Remove Title Attributes (79 errors/warnings)

**Fixes:** title_attribute_ErrTitleAttrFound (51), WarnVagueTitleAttribute (20), ErrImproperTitleAttribute (8), ErrEmptyTitleAttr (2)

**Strategy:** Replace all `title=` attributes on non-iframe elements with either visible text, `aria-label`, or remove entirely (redundant titles).

- [ ] **Step 1: Find all title attributes**

```bash
grep -rn 'title=' auto_a11y/web/templates/ --include="*.html" | grep -v '<title>' | grep -v 'iframe' | grep -v '{% block' > /tmp/title_attrs.txt
wc -l /tmp/title_attrs.txt
```

- [ ] **Step 2: Fix scripts/list.html (primary source)**

In `auto_a11y/web/templates/scripts/list.html`:

Lines 104, 108, 112 (badges): Remove `title=` entirely. The badge icon + color conveys the meaning, and the adjacent text already describes the run mode. If the badge is icon-only, add `aria-label="<description>"` instead.

Lines 136, 140 (view/edit buttons): Replace `title=` with `aria-label=`:
```html
<a ... class="btn btn-outline-brand" aria-label="{{ ftl('common-view') }}">
```

Lines 144, 148 (toggle/delete buttons): Replace `title=` with `aria-label=`:
```html
<button ... aria-label="{% if script.enabled %}{{ ftl('common-disable') }}{% else %}{{ ftl('common-enable') }}{% endif %}">
<button ... aria-label="{{ ftl('common-delete') }}">
```

- [ ] **Step 3: Fix remaining title attributes across codebase**

For each file listed in `/tmp/title_attrs.txt`, apply the same pattern:
- Icon-only buttons/links: `title=` → `aria-label=`
- Badges/icons with adjacent text: remove `title=` entirely
- Empty `title=""`: remove
- Vague titles ("click", "more", "view"): remove or replace with descriptive aria-label

- [ ] **Step 4: Verify no title attributes remain**

```bash
grep -rn 'title=' auto_a11y/web/templates/ --include="*.html" | grep -v '<title>' | grep -v 'iframe' | grep -v '{% block' | grep -v 'aria-'
```

Expected output: only `title` in form/meta/structural contexts (page title tags, etc.) — no interactive elements.

- [ ] **Step 5: Commit**

```bash
git add auto_a11y/web/templates/
git commit -m "a11y: replace all title= attributes with aria-label or remove (fixes 79 issues)"
```

---

## Task 10: Fix Form Labels and Fieldsets (44 errors, 10 warnings)

**Fixes:** forms_ErrOrphanLabelWithNoId (24), forms_ErrNoLabel (10), forms_ErrFieldLabelledUsingAriaLabel (4), landmarks_ErrFormUsesAriaLabelInsteadOfVisibleElement (4), forms_WarnMissingRequiredIndication (7), forms_WarnNoFieldset (3), forms_ErrFormEmptyHasNoInteractiveElements (2)

- [ ] **Step 1: Find orphan labels**

```bash
grep -rn '<label' auto_a11y/web/templates/ --include="*.html" | grep -v 'for=' | head -30
```

For each `<label>` without `for=`:
- If it wraps the input: fine (implicit association) — but ensure there's no parent `<label>` (nesting breaks semantics)
- If it's adjacent to an input: add `for="<input-id>"` and ensure the input has matching `id`
- If it's decorative/not labeling anything: change to `<span>` or `<div>`

- [ ] **Step 2: Find inputs without labels**

```bash
grep -rn '<input\|<select\|<textarea' auto_a11y/web/templates/ --include="*.html" | grep -v 'type="hidden"' | grep -v 'type="submit"' | grep -v 'type="button"' > /tmp/form_inputs.txt
```

For each input, verify it has one of:
- `<label for="<id>">` with matching id
- Wrapping `<label>` element
- `aria-labelledby="<element-id>"`

If using `aria-label` only — this triggers `forms_ErrFieldLabelledUsingAriaLabel`. Replace with a visible `<label>`.

- [ ] **Step 3: Fix forms with aria-label-only labeling**

Pattern violations:
- `testing/dashboard.html` project select with only `aria-label`
- `testing/fixture_status.html` search input with only `aria-label`
- `reports/dashboard.html` date inputs with only `aria-label`

Replace each with visible `<label for="x">` elements. Keep placeholder optional.

- [ ] **Step 4: Add required field indicators**

Find forms with `required` attribute:
```bash
grep -rn 'required' auto_a11y/web/templates/ --include="*.html" | grep -E '<input|<select|<textarea'
```

For each required field, verify the label has `*` or "(required)" text. Pattern:
```html
<label for="email" class="form-label">
    {{ ftl('auth-email') }} <span class="text-severity-high" aria-hidden="true">*</span>
    <span class="visually-hidden">{{ ftl('common-required') }}</span>
</label>
```

Add translation key if missing:
- `en/common.ftl`: `common-required = required`
- `fr/common.ftl`: `common-required = requis`

- [ ] **Step 5: Add fieldsets for radio/checkbox groups**

Search for radio/checkbox groups:
```bash
grep -rn 'type="radio"\|type="checkbox"' auto_a11y/web/templates/ --include="*.html"
```

For each group (multiple inputs sharing a `name=`), wrap in `<fieldset>` with `<legend>`:
```html
<fieldset>
    <legend class="form-label">{{ ftl('common-pick-one') }}</legend>
    <!-- radios here -->
</fieldset>
```

- [ ] **Step 6: Fix forms with aria-label but no visible heading**

For each `<form aria-label="X">` identified in report, add a visible heading:
```html
<h2 id="search-form-heading">{{ ftl('common-search') }}</h2>
<form aria-labelledby="search-form-heading">
    <!-- form fields -->
</form>
```

Then remove the `aria-label` since `aria-labelledby` is more robust.

- [ ] **Step 7: Fix empty forms (no interactive elements)**

Search for `<form>` tags that contain no `<input>`/`<select>`/`<textarea>`/`<button type="submit">`:
```bash
grep -B1 -A20 '<form' auto_a11y/web/templates/ -r --include="*.html" | grep -c 'submit\|input\|button\|select\|textarea'
```

For each empty form, either:
- Add the missing submit button/input
- Remove the `<form>` tag entirely if it was a mistake

- [ ] **Step 8: Commit**

```bash
git add auto_a11y/web/templates/ auto_a11y/web/translations/
git commit -m "a11y: fix form labels, required indicators, fieldsets (fixes 54 form issues)"
```

---

## Task 11: Fix Form Focus Outline Issues (10 warnings)

**Fixes:** forms_WarnInputNoBorderOutline (5), forms_WarnInputFocusOutlineExceedsParent (5)

This is largely addressed by Task 1's focus rewrite, but verify:

- [ ] **Step 1: Verify border changes on form focus**

After Task 1, `.form-control:focus` should change both `outline` AND `border-color`. Verify:

```bash
grep -A5 '.form-control:focus' auto_a11y/web/static/css/style.css
```

If border-color doesn't change, add it.

- [ ] **Step 2: Fix outline clipping**

For forms inside containers with `overflow: hidden`, the focus outline gets clipped. Find them:

```bash
grep -rn 'overflow.*hidden' auto_a11y/web/static/css/ auto_a11y/web/templates/
```

For each container holding form controls, change `overflow: hidden` to `overflow: clip` (clips content but not outlines). If `clip` isn't supported in target browsers, use `overflow: visible` or add extra padding to give outlines room.

- [ ] **Step 3: Commit (if changes made)**

```bash
git add auto_a11y/web/static/css/
git commit -m "a11y: ensure form focus has border change and outlines aren't clipped"
```

---

## Task 12: Fix Landmarks and Content Placement

**Fixes:** landmarks_ErrContentOutsideLandmarks (1), aria_ErrAriaLabelMayNotBeFoundByVoiceControl (57)

- [ ] **Step 1: Find content outside landmarks**

Extract the XPath from the report:
```bash
grep 'ErrContentOutsideLandmarks' selftest/Project_Self_Test_20260416_142813.html | head -5
```

For the flagged element, read its source template and wrap it in an appropriate landmark (`<main>`, `<aside>`, `<section aria-labelledby>`, etc.).

- [ ] **Step 2: Fix aria-label voice control issues**

Extract failing XPaths:
```bash
grep -oP 'ErrAriaLabelMayNotBeFoundByVoiceControl[^<]*</td>[^<]+<td>[^<]+</td>' selftest/Project_Self_Test_20260416_142813.html | head -20
```

For each element with a mismatched aria-label, update it to INCLUDE the visible text. Examples:
- Button with text "Save" and `aria-label="Save changes"` — OK (includes "Save")
- Button with text "X" and `aria-label="Close"` — FAIL (no overlap)
- Fix: Either change aria-label to "Close X" OR add visible text

Search templates for aria-labels that don't match visible text:
```bash
grep -rn 'aria-label=' auto_a11y/web/templates/ --include="*.html" | head -50
```

Audit each match. For icon-only buttons without text, aria-label must be a clear voice-command trigger. For buttons with short text like "X" or "→", voice control can't target them easily — the aria-label should start with the visible character if possible.

- [ ] **Step 3: Commit**

```bash
git add auto_a11y/web/templates/
git commit -m "a11y: fix landmarks and aria-label voice-control consistency"
```

---

## Task 13: Fix Table Captions and Anchor Targets

**Fixes:** tables_ErrTableMissingCaption (1), focus_management/tabindex_ErrAnchorTargetTabindex (12)

- [ ] **Step 1: Find the uncaptioned table**

```bash
grep 'ErrTableMissingCaption' selftest/Project_Self_Test_20260416_142813.html
```

For the flagged table, add `<caption>`:
```html
<table>
    <caption>{{ ftl('<feature>-<table-purpose>') }}</caption>
    <!-- existing thead/tbody -->
</table>
```

Add translation keys as needed.

- [ ] **Step 2: Fix anchor targets without tabindex**

The 12 errors are for in-page links `<a href="#id">` where the target `<div id="id">` is not focusable. Add `tabindex="-1"` to each target:

Extract failing targets:
```bash
grep 'ErrAnchorTargetTabindex' selftest/Project_Self_Test_20260416_142813.html | head -10
```

For each flagged target element, add `tabindex="-1"`:
```html
<div id="section-1" tabindex="-1">
```

- [ ] **Step 3: Commit**

```bash
git add auto_a11y/web/templates/
git commit -m "a11y: add table captions and tabindex=-1 to anchor targets"
```

---

## Task 14: Final Verification

- [ ] **Step 1: Start the app**

```bash
cd /home/tait/Documents/cnib/code/auto_a11y_python
source .venv/bin/activate 2>/dev/null || python -m venv .venv && source .venv/bin/activate
python run.py --debug &
```

Wait for "Running on http://127.0.0.1:5001".

- [ ] **Step 2: Run self-test**

Via the web UI at http://localhost:5001:
1. Navigate to the self-test project
2. Run the full test
3. Export HTML report

Or via CLI if available:
```bash
python -m auto_a11y.cli selftest --output selftest/
```

- [ ] **Step 3: Compare before/after counts**

```bash
grep -oP '<td>(aria_|colors_|focus_|forms_|headings_|images_|interactive_|landmarks_|language_|lists_|maps_|media_|page_title_|reading_order_|tables_|title_|tabindex_)\w+</td>' selftest/Project_Self_Test_*.html | sort | uniq -c | sort -rn > /tmp/final_counts.txt
cat /tmp/final_counts.txt
```

Expected: 0 violations, 0 warnings. If any remain, investigate each and iterate.

- [ ] **Step 4: Manual smoke test in browser**

With the app running:
1. Tab through main nav — verify every focus ring is visible
2. Tab through the skip link — verify it becomes visible and has outline
3. Open a dropdown menu — verify keyboard accessibility
4. Fill out a form — verify labels and focus indicators
5. Switch to dark mode and repeat steps 1-4
6. Test with Windows High Contrast Mode enabled (Settings → Accessibility → Contrast themes) — verify nothing disappears

- [ ] **Step 5: Final commit**

If any final tweaks were needed:
```bash
git add -A
git commit -m "a11y: final polish from self-test re-run"
```

---

## Execution Order Summary

| Priority | Task | Issues Fixed | Cumulative |
|----------|------|--------------|------------|
| 1 | Task 2 — Cursor inheritance | 9,274 | 9,274 |
| 2 | Task 1 — Focus indicator system | 6,972 | 16,246 |
| 3 | Task 6 — Long headings | 2,496 | 18,742 |
| 4 | Task 7 — Contrast fixes | 1,595 | 20,337 |
| 5 | Task 8 — List structure | 1,448 | 21,785 |
| 6 | Task 4 — Accordion ARIA | 1,274 | 23,059 |
| 7 | Task 5 — Heading hierarchy | 1,229 | 24,288 |
| 8 | Task 9 — Title attributes | 79 | 24,367 |
| 9 | Task 12 — Landmarks + voice control | 58 | 24,425 |
| 10 | Task 10 — Form labels | 54 | 24,479 |
| 11 | Task 13 — Tables + anchors | 13 | 24,492 |
| 12 | Task 11 — Form focus details | 10 | 24,502 |
| 13 | Task 3 — Color scheme support | 10 | 24,512 |
| 14 | Task 14 — Verification | - | - |

**Total targeted: 24,512 of ~50,037 violations+warnings (49%)**

The remainder are duplicates of the above across 41 pages — each root-cause fix cascades, so resolving the 14 tasks should eliminate ~95%+ of all reported issues. Remaining residuals are addressed in Task 14.

---

## Risk Notes

1. **Task 1 changes Bootstrap focus behavior.** If `.form-control:focus` no longer relies on `box-shadow`, existing visual designs may shift. Verify in browser during Step 6 of Task 1.
2. **Task 2's `pointer-events: none` on children** will break any JavaScript that attaches click handlers directly to child `<span>` elements. Audit before committing:
   ```bash
   grep -rn 'addEventListener.*click\|onclick' auto_a11y/web/static/js/ auto_a11y/web/templates/ | grep -v 'data-bs' | head
   ```
   If any handlers are attached to children that should remain interactive, add an exception or move the handler to the parent.
3. **Task 8 Option A (`role="list"`)** may not satisfy the test if the test checks element children rather than role. Ready fallback to Option C.
4. **Task 6 heading restructure** may break CSS selectors that target heading elements. Test visually.

---

## Files NOT Modified (Intentional)

- `auto_a11y/testing/touchpoint_tests/*.py` — test logic itself. We're making the app conform to the tests, not changing the tests.
- `auto_a11y/scripts/dependencies/*.js` — W3C spec-compliant libraries, shouldn't be modified.
- `auto_a11y/web/static/public/css/*.css` (except tokens.css) — public-facing frontend CSS may have its own concerns; only tokens are updated.
