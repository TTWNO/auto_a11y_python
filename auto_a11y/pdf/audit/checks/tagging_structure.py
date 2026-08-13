"""Tagging-structure-related accessibility checks.

Twenty-eight checks ported from pdfMax's
``python/checker/pdf_accessibility_audit.py`` covering the structure
tree, RoleMap correctness, named structure containers (TOC, Ruby,
Warichu), Note/Formula/multimedia element conventions, embedded files,
optional content groups, intra-document link destinations, Form XObject
reuse, Reference XObjects, tab-order, the relaxed PDF/UA-2 heading
rules, and a structural reading-order check that consumes the Phase 3
visual-position collectors.

Mirrors the convention established in
:mod:`auto_a11y.pdf.audit.checks.headings`: each check is a plain
function ``(ctx) -> list[CheckResult]`` and the module exposes a
:data:`TAGGING_STRUCTURE_CHECKS` registry list. ``CheckResult``
``name`` and ``standard`` strings match pdfMax verbatim so Phase 6's
check catalogue can map them.

Already ported elsewhere (intentionally not re-ported here):

* ``Heading hierarchy valid`` / ``No multiple headings per node`` /
  ``No mixed heading tag types`` — :mod:`auto_a11y.pdf.audit.checks.headings`.
* ``Figure elements have BBox attribute`` —
  :mod:`auto_a11y.pdf.audit.checks.images_alt_text`.

Three checks deferred when this module was first written have since
landed, each on data that did not exist then:

* :func:`check_artifact_classification_subtypes` (PDF/UA-2; pdfMax line
  ~7505) needs to name the structure element nearest an unclassified
  ``/Artifact``, which means knowing how far the marker sits from the
  nearest ``/MCID``. pdfMax measured that in decoded stream bytes;
  :class:`~auto_a11y.pdf.audit.content_classification.ArtifactMark`
  measures it in operator ordinals, which answers the same question
  without depending on how the stream is encoded.
* :func:`check_all_content_tagged` (PDF/UA, WCAG 1.3.1; pdfMax line
  ~11489) needs a per-page count of marked-content sections carrying an
  ``/MCID``, now recorded as
  :attr:`~auto_a11y.pdf.audit.content_classification.PageContentClassification.mcid_marks`.
* :func:`check_formula_unicode_mapping_valid` (Matterhorn 17-003; pdfMax
  line ~7262) consumes
  :func:`auto_a11y.pdf.audit.font_metadata.extract_font_metadata`, the
  collector that also unblocked the Matterhorn 10/31-series font checks.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator

import pikepdf

from auto_a11y.pdf.audit import pikepdf_helpers
from auto_a11y.pdf.audit.reading_order import (
    ElementPosition,
    ReadingOrderMismatch,
)
from auto_a11y.pdf.audit.structure import STANDARD_PDF_TAGS, resolve_tag
from auto_a11y.pdf.models import AuditContext, CheckResult


# ---------------------------------------------------------------------------
# Module-private constants
# ---------------------------------------------------------------------------


#: Valid direct children of a ``TOC`` element (Matterhorn 09-006).
_VALID_TOC_CHILDREN: frozenset[str] = frozenset({"TOCI"})

#: Valid direct children of a ``TOCI`` element (Matterhorn 09-006).
_VALID_TOCI_CHILDREN: frozenset[str] = frozenset(
    {"TOC", "Reference", "P", "Lbl", "NonStruct"}
)

#: Permitted direct children of a ``Ruby`` element (Matterhorn 09-007).
#: ``RB`` and ``RT`` are required; ``RP`` is optional ("ruby parenthesis"
#: fallback). Required tags are checked inline.
_VALID_RUBY_CHILDREN: frozenset[str] = frozenset({"RB", "RT", "RP"})

#: Permitted direct children of a ``Warichu`` element (Matterhorn
#: 09-008). Both ``WT`` and ``WP`` are required and exhaustive.
_VALID_WARICHU_CHILDREN: frozenset[str] = frozenset({"WT", "WP"})

#: Multimedia / embedded-content tags that PDF/UA-2 requires to carry
#: ``/AF`` associated-file pointers.
_AF_TAGS: frozenset[str] = frozenset(
    {"Formula", "Figure", "RichMedia", "Screen"}
)

#: Reading-order correlation thresholds. Mirrors pdfMax line ~3786:
#: ``>= 0.9`` PASSes outright, ``>= 0.7`` WARNs, anything lower FAILs.
_READING_ORDER_PASS_THRESHOLD: float = 0.9
_READING_ORDER_WARN_THRESHOLD: float = 0.7

#: Window size for nearby-pair correlation. Matches the value
#: :func:`auto_a11y.pdf.audit.reading_order.compare_reading_orders`
#: uses internally — pdfMax line ~3707.
_READING_ORDER_NEARBY_WINDOW: int = 5


# ---------------------------------------------------------------------------
# check_structure_tree_exists
# ---------------------------------------------------------------------------


def check_structure_tree_exists(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 01-006: catalog has ``/StructTreeRoot`` with non-empty ``/K``.

    Mirrors pdfMax line ~4593. PASS when the structure tree exists and
    carries at least one ``/K`` entry; FAIL otherwise.
    """
    struct_root = pikepdf_helpers.get_dict(ctx.pdf.Root, "/StructTreeRoot")
    has_k = False
    if struct_root is not None:
        try:
            k_val = struct_root["/K"]
        except KeyError:
            k_val = None
        if k_val is not None:
            if isinstance(k_val, pikepdf.Array):
                has_k = len(k_val) > 0
            else:
                has_k = True
    if struct_root is not None and has_k:
        return [
            CheckResult(
                name="Structure tree exists",
                standard="Matterhorn 01-006",
                result="PASS",
                details=f"{len(ctx.elements)} structure elements found",
            )
        ]
    return [
        CheckResult(
            name="Structure tree exists",
            standard="Matterhorn 01-006",
            result="FAIL",
            details="No /StructTreeRoot or empty /K",
        )
    ]


# ---------------------------------------------------------------------------
# check_role_mapping_valid
# ---------------------------------------------------------------------------


