"""Table-structure-related accessibility checks.

Seven checks ported from pdfMax's
``python/checker/pdf_accessibility_audit.py``:

* :func:`check_table_headers_defined` (PDF/UA, WCAG 1.3.1) — pdfMax
  line ~6256. Every non-empty ``Table`` has at least one ``TH``.
* :func:`check_table_header_scope_defined` (PDF/UA, WCAG 1.3.1) —
  pdfMax line ~6302. Every ``TH`` cell carries a valid ``/Scope``
  (``Column``/``Row``/``Both``).
* :func:`check_table_structure_sections` (PDF/UA, WCAG 1.3.1) —
  pdfMax line ~6351. Tables with direct ``TR`` children should also
  carry ``THead``/``TBody`` section wrappers.
* :func:`check_table_regularity` (PDF/UA, WCAG 1.3.1) — pdfMax line
  ~6387. Each ``TR`` in a table has the same number of cells (counting
  ``/ColSpan``).
* :func:`check_no_empty_tables` (Matterhorn 09-006) — pdfMax line
  ~6436. Tables contain at least one ``TD``/``TH`` data cell.
* :func:`check_table_captions` (WCAG 1.3.1) — pdfMax line ~6470.
  Tables have ``Caption`` direct children.

:func:`check_complex_table_headers_association` (pdfMax line ~6528) was
deferred when this module was written and has since landed; it reads
``/Headers``, ``/ColSpan`` and ``/RowSpan`` off cell dictionaries
through :mod:`auto_a11y.pdf.audit.pikepdf_helpers`.

Mirrors the convention established in
:mod:`auto_a11y.pdf.audit.checks.headings`: each check is a plain
function ``(ctx) -> list[CheckResult]`` and the module exposes a
:data:`TABLE_CHECKS` registry list. ``CheckResult.name`` and
``CheckResult.standard`` strings match pdfMax's verbatim so Phase 6's
catalogue can map them.
"""
from __future__ import annotations

from collections.abc import Callable

import pikepdf

from auto_a11y.pdf.audit import pikepdf_helpers
from auto_a11y.pdf.audit.structure import StructElement
from auto_a11y.pdf.models import AuditContext, CheckResult


# ---------------------------------------------------------------------------
# Module-private constants
# ---------------------------------------------------------------------------


#: ``TR`` is a direct child of these section-wrapper tags as well as
#: ``Table``. Used to drive depth-first traversal that follows section
#: wrappers but stops at cells.
_SECTION_WRAPPERS: frozenset[str] = frozenset({"THead", "TBody", "TFoot"})

#: Cell tags that hold real content.
_CELL_TAGS: frozenset[str] = frozenset({"TD", "TH"})

