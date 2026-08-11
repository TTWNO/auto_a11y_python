"""Fixes for ``/RoleMap`` — the document's custom-tag dictionary.

A tagged PDF may invent its own tag names, provided ``/RoleMap`` says
what each one means in standard terms: ``/Heading1 → /H1``. Assistive
technology resolves through that table, so a mapping that leads nowhere
standard leaves the tag meaningless — the reader gets an element of
unknown kind, which is no better than untagged content.

Three failure modes, one per fix: a mapping that loops, a mapping that
redefines a standard tag, and a mapping that lands on something that is
not standard at all.
"""
from __future__ import annotations

import pikepdf
from pikepdf import Name

from auto_a11y.pdf.fix.models import FixOptions, FixResult

# The structure tags defined by PDF 1.7 and PDF 2.0. A role map exists to
# translate into this set; anything outside it is not a destination.
STANDARD_PDF_TAGS = frozenset({
    "Document", "Part", "Art", "Sect", "Div", "BlockQuote", "Caption",
    "TOC", "TOCI", "Index", "NonStruct", "Private",
    "H", "H1", "H2", "H3", "H4", "H5", "H6",
    "P", "L", "LI", "Lbl", "LBody",
    "Table", "TR", "TH", "TD", "THead", "TBody", "TFoot",
    "Span", "Quote", "Note", "Reference", "BibEntry", "Code",
    "Link", "Annot", "Ruby", "RB", "RT", "RP", "Warichu", "WT", "WP",
    "Figure", "Formula", "Form",
})

_NO_TREE = "No structure tree — nothing to fix"
_NO_ROLE_MAP = "No RoleMap — nothing to fix"


def _role_map_object(pdf: pikepdf.Pdf) -> pikepdf.Object | None:
    struct_root = pdf.Root.get(Name("/StructTreeRoot"))
    if struct_root is None:
        return None
    return struct_root.get(Name("/RoleMap"))


def _as_dict(role_map: pikepdf.Object) -> dict[str, str]:
    """The role map as plain strings, slashes stripped from both sides."""
    return {
        str(key).lstrip("/"): str(role_map[key]).lstrip("/")
        for key in role_map.keys()
    }


def _cycle_members(mapping: dict[str, str]) -> set[str]:
    """Every tag whose resolution chain never terminates.

    A chain that revisits a tag it has already passed through will loop
    forever, so nothing on that chain resolves. Tags that merely *lead
    into* a cycle are included: they resolve no better than the cycle
    itself does.
    """
    looping: set[str] = set()
    for start in mapping:
        seen: list[str] = []
        current = start
        while current in mapping and current not in seen:
            seen.append(current)
            current = mapping[current]
        if current in seen:
            looping.update(seen)
    return looping


def fix_circular_roles(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Remove ``/RoleMap`` entries whose resolution loops.

    ``/Heading → /Title`` and ``/Title → /Heading`` describe each other
    and nothing else, so neither tag has a standard meaning. Every entry
    on the loop goes: breaking a single edge would leave the survivor
    pointing at a name that is no longer defined, which is the same
    problem with fewer entries.

    Overlaps :func:`fix_role_mapping`, which removes anything that fails
    to resolve to a standard tag — a superset. Kept separate because the
    report offers each against its own check.
    """
    role_map = _role_map_object(pdf)
    if role_map is None:
        return FixResult("fix_circular_roles", True, _NO_ROLE_MAP)

    looping = _cycle_members(_as_dict(role_map))
    if not looping:
        return FixResult(
            "fix_circular_roles", True, "No circular role mappings found",
        )

    for tag in sorted(looping):
        if Name(f"/{tag}") in role_map:
            del role_map[Name(f"/{tag}")]

    return FixResult(
        "fix_circular_roles", True,
        f"Removed {len(looping)} circular role mapping(s): "
        + ", ".join(sorted(looping)),
    )


def fix_standard_remap(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Remove ``/RoleMap`` entries that redefine a standard tag.

    Mapping ``/P → /Div`` tells a reader that every paragraph in the
    document is really a generic division. Standard tags already have
    fixed meanings; redefining one makes the whole document's tagging
    unreliable, since a consumer cannot know whether ``/P`` means what it
    says. Removing the entry restores the standard meaning without
    touching a single element.
    """
    role_map = _role_map_object(pdf)
    if role_map is None:
        return FixResult("fix_standard_remap", True, _NO_ROLE_MAP)

    removed: list[str] = []
    for key in list(role_map.keys()):
        source = str(key).lstrip("/")
        if source in STANDARD_PDF_TAGS:
            removed.append(f"{source} -> {str(role_map[key]).lstrip('/')}")
            del role_map[key]

    if not removed:
        return FixResult(
            "fix_standard_remap", True, "No standard tags remapped in RoleMap",
        )
    return FixResult(
        "fix_standard_remap", True,
        f"Removed {len(removed)} standard-tag remapping(s): "
        + "; ".join(removed),
    )


def fix_role_mapping(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Remove ``/RoleMap`` entries that never reach a standard tag.

    Follows each chain to its end. A chain finishing on something outside
    the standard set — or looping forever — leaves its tag meaningless,
    and an entry that says nothing is worse than no entry: it suggests
    the tag was accounted for.

    Removing the mapping does not remove the elements using that tag.
    They become untagged-in-effect rather than falsely accounted for,
    which is the honest state and what the corresponding check reports.
    """
    role_map = _role_map_object(pdf)
    if role_map is None:
        return FixResult("fix_role_mapping", True, _NO_ROLE_MAP)

    mapping = _as_dict(role_map)
    looping = _cycle_members(mapping)

    def resolves_to_standard(tag: str) -> bool:
        if tag in looping:
            return False
        seen: set[str] = set()
        current = tag
        while current in mapping and current not in seen:
            seen.add(current)
            current = mapping[current]
        return current in STANDARD_PDF_TAGS

    removed: list[str] = []
    for source in sorted(mapping):
        if resolves_to_standard(source):
            continue
        removed.append(f"{source} -> {mapping[source]}")
        if Name(f"/{source}") in role_map:
            del role_map[Name(f"/{source}")]

    if not removed:
        return FixResult(
            "fix_role_mapping", True,
            "All role mappings resolve to standard tags",
        )
    return FixResult(
        "fix_role_mapping", True,
        f"Removed {len(removed)} role mapping(s) that do not resolve to a"
        + f" standard tag: {'; '.join(removed)}",
    )
