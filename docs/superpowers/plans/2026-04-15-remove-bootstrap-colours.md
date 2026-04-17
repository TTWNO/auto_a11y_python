# Remove Bootstrap Colour Classes Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace all Bootstrap colour utility classes with custom token-based classes so the application has full control over all colours without fighting Bootstrap's internal colour system.

**Architecture:** Define custom CSS utility classes in `style.css` that map directly to the existing design tokens in `tokens.css`. Replace every Bootstrap colour class in templates, JS, and Python files with the custom equivalent. Remove the ~800 lines of Bootstrap colour override CSS that exists today. The result: tokens.css controls colours, style.css defines utility classes, no Bootstrap colour interference.

**Tech Stack:** CSS custom properties, Jinja2 templates, JavaScript, Python (Flask reporting)

---

## Class Name Mapping

Every Bootstrap colour class maps to a custom class. This table is the single source of truth for all replacement tasks.

### Buttons

| Bootstrap | Custom | Token Used |
|-----------|--------|------------|
| `btn-primary` | `btn-brand` | `--color-brand` / `--color-link` |
| `btn-secondary` | `btn-neutral` | `--color-text-muted` / `--color-bg-subtle` |
| `btn-success` | `btn-pass` | `--color-severity-pass` |
| `btn-danger` | `btn-high` | `--color-severity-high` |
| `btn-warning` | `btn-medium` | `--color-severity-medium` |
| `btn-info` | `btn-info` | `--color-info` |
| `btn-dark` | `btn-dark` | `--color-bg-header` |
| `btn-outline-primary` | `btn-outline-brand` | `--color-link` |
| `btn-outline-secondary` | `btn-outline-neutral` | `--color-text-muted` |
| `btn-outline-warning` | `btn-outline-medium` | `--color-severity-medium` |
| `btn-outline-success` | `btn-outline-pass` | `--color-severity-pass` |
| `btn-outline-danger` | `btn-outline-high` | `--color-severity-high` |

### Text

| Bootstrap | Custom | Token Used |
|-----------|--------|------------|
| `text-primary` | `text-brand` | `--color-link` (already exists) |
| `text-secondary` | `text-muted` | `--color-text-muted` |
| `text-success` | `text-severity-pass` | (already exists) |
| `text-danger` | `text-severity-high` | (already exists) |
| `text-warning` | `text-severity-medium` | (already exists) |
| `text-info` | `text-info-custom` | (already exists) |
| `text-muted` | `text-muted` | `--color-text-muted` (redefine) |
| `text-dark` | _(remove, use body default)_ | |
| `text-white` | `text-inverse` | `--color-text-inverse` |
| `text-body` | _(remove, use body default)_ | |
| `text-light` | `text-muted` | `--color-text-muted` |

### Backgrounds

| Bootstrap | Custom | Token Used |
|-----------|--------|------------|
| `bg-primary` | `bg-brand` | `--color-link` |
| `bg-secondary` | `bg-neutral` | `--color-severity-low-badge` |
| `bg-success` | `bg-pass` | `--color-severity-pass` |
| `bg-danger` | `bg-high` | `--color-severity-high` |
| `bg-warning` | `bg-medium` | `--color-severity-medium` |
| `bg-info` | `bg-info` | `--color-info` (redefine) |
| `bg-light` | `bg-subtle` | `--color-bg-subtle` |
| `bg-dark` | `bg-header` | `--color-bg-header` |
| `bg-white` | `bg-elevated` | `--color-bg-elevated` |

### Alerts

| Bootstrap | Custom | Token Used |
|-----------|--------|------------|
| `alert-info` | `alert-info` | `--color-info-*` (redefine) |
| `alert-success` | `alert-pass` | `--color-severity-pass-*` |
| `alert-danger` | `alert-high` | `--color-severity-high-*` |
| `alert-warning` | `alert-medium` | `--color-severity-medium-*` |
| `alert-secondary` | `alert-neutral` | `--color-bg-subtle` / `--color-border` |

### Badges

Badges change from `badge bg-X` to `badge badge-X`. The badge classes set both background AND text colour.

| Bootstrap | Custom |
|-----------|--------|
| `badge bg-primary` | `badge badge-brand` |
| `badge bg-secondary` | `badge badge-neutral` |
| `badge bg-success` | `badge badge-pass` |
| `badge bg-danger` | `badge badge-high` |
| `badge bg-warning` | `badge badge-medium` |
| `badge bg-warning text-dark` | `badge badge-medium` |
| `badge bg-info` | `badge badge-info` |
| `badge bg-light text-dark` | `badge badge-subtle` |

### Tables

| Bootstrap | Custom |
|-----------|--------|
| `table-light` | `table-subtle` |
| `table-info` | `table-info` (redefine) |
| `table-warning` | `table-medium` |
| `table-danger` | `table-high` |
| `table-success` | `table-pass` |
| `table-secondary` | `table-neutral` |

