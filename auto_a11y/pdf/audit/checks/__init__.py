"""Phase 4 per-touchpoint check modules.

Each submodule of this package exposes:

* one function per individual check, with signature
  ``(ctx: AuditContext) -> list[CheckResult]``;
* a module-level registry list (e.g. ``HEADING_CHECKS``) listing those
  functions in the order the orchestrator should call them.

Phase 5.3 ``run_audit`` iterates ``ALL_CHECKS`` (the flat concatenation
of every registry list) and invokes each check function with the
populated :class:`auto_a11y.pdf.models.AuditContext`. Phase 6's check
catalogue iterates the same list to enumerate all known check names.
"""
from __future__ import annotations

from collections.abc import Callable

from auto_a11y.pdf.models import AuditContext, CheckResult

from .annotations import ANNOTATIONS_CHECKS
from .color_contrast import COLOR_CONTRAST_CHECKS
from .document_properties import DOCUMENT_PROPERTIES_CHECKS
from .fonts import FONTS_CHECKS
from .forms import FORMS_CHECKS
from .headings import HEADING_CHECKS
from .images_alt_text import IMAGES_ALT_TEXT_CHECKS
from .interactive import INTERACTIVE_CHECKS
from .language import LANGUAGE_CHECKS
from .links_navigation import LINKS_NAVIGATION_CHECKS
from .lists import LIST_CHECKS
from .tables import TABLE_CHECKS
from .tagging_structure import TAGGING_STRUCTURE_CHECKS

CheckFunction = Callable[[AuditContext], list[CheckResult]]

ALL_CHECKS: list[CheckFunction] = [
    *DOCUMENT_PROPERTIES_CHECKS,
    *TAGGING_STRUCTURE_CHECKS,
    *HEADING_CHECKS,
    *LANGUAGE_CHECKS,
    *IMAGES_ALT_TEXT_CHECKS,
    *ANNOTATIONS_CHECKS,
    *LINKS_NAVIGATION_CHECKS,
    *LIST_CHECKS,
    *TABLE_CHECKS,
    *FORMS_CHECKS,
    *INTERACTIVE_CHECKS,
    *COLOR_CONTRAST_CHECKS,
    *FONTS_CHECKS,
]

__all__ = [
    "ALL_CHECKS",
    "ANNOTATIONS_CHECKS",
    "CheckFunction",
    "COLOR_CONTRAST_CHECKS",
    "DOCUMENT_PROPERTIES_CHECKS",
    "FONTS_CHECKS",
    "FORMS_CHECKS",
    "HEADING_CHECKS",
    "IMAGES_ALT_TEXT_CHECKS",
    "INTERACTIVE_CHECKS",
    "LANGUAGE_CHECKS",
    "LINKS_NAVIGATION_CHECKS",
    "LIST_CHECKS",
    "TABLE_CHECKS",
    "TAGGING_STRUCTURE_CHECKS",
]
