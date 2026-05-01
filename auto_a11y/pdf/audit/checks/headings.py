"""Heading-related accessibility checks.

Three checks ported verbatim from pdfMax's
``python/checker/pdf_accessibility_audit.py`` (lines ~4822-4904):

* :func:`check_heading_hierarchy` — first heading must be H1, only one
  H1 in the document, no skipped levels (WCAG 1.3.1, 2.4.6).
* :func:`check_no_multiple_headings_per_node` — each structure node
  has at most one heading child (Matterhorn 14-006).
* :func:`check_no_mixed_heading_tag_types` — the document uses either
  generic ``H`` or numbered ``H1``–``H6``, never both (Matterhorn
  14-007).

This module establishes the convention every Phase 4 check module
follows: each check is a plain function ``(ctx) -> list[CheckResult]``,
and the module exposes a :data:`HEADING_CHECKS` list that the Phase 5.3
orchestrator iterates. All :class:`CheckResult` ``name`` and
``standard`` strings match pdfMax's exactly so Phase 6's check
catalogue can map them.
"""
from __future__ import annotations

from collections.abc import Callable

from auto_a11y.pdf.models import AuditContext, CheckResult


# ---------------------------------------------------------------------------
# Module-private constants
# ---------------------------------------------------------------------------


#: Numbered heading tags as they appear after RoleMap resolution
#: (no leading ``/``, matching :attr:`StructElement.resolved_tag`).
_NUMBERED_HEADING_TAGS: frozenset[str] = frozenset(
    {"H1", "H2", "H3", "H4", "H5", "H6"}
)

#: Every heading tag — numbered plus the generic ``H`` (PDF 1.7+).
_ALL_HEADING_TAGS: frozenset[str] = _NUMBERED_HEADING_TAGS | {"H"}


# ---------------------------------------------------------------------------
# check_heading_hierarchy
# ---------------------------------------------------------------------------


def check_heading_hierarchy(ctx: AuditContext) -> list[CheckResult]:
    """Validate heading hierarchy: first H1, single H1, no skipped levels.

    WCAG 1.3.1, 2.4.6. Reads :attr:`AuditContext.elements`. Returns a
    one-element list with PASS if every rule holds (or no headings are
    present), FAIL otherwise. Mirrors pdfMax's ``CheckResult`` exactly.
    """
    heading_levels: list[tuple[int, int, str]] = []
    for elem in ctx.elements:
        tag = elem.resolved_tag
        if tag in _NUMBERED_HEADING_TAGS:
            level = int(tag[1])
            heading_levels.append((elem.index, level, elem.custom_tag))

    issues: list[str] = []
    if heading_levels:
        # First heading must be H1.
        first_idx, first_level, first_tag = heading_levels[0]
        if first_level != 1:
            issues.append(
                f"[{first_idx}] {first_tag}: first heading is"
                + f" H{first_level}, should be H1"
            )

        # At most one H1 (the document's root heading).
        h1_count = sum(1 for _, lvl, _ in heading_levels if lvl == 1)
        if h1_count > 1:
            h1_indices = [
                f"[{idx}]" for idx, lvl, _ in heading_levels if lvl == 1
            ]
            issues.append(
                f"Multiple H1 headings ({h1_count}): "
                + f"{', '.join(h1_indices)} — document should have a"
                + " single H1 as the root heading"
            )

        # No skipped levels.
        prev_level = 0
        for idx, level, tag in heading_levels:
            if level > prev_level + 1 and prev_level > 0:
                issues.append(
                    f"[{idx}] {tag}: H{level} follows H{prev_level}"
                    + " (skipped level)"
                )
            prev_level = level

    if not issues:
        levels_str = (
            ", ".join(f"H{lvl}" for _, lvl, _ in heading_levels)
            if heading_levels
            else "none found"
        )
        return [
            CheckResult(
                name="Heading hierarchy valid",
                standard="WCAG 1.3.1, 2.4.6",
                result="PASS",
                details=f"Heading sequence: {levels_str}",
            )
        ]

    issues_block = "\n".join(f"- {issue}" for issue in issues)
    return [
        CheckResult(
            name="Heading hierarchy valid",
            standard="WCAG 1.3.1, 2.4.6",
            result="FAIL",
            details=f"Heading hierarchy issues:\n\n{issues_block}",
        )
    ]


