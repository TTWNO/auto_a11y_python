# Native `<dialog>` Modal Conversion — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert all 27 Bootstrap modals to native `<dialog>` elements for built-in accessibility (focus trapping, inert background, screen reader semantics).

**Architecture:** Create a shared `modal.css` + `modal.js` utility, include in base template, then convert each template file by swapping the outer `<div>` to `<dialog>`, replacing Bootstrap data attributes with custom ones, and replacing Bootstrap Modal JS API calls with native `.showModal()`/`.close()`.

**Tech Stack:** HTML `<dialog>`, vanilla JS, CSS, Jinja2 templates, Bootstrap 5 (inner classes retained)

**Spec:** `docs/superpowers/specs/2026-04-14-native-dialog-modals-design.md`

---

## Transformation Rules Reference

Every task below applies these rules. Do NOT deviate.

### HTML Rules

1. **Outer element:** `<div class="modal fade" id="X" tabindex="-1" aria-labelledby="Y" aria-hidden="true">` → `<dialog class="modal" id="X" aria-labelledby="Y">`
   - Remove: `fade` class, `tabindex="-1"`, `aria-hidden="true"`
   - Closing tag: `</div>` → `</dialog>` (match the CORRECT closing div — the outermost one)
2. **Trigger buttons:** `data-bs-toggle="modal" data-bs-target="#X"` → `data-open-modal="#X"` (remove both Bootstrap attrs, add one custom attr)
3. **Close buttons:** `data-bs-dismiss="modal"` → `data-close-modal`
4. **Missing `aria-label` on close buttons:** Add `aria-label="{{ ftl('common-close') }}"` to any `btn-close` that lacks it
5. **Missing `aria-labelledby`:** Add `aria-labelledby` pointing to the modal's title `id` if missing
6. **Inner structure unchanged:** `modal-dialog`, `modal-content`, `modal-header`, `modal-body`, `modal-footer` — keep as-is

### JS Rules

| Old pattern | New pattern |
|---|---|
| `const modal = new bootstrap.Modal(document.getElementById('X')); modal.show();` | `document.getElementById('X').showModal();` |
| `const modal = bootstrap.Modal.getInstance(document.getElementById('X')); if (modal) modal.hide();` | `document.getElementById('X').close();` |
| `$('#X').modal('show');` | `openModal('X');` |
| `$('#X').modal('hide');` | `closeModal('X');` |
| `el.addEventListener('hidden.bs.modal', fn)` | `el.addEventListener('close', fn)` |
| `el.addEventListener('show.bs.modal', fn)` | Call `fn()` immediately before `el.showModal()` at the call site, then remove the listener |

### Persistent dialogs (non-dismissable)

Add `data-modal-persistent` to the `<dialog>` tag. The utility handles the rest.

---

## Task 1: Create Shared Assets

**Files:**
- Create: `auto_a11y/web/static/css/modal.css`
- Create: `auto_a11y/web/static/js/modal.js`
- Modify: `auto_a11y/web/templates/base.html`

- [ ] **Step 1: Create `modal.css`**

```css
/* Native <dialog> overrides — works with Bootstrap's inner modal classes */
dialog.modal {
    padding: 0;
    border: none;
    background: transparent;
    max-width: 100%;
    max-height: 100%;
    width: 100%;
    height: 100%;
    overflow-y: auto;
}

/* Override Bootstrap's .modal { display: none } when dialog is open */
dialog.modal[open] {
    display: block;
}

dialog.modal::backdrop {
    background-color: rgba(0, 0, 0, 0.5);
}

dialog.modal .modal-dialog {
    min-height: 100%;
    margin: 1.75rem auto;
}
```

- [ ] **Step 2: Create `modal.js`**

