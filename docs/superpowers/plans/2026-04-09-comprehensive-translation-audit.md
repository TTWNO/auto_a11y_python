# Comprehensive Translation Audit Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Find and fix all untranslated user-facing text across the platform, add accurate French translations, and add a Python-level `(FR)` debug suffix to make gaps visually obvious.

**Architecture:** Modify the existing `_escaped_gettext` wrapper in `app.py` to append ` (FR)` when locale is French and a real translation exists. Wrap all bare `flash()` and user-facing `jsonify()` strings with `_()`. Wrap all hardcoded template text with `{{ _('...') }}`. Extract new strings via pybabel, add French translations to `.po`, compile.

**Tech Stack:** Flask-Babel, pybabel CLI, Jinja2 templates, Python

---

## File Structure

No new files are created (except the plan itself). Modified files:

| File | Purpose |
|------|---------|
| `auto_a11y/web/app.py` | Add `(FR)` debug suffix to gettext wrapper |
| `auto_a11y/web/routes/groups.py` | Wrap flash() with _() |
| `auto_a11y/web/routes/pages.py` | Wrap flash()/jsonify with _() |
| `auto_a11y/web/routes/projects.py` | Wrap flash()/jsonify with _() |
| `auto_a11y/web/routes/websites.py` | Wrap flash()/jsonify with _() |
| `auto_a11y/web/routes/recordings.py` | Wrap flash()/jsonify with _() |
| `auto_a11y/web/routes/schedules.py` | Wrap flash()/jsonify with _() |
| `auto_a11y/web/routes/scripts.py` | Wrap flash()/jsonify with _() |
| `auto_a11y/web/routes/discovered_pages.py` | Wrap flash()/jsonify with _() |
| `auto_a11y/web/routes/project_users.py` | Wrap flash()/jsonify with _() |
| `auto_a11y/web/routes/project_participants.py` | Wrap flash()/jsonify with _() |
| `auto_a11y/web/routes/website_users.py` | Wrap flash()/jsonify with _() |
| `auto_a11y/web/routes/members.py` | Wrap jsonify with _() |
| `auto_a11y/web/routes/auth.py` | Fix legacy decorator bare strings |
| `auto_a11y/core/permissions.py` | Wrap flash()/jsonify with _() |
| `auto_a11y/web/templates/**/*.html` | Wrap hardcoded text with {{ _() }} |
| `auto_a11y/web/translations/fr/LC_MESSAGES/messages.po` | Add new French translations |

---

### Task 1: Add (FR) Debug Suffix to Gettext Wrapper

**Files:**
- Modify: `auto_a11y/web/app.py:118-122`

- [ ] **Step 1: Modify the _escaped_gettext wrapper**

In `app.py`, the current wrapper is:
```python
from markupsafe import escape as _markup_escape
from flask_babel import gettext as _babel_gettext
def _escaped_gettext(*args, **kwargs):
    return _markup_escape(_babel_gettext(*args, **kwargs))
app.jinja_env.globals['_'] = _escaped_gettext
```

Replace with:
```python
from markupsafe import escape as _markup_escape
from flask_babel import gettext as _babel_gettext
def _escaped_gettext(*args, **kwargs):
    original = args[0] if args else ''
    translated = _babel_gettext(*args, **kwargs)
    # Append (FR) suffix when locale is French and a real translation exists
    if get_locale() == 'fr' and str(translated) != str(original):
        translated = str(translated) + ' (FR)'
    return _markup_escape(translated)
app.jinja_env.globals['_'] = _escaped_gettext
```

This ensures:
- Only French locale gets the suffix
- Only strings with actual translations get the suffix
- Untranslated strings (where msgstr == msgid) show bare English — making gaps obvious

- [ ] **Step 2: Also patch the Python-side `_()` import**

Many route files use `from flask_babel import gettext as _`. These bypass the Jinja2 wrapper.
Add a Python-side wrapper that does the same thing. After the `babel = Babel(...)` line in `app.py`:

```python
import flask_babel
_original_gettext = flask_babel.gettext
def _debug_gettext(*args, **kwargs):
    original = args[0] if args else ''
    translated = _original_gettext(*args, **kwargs)
    if get_locale() == 'fr' and str(translated) != str(original):
        return str(translated) + ' (FR)'
    return translated
flask_babel.gettext = _debug_gettext
```

This monkey-patches `flask_babel.gettext` so that all `from flask_babel import gettext as _` calls in route files also get the `(FR)` suffix.

- [ ] **Step 3: Fix login_manager.login_message**

In `app.py` line 160:
```python
login_manager.login_message = 'Please log in to access this page.'
```
This should use a lazy translation:
```python
from flask_babel import lazy_gettext
login_manager.login_message = lazy_gettext('Please log in to access this page.')
```

- [ ] **Step 4: Verify the wrapper works**

Start the app, switch to French, confirm that already-translated strings show `(FR)` suffix and untranslated strings show bare English.

---

### Task 2: Wrap Flash/Jsonify in Route Files (Batch A — auth, groups, pages, projects)

**Files:**
- Modify: `auto_a11y/web/routes/auth.py` (legacy decorators only — lines 121-175)
- Modify: `auto_a11y/web/routes/groups.py` (add `from flask_babel import gettext as _`)
- Modify: `auto_a11y/web/routes/pages.py` (wrap bare flash/jsonify)
- Modify: `auto_a11y/web/routes/projects.py` (wrap bare flash/jsonify)

