"""Fixes that repair list structure.

A tagged list is a contract: ``<L>`` contains ``<LI>``, and each ``<LI>``
holds an optional ``<Lbl>`` (the bullet or number) and an ``<LBody>`` (the
item's content). Screen readers rely on it to announce "list of 5 items,
item 2 of 5" and to let a user jump between items.

When the shape is wrong the announcement goes with it: a document whose
paragraphs sit directly inside ``<L>`` is read as an undifferentiated run
of text that happens to be called a list. These fixes restore the shape
without touching the content — every existing node keeps its text and its
children; it only gains the wrappers the specification requires.
"""
from __future__ import annotations

from collections.abc import Sequence

import pikepdf
from pikepdf import Dictionary, Name

from auto_a11y.pdf.fix._pdf_objects import (
    is_content_ref,
    kids,
    read_role_map,
    resolved_tag,
    write_kids,
)
from auto_a11y.pdf.fix.models import FixOptions, FixResult

# What an <LI> is allowed to contain directly.
_LIST_ITEM_PARTS = ("Lbl", "LBody")


def _new_element(
    pdf: pikepdf.Pdf,
    tag: str,
    *,
    parent: pikepdf.Object,
    children: Sequence[pikepdf.Object],
) -> pikepdf.Object:
    """Create an indirect structure element wrapping ``children``.

    Each child's ``/P`` is repointed at the new element. Parent pointers
    are not decoration: consumers walk them to work out where a node sits,
    and a tree whose ``/P`` entries disagree with its ``/K`` entries is
    harder to reason about than one that was simply malformed.
    """
    element = pdf.make_indirect(
        Dictionary(Type=Name.StructElem, S=Name(f"/{tag}"), P=parent)
    )
    write_kids(element, list(children))
    for child in children:
        if isinstance(child, pikepdf.Dictionary) and not is_content_ref(child):
            child[Name("/P")] = element
    return element


def _wrap_as_item(
    pdf: pikepdf.Pdf, child: pikepdf.Object, *, parent_list: pikepdf.Object
) -> pikepdf.Object:
    """Wrap a stray child of ``<L>`` into ``<LI><LBody>…</LBody></LI>``."""
    item = pdf.make_indirect(
        Dictionary(Type=Name.StructElem, S=Name("/LI"), P=parent_list)
    )
    body = _new_element(pdf, "LBody", parent=item, children=[child])
    write_kids(item, [body])
    return item


def _repair_list(pdf: pikepdf.Pdf, node: pikepdf.Object, role_map: dict[str, str]) -> int:
    """Give every child of an ``<L>`` a proper ``<LI>``. Returns changes made."""
    children = kids(node)
    if not children:
        return 0

    repaired: list[pikepdf.Object] = []
    changed = False
    for child in children:
        is_element = isinstance(child, pikepdf.Dictionary) and not is_content_ref(child)
        if is_element and resolved_tag(child, role_map) == "LI":
            repaired.append(child)
            continue
        # Anything else — a paragraph, a nested list, a bare marked-content
        # reference — is content that belongs to an item that was never
        # created. Wrapping preserves it; leaving it makes the list a lie.
        repaired.append(_wrap_as_item(pdf, child, parent_list=node))
        changed = True

    if changed:
        write_kids(node, repaired)
    return 1 if changed else 0


def _repair_item(pdf: pikepdf.Pdf, item: pikepdf.Object, role_map: dict[str, str]) -> int:
    """Ensure an ``<LI>``'s content sits inside an ``<LBody>``."""
    children = kids(item)
    if not children:
        return 0

    parts: list[pikepdf.Object] = []
    loose: list[pikepdf.Object] = []
    for child in children:
        is_element = isinstance(child, pikepdf.Dictionary) and not is_content_ref(child)
        if is_element and resolved_tag(child, role_map) in _LIST_ITEM_PARTS:
            parts.append(child)
        else:
            loose.append(child)

    if not loose:
        return 0
    # An item that already has a body knows where its content goes; moving
    # the strays into a second LBody would split one item in two.
    if any(
        isinstance(p, pikepdf.Dictionary) and resolved_tag(p, role_map) == "LBody"
        for p in parts
    ):
        return 0

    body = _new_element(pdf, "LBody", parent=item, children=loose)
    write_kids(item, [*parts, body])
    return 1


