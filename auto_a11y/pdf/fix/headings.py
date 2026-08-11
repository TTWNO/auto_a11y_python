"""Fixes for heading structure.

Headings are how a screen-reader user navigates a long document: jump to
the next heading, or pull up a list of them and skim. That only works if
the levels describe the document's actual hierarchy — a run of H1s gives
no shape, and a jump from H1 to H4 implies two missing sections.

Which level a heading should be is a judgement about meaning, so these
fixes apply a person's decision rather than inferring one.
"""
from __future__ import annotations

import pikepdf
from pikepdf import Dictionary, Name

from auto_a11y.pdf.audit.structure import StructElement, walk_structure_tree
from auto_a11y.pdf.fix._pdf_objects import is_content_ref, kids, write_kids
from auto_a11y.pdf.fix.models import FixOptions, FixResult

_HEADING_TAGS = frozenset({"H1", "H2", "H3", "H4", "H5", "H6"})

# Tags a heading may be made from. Everything here is a run of prose that
# could reasonably have been marked as a heading in the first place.
#
# The exclusions matter more than the inclusions: promoting a <TD> to a
# heading takes a cell out of its table's grid, and promoting an <LI>
# breaks the list's item count. Those are positional elements whose
# meaning comes from where they sit, and retagging one damages the
# structure around it rather than just relabelling it.
_PROMOTABLE = frozenset({"P", "Span", "Div", "Quote", "Caption", "NonStruct", "H"})

# Level 0 means "this is not a heading" — demote it to a paragraph.
_DEMOTE_TO_PARAGRAPH = 0
_MAX_LEVEL = 6