### Progress Bars

| Bootstrap | Custom |
|-----------|--------|
| `progress-bar bg-success` | `progress-bar progress-pass` |
| `progress-bar bg-warning` | `progress-bar progress-medium` |
| `progress-bar bg-danger` | `progress-bar progress-high` |
| `progress-bar bg-secondary` | `progress-bar progress-neutral` |

### Card Headers

| Bootstrap | Custom |
|-----------|--------|
| `card-header bg-warning text-dark` | `card-header card-header-medium` |
| `card-header bg-info text-dark` | `card-header card-header-info` |
| `card-header bg-success text-dark` | `card-header card-header-pass` |

### Toasts (Bootstrap 5 text-bg-*)

| Bootstrap | Custom |
|-----------|--------|
| `text-bg-success` | `toast-pass` |
| `text-bg-danger` | `toast-high` |
| `text-bg-warning` | `toast-medium` |

### Dynamic Jinja2 Mapping Dictionaries

Many templates use inline dictionaries to map severity/impact/status to Bootstrap names. These must change:

**Impact badges** (most common pattern):
```jinja2
{# OLD #}
bg-{{ {'high': 'danger', 'medium': 'warning', 'low': 'info'}[impact] or 'secondary' }}
{# NEW #}
badge-{{ {'high': 'high', 'medium': 'medium', 'low': 'info'}[impact] or 'neutral' }}
```

**Extended impact badges** (axe-core labels):
```jinja2
{# OLD #}
bg-{{ {'critical': 'danger', 'high': 'danger', 'serious': 'danger', 'moderate': 'warning', 'medium': 'warning', 'minor': 'info', 'low': 'info'}[impact] or 'secondary' }}
{# NEW #}
badge-{{ {'critical': 'high', 'high': 'high', 'serious': 'high', 'moderate': 'medium', 'medium': 'medium', 'minor': 'info', 'low': 'info'}[impact] or 'neutral' }}
```

**Status badges**:
```jinja2
{# OLD #}
bg-{{ {'active': 'success', 'paused': 'warning', 'completed': 'info', 'archived': 'secondary'}[status] }}
{# NEW #}
badge-{{ {'active': 'pass', 'paused': 'medium', 'completed': 'info', 'archived': 'neutral'}[status] }}
```

**Flash message alerts** (used in ~20 templates):
```jinja2
{# OLD #}
alert-{{ 'danger' if category == 'error' else category }}
{# NEW #}
alert-{{ {'error': 'high', 'success': 'pass', 'warning': 'medium', 'info': 'info'}.get(category, 'info') }}
```

**Schedule type badges**:
```jinja2
{# OLD #}
{% set type_colors = {'daily': 'primary', 'weekly': 'info', 'monthly': 'secondary', 'one_time': 'warning', 'cron': 'dark'} %}
{# NEW #}
{% set type_colors = {'daily': 'brand', 'weekly': 'info', 'monthly': 'neutral', 'one_time': 'medium', 'cron': 'neutral'} %}
```

### Dynamic JavaScript Mapping

JS files use similar patterns. These are embedded in Jinja2 templates:

**Badge class assignment** (websites/view.html, fixture_status.html, projects/create.html, etc.):
```javascript
// OLD
let badgeClass = 'secondary';
if (...) badgeClass = 'success';
else if (...) badgeClass = 'warning';
else if (...) badgeClass = 'danger';
statusCell.innerHTML = `<span class="badge bg-${badgeClass}">...`;

// NEW
let badgeClass = 'neutral';
if (...) badgeClass = 'pass';
else if (...) badgeClass = 'medium';
else if (...) badgeClass = 'high';
statusCell.innerHTML = `<span class="badge badge-${badgeClass}">...`;
```

**Alert creation** (base.html, configure.html):
```javascript
// OLD
alertDiv.className = `alert alert-${type === 'error' ? 'danger' : type} ...`;

// NEW
const alertMap = {error: 'high', success: 'pass', warning: 'medium', info: 'info', danger: 'high'};
alertDiv.className = `alert alert-${alertMap[type] || 'info'} ...`;
```

**Toast creation** (testing/dashboard.html):
```javascript
// OLD
if (type === 'success') toast.classList.add('text-bg-success');
else if (type === 'danger') toast.classList.add('text-bg-danger');
else if (type === 'warning') toast.classList.add('text-bg-warning');

// NEW
const toastMap = {success: 'toast-pass', danger: 'toast-high', warning: 'toast-medium'};
toast.classList.add(toastMap[type] || 'toast-info');
```

---

## Task 1: Define Custom CSS Utility Classes

**Files:**
- Modify: `auto_a11y/web/static/css/style.css`

Add all custom utility classes in a new section after the existing utility classes (around line 598). These classes use design tokens directly, so they work in both light and dark mode automatically.

- [ ] **Step 1: Add button classes**

Add after the existing utility classes section (~line 598):

