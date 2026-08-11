"""Fixes that prune meaningless nodes from the structure tree.

Empty tags are noise a screen reader still has to walk through: a run of
``<P>`` elements with no content announces as nothing at all, repeatedly,
and a reader cannot tell an empty paragraph from a missed one.

Pruning is destructive, so the bar for removing a node is that it can
carry no meaning under any reading — no content, no children, no alt or
actual text — and that its absence cannot change how its siblings are
interpreted. The second half of that is why table cells are never
removed: they are positional.
"""
from __future__ import annotations

from collections.abc import Callable

import pikepdf
from pikepdf import Array, Name

from auto_a11y.pdf.fix.models import FixOptions, FixResult

# Containers whose job is to group rather than to carry content. An empty
# one is odd but harmless, and removing it can change how a document is
# sectioned, so they stay.
_CONTAINERS = frozenset({
    "Document", "Part", "Sect", "Div", "Art", "BlockQuote",
    "TOC", "TOCI", "Index", "NonStruct",
    "Table", "TR", "THead", "TBody", "TFoot",
    "L", "LI",
    "RB", "RT", "RP", "Warichu", "WP", "WT",
})

# Table cells are positional: a row's cells map to columns by their order,
# so dropping an empty one shifts every later cell in that row into the
# wrong column and breaks its header association. A blank cell in a data
# table is ordinary — it means "no value here" — and must survive.
_POSITIONAL = frozenset({"TD", "TH"})

_PROTECTED = _CONTAINERS | _POSITIONAL

# Structure-tree node types that reference content rather than being
# elements in their own right.
_CONTENT_REFS = ("/MCR", "/OBJR")


def _read_role_map(struct_root: pikepdf.Object) -> dict[str, str]:
    """Custom tag → standard tag, both without leading slashes."""
    raw = struct_root.get(Name("/RoleMap"))
    if raw is None:
        return {}
    return {
        str(key).lstrip("/"): str(raw[key]).lstrip("/")
        for key in raw.keys()
    }


def _resolved_tag(node: pikepdf.Object, role_map: dict[str, str]) -> str:
    """The node's structure tag after role-map resolution, no slash."""
    tag = node.get(Name("/S"))
    if tag is None:
        return ""
    bare = str(tag).lstrip("/")
    return role_map.get(bare, bare)


def _kids(node: pikepdf.Object) -> list[pikepdf.Object] | None:
    """``/K`` as a list, or ``None`` when the key is absent.

    ``/K`` may hold a single object rather than an array; both shapes are
    normalised here so callers do not each have to.
    """
    kids = node.get(Name("/K"))
    if kids is None:
        return None
    if isinstance(kids, Array):
        return [kids[i] for i in range(len(kids))]
    return [kids]


def _write_kids(node: pikepdf.Object, kids: list[pikepdf.Object]) -> None:
    """Store ``kids`` back onto ``/K``, dropping the key when empty."""
    if not kids:
        if Name("/K") in node:
            del node[Name("/K")]
    elif len(kids) == 1:
        node[Name("/K")] = kids[0]
    else:
        node[Name("/K")] = Array(kids)


def _is_content_ref(node: pikepdf.Object) -> bool:
    node_type = node.get(Name("/Type"))
    return node_type is not None and str(node_type) in _CONTENT_REFS


def _prune(
    node: pikepdf.Object,
    should_remove: Callable[[pikepdf.Object], bool],
) -> int:
    """Depth-first prune of ``node``'s descendants. Returns the count removed.

    Children are visited before the parent decides, so a node emptied by
    its own children being removed is itself considered on the way back
    up — which is what lets a chain of nested empty wrappers collapse in
    one pass.
    """
    kids = _kids(node)
    if kids is None:
        return 0

    removed = 0
    for child in kids:
        if isinstance(child, pikepdf.Dictionary) and not _is_content_ref(child):
            removed += _prune(child, should_remove)

    kept: list[pikepdf.Object] = []
    for child in _kids(node) or []:
        if (
            isinstance(child, pikepdf.Dictionary)
            and not _is_content_ref(child)
            and should_remove(child)
        ):
            removed += 1
            continue
        kept.append(child)

    if len(kept) != len(_kids(node) or []):
        _write_kids(node, kept)
    return removed


