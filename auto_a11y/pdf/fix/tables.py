"""Fixes for table structure: header cells and header scope.

A data table is only navigable if each cell knows which headers describe
it. Screen readers announce those headers as the user moves between
cells, so a table tagged entirely as ``<TD>`` becomes a flat run of
values with nothing to anchor them — the reader hears "42" with no way to
learn it is March's figure for Ontario.

Both fixes work from the audit's own structure walk, so element
references and tag resolution match what the report showed.
"""
from __future__ import annotations

from collections.abc import Mapping

import pikepdf
from pikepdf import Name

from auto_a11y.pdf.audit.structure import StructElement, walk_structure_tree
from auto_a11y.pdf.fix.models import FixOptions, FixResult

# Structure tags that group rows inside a table.
_ROW_GROUPS = ("THead", "TBody", "TFoot")

# Scope values that already say something useful.
_VALID_SCOPES = ("/Column", "/Row", "/Both")


def _children(
    by_index: Mapping[int, StructElement], element: StructElement
) -> list[StructElement]:
    """Direct child elements of ``element``, in document order."""
    return [
        by_index[i] for i in element.children_indices if i in by_index
    ]


def _collect_rows(
    by_index: Mapping[int, StructElement], table: StructElement
) -> list[StructElement]:
    """Every ``<TR>`` in a table, reaching through THead/TBody/TFoot."""
    rows: list[StructElement] = []
    for child in _children(by_index, table):
        if child.resolved_tag == "TR":
            rows.append(child)
        elif child.resolved_tag in _ROW_GROUPS:
            rows.extend(_collect_rows(by_index, child))
    return rows


def fix_table_headers(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Retag chosen table cells between ``<TD>`` and ``<TH>``.

    Which cells are headers is a judgement about the table's meaning that
    only a person can make, so this applies a selection rather than
    inferring one.

    Element references are 1-based, matching what the report prints and
    what every other fix here accepts. The original read them 0-based
    while the alt-text fixes read the same references 1-based, so one of
    the two acted on the cell next to the one the user picked.
    """
    if not opts.table_headers_map:
        return FixResult(
            "fix_table_headers", False, "No table header changes provided",
        )

    targets: dict[int, str] = {}
    rejected: list[str] = []
    for key, tag in opts.table_headers_map.items():
        if tag not in ("TH", "TD"):
            rejected.append(f"{key} -> {tag}")
            continue
        try:
            position = int(key)
        except (TypeError, ValueError):
            rejected.append(str(key))
            continue
        if position < 1:
            rejected.append(str(key))
            continue
        targets[position - 1] = tag

    if rejected:
        return FixResult(
            "fix_table_headers", False,
            "Not a 1-based element reference mapped to TH or TD: "
            + ", ".join(sorted(rejected)),
        )
    if not targets:
        return FixResult(
            "fix_table_headers", False, "No valid table header changes provided",
        )

    elements, _role_map = walk_structure_tree(pdf)
    if not elements:
        return FixResult("fix_table_headers", False, "No structure tree found")
    by_index = {e.index: e for e in elements}

    changed = 0
    problems: list[str] = []
    for index, new_tag in sorted(targets.items()):
        element = by_index.get(index)
        if element is None:
            problems.append(f"{index + 1} not found")
            continue
        if element.resolved_tag not in ("TD", "TH"):
            problems.append(
                f"{index + 1} is <{element.resolved_tag or '?'}>, not a table cell"
            )
            continue

        element.obj[Name("/S")] = Name(f"/{new_tag}")
        if new_tag == "TH":
            # A header with no scope leaves the reader to guess whether it
            # describes its column or its row; fix_table_scope refines this
            # from position, but a plain column header is the common case.
            element.obj[Name("/Scope")] = Name("/Column")
        elif Name("/Scope") in element.obj:
            # Scope on a data cell is meaningless and misleads a checker
            # into treating it as a header.
            del element.obj[Name("/Scope")]
        changed += 1

    if changed and not problems:
        return FixResult(
            "fix_table_headers", True, f"Retagged {changed} table cell(s)",
        )
    if changed:
        return FixResult(
            "fix_table_headers", True,
            f"Retagged {changed} of {len(targets)} table cell(s) — "
            + "; ".join(problems),
        )
    return FixResult(
        "fix_table_headers", False,
        "Retagged no cells — " + "; ".join(problems),
    )


def fix_table_scope(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Give every ``<TH>`` a ``/Scope`` inferred from its position.

    Position is a good enough guide for the ordinary table: the first row
    holds column headers, and a header starting a later row labels that
    row. Cells that already declare a scope are left alone — an existing
    value came from someone who could see the table, and this cannot.

    Irregular tables (spanning headers, multiple header rows) are not
    served well by position alone; those need ``/Headers`` associations,
    which is a separate and larger fix.
    """
    elements, _role_map = walk_structure_tree(pdf)
    if not elements:
        return FixResult("fix_table_scope", False, "No structure tree found")

    by_index = {e.index: e for e in elements}
    tables = [e for e in elements if e.resolved_tag == "Table"]
    if not tables:
        return FixResult("fix_table_scope", True, "No tables in document")

    scoped = 0
    for table in tables:
        for row_position, row in enumerate(_collect_rows(by_index, table)):
            for cell_position, cell in enumerate(_children(by_index, row)):
                if cell.resolved_tag != "TH":
                    continue
                existing = cell.obj.get(Name("/Scope"))
                if existing is not None and str(existing) in _VALID_SCOPES:
                    continue
                cell.obj[Name("/Scope")] = (
                    Name("/Row")
                    if row_position > 0 and cell_position == 0
                    else Name("/Column")
                )
                scoped += 1

    if scoped:
        return FixResult(
            "fix_table_scope", True, f"Set /Scope on {scoped} TH cell(s)",
        )
    return FixResult(
        "fix_table_scope", True,
        "All TH cells already have a valid /Scope",
    )