```css
/* ============================================================
   Custom Colour Utility Classes
   All classes use design tokens — automatic light/dark support.
   ============================================================ */

/* --- Buttons --- */
.btn-brand {
    color: var(--color-button-primary-text);
    background-color: var(--color-link);
    border-color: var(--color-link);
}
.btn-brand:hover, .btn-brand:active, .btn-brand:focus {
    background-color: var(--color-link-hover);
    border-color: var(--color-link-hover);
    color: var(--color-button-primary-text);
}
.btn-neutral {
    color: var(--color-text);
    background-color: var(--color-bg-subtle);
    border-color: var(--color-border);
}
.btn-neutral:hover, .btn-neutral:active, .btn-neutral:focus {
    background-color: var(--color-border);
    border-color: var(--color-border-strong);
    color: var(--color-text);
}
.btn-pass {
    color: var(--color-text-inverse);
    background-color: var(--color-severity-pass);
    border-color: var(--color-severity-pass);
}
.btn-pass:hover, .btn-pass:active, .btn-pass:focus {
    filter: brightness(0.85);
    color: var(--color-text-inverse);
}
.btn-high {
    color: var(--color-text-inverse);
    background-color: var(--color-severity-high);
    border-color: var(--color-severity-high);
}
.btn-high:hover, .btn-high:active, .btn-high:focus {
    filter: brightness(0.85);
    color: var(--color-text-inverse);
}
.btn-medium {
    color: var(--color-text);
    background-color: var(--color-severity-medium);
    border-color: var(--color-severity-medium);
}
.btn-medium:hover, .btn-medium:active, .btn-medium:focus {
    filter: brightness(0.85);
    color: var(--color-text);
}
.btn-info {
    color: var(--color-text-inverse);
    background-color: var(--color-info);
    border-color: var(--color-info);
}
.btn-info:hover, .btn-info:active, .btn-info:focus {
    filter: brightness(0.85);
    color: var(--color-text-inverse);
}
.btn-dark {
    color: var(--color-text-inverse);
    background-color: var(--color-bg-header);
    border-color: var(--color-bg-header);
}
.btn-dark:hover, .btn-dark:active, .btn-dark:focus {
    filter: brightness(1.2);
    color: var(--color-text-inverse);
}

/* Outline buttons */
.btn-outline-brand {
    color: var(--color-link);
    border-color: var(--color-link);
    background-color: transparent;
}
.btn-outline-brand:hover, .btn-outline-brand:active, .btn-outline-brand.active {
    background-color: var(--color-link);
    border-color: var(--color-link);
    color: var(--color-button-primary-text);
}
.btn-outline-neutral {
    color: var(--color-text-muted);
    border-color: var(--color-border);
    background-color: transparent;
}
.btn-outline-neutral:hover, .btn-outline-neutral:active, .btn-outline-neutral.active {
    background-color: var(--color-bg-subtle);
    border-color: var(--color-border-strong);
    color: var(--color-text);
}
.btn-outline-medium {
    color: var(--color-severity-medium);
    border-color: var(--color-severity-medium-border);
    background-color: transparent;
}
.btn-outline-medium:hover, .btn-outline-medium:active, .btn-outline-medium.active {
    background-color: var(--color-severity-medium);
    border-color: var(--color-severity-medium);
    color: var(--color-text);
}
.btn-outline-pass {
    color: var(--color-severity-pass);
    border-color: var(--color-severity-pass);
    background-color: transparent;
}
.btn-outline-pass:hover, .btn-outline-pass:active, .btn-outline-pass.active {
    background-color: var(--color-severity-pass);
    border-color: var(--color-severity-pass);
    color: var(--color-text-inverse);
}
.btn-outline-high {
    color: var(--color-severity-high);
    border-color: var(--color-severity-high);
    background-color: transparent;
}
.btn-outline-high:hover, .btn-outline-high:active, .btn-outline-high.active {
    background-color: var(--color-severity-high);
    border-color: var(--color-severity-high);
    color: var(--color-text-inverse);
}
```

- [ ] **Step 2: Add alert classes**

```css
/* --- Alerts --- */
.alert-info {
    background-color: var(--color-info-bg);
    color: var(--color-info);
    border-color: var(--color-info-border);
}
.alert-pass {
    background-color: var(--color-severity-pass-bg);
    color: var(--color-severity-pass);
    border-color: var(--color-severity-pass-border);
}
.alert-high {
    background-color: var(--color-severity-high-bg);
    color: var(--color-severity-high);
    border-color: var(--color-severity-high-border);
}
.alert-medium {
    background-color: var(--color-severity-medium-bg);
    color: var(--color-severity-medium);
    border-color: var(--color-severity-medium-border);
}
.alert-neutral {
    background-color: var(--color-bg-subtle);
    color: var(--color-text);
    border-color: var(--color-border);
}
.alert a { color: inherit; text-decoration: underline; }
```