```javascript
document.addEventListener('DOMContentLoaded', function() {
    // Wire data-open-modal="<selector>" buttons
    document.addEventListener('click', function(e) {
        var opener = e.target.closest('[data-open-modal]');
        if (opener) {
            e.preventDefault();
            var dialog = document.querySelector(opener.getAttribute('data-open-modal'));
            if (dialog) dialog.showModal();
        }
    });

    // Wire data-close-modal buttons
    document.addEventListener('click', function(e) {
        var closer = e.target.closest('[data-close-modal]');
        if (closer) {
            var dialog = closer.closest('dialog');
            if (dialog) dialog.close();
        }
    });

    // Backdrop click to close (skip persistent dialogs)
    document.addEventListener('click', function(e) {
        if (e.target.tagName === 'DIALOG' && e.target.open
            && !e.target.hasAttribute('data-modal-persistent')) {
            e.target.close();
        }
    });

    // Prevent Escape on persistent dialogs
    document.addEventListener('cancel', function(e) {
        if (e.target.tagName === 'DIALOG'
            && e.target.hasAttribute('data-modal-persistent')) {
            e.preventDefault();
        }
    });
});

// Programmatic helpers for inline scripts
function openModal(id) {
    var dialog = document.getElementById(id);
    if (dialog) dialog.showModal();
}

function closeModal(id) {
    var dialog = document.getElementById(id);
    if (dialog) dialog.close();
}
```

- [ ] **Step 3: Include both in `base.html`**

In `auto_a11y/web/templates/base.html`:
- Add CSS link after line 23 (after `theme-toggle.css`, before `{% block extra_css %}`):
  ```html
  <link rel="stylesheet" href="{{ url_for('static', filename='css/modal.css') }}">
  ```
- Add JS script after line 641 (after `theme-toggle.js`, before the CSRF inline script):
  ```html
  <script src="{{ url_for('static', filename='js/modal.js') }}"></script>
  ```

- [ ] **Step 4: Commit**

```
git add auto_a11y/web/static/css/modal.css auto_a11y/web/static/js/modal.js auto_a11y/web/templates/base.html
git commit -m "feat: add shared modal.css and modal.js for native dialog support"
```

---

## Task 2: Convert `websites/edit.html`

Simplest template: 1 modal, 1 trigger button, 2 dismiss buttons, no JS.

**Files:**
- Modify: `auto_a11y/web/templates/websites/edit.html`

**Modal:** `deleteWebsiteModal` (line 126)
**Trigger:** line 114 (`data-bs-toggle="modal" data-bs-target="#deleteWebsiteModal"`)
**Dismiss buttons:** lines 131, 138

- [ ] **Step 1: Convert modal element**

Line 126: `<div class="modal fade" id="deleteWebsiteModal" tabindex="-1" aria-labelledby="deleteWebsiteModalLabel">`
→ `<dialog class="modal" id="deleteWebsiteModal" aria-labelledby="deleteWebsiteModalLabel">`

Find the matching closing `</div>` for this modal (after line 146) and change it to `</dialog>`.

- [ ] **Step 2: Convert trigger button**

Line 114: Replace `data-bs-toggle="modal" data-bs-target="#deleteWebsiteModal"` with `data-open-modal="#deleteWebsiteModal"`

- [ ] **Step 3: Convert dismiss buttons**

Line 131: Replace `data-bs-dismiss="modal"` with `data-close-modal`
Line 138: Replace `data-bs-dismiss="modal"` with `data-close-modal`

- [ ] **Step 4: Commit**

```
git add auto_a11y/web/templates/websites/edit.html
git commit -m "feat: convert deleteWebsiteModal to native dialog"
```

---

## Task 3: Convert `recordings/detail.html`

1 modal, 1 trigger button, 2 dismiss buttons, no JS.

**Files:**
- Modify: `auto_a11y/web/templates/recordings/detail.html`

**Modal:** `criteriaBreakdownModal` (line 691)
**Trigger:** line 378 (`data-bs-toggle="modal" data-bs-target="#criteriaBreakdownModal"`)
**Dismiss buttons:** lines 698, 817

- [ ] **Step 1: Convert modal element**

Line 691: `<div class="modal fade" id="criteriaBreakdownModal" tabindex="-1" aria-labelledby="criteriaBreakdownModalLabel" aria-hidden="true">`
→ `<dialog class="modal" id="criteriaBreakdownModal" aria-labelledby="criteriaBreakdownModalLabel">`

