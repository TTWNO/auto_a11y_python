"""
Flask-Fluent integration layer.

Provides Fluent (Project Fluent) message resolution for Flask/Jinja2 templates,
replacing the previous Flask-Babel/gettext setup.

Usage in templates:
    {{ ftl('hello') }}
    {{ ftl('greeting', name='World') }}
    {{ ftl_attr('search-input', 'placeholder') }}
"""
from __future__ import annotations

import logging
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Generator, Sequence
from typing import Any, cast
from typing_extensions import override

from flask import Flask, request, session
from markupsafe import Markup, escape

from fluent_compiler.bundle import FluentBundle

logger = logging.getLogger(__name__)


class MissingTranslationError(KeyError):
    """Raised in debug (strict) mode when a Fluent message is missing
    from one or more supported locales, or when format-time errors occur.

    Subclasses ``KeyError`` so existing ``except KeyError`` blocks in
    production code paths continue to work unchanged in non-strict mode.
    """


# ContextVar for locale override via force_locale().
# NOTE: ContextVar does NOT propagate to child threads.  If you spawn
# threads inside a force_locale() block the override will not be visible
# in those threads.
_locale_override: ContextVar[str | None] = ContextVar('_locale_override', default=None)

# Module-level bundle registry: locale -> FluentBundle
_bundles: dict[str, FluentBundle] = {}

# Supported locales and default
_SUPPORTED_LOCALES: tuple[str, ...] = ('en', 'fr')
_DEFAULT_LOCALE: str = 'en'

# Module-level strict-mode flag. Set by init_fluent() from app.debug.
# When True, ftl() raises MissingTranslationError on any miss or format
# error in any supported locale, instead of logging + falling back.
_strict_mode: bool = False


def _is_strict() -> bool:
    """Return True if strict translation checking is enabled.

    Tests can monkey-patch this function (or the _strict_mode module
    variable) to control strict behavior per-test.
    """
    return _strict_mode


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ftl(message_id: str, **kwargs: object) -> Markup | str:
    """Resolve a Fluent message for the current locale.

    Falls back: current locale -> English -> message ID as plain string.

    Returns ``Markup(escape(value))`` on success so that French apostrophes
    are safely escaped for HTML/JS contexts.  On complete miss the raw
    message ID string is returned (not Markup) so callers can distinguish
    missing translations.

    In strict mode (Flask debug), raises ``MissingTranslationError`` if
    the message ID is missing from any supported locale.
    """
    # Strict-mode full-coverage check
    if _is_strict():
        missing = [
            loc for loc in _SUPPORTED_LOCALES
            if _resolve(loc, message_id, kwargs) is None
        ]
        if missing:
            raise MissingTranslationError(
                f"Fluent message {message_id!r} missing from locale(s): "
                f"{', '.join(missing)}\n"
                f"  Strict mode is active (Flask debug). Add this message ID to:\n"
                + "\n".join(
                    f"    auto_a11y/web/translations/{loc}/*.ftl"
                    for loc in missing
                )
            )

    # Non-strict path (also the post-strict-check path)
    locale = _get_current_locale()
    result = _resolve(locale, message_id, kwargs)
    if result is not None:
        value, errors = result
        if errors:
            logger.warning("Fluent errors for '%s' [%s]: %s", message_id, locale, errors)
        return Markup(escape(value))

    # Fallback to English
    if locale != _DEFAULT_LOCALE:
        result = _resolve(_DEFAULT_LOCALE, message_id, kwargs)
        if result is not None:
            value, errors = result
            if errors:
                logger.warning("Fluent errors for '%s' [%s]: %s", message_id, _DEFAULT_LOCALE, errors)
            return Markup(escape(value))

    # Complete miss — return the message ID as a plain string
    logger.warning("Missing Fluent message: %s", message_id)
    return message_id


def ftl_attr(message_id: str, attr: str, **kwargs: object) -> Markup | str:
    """Resolve a Fluent message attribute via ``message_id.attr``."""
    return ftl(f"{message_id}.{attr}", **kwargs)


@contextmanager
def force_locale(locale: str) -> Generator[None, None, None]:
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


def lazy_ftl(message_id: str, **kwargs: object) -> _LazyFtl:
    """Return a lazy proxy that resolves the Fluent message at ``str()`` time.

    Useful for module-level constants or anything evaluated before a request
    context exists (e.g. ``login_manager.login_message``).
    """
    return _LazyFtl(message_id, dict(kwargs))


# ---------------------------------------------------------------------------
# Issue description & WCAG helpers
# ---------------------------------------------------------------------------

# Lazy-loaded map: English text -> FTL message ID (for inline issue translations)
_inline_issue_ids: dict[str, str] | None = None


def _load_inline_issue_ids() -> dict[str, str]:
    """Load the inline issue ID map from the JSON file (once)."""
    global _inline_issue_ids
    if _inline_issue_ids is None:
        import json
        map_path = os.path.join(os.path.dirname(__file__), 'translations', 'inline_issue_ids.json')
        try:
            with open(map_path, 'r', encoding='utf-8') as f:
                loaded: dict[str, str] = json.load(f)
                _inline_issue_ids = loaded
        except FileNotFoundError:
            logger.warning("inline_issue_ids.json not found at %s", map_path)
            _inline_issue_ids = {}
    return _inline_issue_ids


