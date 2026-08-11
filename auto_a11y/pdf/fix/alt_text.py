"""Fixes that write alternative text onto structure elements.

Alt text cannot be generated, only collected: the fix supplies the
plumbing and the person supplies the words. What the plumbing owes them
is that the words land on the element they were written for — an alt text
attached to the wrong figure is worse than none, because it describes
something confidently and wrongly, and nothing downstream reveals it.

Element indices therefore come from the same walk the audit uses
(:func:`~auto_a11y.pdf.audit.structure.walk_structure_tree`) rather than
a second implementation kept in step by hand, and each target's tag is
verified before anything is written.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

import pikepdf
from pikepdf import Name, String

from auto_a11y.pdf.audit.structure import StructElement, walk_structure_tree
from auto_a11y.pdf.fix.models import FixOptions, FixResult


def _parse_targets(
    mapping: Mapping[str, str],
) -> tuple[dict[int, str], list[str]]:
    """Convert 1-based element keys to 0-based indices.

    Reports number elements from 1, so the keys the UI hands back are
    1-based and are converted here. Returns ``(targets, rejected_keys)``.
    """
    targets: dict[int, str] = {}
    rejected: list[str] = []
    for key, text in mapping.items():
        try:
            position = int(key)
        except (TypeError, ValueError):
            rejected.append(str(key))
            continue
        if position < 1:
            rejected.append(str(key))
            continue
        targets[position - 1] = text
    return targets, rejected


def _apply_alt_text(
    pdf: pikepdf.Pdf,
    mapping: Mapping[str, str],
    *,
    fix_id: str,
    expected_tags: Sequence[str],
    noun: str,
) -> FixResult:
    """Write ``/Alt`` onto the named elements, verifying each one's tag.

    ``expected_tags`` are resolved structure tags (``"Figure"``), matched
    after role-map resolution so a document using a custom tag mapped to
    Figure is still recognised.
    """
    if not mapping:
        return FixResult(fix_id, False, f"No {noun} alt text values provided")

    targets, rejected = _parse_targets(mapping)
    if rejected:
        joined = ", ".join(sorted(rejected))
        return FixResult(
            fix_id, False, f"Element references are not 1-based numbers: {joined}",
        )
    if not targets:
        return FixResult(fix_id, False, "No element references provided")

    elements, _role_map = walk_structure_tree(pdf)
    if not elements:
        return FixResult(fix_id, False, "No structure tree found")

    by_index: dict[int, StructElement] = {e.index: e for e in elements}

    written = 0
    missing: list[int] = []
    wrong_kind: list[str] = []
    for index, text in sorted(targets.items()):
        element = by_index.get(index)
        if element is None:
            missing.append(index + 1)
            continue
        if element.resolved_tag not in expected_tags:
            # The document has moved on since the report was produced, or
            # the reference is simply wrong. Writing here would describe
            # the wrong thing convincingly.
            wrong_kind.append(f"{index + 1} is <{element.resolved_tag or '?'}>")
            continue
        element.obj[Name("/Alt")] = String(text)
        written += 1

    problems: list[str] = []
    if missing:
        problems.append(
            "not found: " + ", ".join(str(n) for n in missing)
        )
    if wrong_kind:
        problems.append(
            f"not a {noun}: " + "; ".join(wrong_kind)
        )

    if written and not problems:
        return FixResult(
            fix_id, True, f"Set alt text on {written} {noun} element(s)",
        )
    if written:
        return FixResult(
            fix_id, True,
            f"Set alt text on {written} of {len(targets)} {noun} element(s)"
            + f" — {'; '.join(problems)}",
        )
    return FixResult(
        fix_id, False,
        f"Set no alt text — {'; '.join(problems)}",
    )


def fix_alt_text(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Set ``/Alt`` on figures from user-supplied descriptions.

    Covers ``<Figure>`` — the tag for an image that carries meaning. An
    image that carries none should be an artifact instead, which is a
    different fix.
    """
    return _apply_alt_text(
        pdf, opts.alt_text_map,
        fix_id="fix_alt_text",
        expected_tags=("Figure",),
        noun="figure",
    )


def fix_formula_alt(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Set ``/Alt`` on ``<Formula>`` elements from user-supplied text.

    A formula rendered as glyphs reads as noise: a screen reader
    announces the characters, not the mathematics. The alt text is the
    only thing that carries the meaning, short of MathML.
    """
    return _apply_alt_text(
        pdf, opts.formula_alt_map,
        fix_id="fix_formula_alt",
        expected_tags=("Formula",),
        noun="formula",
    )