Change the matching closing `</div>` to `</dialog>`.

- [ ] **Step 2: Convert trigger button**

Line 378: Replace `data-bs-toggle="modal" data-bs-target="#criteriaBreakdownModal"` with `data-open-modal="#criteriaBreakdownModal"`

- [ ] **Step 3: Convert dismiss buttons**

Line 698: Replace `data-bs-dismiss="modal"` with `data-close-modal`
Line 817: Replace `data-bs-dismiss="modal"` with `data-close-modal`

- [ ] **Step 4: Commit**

```
git add auto_a11y/web/templates/recordings/detail.html
git commit -m "feat: convert criteriaBreakdownModal to native dialog"
```

---

## Task 4: Convert `projects/create.html`

1 modal, 2 dismiss buttons, 1 `new bootstrap.Modal` JS call.

**Files:**
- Modify: `auto_a11y/web/templates/projects/create.html`

**Modal:** `testDetailsModal` (line 399)
**Dismiss buttons:** lines 407, 418
**JS:** line 517 (`new bootstrap.Modal(...).show()` pattern)

- [ ] **Step 1: Convert modal element**

Line 399: `<div class="modal fade test-details-modal" id="testDetailsModal" tabindex="-1" aria-labelledby="testDetailsModalLabel" aria-hidden="true">`
→ `<dialog class="modal test-details-modal" id="testDetailsModal" aria-labelledby="testDetailsModalLabel">`

Change matching closing `</div>` to `</dialog>`.

- [ ] **Step 2: Convert dismiss buttons**

Line 407: Replace `data-bs-dismiss="modal"` with `data-close-modal`
Line 418: Replace `data-bs-dismiss="modal"` with `data-close-modal`

- [ ] **Step 3: Convert JS**

Line 517: Replace `const modal = new bootstrap.Modal(document.getElementById('testDetailsModal')); modal.show();`
→ `document.getElementById('testDetailsModal').showModal();`

(This is a two-line pattern — the `new bootstrap.Modal(...)` line and the `modal.show()` line. Replace both with the single `showModal()` call.)

- [ ] **Step 4: Commit**

```
git add auto_a11y/web/templates/projects/create.html
git commit -m "feat: convert testDetailsModal to native dialog in projects/create"
```

---

## Task 5: Convert `projects/edit.html`

2 modals, 1 trigger button, 4 dismiss buttons, 1 `new bootstrap.Modal` JS call.

**Files:**
- Modify: `auto_a11y/web/templates/projects/edit.html`

**Modals:** `deleteProjectModal` (line 462), `testDetailsModal` (line 485)
**Trigger:** line 450 (`data-bs-toggle="modal" data-bs-target="#deleteProjectModal"`)
**Dismiss buttons:** lines 467, 474, 493, 504
**JS:** line 642 (`new bootstrap.Modal` for testDetailsModal)

- [ ] **Step 1: Convert modal elements**

Line 462: `<div class="modal fade" id="deleteProjectModal" tabindex="-1" aria-labelledby="deleteProjectModalLabel">`
→ `<dialog class="modal" id="deleteProjectModal" aria-labelledby="deleteProjectModalLabel">`
(+ closing `</div>` → `</dialog>`)

Line 485: `<div class="modal fade test-details-modal" id="testDetailsModal" tabindex="-1" aria-labelledby="testDetailsModalLabel" aria-hidden="true">`
→ `<dialog class="modal test-details-modal" id="testDetailsModal" aria-labelledby="testDetailsModalLabel">`
(+ closing `</div>` → `</dialog>`)

- [ ] **Step 2: Convert trigger button**

Line 450: Replace `data-bs-toggle="modal" data-bs-target="#deleteProjectModal"` with `data-open-modal="#deleteProjectModal"`

- [ ] **Step 3: Convert dismiss buttons**

Lines 467, 474, 493, 504: Replace `data-bs-dismiss="modal"` with `data-close-modal`

- [ ] **Step 4: Convert JS**

Line 642: Replace `const modal = new bootstrap.Modal(document.getElementById('testDetailsModal')); modal.show();`
→ `document.getElementById('testDetailsModal').showModal();`