def _walk(
    pdf: pikepdf.Pdf,
    node: pikepdf.Object,
    role_map: dict[str, str],
    *,
    repair_items: bool,
) -> int:
    """Repair every list at or below ``node``. Returns the number changed."""
    changed = 0
    tag = resolved_tag(node, role_map)
    if tag == "L":
        changed += _repair_list(pdf, node, role_map)
        if repair_items:
            for child in kids(node) or []:
                if (
                    isinstance(child, pikepdf.Dictionary)
                    and not is_content_ref(child)
                    and resolved_tag(child, role_map) == "LI"
                ):
                    changed += _repair_item(pdf, child, role_map)

    for child in kids(node) or []:
        if isinstance(child, pikepdf.Dictionary) and not is_content_ref(child):
            changed += _walk(pdf, child, role_map, repair_items=repair_items)
    return changed


def _walk_document(
    pdf: pikepdf.Pdf, *, repair_items: bool
) -> tuple[int, bool]:
    """Run the repair over every root. Returns ``(changed, had_tree)``."""
    struct_root = pdf.Root.get(Name("/StructTreeRoot"))
    if struct_root is None:
        return 0, False
    role_map = read_role_map(struct_root)
    changed = 0
    for root in kids(struct_root) or []:
        if isinstance(root, pikepdf.Dictionary):
            changed += _walk(pdf, root, role_map, repair_items=repair_items)
    return changed, True


def fix_list_structure(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Restore ``<L> → <LI> → <LBody>`` where the wrappers are missing.

    Two repairs, applied together: a child of ``<L>`` that is not an
    ``<LI>`` gets wrapped in one, and an ``<LI>`` whose content sits loose
    gets an ``<LBody>`` around it.

    Nothing is deleted or moved between items — each stray becomes its own
    item in the position it already held, so the reading order is
    unchanged.
    """
    changed, had_tree = _walk_document(pdf, repair_items=True)
    if not had_tree:
        return FixResult("fix_list_structure", False, "No structure tree found")
    if changed:
        return FixResult(
            "fix_list_structure", True,
            f"Repaired structure in {changed} list element(s)",
        )
    return FixResult(
        "fix_list_structure", True, "All lists already have valid structure",
    )


def fix_list_nesting(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Give a nested list the ``<LI>``/``<LBody>`` wrappers it needs.

    A sublist written directly inside its parent ``<L>``, or directly
    inside an ``<LI>``, is the commonest list malformation: authoring
    tools produce it whenever someone indents a bullet. The sublist is
    not moved — it stays where it is in reading order and gains the
    wrappers around it.

    This overlaps :func:`fix_list_structure`, which repairs the same
    shapes as part of a broader sweep. It exists separately because the
    report offers it against the nesting check specifically, and someone
    who chose that one check should not have every other list in the
    document rewritten as a side effect.
    """
    struct_root = pdf.Root.get(Name("/StructTreeRoot"))
    if struct_root is None:
        return FixResult("fix_list_nesting", False, "No structure tree found")
    role_map = read_role_map(struct_root)

    def repair(node: pikepdf.Object) -> int:
        changed = 0
        tag = resolved_tag(node, role_map)
        children = kids(node) or []

        if tag == "L":
            rebuilt: list[pikepdf.Object] = []
            modified = False
            for child in children:
                is_element = (
                    isinstance(child, pikepdf.Dictionary)
                    and not is_content_ref(child)
                )
                if is_element and resolved_tag(child, role_map) == "L":
                    rebuilt.append(_wrap_as_item(pdf, child, parent_list=node))
                    modified = True
                else:
                    rebuilt.append(child)
            if modified:
                write_kids(node, rebuilt)
                changed += 1
        elif tag == "LI":
            loose_lists = [
                c for c in children
                if isinstance(c, pikepdf.Dictionary)
                and not is_content_ref(c)
                and resolved_tag(c, role_map) == "L"
            ]
            if loose_lists:
                rest = [c for c in children if c not in loose_lists]
                body = _new_element(pdf, "LBody", parent=node, children=loose_lists)
                write_kids(node, [*rest, body])
                changed += 1

        for child in kids(node) or []:
            if isinstance(child, pikepdf.Dictionary) and not is_content_ref(child):
                changed += repair(child)
        return changed

    changed = 0
    for root in kids(struct_root) or []:
        if isinstance(root, pikepdf.Dictionary):
            changed += repair(root)

    if changed:
        return FixResult(
            "fix_list_nesting", True,
            f"Corrected nesting in {changed} list element(s)",
        )
    return FixResult(
        "fix_list_nesting", True, "All nested lists are already correct",
    )
