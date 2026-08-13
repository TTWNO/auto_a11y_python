"""List-structure-related accessibility checks.

Four checks ported from pdfMax's
``python/checker/pdf_accessibility_audit.py``:

* :func:`check_list_structure_valid` (Matterhorn 09-006 / WCAG 1.3.1)
  — pdfMax line ~6607. ``L`` elements only contain ``LI`` children, and
  every ``LI`` carries either ``Lbl`` or ``LBody``.
* :func:`check_no_empty_lists` (Matterhorn 09-006) — pdfMax line ~6649.
  ``L`` elements have at least one child.
* :func:`check_list_nesting_valid` (PDF/UA, WCAG 1.3.1) — pdfMax line
  ~6671. Nested ``L`` elements appear inside ``LBody``, never as a
  direct child of an outer ``L`` or ``LI``.
* :func:`check_list_item_labels` (PDF/UA best practice) — pdfMax line
  ~6707. Every non-empty ``LI`` carries an ``Lbl``.

* :func:`check_untagged_lists_detected` (WCAG 1.3.1) — pdfMax line
  ~6733. Runs of sibling paragraphs whose text carries list markers but
  which are tagged ``P`` rather than ``L`` > ``LI``. The heuristic lives
  in :mod:`auto_a11y.pdf.audit.candidates` because the AI semantic pass
  consumes the same shortlist.

Mirrors the convention established in
:mod:`auto_a11y.pdf.audit.checks.headings`: each check is a plain
function ``(ctx) -> list[CheckResult]`` and the module exposes a
:data:`LIST_CHECKS` registry list. ``CheckResult.name`` and
``CheckResult.standard`` strings match pdfMax's verbatim so Phase 6's
catalogue can map them.
"""
from __future__ import annotations

from collections.abc import Callable

from auto_a11y.pdf.audit.candidates import find_list_candidates
from auto_a11y.pdf.audit.structure import StructElement
from auto_a11y.pdf.models import AuditContext, CheckResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _children(elem: StructElement, all_elements: list[StructElement]) -> list[StructElement]:
    """Resolve a StructElement's children-by-index against the flat list."""
    return [
        all_elements[ci]
        for ci in elem.children_indices
        if 0 <= ci < len(all_elements)
    ]


# ---------------------------------------------------------------------------
# check_list_structure_valid
# ---------------------------------------------------------------------------


