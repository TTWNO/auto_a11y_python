"""
Flask-Fluent integration layer.

Provides Fluent (Project Fluent) message resolution for Flask/Jinja2 templates,
replacing the previous Flask-Babel/gettext setup.

Usage in templates:
    {{ ftl('hello') }}
    {{ ftl('greeting', name='World') }}
    {{ ftl_attr('search-input', 'placeholder') }}
"""

import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar

from flask import request, session
from markupsafe import Markup, escape

logger = logging.getLogger(__name__)

# ContextVar for locale override via force_locale().
# NOTE: ContextVar does NOT propagate to child threads.  If you spawn
# threads inside a force_locale() block the override will not be visible
# in those threads.
_locale_override: ContextVar[str | None] = ContextVar('_locale_override', default=None)

# Module-level bundle registry: locale -> FluentBundle
_bundles: dict = {}

# Supported locales and default
_SUPPORTED_LOCALES = ('en', 'fr')
_DEFAULT_LOCALE = 'en'


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ftl(message_id: str, **kwargs) -> Markup | str:
    """Resolve a Fluent message for the current locale.

    Falls back: current locale -> English -> message ID as plain string.

    Returns ``Markup(escape(value))`` on success so that French apostrophes
    are safely escaped for HTML/JS contexts.  On complete miss the raw
    message ID string is returned (not Markup) so callers can distinguish
    missing translations.
    """
    locale = _get_current_locale()
    value = _resolve(locale, message_id, kwargs)
    if value is not None:
        return Markup(escape(value))

    # Fallback to English
    if locale != _DEFAULT_LOCALE:
        value = _resolve(_DEFAULT_LOCALE, message_id, kwargs)
        if value is not None:
            return Markup(escape(value))

    # Complete miss — return the message ID as a plain string
    logger.warning("Missing Fluent message: %s", message_id)
    return message_id


def ftl_attr(message_id: str, attr: str, **kwargs) -> Markup | str:
    """Resolve a Fluent message attribute via ``message_id.attr``."""
    return ftl(f"{message_id}.{attr}", **kwargs)


@contextmanager
def force_locale(locale: str):
    """Context manager that overrides the current locale.

    Works both inside and outside a Flask request context (it uses a
    ContextVar, not the session).

    NOTE: ContextVar does NOT propagate to child threads.

    Usage::

        with force_locale('fr'):
            label = ftl('greeting', name='World')
    """
    token = _locale_override.set(locale)
    try:
        yield
    finally:
        _locale_override.reset(token)


def lazy_ftl(message_id: str, **kwargs):
    """Return a lazy proxy that resolves the Fluent message at ``str()`` time.

    Useful for module-level constants or anything evaluated before a request
    context exists (e.g. ``login_manager.login_message``).
    """
    return _LazyFtl(message_id, kwargs)


# ---------------------------------------------------------------------------
# Issue description & WCAG helpers
# ---------------------------------------------------------------------------

# Lazy-loaded map: English text -> FTL message ID (for inline issue translations)
_inline_issue_ids: dict | None = None


def _load_inline_issue_ids() -> dict:
    """Load the inline issue ID map from the JSON file (once)."""
    global _inline_issue_ids
    if _inline_issue_ids is None:
        import json
        map_path = os.path.join(os.path.dirname(__file__), 'translations', 'inline_issue_ids.json')
        try:
            with open(map_path, 'r', encoding='utf-8') as f:
                _inline_issue_ids = json.load(f)
        except FileNotFoundError:
            logger.warning("inline_issue_ids.json not found at %s", map_path)
            _inline_issue_ids = {}
    return _inline_issue_ids


def ftl_issue(code: str, field: str, **kwargs) -> Markup | str:
    """Get an issue description field via Fluent attribute.

    Usage::

        ftl_issue('ErrNoAlt', 'title')
        ftl_issue('ErrNoAlt', 'what-generic')
    """
    return ftl_attr(code, field, **kwargs)


def ftl_wcag(criterion_name: str) -> Markup | str:
    """Get a WCAG criterion name translation via Fluent.

    Converts a criterion name like ``'Non-text Content'`` to the FTL message
    ID ``wcag-non-text-content`` and resolves it.
    """
    import re
    slug = criterion_name.lower().strip()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    msg_id = f'wcag-{slug}'
    return ftl(msg_id)


def ftl_enum(value) -> Markup | str:
    """Translate an enum value string via Fluent.

    Converts 'discovery_failed' to 'enum-discovery-failed' and looks up
    the FTL message.  Falls back to title-cased original if no FTL message
    is found.
    """
    if not value:
        return value or ''
    # Normalize: lowercase, replace underscores/spaces with hyphens
    normalized = str(value).lower().replace('_', '-').replace(' ', '-')
    msg_id = f'enum-{normalized}'
    result = ftl(msg_id)
    # If ftl() returned the message ID itself (not found), fall back to title case
    if isinstance(result, str) and result == msg_id:
        return str(value).replace('_', ' ').title()
    return result


def ftl_translate_issue(text: str) -> str:
    """Translate an inline issue description string via Fluent.

    Looks up the English text in the inline issue ID map, resolves the
    corresponding FTL message, and returns the translated string.
    Falls back to the original text if no mapping exists.

    This replaces the old ``translate_issue`` Jinja2 filter.
    """
    if not text:
        return text or ''
    id_map = _load_inline_issue_ids()
    ftl_id = id_map.get(text)
    if ftl_id is None:
        # No mapping — return original text
        return text
    result = ftl(ftl_id)
    # If ftl() returned the message ID (miss), fall back to original text
    if isinstance(result, str) and result == ftl_id:
        return text
    return str(result)


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

