"""Fixes that put annotations into the structure tree.

An annotation reaches the structure tree through an ``/OBJR`` — an object
reference sitting where the annotation belongs in reading order. Without
one, a screen reader meets the annotation only when it happens to land on
it, out of sequence with the text around it; and with one under the wrong
parent, the annotation is announced as the wrong kind of thing.

Two structures have to stay consistent while this is repaired: the tag
tree itself, and the ``/ParentTree`` number tree that maps an
annotation's ``/StructParent`` key back to its structure element. The
second is the one that is easy to damage — see
:func:`_append_parent_tree_entries`.
"""
from __future__ import annotations

from dataclasses import dataclass

import pikepdf
from pikepdf import Array, Dictionary, Name

from auto_a11y.pdf.fix._pdf_objects import (
    is_content_ref,
    kids,
    read_role_map,
    resolved_tag,
    write_kids,
)
from auto_a11y.pdf.fix.models import FixOptions, FixResult


@dataclass(frozen=True)
class _Annotation:
    """A page annotation and the page it belongs to."""

    page: pikepdf.Object
    obj: pikepdf.Object


def _annotations_of_subtype(
    pdf: pikepdf.Pdf, subtype: str
) -> dict[tuple[int, int], _Annotation]:
    """Every annotation of ``subtype``, keyed by object id.

    Keyed on ``objgen`` rather than ``id()``: pikepdf hands out fresh
    Python wrappers for the same PDF object, so identity comparison finds
    the same annotation unequal to itself depending on how it was reached.
    """
    found: dict[tuple[int, int], _Annotation] = {}
    for page in pdf.pages:
        annots = page.obj.get(Name("/Annots"))
        if annots is None:
            continue
        for index in range(len(annots)):
            annot = annots[index]
            if str(annot.get(Name("/Subtype")) or "") != subtype:
                continue
            if annot.is_indirect:
                found[annot.objgen] = _Annotation(page=page.obj, obj=annot)
    return found


def _objr_target(node: pikepdf.Object) -> tuple[int, int] | None:
    """The object id an ``/OBJR`` points at, if it points anywhere."""
    if str(node.get(Name("/Type")) or "") != "/OBJR":
        return None
    target = node.get(Name("/Obj"))
    if target is None or not target.is_indirect:
        return None
    return target.objgen


def _append_parent_tree_entries(
    pdf: pikepdf.Pdf,
    struct_root: pikepdf.Object,
    entries: list[tuple[int, pikepdf.Object]],
) -> bool:
    """Add ``(key, element)`` pairs to ``/ParentTree``. False if unsafe.

    ``/ParentTree`` is a number tree, which may be a single node holding
    ``/Nums`` or a multi-level tree whose root holds ``/Kids``. Appending
    to the flat form is safe. Appending to the branching form is not, and
    the original did it anyway: it read ``/Nums`` from the root (absent,
    so empty), wrote back a ``/Nums`` containing only its new entries, and
    deleted ``/Kids`` — discarding every existing marked-content mapping
    in the document and untagging the whole file.

    Rather than risk that, this returns False for a branching tree and
    leaves it untouched. Large documents are exactly the ones with
    branching parent trees and the most to lose.
    """
    parent_tree = struct_root.get(Name("/ParentTree"))
    if parent_tree is None:
        parent_tree = pdf.make_indirect(Dictionary(Nums=Array([])))
        struct_root[Name("/ParentTree")] = parent_tree

    if Name("/Kids") in parent_tree and Name("/Nums") not in parent_tree:
        return False

    nums = parent_tree.get(Name("/Nums"))
    # A number tree's /Nums alternates integer keys with their values, so
    # the list is deliberately mixed-typed.
    flat: list[object] = (
        [nums[i] for i in range(len(nums))] if nums is not None else []
    )
    for key, element in entries:
        flat.append(key)
        flat.append(element)
    parent_tree[Name("/Nums")] = Array(flat)
    return True


