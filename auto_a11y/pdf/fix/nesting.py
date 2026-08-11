"""Fixes for structure elements nested inside the wrong parent.

Some tags describe a run of content and cannot meaningfully contain
another of their own kind: a paragraph inside a paragraph, a heading
inside a heading. Authoring tools produce them when a style is applied
over a range that already had one, and the result confuses a screen
reader's sense of where one block ends and the next begins.

The repairs here move content up rather than deleting it: whatever the
inner element held becomes a child of the outer one, in the same place.
"""
from __future__ import annotations

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

_HEADINGS = frozenset({"H", "H1", "H2", "H3", "H4", "H5", "H6"})
_PARAGRAPH = "P"


def _walk_tree(
    pdf: pikepdf.Pdf,
) -> tuple[pikepdf.Object, dict[str, str]] | None:
    """The structure root and its role map, or ``None`` when there is none."""
    struct_root = pdf.Root.get(Name("/StructTreeRoot"))
    if struct_root is None:
        return None
    return struct_root, read_role_map(struct_root)


def fix_correct_nesting(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Unwrap a paragraph inside a paragraph, or a heading inside a heading.

    The inner element's children are promoted into the outer element at
    the position the inner one occupied, so reading order is unchanged and
    no content is lost. Only the redundant wrapper goes.

    An inner element holding nothing is simply removed — there was no
    content to promote, and an empty paragraph inside a paragraph is
    exactly the artefact this is for.
    """
    found = _walk_tree(pdf)
    if found is None:
        return FixResult("fix_correct_nesting", False, "No structure tree found")
    struct_root, role_map = found

    unwrapped = 0

    def repair(node: pikepdf.Object) -> None:
        nonlocal unwrapped
        children = kids(node)
        if not children:
            return

        tag = resolved_tag(node, role_map)
        wants_unwrap = tag == _PARAGRAPH or tag in _HEADINGS

        if wants_unwrap:
            rebuilt: list[pikepdf.Object] = []
            changed = False
            for child in children:
                if not isinstance(child, pikepdf.Dictionary) or is_content_ref(child):
                    rebuilt.append(child)
                    continue

                child_tag = resolved_tag(child, role_map)
                same_kind = (
                    (tag == _PARAGRAPH and child_tag == _PARAGRAPH)
                    or (tag in _HEADINGS and child_tag in _HEADINGS)
                )
                if not same_kind:
                    rebuilt.append(child)
                    continue

                for promoted in kids(child) or []:
                    if (
                        isinstance(promoted, pikepdf.Dictionary)
                        and not is_content_ref(promoted)
                    ):
                        promoted[Name("/P")] = node
                    rebuilt.append(promoted)
                unwrapped += 1
                changed = True

            if changed:
                write_kids(node, rebuilt)

        for child in kids(node) or []:
            if isinstance(child, pikepdf.Dictionary) and not is_content_ref(child):
                repair(child)

    for root in kids(struct_root) or []:
        if isinstance(root, pikepdf.Dictionary):
            repair(root)

    if unwrapped:
        return FixResult(
            "fix_correct_nesting", True,
            f"Unwrapped {unwrapped} element(s) nested inside their own kind",
        )
    return FixResult(
        "fix_correct_nesting", True, "No element is nested inside its own kind",
    )


def fix_toc_structure(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Give every entry in a table of contents its own ``<TOCI>``.

    A ``<TOC>`` is a list of entries, and each entry is a ``<TOCI>``
    holding its label, its text and its reference. Content sitting
    directly under the ``<TOC>`` belongs to an entry that was never
    created, so a reader navigating the contents finds one undivided
    block instead of a list they can move through.

    A nested ``<TOC>`` is left where it is: a sub-table of contents is a
    legitimate direct child, not an entry.
    """
    found = _walk_tree(pdf)
    if found is None:
        return FixResult("fix_toc_structure", False, "No structure tree found")
    struct_root, role_map = found

    wrapped = 0

    def repair(node: pikepdf.Object) -> None:
        nonlocal wrapped
        if resolved_tag(node, role_map) == "TOC":
            children = kids(node) or []
            rebuilt: list[pikepdf.Object] = []
            changed = False
            for child in children:
                is_element = (
                    isinstance(child, pikepdf.Dictionary)
                    and not is_content_ref(child)
                )
                child_tag = (
                    resolved_tag(child, role_map) if is_element else ""
                )
                if not is_element or child_tag in ("TOCI", "TOC"):
                    rebuilt.append(child)
                    continue

                entry = pdf.make_indirect(
                    Dictionary(Type=Name.StructElem, S=Name("/TOCI"), P=node)
                )
                write_kids(entry, [child])
                child[Name("/P")] = entry
                rebuilt.append(entry)
                wrapped += 1
                changed = True
            if changed:
                write_kids(node, rebuilt)

        for child in kids(node) or []:
            if isinstance(child, pikepdf.Dictionary) and not is_content_ref(child):
                repair(child)

    for root in kids(struct_root) or []:
        if isinstance(root, pikepdf.Dictionary):
            repair(root)

    if wrapped:
        return FixResult(
            "fix_toc_structure", True,
            f"Gave {wrapped} contents entry/entries their own <TOCI>",
        )
    return FixResult(
        "fix_toc_structure", True,
        "Every table of contents is already made of entries",
    )