- [ ] **Step 3: Add badge classes**

```css
/* --- Badges --- */
.badge-brand {
    background-color: var(--color-brand);
    color: var(--color-text-inverse);
}
.badge-neutral {
    background-color: var(--color-severity-low-badge);
    color: var(--color-text-inverse);
}
.badge-pass {
    background-color: var(--color-severity-pass-badge);
    color: var(--color-text-inverse);
}
.badge-high {
    background-color: var(--color-severity-high-badge);
    color: var(--color-text-inverse);
}
.badge-medium {
    background-color: var(--color-severity-medium-bg);
    color: var(--color-severity-medium);
    border: 1px solid var(--color-severity-medium-border);
}
.badge-info {
    background-color: var(--color-info-bg);
    color: var(--color-info);
    border: 1px solid var(--color-info-border);
}
.badge-subtle {
    background-color: var(--color-bg-subtle);
    color: var(--color-text);
    border: 1px solid var(--color-border);
}
```

- [ ] **Step 4: Add background, text, table, progress, card-header, toast, and misc classes**

```css
/* --- Backgrounds --- */
.bg-brand { background-color: var(--color-link) !important; }
.bg-neutral { background-color: var(--color-severity-low-badge) !important; }
.bg-pass { background-color: var(--color-severity-pass) !important; }
.bg-high { background-color: var(--color-severity-high) !important; }
.bg-medium { background-color: var(--color-severity-medium) !important; }
.bg-info { background-color: var(--color-info) !important; }
.bg-subtle { background-color: var(--color-bg-subtle) !important; }
.bg-header { background-color: var(--color-bg-header) !important; }
.bg-elevated { background-color: var(--color-bg-elevated) !important; }

/* --- Text --- */
.text-muted { color: var(--color-text-muted) !important; }
.text-inverse { color: var(--color-text-inverse) !important; }

/* --- Tables --- */
.table-subtle, .table-subtle > th, .table-subtle > td {
    background-color: var(--color-bg-subtle);
    color: var(--color-text);
}
.table-info, .table-info > th, .table-info > td {
    background-color: var(--color-info-bg);
    color: var(--color-text);
}
.table-medium, .table-medium > th, .table-medium > td {
    background-color: var(--color-severity-medium-bg);
    color: var(--color-text);
}
.table-high, .table-high > th, .table-high > td {
    background-color: var(--color-severity-high-bg);
    color: var(--color-text);
}
.table-pass, .table-pass > th, .table-pass > td {
    background-color: var(--color-severity-pass-bg);
    color: var(--color-text);
}
.table-neutral, .table-neutral > th, .table-neutral > td {
    background-color: var(--color-bg-subtle);
    color: var(--color-text);
}

/* --- Progress Bars --- */
.progress-pass { background-color: var(--color-severity-pass) !important; }
.progress-medium { background-color: var(--color-severity-medium) !important; color: var(--color-text) !important; }
.progress-high { background-color: var(--color-severity-high) !important; }
.progress-neutral { background-color: var(--color-text-muted) !important; }

/* --- Card Headers --- */
.card-header-medium {
    background-color: var(--color-severity-medium-bg) !important;
    color: var(--color-severity-medium) !important;
    border-bottom-color: var(--color-severity-medium-border) !important;
}
.card-header-info {
    background-color: var(--color-info-bg) !important;
    color: var(--color-info) !important;
    border-bottom-color: var(--color-info-border) !important;
}
.card-header-pass {
    background-color: var(--color-severity-pass-bg) !important;
    color: var(--color-severity-pass) !important;
    border-bottom-color: var(--color-severity-pass-border) !important;
}

/* --- Toasts --- */
.toast-pass { background-color: var(--color-severity-pass) !important; color: var(--color-text-inverse) !important; }
.toast-high { background-color: var(--color-severity-high) !important; color: var(--color-text-inverse) !important; }
.toast-medium { background-color: var(--color-severity-medium) !important; color: var(--color-text) !important; }
.toast-info { background-color: var(--color-info) !important; color: var(--color-text-inverse) !important; }
```

- [ ] **Step 5: Commit**

```bash
git add auto_a11y/web/static/css/style.css
git commit -m "feat: add custom colour utility classes using design tokens"
```

---

## Task 2: Replace Static Bootstrap Colour Classes in Templates

**Files:** All `.html` files under `auto_a11y/web/templates/`

Use scripted bulk replacement for static (non-dynamic) Bootstrap colour classes. Order matters — replace longer/more-specific patterns first to avoid partial matches.

- [ ] **Step 1: Replace button classes**

Run these replacements across all template `.html` files:

