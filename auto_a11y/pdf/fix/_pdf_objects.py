"""Small pikepdf helpers shared by the fix modules.

The audit engine has its own read-oriented helpers in
:mod:`auto_a11y.pdf.audit.pikepdf_helpers`. These are the write-side
equivalents plus the array iteration the fixes need — pikepdf types
``Array`` iteration as ``Iterable`` rather than ``Iterator``, so the
whole codebase indexes instead, and doing that inline in fifty-odd fixes
would bury the logic.
"""
from __future__ import annotations

import pikepdf
from pikepdf import Array, Dictionary, Name


def items(array: pikepdf.Object | None) -> list[pikepdf.Object]:
    """Return an Array's elements as a list, or ``[]`` for anything else."""
    if not isinstance(array, pikepdf.Array):
        return []
    return [array[i] for i in range(len(array))]


def ensure_dict(
    parent: pikepdf.Object, key: str, pdf: pikepdf.Pdf
) -> pikepdf.Object:
    """Return ``parent[key]``, creating it as an indirect Dictionary if absent."""
    existing = parent.get(Name(key))
    if existing is not None:
        return existing
    created = pdf.make_indirect(Dictionary())
    parent[Name(key)] = created
    return created


def entry_text(obj: pikepdf.Object, key: str) -> str:
    """Return ``obj[key]`` as stripped text, or ``""`` when absent."""
    value = obj.get(Name(key))
    return str(value).strip() if value is not None else ""


def entry_int(obj: pikepdf.Object, key: str) -> int:
    """Return ``obj[key]`` as an int, or ``0`` when absent or non-numeric."""
    value = obj.get(Name(key))
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def flatten_form_fields(pdf: pikepdf.Pdf) -> list[pikepdf.Object]:
    """Return every terminal form field in ``/AcroForm /Fields``.

    An AcroForm field tree mixes two kinds of node: intermediate nodes
    that group children under a shared name, and terminal fields that
    carry a field type (``/FT``) and are what the user actually fills in.
    Only terminal fields matter to the fixes, so nodes with ``/Kids`` and
    no ``/FT`` are walked through rather than returned.

    Returns an empty list when the document has no form at all.
    """
    acroform = pdf.Root.get(Name("/AcroForm"))
    if acroform is None:
        return []

    stack = items(acroform.get(Name("/Fields")))
    terminal: list[pikepdf.Object] = []
    # Guard against a field tree that loops back on itself; malformed
    # documents do exist and an infinite walk would hang the fixer.
    seen: set[tuple[int, int]] = set()
    while stack:
        field = stack.pop(0)
        key = (field.objgen if field.is_indirect else (id(field), 0))
        if key in seen:
            continue
        seen.add(key)

        kids = field.get(Name("/Kids"))
        if kids is not None and field.get(Name("/FT")) is None:
            stack.extend(items(kids))
        else:
            terminal.append(field)
    return terminal


# Structure-tree node types that reference content rather than being
# elements in their own right.
_CONTENT_REFS = ("/MCR", "/OBJR")


def read_role_map(struct_root: pikepdf.Object) -> dict[str, str]:
    """Custom tag → standard tag, both without leading slashes."""
    raw = struct_root.get(Name("/RoleMap"))
    if raw is None:
        return {}
    return {
        str(key).lstrip("/"): str(raw[key]).lstrip("/")
        for key in raw.keys()
    }


def resolved_tag(node: pikepdf.Object, role_map: dict[str, str]) -> str:
    """The node's structure tag after role-map resolution, no slash."""
    tag = node.get(Name("/S"))
    if tag is None:
        return ""
    bare = str(tag).lstrip("/")
    return role_map.get(bare, bare)


def kids(node: pikepdf.Object) -> list[pikepdf.Object] | None:
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


def write_kids(node: pikepdf.Object, kids: list[pikepdf.Object]) -> None:
    """Store ``kids`` back onto ``/K``, dropping the key when empty."""
    if not kids:
        if Name("/K") in node:
            del node[Name("/K")]
    elif len(kids) == 1:
        node[Name("/K")] = kids[0]
    else:
        node[Name("/K")] = Array(kids)


def is_content_ref(node: pikepdf.Object) -> bool:
    node_type = node.get(Name("/Type"))
    return node_type is not None and str(node_type) in _CONTENT_REFS


