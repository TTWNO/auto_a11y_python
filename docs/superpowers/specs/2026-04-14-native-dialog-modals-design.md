# Native `<dialog>` Modal Conversion

**Date:** 2026-04-14
**Status:** Approved
**Scope:** All Bootstrap modals in app templates → native `<dialog>` elements

## Motivation

Native `<dialog>` elements provide built-in accessibility benefits over Bootstrap's `<div>`-based modals:
- Automatic focus trapping (no JS needed)
- Automatic `aria-modal="true"` semantics
- Native Escape key handling
- Proper inert background (blocks interaction with content behind the dialog)
- Screen readers announce the dialog role natively

## Scope

28 modals across 12 template files:

| Template | Modals | IDs |
|----------|--------|-----|
| `pages/view.html` | 2 | screenshotModal, testModal |
| `projects/view.html` | 5 | addWebsiteModal, discoveryModal, testAllModal, deleteModal |
| `projects/create.html` | 1 | testDetailsModal |
| `projects/edit.html` | 2 | deleteProjectModal, testDetailsModal |
| `websites/view.html` | 5 | discoveryModal, addPageModal, testAllModal, testUntestedModal, screenshotModal |
| `websites/edit.html` | 1 | deleteWebsiteModal |
| `recordings/detail.html` | 1 | criteriaBreakdownModal |
| `testing/configure.html` | 1 | confirmModal |
| `testing/fixture_status.html` | 1 | helpModal (dynamically created) |
| `automated_tests/list.html` | 1 | uploadModal |
| `drupal_sync/sync_card.html` | 1 | uploadModal |
| `reports/dashboard.html` | 7 | generateReportModal, siteStructureModal, discoveryModal, staticHtmlModal, deduplicatedModal, recordingsModal, messageModal |

## Design

### 1. HTML Changes

Each modal:

**Before:**
```html
<div class="modal fade" id="myModal" tabindex="-1" aria-labelledby="myModalLabel" aria-hidden="true">
    <div class="modal-dialog">
        <div class="modal-content">
            <div class="modal-header">
                <h2 class="modal-title" id="myModalLabel">Title</h2>
                <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
            </div>
            <div class="modal-body">...</div>
            <div class="modal-footer">...</div>
        </div>
    </div>
</div>
```

**After:**
```html
<dialog class="modal" id="myModal" aria-labelledby="myModalLabel">
    <div class="modal-dialog">
        <div class="modal-content">
            <div class="modal-header">
                <h2 class="modal-title" id="myModalLabel">Title</h2>
                <button type="button" class="btn-close" data-close-modal aria-label="{{ ftl('common-close') }}"></button>
            </div>
            <div class="modal-body">...</div>
            <div class="modal-footer">...</div>
        </div>
    </div>
</dialog>
```

Removed: `fade` class, `tabindex="-1"`, `aria-hidden="true"` (native `<dialog>` handles all three).

Trigger buttons: `data-bs-toggle="modal" data-bs-target="#id"` → `data-open-modal="#id"`.

Close buttons: `data-bs-dismiss="modal"` → `data-close-modal`.

Inner structure unchanged: `modal-dialog`, `modal-content`, `modal-header`, `modal-body`, `modal-footer`.

### 2. CSS Overrides

New file: `auto_a11y/web/static/css/modal.css`

```css
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

dialog.modal::backdrop {
    background-color: rgba(0, 0, 0, 0.5);
}

dialog.modal .modal-dialog {
    display: flex;
    align-items: center;
    min-height: 100%;
    margin: 1.75rem auto;
}
```

Included in the base template. Bootstrap's inner classes (`modal-content`, `modal-header`, etc.) continue to provide visual styling.

### 3. Shared `modal.js` Utility

New file: `auto_a11y/web/static/js/modal.js`

```javascript
document.addEventListener('DOMContentLoaded', function() {
    // Wire data-open-modal="<selector>" buttons
    document.addEventListener('click', function(e) {
        const opener = e.target.closest('[data-open-modal]');
        if (opener) {
            e.preventDefault();
            const dialog = document.querySelector(opener.getAttribute('data-open-modal'));
            if (dialog) dialog.showModal();
        }
    });

    // Wire data-close-modal buttons
    document.addEventListener('click', function(e) {
        const closer = e.target.closest('[data-close-modal]');
        if (closer) {
            const dialog = closer.closest('dialog');
            if (dialog) dialog.close();
        }
    });

    // Backdrop click to close
    document.addEventListener('click', function(e) {
        if (e.target.tagName === 'DIALOG' && e.target.open) {
            e.target.close();
        }
    });
});

function openModal(id) {
    const dialog = document.getElementById(id);
    if (dialog) dialog.showModal();
}

function closeModal(id) {
    const dialog = document.getElementById(id);
    if (dialog) dialog.close();
}
```

### 4. JS Migration Patterns

| Bootstrap pattern | Replacement |
|---|---|
| `new bootstrap.Modal(el).show()` | `el.showModal()` or `openModal('id')` |
| `bootstrap.Modal.getInstance(el).hide()` | `el.close()` or `closeModal('id')` |
| `$('#id').modal('show')` | `openModal('id')` |
| `$('#id').modal('hide')` | `closeModal('id')` |
| `el.addEventListener('show.bs.modal', fn)` | Call `fn()` before `showModal()` at call site |
| `el.addEventListener('hidden.bs.modal', fn)` | `el.addEventListener('close', fn)` |

Special cases:
- `showMessageDialog()` in `reports/dashboard.html`: Rewrite to use `openModal('messageModal')` internally.
- `fixture_status.html` dynamically created modal: Change to create a `<dialog>` element instead of a `<div>`, use `.showModal()`, listen for `close` event instead of `hidden.bs.modal`.
- `show.bs.modal` listeners on siteStructureModal and discoveryModal (dashboard): Move the load function call to before `showModal()` at the trigger site.

## Files Changed

**New files:**
- `auto_a11y/web/static/css/modal.css`
- `auto_a11y/web/static/js/modal.js`

**Modified templates (HTML + inline JS):**
- `auto_a11y/web/templates/pages/view.html`
- `auto_a11y/web/templates/projects/view.html`
- `auto_a11y/web/templates/projects/create.html`
- `auto_a11y/web/templates/projects/edit.html`
- `auto_a11y/web/templates/websites/view.html`
- `auto_a11y/web/templates/websites/edit.html`
- `auto_a11y/web/templates/recordings/detail.html`
- `auto_a11y/web/templates/testing/configure.html`
- `auto_a11y/web/templates/testing/fixture_status.html`
- `auto_a11y/web/templates/automated_tests/list.html`
- `auto_a11y/web/templates/drupal_sync/sync_card.html`
- `auto_a11y/web/templates/reports/dashboard.html`

**Modified base template** (to include new CSS/JS):
- Base template (wherever `<link>` and `<script>` tags are included)

## Out of Scope

- Removing Bootstrap's modal JS/CSS from the bundle (can be done separately once all modals are converted)
- Animation/transitions (native `<dialog>` has no built-in fade; can be added with CSS later if desired)
- Fixture HTML files (test fixtures, not app UI)