```bash
# Outline buttons first (longer match)
find auto_a11y/web/templates -name '*.html' -exec sed -i \
  -e 's/btn-outline-primary/btn-outline-brand/g' \
  -e 's/btn-outline-secondary/btn-outline-neutral/g' \
  -e 's/btn-outline-warning/btn-outline-medium/g' \
  -e 's/btn-outline-success/btn-outline-pass/g' \
  -e 's/btn-outline-danger/btn-outline-high/g' \
  {} +

# Regular buttons
find auto_a11y/web/templates -name '*.html' -exec sed -i \
  -e 's/btn-primary/btn-brand/g' \
  -e 's/btn-secondary/btn-neutral/g' \
  -e 's/btn-success/btn-pass/g' \
  -e 's/btn-danger/btn-high/g' \
  -e 's/btn-warning/btn-medium/g' \
  {} +
```

**Caution:** `btn-info` stays the same name, so no replacement needed.

- [ ] **Step 2: Replace static alert classes**

```bash
find auto_a11y/web/templates -name '*.html' -exec sed -i \
  -e 's/alert-success/alert-pass/g' \
  -e 's/alert-danger/alert-high/g' \
  -e 's/alert-warning/alert-medium/g' \
  -e 's/alert-secondary/alert-neutral/g' \
  {} +
```

**Caution:** `alert-info` stays the same name. Do NOT replace `alert-dismissible`.

- [ ] **Step 3: Replace static badge classes**

The pattern `badge bg-X` becomes `badge badge-X`. Also handle `badge bg-X text-dark` → `badge badge-X`.

```bash
# First: compound patterns (bg-warning text-dark, bg-light text-dark)
find auto_a11y/web/templates -name '*.html' -exec sed -i \
  -e 's/badge bg-warning text-dark/badge badge-medium/g' \
  -e 's/badge bg-light text-dark/badge badge-subtle/g' \
  {} +

# Then: simple badge patterns
find auto_a11y/web/templates -name '*.html' -exec sed -i \
  -e 's/badge bg-primary/badge badge-brand/g' \
  -e 's/badge bg-secondary/badge badge-neutral/g' \
  -e 's/badge bg-success/badge badge-pass/g' \
  -e 's/badge bg-danger/badge badge-high/g' \
  -e 's/badge bg-warning/badge badge-medium/g' \
  -e 's/badge bg-info/badge badge-info/g' \
  {} +
```

- [ ] **Step 4: Replace static text colour classes**

```bash
find auto_a11y/web/templates -name '*.html' -exec sed -i \
  -e 's/text-primary/text-brand/g' \
  -e 's/text-success/text-severity-pass/g' \
  -e 's/text-danger/text-severity-high/g' \
  -e 's/text-warning/text-severity-medium/g' \
  -e 's/text-info/text-info-custom/g' \
  -e 's/text-white/text-inverse/g' \
  {} +
```

**Caution:** `text-muted` stays the same. `text-dark` should be removed (handled per-file in edge cases). Do NOT replace `text-center`, `text-truncate`, `text-nowrap`, `text-decoration-none`, `text-break`, `text-bg-*`, etc. The sed patterns above are safe because they only match the exact Bootstrap colour classes.

- [ ] **Step 5: Replace static background classes on non-badge elements**

```bash
# Card headers first (compound patterns)
find auto_a11y/web/templates -name '*.html' -exec sed -i \
  -e 's/card-header bg-warning text-dark/card-header card-header-medium/g' \
  -e 's/card-header bg-info text-dark/card-header card-header-info/g' \
  -e 's/card-header bg-success text-dark/card-header card-header-pass/g' \
  {} +

# Navbar bg-primary → bg-header (only on navbar)
# This must be done carefully — only replace bg-primary when on a navbar element
find auto_a11y/web/templates -name '*.html' -exec sed -i \
  -e 's/navbar bg-primary/navbar bg-header/g' \
  {} +

# Other bg-* classes (only on non-badge elements — badges already handled above)
# These are less common and should be checked manually after bulk replace
```

- [ ] **Step 6: Replace table colour classes**

```bash
find auto_a11y/web/templates -name '*.html' -exec sed -i \
  -e 's/table-light/table-subtle/g' \
  {} +
```

- [ ] **Step 7: Replace spinner/progress classes**

```bash
find auto_a11y/web/templates -name '*.html' -exec sed -i \
  -e 's/spinner-border text-brand/spinner-border text-brand/g' \
  {} +
```

(Note: `text-primary` was already replaced to `text-brand` in step 4, so spinner references should already be correct.)

- [ ] **Step 8: Verify no over-replacement**

```bash
# Check that non-colour Bootstrap classes weren't affected
grep -rn 'text-brand-' auto_a11y/web/templates/  # Should find nothing (over-matched text-primary-*)
grep -rn 'btn-brand-' auto_a11y/web/templates/  # Should find nothing (over-matched btn-primary-*)
grep -rn 'alert-high-' auto_a11y/web/templates/  # Should find nothing
```

- [ ] **Step 9: Commit**

```bash
git add auto_a11y/web/templates/
git commit -m "refactor: replace static Bootstrap colour classes with custom token classes"
```

---