# ---------------------------------------------------------------------------
# check_no_multiple_headings_per_node
# ---------------------------------------------------------------------------


def check_no_multiple_headings_per_node(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 14-006: each parent node has at most one heading child.

    Reads :attr:`AuditContext.elements`. Walks every element with
    children and counts how many of those direct children are heading
    tags (``H``, ``H1``–``H6``). Reports any parent with more than one.
    """
    elements = ctx.elements
    multi_heading_nodes: list[str] = []

    for elem in elements:
        if not elem.children_indices:
            continue
        heading_children = [
            elements[ci]
            for ci in elem.children_indices
            if 0 <= ci < len(elements)
            and elements[ci].resolved_tag in _ALL_HEADING_TAGS
        ]
        if len(heading_children) > 1:
            tags = ", ".join(
                f"{h.resolved_tag}[{h.index}]" for h in heading_children
            )
            multi_heading_nodes.append(
                f"[{elem.index}] {elem.resolved_tag} has"
                + f" {len(heading_children)} headings: {tags}"
            )

    if not multi_heading_nodes:
        return [
            CheckResult(
                name="No multiple headings per node",
                standard="Matterhorn 14-006",
                result="PASS",
                details="No structure nodes have multiple heading children",
            )
        ]

    return [
        CheckResult(
            name="No multiple headings per node",
            standard="Matterhorn 14-006",
            result="FAIL",
            details=(
                f"{len(multi_heading_nodes)} node(s) with multiple "
                f"heading children: "
                f"{'; '.join(multi_heading_nodes[:5])}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_no_mixed_heading_tag_types
# ---------------------------------------------------------------------------


def check_no_mixed_heading_tag_types(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 14-007: a document uses either ``H`` or ``H1``–``H6``.

    Reads :attr:`AuditContext.elements`. PASSes when only one of the
    two heading systems is present (or neither). FAILs when both are
    used; details quote the first three example indices of each.
    """
    elements = ctx.elements
    has_generic_h = any(e.resolved_tag == "H" for e in elements)
    has_numbered_h = any(
        e.resolved_tag in _NUMBERED_HEADING_TAGS for e in elements
    )

    if not (has_generic_h and has_numbered_h):
        return [
            CheckResult(
                name="No mixed heading tag types",
                standard="Matterhorn 14-007",
                result="PASS",
                details="Document uses a single heading tag system",
            )
        ]

    generic_examples = [e for e in elements if e.resolved_tag == "H"]
    numbered_examples = [
        e for e in elements if e.resolved_tag in _NUMBERED_HEADING_TAGS
    ]
    generic_refs = " ".join(f"[{e.index}]" for e in generic_examples[:3])
    numbered_refs = " ".join(f"[{e.index}]" for e in numbered_examples[:3])
    return [
        CheckResult(
            name="No mixed heading tag types",
            standard="Matterhorn 14-007",
            result="FAIL",
            details=(
                f"Document uses both generic H "
                f"({len(generic_examples)}, e.g. {generic_refs}) and "
                f"numbered H1-H6 ({len(numbered_examples)}, e.g. "
                f"{numbered_refs}) tags — must use one system "
                f"consistently"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------


#: Phase 5.3's pipeline iterates this list in order. Phase 6's check
#: catalogue iterates the same list to enumerate every check name.
HEADING_CHECKS: list[Callable[[AuditContext], list[CheckResult]]] = [
    check_heading_hierarchy,
    check_no_multiple_headings_per_node,
    check_no_mixed_heading_tag_types,
]


__all__ = [
    "HEADING_CHECKS",
    "check_heading_hierarchy",
    "check_no_mixed_heading_tag_types",
    "check_no_multiple_headings_per_node",
]
