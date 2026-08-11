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
from pikepdf import Dictionary, Name


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