def fix_heading_levels(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Set chosen elements to a heading level, or demote them to paragraphs.

    ``opts.heading_levels_map`` maps a 1-based element reference to a
    level: ``"1"`` through ``"6"`` for ``<H1>``–``<H6>``, or ``"0"`` to
    turn a heading back into a ``<P>`` — which is what you want for text
    that was styled large but is not structurally a heading.

    Two corrections to the original:

    References are 1-based, matching what the report prints and what the
    alt-text fixes already used. The original read them 0-based, so every
    level change landed on the element before the one selected — and
    since neighbouring elements are often both headings, the result
    looked plausible while being wrong.

    Promotion is restricted to prose elements. The original promoted
    whatever it was pointed at, so a stale reference could retag a table
    cell or a list item as a heading, taking it out of the grid or list
    it belongs to. Demotion is unrestricted, since only headings can be
    demoted.
    """
    if not opts.heading_levels_map:
        return FixResult(
            "fix_heading_levels", False, "No heading level changes provided",
        )

    targets: dict[int, int] = {}
    rejected: list[str] = []
    for key, raw_level in opts.heading_levels_map.items():
        try:
            position = int(key)
            level = int(raw_level)
        except (TypeError, ValueError):
            rejected.append(f"{key} -> {raw_level}")
            continue
        if position < 1 or not (_DEMOTE_TO_PARAGRAPH <= level <= _MAX_LEVEL):
            rejected.append(f"{key} -> {raw_level}")
            continue
        targets[position - 1] = level

    if rejected:
        return FixResult(
            "fix_heading_levels", False,
            "Not a 1-based element reference mapped to a level 0-6: "
            + ", ".join(sorted(rejected)),
        )
    if not targets:
        return FixResult(
            "fix_heading_levels", False, "No valid heading level changes provided",
        )

    elements, _role_map = walk_structure_tree(pdf)
    if not elements:
        return FixResult("fix_heading_levels", False, "No structure tree found")
    by_index = {e.index: e for e in elements}

    changed = 0
    problems: list[str] = []
    for index, level in sorted(targets.items()):
        element = by_index.get(index)
        if element is None:
            problems.append(f"{index + 1} not found")
            continue

        tag = element.resolved_tag
        is_heading = tag in _HEADING_TAGS

        if level == _DEMOTE_TO_PARAGRAPH:
            if not is_heading:
                problems.append(f"{index + 1} is <{tag or '?'}>, not a heading")
                continue
            element.obj[Name("/S")] = Name("/P")
            changed += 1
            continue

        if not is_heading and tag not in _PROMOTABLE:
            problems.append(
                f"{index + 1} is <{tag or '?'}>, which cannot become a heading"
            )
            continue

        element.obj[Name("/S")] = Name(f"/H{level}")
        changed += 1

    if changed and not problems:
        return FixResult(
            "fix_heading_levels", True,
            f"Set the heading level on {changed} element(s)",
        )
    if changed:
        return FixResult(
            "fix_heading_levels", True,
            f"Set the heading level on {changed} of {len(targets)} element(s)"
            + f" — {'; '.join(problems)}",
        )
    return FixResult(
        "fix_heading_levels", False,
        "Changed no heading levels — " + "; ".join(problems),
    )


# Elements that divide a document into sections. Heading depth is counted
# against these.
#
# <Div> is deliberately absent, though the original counted it. The
# specification calls Div "a generic block-level element or group of
# elements" — it carries no sectioning meaning, so treating it as a level
# invents hierarchy from layout. It also interacts badly with
# fix_heading_containers, which wraps headings in Div: running that first
# would demote every heading it touched.
_SECTIONING_TAGS = frozenset({"Document", "Part", "Sect", "Art"})

_GENERIC_HEADING = "H"

# Both heading conventions, for fixes that care that something *is* a
# heading rather than what level it claims.
_ANY_HEADING_TAG = _HEADING_TAGS | {_GENERIC_HEADING}


def fix_heading_containers(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Give each heading under a shared parent its own container.

    A node holding several headings side by side describes a structure
    with no content between them — the headings are siblings rather than
    each introducing something. Wrapping all but the first in a ``<Div>``
    separates them, so each heading owns a region of the document.

    The first heading keeps its place, so nothing moves in reading order.
    """
    elements, _role_map = walk_structure_tree(pdf)
    if not elements:
        return FixResult("fix_heading_containers", False, "No structure tree found")

    by_objgen = {
        e.obj.objgen: e for e in elements if e.obj.is_indirect
    }
    wrapped = 0

    for element in elements:
        # Rebuilt from the node's real /K, not from its child elements:
        # /K also carries marked-content ids and object references, and
        # writing back only the elements would delete the node's text.
        children = kids(element.obj) or []
        headings = [
            child for child in children
            if isinstance(child, pikepdf.Dictionary)
            and not is_content_ref(child)
            and child.is_indirect
            and (found := by_objgen.get(child.objgen)) is not None
            and found.resolved_tag in _ANY_HEADING_TAG
        ]
        if len(headings) < 2:
            continue

        extra = {h.objgen for h in headings[1:]}
        rebuilt: list[pikepdf.Object] = []
        for child in children:
            if not (
                isinstance(child, pikepdf.Dictionary)
                and child.is_indirect
                and child.objgen in extra
            ):
                rebuilt.append(child)
                continue
            container = pdf.make_indirect(
                Dictionary(
                    Type=Name.StructElem, S=Name("/Div"), P=element.obj,
                )
            )
            write_kids(container, [child])
            child[Name("/P")] = container
            rebuilt.append(container)
            wrapped += 1
        write_kids(element.obj, rebuilt)

    if wrapped:
        return FixResult(
            "fix_heading_containers", True,
            f"Gave {wrapped} heading(s) their own container",
        )
    return FixResult(
        "fix_heading_containers", True,
        "No node holds more than one heading",
    )


def fix_generic_headings(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Give generic ``<H>`` tags a level, where the document mixes both styles.

    PDF allows two heading conventions: numbered ``<H1>``–``<H6>``, or
    plain ``<H>`` whose level comes from how deeply it is nested. Either
    is valid on its own. A document using both is readable under neither,
    because a reader cannot tell whether an ``<H>`` beside an ``<H2>``
    outranks it.

    Only acts when both appear, and derives each level from the number of
    enclosing sectioning elements. Where a document uses ``<H>``
    throughout, it is left alone — that is the PDF 2.0 convention, not a
    fault.
    """
    elements, _role_map = walk_structure_tree(pdf)
    if not elements:
        return FixResult("fix_generic_headings", False, "No structure tree found")

    tags = {e.resolved_tag for e in elements}
    if _GENERIC_HEADING not in tags or not (tags & _HEADING_TAGS):
        return FixResult(
            "fix_generic_headings", True,
            "The document does not mix generic and numbered headings",
        )

    by_index = {e.index: e for e in elements}

    def sectioning_depth(element: StructElement) -> int:
        """How many sectioning elements enclose this one."""
        depth = 0
        parent = by_index.get(element.parent_index)
        while parent is not None:
            if parent.resolved_tag in _SECTIONING_TAGS:
                depth += 1
            parent = by_index.get(parent.parent_index)
        return depth

    converted = 0
    for element in elements:
        if element.resolved_tag != _GENERIC_HEADING:
            continue
        level = min(max(sectioning_depth(element), 1), _MAX_LEVEL)
        element.obj[Name("/S")] = Name(f"/H{level}")
        converted += 1

    return FixResult(
        "fix_generic_headings", True,
        f"Gave {converted} generic <H> tag(s) a numbered level",
    )