## Task 3: Replace Dynamic Jinja2 Bootstrap Colour Patterns

**Files:** Templates with dynamic `{{ }}` colour class selection (see mapping above)

These cannot be done with simple sed — each needs a targeted edit because the Jinja2 expressions vary.

- [ ] **Step 1: Replace flash message alert patterns (~20 templates)**

Many templates have this exact pattern for flash messages:
```jinja2
alert-{{ 'danger' if category == 'error' else category }}
```
Replace with:
```jinja2
alert-{{ {'error': 'high', 'success': 'pass', 'warning': 'medium', 'info': 'info'}.get(category, 'info') }}
```

Files (all share identical pattern):
- `base.html`
- `pages/test_matrix_v2.html`
- `pages/test_matrix.html`
- `website_users/list.html`, `create.html`, `edit.html`
- `project_users/list.html`, `create.html`, `edit.html`, `view.html`
- `project_participants/list.html`, `create_supervisor.html`, `create_tester.html`, `edit_supervisor.html`, `edit_tester.html`
- `schedules/form.html`, `list.html`, `view.html`
- `scripts/list.html`

This CAN be done with sed since the pattern is identical:
```bash
find auto_a11y/web/templates -name '*.html' -exec sed -i \
  "s/alert-{{ 'danger' if category == 'error' else category }}/alert-{{ {'error': 'high', 'success': 'pass', 'warning': 'medium', 'info': 'info'}.get(category, 'info') }}/g" \
  {} +
```

- [ ] **Step 2: Replace dynamic badge impact dictionaries**

Multiple templates have impact-to-colour mapping dicts. The exact dictionary varies, but the replacement pattern is consistent.

Short impact dict (`{'high': 'danger', 'medium': 'warning', 'low': 'info'}`):
```bash
find auto_a11y/web/templates -name '*.html' -exec sed -i \
  "s/bg-{{ {'high': 'danger', 'medium': 'warning', 'low': 'info'}/badge-{{ {'high': 'high', 'medium': 'medium', 'low': 'info'}/g" \
  {} +
# Also fix the fallback values
find auto_a11y/web/templates -name '*.html' -exec sed -i \
  "s/or 'secondary' }}/or 'neutral' }}/g" \
  {} +
```

Extended impact dict (axe-core labels):
```bash
find auto_a11y/web/templates -name '*.html' -exec sed -i \
  "s/bg-{{ {'critical': 'danger', 'high': 'danger', 'serious': 'danger', 'moderate': 'warning', 'medium': 'warning', 'minor': 'info', 'low': 'info'}/badge-{{ {'critical': 'high', 'high': 'high', 'serious': 'high', 'moderate': 'medium', 'medium': 'medium', 'minor': 'info', 'low': 'info'}/g" \
  {} +
```

Other dict variants (`{'high': 'danger', 'medium': 'warning', 'low': 'secondary'}` in recordings):
```bash
find auto_a11y/web/templates -name '*.html' -exec sed -i \
  "s/bg-{{ {'high': 'danger', 'medium': 'warning', 'low': 'secondary'}/badge-{{ {'high': 'high', 'medium': 'medium', 'low': 'neutral'}/g" \
  {} +
```

Axe-core variant with `'critical': 'danger'` reorder variants in dedup templates — handle manually.

- [ ] **Step 3: Replace project status badge dictionaries**

In `projects/list.html`, `projects/view.html`:
```bash
find auto_a11y/web/templates/projects -name '*.html' -exec sed -i \
  "s/bg-{{ {'active': 'success', 'paused': 'warning', 'completed': 'info', 'archived': 'secondary'}/badge-{{ {'active': 'pass', 'paused': 'medium', 'completed': 'info', 'archived': 'neutral'}/g" \
  {} +
```

- [ ] **Step 4: Replace AI analysis badge in dashboard.html**

```jinja2
{# OLD #}
badge bg-{{ 'success' if config.RUN_AI_ANALYSIS else 'secondary' }}
{# NEW #}
badge badge-{{ 'pass' if config.RUN_AI_ANALYSIS else 'neutral' }}
```

- [ ] **Step 5: Replace discovery attempt status badges (websites/view.html)**

```jinja2
{# OLD #}
bg-{{ {'completed': 'success', 'failed': 'danger', 'running': 'warning'}[attempt.status] }}
{# NEW #}
badge-{{ {'completed': 'pass', 'failed': 'high', 'running': 'medium'}[attempt.status] }}
```

- [ ] **Step 6: Replace schedule type colours (schedules/dashboard.html)**

```jinja2
{# OLD #}
{% set type_colors = {'daily': 'primary', 'weekly': 'info', 'monthly': 'secondary', 'one_time': 'warning', 'cron': 'dark'} %}
<span class="badge type-badge bg-{{ type_colors.get(type_value, 'secondary') }}">
{# NEW #}
{% set type_colors = {'daily': 'brand', 'weekly': 'info', 'monthly': 'neutral', 'one_time': 'medium', 'cron': 'neutral'} %}
<span class="badge type-badge badge-{{ type_colors.get(type_value, 'neutral') }}">
```