def _wrap_misparented(
    pdf: pikepdf.Pdf,
    node: pikepdf.Object,
    role_map: dict[str, str],
    *,
    targets: set[tuple[int, int]],
    wrapper_tag: str,
    linked: set[tuple[int, int]],
) -> int:
    """Wrap OBJRs that sit under the wrong parent. Returns the count."""
    children = kids(node)
    if not children:
        return 0

    parent_tag = resolved_tag(node, role_map)
    rebuilt: list[pikepdf.Object] = []
    wrapped = 0
    changed = False

    for child in children:
        if not isinstance(child, pikepdf.Dictionary):
            rebuilt.append(child)
            continue

        target = _objr_target(child)
        if target is not None and target in targets:
            if parent_tag == wrapper_tag:
                linked.add(target)
                rebuilt.append(child)
                continue
            wrapper = pdf.make_indirect(
                Dictionary(
                    Type=Name.StructElem, S=Name(f"/{wrapper_tag}"), P=node,
                )
            )
            write_kids(wrapper, [child])
            page = child.get(Name("/Pg")) or node.get(Name("/Pg"))
            if page is not None:
                wrapper[Name("/Pg")] = page
            rebuilt.append(wrapper)
            linked.add(target)
            wrapped += 1
            changed = True
            continue

        if not is_content_ref(child):
            wrapped += _wrap_misparented(
                pdf, child, role_map,
                targets=targets, wrapper_tag=wrapper_tag, linked=linked,
            )
        rebuilt.append(child)

    if changed:
        write_kids(node, rebuilt)
    return wrapped


def fix_widget_form_tags(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Put every form widget inside a ``<Form>`` structure element.

    A widget annotation is the on-page part of a form field. Reached
    through a ``<Form>`` element it is announced in reading order with its
    label; reached any other way it arrives detached from the text that
    explains it.

    Two repairs. A widget already referenced from the tree but sitting
    under some other parent gets a ``<Form>`` wrapper around its
    ``/OBJR``, in place, so reading order does not move. A widget absent
    from the tree entirely gets a new ``<Form>`` with an ``/OBJR``,
    appended to the document element, and a ``/StructParent`` key
    registered in ``/ParentTree``.

    The second repair is skipped — with the reason reported — when the
    document's ``/ParentTree`` is a branching number tree, because
    appending to one safely needs number-tree surgery this does not do.
    The alternative, which the original took, flattens the tree and
    discards every existing mapping in the document.
    """
    struct_root = pdf.Root.get(Name("/StructTreeRoot"))
    if struct_root is None:
        return FixResult("fix_widget_form_tags", False, "No structure tree found")

    widgets = _annotations_of_subtype(pdf, "/Widget")
    if not widgets:
        return FixResult(
            "fix_widget_form_tags", True, "No widget annotations in document",
        )

    roots = [
        root for root in (kids(struct_root) or [])
        if isinstance(root, pikepdf.Dictionary)
    ]
    if not roots:
        return FixResult("fix_widget_form_tags", False, "Empty structure tree")

    role_map = read_role_map(struct_root)
    linked: set[tuple[int, int]] = set()
    wrapped = 0
    for root in roots:
        wrapped += _wrap_misparented(
            pdf, root, role_map,
            targets=set(widgets), wrapper_tag="Form", linked=linked,
        )

    unlinked = sorted(set(widgets) - linked)
    created = 0
    skipped_reason = ""

    if unlinked:
        document = roots[0]
        next_key = int(struct_root.get(Name("/ParentTreeNextKey")) or 0)
        new_elements: list[pikepdf.Object] = []
        entries: list[tuple[int, pikepdf.Object]] = []

        for objgen in unlinked:
            annotation = widgets[objgen]
            element = pdf.make_indirect(
                Dictionary(
                    Type=Name.StructElem,
                    S=Name("/Form"),
                    P=document,
                    Pg=annotation.page,
                )
            )
            objr = pdf.make_indirect(
                Dictionary(
                    Type=Name("/OBJR"), Obj=annotation.obj, Pg=annotation.page,
                )
            )
            write_kids(element, [objr])
            entries.append((next_key + created, element))
            new_elements.append(element)
            created += 1

        if _append_parent_tree_entries(pdf, struct_root, entries):
            for offset, objgen in enumerate(unlinked):
                widgets[objgen].obj[Name("/StructParent")] = next_key + offset
            write_kids(document, [*(kids(document) or []), *new_elements])
            struct_root[Name("/ParentTreeNextKey")] = next_key + created
        else:
            created = 0
            skipped_reason = (
                f"; {len(unlinked)} widget(s) are absent from the structure"
                + " tree and were left alone, because this document's"
                + " /ParentTree is a branching number tree and appending to"
                + " it safely is not something this fix does"
            )

    if not wrapped and not created:
        return FixResult(
            "fix_widget_form_tags", bool(not skipped_reason),
            (
                "All widget annotations are already inside Form elements"
                if not skipped_reason
                else "No widgets were re-tagged" + skipped_reason
            ),
        )

    parts: list[str] = []
    if wrapped:
        parts.append(f"wrapped {wrapped} misparented")
    if created:
        parts.append(f"created {created} new Form tag(s)")
    return FixResult(
        "fix_widget_form_tags", True,
        f"Fixed {wrapped + created} widget annotation(s): "
        + ", ".join(parts) + skipped_reason,
    )
