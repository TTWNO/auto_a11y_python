"""Tests for ``auto_a11y.pdf.audit.checks.headings``.

Three heading-related checks ported from pdfMax's
``pdf_accessibility_audit.py`` (lines ~4822-4904):

* ``check_heading_hierarchy`` — first H1, single H1, no skipped levels.
* ``check_no_multiple_headings_per_node`` — Matterhorn 14-006.
* ``check_no_mixed_heading_tag_types`` — Matterhorn 14-007.

These tests also pin the convention that every Phase 4 check module
follows: a ``ctx: AuditContext -> list[CheckResult]`` signature and a
module-level registry list (``HEADING_CHECKS``) that the orchestrator
iterates.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf

from auto_a11y.pdf.audit.checks.headings import (
    HEADING_CHECKS,
    check_heading_hierarchy,
    check_heading_size_hierarchy,
    check_no_mixed_heading_tag_types,
    check_no_multiple_headings_per_node,
)
from auto_a11y.pdf.audit.structure import StructElement
from auto_a11y.pdf.models import AuditContext, CheckResult


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _ctx_with_elements(elems: list[StructElement]) -> AuditContext:
    """Build a minimal ``AuditContext`` with just the structure elements.

    The heading checks read only ``ctx.elements`` so the other context
    fields can take throwaway values.
    """
    pdf = pikepdf.Pdf.new()
    return AuditContext(
        pdf=pdf,
        pdf_path=Path("/tmp/test.pdf"),
        elements=elems,
        role_map={},
    )


def _heading(
    index: int,
    level: int | None,
    *,
    parent_index: int = -1,
    children_indices: list[int] | None = None,
) -> StructElement:
    """Build a ``StructElement`` representing an H1-H6 (or generic ``H``).

    ``level=None`` produces the generic ``/H`` tag; otherwise produces
    ``/H{level}``. The element is otherwise empty (no MCIDs, no text,
    no alt). Pass ``parent_index``/``children_indices`` when a test
    needs them.
    """
    tag = f"H{level}" if level is not None else "H"
    return StructElement(
        index=index,
        custom_tag=f"/{tag}",
        resolved_tag=tag,
        alt_text=None,
        actual_text=None,
        lang=None,
        children_indices=children_indices if children_indices is not None else [],
        mcids=[],
        parent_index=parent_index,
        obj=pikepdf.Dictionary(),
    )


def _non_heading(
    index: int,
    tag: str,
    *,
    parent_index: int = -1,
    children_indices: list[int] | None = None,
) -> StructElement:
    """Build a ``StructElement`` for a non-heading tag (e.g. ``Sect``)."""
    return StructElement(
        index=index,
        custom_tag=f"/{tag}",
        resolved_tag=tag,
        alt_text=None,
        actual_text=None,
        lang=None,
        children_indices=children_indices if children_indices is not None else [],
        mcids=[],
        parent_index=parent_index,
        obj=pikepdf.Dictionary(),
    )


def _only(results: list[CheckResult]) -> CheckResult:
    """Assert single-element list and return its sole entry."""
    assert len(results) == 1, f"expected one CheckResult, got {len(results)}"
    return results[0]


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------


def test_heading_checks_registry_lists_every_check() -> None:
    """``HEADING_CHECKS`` is the phase-5 entry point — must list everything."""
    assert HEADING_CHECKS == [
        check_heading_hierarchy,
        check_no_multiple_headings_per_node,
        check_no_mixed_heading_tag_types,
        check_heading_size_hierarchy,
    ]


# ---------------------------------------------------------------------------
# check_heading_hierarchy
# ---------------------------------------------------------------------------


def test_heading_hierarchy_passes_on_well_formed_h1_h2_h3() -> None:
    elems = [
        _heading(0, 1),
        _heading(1, 2),
        _heading(2, 3),
    ]
    res = _only(check_heading_hierarchy(_ctx_with_elements(elems)))
    assert res.name == "Heading hierarchy valid"
    assert res.standard == "WCAG 1.3.1, 2.4.6"
    assert res.result == "PASS"
    assert "H1" in res.details and "H2" in res.details and "H3" in res.details


def test_heading_hierarchy_passes_on_no_headings() -> None:
    """pdfMax behaviour: no headings PASSes with a 'none found' note."""
    elems = [_non_heading(0, "Document")]
    res = _only(check_heading_hierarchy(_ctx_with_elements(elems)))
    assert res.result == "PASS"
    assert "none found" in res.details


def test_heading_hierarchy_fails_when_first_heading_is_not_h1() -> None:
    elems = [
        _heading(0, 2),
        _heading(1, 3),
    ]
    res = _only(check_heading_hierarchy(_ctx_with_elements(elems)))
    assert res.result == "FAIL"
    assert "first heading is H2" in res.details


def test_heading_hierarchy_fails_on_multiple_h1() -> None:
    elems = [
        _heading(0, 1),
        _heading(1, 2),
        _heading(2, 1),
    ]
    res = _only(check_heading_hierarchy(_ctx_with_elements(elems)))
    assert res.result == "FAIL"
    assert "Multiple H1 headings" in res.details
    # Both H1 indices should be referenced.
    assert "[0]" in res.details and "[2]" in res.details


def test_heading_hierarchy_fails_on_skipped_level() -> None:
    elems = [
        _heading(0, 1),
        _heading(1, 3),  # skips H2
    ]
    res = _only(check_heading_hierarchy(_ctx_with_elements(elems)))
    assert res.result == "FAIL"
    assert "skipped level" in res.details


# ---------------------------------------------------------------------------
# check_no_multiple_headings_per_node
# ---------------------------------------------------------------------------


def test_multiple_headings_per_node_passes_on_one_heading_per_parent() -> None:
    # Sect[0] -> [H1[1], P[2]]
    elems = [
        _non_heading(0, "Sect", children_indices=[1, 2]),
        _heading(1, 1, parent_index=0),
        _non_heading(2, "P", parent_index=0),
    ]
    res = _only(check_no_multiple_headings_per_node(_ctx_with_elements(elems)))
    assert res.name == "No multiple headings per node"
    assert res.standard == "Matterhorn 14-006"
    assert res.result == "PASS"


def test_multiple_headings_per_node_fails_on_two_headings_under_one_parent() -> None:
    # Sect[0] -> [H1[1], H2[2]]
    elems = [
        _non_heading(0, "Sect", children_indices=[1, 2]),
        _heading(1, 1, parent_index=0),
        _heading(2, 2, parent_index=0),
    ]
    res = _only(check_no_multiple_headings_per_node(_ctx_with_elements(elems)))
    assert res.result == "FAIL"
    assert "Sect" in res.details
    assert "H1" in res.details and "H2" in res.details


def test_multiple_headings_per_node_passes_on_empty_elements() -> None:
    res = _only(check_no_multiple_headings_per_node(_ctx_with_elements([])))
    assert res.result == "PASS"


# ---------------------------------------------------------------------------
# check_no_mixed_heading_tag_types
# ---------------------------------------------------------------------------


def test_mixed_heading_tag_types_passes_on_all_numbered() -> None:
    elems = [_heading(0, 1), _heading(1, 2), _heading(2, 3)]
    res = _only(check_no_mixed_heading_tag_types(_ctx_with_elements(elems)))
    assert res.name == "No mixed heading tag types"
    assert res.standard == "Matterhorn 14-007"
    assert res.result == "PASS"


def test_mixed_heading_tag_types_passes_on_all_generic_h() -> None:
    elems = [_heading(0, None), _heading(1, None)]
    res = _only(check_no_mixed_heading_tag_types(_ctx_with_elements(elems)))
    assert res.result == "PASS"


def test_mixed_heading_tag_types_passes_on_no_headings() -> None:
    elems = [_non_heading(0, "Document"), _non_heading(1, "P")]
    res = _only(check_no_mixed_heading_tag_types(_ctx_with_elements(elems)))
    assert res.result == "PASS"


def test_mixed_heading_tag_types_fails_when_both_systems_present() -> None:
    elems = [
        _heading(0, 1),
        _heading(1, None),  # generic H mixed with H1
        _heading(2, 2),
    ]
    res = _only(check_no_mixed_heading_tag_types(_ctx_with_elements(elems)))
    assert res.result == "FAIL"
    assert "generic H" in res.details
    assert "numbered H1-H6" in res.details
