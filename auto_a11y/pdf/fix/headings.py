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
from pikepdf import Name

from auto_a11y.pdf.audit.structure import walk_structure_tree
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
