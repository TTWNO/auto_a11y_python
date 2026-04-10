# Fluent Migration Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the entire i18n system from gettext/Flask-Babel to Project Fluent, eliminating the `.mo` compilation step, fuzzy marker bugs, and JS/Python translation format split.

**Architecture:** A new `auto_a11y/web/fluent.py` module provides the Flask-Fluent integration layer (bundle loading, `ftl()` global, escaping, `force_locale`, `lazy_ftl`). A migration script bulk-converts the ~2,700 `.po` entries to `.ftl` files and rewrites ~4,000+ template call sites. JavaScript translations move from `window.i18n` to `@fluent/bundle` loading shared `.ftl` files. Issue descriptions and WCAG labels also move to `.ftl`.

**Tech Stack:** `fluent-compiler` 1.1 (Python), `@fluent/bundle` (JS), `markupsafe` (escaping), `babel.dates` (datetime formatting)

**Spec:** `docs/FLUENT_MIGRATION_PROPOSAL.md`

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `auto_a11y/web/fluent.py` | Flask-Fluent integration: bundle loading, `ftl()`, `lazy_ftl()`, `force_locale`, escaping, `datetimeformat` filter |
| `auto_a11y/web/translations/en/*.ftl` | English message files organized by feature area (~10 files) |
| `auto_a11y/web/translations/fr/*.ftl` | French message files mirroring `en/` |
| `auto_a11y/web/static/js/fluent.js` | JS helper: loads `.ftl` files, exposes `ftl()` function |
| `scripts/migrate_po_to_ftl.py` | One-time migration script: `.po` → `.ftl` + template rewriting |
| `scripts/migrate_issues_to_ftl.py` | One-time migration: `issue_translations_fr.json` → `.ftl` |
| `tests/test_fluent.py` | Tests for the Flask-Fluent integration layer |

### Modified Files

| File | What Changes |
|------|-------------|
| `auto_a11y/web/app.py` (lines 7, 39-40, 88-129, 132-137, 167-168, 177-224) | Remove Flask-Babel init, escape wrappers, dynamic_translations; add Fluent init |
| `auto_a11y/reporting/static_html_generator.py` (line 19, 287-301) | Replace `jinja2.ext.i18n` / `force_locale` / `pgettext` with Fluent |
| `auto_a11y/reporting/project_report.py` (line 12, 62) | Replace `force_locale` import/usage |
| `auto_a11y/reporting/page_structure_report.py` (line 14, 243, 248) | Replace `force_locale` import/usage |
| `auto_a11y/reporting/discovery_report.py` (line 20, 10 usages) | Replace `force_locale` import/usage |
| `auto_a11y/reporting/recordings_report.py` (line 13, 255) | Replace `force_locale` import/usage |
| `auto_a11y/reporting/comprehensive_report.py` (line 10, 505, 510) | Replace `force_locale` import/usage |
| `auto_a11y/web/routes/reports.py` (line 6, 11 usages) | Replace `force_locale` import/usage |
| `auto_a11y/web/routes/pages.py` (line 6, lines 77, 80) | Replace `force_locale`/`lazy_gettext` |
| `auto_a11y/reporting/wcag_mapper.py` (~66 `lazy_gettext` calls) | Replace `lazy_gettext as _` with `lazy_ftl` |
| `auto_a11y/reporting/issue_descriptions_translated.py` | Replace `from flask_babel import get_locale` with Fluent locale detection |
| `auto_a11y/reporting/report_generator.py` | Replace `gettext as _` import/usage |
| `auto_a11y/core/permissions.py` | Replace `gettext as _` import/usage |
| `auto_a11y/web/routes/auth.py` | Replace `_` import/usage |
| `auto_a11y/web/routes/website_users.py` | Replace `gettext as _` import/usage |
| `auto_a11y/web/routes/project_participants.py` | Replace `gettext as _` import/usage |
| `auto_a11y/web/routes/members.py` | Replace `gettext as _` import/usage |
| `auto_a11y/web/routes/groups.py` | Replace `gettext as _` import/usage |
| `auto_a11y/web/routes/schedules.py` | Replace `gettext as _` import/usage |
| `auto_a11y/web/routes/scripts.py` | Replace `gettext as _` import/usage |
| `auto_a11y/web/routes/websites.py` | Replace `gettext as _` import/usage |
| `auto_a11y/web/routes/recordings.py` | Replace `gettext as _` import/usage |
| `auto_a11y/web/routes/discovered_pages.py` | Replace `gettext as _` import/usage |
| `auto_a11y/web/routes/share_tokens.py` | Replace `_` import/usage |
| `auto_a11y/web/routes/public.py` | Replace `_` import/usage |
| `auto_a11y/testing/result_processor.py` | Replace `gettext as _` import/usage |
| All 88 template files in `auto_a11y/web/templates/` | `{{ _('...') }}` → `{{ ftl('...') }}` |
| `auto_a11y/web/static/js/issue-filters.js` (line 84, 339, 356) | Replace `window.i18n` with Fluent `ftl()` |
| `scripts/validate_translations.py` | Rewrite to validate `.ftl` coverage |
| `tests/test_translations.py` | Rewrite to validate `.ftl` files instead of `.po` |
| `tests/test_static_html_streaming.py` (lines 265-484) | Update `force_locale` mocks |
| `.github/workflows/ci.yml` (lines 47-50) | Remove `pybabel compile`, add Fluent validation |
| `run.py` (lines 133-160, 200) | Remove `compile_translations()` |
| `render-build.sh` (lines 21-22) | Remove `pybabel compile` |
| `Dockerfile` (lines 46-47) | Remove `pybabel compile` |
| `requirements.txt` (line 29) | Replace `flask-babel` with `fluent-compiler` |
| `CLAUDE.md` | Update translation workflow section |
| `README.md` + `README.fr.md` | Update translation references |

### Deleted Files (Phase 6)

| File | Why |
|------|-----|
| `babel.cfg` | No longer needed |
| `auto_a11y/web/translations/messages.pot` | Replaced by `.ftl` files |
| `auto_a11y/web/translations/fr/LC_MESSAGES/messages.po` | Replaced by `.ftl` files |
| `auto_a11y/web/translations/fr/LC_MESSAGES/messages.mo` | No binary format in Fluent |
| `auto_a11y/reporting/issue_translations_fr.json` | Migrated to `.ftl` |
| `auto_a11y/reporting/issue_translations_inline.py` | Migrated to `.ftl` |
| `auto_a11y/reporting/wcag_translations_fr.py` | Migrated to `.ftl` |
| `scripts/migrate_po_to_ftl.py` | One-time script, no longer needed |
| `scripts/migrate_issues_to_ftl.py` | One-time script, no longer needed |

---

## Task 1: Install Dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add fluent-compiler to requirements**

In `requirements.txt`, add:
```
fluent-compiler==1.1
```

Pin the exact version. `fluent-compiler` has a single maintainer — pinning ensures reproducible builds and protects against unexpected breaking changes. Bump deliberately after testing.

Do NOT remove `flask-babel` yet — it stays until Phase 2 is complete (dual-system operation).

- [ ] **Step 2: Install**