#: Valid values for the /Scope attribute on a ``TH``.
_VALID_SCOPES: frozenset[str] = frozenset({"Column", "Row", "Both"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _children(elem: StructElement, all_elements: list[StructElement]) -> list[StructElement]:
    """Resolve a StructElement's children-by-index against the flat list."""
    return [
        all_elements[ci]
        for ci in elem.children_indices
        if 0 <= ci < len(all_elements)
    ]


def _table_descendants(
    table_elem: StructElement, all_elements: list[StructElement]
) -> list[StructElement]:
    """All descendants of ``table_elem`` (BFS, no cycles).

    Mirrors pdfMax's inline BFS used by every table check (e.g. line
    ~6263). A duplicate-protection ``visited`` set guards against
    malformed structure trees that would otherwise loop forever.
    """
    out: list[StructElement] = []
    visited: set[int] = set()
    stack: list[int] = list(table_elem.children_indices)
    while stack:
        ci = stack.pop(0)
        if ci in visited or ci < 0 or ci >= len(all_elements):
            continue
        visited.add(ci)
        child = all_elements[ci]
        out.append(child)
        stack.extend(child.children_indices)
    return out


def _first_text_preview(elements_iter: list[StructElement]) -> str:
    """Return the first non-empty ``text_content`` snippet (max 60 chars)."""
    for desc in elements_iter:
        if desc.text_content:
            return desc.text_content[:60]
    return ""


def _read_colspan(cell_obj: pikepdf.Dictionary | None) -> int:
    """Read ``/ColSpan`` off a cell dictionary, defaulting to 1.

    pikepdf stores ``/ColSpan`` as a PDF integer; we use the typed
    helper :func:`pikepdf_helpers.get_int` which returns ``None`` when
    the entry is missing or non-integer. Anything non-integer at runtime
    falls back to 1 — matching pdfMax's defensive ``int(cs)`` guarded
    by ``try``.
    """
    if cell_obj is None:
        return 1
    cs = pikepdf_helpers.get_int(cell_obj, "/ColSpan")
    if cs is None or cs <= 0:
        return 1
    return cs


# ---------------------------------------------------------------------------
# check_table_headers_defined
# ---------------------------------------------------------------------------


def check_table_headers_defined(ctx: AuditContext) -> list[CheckResult]:
    """PDF/UA, WCAG 1.3.1: every non-empty table has at least one ``TH``.

    Mirrors pdfMax line ~6256. Empty tables (no ``TD``/``TH`` cells) are
    skipped here — :func:`check_no_empty_tables` catches them.
    """
    elements = ctx.elements
    table_elements = [e for e in elements if e.resolved_tag == "Table"]
    if not table_elements:
        return [
            CheckResult(
                name="Table headers defined",
                standard="PDF/UA, WCAG 1.3.1",
                result="NA",
                details="No tables found in document",
            )
        ]

    tables_missing_th: list[str] = []
    for table_elem in table_elements:
        descendants = _table_descendants(table_elem, elements)
        has_cells = any(d.resolved_tag in _CELL_TAGS for d in descendants)
        if not has_cells:
            continue  # caught by check_no_empty_tables
        has_th = any(d.resolved_tag == "TH" for d in descendants)
        if not has_th:
            preview = _first_text_preview(descendants)
            preview_part = (
                ", starts: " + preview if preview else ""
            )
            tables_missing_th.append(
                f"[{table_elem.index + 1}] Table"
                + f" ({len(descendants)} descendants{preview_part})"
            )

    if not tables_missing_th:
        return [
            CheckResult(
                name="Table headers defined",
                standard="PDF/UA, WCAG 1.3.1",
                result="PASS",
                details=(
                    f"All {len(table_elements)} table(s) have TH header cells"
                ),
            )
        ]
    return [
        CheckResult(
            name="Table headers defined",
            standard="PDF/UA, WCAG 1.3.1",
            result="FAIL",
            details=(
                f"{len(tables_missing_th)} table(s) missing TH (header) cells:"
                f" {'; '.join(tables_missing_th[:5])}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_table_header_scope_defined
# ---------------------------------------------------------------------------


def check_table_header_scope_defined(ctx: AuditContext) -> list[CheckResult]:
    """PDF/UA, WCAG 1.3.1: every ``TH`` carries a valid ``/Scope`` attribute.

    Mirrors pdfMax line ~6302. ``/Scope`` must be one of ``Column``,
    ``Row``, or ``Both``. Missing or invalid scope FAILs.
    """
    elements = ctx.elements
    table_elements = [e for e in elements if e.resolved_tag == "Table"]
    if not table_elements:
        return [
            CheckResult(
                name="Table header scope defined",
                standard="PDF/UA, WCAG 1.3.1",
                result="NA",
                details="No tables found in document",
            )
        ]

    th_no_scope: list[str] = []
    th_invalid_scope: list[str] = []
    for table_elem in table_elements:
        for desc in _table_descendants(table_elem, elements):
            if desc.resolved_tag != "TH":
                continue
            scope_name = pikepdf_helpers.get_name(desc.obj, "/Scope")
            scope_val = (
                str(scope_name).lstrip("/") if scope_name is not None else None
            )
            if scope_val is None:
                preview = (desc.text_content or "").strip()[:40]
                th_no_scope.append(
                    f"[{desc.index + 1}] TH '{preview}'"
                    if preview
                    else f"[{desc.index + 1}] TH"
                )
            elif scope_val not in _VALID_SCOPES:
                th_invalid_scope.append(
                    f"[{desc.index + 1}] TH Scope='{scope_val}'"
                )

    all_issues = th_no_scope + th_invalid_scope
    if all_issues:
        return [
            CheckResult(
                name="Table header scope defined",
                standard="PDF/UA, WCAG 1.3.1",
                result="FAIL",
                details=(
                    f"{len(th_no_scope)} TH cell(s) missing /Scope,"
                    f" {len(th_invalid_scope)} with invalid scope:"
                    f" {'; '.join(all_issues[:5])}"
                ),
            )
        ]
    th_count = sum(1 for e in elements if e.resolved_tag == "TH")
    if th_count > 0:
        return [
            CheckResult(
                name="Table header scope defined",
                standard="PDF/UA, WCAG 1.3.1",
                result="PASS",
                details=(
                    f"All {th_count} TH cell(s) have valid /Scope attribute"
                ),
            )
        ]
    return [
        CheckResult(
            name="Table header scope defined",
            standard="PDF/UA, WCAG 1.3.1",
            result="NA",
            details="No TH cells found (see 'Table headers defined' check)",
        )
    ]


# ---------------------------------------------------------------------------
# check_table_structure_sections
# ---------------------------------------------------------------------------


def check_table_structure_sections(ctx: AuditContext) -> list[CheckResult]:
    """PDF/UA, WCAG 1.3.1: tables use ``THead``/``TBody`` section wrappers.

    Mirrors pdfMax line ~6351. WARN-only — section wrappers improve
    semantics but PDF/UA does not strictly require them.
    """
    elements = ctx.elements
    table_elements = [e for e in elements if e.resolved_tag == "Table"]
    if not table_elements:
        return [
            CheckResult(
                name="Table structure sections",
                standard="PDF/UA, WCAG 1.3.1",
                result="NA",
                details="No tables found in document",
            )
        ]

    tables_no_sections: list[str] = []
    for table_elem in table_elements:
        direct_children = _children(table_elem, elements)
        has_thead = any(c.resolved_tag == "THead" for c in direct_children)
        has_tbody = any(c.resolved_tag == "TBody" for c in direct_children)
        has_direct_tr = any(c.resolved_tag == "TR" for c in direct_children)
        if has_direct_tr and not (has_thead or has_tbody):
            preview = ""
            for c in direct_children:
                if c.resolved_tag != "TR":
                    continue
                for cell in _children(c, elements):
                    if cell.text_content:
                        preview = cell.text_content.strip()[:40]
                        break
                if preview:
                    break
            preview_part = " (" + preview + ")" if preview else ""
            tables_no_sections.append(
                f"[{table_elem.index + 1}] Table" + preview_part
            )

    if not tables_no_sections:
        return [
            CheckResult(
                name="Table structure sections",
                standard="PDF/UA, WCAG 1.3.1",
                result="PASS",
                details=(
                    f"All {len(table_elements)} table(s) have THead/TBody"
                    " section wrappers"
                ),
            )
        ]
    return [
        CheckResult(
            name="Table structure sections",
            standard="PDF/UA, WCAG 1.3.1",
            result="WARN",
            details=(
                f"{len(tables_no_sections)} table(s) missing THead/TBody"
                f" section wrappers: {'; '.join(tables_no_sections[:5])}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_table_regularity
# ---------------------------------------------------------------------------


def _row_cell_counts_for_table(
    table_elem: StructElement, all_elements: list[StructElement]
) -> list[int]:
    """Walk a Table, returning per-row cell counts (with ColSpan applied).

    Iterates direct children that are either ``TR`` or section wrappers
    (``THead``/``TBody``/``TFoot``) and recurses into the latter to
    pick up their TRs. Mirrors pdfMax's BFS at line ~6388.
    """
    row_counts: list[int] = []
    visited: set[int] = set()
    stack: list[int] = list(table_elem.children_indices)
    while stack:
        ci = stack.pop(0)
        if ci in visited or ci < 0 or ci >= len(all_elements):
            continue
        visited.add(ci)
        child = all_elements[ci]
        if child.resolved_tag == "TR":
            count = 0
            for cci in child.children_indices:
                if 0 <= cci < len(all_elements):
                    cell = all_elements[cci]
                    if cell.resolved_tag in _CELL_TAGS:
                        count += _read_colspan(cell.obj)
            row_counts.append(count)
        elif child.resolved_tag in _SECTION_WRAPPERS:
            stack.extend(child.children_indices)
    return row_counts


def check_table_regularity(ctx: AuditContext) -> list[CheckResult]:
    """PDF/UA, WCAG 1.3.1: every row in a table has the same cell count.

    Mirrors pdfMax line ~6387. ``/ColSpan`` is added to each cell's
    contribution. WARN-only — irregular tables can still be readable
    when ``/Headers`` associations exist.
    """
    elements = ctx.elements
    table_elements = [e for e in elements if e.resolved_tag == "Table"]
    if not table_elements:
        return [
            CheckResult(
                name="Table regularity",
                standard="PDF/UA, WCAG 1.3.1",
                result="NA",
                details="No tables found in document",
            )
        ]

    irregular: list[str] = []
    for table_elem in table_elements:
        counts = _row_cell_counts_for_table(table_elem, elements)
        if counts and len(set(counts)) > 1:
            irregular.append(
                f"[{table_elem.index + 1}] Table:"
                + f" rows have {min(counts)}-{max(counts)} cells"
            )

    if not irregular:
        return [
            CheckResult(
                name="Table regularity",
                standard="PDF/UA, WCAG 1.3.1",
                result="PASS",
                details=(
                    f"All {len(table_elements)} table(s) have consistent cell"
                    " counts across rows"
                ),
            )
        ]
    return [
        CheckResult(
            name="Table regularity",
            standard="PDF/UA, WCAG 1.3.1",
            result="WARN",
            details=(
                f"{len(irregular)} table(s) have inconsistent cell counts"
                f" across rows: {'; '.join(irregular[:5])}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_no_empty_tables
# ---------------------------------------------------------------------------


def check_no_empty_tables(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 09-006: every table contains at least one ``TD``/``TH``.

    Mirrors pdfMax line ~6436.
    """
    elements = ctx.elements
    table_elements = [e for e in elements if e.resolved_tag == "Table"]
    if not table_elements:
        return [
            CheckResult(
                name="No empty tables",
                standard="Matterhorn 09-006",
                result="NA",
                details="No tables found in document",
            )
        ]

    empty_tables: list[str] = []
    for table_elem in table_elements:
        descendants = _table_descendants(table_elem, elements)
        if not any(d.resolved_tag in _CELL_TAGS for d in descendants):
            empty_tables.append(
                f"[{table_elem.index + 1}] Table (no TD/TH cells)"
            )

    if not empty_tables:
        return [
            CheckResult(
                name="No empty tables",
                standard="Matterhorn 09-006",
                result="PASS",
                details=(
                    f"All {len(table_elements)} table(s) contain data cells"
                ),
            )
        ]
    return [
        CheckResult(
            name="No empty tables",
            standard="Matterhorn 09-006",
            result="FAIL",
            details=(
                f"{len(empty_tables)} empty table(s) with no data cells:"
                f" {'; '.join(empty_tables[:5])}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_table_captions
# ---------------------------------------------------------------------------


def check_table_captions(ctx: AuditContext) -> list[CheckResult]:
    """WCAG 1.3.1: every non-empty table has a ``Caption`` direct child.

    Mirrors pdfMax line ~6470. WARN-only — Caption is recommended but
    not strictly required by PDF/UA.
    """
    elements = ctx.elements
    table_elements = [e for e in elements if e.resolved_tag == "Table"]
    if not table_elements:
        return [
            CheckResult(
                name="Table captions",
                standard="WCAG 1.3.1",
                result="NA",
                details="No tables found in document",
            )
        ]

    tables_no_caption: list[str] = []
    for table_elem in table_elements:
        descendants = _table_descendants(table_elem, elements)
        has_cells = any(d.resolved_tag in _CELL_TAGS for d in descendants)
        if not has_cells:
            continue
        has_caption = any(
            c.resolved_tag == "Caption" for c in _children(table_elem, elements)
        )
        if not has_caption:
            preview = _first_text_preview(descendants).strip()[:40]
            preview_part = " (" + preview + ")" if preview else ""
            tables_no_caption.append(
                f"[{table_elem.index + 1}] Table" + preview_part
            )

    if not tables_no_caption:
        return [
            CheckResult(
                name="Table captions",
                standard="WCAG 1.3.1",
                result="PASS",
                details=(
                    f"All {len(table_elements)} table(s) have Caption elements"
                ),
            )
        ]
    return [
        CheckResult(
            name="Table captions",
            standard="WCAG 1.3.1",
            result="WARN",
            details=(
                f"{len(tables_no_caption)} table(s) missing Caption element:"
                f" {'; '.join(tables_no_caption[:5])}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------


#: Phase 5.3's pipeline iterates this list in order. Phase 6's check
#: catalogue iterates the same list to enumerate every check name.
def _table_rows(
    table_elem: StructElement, all_elements: list[StructElement]
) -> list[StructElement]:
    """The ``TR`` elements of a table, in order, through section wrappers."""
    rows: list[StructElement] = []
    visited: set[int] = set()
    stack: list[int] = list(table_elem.children_indices)
    while stack:
        ci = stack.pop(0)
        if ci in visited or ci < 0 or ci >= len(all_elements):
            continue
        visited.add(ci)
        child = all_elements[ci]
        if child.resolved_tag == "TR":
            rows.append(child)
        elif child.resolved_tag in _SECTION_WRAPPERS:
            stack.extend(child.children_indices)
    return rows


def _has_span(cell: StructElement) -> bool:
    """``True`` when a cell declares ``/ColSpan`` or ``/RowSpan`` above 1."""
    for key in ("/ColSpan", "/RowSpan"):
        span = pikepdf_helpers.get_int(cell.obj, key)
        if span is not None and span > 1:
            return True
    return False


def check_complex_table_headers_association(
    ctx: AuditContext,
) -> list[CheckResult]:
    """PDF/UA, WCAG 1.3.1: complex tables associate cells with headers.

    Mirrors pdfMax line ~6528. ``/Scope`` on a ``TH`` says "this heads
    its column" — enough for a grid where every cell sits under exactly
    one header. It stops being enough once a table has headers running
    both ways, or cells that span, because then which headers govern a
    given cell is no longer derivable from position. ``/Headers`` on the
    cell states it outright, and that is what this check asks for.

    A table qualifies as complex when it has both column and row headers
    or any spanning cell; simple tables are reported as satisfied by
    ``/Scope`` alone. WARN rather than FAIL, matching pdfMax — the
    association is inferable often enough that a missing ``/Headers`` is
    a risk rather than a certainty.
    """
    name = "Complex table headers association"
    standard = "PDF/UA, WCAG 1.3.1"
    elements = ctx.elements
    tables = [e for e in elements if e.resolved_tag == "Table"]
    if not tables:
        return [CheckResult(
            name=name, standard=standard, result="NA",
            details="No tables found in document",
        )]

    complex_count = 0
    missing: list[str] = []
    for table in tables:
        rows = _table_rows(table, elements)
        if not rows:
            continue
        data_cells: list[StructElement] = []
        has_header = False
        has_span = False
        for row in rows:
            for cell in _children(row, elements):
                if cell.resolved_tag == "TH":
                    has_header = True
                elif cell.resolved_tag == "TD":
                    data_cells.append(cell)
                if cell.resolved_tag in _CELL_TAGS and _has_span(cell):
                    has_span = True
        if not has_header or not data_cells:
            continue

        first_row = _children(rows[0], elements)
        column_headers = any(c.resolved_tag == "TH" for c in first_row)
        row_headers = any(
            (cells := _children(row, elements)) and cells[0].resolved_tag == "TH"
            for row in rows[1:]
        )
        if not ((column_headers and row_headers) or has_span):
            continue

        complex_count += 1
        without_headers = [
            cell for cell in data_cells
            if cell.obj.get(pikepdf.Name("/Headers")) is None
        ]
        if without_headers:
            preview = (without_headers[0].text_content or "").strip()[:30]
            missing.append(
                f"[{table.index + 1}] Table: {len(without_headers)} TD cell(s)"
                + " missing /Headers"
                + (f" (e.g. {preview})" if preview else "")
            )

    if missing:
        return [CheckResult(
            name=name, standard=standard, result="WARN",
            details=(
                f"{len(missing)} complex table(s) have TD cells without"
                f" /Headers attribute: {'; '.join(missing[:5])}"
            ),
        )]
    if complex_count:
        return [CheckResult(
            name=name, standard=standard, result="PASS",
            details=(
                f"All {complex_count} complex table(s) have /Headers on TD cells"
            ),
        )]
    return [CheckResult(
        name=name, standard=standard, result="NA",
        details="No complex tables found (simple tables use /Scope)",
    )]


TABLE_CHECKS: list[Callable[[AuditContext], list[CheckResult]]] = [
    check_table_headers_defined,
    check_table_header_scope_defined,
    check_table_structure_sections,
    check_table_regularity,
    check_no_empty_tables,
    check_table_captions,
    check_complex_table_headers_association,
]


__all__ = [
    "TABLE_CHECKS",
    "check_complex_table_headers_association",
    "check_no_empty_tables",
    "check_table_captions",
    "check_table_header_scope_defined",
    "check_table_headers_defined",
    "check_table_regularity",
    "check_table_structure_sections",
]
