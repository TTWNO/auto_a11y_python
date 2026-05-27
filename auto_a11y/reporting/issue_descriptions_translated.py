"""
Translation wrapper for issue descriptions.

This module wraps get_detailed_issue_description() to provide runtime translation
of accessibility issue descriptions using built-in translations stored in JSON.

The translations are loaded from issue_translations_fr.json which was extracted
from the PO file. This approach avoids pybabel update issues that comment out
translations for strings not found in direct _() calls.
"""
from __future__ import annotations

import re
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from auto_a11y.reporting.issue_descriptions_enhanced import (
    get_detailed_issue_description as _get_original_description,
    ImpactScale,
)

_TRANSLATIONS_CACHE: dict[str, dict[str, dict[str, str]]] = {}


_PLACEHOLDER_FALLBACKS_EN: dict[str, str] = {
    'skippedFrom': '?',
    'skippedTo': '?',
    'levelsSkipped': 'one or more',
    'expectedLevel': '?',
    'fromLevel': '?',
    'toLevel': '?',
    'foundLevel': '?',
    'firstHeadingLevel': '?',
    'current_level': '?',
    'suggested_level': '?',
    'next_level': '?',
    'heading_text': 'this heading',
    'headingText': 'this heading',
    'element': 'an element',
    'element_text': 'this element',
    'element_tag': 'element',
    'count': 'multiple',
    'itemCount': 'multiple',
    'fieldCount': 'several',
    'sizeCount': 'several',
    'linkCount': 'several',
    'fg': 'an unknown colour',
    'bg': 'an unknown colour',
    'ratio': 'a low value',
    'textColor': 'unknown',
    'backgroundColor': 'unknown',
    'fontSize': 'unknown',
    'minLineHeight': 'an adequate value',
    'pattern': 'a non-semantic pattern',
    'iconClasses': 'icon font classes',
    'animationName': 'an animation',
    'duration': 'too long',
}

_PLACEHOLDER_FALLBACKS_FR: dict[str, str] = {
    'skippedFrom': '?',
    'skippedTo': '?',
    'levelsSkipped': 'un ou plusieurs',
    'expectedLevel': '?',
    'fromLevel': '?',
    'toLevel': '?',
    'foundLevel': '?',
    'firstHeadingLevel': '?',
    'current_level': '?',
    'suggested_level': '?',
    'next_level': '?',
    'heading_text': 'ce titre',
    'headingText': 'ce titre',
    'element': 'un élément',
    'element_text': 'cet élément',
    'element_tag': 'élément',
    'count': 'plusieurs',
    'itemCount': 'plusieurs',
    'fieldCount': 'plusieurs',
    'sizeCount': 'plusieurs',
    'linkCount': 'plusieurs',
    'fg': 'une couleur inconnue',
    'bg': 'une couleur inconnue',
    'ratio': 'une valeur faible',
    'textColor': 'inconnue',
    'backgroundColor': 'inconnue',
    'fontSize': 'inconnue',
    'minLineHeight': 'une valeur adéquate',
    'pattern': 'un motif non sémantique',
    'iconClasses': 'des classes d\'icônes',
    'animationName': 'une animation',
    'duration': 'trop longue',
}


def _is_meaningful(value: Any) -> bool:
    """True when a metadata value is worth substituting.

    Empty strings, ``None`` and the literal strings ``"none"``/``"null"``/
    ``"undefined"`` (which the JS tests and AI analyzer emit for absent data)
    are treated as missing so the caller falls back to readable prose instead
    of rendering an empty fragment such as ``<h>`` or ``Heading ""``.
    """
    if value is None:
        return False
    text = str(value).strip()
    return text != '' and text.lower() not in ('none', 'null', 'undefined')


def _style_variants(key: str) -> list[str]:
    """Return ``key`` plus its camelCase/snake_case counterpart(s).

    Description templates and the metadata that fills them don't always agree
    on a naming style — e.g. a template asks for ``{current_level}`` while the
    test emits ``currentLevel``. Trying both styles recovers the real value
    instead of dropping to a fallback.
    """
    variants = [key]
    if '_' in key:
        head, *rest = key.split('_')
        camel = head + ''.join(w[:1].upper() + w[1:] for w in rest if w)
        if camel and camel not in variants:
            variants.append(camel)
    snake = re.sub(r'(?<!^)(?=[A-Z])', '_', key).lower()
    if snake not in variants:
        variants.append(snake)
    return variants