def ftl_issue(code: str, field: str, **kwargs: object) -> Markup | str:
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
    slug = criterion_name.lower().strip()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    msg_id = f'wcag-{slug}'
    return ftl(msg_id)


def ftl_enum(value: object) -> Markup | str:
    """Translate an enum value string via Fluent.

    Converts 'discovery_failed' to 'enum-discovery-failed' and looks up
    the FTL message.  Falls back to title-cased original if no FTL message
    is found.
    """
    if not value:
        return str(value) if value is not None else ''
    # Normalize: lowercase, replace underscores/spaces with hyphens
    normalized = str(value).lower().replace('_', '-').replace(' ', '-')
    msg_id = f'enum-{normalized}'
    result = ftl(msg_id)
    # If ftl() returned the message ID itself (not found), fall back to title case
    if str(result) == msg_id:
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
        # No mapping -- return original text
        return text
    result = ftl(ftl_id)
    # If ftl() returned the message ID (miss), fall back to original text
    if str(result) == ftl_id:
        return text
    return str(result)


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

def init_fluent(app: Flask) -> None:
    """Initialize Fluent integration on *app*.

    * Loads ``.ftl`` files from the translations directory.
    * Registers ``ftl``, ``ftl_attr``, ``lazy_ftl`` as Jinja2 globals.
    * Registers the ``datetimeformat`` template filter.
    """
    global _strict_mode
    _strict_mode = bool(app.debug)

    translations_dir = os.path.join(os.path.dirname(__file__), 'translations')
    _load_bundles(translations_dir)

    # Jinja2 globals — cast to dict[str, Any] to bypass strict Jinja2 typing
    globals_dict = cast(dict[str, Any], app.jinja_env.globals)
    globals_dict['ftl'] = ftl
    globals_dict['ftl_attr'] = ftl_attr
    globals_dict['lazy_ftl'] = lazy_ftl
    globals_dict['ftl_issue'] = ftl_issue
    globals_dict['ftl_wcag'] = ftl_wcag
    globals_dict['ftl_enum'] = ftl_enum
    globals_dict['ftl_translate_issue'] = ftl_translate_issue

    # Template filters
    app.jinja_env.filters['datetimeformat'] = _datetimeformat_filter

    logger.info(
        "Fluent initialized — locales loaded: %s (strict_mode=%s)",
        ', '.join(sorted(_bundles.keys())) or '(none)',
        _strict_mode,
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
        lang: str | None = session.get('language')
        if lang and lang in _SUPPORTED_LOCALES:
            return lang
        best = request.accept_languages.best_match(_SUPPORTED_LOCALES)
        if best:
            return best
    except RuntimeError:
        # Outside request context -- fall through to default
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


def _resolve(locale: str, message_id: str, args: dict[str, object]) -> tuple[str, Sequence[object]] | None:
    """Try to format *message_id* in the given locale's bundle.

    Returns ``(value, errors)`` tuple where ``errors`` is the Fluent
    error list (empty list if none). Returns ``None`` if the message
    is not found at all in this bundle.

    Note: this function no longer logs format errors — callers decide
    whether to log or raise based on strict mode.
    """
    bundle = _bundles.get(locale)
    if bundle is None:
        return None
    try:
        value, errors = bundle.format(message_id, args or None)
        return (value, errors or [])
    except (KeyError, Exception):
        return None


def _datetimeformat_filter(value: Any, format: str = 'medium') -> str:
    """Jinja2 filter: format a datetime using Babel's locale-aware formatting."""
    if value is None:
        return ''
    from babel.dates import format_datetime
    locale = _get_current_locale()
    return str(format_datetime(value, format, locale=locale))


# ---------------------------------------------------------------------------
# Lazy proxy
# ---------------------------------------------------------------------------

class _LazyFtl:
    """Lazy proxy that defers Fluent message resolution until stringified."""

    __slots__ = ('_message_id', '_kwargs')

    def __init__(self, message_id: str, kwargs: dict[str, object]) -> None:
        self._message_id = message_id
        self._kwargs = kwargs

    @override
    def __str__(self) -> str:
        return str(ftl(self._message_id, **self._kwargs))

    def __html__(self) -> Markup:
        """Called by Jinja2's auto-escaping -- returns already-escaped Markup."""
        return Markup(str(self))

    @override
    def __repr__(self) -> str:
        return f"_LazyFtl({self._message_id!r})"

    @override
    def __eq__(self, other: object) -> bool:
        return str(self) == str(other)

    def __lt__(self, other: object) -> bool:
        return str(self) < str(other)

    def __le__(self, other: object) -> bool:
        return str(self) <= str(other)

    def __gt__(self, other: object) -> bool:
        return str(self) > str(other)

    def __ge__(self, other: object) -> bool:
        return str(self) >= str(other)

    @override
    def __hash__(self) -> int:
        return hash(str(self))

    def __bool__(self) -> bool:
        return bool(str(self))