- [ ] **Step 5: Commit**

```
git add auto_a11y/web/templates/projects/edit.html
git commit -m "feat: convert deleteProjectModal and testDetailsModal to native dialog in projects/edit"
```

---

## Task 6: Convert `automated_tests/list.html`

1 modal, 1 dismiss button, 1 `new bootstrap.Modal` JS call.

**Files:**
- Modify: `auto_a11y/web/templates/automated_tests/list.html`

**Modal:** `uploadModal` (line 164)
**Dismiss:** line 197
**JS:** line 335 (`new bootstrap.Modal`)

- [ ] **Step 1: Convert modal element**

Line 164: `<div class="modal fade" id="uploadModal" tabindex="-1" aria-labelledby="uploadModalTitle">`
→ `<dialog class="modal" id="uploadModal" aria-labelledby="uploadModalTitle">`
(+ closing `</div>` → `</dialog>`)

- [ ] **Step 2: Convert dismiss button**

Line 197: Replace `data-bs-dismiss="modal"` with `data-close-modal`

- [ ] **Step 3: Convert JS**

Line 335: Replace `const modal = new bootstrap.Modal(document.getElementById('uploadModal')); modal.show();`
→ `document.getElementById('uploadModal').showModal();`

- [ ] **Step 4: Commit**

```
git add auto_a11y/web/templates/automated_tests/list.html
git commit -m "feat: convert uploadModal to native dialog in automated_tests/list"
```

---

## Task 7: Convert `drupal_sync/sync_card.html`

1 modal, 2 dismiss buttons, 1 `new bootstrap.Modal` JS call.

**Files:**
- Modify: `auto_a11y/web/templates/drupal_sync/sync_card.html`

**Modal:** `uploadModal` (line 26)
**Dismiss buttons:** lines 31, 117
**JS:** line 236 (`new bootstrap.Modal`)

- [ ] **Step 1: Convert modal element**

Line 26: `<div class="modal fade" id="uploadModal" tabindex="-1" aria-labelledby="uploadModalTitle">`
→ `<dialog class="modal" id="uploadModal" aria-labelledby="uploadModalTitle">`
(+ closing `</div>` → `</dialog>`)

- [ ] **Step 2: Convert dismiss buttons**

Line 31: Replace `data-bs-dismiss="modal"` with `data-close-modal`
Line 117: Replace `data-bs-dismiss="modal"` with `data-close-modal`

- [ ] **Step 3: Convert JS**

Line 236: Replace `const modal = new bootstrap.Modal(document.getElementById('uploadModal')); modal.show();`
→ `document.getElementById('uploadModal').showModal();`

- [ ] **Step 4: Commit**

```
git add auto_a11y/web/templates/drupal_sync/sync_card.html
git commit -m "feat: convert uploadModal to native dialog in drupal_sync/sync_card"
```

---

## Task 8: Convert `testing/configure.html`

1 modal used as reusable confirm dialog, 2 dismiss buttons, 2 `new bootstrap.Modal` JS calls (one per confirm action).

**Files:**
- Modify: `auto_a11y/web/templates/testing/configure.html`

**Modal:** `confirmModal` (line 188)
**Dismiss buttons:** lines 193, 199
**JS:** lines 239 and 273 (two separate `new bootstrap.Modal` + `modal.show()` patterns)

- [ ] **Step 1: Convert modal element**

Line 188: `<div class="modal fade" id="confirmModal" tabindex="-1" aria-labelledby="confirmModalLabel" aria-hidden="true">`
→ `<dialog class="modal" id="confirmModal" aria-labelledby="confirmModalLabel">`
(+ closing `</div>` → `</dialog>`)

- [ ] **Step 2: Convert dismiss buttons**

Line 193: Replace `data-bs-dismiss="modal"` with `data-close-modal`
Line 199: Replace `data-bs-dismiss="modal"` with `data-close-modal`

- [ ] **Step 3: Convert JS — first confirm usage**