Run: `.venv/bin/python -m pip install fluent-compiler`
Expected: Successfully installed fluent-compiler-1.1, fluent-syntax, etc.

- [ ] **Step 3: Verify import works**

Run: `.venv/bin/python -c "from fluent_compiler.bundle import FluentBundle; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "Add fluent-compiler dependency for Fluent migration"
```

---

## Task 2: Write Flask-Fluent Integration Layer

**Files:**
- Create: `auto_a11y/web/fluent.py`
- Create: `tests/test_fluent.py`

This is the core of the migration. It provides `ftl()`, `lazy_ftl()`, `force_locale()`, and the MarkupSafe escaper — everything the rest of the codebase will call.

- [ ] **Step 1: Write failing tests for the integration layer**

Create `tests/test_fluent.py`:

```python
"""Tests for the Flask-Fluent integration layer."""
import os
import tempfile
import shutil
import pytest
from flask import Flask

# Will be importable after Step 3
from auto_a11y.web.fluent import init_fluent, ftl, lazy_ftl, force_locale


@pytest.fixture
def app_with_fluent():
    """Create a minimal Flask app with Fluent configured."""
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'test'
    app.config['BABEL_DEFAULT_LOCALE'] = 'en'
    app.config['BABEL_SUPPORTED_LOCALES'] = ['en', 'fr']

    # Create temp translation dir with test .ftl files
    tmpdir = tempfile.mkdtemp()
    en_dir = os.path.join(tmpdir, 'en')
    fr_dir = os.path.join(tmpdir, 'fr')
    os.makedirs(en_dir)
    os.makedirs(fr_dir)

    with open(os.path.join(en_dir, 'test.ftl'), 'w') as f:
        f.write('hello = Hello\n')
        f.write('greeting = Hello, { $name }!\n')
        f.write('items-count =\n')
        f.write('    { $count ->\n')
        f.write('        [one] { $count } item\n')
        f.write('       *[other] { $count } items\n')
        f.write('    }\n')
        f.write('has-apostrophe = It works\n')

    with open(os.path.join(fr_dir, 'test.ftl'), 'w') as f:
        f.write('hello = Bonjour\n')
        f.write('greeting = Bonjour, { $name } !\n')
        f.write('items-count =\n')
        f.write('    { $count ->\n')
        f.write('        [one] { $count } élément\n')
        f.write('       *[other] { $count } éléments\n')
        f.write('    }\n')
        f.write("has-apostrophe = C'est l'aide\n")

    app.config['FLUENT_TRANSLATION_DIRECTORIES'] = tmpdir
    init_fluent(app)

    yield app

    shutil.rmtree(tmpdir)


class TestFtlFunction:
    """Tests for the ftl() translation function."""

    def test_simple_message_english(self, app_with_fluent):
        with app_with_fluent.test_request_context():
            from flask import session
            session['language'] = 'en'
            # ftl() returns Markup, so compare with str()
            assert str(ftl('hello')) == 'Hello'

    def test_simple_message_french(self, app_with_fluent):
        with app_with_fluent.test_request_context():
            from flask import session
            session['language'] = 'fr'
            assert str(ftl('hello')) == 'Bonjour'

    def test_returns_markup_type(self, app_with_fluent):
        """ftl() should return Markup for Jinja2 auto-escaping compatibility."""
        from markupsafe import Markup
        with app_with_fluent.test_request_context():
            from flask import session
            session['language'] = 'en'
            result = ftl('hello')
            assert isinstance(result, Markup)

    def test_message_with_variable(self, app_with_fluent):
        with app_with_fluent.test_request_context():
            from flask import session
            session['language'] = 'en'
            assert str(ftl('greeting', name='World')) == 'Hello, World!'

    def test_plural_one(self, app_with_fluent):
        with app_with_fluent.test_request_context():
            from flask import session
            session['language'] = 'en'
            assert str(ftl('items-count', count=1)) == '1 item'

    def test_plural_other(self, app_with_fluent):
        with app_with_fluent.test_request_context():
            from flask import session
            session['language'] = 'en'
            assert str(ftl('items-count', count=5)) == '5 items'

    def test_missing_message_returns_id(self, app_with_fluent):
        with app_with_fluent.test_request_context():
            from flask import session
            session['language'] = 'en'
            result = ftl('nonexistent-message')
            assert result == 'nonexistent-message'

    def test_missing_french_falls_back_to_english(self, app_with_fluent):
        """If a message exists in EN but not FR, return the EN version."""
        # Add an EN-only message
        en_dir = os.path.join(
            app_with_fluent.config['FLUENT_TRANSLATION_DIRECTORIES'], 'en'
        )
        with open(os.path.join(en_dir, 'test.ftl'), 'a') as f:
            f.write('en-only = English only\n')
        # Re-init to pick up changes
        init_fluent(app_with_fluent)

        with app_with_fluent.test_request_context():
            from flask import session
            session['language'] = 'fr'
            assert str(ftl('en-only')) == 'English only'

    def test_apostrophe_escaping(self, app_with_fluent):
        """French apostrophes must be HTML-escaped for safety.

        ftl() wraps all output in markupsafe.escape(), which converts
        apostrophes to &#39;. This matches the old _escaped_gettext behavior
        and prevents French text from breaking HTML attributes and JS strings.
        """
        with app_with_fluent.test_request_context():
            from flask import session
            session['language'] = 'fr'
            result = ftl('has-apostrophe')
            result_str = str(result)
            # markupsafe.escape turns ' into &#39;
            assert '&#39;' in result_str


class TestForceLocale:
    """Tests for the force_locale context manager."""

    def test_force_locale_overrides_session(self, app_with_fluent):
        with app_with_fluent.test_request_context():
            from flask import session
            session['language'] = 'en'
            assert str(ftl('hello')) == 'Hello'
            with force_locale('fr'):
                assert 'Bonjour' in str(ftl('hello'))
            # Back to English after context manager exits
            assert str(ftl('hello')) == 'Hello'

    def test_force_locale_without_request_context(self, app_with_fluent):
        """force_locale must work outside request context (for report generation)."""
        with app_with_fluent.app_context():
            with force_locale('fr'):
                assert 'Bonjour' in str(ftl('hello'))


class TestLazyFtl:
    """Tests for the lazy_ftl() proxy."""

    def test_lazy_resolves_at_str_time(self, app_with_fluent):
        lazy_msg = lazy_ftl('hello')
        # Should not resolve yet — no request context
        with app_with_fluent.test_request_context():
            from flask import session
            session['language'] = 'fr'
            assert 'Bonjour' in str(lazy_msg)

    def test_lazy_works_in_different_locales(self, app_with_fluent):
        lazy_msg = lazy_ftl('hello')
        with app_with_fluent.test_request_context():
            from flask import session
            session['language'] = 'en'
            assert str(lazy_msg) == 'Hello'
            session['language'] = 'fr'
            assert 'Bonjour' in str(lazy_msg)

    def test_lazy_html_returns_markup(self, app_with_fluent):
        """__html__() should return Markup for Jinja2 compatibility."""
        from markupsafe import Markup
        lazy_msg = lazy_ftl('hello')
        with app_with_fluent.test_request_context():
            from flask import session
            session['language'] = 'en'
            assert isinstance(lazy_msg.__html__(), Markup)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_fluent.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'auto_a11y.web.fluent'`