- [ ] **Step 7: Replace static report dynamic patterns**

Files: `static_report/page_detail.html`, `dedup_unassigned.html`, `dedup_component.html`

These have `alert-{{ alert_type }}` and `text-{{ alert_type }}` where `alert_type` is a Python variable. The Python code that sets `alert_type` must also change (it currently sets 'danger', 'warning', 'info', etc.). Check what sets `alert_type` in the reporting Python code, and update both the template and the Python code together.

- [ ] **Step 8: Replace dynamic JS colour patterns in templates**

Manually update the JavaScript sections in:
- `base.html` — alert creation function
- `testing/configure.html` — alert function
- `websites/view.html` — badge class assignment in polling JS
- `projects/create.html` — impact badge JS (two occurrences)
- `projects/edit.html` — impact badge JS
- `testing/fixture_status.html` — success rate badge JS
- `testing/dashboard.html` — toast creation

Each follows the pattern shown in the Dynamic JavaScript Mapping section above.

- [ ] **Step 9: Commit**

```bash
git add auto_a11y/web/templates/
git commit -m "refactor: replace dynamic Bootstrap colour patterns in templates"
```

---

## Task 4: Replace Bootstrap Colours in Python Reporting Files

**Files:**
- `auto_a11y/reporting/page_structure_report.py`
- `auto_a11y/reporting/project_report.py`
- `auto_a11y/reporting/discovery_report.py`
- `auto_a11y/reporting/comprehensive_report.py`

- [ ] **Step 1: Update page_structure_report.py**

Replace in Python string literals:
- `text-warning` → `text-severity-medium`
- `text-primary` → `text-brand`
- `text-danger` → `text-severity-high`
- `text-muted` → `text-muted` (keep — we redefine it)
- `badge bg-success` → `badge badge-pass`
- `badge bg-danger` → `badge badge-high`
- `badge bg-secondary` → `badge badge-neutral`
- `badge bg-warning` → `badge badge-medium`
- `btn-outline-primary` → `btn-outline-brand`
- `btn-outline-secondary` → `btn-outline-neutral`
- `btn btn-sm btn-outline-primary` → `btn btn-sm btn-outline-brand`
- `btn btn-sm btn-outline-secondary` → `btn btn-sm btn-outline-neutral`
- Remove the CSS `.text-warning` override in the inline `<style>` (no longer needed)

- [ ] **Step 2: Update project_report.py**

Replace:
- `text-danger` → `text-severity-high`
- `text-warning` → `text-severity-medium`
- `text-muted` → `text-muted` (keep)
- `bg-success` / `bg-warning` / `bg-danger` (on progress bars) → `progress-pass` / `progress-medium` / `progress-high`
- `alert alert-info` → `alert alert-info` (keep — we redefine it)

- [ ] **Step 3: Update discovery_report.py**

Replace:
- `text-danger` → `text-severity-high`

- [ ] **Step 4: Update comprehensive_report.py**

Replace:
- `text-muted` → `text-muted` (keep)

- [ ] **Step 5: Check for any Python code that sets `alert_type` variable for static reports**

Search for where `alert_type` is assigned in Python. If it's set to Bootstrap names ('danger', 'warning', 'info'), update to custom names ('high', 'medium', 'info').

- [ ] **Step 6: Commit**

```bash
git add auto_a11y/reporting/
git commit -m "refactor: replace Bootstrap colour classes in Python report generation"
```

---

## Task 5: Clean Up CSS Files

**Files:**
- `auto_a11y/web/static/css/style.css` — remove Bootstrap colour overrides
- `auto_a11y/web/static/css/mobile.css` — replace `--bs-*` references
- `auto_a11y/web/static/css/help-system.css` — replace `--bs-primary` references

- [ ] **Step 1: Remove Bootstrap `--bs-*` variable mappings from style.css**

Delete lines 1-26 (the `:root` block that maps `--bs-primary`, `--bs-info`, etc. to tokens, and the dark-mode `--bs-primary-rgb` overrides). These are no longer needed since we don't use Bootstrap colour classes.

- [ ] **Step 2: Remove Bootstrap colour class overrides from style.css**

Delete the following sections:
- Lines ~340-418: `.btn-primary`, `.bg-primary`, `.text-primary`, `.border-primary`, `.bg-info`, `.text-info`, `.border-info`, `.bg-warning`, `.badge.bg-warning`, `.alert-warning`, `.progress-bar.bg-warning`, `.text-warning`, `.btn-outline-warning`, `.progress-bar.bg-secondary` overrides
- Lines ~600-636: Dark mode `.text-secondary` override and dark mode severity header overrides (these use `.bg-*` selectors)
- Lines ~638-1100+: The entire "COMPREHENSIVE DARK-MODE BOOTSTRAP OVERRIDES" section (sections A through H+) — ALL of this CSS exists only to fix Bootstrap colour classes in dark mode. With custom classes, none of it is needed.