**Pattern for flash():**
```python
# Before:
flash('Page not found', 'error')
# After:
flash(_('Page not found'), 'error')
```

**Pattern for f-string flash():**
```python
# Before:
flash(f'Project "{name}" created successfully', 'success')
# After:
flash(_('Project "%(name)s" created successfully', name=name), 'success')
```

**Pattern for jsonify errors:**
```python
# Before:
return jsonify({'error': 'Page not found'}), 404
# After:
return jsonify({'error': _('Page not found')}), 404
```

- [ ] **Step 1: Fix auth.py legacy decorators**

In `project_role_required` (lines 121-149) and `project_admin_required` (lines 152-175), wrap bare strings with `_()`. These decorators already have access to `_` from the module import.

- [ ] **Step 2: Fix groups.py**

Add `from flask_babel import gettext as _` import. Wrap all 10 flash() calls.

- [ ] **Step 3: Fix pages.py**

Wrap all bare flash() and jsonify() calls (lines 145, 291, 304, 307, 324, 326, 330, 410, 422, 426, 437, 443, 481, 487, 496, 502, 504, 514, 518).

- [ ] **Step 4: Fix projects.py**

Wrap all bare flash() and jsonify() calls throughout the file.

---

### Task 3: Wrap Flash/Jsonify in Route Files (Batch B — websites, recordings, schedules, scripts)

**Files:**
- Modify: `auto_a11y/web/routes/websites.py`
- Modify: `auto_a11y/web/routes/recordings.py`
- Modify: `auto_a11y/web/routes/schedules.py`
- Modify: `auto_a11y/web/routes/scripts.py`

- [ ] **Step 1: Fix websites.py** — wrap all flash/jsonify strings
- [ ] **Step 2: Fix recordings.py** — wrap all flash/jsonify strings
- [ ] **Step 3: Fix schedules.py** — wrap all flash/jsonify strings
- [ ] **Step 4: Fix scripts.py** — wrap all flash/jsonify strings

Each file needs `from flask_babel import gettext as _` if not already present.

---

### Task 4: Wrap Flash/Jsonify in Route Files (Batch C — users, participants, members, discovered, permissions)

**Files:**
- Modify: `auto_a11y/web/routes/project_users.py`
- Modify: `auto_a11y/web/routes/project_participants.py`
- Modify: `auto_a11y/web/routes/website_users.py`
- Modify: `auto_a11y/web/routes/members.py`
- Modify: `auto_a11y/web/routes/discovered_pages.py`
- Modify: `auto_a11y/core/permissions.py`

- [ ] **Step 1-6: Fix each file** — same pattern as Tasks 2-3

---

### Task 5: Wrap Hardcoded Text in Templates

**Files:** All templates in `auto_a11y/web/templates/`

**Priority order:**
1. `base.html` — global UI (activity status, language switcher)
2. Core templates: `pages/view.html`, `pages/view_enhanced.html`
3. Forms: `discovered_pages/view.html`, `recordings/combined.html`
4. Admin: `drupal_sync/project_sync.html`, `testing/fixture_status.html`
5. Static reports: `static_report/*.html`
6. All remaining templates

**Pattern:**
```html
<!-- Before: -->
<h2>Activity in Progress</h2>
<!-- After: -->
<h2>{{ _('Activity in Progress') }}</h2>

<!-- Before: -->
<input placeholder="Search...">
<!-- After: -->
<input placeholder="{{ _('Search...') }}">

<!-- Before: -->
<span title="Copy to clipboard">
<!-- After: -->
<span title="{{ _('Copy to clipboard') }}">
```

**For JavaScript in templates:**
```html
<!-- Before: -->
textContent = 'Discovery Running';
<!-- After: -->
textContent = '{{ _("Discovery Running") }}';
```

- [ ] **Step 1-6:** Fix templates in priority order

---

### Task 6: Extract, Translate, and Compile

- [ ] **Step 1: Extract new strings**
```bash
cd /home/tait/Documents/cnib/code/auto_a11y_python
pybabel extract -F babel.cfg -o auto_a11y/web/translations/messages.pot .
```

- [ ] **Step 2: Update .po file with new strings**
```bash
pybabel update -i auto_a11y/web/translations/messages.pot -d auto_a11y/web/translations
```
This adds new `msgid` entries with empty `msgstr` — it does NOT modify existing translations.

- [ ] **Step 3: Add French translations for all new empty msgstr entries**

Open `auto_a11y/web/translations/fr/LC_MESSAGES/messages.po` and fill in every empty `msgstr ""` with an accurate French translation.

**Translation quality rules:**
- Apostrophes must NOT be escaped: use `l'aide`, not `l\'aide`
- Never mark entries as `fuzzy`
- Verify each translation is accurate and matches the meaning of the msgid
- Use formal French (vous, not tu)

- [ ] **Step 4: Compile translations**
```bash
pybabel compile -f -d auto_a11y/web/translations
```

- [ ] **Step 5: Verify via localhost:8080**

Browse the app in French. Every translated string should show `(FR)` suffix. Any string without `(FR)` is a gap that needs fixing.
