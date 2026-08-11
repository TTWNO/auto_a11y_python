"""The fix registry: stable fix IDs to the functions that apply them.

Fix IDs are part of the stored record of what was done to a document and
are referenced by the fix-selection UI, so they are treated as a stable
API: rename a function freely, but never a key.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias

import pikepdf

from auto_a11y.pdf.fix import document_properties, forms
from auto_a11y.pdf.fix.models import FixOptions, FixResult

FixFn: TypeAlias = Callable[[pikepdf.Pdf, FixOptions], FixResult]


FIX_REGISTRY: dict[str, FixFn] = {
    # Document properties
    "fix_title": document_properties.fix_title,
    "fix_language": document_properties.fix_language,
    "fix_mark_info": document_properties.fix_mark_info,
    "fix_metadata": document_properties.fix_metadata,
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