- [ ] **Step 3: Implement the integration layer**

Create `auto_a11y/web/fluent.py`:

```python
"""Flask-Fluent integration layer.

Replaces Flask-Babel with fluent-compiler for i18n. Provides:
- ftl(message_id, **args) — resolve a Fluent message for the current locale
- lazy_ftl(message_id, **args) — lazy proxy that resolves at str() time
- force_locale(locale) — context manager to override locale
- init_fluent(app) — initialize Fluent on a Flask app

IMPORTANT API notes for fluent-compiler:
- bundle.format(message_id, args_dict) returns (value, errors_list), NOT a string
- bundle.has_message(message_id) checks existence (format raises KeyError if missing)
- Attributes are accessed via bundle.format('message-id.attr-name', args_dict)
- use_isolating=False disables Unicode bidi chars (not needed for EN/FR)
- The built-in Escaper system escapes interpolated variables only, not message text.
  For French apostrophe safety in JS/HTML contexts, templates must use | tojson for JS
  and double-quoted HTML attributes. The ftl() function additionally wraps all output
  in markupsafe.escape() to be safe by default (matching the old _escaped_gettext behavior).
"""
import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar

from babel.dates import format_datetime as babel_format_datetime
from flask import has_request_context, request, session
from fluent_compiler.bundle import FluentBundle
from markupsafe import Markup, escape

logger = logging.getLogger(__name__)

# Context variable for force_locale override.
# NOTE: ContextVar does NOT propagate to child threads by default in Python.
# Report generators that run in background threads must call force_locale()
# inside the thread, not in the parent. The existing codebase already does this
# (e.g., `with app.app_context(), force_locale(language):` inside each thread).
_locale_override: ContextVar[str | None] = ContextVar('_locale_override', default=None)

# Module-level state set by init_fluent()
_bundles: dict[str, FluentBundle] = {}
_default_locale: str = 'en'
_supported_locales: list[str] = ['en', 'fr']


def _get_current_locale() -> str:
    """Get the current locale, checking overrides first."""
    # 1. Check force_locale override
    override = _locale_override.get(None)
    if override:
        return override
    # 2. Check Flask session
    if has_request_context() and 'language' in session:
        return session['language']
    # 3. Check browser Accept-Language
    if has_request_context():
        return request.accept_languages.best_match(_supported_locales) or _default_locale
    # 4. Default
    return _default_locale


def ftl(message_id: str, **kwargs) -> Markup | str:
    """Resolve a Fluent message for the current locale.

    Returns a Markup-escaped string safe for HTML templates.
    Falls back: current locale → English → message ID.

    NOTE: All output is wrapped in markupsafe.escape() to prevent French
    apostrophes from breaking HTML attributes and JS strings. This matches
    the behavior of the old _escaped_gettext() wrapper. For JS contexts,
    templates should still use {{ ftl('id') | tojson }} for proper quoting.
    """
    locale = _get_current_locale()

    # Try current locale
    bundle = _bundles.get(locale)
    if bundle and bundle.has_message(message_id):
        value, errors = bundle.format(message_id, kwargs)
        if errors:
            logger.warning(f"Fluent format errors for '{message_id}': {errors}")
        return Markup(escape(value))

    # Fallback to English
    if locale != _default_locale:
        en_bundle = _bundles.get(_default_locale)
        if en_bundle and en_bundle.has_message(message_id):
            value, errors = en_bundle.format(message_id, kwargs)
            logger.debug(f"Fluent fallback to EN for '{message_id}' (locale={locale})")
            return Markup(escape(value))

    # Last resort: return the message ID
    logger.warning(f"Fluent message not found: '{message_id}' (locale={locale})")
    return message_id


def ftl_attr(message_id: str, attr: str, **kwargs) -> Markup | str:
    """Resolve a Fluent message attribute.

    Attributes are accessed via dot notation: 'message-id.attr-name'.
    Example: ftl_attr('search-input', 'placeholder')
    """
    locale = _get_current_locale()
    full_id = f"{message_id}.{attr}"

    bundle = _bundles.get(locale)
    if bundle and bundle.has_message(message_id):
        value, errors = bundle.format(full_id, kwargs)
        if errors:
            logger.warning(f"Fluent format errors for '{full_id}': {errors}")
        return Markup(escape(value))

    if locale != _default_locale:
        en_bundle = _bundles.get(_default_locale)
        if en_bundle and en_bundle.has_message(message_id):
            value, errors = en_bundle.format(full_id, kwargs)
            return Markup(escape(value))

    logger.warning(f"Fluent attribute not found: '{full_id}' (locale={locale})")
    return full_id


@contextmanager
def force_locale(locale: str):
    """Context manager to temporarily override the locale.

    Works both inside and outside Flask request context.
    Used by report generators to force a specific locale.

    IMPORTANT: ContextVar does not propagate to child threads.
    Call force_locale() inside the thread, not the parent.
    """
    token = _locale_override.set(locale)
    try:
        yield
    finally:
        _locale_override.reset(token)


class _LazyFtl:
    """Lazy proxy for ftl() — resolves at str() time, not import time."""

    __slots__ = ('_message_id', '_kwargs')

    def __init__(self, message_id: str, **kwargs):
        self._message_id = message_id
        self._kwargs = kwargs

    def __str__(self):
        return str(ftl(self._message_id, **self._kwargs))

    def __repr__(self):
        return f"lazy_ftl('{self._message_id}')"

    def __html__(self):
        # Return Markup so Jinja2 treats it as safe
        return Markup(str(self))

    def __eq__(self, other):
        if isinstance(other, str):
            return str(self) == other
        return NotImplemented

    def __bool__(self):
        return True


def lazy_ftl(message_id: str, **kwargs) -> _LazyFtl:
    """Return a lazy proxy that resolves the message at str() time.

    Use for strings defined at module scope (e.g., login_manager.login_message).
    """
    return _LazyFtl(message_id, **kwargs)


def _load_bundles(translations_dir: str) -> dict[str, FluentBundle]:
    """Load FluentBundle for each locale from .ftl files."""
    bundles = {}
    for locale in _supported_locales:
        locale_dir = os.path.join(translations_dir, locale)
        if not os.path.isdir(locale_dir):
            logger.warning(f"Fluent locale directory not found: {locale_dir}")
            continue

        ftl_files = sorted(
            os.path.join(locale_dir, f)
            for f in os.listdir(locale_dir)
            if f.endswith('.ftl')
        )
        if not ftl_files:
            logger.warning(f"No .ftl files found in {locale_dir}")
            continue

        try:
            # use_isolating=False: disable Unicode bidi isolation chars.
            # Not needed for EN/FR (both LTR). Avoids invisible chars in output
            # that break string comparisons and programmatic usage.
            bundle = FluentBundle.from_files(locale, ftl_files, use_isolating=False)
            bundles[locale] = bundle
            logger.info(f"Loaded Fluent bundle for '{locale}': {len(ftl_files)} files")
        except Exception as e:
            logger.error(f"Failed to load Fluent bundle for '{locale}': {e}")

    return bundles


def _datetimeformat_filter(value, format='medium'):
    """Locale-aware datetime formatting using Babel directly.

    Replaces Flask-Babel's format_datetime. Accepts the same format strings
    ('short', 'medium', 'long', 'full', or Babel pattern strings like 'yyyy-MM-dd').
    """
    if value is None:
        return ''
    locale = _get_current_locale()
    return babel_format_datetime(value, format, locale=locale)


def init_fluent(app):
    """Initialize Fluent on a Flask app.

    Call this instead of Babel(app, ...).
    Registers ftl(), ftl_attr(), and lazy_ftl() as Jinja2 globals.
    Registers datetimeformat filter.
    """
    global _bundles, _default_locale, _supported_locales

    _default_locale = app.config.get('BABEL_DEFAULT_LOCALE', 'en')
    _supported_locales = app.config.get('BABEL_SUPPORTED_LOCALES', ['en', 'fr'])

    translations_dir = app.config.get(
        'FLUENT_TRANSLATION_DIRECTORIES',
        os.path.join(os.path.dirname(__file__), 'translations')
    )

    _bundles = _load_bundles(translations_dir)

    # Register Jinja2 globals
    app.jinja_env.globals['ftl'] = ftl
    app.jinja_env.globals['ftl_attr'] = ftl_attr
    app.jinja_env.globals['lazy_ftl'] = lazy_ftl

    # Keep _() as an alias during dual-system migration
    # (remove after Phase 2 is complete)

    # Register datetime filter (replaces Flask-Babel's format_datetime)
    app.template_filter('datetimeformat')(_datetimeformat_filter)

    logger.info(f"Fluent initialized: {len(_bundles)} locales loaded")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_fluent.py -v`