def _placeholder_fallback(key: str, locale: str) -> str:
    """Return a sensible fallback when a placeholder can't be filled from metadata.

    Prevents literal '{key}' strings from leaking into rendered reports. The
    fallback table is consulted with the raw key (and its camel/snake variants)
    first, then with the leading dotted/underscore segment (so e.g.
    'currentElement.tag' falls back via 'currentElement' if present).
    """
    table = _PLACEHOLDER_FALLBACKS_FR if locale == 'fr' else _PLACEHOLDER_FALLBACKS_EN
    for variant in _style_variants(key):
        if variant in table:
            return table[variant]
    base = key.split('.')[0]
    for variant in _style_variants(base):
        if variant in table:
            return table[variant]
    base = base.split('_')[0]
    return table.get(base, '')


def _metadata_lookup(metadata: dict[str, Any], path: str) -> str | None:
    """Resolve a (possibly dotted) placeholder path against ``metadata``.

    Walks each segment trying camelCase/snake_case variants, and treats
    empty/placeholder values as missing. Returns the stringified leaf value or
    ``None`` when nothing meaningful is found.
    """
    current: Any = metadata
    parts = path.split('.')
    for idx, segment in enumerate(parts):
        if not hasattr(current, 'get'):
            return None
        # ``.get`` only (no ``in``/subscript) so strict type-checkers don't
        # choke on the ``hasattr``-narrowed protocol type. An absent key and an
        # explicit ``None`` value are treated identically (both → skip variant).
        value: Any = None
        for variant in _style_variants(segment):
            candidate: Any = current.get(variant)
            if candidate is not None:
                value = candidate
                break
        if value is None:
            return None
        if idx == len(parts) - 1:
            if not _is_meaningful(value):
                return None
            return str(value)
        current = value
    return None


def _load_translations(lang: str) -> dict[str, dict[str, str]]:
    """Load translations for a language from JSON file."""
    if lang in _TRANSLATIONS_CACHE:
        return _TRANSLATIONS_CACHE[lang]
    
    translations_file = Path(__file__).parent / f'issue_translations_{lang}.json'
    if translations_file.exists():
        try:
            with open(translations_file, 'r', encoding='utf-8') as f:
                _TRANSLATIONS_CACHE[lang] = json.load(f)
                logger.debug(f"Loaded {len(_TRANSLATIONS_CACHE[lang])} issue translations for {lang}")
        except Exception as e:
            logger.warning(f"Failed to load translations for {lang}: {e}")
            _TRANSLATIONS_CACHE[lang] = {}
    else:
        _TRANSLATIONS_CACHE[lang] = {}
    
    return _TRANSLATIONS_CACHE[lang]


def _get_current_locale() -> str:
    """Get the current locale from Fluent integration."""
    try:
        from auto_a11y.web import fluent as _fluent_mod
        get_locale = getattr(_fluent_mod, '_get_current_locale')
        result: str = get_locale()
        return result
    except Exception:
        return 'en'


def _extract_error_type(issue_code: str) -> str:
    """Extract the error type from an issue code."""
    if issue_code.startswith('AI_'):
        return issue_code
    
    if '_' in issue_code:
        parts = issue_code.split('_')
        for i, part in enumerate(parts):
            if part.startswith(('Err', 'Warn', 'Info', 'Disco')):
                return '_'.join(parts[i:])
    
    return issue_code