def check_role_mapping_valid(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 02-001: every RoleMap entry resolves to a standard PDF tag.

    Mirrors pdfMax line ~5112. Walks the resolved RoleMap chain (through
    :func:`auto_a11y.pdf.audit.structure.resolve_tag`) and reports any
    custom tag whose ultimate target is not in
    :data:`auto_a11y.pdf.audit.structure.STANDARD_PDF_TAGS`.
    """
    invalid_mappings: list[str] = []
    for custom, standard in ctx.role_map.items():
        resolved = resolve_tag(custom, ctx.role_map)
        clean = resolved.lstrip("/")
        if clean not in STANDARD_PDF_TAGS:
            invalid_mappings.append(
                f"{custom} -> {standard} (resolves to {resolved}, not a standard tag)"
            )
    if not invalid_mappings:
        return [
            CheckResult(
                name="Role mapping valid",
                standard="Matterhorn 02-001",
                result="PASS",
                details=(
                    f"{len(ctx.role_map)} custom tags, all map to standard tags"
                ),
            )
        ]
    return [
        CheckResult(
            name="Role mapping valid",
            standard="Matterhorn 02-001",
            result="FAIL",
            details=f"Invalid mappings: {'; '.join(invalid_mappings)}",
        )
    ]


# ---------------------------------------------------------------------------
# check_no_circular_role_mappings
# ---------------------------------------------------------------------------


def check_no_circular_role_mappings(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 02-003: the RoleMap contains no cycles.

    Mirrors pdfMax line ~5127. Walks each entry, recording every target
    visited; if walking ``role_map[role_map[...]]`` ever returns to a
    previously seen tag, we have a cycle and report the chain.
    """
    role_map = ctx.role_map
    circular: list[str] = []
    for custom in role_map:
        seen: set[str] = set()
        current = custom
        while current in role_map and current not in seen:
            seen.add(current)
            current = role_map[current]
        if current in seen:
            chain = [custom]
            c = role_map[custom]
            while c != current:
                chain.append(c)
                c = role_map[c]
            chain.append(current)
            circular.append(" -> ".join(chain))
    if not circular:
        return [
            CheckResult(
                name="No circular role mappings",
                standard="Matterhorn 02-003",
                result="PASS",
                details="No circular mappings in RoleMap",
            )
        ]
    return [
        CheckResult(
            name="No circular role mappings",
            standard="Matterhorn 02-003",
            result="FAIL",
            details=f"Circular role mapping(s): {'; '.join(circular)}",
        )
    ]


# ---------------------------------------------------------------------------
# check_standard_tags_not_remapped
# ---------------------------------------------------------------------------


def check_standard_tags_not_remapped(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 02-004: standard PDF tags are not remapped via RoleMap.

    Mirrors pdfMax line ~5151. A RoleMap entry whose key (after
    stripping the leading ``/``) is in
    :data:`auto_a11y.pdf.audit.structure.STANDARD_PDF_TAGS` redefines a
    standard tag, which assistive technology can't reason about.
    """
    remapped: list[str] = []
    for custom in ctx.role_map:
        clean = custom.lstrip("/")
        if clean in STANDARD_PDF_TAGS:
            remapped.append(f"{custom} -> {ctx.role_map[custom]}")
    if not remapped:
        return [
            CheckResult(
                name="Standard tags not remapped",
                standard="Matterhorn 02-004",
                result="PASS",
                details="No standard structure types remapped in RoleMap",
            )
        ]
    return [
        CheckResult(
            name="Standard tags not remapped",
            standard="Matterhorn 02-004",
            result="FAIL",
            details=f"Standard tag(s) remapped: {'; '.join(remapped)}",
        )
    ]


# ---------------------------------------------------------------------------
# check_tab_order_follows_structure
# ---------------------------------------------------------------------------


def check_tab_order_follows_structure(ctx: AuditContext) -> list[CheckResult]:
    """PDF/UA, WCAG 2.1.1: every page declares ``/Tabs = /S``.

    Mirrors pdfMax line ~4769. PASS when every page sets ``/Tabs`` to
    ``/S`` (structure-driven tab order); FAIL when any page either
    omits ``/Tabs`` or sets it to a value other than ``/S``.
    """
    tab_ok = True
    tab_details: list[str] = []
    for page_num, page in enumerate(ctx.pdf.pages, start=1):
        tabs_name = pikepdf_helpers.get_name(page.obj, "/Tabs")
        if tabs_name is not None and str(tabs_name) == "/S":
            tab_details.append(f"Page {page_num}: /Tabs = /S (structure)")
        elif tabs_name is not None:
            tab_details.append(
                f"Page {page_num}: /Tabs = {str(tabs_name)} (not structure)"
            )
            tab_ok = False
        else:
            # /Tabs may also be a String in malformed PDFs; pdfMax just
            # printed ``str(tabs)``. Reproduce that liberality.
            try:
                tabs_obj = page.obj["/Tabs"]
            except KeyError:
                tab_details.append(f"Page {page_num}: /Tabs not set")
                tab_ok = False
                continue
            tab_details.append(
                f"Page {page_num}: /Tabs = {str(tabs_obj)} (not structure)"
            )
            tab_ok = False

    if tab_ok:
        return [
            CheckResult(
                name="Tab order follows structure",
                standard="PDF/UA, WCAG 2.1.1",
                result="PASS",
                details="; ".join(tab_details),
            )
        ]
    return [
        CheckResult(
            name="Tab order follows structure",
            standard="PDF/UA, WCAG 2.1.1",
            result="FAIL",
            details="; ".join(tab_details),
        )
    ]


# ---------------------------------------------------------------------------
# check_toc_structure_valid
# ---------------------------------------------------------------------------


def check_toc_structure_valid(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 09-006: TOC contains only TOCI; TOCI contains only valid kids.

    Mirrors pdfMax line ~6829.
    """
    elements = ctx.elements
    toc_issues: list[str] = []
    for elem in elements:
        if elem.resolved_tag == "TOC":
            for ci in elem.children_indices:
                if 0 <= ci < len(elements):
                    child = elements[ci]
                    if child.resolved_tag not in _VALID_TOC_CHILDREN:
                        toc_issues.append(
                            f"[{elem.index + 1}] TOC has invalid child {child.resolved_tag}[{child.index + 1}]"
                        )
        elif elem.resolved_tag == "TOCI":
            for ci in elem.children_indices:
                if 0 <= ci < len(elements):
                    child = elements[ci]
                    if child.resolved_tag not in _VALID_TOCI_CHILDREN:
                        toc_issues.append(
                            f"[{elem.index + 1}] TOCI has invalid child {child.resolved_tag}[{child.index + 1}]"
                        )

    if not toc_issues:
        toc_count = sum(1 for e in elements if e.resolved_tag == "TOC")
        details = (
            f"{toc_count} TOC element(s) checked"
            if toc_count
            else "No TOC elements in document"
        )
        return [
            CheckResult(
                name="TOC structure valid",
                standard="Matterhorn 09-006",
                result="PASS",
                details=details,
            )
        ]
    return [
        CheckResult(
            name="TOC structure valid",
            standard="Matterhorn 09-006",
            result="FAIL",
            details=(
                f"{len(toc_issues)} TOC structure violation(s):"
                f" {'; '.join(toc_issues[:5])}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_ruby_structure_valid
# ---------------------------------------------------------------------------


def check_ruby_structure_valid(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 09-007: every Ruby has RB+RT (RP optional), no other kids.

    Mirrors pdfMax line ~6855.
    """
    elements = ctx.elements
    ruby_issues: list[str] = []
    for elem in elements:
        if elem.resolved_tag != "Ruby":
            continue
        child_tags: set[str] = set()
        for ci in elem.children_indices:
            if 0 <= ci < len(elements):
                child_tags.add(elements[ci].resolved_tag)
        if "RB" not in child_tags:
            ruby_issues.append(f"[{elem.index + 1}] Ruby missing required RB child")
        if "RT" not in child_tags:
            ruby_issues.append(f"[{elem.index + 1}] Ruby missing required RT child")
        invalid = child_tags - _VALID_RUBY_CHILDREN
        for inv in invalid:
            ruby_issues.append(f"[{elem.index + 1}] Ruby has invalid child {inv}")
    if not ruby_issues:
        ruby_count = sum(1 for e in elements if e.resolved_tag == "Ruby")
        details = (
            f"{ruby_count} Ruby element(s) checked"
            if ruby_count
            else "No Ruby elements in document"
        )
        return [
            CheckResult(
                name="Ruby structure valid",
                standard="Matterhorn 09-007",
                result="PASS",
                details=details,
            )
        ]
    return [
        CheckResult(
            name="Ruby structure valid",
            standard="Matterhorn 09-007",
            result="FAIL",
            details=(
                f"{len(ruby_issues)} Ruby structure violation(s):"
                f" {'; '.join(ruby_issues[:5])}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_warichu_structure_valid
# ---------------------------------------------------------------------------


def check_warichu_structure_valid(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 09-008: every Warichu has both WT and WP children.

    Mirrors pdfMax line ~6880.
    """
    elements = ctx.elements
    warichu_issues: list[str] = []
    for elem in elements:
        if elem.resolved_tag != "Warichu":
            continue
        child_tags: set[str] = set()
        for ci in elem.children_indices:
            if 0 <= ci < len(elements):
                child_tags.add(elements[ci].resolved_tag)
        if "WT" not in child_tags:
            warichu_issues.append(
                f"[{elem.index + 1}] Warichu missing required WT child"
            )
        if "WP" not in child_tags:
            warichu_issues.append(
                f"[{elem.index + 1}] Warichu missing required WP child"
            )
        invalid = child_tags - _VALID_WARICHU_CHILDREN
        for inv in invalid:
            warichu_issues.append(
                f"[{elem.index + 1}] Warichu has invalid child {inv}"
            )
    if not warichu_issues:
        warichu_count = sum(1 for e in elements if e.resolved_tag == "Warichu")
        details = (
            f"{warichu_count} Warichu element(s) checked"
            if warichu_count
            else "No Warichu elements in document"
        )
        return [
            CheckResult(
                name="Warichu structure valid",
                standard="Matterhorn 09-008",
                result="PASS",
                details=details,
            )
        ]
    return [
        CheckResult(
            name="Warichu structure valid",
            standard="Matterhorn 09-008",
            result="FAIL",
            details=(
                f"{len(warichu_issues)} Warichu structure violation(s):"
                f" {'; '.join(warichu_issues[:5])}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_note_tags_have_unique_ids
# ---------------------------------------------------------------------------


def check_note_tags_have_unique_ids(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 19-003: every Note element has a unique ``/ID``.

    Mirrors pdfMax line ~6961.
    """
    note_elements = [e for e in ctx.elements if e.resolved_tag == "Note"]
    if not note_elements:
        return [
            CheckResult(
                name="Note tags have unique IDs",
                standard="Matterhorn 19-003",
                result="NA",
                details="No Note elements in document",
            )
        ]

    missing_id: list[int] = []
    seen_ids: dict[str, int] = {}
    duplicate_ids: list[tuple[int, str]] = []
    for elem in note_elements:
        try:
            id_val = elem.obj["/ID"]
        except KeyError:
            id_val = None
        if id_val is None:
            missing_id.append(elem.index)
        else:
            id_str = str(id_val)
            if id_str in seen_ids:
                duplicate_ids.append((elem.index, id_str))
            else:
                seen_ids[id_str] = elem.index

    if not missing_id and not duplicate_ids:
        return [
            CheckResult(
                name="Note tags have unique IDs",
                standard="Matterhorn 19-003",
                result="PASS",
                details=(
                    f"All {len(note_elements)} Note element(s) have unique"
                    " /ID attributes"
                ),
            )
        ]
    issues: list[str] = []
    if missing_id:
        refs = " ".join(f"[{i + 1}]" for i in missing_id[:5])
        issues.append(f"{len(missing_id)} missing /ID: {refs}")
    if duplicate_ids:
        refs = " ".join(f"[{i + 1}]" for i, _ in duplicate_ids[:5])
        issues.append(f"{len(duplicate_ids)} duplicate /ID: {refs}")
    return [
        CheckResult(
            name="Note tags have unique IDs",
            standard="Matterhorn 19-003",
            result="FAIL",
            details=f"Note ID issues: {'; '.join(issues)}",
        )
    ]


# ---------------------------------------------------------------------------
# check_formula_alt_text
# ---------------------------------------------------------------------------


def check_formula_alt_text(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 17-002: every Formula element has /Alt or /ActualText.

    Mirrors pdfMax line ~6994.
    """
    formula_elements = [e for e in ctx.elements if e.resolved_tag == "Formula"]
    if not formula_elements:
        return [
            CheckResult(
                name="Formula elements have alt text or ActualText",
                standard="Matterhorn 17-002",
                result="NA",
                details="No Formula elements in document",
            )
        ]
    formulas_without_alt = [
        e for e in formula_elements if not e.alt_text and not e.actual_text
    ]
    if not formulas_without_alt:
        return [
            CheckResult(
                name="Formula elements have alt text or ActualText",
                standard="Matterhorn 17-002",
                result="PASS",
                details=(
                    f"All {len(formula_elements)} Formula element(s) have"
                    " alt text or ActualText"
                ),
            )
        ]
    refs = " ".join(f"[{e.index + 1}]" for e in formulas_without_alt[:5])
    return [
        CheckResult(
            name="Formula elements have alt text or ActualText",
            standard="Matterhorn 17-002",
            result="FAIL",
            details=(
                f"{len(formulas_without_alt)} Formula element(s) missing both"
                f" /Alt and /ActualText: {refs}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_mathml_associated_with_formula
# ---------------------------------------------------------------------------


def check_mathml_associated_with_formula(
    ctx: AuditContext,
) -> list[CheckResult]:
    """PDF/UA-2: every Formula has MathML via ``/AF`` or ``<math>`` text.

    Mirrors pdfMax line ~7394. WARN (not FAIL) is used per pdfMax's
    advisory treatment of PDF/UA-2 recommendations.
    """
    formula_elements = [e for e in ctx.elements if e.resolved_tag == "Formula"]
    if not formula_elements:
        return [
            CheckResult(
                name="MathML associated with Formula elements",
                standard="PDF/UA-2",
                result="NA",
                details="No Formula elements in document",
            )
        ]
    formulas_without_mathml: list[int] = []
    for elem in formula_elements:
        has_mathml = False
        try:
            af = elem.obj["/AF"]
        except KeyError:
            af = None
        if af is not None:
            has_mathml = True
        if not has_mathml:
            alt = (elem.alt_text or "").lower()
            actual = (elem.actual_text or "").lower()
            if "<math" in alt or "<math" in actual:
                has_mathml = True
        if not has_mathml:
            formulas_without_mathml.append(elem.index)

    if not formulas_without_mathml:
        return [
            CheckResult(
                name="MathML associated with Formula elements",
                standard="PDF/UA-2",
                result="PASS",
                details=(
                    f"All {len(formula_elements)} Formula element(s) have"
                    " MathML associations"
                ),
            )
        ]
    return [
        CheckResult(
            name="MathML associated with Formula elements",
            standard="PDF/UA-2",
            result="WARN",
            details=(
                f"{len(formulas_without_mathml)} Formula element(s) lack"
                " MathML association (PDF/UA-2 recommendation)"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_associated_files_on_embedded_content
# ---------------------------------------------------------------------------


def check_associated_files_on_embedded_content(
    ctx: AuditContext,
) -> list[CheckResult]:
    """PDF/UA-2: Formula/Figure/RichMedia/Screen elements carry ``/AF``.

    Mirrors pdfMax line ~7676. WARN-only.
    """
    af_elements: list[str] = []
    af_missing: list[str] = []
    for elem in ctx.elements:
        tag = elem.resolved_tag
        if tag not in _AF_TAGS:
            continue
        af_elements.append(tag)
        try:
            af = elem.obj["/AF"]
        except KeyError:
            af = None
        if af is None:
            af_missing.append(tag)

    if not af_elements:
        return [
            CheckResult(
                name="Associated Files property on embedded content",
                standard="PDF/UA-2",
                result="NA",
                details="No Formula/Figure/multimedia elements requiring /AF",
            )
        ]
    if not af_missing:
        return [
            CheckResult(
                name="Associated Files property on embedded content",
                standard="PDF/UA-2",
                result="PASS",
                details=(
                    f"All {len(af_elements)} embedded content element(s) have"
                    " /AF property"
                ),
            )
        ]
    return [
        CheckResult(
            name="Associated Files property on embedded content",
            standard="PDF/UA-2",
            result="WARN",
            details=(
                f"{len(af_missing)} element(s) lack /AF associated files:"
                f" {', '.join(af_missing[:10])}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_optional_content_groups_have_name
# ---------------------------------------------------------------------------


def check_optional_content_groups_have_name(
    ctx: AuditContext,
) -> list[CheckResult]:
    """Matterhorn 20-001: every OCG carries a ``/Name``.

    Mirrors pdfMax line ~7293. PASS when the document has no
    ``/OCProperties``, no ``/OCGs``, or every OCG has a non-empty
    ``/Name``; FAIL otherwise.
    """
    oc_props = pikepdf_helpers.get_dict(ctx.pdf.Root, "/OCProperties")
    if oc_props is None:
        return [
            CheckResult(
                name="Optional content groups have Name",
                standard="Matterhorn 20-001",
                result="NA",
                details="No optional content (layers) in document",
            )
        ]
    ocgs = pikepdf_helpers.get_array(oc_props, "/OCGs")
    if ocgs is None or len(ocgs) == 0:
        return [
            CheckResult(
                name="Optional content groups have Name",
                standard="Matterhorn 20-001",
                result="NA",
                details="No optional content groups defined",
            )
        ]

    unnamed = 0
    total = 0
    for i in range(len(ocgs)):
        ocg = ocgs[i]
        if not isinstance(ocg, pikepdf.Dictionary):
            continue
        total += 1
        # /Name on an OCG is conventionally a String per PDF 1.7 8.11.2.
        name_str = pikepdf_helpers.get_string(ocg, "/Name")
        if not name_str:
            unnamed += 1
    if unnamed == 0:
        return [
            CheckResult(
                name="Optional content groups have Name",
                standard="Matterhorn 20-001",
                result="PASS",
                details=(
                    f"All {total} optional content group(s) have /Name"
                ),
            )
        ]
    return [
        CheckResult(
            name="Optional content groups have Name",
            standard="Matterhorn 20-001",
            result="FAIL",
            details=(
                f"{unnamed} of {total} optional content group(s) missing /Name"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_optional_content_no_as_entry
# ---------------------------------------------------------------------------


def check_optional_content_no_as_entry(
    ctx: AuditContext,
) -> list[CheckResult]:
    """Matterhorn 20-002: ``/OCProperties /D`` carries no ``/AS`` entry.

    Mirrors pdfMax line ~7312. Auto-state changes are prohibited because
    they let layer visibility flip without user action, hiding content
    from assistive technology.
    """
    oc_props = pikepdf_helpers.get_dict(ctx.pdf.Root, "/OCProperties")
    if oc_props is None:
        return [
            CheckResult(
                name="Optional content has no AS entry",
                standard="Matterhorn 20-002",
                result="NA",
                details="No optional content in document",
            )
        ]
    oc_default = pikepdf_helpers.get_dict(oc_props, "/D")
    has_as = False
    if oc_default is not None:
        try:
            as_entry = oc_default["/AS"]
        except KeyError:
            as_entry = None
        if as_entry is not None:
            has_as = True
    if not has_as:
        return [
            CheckResult(
                name="Optional content has no AS entry",
                standard="Matterhorn 20-002",
                result="PASS",
                details=(
                    "No /AS (auto-state) entry in optional content default"
                    " configuration"
                ),
            )
        ]
    return [
        CheckResult(
            name="Optional content has no AS entry",
            standard="Matterhorn 20-002",
            result="FAIL",
            details=(
                "/AS entry found in /OCProperties/D — auto-state changes"
                " prohibited in PDF/UA"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_embedded_files_have_f_and_uf
# ---------------------------------------------------------------------------


def _walk_name_tree(
    node: pikepdf.Dictionary,
) -> Iterator[tuple[pikepdf.Object, pikepdf.Object]]:
    """Yield ``(key, value)`` pairs from a PDF name tree.

    Mirrors pdfMax's inner ``walk_name_tree`` helper at line ~7331.
    Non-dictionary kids are skipped silently — a malformed leaf must
    not abort the walk.
    """
    names = pikepdf_helpers.get_array(node, "/Names")
    if names is not None:
        i = 0
        while i + 1 < len(names):
            yield names[i], names[i + 1]
            i += 2
    kids = pikepdf_helpers.get_array(node, "/Kids")
    if kids is not None:
        for ki in range(len(kids)):
            kid = kids[ki]
            if isinstance(kid, pikepdf.Dictionary):
                yield from _walk_name_tree(kid)


def check_embedded_files_have_f_and_uf(
    ctx: AuditContext,
) -> list[CheckResult]:
    """Matterhorn 21-001: every embedded file has ``/F`` and ``/UF``.

    Mirrors pdfMax line ~7330.
    """
    names_dict = pikepdf_helpers.get_dict(ctx.pdf.Root, "/Names")
    embedded_files_tree = (
        pikepdf_helpers.get_dict(names_dict, "/EmbeddedFiles")
        if names_dict is not None
        else None
    )
    if embedded_files_tree is None:
        return [
            CheckResult(
                name="Embedded files have F and UF keys",
                standard="Matterhorn 21-001",
                result="NA",
                details="No embedded files in document",
            )
        ]

    ef_issues: list[str] = []
    ef_count = 0
    for key, filespec in _walk_name_tree(embedded_files_tree):
        ef_count += 1
        fname = str(key)
        if not isinstance(filespec, pikepdf.Dictionary):
            ef_issues.append(f"{fname} not a Dictionary")
            continue
        has_f = "/F" in filespec
        has_uf = "/UF" in filespec
        if not has_f or not has_uf:
            missing: list[str] = []
            if not has_f:
                missing.append("/F")
            if not has_uf:
                missing.append("/UF")
            ef_issues.append(f"{fname} missing {', '.join(missing)}")

    if not ef_issues:
        return [
            CheckResult(
                name="Embedded files have F and UF keys",
                standard="Matterhorn 21-001",
                result="PASS",
                details=(
                    f"All {ef_count} embedded file(s) have both /F and /UF"
                    " keys"
                ),
            )
        ]
    return [
        CheckResult(
            name="Embedded files have F and UF keys",
            standard="Matterhorn 21-001",
            result="FAIL",
            details=(
                f"{len(ef_issues)} of {ef_count} embedded file(s) missing"
                f" keys: {'; '.join(ef_issues[:3])}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_no_reference_xobjects
# ---------------------------------------------------------------------------


def check_no_reference_xobjects(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 30-001: no XObject carries a ``/Ref`` entry.

    Mirrors pdfMax line ~7065. Reference XObjects point at external PDF
    files and are forbidden in PDF/UA because the audited document
    must be self-contained.
    """
    ref_xobject_count = 0
    for page in ctx.pdf.pages:
        resources = pikepdf_helpers.get_dict(page.obj, "/Resources")
        if resources is None:
            continue
        xobjects = pikepdf_helpers.get_dict(resources, "/XObject")
        if xobjects is None:
            continue
        for xobj_key in xobjects.keys():
            try:
                xobj = xobjects[xobj_key]
            except (pikepdf.PdfError, KeyError, AttributeError):
                continue
            if not isinstance(xobj, (pikepdf.Stream, pikepdf.Dictionary)):
                continue
            if "/Ref" in xobj:
                ref_xobject_count += 1
    if ref_xobject_count == 0:
        return [
            CheckResult(
                name="No Reference XObjects",
                standard="Matterhorn 30-001",
                result="PASS",
                details="No Reference XObjects found",
            )
        ]
    return [
        CheckResult(
            name="No Reference XObjects",
            standard="Matterhorn 30-001",
            result="FAIL",
            details=(
                f"{ref_xobject_count} Reference XObject(s) found —"
                " prohibited in PDF/UA"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_form_xobjects_with_mcids_not_reused
# ---------------------------------------------------------------------------


def _form_xobject_has_mcid(xobj: pikepdf.Stream) -> bool:
    """Return ``True`` if a Form XObject's content stream uses ``/MCID``.

    Walks the parsed content stream looking for a ``BDC`` operator whose
    second operand (the marked-content properties dictionary) carries a
    ``/MCID``. Mirrors pdfMax line ~7110. Failures (malformed content,
    decode errors) are treated as "no MCID" — the same defensive posture
    pdfMax used.
    """
    try:
        ops = pikepdf.parse_content_stream(xobj)
    except (pikepdf.PdfError, ValueError, TypeError, UnicodeDecodeError):
        return False
    for inst in ops:
        try:
            op = str(inst.operator)
        except (UnicodeDecodeError, ValueError):
            continue
        if op != "BDC":
            continue
        operands = inst.operands
        if len(operands) < 2:
            continue
        prop = operands[1]
        if not isinstance(prop, pikepdf.Dictionary):
            continue
        if "/MCID" in prop:
            return True
    return False


def check_form_xobjects_with_mcids_not_reused(
    ctx: AuditContext,
) -> list[CheckResult]:
    """Matterhorn 30-002: a Form XObject with MCIDs appears on one page only.

    Mirrors pdfMax line ~7088. Walking pages, we group XObjects by their
    ``objgen`` identity (PDF object number, stable across save/reload)
    and flag any Form XObject that (a) appears in multiple pages' XObject
    resources *and* (b) carries at least one ``BDC`` with ``/MCID``.
    Reusing such an XObject creates duplicate structure-tree references.
    """
    form_xobj_pages: dict[tuple[int, int], set[int]] = {}
    form_xobj_has_mcid: dict[tuple[int, int], bool] = {}

    for page_idx, page in enumerate(ctx.pdf.pages):
        resources = pikepdf_helpers.get_dict(page.obj, "/Resources")
        if resources is None:
            continue
        xobjects = pikepdf_helpers.get_dict(resources, "/XObject")
        if xobjects is None:
            continue
        for xobj_key in xobjects.keys():
            try:
                xobj_raw = xobjects[xobj_key]
            except (pikepdf.PdfError, KeyError, AttributeError):
                continue
            if not isinstance(xobj_raw, pikepdf.Stream):
                continue
            xobj_subtype = pikepdf_helpers.get_name(xobj_raw, "/Subtype")
            if xobj_subtype is None or str(xobj_subtype) != "/Form":
                continue
            xobj_id = xobj_raw.objgen
            form_xobj_pages.setdefault(xobj_id, set()).add(page_idx)
            if xobj_id not in form_xobj_has_mcid:
                form_xobj_has_mcid[xobj_id] = _form_xobject_has_mcid(xobj_raw)

    reused = [
        xid
        for xid, pages in form_xobj_pages.items()
        if len(pages) > 1 and form_xobj_has_mcid.get(xid, False)
    ]
    if not reused:
        return [
            CheckResult(
                name="Form XObjects with MCIDs not reused",
                standard="Matterhorn 30-002",
                result="PASS",
                details="No Form XObjects with MCIDs are reused across pages",
            )
        ]
    return [
        CheckResult(
            name="Form XObjects with MCIDs not reused",
            standard="Matterhorn 30-002",
            result="FAIL",
            details=(
                f"{len(reused)} Form XObject(s) with marked content IDs are"
                " reused on multiple pages"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_structure_destinations_for_intra_links
# ---------------------------------------------------------------------------


def _link_is_intra_document(annot: pikepdf.Dictionary) -> bool:
    """Return ``True`` when a Link annot targets an in-document destination.

    Mirrors pdfMax line ~7427. An annotation is intra-document if it
    has either a ``/Dest`` entry or an ``/A`` action whose ``/S``
    (action subtype) is ``/GoTo``.
    """
    try:
        dest = annot["/Dest"]
    except KeyError:
        dest = None
    if dest is not None:
        return True
    action = pikepdf_helpers.get_dict(annot, "/A")
    if action is None:
        return False
    action_type = pikepdf_helpers.get_name(action, "/S")
    return action_type is not None and str(action_type) == "/GoTo"


def check_structure_destinations_for_intra_links(
    ctx: AuditContext,
) -> list[CheckResult]:
    """PDF/UA-2: every intra-document link carries an ``/SD`` destination.

    Mirrors pdfMax line ~7421. WARN-only (advisory under PDF/UA-2).
    """
    intra_links: list[pikepdf.Dictionary] = []
    for page in ctx.pdf.pages:
        annots = pikepdf_helpers.get_array(page.obj, "/Annots")
        if annots is None:
            continue
        for ai in range(len(annots)):
            item = annots[ai]
            if not isinstance(item, pikepdf.Dictionary):
                continue
            subtype = pikepdf_helpers.get_name(item, "/Subtype")
            if subtype is None or str(subtype) != "/Link":
                continue
            if _link_is_intra_document(item):
                intra_links.append(item)

    if not intra_links:
        return [
            CheckResult(
                name="Structure destinations for intra-document links",
                standard="PDF/UA-2",
                result="NA",
                details="No intra-document links found",
            )
        ]
    links_without_sd = 0
    for annot in intra_links:
        try:
            sd = annot["/SD"]
        except KeyError:
            sd = None
        if sd is None:
            links_without_sd += 1
    if links_without_sd == 0:
        return [
            CheckResult(
                name="Structure destinations for intra-document links",
                standard="PDF/UA-2",
                result="PASS",
                details=(
                    f"All {len(intra_links)} intra-document link(s) have"
                    " structure destinations"
                ),
            )
        ]
    return [
        CheckResult(
            name="Structure destinations for intra-document links",
            standard="PDF/UA-2",
            result="WARN",
            details=(
                f"{links_without_sd} of {len(intra_links)} intra-document"
                " link(s) lack /SD structure destination (PDF/UA-2"
                " recommendation)"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_pdfua2_heading_hierarchy
# ---------------------------------------------------------------------------


def check_pdfua2_heading_hierarchy(ctx: AuditContext) -> list[CheckResult]:
    """PDF/UA-2: heading levels skip at most one level within a Sect.

    Mirrors pdfMax line ~7640. PDF/UA-2 relaxes the strict "no skipped
    levels" rule from PDF/UA-1: within a single section, the first
    heading sets the base, and subsequent headings must not skip more
    than one level *down* from any previously seen heading in that
    section.
    """
    sect_headings: dict[int | str, list[int]] = {}
    for elem in ctx.elements:
        tag = elem.resolved_tag
        if tag in ("H1", "H2", "H3", "H4", "H5", "H6"):
            level = int(tag[1])
            sect_key: int | str = (
                elem.parent_index if elem.parent_index >= 0 else "root"
            )
            sect_headings.setdefault(sect_key, []).append(level)

    heading_issues: list[str] = []
    for levels in sect_headings.values():
        if len(levels) < 2:
            continue
        seen_max = levels[0]
        for lvl in levels[1:]:
            if lvl > seen_max + 1:
                heading_issues.append(f"H{seen_max}->H{lvl}")
            seen_max = max(seen_max, lvl)

    if not heading_issues:
        return [
            CheckResult(
                name="PDF/UA-2 heading hierarchy",
                standard="PDF/UA-2",
                result="PASS",
                details=(
                    "Heading hierarchy is valid under PDF/UA-2 relaxed rules"
                ),
            )
        ]
    unique_issues = sorted(set(heading_issues))[:5]
    return [
        CheckResult(
            name="PDF/UA-2 heading hierarchy",
            standard="PDF/UA-2",
            result="WARN",
            details=(
                f"Heading level skips under PDF/UA-2 rules:"
                f" {', '.join(unique_issues)}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_reading_order_matches_visual_layout
# ---------------------------------------------------------------------------


def _correlation_from_mismatches(
    common_size: int, mismatch_count: int
) -> float:
    """Reproduce pdfMax's Spearman-like correlation formula.

    pdfMax (line ~3727) uses
    ``1 - (inversions / (n * min(n, 5) / 2))``. We re-derive ``n``
    (the size of the structure∩visual common set) and plug in the
    inversion count returned by the Phase 3 collector.
    """
    if common_size < 2:
        return 1.0
    max_inversions = common_size * min(common_size, _READING_ORDER_NEARBY_WINDOW) / 2.0
    if max_inversions <= 0:
        return 1.0
    # ``mismatch_count`` is derived from a different common set and can
    # exceed ``max_inversions``, which would yield a negative ratio shown
    # as e.g. "-40%". Clamp into [0, 1] so the printed percentage stays
    # sensible (the >= 0.9 / >= 0.7 verdict is unaffected).
    return max(0.0, 1.0 - (mismatch_count / max_inversions))


def _reading_order_warn_no_data() -> CheckResult:
    """Return the WARN result used when visual-position data is unavailable.

    pdfMax line ~3756 uses this when pdfminer fails to extract any
    visual blocks; we use the same wording.
    """
    return CheckResult(
        name="Reading order matches visual layout",
        standard="WCAG 1.3.2",
        result="WARN",
        details="Could not extract visual positions from PDF",
    )


def _structure_order_indices(
    ctx: AuditContext, positions: dict[int, ElementPosition]
) -> list[int]:
    """Mirror pdfMax's leaf-element filter for structure order (line ~3771).

    A structure element appears in the comparison if it has a position
    *and* it carries at least one MCID or alt text *and* its tag is
    not the all-encompassing ``Document``. This keeps the comparison
    grounded in leaf content rather than container tags.
    """
    return [
        e.index
        for e in ctx.elements
        if e.resolved_tag != "Document"
        and (e.mcids or e.alt_text)
        and e.index in positions
    ]


def check_reading_order_matches_visual_layout(
    ctx: AuditContext,
) -> list[CheckResult]:
    """WCAG 1.3.2: tagged reading order tracks the visual layout.

    Consumes :attr:`AuditContext.visual_blocks`,
    :attr:`AuditContext.element_positions`,
    :attr:`AuditContext.visual_reading_order`, and
    :attr:`AuditContext.reading_order_mismatches` from the Phase 3
    collectors. PASS at correlation ≥ 0.9 with no mismatches; WARN at
    correlation ≥ 0.7; FAIL otherwise. Mirrors pdfMax line ~3786.
    """
    if (
        ctx.visual_blocks is None
        or ctx.element_positions is None
        or ctx.visual_reading_order is None
        or ctx.reading_order_mismatches is None
    ):
        return [_reading_order_warn_no_data()]
    if not ctx.visual_blocks or not ctx.element_positions:
        return [_reading_order_warn_no_data()]

    positions = ctx.element_positions
    mismatches: list[ReadingOrderMismatch] = ctx.reading_order_mismatches
    structure_order = _structure_order_indices(ctx, positions)
    visual_order = ctx.visual_reading_order

    common = set(structure_order) & set(visual_order)
    correlation = _correlation_from_mismatches(len(common), len(mismatches))

    columns = ctx.columns or []

    if correlation >= _READING_ORDER_PASS_THRESHOLD and not mismatches:
        return [
            CheckResult(
                name="Reading order matches visual layout",
                standard="WCAG 1.3.2",
                result="PASS",
                details=(
                    f"Structure order matches visual layout"
                    f" ({correlation:.0%} correlation,"
                    f" {len(positions)} elements positioned,"
                    f" {len(columns)} column(s) detected)"
                ),
            )
        ]
    examples = ""
    if mismatches:
        ex = mismatches[:3]
        examples = " — e.g. " + ", ".join(
            f"[{m.struct_first + 1}] before [{m.struct_second + 1}]"
            for m in ex
        )
    if correlation >= _READING_ORDER_WARN_THRESHOLD:
        return [
            CheckResult(
                name="Reading order matches visual layout",
                standard="WCAG 1.3.2",
                result="WARN",
                details=(
                    f"Minor reading order differences detected"
                    f" ({correlation:.0%} correlation,"
                    f" {len(mismatches)} mismatch(es)){examples}"
                ),
            )
        ]
    return [
        CheckResult(
            name="Reading order matches visual layout",
            standard="WCAG 1.3.2",
            result="FAIL",
            details=(
                f"Significant reading order mismatches"
                f" ({correlation:.0%} correlation,"
                f" {len(mismatches)} mismatch(es)){examples}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------


#: Phase 5.3's pipeline iterates this list in order. Phase 6's check
#: catalogue iterates the same list to enumerate every check name.


# ---------------------------------------------------------------------------
# check_no_empty_tags
# ---------------------------------------------------------------------------

#: Tags whose job is to group other elements. An empty one is odd but
#: carries no content of its own, so it is not a fault.
_GROUPING_TAGS = frozenset({
    "Document", "Part", "Sect", "Div", "Art", "BlockQuote",
    "TOC", "TOCI", "Index", "NonStruct",
    "Table", "TR", "THead", "TBody", "TFoot",
    "L", "LI",
    "RB", "RT", "RP", "Warichu", "WP", "WT",
})

#: Examples listed before the message is truncated.
_EXAMPLE_LIMIT = 10


def check_no_empty_tags(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 09-006: no leaf tag is empty of content.

    An empty leaf announces as nothing while still costing the reader a
    stop: navigating by element lands on it, reads out silence, and moves
    on. A screen-reader user cannot tell an empty paragraph from one whose
    text failed to reach them.

    Reported as a warning rather than a failure. Empty tags are noise
    rather than lost information — nothing the author wrote is missing —
    and a document can be entirely usable with a few of them.
    """
    empty: list[str] = []
    for element in ctx.elements:
        if element.resolved_tag in _GROUPING_TAGS:
            continue
        if element.children_indices:
            continue
        has_content = bool(
            (element.text_content or "").strip()
            or element.alt_text
            or element.actual_text
            or element.mcids
        )
        if not has_content:
            empty.append(f"[{element.index + 1}] {element.resolved_tag}")

    if not empty:
        if not ctx.elements:
            return [
                CheckResult(
                    name="No empty tags",
                    standard="Matterhorn 09-006",
                    result="NA",
                    details="No structure elements in document",
                )
            ]
        return [
            CheckResult(
                name="No empty tags",
                standard="Matterhorn 09-006",
                result="PASS",
                details="No empty leaf tags found",
            )
        ]

    return [
        CheckResult(
            name="No empty tags",
            standard="Matterhorn 09-006",
            result="WARN",
            details=(
                f"{len(empty)} empty leaf tag(s) with no text, alt text or"
                f" content: {'; '.join(empty[:_EXAMPLE_LIMIT])}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_correct_nesting
# ---------------------------------------------------------------------------

_HEADING_TAGS = frozenset({"H1", "H2", "H3", "H4", "H5", "H6"})

#: Where each positional tag is required to sit. A cell outside a row, or
#: a row outside a table, breaks the grid the reader navigates by.
_REQUIRED_PARENTS: dict[str, frozenset[str]] = {
    "TD": frozenset({"TR"}),
    "TH": frozenset({"TR"}),
    "TR": frozenset({"Table", "THead", "TBody", "TFoot"}),
    "LI": frozenset({"L"}),
    "Lbl": frozenset({"LI", "L"}),
    "LBody": frozenset({"LI"}),
}


def check_correct_nesting(ctx: AuditContext) -> list[CheckResult]:
    """PDF/UA, WCAG 1.3.1: structure elements sit inside legal parents.

    Two kinds of violation. An element inside another of its own kind — a
    paragraph in a paragraph, a heading in a heading — leaves a reader
    unable to tell where one block ends. And a positional element outside
    its required parent, such as a cell that is not in a row, breaks the
    grid or list that gives the surrounding content its meaning.
    """
    by_index = {e.index: e for e in ctx.elements}
    violations: list[str] = []

    for element in ctx.elements:
        parent = by_index.get(element.parent_index)
        if parent is None:
            continue
        child_tag = element.resolved_tag
        parent_tag = parent.resolved_tag
        reference = f"[{element.index + 1}]"

        if child_tag == "P" and parent_tag == "P":
            violations.append(f"{reference} P inside P")
        elif child_tag in _HEADING_TAGS and parent_tag in _HEADING_TAGS:
            violations.append(f"{reference} {child_tag} inside {parent_tag}")

        allowed = _REQUIRED_PARENTS.get(child_tag)
        if allowed is not None and parent_tag not in allowed:
            violations.append(
                f"{reference} {child_tag} inside {parent_tag or '?'},"
                + f" expected {' or '.join(sorted(allowed))}"
            )

    if not ctx.elements:
        return [
            CheckResult(
                name="Correct nesting",
                standard="PDF/UA, WCAG 1.3.1",
                result="NA",
                details="No structure elements in document",
            )
        ]
    if not violations:
        return [
            CheckResult(
                name="Correct nesting",
                standard="PDF/UA, WCAG 1.3.1",
                result="PASS",
                details="All structure elements sit inside a legal parent",
            )
        ]
    return [
        CheckResult(
            name="Correct nesting",
            standard="PDF/UA, WCAG 1.3.1",
            result="FAIL",
            details=(
                f"{len(violations)} nesting violation(s):"
                f" {'; '.join(violations[:_EXAMPLE_LIMIT])}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Content classification (Matterhorn 01-003, 01-004, 01-005)
# ---------------------------------------------------------------------------


def _pages_with(
    ctx: AuditContext, attribute: str
) -> list[int]:
    """Page numbers where ``attribute`` is non-zero, in order."""
    if ctx.content_classification is None:
        return []
    return [
        page.page_number
        for page in ctx.content_classification
        if getattr(page, attribute)
    ]


def _page_list(pages: list[int]) -> str:
    shown = ", ".join(str(p) for p in pages[:_EXAMPLE_LIMIT])
    if len(pages) > _EXAMPLE_LIMIT:
        shown += f" and {len(pages) - _EXAMPLE_LIMIT} more"
    return shown


def check_artifact_not_inside_tagged(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 01-003: an artifact is not nested inside tagged content.

    An artifact declared inside real content tells a reader to skip
    something that sits in the middle of what they are reading, which
    either loses text or interrupts it depending on how the reader
    resolves the contradiction.
    """
    name = "Artifact not inside tagged content"
    standard = "Matterhorn 01-003"
    if ctx.content_classification is None:
        return [CheckResult(
            name=name, standard=standard, result="NA",
            details="Page content was not classified",
        )]

    pages = _pages_with(ctx, "artifact_inside_tagged")
    if not pages:
        return [CheckResult(
            name=name, standard=standard, result="PASS",
            details="No artifact is nested inside tagged content",
        )]
    return [CheckResult(
        name=name, standard=standard, result="FAIL",
        details=(
            f"Artifacts nested inside tagged content on page(s)"
            f" {_page_list(pages)}"
        ),
    )]


def check_tagged_not_inside_artifact(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 01-004: tagged content is not nested inside an artifact.

    This is the more damaging direction. Content marked as an artifact is
    content a reader is told to ignore, so real text inside one is simply
    never announced — it is on the page and unreachable.
    """
    name = "Tagged content not inside artifact"
    standard = "Matterhorn 01-004"
    if ctx.content_classification is None:
        return [CheckResult(
            name=name, standard=standard, result="NA",
            details="Page content was not classified",
        )]

    pages = _pages_with(ctx, "tagged_inside_artifact")
    if not pages:
        return [CheckResult(
            name=name, standard=standard, result="PASS",
            details="No tagged content is nested inside an artifact",
        )]
    return [CheckResult(
        name=name, standard=standard, result="FAIL",
        details=(
            f"Tagged content nested inside an artifact on page(s)"
            f" {_page_list(pages)} — that content is never announced"
        ),
    )]


def check_all_content_tagged_or_artifact(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 01-005: every mark is either tagged content or an artifact.

    Content belonging to neither is visible on the page and invisible to
    assistive technology, with nothing to indicate the omission.

    Counts untagged images as well as untagged text, which is what makes
    this check catch a scanned document: every page of one is a single
    untagged image and no text at all, so a text-only count reports it
    clean.
    """
    name = "All content is tagged or artifact"
    standard = "Matterhorn 01-005"
    if ctx.content_classification is None:
        return [CheckResult(
            name=name, standard=standard, result="NA",
            details="Page content was not classified",
        )]

    text = sum(p.untagged_text_operators for p in ctx.content_classification)
    images = sum(p.untagged_image_operators for p in ctx.content_classification)
    if not text and not images:
        return [CheckResult(
            name=name, standard=standard, result="PASS",
            details="All page content is inside a tagged or artifact section",
        )]

    pages = _pages_with(ctx, "untagged_operators")
    parts: list[str] = []
    if text:
        parts.append(f"{text} text operator(s)")
    if images:
        parts.append(f"{images} image(s)")
    return [CheckResult(
        name=name, standard=standard, result="FAIL",
        details=(
            " and ".join(parts)
            + " outside any tagged or artifact section, on page(s) "
            + _page_list(pages)
        ),
    )]


def check_all_content_tagged(ctx: AuditContext) -> list[CheckResult]:
    """PDF/UA, WCAG 1.3.1: no page carries text with no marked content.

    Mirrors pdfMax line ~11489, and is deliberately narrower than
    :func:`check_all_content_tagged_or_artifact`, which counts individual
    marks. This one asks a page-level question: a page that shows text
    and declares not one ``/MCID`` has no route from the structure tree
    to anything on it, however its marks are bracketed.
    """
    name = "All content tagged"
    standard = "PDF/UA, WCAG 1.3.1"
    if ctx.content_classification is None:
        return [CheckResult(
            name=name, standard=standard, result="NA",
            details="Page content was not classified",
        )]

    untagged = [
        page.page_number
        for page in ctx.content_classification
        if page.text_operators and not page.mcid_marks
    ]
    if not untagged:
        return [CheckResult(
            name=name, standard=standard, result="PASS",
            details="All text content is associated with structure tags",
        )]
    return [CheckResult(
        name=name, standard=standard, result="FAIL",
        details=(
            f"{len(untagged)} page(s) have text content not associated with"
            f" structure tags: page(s) {_page_list(untagged)}"
        ),
    )]


def check_artifact_classification_subtypes(
    ctx: AuditContext,
) -> list[CheckResult]:
    """PDF/UA-2: every artifact says which kind of artifact it is.

    Mirrors pdfMax line ~7505. WARN rather than FAIL: an unclassified
    artifact is still skippable, so what is lost is the reader's ability
    to skip *selectively*, not access to the content.
    """
    name = "Artifact classification subtypes"
    standard = "PDF/UA-2"
    if ctx.content_classification is None:
        return [CheckResult(
            name=name, standard=standard, result="NA",
            details="Page content was not classified",
        )]

    total = sum(len(page.artifact_marks) for page in ctx.content_classification)
    if not total:
        return [CheckResult(
            name=name, standard=standard, result="NA",
            details="No artifact markers found in content streams",
        )]

    unclassified: list[str] = []
    for page in ctx.content_classification:
        for mark in page.artifact_marks:
            if mark.has_subtype:
                continue
            element = _element_for_mcid(ctx, page.page_number, mark.nearest_mcid)
            ref = f"[{element + 1}] " if element is not None else ""
            unclassified.append(f"{ref}p.{page.page_number}")

    if not unclassified:
        return [CheckResult(
            name=name, standard=standard, result="PASS",
            details=f"All {total} artifact(s) have /Subtype classification",
        )]
    return [CheckResult(
        name=name, standard=standard, result="WARN",
        details=(
            f"{len(unclassified)} of {total} artifact(s) lack /Subtype"
            + " classification (Pagination/Layout/Page/Background): "
            + "; ".join(unclassified[:10])
        ),
    )]


def _element_for_mcid(
    ctx: AuditContext, page_number: int, mcid: int | None
) -> int | None:
    """Index of the structure element owning *mcid* on a page, if any.

    Marked-content ids are only unique within a page, so the page has to
    take part in the match — a bare ``mcid in elem.mcids`` would name an
    element from whichever page happened to reuse the number first.
    ``mcid_page_map`` records page *indices*, hence the offset from the
    1-based page number the classification carries.
    """
    if mcid is None:
        return None
    page_index = page_number - 1
    for elem in ctx.elements:
        if mcid in elem.mcids and elem.mcid_page_map.get(mcid) == page_index:
            return elem.index
    return None


def check_formula_unicode_mapping_valid(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 17-003: fonts carrying formula text map to real Unicode.

    Mirrors pdfMax line ~7262. A glyph mapped into the Private Use Area
    has no meaning outside the font that defines it: a screen reader
    reading it aloud produces nothing, and copied text arrives as
    replacement characters. Mathematical notation is where this happens,
    because the glyphs a formula needs are the ones a font is most likely
    to invent.

    Document-wide rather than per-formula, matching the original: a
    ToUnicode CMap belongs to a font, and nothing in it records which of
    its glyphs a given Formula element used.
    """
    name = "Formula Unicode mapping valid"
    standard = "Matterhorn 17-003"
    if not any(e.resolved_tag == "Formula" for e in ctx.elements):
        return [CheckResult(
            name=name, standard=standard, result="NA",
            details="No Formula elements in document",
        )]
    if ctx.font_metadata is None:
        return [CheckResult(
            name=name, standard=standard, result="NA",
            details="Font metadata was not collected",
        )]

    issues: list[str] = []
    for font in ctx.font_metadata.fonts:
        if font.to_unicode is None:
            continue
        pua = sorted({
            code
            for value in font.to_unicode.mapping.values()
            for code in (ord(ch) for ch in value)
            if _is_private_use(code)
        })
        if pua:
            sample = ", ".join(f"U+{code:04X}" for code in pua[:3])
            issues.append(f"{font.base_font}: PUA chars {sample}")

    if not issues:
        return [CheckResult(
            name=name, standard=standard, result="PASS",
            details="No PUA/unmapped characters detected in font ToUnicode maps",
        )]
    return [CheckResult(
        name=name, standard=standard, result="WARN",
        details=(
            f"{len(issues)} font(s) map to Private Use Area characters"
            f" (may affect Formula content): {'; '.join(issues[:3])}"
        ),
    )]


def _is_private_use(code: int) -> bool:
    """``True`` for a Unicode Private Use Area code point.

    The BMP area plus planes 15 and 16, which is what pdfMax tested.
    """
    return 0xE000 <= code <= 0xF8FF or 0xF0000 <= code <= 0xFFFFD


TAGGING_STRUCTURE_CHECKS: list[Callable[[AuditContext], list[CheckResult]]] = [
    check_structure_tree_exists,
    check_role_mapping_valid,
    check_no_circular_role_mappings,
    check_standard_tags_not_remapped,
    check_tab_order_follows_structure,
    check_toc_structure_valid,
    check_ruby_structure_valid,
    check_warichu_structure_valid,
    check_note_tags_have_unique_ids,
    check_no_empty_tags,
    check_correct_nesting,
    check_artifact_not_inside_tagged,
    check_tagged_not_inside_artifact,
    check_all_content_tagged_or_artifact,
    check_formula_alt_text,
    check_mathml_associated_with_formula,
    check_associated_files_on_embedded_content,
    check_optional_content_groups_have_name,
    check_optional_content_no_as_entry,
    check_embedded_files_have_f_and_uf,
    check_no_reference_xobjects,
    check_form_xobjects_with_mcids_not_reused,
    check_structure_destinations_for_intra_links,
    check_pdfua2_heading_hierarchy,
    check_reading_order_matches_visual_layout,
    check_all_content_tagged,
    check_artifact_classification_subtypes,
    check_formula_unicode_mapping_valid,
]


__all__ = [
    "TAGGING_STRUCTURE_CHECKS",
    "check_associated_files_on_embedded_content",
    "check_embedded_files_have_f_and_uf",
    "check_form_xobjects_with_mcids_not_reused",
    "check_formula_alt_text",
    "check_formula_unicode_mapping_valid",
    "check_mathml_associated_with_formula",
    "check_all_content_tagged",
    "check_all_content_tagged_or_artifact",
    "check_artifact_classification_subtypes",
    "check_artifact_not_inside_tagged",
    "check_correct_nesting",
    "check_tagged_not_inside_artifact",
    "check_no_circular_role_mappings",
    "check_no_empty_tags",
    "check_no_reference_xobjects",
    "check_note_tags_have_unique_ids",
    "check_optional_content_groups_have_name",
    "check_optional_content_no_as_entry",
    "check_pdfua2_heading_hierarchy",
    "check_reading_order_matches_visual_layout",
    "check_role_mapping_valid",
    "check_ruby_structure_valid",
    "check_standard_tags_not_remapped",
    "check_structure_destinations_for_intra_links",
    "check_structure_tree_exists",
    "check_tab_order_follows_structure",
    "check_toc_structure_valid",
    "check_warichu_structure_valid",
]