**This removes approximately 700-800 lines of CSS.**

- [ ] **Step 3: Update card dark mode selectors**

The card dark mode CSS uses `:not(.bg-primary):not(.bg-secondary)...` selectors. Update to use our new class names:
```css
/* OLD */
[data-theme="dark"] .card:not(.bg-primary):not(.bg-secondary):not(.bg-success)...
/* NEW */
[data-theme="dark"] .card:not(.bg-brand):not(.bg-neutral):not(.bg-pass)...
```

Or simplify: since our custom bg-* classes already set colours correctly via tokens, we may not need exclusion selectors at all. Cards without explicit bg-* get `--color-bg-elevated` in dark mode — that's correct.

- [ ] **Step 4: Update mobile.css**

Replace `--bs-primary` and `--bs-secondary` references:
- Line 84: `background: var(--bs-primary)` → `background: var(--color-bg-header)`
- Line 201: `color: var(--bs-secondary)` → `color: var(--color-text-muted)`
- Line 216: `color: var(--bs-primary)` → `color: var(--color-brand)`
- Line 332: `color: var(--bs-secondary)` → `color: var(--color-text-muted)`
- Lines 621-626: `.badge.bg-danger`, `.badge.bg-warning`, `.badge.bg-info`, `.badge.bg-success` → `.badge-high`, `.badge-medium`, `.badge-info`, `.badge-pass`
- Line 730-732: `.nav-tabs .nav-link.active` references to `--bs-primary` → `--color-brand`
- Line 848: `.pull-to-refresh` `background: var(--bs-primary)` → `background: var(--color-brand)`
- Lines 422-424: `.btn-primary.w-100`, `.btn-success.w-100`, `.btn-danger.w-100` → `.btn-brand.w-100`, `.btn-pass.w-100`, `.btn-high.w-100`

- [ ] **Step 5: Update help-system.css**

Replace `--bs-primary` references:
- Line 282: `.help-card` `border-left: 4px solid var(--bs-primary)` → `border-left: 4px solid var(--color-brand)`
- Lines 303-311: `.help-nav .nav-link:hover` and `.nav-link.active` — replace `var(--bs-primary)` with `var(--color-brand)` and `rgba(var(--bs-primary-rgb), 0.1)` with `var(--color-brand-subtle)`
- Line 315: `.score-comparison` gradient — replace `var(--bs-primary)` with `var(--color-brand)` and `var(--bs-purple, #764ba2)` with `var(--color-discovery)`

- [ ] **Step 6: Commit**

```bash
git add auto_a11y/web/static/css/
git commit -m "refactor: remove Bootstrap colour overrides from CSS, replace --bs-* with tokens"
```

---

## Task 6: Verification

- [ ] **Step 1: Grep for remaining Bootstrap colour class names in templates**

```bash
# Should return NO results (excluding Bootstrap's own CSS/JS files)
grep -rn --include='*.html' \
  -e 'btn-primary' -e 'btn-secondary' -e 'btn-success' -e 'btn-danger' -e 'btn-warning' \
  -e 'text-primary' -e 'text-secondary' -e 'text-success' -e 'text-danger' -e 'text-warning' \
  -e 'bg-primary' -e 'bg-secondary' -e 'bg-success' -e 'bg-danger' -e 'bg-warning' -e 'bg-light' -e 'bg-dark' \
  -e 'alert-success' -e 'alert-danger' -e 'alert-warning' -e 'alert-secondary' \
  -e 'text-white' -e 'text-dark' \
  -e 'table-light' \
  -e 'text-bg-' \
  auto_a11y/web/templates/
```

- [ ] **Step 2: Grep for remaining `--bs-*` in CSS**

```bash
grep -rn '\-\-bs-' auto_a11y/web/static/css/
# Should return nothing
```

- [ ] **Step 3: Grep for remaining Bootstrap colour classes in Python**

```bash
grep -rn --include='*.py' \
  -e 'btn-primary' -e 'btn-secondary' -e 'text-primary' -e 'text-danger' -e 'text-warning' \
  -e 'bg-success' -e 'bg-danger' -e 'bg-warning' -e 'bg-secondary' \
  -e 'badge bg-' \
  auto_a11y/
```

- [ ] **Step 4: Visual smoke test**

```bash
python run.py --debug
# Visit in browser and check:
# 1. Dashboard — buttons, badges, stat cards
# 2. Any project page — alerts, status badges
# 3. Test results page — severity badges, impact badges
# 4. Dark mode toggle — all colours should work
# 5. Fixture status page — success rate badges
```

- [ ] **Step 5: Final commit (if any fixups needed)**

```bash
git add -A
git commit -m "fix: address colour class migration edge cases"
```