Around line 239: Replace `const modal = new bootstrap.Modal(document.getElementById('confirmModal')); modal.show();`
→ `document.getElementById('confirmModal').showModal();`

Also in the confirm button's onclick handler, the `modal.hide()` call (which used the captured `modal` variable) must change. The handler should use `document.getElementById('confirmModal').close();` or `closeModal('confirmModal');` instead.

- [ ] **Step 4: Convert JS — second confirm usage**

Around line 273: Same transformation as step 3.

- [ ] **Step 5: Commit**

```
git add auto_a11y/web/templates/testing/configure.html
git commit -m "feat: convert confirmModal to native dialog in testing/configure"
```

---

## Task 9: Convert `testing/fixture_status.html`

1 dynamically created modal, 2 dismiss buttons (in the dynamic HTML string), 1 `new bootstrap.Modal`, 1 `hidden.bs.modal` listener.

**Files:**
- Modify: `auto_a11y/web/templates/testing/fixture_status.html`

**Dynamic modal:** helpModal — created at runtime around line 558-593 via `insertAdjacentHTML`
**Dismiss buttons:** lines 564, 570 (in the template literal string)
**JS:** line 590 (`new bootstrap.Modal`), line 594 (`hidden.bs.modal` listener)

- [ ] **Step 1: Convert the dynamic modal HTML string**

In the JavaScript template literal that builds the modal HTML (around lines 558-571):
- Change `<div class="modal fade"` → `<dialog class="modal"`
- Remove `tabindex="-1"` and `aria-hidden="true"` from the string
- Change `data-bs-dismiss="modal"` → `data-close-modal` (two occurrences in the string)
- Change closing `</div>` for the outermost modal div → `</dialog>`
- Ensure `aria-labelledby="helpModalLabel"` is present

- [ ] **Step 2: Convert JS — show modal**

Line 590: Replace `const modal = new bootstrap.Modal(document.getElementById('helpModal')); modal.show();`
→ `document.getElementById('helpModal').showModal();`

- [ ] **Step 3: Convert JS — hidden event**

Line 594: Replace `document.getElementById('helpModal').addEventListener('hidden.bs.modal', function() { this.remove(); });`
→ `document.getElementById('helpModal').addEventListener('close', function() { this.remove(); });`

- [ ] **Step 4: Commit**

```
git add auto_a11y/web/templates/testing/fixture_status.html
git commit -m "feat: convert dynamic helpModal to native dialog in fixture_status"
```

---

## Task 10: Convert `pages/view.html`

2 modals (screenshotModal + testModal), 3 dismiss buttons, 1 `new bootstrap.Modal` JS call, 1 jQuery `.modal('show')`. The `testModal` is a progress dialog — mark it persistent.

**Files:**
- Modify: `auto_a11y/web/templates/pages/view.html`

**Modals:** `screenshotModal` (line 3704), `testModal` (line 3725)
**Dismiss buttons:** lines 3709, 3718, 3730
**JS:** line 3780 (`$('#screenshotModal').modal('show')`), line 3952 (`new bootstrap.Modal` for testModal)

- [ ] **Step 1: Convert screenshotModal**

Line 3704: `<div class="modal fade" id="screenshotModal" tabindex="-1" aria-labelledby="screenshotModalTitle">`
→ `<dialog class="modal" id="screenshotModal" aria-labelledby="screenshotModalTitle">`
(+ closing `</div>` → `</dialog>`)

- [ ] **Step 2: Convert testModal (persistent)**

Line 3725: `<div class="modal fade" id="testModal" tabindex="-1" aria-labelledby="testModalTitle">`
→ `<dialog class="modal" id="testModal" aria-labelledby="testModalTitle" data-modal-persistent>`
(+ closing `</div>` → `</dialog>`)

- [ ] **Step 3: Convert dismiss buttons**

Lines 3709, 3718, 3730: Replace `data-bs-dismiss="modal"` with `data-close-modal`

- [ ] **Step 4: Convert JS — screenshot modal**

Line 3780: Replace `$('#screenshotModal').modal('show');`
→ `openModal('screenshotModal');`

- [ ] **Step 5: Convert JS — test modal**