def check_list_structure_valid(ctx: AuditContext) -> list[CheckResult]:
    """PDF/UA, WCAG 1.3.1: ``L`` -> ``LI`` -> ``Lbl``/``LBody`` structure.

    Mirrors pdfMax line ~6607. PASS when every ``L`` has only ``LI``
    children, and every non-empty ``LI`` has a ``Lbl`` or ``LBody``
    child. Truly empty lists (no children at all) are skipped here —
    :func:`check_no_empty_lists` covers them.
    """
    elements = ctx.elements
    list_elements = [e for e in elements if e.resolved_tag == "L"]
    if not list_elements:
        return [
            CheckResult(
                name="List structure valid",
                standard="PDF/UA, WCAG 1.3.1",
                result="NA",
                details="No lists found in document",
            )
        ]

    list_issues: list[str] = []
    for list_elem in list_elements:
        children = _children(list_elem, elements)
        if not children:
            # Empty L — covered by check_no_empty_lists.
            continue
        non_li = [c for c in children if c.resolved_tag != "LI"]
        if non_li:
            bad_tags = ", ".join(c.resolved_tag for c in non_li[:3])
            list_issues.append(
                f"[{list_elem.index + 1}] L has non-LI children: {bad_tags}"
            )
        for child in children:
            if child.resolved_tag != "LI":
                continue
            li_children = _children(child, elements)
            li_child_tags = {c.resolved_tag for c in li_children}
            if li_children and not (li_child_tags & {"Lbl", "LBody"}):
                list_issues.append(
                    f"[{child.index + 1}] LI missing Lbl/LBody (has: "
                    + f"{', '.join(sorted(li_child_tags))})"
                )

    if not list_issues:
        non_empty = [
            e
            for e in list_elements
            if any(
                elements[ci].resolved_tag == "LI"
                for ci in e.children_indices
                if 0 <= ci < len(elements)
            )
        ]
        return [
            CheckResult(
                name="List structure valid",
                standard="PDF/UA, WCAG 1.3.1",
                result="PASS",
                details=(
                    f"All {len(non_empty)} list(s) have valid"
                    " L > LI > Lbl/LBody structure"
                ),
            )
        ]
    return [
        CheckResult(
            name="List structure valid",
            standard="PDF/UA, WCAG 1.3.1",
            result="FAIL",
            details=(
                f"{len(list_issues)} list structure issue(s):"
                f" {'; '.join(list_issues[:5])}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_no_empty_lists
# ---------------------------------------------------------------------------


def check_no_empty_lists(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 09-006: ``L`` elements have at least one child.

    Mirrors pdfMax line ~6649.
    """
    elements = ctx.elements
    list_elements = [e for e in elements if e.resolved_tag == "L"]
    if not list_elements:
        return [
            CheckResult(
                name="No empty lists",
                standard="Matterhorn 09-006",
                result="NA",
                details="No lists found in document",
            )
        ]

    empty_lists: list[str] = []
    for list_elem in list_elements:
        children = _children(list_elem, elements)
        if not children:
            empty_lists.append(f"[{list_elem.index + 1}] L (no children)")

    if not empty_lists:
        return [
            CheckResult(
                name="No empty lists",
                standard="Matterhorn 09-006",
                result="PASS",
                details=(
                    f"All {len(list_elements)} list(s) contain list items"
                ),
            )
        ]
    return [
        CheckResult(
            name="No empty lists",
            standard="Matterhorn 09-006",
            result="FAIL",
            details=(
                f"{len(empty_lists)} empty list(s) with no list items:"
                f" {'; '.join(empty_lists[:5])}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_list_nesting_valid
# ---------------------------------------------------------------------------


def check_list_nesting_valid(ctx: AuditContext) -> list[CheckResult]:
    """PDF/UA, WCAG 1.3.1: nested ``L`` lives inside ``LBody``.

    Mirrors pdfMax line ~6671. WARN when an ``L`` is a direct child of
    another ``L`` or of an ``LI`` (it should be wrapped in ``LBody``).
    """
    elements = ctx.elements
    list_elements = [e for e in elements if e.resolved_tag == "L"]
    if not list_elements:
        return [
            CheckResult(
                name="List nesting valid",
                standard="PDF/UA, WCAG 1.3.1",
                result="NA",
                details="No lists found in document",
            )
        ]

    nesting_issues: list[str] = []
    for list_elem in list_elements:
        children = _children(list_elem, elements)
        # L directly inside L (should be in LBody)
        for child in children:
            if child.resolved_tag == "L":
                nesting_issues.append(
                    f"[{child.index + 1}] Nested L is direct child of L"
                    + " (should be inside LBody)"
                )
        # L directly inside LI (should be in LBody within LI)
        for child in children:
            if child.resolved_tag != "LI":
                continue
            for li_child in _children(child, elements):
                if li_child.resolved_tag == "L":
                    nesting_issues.append(
                        f"[{li_child.index + 1}] Nested L is direct child of LI"
                        + " (should be inside LBody within LI)"
                    )

    if not nesting_issues:
        return [
            CheckResult(
                name="List nesting valid",
                standard="PDF/UA, WCAG 1.3.1",
                result="PASS",
                details=(
                    f"All {len(list_elements)} list(s) have valid nesting"
                ),
            )
        ]
    return [
        CheckResult(
            name="List nesting valid",
            standard="PDF/UA, WCAG 1.3.1",
            result="WARN",
            details=(
                f"{len(nesting_issues)} list nesting issue(s):"
                f" {'; '.join(nesting_issues[:5])}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_list_item_labels
# ---------------------------------------------------------------------------


def check_list_item_labels(ctx: AuditContext) -> list[CheckResult]:
    """PDF/UA (best practice): every non-empty ``LI`` carries an ``Lbl``.

    Mirrors pdfMax line ~6707. WARN when one or more ``LI`` elements
    have children but no ``Lbl`` element among them. Truly empty
    ``LI``s are excluded (they're caught upstream by
    :func:`check_list_structure_valid` via the empty-list path).
    """
    elements = ctx.elements
    list_elements = [e for e in elements if e.resolved_tag == "L"]
    if not list_elements:
        return [
            CheckResult(
                name="List item labels",
                standard="PDF/UA (best practice)",
                result="NA",
                details="No lists found in document",
            )
        ]

    li_elements = [e for e in elements if e.resolved_tag == "LI"]
    missing_lbl: list[str] = []
    for li_elem in li_elements:
        li_children = _children(li_elem, elements)
        if not li_children:
            continue  # empty LI — not this check's concern
        has_lbl = any(c.resolved_tag == "Lbl" for c in li_children)
        if not has_lbl:
            child_tags = ", ".join(c.resolved_tag for c in li_children[:3])
            missing_lbl.append(
                f"[{li_elem.index + 1}] LI has no Lbl (children: {child_tags})"
            )

    if not missing_lbl:
        return [
            CheckResult(
                name="List item labels",
                standard="PDF/UA (best practice)",
                result="PASS",
                details=(
                    f"All {len(li_elements)} list item(s) have Lbl elements"
                ),
            )
        ]
    return [
        CheckResult(
            name="List item labels",
            standard="PDF/UA (best practice)",
            result="WARN",
            details=(
                f"{len(missing_lbl)} of {len(li_elements)} list item(s)"
                f" missing Lbl element: {'; '.join(missing_lbl[:5])}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_untagged_lists_detected
# ---------------------------------------------------------------------------


def check_untagged_lists_detected(ctx: AuditContext) -> list[CheckResult]:
    """WCAG 1.3.1: paragraph runs that read as lists but are not tagged as one.

    Mirrors pdfMax line ~6733. A screen reader announces a real list with
    its item count and lets the user jump between items; the same content
    as consecutive ``P`` elements offers none of that, which is why this
    is a finding even though the text is identical on the page.

    ``WARN`` rather than ``FAIL``: the detection is a marker-pattern
    heuristic, and prose legitimately begins with "1." now and then.
    """
    candidates = find_list_candidates(ctx.elements)
    if not candidates:
        return [
            CheckResult(
                name="Untagged lists detected",
                standard="WCAG 1.3.1",
                result="PASS",
                details=(
                    "No paragraph sequences detected that appear to be "
                    "untagged lists"
                ),
            )
        ]

    total_items = sum(len(group.elements) for group in candidates)
    patterns = sorted({group.pattern for group in candidates})

    # First few affected elements, 1-based to match every other reference
    # in this report. (pdfMax printed these 0-based, which did not agree
    # with its own element numbering elsewhere.)
    refs: list[str] = []
    affected: list[int] = []
    for group in candidates:
        for item in group.elements:
            affected.append(item.index)
            if len(refs) < 5:
                refs.append(f"[{item.index + 1}]")

    return [
        CheckResult(
            name="Untagged lists detected",
            standard="WCAG 1.3.1",
            result="WARN",
            details=(
                f"{len(candidates)} sequence(s) of paragraph tags "
                f"({total_items} items total) appear to be lists "
                f"({', '.join(patterns)}) but are tagged as P, not L > LI: "
                f"{' '.join(refs)}"
            ),
            elements=tuple(affected),
        )
    ]


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------


#: Phase 5.3's pipeline iterates this list in order. Phase 6's check
#: catalogue iterates the same list to enumerate every check name.
LIST_CHECKS: list[Callable[[AuditContext], list[CheckResult]]] = [
    check_list_structure_valid,
    check_no_empty_lists,
    check_list_nesting_valid,
    check_list_item_labels,
    check_untagged_lists_detected,
]


__all__ = [
    "LIST_CHECKS",
    "check_list_item_labels",
    "check_list_nesting_valid",
    "check_list_structure_valid",
    "check_no_empty_lists",
    "check_untagged_lists_detected",
]
