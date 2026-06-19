"""Regression tests for three LOW-severity audit-check bugs.

Covers:

* (Bug A) ``_find_interactive_descendants`` in
  :mod:`auto_a11y.pdf.audit.checks.images_alt_text` must not recurse
  unboundedly on a malformed structure tree (self-referential or
  duplicated ``children_indices``). It now mirrors the table/list BFS
  helpers' ``visited`` guard.
* (Bug B) ``check_alt_text_on_figure_art`` must report a 1-based index
  (``e.index + 1``) like every other check in the module, instead of
  the raw 0-based ``e.index``.
* (Bug C) ``_correlation_from_mismatches`` in
  :mod:`auto_a11y.pdf.audit.checks.tagging_structure` must clamp its
  result into ``[0, 1]`` so a ``mismatch_count`` from a different common
  set cannot surface a negative "correlation" percentage.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pikepdf

import auto_a11y.pdf.audit.checks.images_alt_text as _images_alt_text
import auto_a11y.pdf.audit.checks.tagging_structure as _tagging_structure
from auto_a11y.pdf.audit.checks.images_alt_text import (
    check_alt_text_on_figure_art,
)
from auto_a11y.pdf.audit.structure import StructElement
from auto_a11y.pdf.models import AuditContext, CheckResult


# The two helpers under test are module-private (leading underscore). We
# access them through ``getattr`` and re-wrap them in locally-typed
# callables so the strict checkers don't flag ``reportPrivateUsage`` while
# still type-checking the call sites. This mirrors the convention in
# ``tests/pdf/test_pdf_audit_job.py``.
def _find_interactive_descendants(
    elem: StructElement,
    elements: list[StructElement],
    index_map: dict[int, StructElement],
    results: list[StructElement],
) -> None:
    fn: Callable[
        [
            StructElement,
            list[StructElement],
            dict[int, StructElement],
            list[StructElement],
        ],
        None,
    ] = getattr(_images_alt_text, "_find_interactive_descendants")
    fn(elem, elements, index_map, results)


def _correlation_from_mismatches(
    common_size: int, mismatch_count: int
) -> float:
    fn: Callable[[int, int], float] = getattr(
        _tagging_structure, "_correlation_from_mismatches"
    )
    return fn(common_size, mismatch_count)


# ---------------------------------------------------------------------------
# Test helpers (mirror tests/pdf/test_checks_images_alt_text.py)
# ---------------------------------------------------------------------------


def _ctx_with_elements(elems: list[StructElement]) -> AuditContext:
    pdf = pikepdf.Pdf.new()
    return AuditContext(
        pdf=pdf,
        pdf_path=Path("/tmp/test.pdf"),
        elements=elems,
        role_map={},
    )


def _struct(
    index: int,
    tag: str,
    *,
    alt_text: str | None = None,
    parent_index: int = -1,
    children_indices: list[int] | None = None,
) -> StructElement:
    return StructElement(
        index=index,
        custom_tag=f"/{tag}",
        resolved_tag=tag,
        alt_text=alt_text,
        actual_text=None,
        lang=None,
        children_indices=children_indices if children_indices is not None else [],
        mcids=[],
        parent_index=parent_index,
        obj=pikepdf.Dictionary(),
    )


def _only(results: list[CheckResult]) -> CheckResult:
    assert len(results) == 1, f"expected one CheckResult, got {len(results)}"
    return results[0]


# ---------------------------------------------------------------------------
# Bug A — recursion guard in _find_interactive_descendants
# ---------------------------------------------------------------------------


def test_find_interactive_descendants_terminates_on_self_reference() -> None:
    """A node listing itself as a child must not recurse forever."""
    # Element 0 is its own child (and has an interactive child too).
    elems = [
        _struct(0, "Figure", alt_text="Logo", children_indices=[0, 1]),
        _struct(1, "Link", parent_index=0),
    ]
    index_map = {e.index: e for e in elems}
    results: list[StructElement] = []

    _find_interactive_descendants(elems[0], elems, index_map, results)

    # Terminates and finds the Link exactly once (no infinite recursion,
    # no duplicate from the self-cycle).
    assert [c.resolved_tag for c in results] == ["Link"]


def test_find_interactive_descendants_terminates_on_mutual_cycle() -> None:
    """Two nodes referencing each other must not recurse forever."""
    elems = [
        _struct(0, "Figure", alt_text="A", children_indices=[1]),
        _struct(1, "Span", parent_index=0, children_indices=[0, 2]),
        _struct(2, "Link", parent_index=1),
    ]
    index_map = {e.index: e for e in elems}
    results: list[StructElement] = []

    _find_interactive_descendants(elems[0], elems, index_map, results)

    assert any(c.resolved_tag == "Link" for c in results)
    # The Link is reachable once; the 0<->1 cycle must not multiply it.
    assert [c.resolved_tag for c in results].count("Link") == 1


def test_find_interactive_descendants_dedups_duplicated_child_index() -> None:
    """A duplicated child index must not double-count the descendant."""
    elems = [
        _struct(0, "Figure", alt_text="A", children_indices=[1, 1, 1]),
        _struct(1, "Link", parent_index=0),
    ]
    index_map = {e.index: e for e in elems}
    results: list[StructElement] = []

    _find_interactive_descendants(elems[0], elems, index_map, results)

    assert [c.resolved_tag for c in results] == ["Link"]


# ---------------------------------------------------------------------------
# Bug B — 1-based index in check_alt_text_on_figure_art
# ---------------------------------------------------------------------------


def test_alt_text_on_figure_art_reports_one_based_index() -> None:
    """Element at index 0 must be referenced as ``[1]`` in the FAIL detail."""
    elems = [_struct(0, "Figure")]  # missing alt -> FAIL
    res = _only(check_alt_text_on_figure_art(_ctx_with_elements(elems)))
    assert res.result == "FAIL"
    assert "[1]" in res.details
    assert "[0]" not in res.details


# ---------------------------------------------------------------------------
# Bug C — clamp correlation to [0, 1]
# ---------------------------------------------------------------------------


def test_correlation_clamped_to_zero_when_mismatch_exceeds_max() -> None:
    """mismatch_count > max_inversions must not yield a negative ratio."""
    # common_size=2 -> max_inversions = 2 * 2 / 2 = 2.0; mismatch 10 > 2.
    corr = _correlation_from_mismatches(common_size=2, mismatch_count=10)
    assert corr >= 0.0
    assert corr <= 1.0


def test_correlation_normal_case_unchanged() -> None:
    """A well-behaved input still returns the expected 1 - ratio value."""
    # common_size=4 -> max_inversions = 4 * 4 / 2 = 8.0; mismatch 2 -> 0.75.
    corr = _correlation_from_mismatches(common_size=4, mismatch_count=2)
    assert abs(corr - 0.75) < 1e-9


def test_correlation_perfect_when_no_mismatches() -> None:
    corr = _correlation_from_mismatches(common_size=4, mismatch_count=0)
    assert corr == 1.0