Expected: All tests PASS

**IMPORTANT — Verify the fluent-compiler API before proceeding.** The implementation above is based on research, but the exact API may differ between versions. Before writing the integration layer, run this verification script:

```python
from fluent_compiler.bundle import FluentBundle
import tempfile, os

# Write a test .ftl file
tmpdir = tempfile.mkdtemp()
with open(os.path.join(tmpdir, 'test.ftl'), 'w') as f:
    f.write('hello = Hello\ngreeting = Hello, { $name }!\n')

bundle = FluentBundle.from_files('en', [os.path.join(tmpdir, 'test.ftl')], use_isolating=False)

# Check: does format() return a tuple (value, errors) or just a string?
result = bundle.format('hello')
print(f"format() return type: {type(result)}, value: {result!r}")

# Check: does has_message() exist?
print(f"has_message('hello'): {bundle.has_message('hello')}")
print(f"has_message('missing'): {bundle.has_message('missing')}")

# Check: what happens with a missing message?
try:
    result = bundle.format('missing')
    print(f"Missing message result: {result!r}")
except Exception as e:
    print(f"Missing message raises: {type(e).__name__}: {e}")
```

Adjust the `ftl()` implementation if the API differs from what's documented above. The key behaviors to verify:
- `bundle.format()` return type (tuple vs string)
- `bundle.has_message()` existence check
- Missing message behavior (KeyError vs None vs fallback string)
- Attribute access syntax (`'id.attr'` vs `attr=` kwarg)
- `use_isolating=False` parameter acceptance

- [ ] **Step 5: Commit**

```bash
git add auto_a11y/web/fluent.py tests/test_fluent.py
git commit -m "Add Flask-Fluent integration layer with tests"
```

---

## Task 3: Create Initial FTL Files and Proof of Concept

**Files:**
- Create: `auto_a11y/web/translations/en/common.ftl`
- Create: `auto_a11y/web/translations/fr/common.ftl`
- Create: `auto_a11y/web/translations/en/auth.ftl`
- Create: `auto_a11y/web/translations/fr/auth.ftl`
- Modify: `auto_a11y/web/app.py` (add Fluent init alongside Babel)
- Modify: `auto_a11y/web/templates/auth/login.html` (convert to ftl())

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p auto_a11y/web/translations/en
mkdir -p auto_a11y/web/translations/fr
```

- [ ] **Step 2: Create EN common.ftl with a few shared strings**

Create `auto_a11y/web/translations/en/common.ftl` with strings extracted from the login page and common UI elements. Look at `auto_a11y/web/templates/auth/login.html` and `auto_a11y/web/templates/base.html` to identify the exact strings used.

Example structure:
```ftl
# Common UI strings shared across features
common-save = Save
common-cancel = Cancel
common-delete = Delete
common-edit = Edit
common-close = Close
common-loading = Loading...
common-back = Back
common-search = Search
common-submit = Submit
```

- [ ] **Step 3: Create FR common.ftl with French translations**

Look up the French translations from `auto_a11y/web/translations/fr/LC_MESSAGES/messages.po` for each string in `en/common.ftl`.

- [ ] **Step 4: Create EN and FR auth.ftl**

Extract all `_()` strings from `auto_a11y/web/templates/auth/login.html` and create `en/auth.ftl` and `fr/auth.ftl`.

- [ ] **Step 5: Add Fluent init to app.py alongside Babel**

In `auto_a11y/web/app.py`, after the Babel initialization (line 113), add:

```python
# Fluent initialization (runs alongside Babel during migration)
from auto_a11y.web.fluent import init_fluent
init_fluent(app)
```

This enables both `_()` (Babel) and `ftl()` (Fluent) as Jinja2 globals simultaneously.

- [ ] **Step 6: Convert login.html to use ftl()**

In `auto_a11y/web/templates/auth/login.html`, replace every `{{ _('...') }}` with the corresponding `{{ ftl('auth-...') }}` call, using the message IDs defined in `auth.ftl`.

- [ ] **Step 7: Verify the login page renders correctly**

Run: `.venv/bin/python run.py --debug`

Visit `http://127.0.0.1:5001/auth/login` in both English and French.
Expected: Page renders identically to before the change. Switch language via the language selector and verify both work.

- [ ] **Step 8: Commit**

```bash
git add auto_a11y/web/translations/en/ auto_a11y/web/translations/fr/ auto_a11y/web/app.py auto_a11y/web/templates/auth/login.html
git commit -m "Add initial FTL files and convert login page as proof of concept"
```

---

## Task 4: Write the Bulk Migration Script

**Files:**
- Create: `scripts/migrate_po_to_ftl.py`

This script automates the tedious part: extracting ~2,700 messages from `.po`, generating message IDs, writing `.ftl` files, and rewriting templates. It is a one-time script.

- [ ] **Step 1: Write the migration script**

Create `scripts/migrate_po_to_ftl.py`. The script must:

1. **Parse `messages.po`** — extract all msgid/msgstr pairs, handling multi-line continuations (774 continuation lines exist). Reuse the parser pattern from `tests/test_translations.py:parse_po_entries()`.