Line 3952: Replace `const modal = new bootstrap.Modal(document.getElementById('testModal')); modal.show();`
→ `document.getElementById('testModal').showModal();`

- [ ] **Step 6: Commit**

```
git add auto_a11y/web/templates/pages/view.html
git commit -m "feat: convert screenshotModal and testModal to native dialog in pages/view"
```

---

## Task 11: Convert `projects/view.html`

4 modals, 2 trigger buttons, 8 dismiss buttons, 1 `new bootstrap.Modal`, 1 jQuery show, 1 jQuery hide. Fix missing `aria-label` on close buttons.

**Files:**
- Modify: `auto_a11y/web/templates/projects/view.html`

**Modals:** `addWebsiteModal` (line 470), `discoveryModal` (line 501), `testAllModal` (line 567), `deleteModal` (line 617)
**Triggers:** line 37 (`#deleteModal`), line 77 (`#addWebsiteModal`)
**Dismiss buttons:** lines 475, 492, 506, 556, 572, 606, 622, 629
**JS:** line 1056 (`$('#discoveryModal').modal('show')`), line 1083 (`$('#discoveryModal').modal('hide')`), line 1272 (`new bootstrap.Modal` for testAllModal)

- [ ] **Step 1: Convert all 4 modal elements**

For each modal, apply HTML Rule 1:
- Line 470: `addWebsiteModal` → `<dialog class="modal" ...>`
- Line 501: `discoveryModal` → `<dialog class="modal" ...>`
- Line 567: `testAllModal` → `<dialog class="modal" ...>`
- Line 617: `deleteModal` → `<dialog class="modal" ...>`

Each needs its closing `</div>` changed to `</dialog>`.

- [ ] **Step 2: Fix missing `aria-label` on close buttons**

Lines 475, 506, 572, 622: These `btn-close` buttons are missing `aria-label`. Add `aria-label="{{ ftl('common-close') }}"` to each.

- [ ] **Step 3: Convert trigger buttons**

Line 37: Replace `data-bs-toggle="modal" data-bs-target="#deleteModal"` with `data-open-modal="#deleteModal"`
Line 77: Replace `data-bs-toggle="modal" data-bs-target="#addWebsiteModal"` with `data-open-modal="#addWebsiteModal"`

- [ ] **Step 4: Convert all 8 dismiss buttons**

Lines 475, 492, 506, 556, 572, 606, 622, 629: Replace `data-bs-dismiss="modal"` with `data-close-modal`

- [ ] **Step 5: Convert JS — jQuery show/hide**

Line 1056: `$('#discoveryModal').modal('show');` → `openModal('discoveryModal');`
Line 1083: `$('#discoveryModal').modal('hide');` → `closeModal('discoveryModal');`

- [ ] **Step 6: Convert JS — new bootstrap.Modal**

Line 1272: Replace `const modal = new bootstrap.Modal(document.getElementById('testAllModal')); modal.show();`
→ `document.getElementById('testAllModal').showModal();`

- [ ] **Step 7: Commit**

```
git add auto_a11y/web/templates/projects/view.html
git commit -m "feat: convert 4 modals to native dialog in projects/view, fix missing aria-labels"
```

---

## Task 12: Convert `websites/view.html`

5 modals, 1 trigger button, 10 dismiss buttons, 4 jQuery show calls, 4 jQuery hide calls. Fix missing `aria-labelledby` on screenshotModal. Fix missing `aria-label` on 5 close buttons.

**Files:**
- Modify: `auto_a11y/web/templates/websites/view.html`

**Modals:** `discoveryModal` (line 460), `addPageModal` (line 491), `testAllModal` (line 524), `testUntestedModal` (line 549), `screenshotModal` (line 583)
**Trigger:** line 289 (`#addPageModal`)
**Dismiss buttons:** lines 465, 480, 496, 515, 529, 538, 554, 572, 588, 597
**jQuery show:** lines 611, 902, 994, 998
**jQuery hide:** lines 928, 1019, 1101, 1181

- [ ] **Step 1: Convert all 5 modal elements**

