"""Tests for the Phase 4 check registry."""
from __future__ import annotations

from auto_a11y.pdf.audit.checks import (
    ALL_CHECKS,
    ANNOTATIONS_CHECKS,
    COLOR_CONTRAST_CHECKS,
    DOCUMENT_PROPERTIES_CHECKS,
    FONTS_CHECKS,
    FORMS_CHECKS,
    HEADING_CHECKS,
    IMAGES_ALT_TEXT_CHECKS,
    INTERACTIVE_CHECKS,
    LANGUAGE_CHECKS,
    LINKS_NAVIGATION_CHECKS,
    LIST_CHECKS,
    TABLE_CHECKS,
    TAGGING_STRUCTURE_CHECKS,
)


def test_all_checks_is_concatenation_of_modules() -> None:
    """ALL_CHECKS contains every per-module registry's functions."""
    expected = (
        DOCUMENT_PROPERTIES_CHECKS
        + TAGGING_STRUCTURE_CHECKS
        + HEADING_CHECKS
        + LANGUAGE_CHECKS
        + IMAGES_ALT_TEXT_CHECKS
        + ANNOTATIONS_CHECKS
        + LINKS_NAVIGATION_CHECKS
        + LIST_CHECKS
        + TABLE_CHECKS
        + FORMS_CHECKS
        + INTERACTIVE_CHECKS
        + COLOR_CONTRAST_CHECKS
        + FONTS_CHECKS
    )
    assert ALL_CHECKS == expected


def test_all_checks_count_matches_phase_4_total() -> None:
    """Phase 4 ported ~97 checks across the 13 modules."""
    assert len(ALL_CHECKS) >= 90, (
        f"Expected at least 90 checks, got {len(ALL_CHECKS)} — "
        "did a registry get dropped?"
    )


def test_no_duplicate_check_functions() -> None:
    """Each check function appears in exactly one registry list."""
    assert len(ALL_CHECKS) == len(set(ALL_CHECKS))


def test_every_check_has_a_unique_name_via_qualname() -> None:
    """Function names should be unique across the whole audit."""
    names = [getattr(fn, "__qualname__", repr(fn)) for fn in ALL_CHECKS]
    assert len(names) == len(set(names))