2. **Map each message to a feature area** — use the `#:` source references in the `.po` file. If the reference is `auto_a11y/web/templates/auth/login.html:42`, the feature is `auth`. Map source paths to feature names:
   - `templates/auth/` → `auth`
   - `templates/pages/` → `pages`
   - `templates/projects/` → `projects`
   - `templates/websites/` → `websites`
   - `templates/reports/` → `reports`
   - `templates/testing/` → `testing`
   - `templates/schedules/` → `schedules`
   - `templates/recordings/` → `recordings`
   - `templates/scripts/` → `scripts`
   - `templates/public/` → `public`
   - `routes/` → use the route module name
   - Everything else → `common`
   - If a message appears in multiple feature areas, put it in `common`

3. **Generate message IDs** — for each msgid, create a kebab-case ID:
   - Strip `%(name)s` placeholders, strip punctuation
   - Lowercase, replace spaces with hyphens
   - Prefix with feature area: `auth-login-button`
   - Truncate to 60 chars max
   - Deduplicate (append `-2`, `-3`, etc. if collision)

4. **Write `.ftl` files** — one per feature area per locale. For messages with `%(name)s` placeholders, convert to Fluent `{ $name }` syntax.

5. **Rewrite template files** — for each `{{ _('English text') }}` in templates, replace with `{{ ftl('generated-id') }}`. For calls with format args like `{{ _('Hello %(name)s') % {'name': user} }}`, convert to `{{ ftl('greeting', name=user) }}`.

6. **Rewrite Python files** — for `_('text')` calls in `.py` files, replace with `ftl('id')`. For `lazy_gettext('text')`, replace with `lazy_ftl('id')`. For `ngettext(singular, plural, count)`, the `.ftl` file gets a selector and the Python call becomes `ftl('id', count=count)`.

7. **Generate a migration report** — JSON file listing every change: original string, new message ID, file, line, old code, new code. This is for manual review.

Key considerations:
- The script should be **idempotent** — skip files already converted (check for `ftl(` presence)
- Skip strings already handled in Task 3 (auth.ftl)
- **`ngettext` and `pgettext` should be flagged for manual review**, not auto-converted. Regex-based parsing of multi-argument function calls with nested string arguments is fragile. The script should: detect them, log them, and leave them for manual conversion in Task 5 Step 3-5.
- Log warnings for strings it can't auto-convert (complex format expressions, multi-line strings, etc.)
- Handle multi-line `.po` continuation strings correctly (774 exist) — concatenate `"line1" "line2"` into a single value

- [ ] **Step 2: Test the script in dry-run mode first**

Run: `.venv/bin/python scripts/migrate_po_to_ftl.py --dry-run`

The script should print what it would do without making changes. Review the output for:
- Are message IDs reasonable?
- Are feature area assignments correct?
- Are format string conversions correct?

- [ ] **Step 3: Commit the migration script**

```bash
git add scripts/migrate_po_to_ftl.py
git commit -m "Add bulk migration script for .po to .ftl conversion"
```

---

## Task 5: Run Bulk Migration and Manual Review

**Files:**
- Modify: All 87 template files
- Modify: Python files with `_()` calls
- Create/Modify: `auto_a11y/web/translations/en/*.ftl` and `fr/*.ftl`

- [ ] **Step 1: Run the migration script**

Run: `.venv/bin/python scripts/migrate_po_to_ftl.py`
Expected: Creates/updates `.ftl` files, rewrites templates and Python files. Produces `migration_report.json`.

- [ ] **Step 2: Review the migration report**

Read `migration_report.json`. Check for:
- Strings that couldn't be auto-converted (fix manually)
- Message IDs that are awkward or unclear (rename in both `.ftl` files and call sites)
- Format string conversions that look wrong

- [ ] **Step 3: Handle ngettext conversions manually**

Search for any remaining `ngettext` calls. The `.po` file has plural entries — verify each one was converted to a Fluent selector. Check `app.py` and `templates/projects/view.html` specifically.

For each `ngettext('%(num)d item', '%(num)d items', count)`, the FTL should be:
```ftl
items-count =
    { $count ->
        [one] { $count } item
       *[other] { $count } items
    }
```
And the Python/template call should be: `ftl('items-count', count=count)`

- [ ] **Step 4: Handle lazy_gettext conversions**

Check these specific locations:
- `auto_a11y/web/app.py:168` — `login_manager.login_message = lazy_gettext(...)` → `lazy_ftl('auth-login-required')`
- `auto_a11y/web/routes/pages.py` — any `lazy_gettext` in touchpoint label dicts
- `auto_a11y/web/routes/projects.py` — any `lazy_gettext` in touchpoint label dicts

- [ ] **Step 5: Handle pgettext conversions**

Check `auto_a11y/reporting/static_html_generator.py:19` for `pgettext` imports. Convert `pgettext('context', 'text')` to `ftl('context-text')` — Fluent IDs inherently carry context.

- [ ] **Step 6: Update static_html_generator.py standalone Jinja2 environment**

In `auto_a11y/reporting/static_html_generator.py` (lines 287-301):
- Remove `extensions=['jinja2.ext.i18n']` from the Jinja2 Environment
- Remove the `install_gettext_callables()` call
- Instead, register `ftl` as a Jinja2 global on the standalone environment:

```python
from auto_a11y.web.fluent import ftl, ftl_attr
self.template_env.globals['ftl'] = ftl
self.template_env.globals['ftl_attr'] = ftl_attr
```

- [ ] **Step 7: Replace all force_locale imports across reporting modules**

In each of these files, replace:
```python
from flask_babel import force_locale, gettext as _
```
with:
```python
from auto_a11y.web.fluent import ftl, force_locale
```

Files to update — **force_locale imports** (with line numbers):
- `auto_a11y/reporting/project_report.py:12`
- `auto_a11y/reporting/page_structure_report.py:14`
- `auto_a11y/reporting/discovery_report.py:20`
- `auto_a11y/reporting/recordings_report.py:13`
- `auto_a11y/reporting/static_html_generator.py:19`
- `auto_a11y/reporting/comprehensive_report.py:10`
- `auto_a11y/web/routes/reports.py:6`
- `auto_a11y/web/routes/pages.py:6`

Files to update — **gettext/lazy_gettext imports** (additional files not listed above):
- `auto_a11y/reporting/report_generator.py` — `gettext as _`
- `auto_a11y/reporting/wcag_mapper.py` — `lazy_gettext as _` (**~66 usages** — these are module-level dict values that need `lazy_ftl`)
- `auto_a11y/reporting/issue_descriptions_translated.py` — `get_locale` (replace with Fluent locale detection)
- `auto_a11y/core/permissions.py` — `gettext as _`
- `auto_a11y/testing/result_processor.py` — `gettext as _`
- `auto_a11y/web/routes/auth.py` — `_`
- `auto_a11y/web/routes/website_users.py` — `gettext as _`
- `auto_a11y/web/routes/project_participants.py` — `gettext as _`
- `auto_a11y/web/routes/members.py` — `gettext as _`
- `auto_a11y/web/routes/groups.py` — `gettext as _`
- `auto_a11y/web/routes/schedules.py` — `gettext as _`
- `auto_a11y/web/routes/scripts.py` — `gettext as _`
- `auto_a11y/web/routes/websites.py` — `gettext as _`
- `auto_a11y/web/routes/recordings.py` — `gettext as _`
- `auto_a11y/web/routes/discovered_pages.py` — `gettext as _`
- `auto_a11y/web/routes/share_tokens.py` — `_`
- `auto_a11y/web/routes/public.py` — `_`

