"""The fix registry: stable fix IDs to the functions that apply them.

Fix IDs are part of the stored record of what was done to a document and
are referenced by the fix-selection UI, so they are treated as a stable
API: rename a function freely, but never a key.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias

import pikepdf

from auto_a11y.pdf.fix import alt_text, document_properties, forms, metadata
from auto_a11y.pdf.fix.models import FixOptions, FixResult

FixFn: TypeAlias = Callable[[pikepdf.Pdf, FixOptions], FixResult]


FIX_REGISTRY: dict[str, FixFn] = {
    # Document properties
    "fix_title": document_properties.fix_title,
    "fix_language": document_properties.fix_language,
    "fix_mark_info": document_properties.fix_mark_info,
    "fix_metadata": document_properties.fix_metadata,
    # Alternative text
    "fix_alt_text": alt_text.fix_alt_text,
    "fix_formula_alt": alt_text.fix_formula_alt,
    # XMP metadata, viewer preferences and conformance claims
    "fix_display_doc_title": metadata.fix_display_doc_title,
    "fix_pdfua_identifier": metadata.fix_pdfua_identifier,
    "fix_suspects": metadata.fix_suspects,
    "fix_xmp_title": metadata.fix_xmp_title,
    "fix_metadata_lang": metadata.fix_metadata_lang,
    "fix_accessibility_permission": metadata.fix_accessibility_permission,
    # Forms and tab order
    "fix_form_labels": forms.fix_form_labels,
    "fix_form_tab_order": forms.fix_form_tab_order,
    "fix_tab_order": forms.fix_tab_order,
    "fix_required_fields": forms.fix_required_fields,
    "fix_field_names_unique": forms.fix_field_names_unique,
    "fix_form_field_lang": forms.fix_form_field_lang,
}


def get_fix(fix_id: str) -> FixFn | None:
    """Return the function for ``fix_id``, or ``None`` if unknown."""
    return FIX_REGISTRY.get(fix_id)


def known_fix_ids() -> frozenset[str]:
    """Every fix ID this build can apply."""
    return frozenset(FIX_REGISTRY)