def _walk_roots(
    pdf: pikepdf.Pdf,
    should_remove: Callable[[pikepdf.Object], bool],
) -> tuple[int, pikepdf.Object] | None:
    """Prune from every root element. ``None`` when there is no tree."""
    struct_root = pdf.Root.get(Name("/StructTreeRoot"))
    if struct_root is None:
        return None
    removed = 0
    for root in _kids(struct_root) or []:
        if isinstance(root, pikepdf.Dictionary):
            removed += _prune(root, should_remove)
    return removed, struct_root


def fix_empty_tags(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Remove leaf tags that carry nothing.

    A tag qualifies only if it has no children, no marked content, and no
    alt or actual text — so a ``<Figure>`` described purely by ``/Alt``
    survives, since the description is the content.

    Divergence from pdfMax: table cells are protected. Its list of
    never-remove tags covers Table, TR and the row groups but not TD or
    TH, so an empty cell — an ordinary thing in a data table, meaning "no
    value" — was deleted, shifting every later cell in that row into the
    wrong column and breaking their header associations. Pruning noise is
    not worth corrupting a table's geometry.
    """
    struct_root = pdf.Root.get(Name("/StructTreeRoot"))
    if struct_root is None:
        return FixResult("fix_empty_tags", False, "No structure tree found")
    role_map = _read_role_map(struct_root)

    def is_empty_leaf(node: pikepdf.Object) -> bool:
        if _resolved_tag(node, role_map) in _PROTECTED:
            return False
        if node.get(Name("/Alt")) is not None:
            return False
        if node.get(Name("/ActualText")) is not None:
            return False
        kids = _kids(node)
        if kids is None:
            return True
        # Any surviving child at all — an element, a marked-content id, or
        # an object reference — means this is not an empty leaf.
        return not kids

    result = _walk_roots(pdf, is_empty_leaf)
    if result is None:
        return FixResult("fix_empty_tags", False, "No structure tree found")
    removed, _ = result

    if removed:
        return FixResult(
            "fix_empty_tags", True, f"Removed {removed} empty leaf tag(s)",
        )
    return FixResult("fix_empty_tags", True, "No empty leaf tags found")


def fix_empty_tables(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Remove ``<Table>`` elements that contain no cells.

    A table with no ``<TD>`` or ``<TH>`` anywhere beneath it describes a
    grid that does not exist. Assistive technology announces "table" and
    then finds nothing to navigate, which is worse than the content being
    untagged.
    """
    struct_root = pdf.Root.get(Name("/StructTreeRoot"))
    if struct_root is None:
        return FixResult("fix_empty_tables", False, "No structure tree found")
    role_map = _read_role_map(struct_root)

    def has_cell(node: pikepdf.Object) -> bool:
        stack = list(_kids(node) or [])
        while stack:
            child = stack.pop()
            if not isinstance(child, pikepdf.Dictionary):
                continue
            if _is_content_ref(child):
                continue
            if _resolved_tag(child, role_map) in _POSITIONAL:
                return True
            stack.extend(_kids(child) or [])
        return False

    def is_empty_table(node: pikepdf.Object) -> bool:
        return _resolved_tag(node, role_map) == "Table" and not has_cell(node)

    result = _walk_roots(pdf, is_empty_table)
    if result is None:
        return FixResult("fix_empty_tables", False, "No structure tree found")
    removed, _ = result

    if removed:
        return FixResult(
            "fix_empty_tables", True,
            f"Removed {removed} empty table(s) from the structure tree",
        )
    return FixResult("fix_empty_tables", True, "No empty tables found")


def fix_empty_lists(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Remove ``<L>`` elements with no children at all.

    Deliberately stricter than the table case: a list is only removed
    when it is completely empty, not when it merely lacks ``<LI>``
    children. A malformed list whose items were never wrapped still holds
    the author's content, and repairing that is fix_list_structure's job
    — deleting it here would throw the content away.
    """
    struct_root = pdf.Root.get(Name("/StructTreeRoot"))
    if struct_root is None:
        return FixResult("fix_empty_lists", False, "No structure tree found")
    role_map = _read_role_map(struct_root)

    def is_empty_list(node: pikepdf.Object) -> bool:
        return _resolved_tag(node, role_map) == "L" and not _kids(node)

    result = _walk_roots(pdf, is_empty_list)
    if result is None:
        return FixResult("fix_empty_lists", False, "No structure tree found")
    removed, _ = result

    if removed:
        return FixResult(
            "fix_empty_lists", True,
            f"Removed {removed} empty list(s) from the structure tree",
        )
    return FixResult("fix_empty_lists", True, "No empty lists found")