For all files: replace `from flask_babel import ...` with `from auto_a11y.web.fluent import ftl` (and `force_locale`, `lazy_ftl` as needed). Replace `_('text')` calls with `ftl('id')`. The `with force_locale(self.language):` blocks stay syntactically the same — only the import source changes.

**Special case — `wcag_mapper.py`:** This file has ~66 `lazy_gettext` calls at module scope defining WCAG criterion labels. All must become `lazy_ftl('wcag-criterion-name')`. The migration script should handle this, but verify manually given the volume.

**Special case — `issue_descriptions_translated.py`:** This file wraps the issue description system and imports `get_locale` from Flask-Babel. Replace with `from auto_a11y.web.fluent import _get_current_locale` or make it use the Fluent bundle's locale detection.

- [ ] **Step 8: Remove Flask-Babel initialization from app.py**

In `auto_a11y/web/app.py`, remove:
- Line 7: `from flask_babel import Babel, format_datetime`
- Lines 88-94: Babel config (`BABEL_DEFAULT_LOCALE`, etc.) — keep these config keys, Fluent reuses them
- Line 113: `babel = Babel(app, locale_selector=get_locale)`
- Lines 118-129: `_escaped_gettext` and `_escaped_ngettext` wrappers and Jinja2 global assignments
- Lines 132-137: `datetimeformat_filter` (replaced by Fluent's version)
- Line 167: `from flask_babel import lazy_gettext`
- Line 168: Replace with `login_manager.login_message = lazy_ftl('auth-login-required')`
- Lines 179-206: `dynamic_translations` dict (these strings move to `.ftl` files)

Keep:
- Lines 101-111: `get_locale()` function — still used by Fluent's `_get_current_locale()`
- Lines 259-264: `set_language` route — unchanged

- [ ] **Step 9: Update test mocks in test_static_html_streaming.py**

In `tests/test_static_html_streaming.py` (lines 265-484), update mocks from:
```python
@patch('auto_a11y.reporting.static_html_generator.force_locale')
```
to:
```python
@patch('auto_a11y.web.fluent.force_locale')
```

Or, if the reporting modules import `force_locale` into their own namespace, patch the local reference:
```python
@patch('auto_a11y.reporting.static_html_generator.force_locale')
```
(This may stay the same — depends on how the import was done. Verify.)

**Note on `datetimeformat` filter:** The 5 templates using `|datetimeformat` require NO changes. The filter name stays the same — only the implementation changes (Babel directly instead of via Flask-Babel). Verify during the smoke test that date formatting still works in both locales.

- [ ] **Step 10: Verify the full app**

Run: `.venv/bin/python run.py --debug`

Test every major page in both EN and FR:
- Login, register, forgot password
- Dashboard
- Project list, project view
- Website list, website view
- Page list, page view (with test results)
- Reports dashboard
- Testing dashboard, fixture status
- Settings, user management

Expected: All pages render correctly in both languages. No `_()` calls remain.

- [ ] **Step 11: Run existing tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass (except `test_translations.py` which tests `.po` — will be rewritten in Task 8).

- [ ] **Step 12: Commit**

```bash
git add -A
git commit -m "Migrate all UI strings from gettext to Fluent

Converts ~2,700 messages and ~4,000 template call sites.
Replaces Flask-Babel with fluent-compiler for i18n.
Updates all reporting modules to use Fluent force_locale."
```

---

## Task 6: JavaScript Migration

**Files:**
- Create: `auto_a11y/web/static/js/fluent.js`
- Modify: `auto_a11y/web/templates/pages/view.html` (lines 3747-3774, 4093-4124)
- Modify: `auto_a11y/web/templates/automated_tests/list.html` (lines 208-224)
- Modify: `auto_a11y/web/templates/reports/dashboard.html` (lines 407-429)
- Modify: `auto_a11y/web/static/js/issue-filters.js` (lines 84, 339, 356)

- [ ] **Step 1: Decide on JS integration approach**

Two options:
- **Option A: Vendor `@fluent/bundle`** — download the minified bundle and serve it as a static file. Simpler, no build step.
- **Option B: Keep the current pattern but use ftl()** — templates still build JS objects, but use `{{ ftl('...') | tojson }}` instead of `{{ _('...') | tojson }}`. Less work, but doesn't share `.ftl` files with JS.

**Recommendation: Option B for now.** The current project has no JS build pipeline (no webpack/vite). Adding `@fluent/bundle` would require either vendoring a UMD build or adding a build step. Option B achieves the goal (no `_()` calls) with minimal disruption. The `.ftl` files are still the single source of truth — JS just receives pre-resolved strings via templates.

If a JS build pipeline is added later, Option A can be revisited.

- [ ] **Step 2: Update template-embedded JS translations**

In `auto_a11y/web/templates/pages/view.html`, replace:
```javascript
const i18n = {
    'screenshot': {{ _('Screenshot:') | tojson }},
    ...
};
```
with:
```javascript
const i18n = {
    'screenshot': {{ ftl('pages-screenshot') | tojson }},
    ...
};
```

Do the same for:
- `auto_a11y/web/templates/automated_tests/list.html` (lines 208-224)
- `auto_a11y/web/templates/reports/dashboard.html` (lines 407-429)
- All other templates that use `{{ _('...') | tojson }}` in JS blocks

The migration script from Task 4 should have handled most of these, but verify each one.

- [ ] **Step 3: Move dynamic_translations to FTL**

The impact level strings (`CRITICAL`, `HIGH`, `MEDIUM`, `LOW`) from `app.py`'s `dynamic_translations` dict (lines 181-206) should already be in `.ftl` files after Task 5. Verify they exist:

```ftl
# In en/common.ftl
impact-critical = Critical
impact-high = High
impact-medium = Medium
impact-low = Low
```

Update any JS code that used `window.translations[currentLang].CRITICAL` to use the template-resolved `{{ ftl('impact-critical') | tojson }}` pattern instead.

- [ ] **Step 4: Verify JS-driven functionality**

Test in browser:
- Page view: test status updates, filter panel, issue filtering
- Reports dashboard: job status, actions
- Automated tests list: filtering, deduplication

Expected: All JS-driven UI text appears correctly in both languages.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Migrate JavaScript translations to use Fluent via templates"
```

---

## Task 7: Migrate Issue Descriptions and WCAG Labels

**Files:**
- Create: `scripts/migrate_issues_to_ftl.py`
- Create: `auto_a11y/web/translations/en/issues.ftl`
- Create: `auto_a11y/web/translations/fr/issues.ftl`
- Create: `auto_a11y/web/translations/en/wcag.ftl`
- Create: `auto_a11y/web/translations/fr/wcag.ftl`
- Modify: `auto_a11y/web/app.py` (lines 39-40, 219-220) — remove old translation imports
- Modify: Templates that use `issue_fr` and `wcag_fr` context variables
- Delete: `auto_a11y/reporting/issue_translations_fr.json`
- Delete: `auto_a11y/reporting/issue_translations_inline.py`
- Delete: `auto_a11y/reporting/wcag_translations_fr.py`

- [ ] **Step 1: Write the issue migration script**

Create `scripts/migrate_issues_to_ftl.py`. It should:

1. Read `auto_a11y/reporting/issue_translations_fr.json`
2. Read the English issue descriptions from `auto_a11y/reporting/issue_descriptions_enhanced.py` (or wherever the EN originals live)
3. For each issue code, write to `en/issues.ftl` and `fr/issues.ftl` using attributes:

```ftl
ErrNoAlt =
    .title = Image has no alt attribute
    .what = The image element has no alt attribute...
    .why = Screen readers cannot describe images without alt text...
    .who = People who are blind or have low vision...
    .remediation = Add a descriptive alt attribute to the image...
```

4. Read `auto_a11y/reporting/wcag_translations_fr.py` and generate `en/wcag.ftl` and `fr/wcag.ftl`:

```ftl
wcag-non-text-content = Non-text Content
wcag-keyboard = Keyboard
wcag-focus-order = Focus Order
```

- [ ] **Step 2: Run the script**

Run: `.venv/bin/python scripts/migrate_issues_to_ftl.py`
Expected: Creates `en/issues.ftl`, `fr/issues.ftl`, `en/wcag.ftl`, `fr/wcag.ftl`

- [ ] **Step 3: Update app.py to load translations from Fluent**

Remove from `auto_a11y/web/app.py`:
- Line 39: `from auto_a11y.reporting.issue_translations_inline import ISSUE_DESCRIPTION_TRANSLATIONS_FR`
- Line 40: `from auto_a11y.reporting.wcag_translations_fr import WCAG_TRANSLATIONS_FR`
- Lines in `inject_globals()` that pass `issue_fr` and `wcag_fr`

Add helper functions in `auto_a11y/web/fluent.py` to retrieve issue descriptions and WCAG names via Fluent:

```python
def ftl_issue(code: str, field: str, **kwargs) -> Markup | str:
    """Get an issue description field: ftl_issue('ErrNoAlt', 'title')"""
    return ftl_attr(code, field, **kwargs)

def ftl_wcag(criterion_name: str) -> Markup | str:
    """Get a WCAG criterion name translation."""
    # Convert "Non-text Content" to "wcag-non-text-content"
    msg_id = 'wcag-' + criterion_name.lower().replace(' ', '-')
    return ftl(msg_id)
```

Register these as Jinja2 globals in `init_fluent()`.

- [ ] **Step 4: Update templates that use issue_fr and wcag_fr**

Templates and filters that currently do:
```python
issue_fr[code]['title']
```
should instead do:
```python
ftl_issue(code, 'title')
```

Update the `translate_issue` filter in `app.py` (lines 421-433) and the `wcag_name` filter (lines 390-419).

- [ ] **Step 5: Clean up imports in all files that referenced the old modules**

Search for any remaining imports of:
- `issue_translations_inline`
- `issue_translations_fr`
- `ISSUE_DESCRIPTION_TRANSLATIONS_FR`
- `wcag_translations_fr`
- `WCAG_TRANSLATIONS_FR`

Remove them and replace with Fluent calls.

- [ ] **Step 6: Delete old translation files**

```bash
rm auto_a11y/reporting/issue_translations_fr.json
rm auto_a11y/reporting/issue_translations_inline.py
rm auto_a11y/reporting/wcag_translations_fr.py
```

- [ ] **Step 7: Verify reports and issue details**

Run the app and check:
- Issue detail views show correct descriptions in both languages
- WCAG criterion labels display correctly
- Reports generate with proper translations (use `force_locale`)

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "Migrate issue descriptions and WCAG labels to Fluent FTL"
```

---

## Task 8: Rewrite Validation Script and Tests

**Files:**
- Modify: `scripts/validate_translations.py`
- Modify: `tests/test_translations.py`
- Modify: `.github/workflows/ci.yml` (lines 47-50)

- [ ] **Step 1: Rewrite validate_translations.py**

Replace the current `.po`-based validation with `.ftl`-based validation:

```python
"""Validate Fluent translation coverage.

Checks that every message in en/*.ftl has a corresponding message in fr/*.ftl.
"""
import os
import sys
from fluent.syntax import parse, ast as fluent_ast


def get_message_ids(ftl_dir):
    """Parse all .ftl files in a directory and return set of message IDs."""
    ids = set()
    for filename in sorted(os.listdir(ftl_dir)):
        if not filename.endswith('.ftl'):
            continue
        filepath = os.path.join(ftl_dir, filename)
        with open(filepath, 'r', encoding='utf-8') as f:
            resource = parse(f.read())
        for entry in resource.body:
            if isinstance(entry, fluent_ast.Message):
                ids.add(entry.id.name)
    return ids


def validate():
    translations_dir = os.path.join(
        os.path.dirname(__file__), os.pardir,
        'auto_a11y', 'web', 'translations'
    )
    en_dir = os.path.join(translations_dir, 'en')
    fr_dir = os.path.join(translations_dir, 'fr')

    en_ids = get_message_ids(en_dir)
    fr_ids = get_message_ids(fr_dir)

    missing_in_fr = en_ids - fr_ids
    extra_in_fr = fr_ids - en_ids

    errors = []
    if missing_in_fr:
        errors.append(f"{len(missing_in_fr)} messages missing in FR:")
        for mid in sorted(missing_in_fr):
            errors.append(f"  - {mid}")

    if extra_in_fr:
        # Warning, not error
        print(f"WARNING: {len(extra_in_fr)} messages in FR but not EN:")
        for mid in sorted(extra_in_fr):
            print(f"  - {mid}")

    if errors:
        print("ERRORS:")
        print("\n".join(errors))
        return 1

    print(f"OK: {len(en_ids)} EN messages, {len(fr_ids)} FR messages, 100% coverage")
    return 0


if __name__ == '__main__':
    sys.exit(validate())
```

- [ ] **Step 2: Rewrite tests/test_translations.py**

Replace the `.po`-based tests with `.ftl`-based tests:

```python
"""Tests for Fluent translation catalog quality."""
import os
from fluent.syntax import parse, ast as fluent_ast

TRANSLATIONS_DIR = os.path.join(
    os.path.dirname(__file__), os.pardir,
    'auto_a11y', 'web', 'translations',
)


def get_messages(locale):
    """Parse all .ftl files for a locale, return dict of id -> value."""
    locale_dir = os.path.join(TRANSLATIONS_DIR, locale)
    messages = {}
    for filename in sorted(os.listdir(locale_dir)):
        if not filename.endswith('.ftl'):
            continue
        with open(os.path.join(locale_dir, filename), 'r', encoding='utf-8') as f:
            resource = parse(f.read())
        for entry in resource.body:
            if isinstance(entry, fluent_ast.Message):
                messages[entry.id.name] = entry
    return messages


class TestFluentTranslationQuality:
    """Checks that catch common .ftl file problems."""

    def test_every_en_message_has_fr_translation(self):
        en = get_messages('en')
        fr = get_messages('fr')
        missing = set(en.keys()) - set(fr.keys())
        assert not missing, (
            f"{len(missing)} EN messages missing FR translation:\n"
            + "\n".join(f"  - {m}" for m in sorted(missing))
        )

    def test_no_empty_fr_messages(self):
        fr = get_messages('fr')
        empty = [mid for mid, msg in fr.items() if msg.value is None]
        assert not empty, (
            f"{len(empty)} FR messages have no value:\n"
            + "\n".join(f"  - {m}" for m in sorted(empty))
        )

    def test_ftl_files_parse_without_errors(self):
        for locale in ['en', 'fr']:
            locale_dir = os.path.join(TRANSLATIONS_DIR, locale)
            for filename in sorted(os.listdir(locale_dir)):
                if not filename.endswith('.ftl'):
                    continue
                with open(os.path.join(locale_dir, filename), 'r', encoding='utf-8') as f:
                    resource = parse(f.read())
                junk = [e for e in resource.body if isinstance(e, fluent_ast.Junk)]
                assert not junk, (
                    f"Parse errors in {locale}/{filename}:\n"
                    + "\n".join(j.content for j in junk)
                )
```

- [ ] **Step 3: Run the new tests**

Run: `.venv/bin/python -m pytest tests/test_translations.py -v`
Expected: All tests PASS

Run: `.venv/bin/python scripts/validate_translations.py`
Expected: `OK: N EN messages, N FR messages, 100% coverage`

- [ ] **Step 4: Update CI workflow**

In `.github/workflows/ci.yml`, replace lines 47-50:

```yaml
- name: Validate translations
  run: |
    python scripts/validate_translations.py
```

Remove the `pybabel compile` line — no compilation step needed.

- [ ] **Step 5: Commit**

```bash
git add scripts/validate_translations.py tests/test_translations.py .github/workflows/ci.yml
git commit -m "Rewrite translation validation for Fluent FTL format"
```

---

## Task 9: Cleanup

**Files:**
- Delete: `babel.cfg`, `messages.pot`, `messages.po`, `messages.mo`
- Modify: `requirements.txt` (remove flask-babel)
- Modify: `run.py` (remove compile_translations)
- Modify: `render-build.sh` (remove pybabel)
- Modify: `Dockerfile` (remove pybabel)
- Modify: `.gitignore` (remove *.mo)
- Modify: `CLAUDE.md`
- Modify: `README.md` + `README.fr.md`
- Delete: `scripts/migrate_po_to_ftl.py`, `scripts/migrate_issues_to_ftl.py`

- [ ] **Step 1: Delete gettext artifacts**

```bash
rm babel.cfg
rm auto_a11y/web/translations/messages.pot
rm auto_a11y/web/translations/fr/LC_MESSAGES/messages.po
rm auto_a11y/web/translations/fr/LC_MESSAGES/messages.mo
rmdir auto_a11y/web/translations/fr/LC_MESSAGES
rmdir auto_a11y/web/translations/fr  # Only if empty (it may have fr/*.ftl now)
```

Note: The `fr/` directory will still exist with `.ftl` files. Only remove `fr/LC_MESSAGES/`.

- [ ] **Step 2: Remove Flask-Babel from requirements**

In `requirements.txt`, remove:
```
flask-babel==4.0.0
```

Keep `babel==2.18.0` — it's still a dependency of `fluent-compiler` and used for `format_datetime`.

- [ ] **Step 3: Remove compile_translations from run.py**

In `run.py`, remove:
- Lines 133-160: The entire `compile_translations()` function
- Line 200: The `compile_translations()` call in `main()`

- [ ] **Step 4: Remove pybabel from build scripts**

In `render-build.sh`, remove lines 21-22:
```bash
echo "==> Compiling translations..."
pybabel compile -f -d auto_a11y/web/translations
```

In `Dockerfile`, remove lines 46-47:
```dockerfile
# Compile translations
RUN pybabel compile -f -d auto_a11y/web/translations
```

- [ ] **Step 5: Clean up .gitignore**

In `.gitignore`, remove line 30:
```
*.mo
```

- [ ] **Step 6: Delete one-time migration scripts**

```bash
rm scripts/migrate_po_to_ftl.py
rm scripts/migrate_issues_to_ftl.py
```

Also delete any other gettext-specific helper scripts that are no longer needed:
- `scripts/fix_format_errors_v2.py` (references pybabel)
- `scripts/fix_format_errors.py` (references pybabel)
- `scripts/add_issue_translations_to_po.py` (references pybabel)
- `scripts/import_translations.py` (references pybabel)

Check with the team before deleting these — they may have historical value.

- [ ] **Step 7: Verify no gettext references remain**

Run:
```bash
grep -r "flask_babel\|flask-babel\|Flask-Babel\|pybabel\|messages\.po\|messages\.mo\|msgid\|msgstr\|gettext\|ngettext\|lazy_gettext\|pgettext" --include="*.py" --include="*.html" --include="*.yml" --include="*.sh" --include="*.cfg" --include="*.txt" auto_a11y/ tests/ scripts/ .github/ *.py *.sh *.cfg Dockerfile
```

Expected: No matches (except possibly in comments, docs, or the `.ftl` migration report).

- [ ] **Step 8: Update CLAUDE.md translation section**

Replace the "Bilingual Translation Requirements" section in `CLAUDE.md` with updated instructions for the Fluent workflow:

- Replace `{{ _('...') }}` references with `{{ ftl('...') }}`
- Replace `pybabel extract/update/compile` workflow with `.ftl` file management
- Update the file paths (`.ftl` files instead of `.po`)
- Remove fuzzy marker warnings (Fluent has no fuzzy concept)
- Keep the apostrophe note (Fluent escaper handles this now)

- [ ] **Step 9: Update README.md and README.fr.md**

Update both READMEs to reflect the new translation system. Keep them in sync (mandatory per CLAUDE.md).

- [ ] **Step 10: Final smoke test**

Run: `.venv/bin/python run.py --debug`

Test every major page flow in both EN and FR. Specifically verify:
- Login, registration
- Project creation and editing
- Website and page management
- Test execution and results viewing
- Report generation (HTML, PDF) in both languages
- Issue details and WCAG labels
- Language switching

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "Remove gettext artifacts and complete Fluent migration

Removes Flask-Babel, babel.cfg, .po/.mo files, and compilation steps.
Updates CLAUDE.md, README.md, and README.fr.md for new workflow.
Cleans up one-time migration scripts."
```

---

## Task Dependencies

```
Task 1 (Install) → Task 2 (Integration Layer) → Task 3 (Proof of Concept)
                                                        ↓
Task 4 (Migration Script) → Task 5 (Bulk Migration) → Task 6 (JS Migration)
                                                        ↓
                                          Task 7 (Issues/WCAG) → Task 8 (Validation/CI) → Task 9 (Cleanup)
```

Tasks 1-3 are sequential (foundation).
Task 4 can start after Task 2 (needs to know the `ftl()` API).
Tasks 5-9 are sequential (each builds on the previous).

**Parallelizable:** Task 4 (write migration script) can be developed in parallel with Task 3 (proof of concept) since they don't modify the same files.