def get_detailed_issue_description(issue_code: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Get detailed translated description for an issue code.

    Uses built-in JSON translations instead of pgettext for reliability.

    Args:
        issue_code: The issue code (e.g., 'headings_ErrEmptyHeading')
        metadata: Additional context about the specific issue instance

    Returns:
        Dictionary with translated description fields
    """
    desc = _get_original_description(issue_code, metadata)

    if not desc:
        return desc

    translated_desc = desc.copy()

    lang = _get_current_locale()

    if lang == 'en':
        return _apply_metadata(translated_desc, metadata)

    translations = _load_translations(lang)
    error_type = _extract_error_type(issue_code)

    if error_type in translations:
        issue_trans = translations[error_type]
        translatable_fields = ('title', 'what', 'what_generic', 'why', 'who', 'remediation')

        for field in translatable_fields:
            if field in issue_trans and issue_trans[field]:
                translated_desc[field] = issue_trans[field]

    return _apply_metadata(translated_desc, metadata)


def _apply_metadata(desc: dict[str, Any], metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Apply metadata substitution to description fields using {placeholder} syntax.

    Even when ``metadata`` is empty we still walk the fields so the final
    cleanup pass can replace any literal ``{placeholder}`` (which would
    otherwise leak to users for issues whose metadata didn't survive the
    pipeline) with a sensible fallback word.
    """
    if metadata is None:
        metadata = {}

    translatable_fields = ('title', 'what', 'what_generic', 'why', 'who', 'remediation')
    
    for field in translatable_fields:
        if field in desc and isinstance(desc[field], str):
            text = desc[field]

            # Special handling for contrast ratio placeholders
            # The test sends: textColor, backgroundColor, contrastRatio
            # Descriptions use: {fg}, {bg}, {ratio} OR %(fg)s, %(bg)s, %(ratio)s
            if ('{ratio}' in text or '%(ratio)s' in text) and 'contrastRatio' in metadata:
                contrast_ratio = metadata.get('contrastRatio', '')
                if isinstance(contrast_ratio, str) and contrast_ratio.endswith(':1'):
                    contrast_ratio = contrast_ratio[:-2]
                text = text.replace('{ratio}', str(contrast_ratio))
                text = text.replace('%(ratio)s', str(contrast_ratio))

            if ('{fg}' in text or '%(fg)s' in text) and 'textColor' in metadata:
                fg = str(metadata.get('textColor', ''))
                text = text.replace('{fg}', fg)
                text = text.replace('%(fg)s', fg)

            if ('{bg}' in text or '%(bg)s' in text) and 'backgroundColor' in metadata:
                bg = str(metadata.get('backgroundColor', ''))
                text = text.replace('{bg}', bg)
                text = text.replace('%(bg)s', bg)
            
            # Special handling for calculated line height minimum
            if '{minLineHeight}' in text and 'fontSize' in metadata:
                font_size = float(metadata.get('fontSize', 16))
                min_line_height = font_size * 1.5
                text = text.replace('{minLineHeight}', f"{min_line_height:.2f}")
            
            # Handle plural/singular forms for font sizes
            if ('{sizeCount_singular_size}' in text or '%(sizeCount_singular_size)s' in text) and 'sizeCount' in metadata:
                count = metadata.get('sizeCount', 0)
                locale = _get_current_locale()
                if locale == 'fr':
                    singular_size_text = 'taille' if count == 1 else 'tailles différentes'
                else:
                    singular_size_text = 'size' if count == 1 else 'different sizes'
                text = text.replace('{sizeCount_singular_size}', singular_size_text)
                text = text.replace('%(sizeCount_singular_size)s', singular_size_text)

            # Handle font sizes list
            if ('{fontSizes_list}' in text or '%(fontSizes_list)s' in text) and 'fontSizes' in metadata:
                sizes_raw = metadata.get('fontSizes', [])
                sizes_list: str = ', '.join(str(s) for s in sizes_raw) if hasattr(sizes_raw, '__iter__') and not isinstance(sizes_raw, str) else str(sizes_raw)
                text = text.replace('{fontSizes_list}', sizes_list)
                text = text.replace('%(fontSizes_list)s', sizes_list)
            
            if '{fieldCount_plural}' in text and 'fieldCount' in metadata:
                count = metadata.get('fieldCount', 0)
                text = text.replace('{fieldCount_plural}', 's' if count != 1 else '')
            
            # Handle search context placeholders
            if '{searchContext_title}' in text or '%(searchContext_title)s' in text:
                search_title = metadata.get('searchContext', {}).get('title', '') if isinstance(metadata.get('searchContext'), dict) else ''
                text = text.replace('{searchContext_title}', search_title)
                text = text.replace('%(searchContext_title)s', search_title)
            if '{searchContext_description}' in text or '%(searchContext_description)s' in text:
                search_desc = metadata.get('searchContext', {}).get('description', '') if isinstance(metadata.get('searchContext'), dict) else ''
                text = text.replace('{searchContext_description}', search_desc)
                text = text.replace('%(searchContext_description)s', search_desc)
            if '{searchContext_remediation}' in text or '%(searchContext_remediation)s' in text:
                search_rem = metadata.get('searchContext', {}).get('remediation', '') if isinstance(metadata.get('searchContext'), dict) else ''
                text = text.replace('{searchContext_remediation}', search_rem)
                text = text.replace('%(searchContext_remediation)s', search_rem)

            # Handle field types summary - generate from fieldTypes dict
            if '{fieldTypes_summary}' in text or '%(fieldTypes_summary)s' in text:
                field_types_raw = metadata.get('fieldTypes', {})
                if hasattr(field_types_raw, 'items') and field_types_raw:
                    # Generate summary like "2 text, 1 email"
                    field_parts = [f"{v} {k}" for k, v in field_types_raw.items()]
                    field_summary = ', '.join(field_parts)
                else:
                    field_summary = str(metadata.get('fieldTypes_summary', ''))
                text = text.replace('{fieldTypes_summary}', str(field_summary))
                text = text.replace('%(fieldTypes_summary)s', str(field_summary))

            # Handle label descriptions
            for label_key in ['asideLabel_description', 'footerLabel_description', 'headerLabel_description']:
                if '{' + label_key + '}' in text:
                    base_key = label_key.replace('_description', '')
                    label_data = metadata.get(base_key, {})
                    if hasattr(label_data, 'get'):
                        text = text.replace('{' + label_key + '}', str(label_data.get('description', '')))
            
            # Handle link count plural
            if '{linkCount_plural}' in text and 'linkCount' in metadata:
                count = metadata.get('linkCount', 0)
                text = text.replace('{linkCount_plural}', 's' if count != 1 else '')
            
            # Replace standard {key} placeholders from metadata
            nested_pattern = r'\{([^}]+)\}'
            locale = _get_current_locale()

            def replace_nested(match: re.Match[str]) -> str:
                path = match.group(1)

                # Skip special placeholders already handled above
                if path in ['fontSizes_list', 'sizeCount_plural', 'sizeCount_singular_size', 'fieldCount_plural',
                            'fieldTypes_summary', 'searchContext_title', 'searchContext_description',
                            'searchContext_remediation', 'minLineHeight', 'ratio', 'fg', 'bg',
                            'asideLabel_description', 'footerLabel_description', 'headerLabel_description',
                            'linkCount_plural']:
                    return match.group(0)

                result = _metadata_lookup(metadata, path)
                if result is not None:
                    return result
                logger.debug("Placeholder {%s} missing from metadata; using fallback.", path)
                return _placeholder_fallback(path, locale)

            text = re.sub(nested_pattern, replace_nested, text)

            # Also handle %(key)s style placeholders (used in French translations)
            percent_pattern = r'%\(([^)]+)\)s'

            def replace_percent(match: re.Match[str]) -> str:
                key = match.group(1)

                # Skip placeholders already handled above
                if key in ['ratio', 'fg', 'bg', 'sizeCount_singular_size', 'fontSizes_list',
                          'searchContext_title', 'searchContext_description', 'searchContext_remediation',
                          'fieldTypes_summary']:
                    return match.group(0)

                # Handle _description suffixed placeholders (for conditional label text)
                if key.endswith('_description'):
                    base_key = key.replace('_description', '')
                    label_value = metadata.get(base_key)
                    if label_value:
                        if locale == 'fr':
                            return f'Il a l\'étiquette "{label_value}".'
                        else:
                            return f'It has the label "{label_value}".'
                    else:
                        if locale == 'fr':
                            return "Il n'a pas d'étiquette."
                        else:
                            return "It has no label."

                # Handle _plural suffixed placeholders (for conditional 's')
                if key.endswith('_plural'):
                    base_key = key.replace('_plural', '')
                    count = metadata.get(base_key, 0)
                    # Return 's' for plural in English, or appropriate French plural marker
                    if isinstance(count, (int, float)) and count != 1:
                        return 's'
                    return ''

                # Look up value in metadata (camel/snake aware, empty == missing)
                resolved = _metadata_lookup(metadata, key)
                if resolved is not None:
                    return resolved
                logger.debug("Placeholder %%(%s)s missing from metadata; using fallback.", key)
                return _placeholder_fallback(key, locale)

            text = re.sub(percent_pattern, replace_percent, text)

            # Final safety net: any literal {single-token} that survived all the
            # passes above (e.g. typo in a template, brand-new placeholder added
            # to a description without a corresponding handler) gets replaced with
            # a graceful fallback rather than leaking '{varname}' to the report.
            def _cleanup_brace(match: re.Match[str]) -> str:
                inner = match.group(1)
                # Heuristic: only sweep placeholders that look like identifiers
                # (so we don't strip "{0}" inside example code or curly-quote text).
                if not re.match(r'^[A-Za-z_][A-Za-z0-9_.]*$', inner):
                    return match.group(0)
                logger.warning(
                    "Unhandled placeholder '{%s}' in field '%s' for issue with metadata keys %s",
                    inner, field, sorted(metadata.keys()) if metadata else [],
                )
                return _placeholder_fallback(inner, locale)

            text = re.sub(r'\{([^{}]+)\}', _cleanup_brace, text)

            desc[field] = text

    return desc


def format_issue_for_display(issue_code: str, violation_data: dict[str, Any]) -> dict[str, Any]:
    """
    Format an issue with all its metadata for display (translated).
    """
    description = get_detailed_issue_description(issue_code, violation_data)

    description['issue_id'] = issue_code
    description['location'] = violation_data.get('xpath', 'Not specified')
    description['element'] = violation_data.get('element', 'Not specified')
    description['url'] = violation_data.get('url', 'Not specified')

    return description


__all__ = [
    'get_detailed_issue_description',
    'format_issue_for_display',
    'ImpactScale',
]