Apply HTML Rule 1 to each:
- Line 460: `discoveryModal`
- Line 491: `addPageModal`
- Line 524: `testAllModal`
- Line 549: `testUntestedModal`
- Line 583: `screenshotModal` — also add `aria-labelledby="screenshotModalTitle"` (currently missing)

Each needs its closing `</div>` changed to `</dialog>`.

- [ ] **Step 2: Fix missing `aria-label` on close buttons**

Lines 465, 496, 529, 554, 588: These `btn-close` buttons are missing `aria-label`. Add `aria-label="{{ ftl('common-close') }}"` to each.

- [ ] **Step 3: Convert trigger button**

Line 289: Replace `data-bs-toggle="modal" data-bs-target="#addPageModal"` with `data-open-modal="#addPageModal"`

- [ ] **Step 4: Convert all 10 dismiss buttons**

Lines 465, 480, 496, 515, 529, 538, 554, 572, 588, 597: Replace `data-bs-dismiss="modal"` with `data-close-modal`

- [ ] **Step 5: Convert jQuery show calls**

Line 611: `$('#screenshotModal').modal('show');` → `openModal('screenshotModal');`
Line 902: `$('#discoveryModal').modal('show');` → `openModal('discoveryModal');`
Line 994: `$('#testAllModal').modal('show');` → `openModal('testAllModal');`
Line 998: `$('#testUntestedModal').modal('show');` → `openModal('testUntestedModal');`

- [ ] **Step 6: Convert jQuery hide calls**

Line 928: `$('#discoveryModal').modal('hide');` → `closeModal('discoveryModal');`
Line 1019: `$('#testUntestedModal').modal('hide');` → `closeModal('testUntestedModal');`
Line 1101: `$('#testAllModal').modal('hide');` → `closeModal('testAllModal');`
Line 1181: `$('#addPageModal').modal('hide');` → `closeModal('addPageModal');`

- [ ] **Step 7: Commit**

```
git add auto_a11y/web/templates/websites/view.html
git commit -m "feat: convert 5 modals to native dialog in websites/view, fix missing aria-labelledby and aria-labels"
```

---

## Task 13: Convert `reports/dashboard.html`

7 modals, 7 trigger buttons, 14 dismiss buttons, 2 `new bootstrap.Modal`, 6 `bootstrap.Modal.getInstance`, 2 `show.bs.modal` listeners, 1 `showMessageDialog` helper. Most complex file.

**Files:**
- Modify: `auto_a11y/web/templates/reports/dashboard.html`

**Modals:** `generateReportModal` (line 291), `siteStructureModal` (line 1052), `discoveryModal` (line 1099), `staticHtmlModal` (line 1166), `deduplicatedModal` (line 1323), `recordingsModal` (line 1454), `messageModal` (line 1535)

**Triggers (data-bs-toggle):** lines 59, 77, 95, 113, 135, 153, 171

**Dismiss buttons (data-bs-dismiss):** lines 296, 390, 1057, 1088, 1104, 1155, 1171, 1231, 1328, 1369, 1459, 1524, 1540, 1546

**JS patterns:**
- `new bootstrap.Modal`: lines 746, 1557
- `bootstrap.Modal.getInstance + hide`: lines 725, 873, 1038, 1306, 1437, 1594
- `show.bs.modal` listeners: lines 773, 898
- `showMessageDialog`: line 1554 (definition), line 1570 (usage)

- [ ] **Step 1: Convert all 7 modal elements**

Apply HTML Rule 1 to each modal. Each `<div class="modal fade" ...>` → `<dialog class="modal" ...>`, remove `tabindex="-1"`, remove `aria-hidden` if present, close with `</dialog>`.

- [ ] **Step 2: Convert all 7 trigger buttons**

Lines 59, 77, 95, 113, 135, 153, 171: Replace `data-bs-toggle="modal" data-bs-target="#X"` with `data-open-modal="#X"`

- [ ] **Step 3: Convert all 14 dismiss buttons**