def init_fluent(app):
    """Initialize Fluent integration on *app*.

    * Loads ``.ftl`` files from the translations directory.
    * Registers ``ftl``, ``ftl_attr``, ``lazy_ftl`` as Jinja2 globals.
    * Registers the ``datetimeformat`` template filter.
    """
    translations_dir = os.path.join(os.path.dirname(__file__), 'translations')
    _load_bundles(translations_dir)

    # Jinja2 globals
    app.jinja_env.globals['ftl'] = ftl
    app.jinja_env.globals['ftl_attr'] = ftl_attr
    app.jinja_env.globals['lazy_ftl'] = lazy_ftl
    app.jinja_env.globals['ftl_issue'] = ftl_issue
    app.jinja_env.globals['ftl_wcag'] = ftl_wcag
    app.jinja_env.globals['ftl_enum'] = ftl_enum
    app.jinja_env.globals['ftl_translate_issue'] = ftl_translate_issue

    # Template filters
    app.jinja_env.filters['datetimeformat'] = _datetimeformat_filter

    logger.info(
        "Fluent initialized — locales loaded: %s",
        ', '.join(sorted(_bundles.keys())) or '(none)',
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_current_locale() -> str:
    """Determine the active locale.

    Priority:
    1. ContextVar override (set by ``force_locale``).
    2. ``session['language']`` (user preference).
    3. ``request.accept_languages`` (browser negotiation).
    4. Default ``'en'``.
    """
    # 1. ContextVar override
    override = _locale_override.get(None)
    if override is not None:
        return override

    # 2 & 3 require a request context
    try:
        lang = session.get('language')
        if lang and lang in _SUPPORTED_LOCALES:
            return lang
        best = request.accept_languages.best_match(_SUPPORTED_LOCALES)
        if best:
            return best
    except RuntimeError:
        # Outside request context — fall through to default
        pass

    return _DEFAULT_LOCALE


def _load_bundles(translations_dir: str) -> None:
    """Load FluentBundles from ``.ftl`` files on disk.

    Expected layout::

        translations_dir/
            en/
                messages.ftl
                other.ftl
            fr/
                messages.ftl
    """
    from fluent_compiler.bundle import FluentBundle

    global _bundles
    _bundles = {}

    if not os.path.isdir(translations_dir):
        logger.warning("Translations directory not found: %s", translations_dir)
        return

    for locale in os.listdir(translations_dir):
        locale_dir = os.path.join(translations_dir, locale)
        if not os.path.isdir(locale_dir):
            continue
        if locale not in _SUPPORTED_LOCALES:
            continue

        ftl_files = sorted(
            os.path.join(locale_dir, f)
            for f in os.listdir(locale_dir)
            if f.endswith('.ftl')
        )
        if not ftl_files:
            continue

        bundle = FluentBundle.from_files(locale, ftl_files, use_isolating=False)
        _bundles[locale] = bundle
        logger.debug("Loaded Fluent bundle for '%s' from %d file(s)", locale, len(ftl_files))


def _resolve(locale: str, message_id: str, args: dict) -> str | None:
    """Try to format *message_id* in the given locale's bundle.

    Returns the formatted string or ``None`` if the message is not found.
    """
    bundle = _bundles.get(locale)
    if bundle is None:
        return None
    if not bundle.has_message(message_id):
        # has_message returns False for attribute-style IDs like 'msg.attr',
        # but format() still works for them.  Try format() and catch KeyError.
        pass
    try:
        value, errors = bundle.format(message_id, args or None)
        if errors:
            logger.warning("Fluent errors for '%s' [%s]: %s", message_id, locale, errors)
        return value
    except (KeyError, Exception):
        return None


def _datetimeformat_filter(value, format='medium'):
    """Jinja2 filter: format a datetime using Babel's locale-aware formatting."""
    if value is None:
        return ''
    from babel.dates import format_datetime
    locale = _get_current_locale()
    return format_datetime(value, format, locale=locale)


# ---------------------------------------------------------------------------
# Lazy proxy
# ---------------------------------------------------------------------------

class _LazyFtl:
    """Lazy proxy that defers Fluent message resolution until stringified."""

    __slots__ = ('_message_id', '_kwargs')

    def __init__(self, message_id: str, kwargs: dict):
        object.__setattr__(self, '_message_id', message_id)
        object.__setattr__(self, '_kwargs', kwargs)

    def __str__(self) -> str:
        return str(ftl(self._message_id, **self._kwargs))

    def __html__(self) -> Markup:
        """Called by Jinja2's auto-escaping — returns already-escaped Markup."""
        return Markup(str(self))

    def __repr__(self) -> str:
        return f"_LazyFtl({self._message_id!r})"

    def __eq__(self, other):
        return str(self) == str(other)

    def __lt__(self, other):
        return str(self) < str(other)

    def __le__(self, other):
        return str(self) <= str(other)

    def __gt__(self, other):
        return str(self) > str(other)

    def __ge__(self, other):
        return str(self) >= str(other)

    def __hash__(self):
        return hash(str(self))

    def __bool__(self):
        return bool(str(self))