Lines 296, 390, 1057, 1088, 1104, 1155, 1171, 1231, 1328, 1369, 1459, 1524, 1540, 1546: Replace `data-bs-dismiss="modal"` with `data-close-modal`

- [ ] **Step 4: Convert `show.bs.modal` listeners**

Line 773: The `siteStructureModal.addEventListener('show.bs.modal', function() { loadProjectsForStructure(); });` pattern. Remove this listener setup entirely. Instead, the trigger buttons for siteStructureModal (line 113) should already use `data-open-modal`. But `loadProjectsForStructure()` needs to run before the dialog opens. Two options:
- Option A: Add an onclick to the trigger button that calls `loadProjectsForStructure()` (the data-open-modal handler runs after)
- Option B: Replace the data-open-modal trigger with a JS onclick: `onclick="loadProjectsForStructure(); openModal('siteStructureModal');"`

Use Option B — replace `data-open-modal="#siteStructureModal"` on line 113 with `onclick="loadProjectsForStructure(); openModal('siteStructureModal');"` and remove the `show.bs.modal` listener at line 773.

Line 898: Same pattern for `discoveryModal`. Replace `data-open-modal="#discoveryModal"` on line 95 with `onclick="loadProjectsForDiscovery(); openModal('discoveryModal');"` and remove the `show.bs.modal` listener at line 898.

- [ ] **Step 5: Convert `bootstrap.Modal.getInstance` calls**

Each of these follows the pattern: `const modal = bootstrap.Modal.getInstance(el); if (modal) modal.hide();`
Replace with direct `.close()`:

Line 725: → `document.getElementById('generateReportModal').close();`
Line 873: → `document.getElementById('siteStructureModal').close();`
Line 1038: → `document.getElementById('discoveryModal').close();`
Line 1306: → `document.getElementById('staticHtmlModal').close();`
Line 1437: → `document.getElementById('deduplicatedModal').close();`
Line 1594: → `document.getElementById('recordingsModal').close();`

For each, remove the `const modal = ...` line and the `if (modal) modal.hide()` line, replacing with the single `.close()` call.

- [ ] **Step 6: Convert `new bootstrap.Modal` calls**

Line 746: `const modal = new bootstrap.Modal(document.getElementById('generateReportModal')); modal.show();`
→ `document.getElementById('generateReportModal').showModal();`

Line 1557 (inside `showMessageDialog`): `const modal = new bootstrap.Modal(document.getElementById('messageModal')); modal.show();`
→ `document.getElementById('messageModal').showModal();`

The full `showMessageDialog` function becomes:
```javascript
function showMessageDialog(title, message) {
    document.getElementById('messageModalTitle').textContent = title;
    document.getElementById('messageModalBody').textContent = message;
    document.getElementById('messageModal').showModal();
}
```

- [ ] **Step 7: Commit**

```
git add auto_a11y/web/templates/reports/dashboard.html
git commit -m "feat: convert 7 modals to native dialog in reports/dashboard"
```

---

## Task 14: Final Validation

- [ ] **Step 1: Verify no Bootstrap modal patterns remain**

Run these searches — all should return zero matches in app templates (excluding fixture files and `.backup` files):

```bash
grep -r 'data-bs-toggle="modal"' auto_a11y/web/templates/
grep -r 'data-bs-dismiss="modal"' auto_a11y/web/templates/
grep -r 'bootstrap\.Modal' auto_a11y/web/templates/
grep -r '\.modal(' auto_a11y/web/templates/ | grep -v '.modal-'
```

- [ ] **Step 2: Verify all modals use `<dialog>`**

```bash
grep -r 'class="modal' auto_a11y/web/templates/ | grep -v 'modal-' | grep -v '.backup'
```

All matches should be `<dialog class="modal"` elements.

- [ ] **Step 3: Verify app starts without template errors**

```bash
python run.py --test-db
```

If DB connection works, briefly start the app and load a page to confirm no Jinja2 rendering errors.

- [ ] **Step 4: Commit any fixes, then final commit**

If any issues found in validation, fix and commit. Then:

```
git add -A
git commit -m "chore: verify native dialog conversion complete — no Bootstrap modal remnants"
```
