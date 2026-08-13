"""Form-related accessibility checks.

Nine checks ported from pdfMax's
``python/checker/pdf_accessibility_audit.py`` (lines ~5694-6234):

* :func:`check_widget_annotations_inside_form_tags` — every ``/Widget``
  annotation lives inside a ``Form`` structure element (Matterhorn
  28-012; pdfMax line ~5694).
* :func:`check_no_xfa_forms_present` — the document does not embed
  XFA form data via ``/AcroForm/XFA`` (Matterhorn 25-001; pdfMax line
  ~6018).
* :func:`check_form_fields_labeled` — every AcroForm terminal field
  carries a non-empty ``/TU`` tooltip (PDF/UA, WCAG 1.3.1, 4.1.2;
  pdfMax line ~6049).
* :func:`check_required_fields_flagged` — fields whose ``/T`` or
  ``/TU`` text hints "required" carry the ``/Ff`` Required bit
  (PDF/UA, WCAG 1.3.1, 3.3.2; pdfMax line ~6074).
* :func:`check_form_fields_tagged_in_structure` — there are at least
  as many ``Form`` structure tags as terminal AcroForm fields
  (PDF/UA, WCAG 1.3.1; pdfMax line ~6103).
* :func:`check_form_page_tab_order` — every page that carries Widget
  annotations declares ``/Tabs = /S`` (PDF/UA, WCAG 2.1.1, 2.4.3;
  pdfMax line ~6120).
* :func:`check_form_field_names_unique` — terminal AcroForm field
  ``/T`` names are unique (WCAG 4.1.2; pdfMax line ~6145).
* :func:`check_redundant_entry_in_forms` — labels that appear on more
  than one page are flagged so authors can offer pre-fill or
  selection (WCAG 3.3.7; pdfMax line ~6165).
* :func:`check_accessible_authentication` — password fields (``/Ff``
  bit 14) are reported so authors confirm paste / autofill works
  (WCAG 3.3.8; pdfMax line ~6202).

Mirrors the convention established in
:mod:`auto_a11y.pdf.audit.checks.headings`: each check is a plain
function ``(ctx) -> list[CheckResult]`` and the module exposes a
:data:`FORMS_CHECKS` registry list. ``CheckResult`` ``name`` and
``standard`` strings match pdfMax verbatim so Phase 6's check
catalogue can map them.

Deferred from this commit:

* ``Form field tooltip language determinable`` (Matterhorn 11-005) —
  already ported to :mod:`auto_a11y.pdf.audit.checks.language`.
* ``Form XObjects with MCIDs not reused`` (Matterhorn 30-002) —
  belongs in the tagging-structure module (Phase 4.2).
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


#: Bit mask for the ``/Ff`` Required field flag (PDF 1.7 §12.7.3.1
#: Table 221, bit 2). Mirrors pdfMax's ``ff & 2`` literal at line 6082.
_REQUIRED_FLAG_MASK: int = 0x2

#: Bit mask for the ``/Ff`` Password flag on text fields (PDF 1.7
#: §12.7.4.3 Table 228, bit 14 = 8192). Mirrors pdfMax's literal at
#: line 6210.
_PASSWORD_FLAG_MASK: int = 0x2000

#: Substrings (case-folded) on a field's ``/T`` or ``/TU`` text that
#: pdfMax treats as a "required" hint. Mirrors the tuple at line 6086
#: verbatim, including the French translations.
_REQUIRED_HINTS: tuple[str, ...] = (
    "required", "mandatory", "must", "obligatoire", "requis",
)

#: Field-type code → human label mapping used in the
#: :func:`check_form_fields_labeled` failure detail. Mirrors pdfMax's
#: dict at line 6059.
_FT_LABELS: dict[str, str] = {
    "Tx": "text",
    "Btn": "button",
    "Ch": "choice",
    "Sig": "signature",
}


# ---------------------------------------------------------------------------
# Helpers shared across the form-iterating checks
# ---------------------------------------------------------------------------


def _iter_page_annot_dicts(
    page_obj: pikepdf.Dictionary,
) -> list[pikepdf.Dictionary]:
    """Yield every dictionary-shaped annotation on a page.

    Annotation arrays may legitimately contain non-dictionary entries
    (e.g. dangling indirect references) — those are skipped rather
    than raising. Mirrors the helper in
    :mod:`auto_a11y.pdf.audit.checks.annotations`.
    """
    annots = pikepdf_helpers.get_array(page_obj, "/Annots")
    if annots is None:
        return []
    out: list[pikepdf.Dictionary] = []
    for ai in range(len(annots)):
        item = annots[ai]
        if isinstance(item, pikepdf.Dictionary):
            out.append(item)
    return out


def _annot_subtype_str(annot: pikepdf.Dictionary) -> str:
    """Render an annotation's ``/Subtype`` as a slash-prefixed string.

    Returns the empty string when ``/Subtype`` is absent or not a Name.
    """
    name = pikepdf_helpers.get_name(annot, "/Subtype")
    return str(name) if name is not None else ""


def _flatten_form_fields(
    raw_fields: pikepdf.Array,
) -> list[pikepdf.Dictionary]:
    """Flatten the AcroForm /Fields tree into terminal field dictionaries.

    Mirrors the helpers in :mod:`auto_a11y.pdf.audit.colors` and
    :mod:`auto_a11y.pdf.audit.checks.language`: a node with ``/Kids``
    and no ``/FT`` is an intermediate (logical group); a node with
    ``/FT`` is terminal. Defensive against entries that are not
    Dictionary-shaped.
    """
    flat: list[pikepdf.Dictionary] = []
    stack: list[pikepdf.Object] = []
    for i in range(len(raw_fields)):
        stack.append(raw_fields[i])
    while stack:
        node = stack.pop(0)
        if not isinstance(node, pikepdf.Dictionary):
            continue
        kids = pikepdf_helpers.get_array(node, "/Kids")
        ft = pikepdf_helpers.get_name(node, "/FT")
        if kids is not None and ft is None:
            for ki in range(len(kids)):
                stack.append(kids[ki])
        else:
            flat.append(node)
    return flat


def _collect_form_fields(pdf: pikepdf.Pdf) -> list[pikepdf.Dictionary]:
    """Return every terminal AcroForm field, or an empty list if none.

    Walks ``Root/AcroForm/Fields``; returns ``[]`` when AcroForm or
    /Fields is missing.
    """
    acroform = pikepdf_helpers.get_dict(pdf.Root, "/AcroForm")
    if acroform is None:
        return []
    raw_fields = pikepdf_helpers.get_array(acroform, "/Fields")
    if raw_fields is None or len(raw_fields) == 0:
        return []
    return _flatten_form_fields(raw_fields)


def _build_annot_struct_map(
    elements: list[StructElement],
) -> dict[int, tuple[int, str]]:
    """Map ``id(annotation)`` to the parent structure element index/tag.

    Mirrors the same-named helper in
    :mod:`auto_a11y.pdf.audit.checks.annotations`. Walks each
    element's ``/K`` array, picks out ``OBJR`` children, and records
    the ``(element_index, resolved_tag)`` of the parent for each
    annotation object referenced.
    """
    annot_to_elem: dict[int, tuple[int, str]] = {}
    for elem in elements:
        try:
            k_obj = elem.obj["/K"]
        except KeyError:
            continue
        items: list[pikepdf.Object]
        if isinstance(k_obj, pikepdf.Array):
            items = [k_obj[i] for i in range(len(k_obj))]
        else:
            items = [k_obj]
        for child in items:
            if not isinstance(child, pikepdf.Dictionary):
                continue
            child_type = pikepdf_helpers.get_name(child, "/Type")
            if child_type is None or str(child_type) != "/OBJR":
                continue
            try:
                obj_ref = child["/Obj"]
            except KeyError:
                continue
            annot_to_elem[id(obj_ref)] = (elem.index, elem.resolved_tag)
    return annot_to_elem


def _field_text(field: pikepdf.Dictionary, key: str) -> str:
    """Return ``field[key]`` as ``str`` (empty when absent / not a String).

    Mirrors pdfMax's ``str(field.get(Name(key), ""))`` idiom: pdfMax
    accepted any value type and stringified it, but the only PDF-legal
    types for ``/T``, ``/TU``, ``/FT`` are String / Name, so we use
    helper-narrowed accessors here. Treats Name values like ``/Tx`` as
    their string form for ``/FT`` look-ups.
    """
    s = pikepdf_helpers.get_string(field, key)
    if s is not None:
        return s
    name = pikepdf_helpers.get_name(field, key)
    if name is not None:
        return str(name)
    return ""


def _field_label(field: pikepdf.Dictionary) -> str:
    """Return a field's accessible label: ``/TU`` else ``/T`` else parent.

    Mirrors pdfMax's redundant-entry-label resolution at lines
    6178-6185: prefer ``/TU``, fall back to ``/T``, walk one parent
    level if both are absent. Returns the lower-cased, stripped form
    so callers can compare directly. Empty string when none found.
    """
    tu = pikepdf_helpers.get_string(field, "/TU")
    t = pikepdf_helpers.get_string(field, "/T")
    if tu is None or t is None:
        parent = pikepdf_helpers.get_dict(field, "/Parent")
        if parent is not None:
            if tu is None:
                tu = pikepdf_helpers.get_string(parent, "/TU")
            if t is None:
                t = pikepdf_helpers.get_string(parent, "/T")
    if tu is not None:
        return tu.strip().lower()
    if t is not None:
        return t.strip().lower()
    return ""


# ---------------------------------------------------------------------------
# check_widget_annotations_inside_form_tags
# ---------------------------------------------------------------------------


def check_widget_annotations_inside_form_tags(
    ctx: AuditContext,
) -> list[CheckResult]:
    """Matterhorn 28-012: every ``/Widget`` annotation lives in a Form tag.

    Reads :attr:`AuditContext.pdf` and :attr:`AuditContext.elements`.
    Builds an annotation→structure map (via OBJR) and reports any
    ``/Widget`` whose parent structure element is not ``Form``, or
    that has no parent at all. Mirrors pdfMax line ~5694 verbatim.
    """
    annot_struct_map = _build_annot_struct_map(ctx.elements)
    widgets_not_in_form: list[tuple[int, str]] = []
    widget_total = 0
    for page_idx, page in enumerate(ctx.pdf.pages):
        page_num = page_idx + 1
        for annot in _iter_page_annot_dicts(page.obj):
            if _annot_subtype_str(annot) != "/Widget":
                continue
            widget_total += 1
            mapping = annot_struct_map.get(id(annot))
            if mapping is None or mapping[1] != "Form":
                elem_ref = f"[{mapping[0]}]" if mapping else ""
                widgets_not_in_form.append((page_num, elem_ref))

    if widget_total == 0:
        return [
            CheckResult(
                name="Widget annotations inside Form tags",
                standard="Matterhorn 28-012",
                result="NA",
                details="No widget annotations in document",
            )
        ]
    if not widgets_not_in_form:
        return [
            CheckResult(
                name="Widget annotations inside Form tags",
                standard="Matterhorn 28-012",
                result="PASS",
                details=(
                    f"All {widget_total} widget annotations are inside"
                    + " Form structure elements"
                ),
            )
        ]
    details = "; ".join(
        f"{ref} p.{pg}" if ref else f"p.{pg}"
        for pg, ref in widgets_not_in_form[:6]
    )
    return [
        CheckResult(
            name="Widget annotations inside Form tags",
            standard="Matterhorn 28-012",
            result="FAIL",
            details=(
                f"{len(widgets_not_in_form)} widget annotation(s) not"
                + f" inside Form structure element: {details}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_no_xfa_forms_present
# ---------------------------------------------------------------------------


def check_no_xfa_forms_present(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 25-001: the document does not embed XFA form data.

    Reads :attr:`AuditContext.pdf`. ``/AcroForm/XFA`` indicates an
    XFA form which is inaccessible to assistive technology and
    prohibited by PDF/UA. Mirrors pdfMax line ~6018 verbatim.

    We only check for the presence of the ``/XFA`` key — its value
    may be an Array (the documented form) or a Stream, but either
    way the form fails. ``acroform.get(Name("/XFA"))`` in pdfMax
    matched any non-None value; we use ``"/XFA" in acroform`` here
    so the helper is unaffected by the value's PDF type.
    """
    acroform = pikepdf_helpers.get_dict(ctx.pdf.Root, "/AcroForm")
    has_xfa = acroform is not None and "/XFA" in acroform
    if has_xfa:
        return [
            CheckResult(
                name="No XFA forms present",
                standard="Matterhorn 25-001",
                result="FAIL",
                details=(
                    "XFA form data found in /AcroForm/XFA — XFA forms"
                    + " are inaccessible to assistive technology and"
                    + " prohibited in PDF/UA"
                ),
            )
        ]
    return [
        CheckResult(
            name="No XFA forms present",
            standard="Matterhorn 25-001",
            result="PASS",
            details=(
                "No XFA form data — document uses standard AcroForm"
                + " fields (if any)"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_form_fields_labeled
# ---------------------------------------------------------------------------


def check_form_fields_labeled(ctx: AuditContext) -> list[CheckResult]:
    """PDF/UA, WCAG 1.3.1, 4.1.2: every form field has a ``/TU`` tooltip.

    Reads :attr:`AuditContext.pdf`. Iterates terminal AcroForm fields
    (flattening intermediate group nodes) and reports any whose
    ``/TU`` is absent or empty. PASSes when there are no fields.
    Mirrors pdfMax line ~6049 verbatim.
    """
    form_fields = _collect_form_fields(ctx.pdf)
    if not form_fields:
        return [
            CheckResult(
                name="Form fields labeled",
                standard="PDF/UA, WCAG 1.3.1, 4.1.2",
                result="NA",
                details="No interactive form fields found in document",
            )
        ]

    fields_missing_tu: list[str] = []
    fields_with_tu = 0
    for field in form_fields:
        tu = pikepdf_helpers.get_string(field, "/TU")
        if tu is not None and tu.strip():
            fields_with_tu += 1
            continue
        t_name = _field_text(field, "/T")
        ft = _field_text(field, "/FT").replace("/", "")
        ft_label = _FT_LABELS.get(ft, ft)
        fields_missing_tu.append(f"'{t_name}' ({ft_label})")

    if not fields_missing_tu:
        return [
            CheckResult(
                name="Form fields labeled",
                standard="PDF/UA, WCAG 1.3.1, 4.1.2",
                result="PASS",
                details=(
                    f"All {fields_with_tu} form fields have accessible"
                    + " names (/TU tooltip)"
                ),
            )
        ]
    suffix = " ..." if len(fields_missing_tu) > 8 else ""
    return [
        CheckResult(
            name="Form fields labeled",
            standard="PDF/UA, WCAG 1.3.1, 4.1.2",
            result="FAIL",
            details=(
                f"{len(fields_missing_tu)} of {len(form_fields)} fields"
                + " missing accessible name (/TU): "
                + "; ".join(fields_missing_tu[:8])
                + suffix
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_required_fields_flagged
# ---------------------------------------------------------------------------


def check_required_fields_flagged(ctx: AuditContext) -> list[CheckResult]:
    """PDF/UA, WCAG 1.3.1, 3.3.2: required fields carry the /Ff bit.

    Reads :attr:`AuditContext.pdf`. For each terminal field, looks at
    ``/T`` and ``/TU`` text for a "required" hint — if found but the
    ``/Ff`` Required bit (bit 2) is *not* set, WARN. PASSes both when
    no fields are required-by-text and when every required-by-text
    field has the flag. Mirrors pdfMax line ~6074 verbatim.
    """
    form_fields = _collect_form_fields(ctx.pdf)
    if not form_fields:
        return [
            CheckResult(
                name="Required fields flagged",
                standard="PDF/UA, WCAG 1.3.1, 3.3.2",
                result="PASS",
                details=(
                    "No required fields detected (none flagged, none"
                    + " hinted in labels)"
                ),
            )
        ]

    required_issues: list[str] = []
    required_ok = 0
    for field in form_fields:
        t_name = _field_text(field, "/T")
        tu = _field_text(field, "/TU")
        ff = pikepdf_helpers.get_int(field, "/Ff") or 0
        is_required_flag = bool(ff & _REQUIRED_FLAG_MASK)
        hint_text = (tu + " " + t_name).lower()
        hints_required = any(w in hint_text for w in _REQUIRED_HINTS)
        if is_required_flag:
            required_ok += 1
        elif hints_required:
            required_issues.append(
                f"'{t_name}' hints required in text but /Ff Required"
                + " bit not set"
            )

    if required_issues:
        return [
            CheckResult(
                name="Required fields flagged",
                standard="PDF/UA, WCAG 1.3.1, 3.3.2",
                result="WARN",
                details=(
                    f"{len(required_issues)} field(s) appear required but"
                    + " lack semantic flag: "
                    + "; ".join(required_issues[:5])
                ),
            )
        ]
    detail = (
        f"{required_ok} field(s) have Required flag set"
        if required_ok
        else (
            "No required fields detected (none flagged, none hinted in"
            + " labels)"
        )
    )
    return [
        CheckResult(
            name="Required fields flagged",
            standard="PDF/UA, WCAG 1.3.1, 3.3.2",
            result="PASS",
            details=detail,
        )
    ]


# ---------------------------------------------------------------------------
# check_form_fields_tagged_in_structure
# ---------------------------------------------------------------------------


def check_form_fields_tagged_in_structure(
    ctx: AuditContext,
) -> list[CheckResult]:
    """PDF/UA, WCAG 1.3.1: there are at least as many Form tags as fields.

    Reads :attr:`AuditContext.pdf` and :attr:`AuditContext.elements`.
    Counts elements with resolved tag ``Form`` and compares against
    the count of terminal AcroForm fields. PASS when ≥, WARN when
    some Form tags are present but not enough, FAIL when zero Form
    tags exist alongside fields. Mirrors pdfMax line ~6103 verbatim.

    A document with no form fields reports ``NA``. pdfMax returns
    nothing at all here, which leaves the check missing from the report
    rather than answered — and a reader cannot tell a check that found
    nothing to examine from one that never ran.
    """
    form_fields = _collect_form_fields(ctx.pdf)
    if not form_fields:
        return [
            CheckResult(
                name="Form fields tagged in structure",
                standard="PDF/UA, WCAG 1.3.1",
                result="NA",
                details="No form fields in document",
            )
        ]

    form_tags_in_tree = sum(
        1 for e in ctx.elements if e.resolved_tag == "Form"
    )
    if form_tags_in_tree >= len(form_fields):
        return [
            CheckResult(
                name="Form fields tagged in structure",
                standard="PDF/UA, WCAG 1.3.1",
                result="PASS",
                details=(
                    f"{form_tags_in_tree} Form tags found for"
                    + f" {len(form_fields)} form fields"
                ),
            )
        ]
    if form_tags_in_tree > 0:
        return [
            CheckResult(
                name="Form fields tagged in structure",
                standard="PDF/UA, WCAG 1.3.1",
                result="WARN",
                details=(
                    f"Only {form_tags_in_tree} Form tags for"
                    + f" {len(form_fields)} form fields -- some fields"
                    + " may not be reachable via structure tree"
                ),
            )
        ]
    return [
        CheckResult(
            name="Form fields tagged in structure",
            standard="PDF/UA, WCAG 1.3.1",
            result="FAIL",
            details=(
                f"No Form tags in structure tree for {len(form_fields)}"
                + " form fields -- screen readers may not find"
                + " interactive fields"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_form_page_tab_order
# ---------------------------------------------------------------------------


def check_form_page_tab_order(ctx: AuditContext) -> list[CheckResult]:
    """PDF/UA, WCAG 2.1.1, 2.4.3: Widget pages declare ``/Tabs = /S``.

    Reads :attr:`AuditContext.pdf`. Pages with at least one ``/Widget``
    annotation must carry ``/Tabs`` set to the Name ``/S`` so keyboard
    focus order follows the structure tree. Mirrors pdfMax line ~6120
    verbatim.

    A document with no Widget annotations reports ``NA``, for the same
    reason :func:`check_form_fields_tagged_in_structure` does: silence
    is indistinguishable from the check not having run.
    """
    pages_with_fields: set[int] = set()
    for page_idx, page in enumerate(ctx.pdf.pages):
        for annot in _iter_page_annot_dicts(page.obj):
            if _annot_subtype_str(annot) == "/Widget":
                pages_with_fields.add(page_idx)
                break

    if not pages_with_fields:
        return [
            CheckResult(
                name="Form page tab order",
                standard="PDF/UA, WCAG 2.1.1, 2.4.3",
                result="NA",
                details="No pages carry Widget annotations",
            )
        ]

    tab_issues: list[str] = []
    for pg in sorted(pages_with_fields):
        tabs = pikepdf_helpers.get_name(ctx.pdf.pages[pg].obj, "/Tabs")
        if tabs is None or str(tabs) != "/S":
            tab_str = (
                f"= {tabs}" if tabs is not None else "not set"
            )
            tab_issues.append(f"Page {pg + 1}: /Tabs {tab_str}")
    if not tab_issues:
        return [
            CheckResult(
                name="Form page tab order",
                standard="PDF/UA, WCAG 2.1.1, 2.4.3",
                result="PASS",
                details=(
                    f"All {len(pages_with_fields)} page(s) with form"
                    + " fields have /Tabs = /S"
                ),
            )
        ]
    return [
        CheckResult(
            name="Form page tab order",
            standard="PDF/UA, WCAG 2.1.1, 2.4.3",
            result="FAIL",
            details=(
                "Tab order not set to structure on pages with form"
                + f" fields: {'; '.join(tab_issues)}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_form_field_names_unique
# ---------------------------------------------------------------------------


def check_form_field_names_unique(ctx: AuditContext) -> list[CheckResult]:
    """WCAG 4.1.2: every terminal field has a unique ``/T`` name.

    Reads :attr:`AuditContext.pdf`. Duplicate ``/T`` values can cause
    data-entry collisions because a Submit Form action serialises by
    name. Mirrors pdfMax line ~6145 verbatim.

    A document with no form fields reports ``NA``, for the same reason
    :func:`check_form_fields_tagged_in_structure` does.
    """
    form_fields = _collect_form_fields(ctx.pdf)
    if not form_fields:
        return [
            CheckResult(
                name="Form field names unique",
                standard="WCAG 4.1.2",
                result="NA",
                details="No form fields in document",
            )
        ]

    field_names: list[str] = []
    for field in form_fields:
        t = pikepdf_helpers.get_string(field, "/T")
        if t is not None:
            field_names.append(t)
    name_counts: dict[str, int] = {}
    for n in field_names:
        name_counts[n] = name_counts.get(n, 0) + 1
    dupes = {n: c for n, c in name_counts.items() if c > 1}
    if dupes:
        dupe_strs = [f"'{n}' (x{c})" for n, c in dupes.items()]
        return [
            CheckResult(
                name="Form field names unique",
                standard="WCAG 4.1.2",
                result="WARN",
                details=(
                    "Duplicate field names may cause data loss: "
                    + "; ".join(dupe_strs[:5])
                ),
            )
        ]
    return [
        CheckResult(
            name="Form field names unique",
            standard="WCAG 4.1.2",
            result="PASS",
            details=f"All {len(field_names)} field names are unique",
        )
    ]


# ---------------------------------------------------------------------------
# check_redundant_entry_in_forms
# ---------------------------------------------------------------------------


def check_redundant_entry_in_forms(ctx: AuditContext) -> list[CheckResult]:
    """WCAG 3.3.7: avoid asking for the same information twice.

    Reads :attr:`AuditContext.pdf`. For each ``/Widget`` annotation,
    resolves an accessible label (prefer ``/TU``, else ``/T``, else
    parent ``/TU`` / ``/T``) and groups labels by page. Labels seen
    on more than one page are reported. PASSes (with the no-fields
    detail) when there are no form fields at all. Mirrors pdfMax
    line ~6165 verbatim.
    """
    form_fields = _collect_form_fields(ctx.pdf)
    if not form_fields:
        return [
            CheckResult(
                name="Redundant entry in forms",
                standard="WCAG 3.3.7",
                result="NA",
                details="No interactive form fields found in document",
            )
        ]

    field_pages: dict[str, set[int]] = {}
    for pg_num, pg in enumerate(ctx.pdf.pages):
        for annot in _iter_page_annot_dicts(pg.obj):
            if _annot_subtype_str(annot) != "/Widget":
                continue
            label = _field_label(annot)
            if label:
                field_pages.setdefault(label, set()).add(pg_num)

    repeated = {k: v for k, v in field_pages.items() if len(v) > 1}
    if not repeated:
        return [
            CheckResult(
                name="Redundant entry in forms",
                standard="WCAG 3.3.7",
                result="PASS",
                details=(
                    "No form fields request the same information across"
                    + " different pages"
                ),
            )
        ]
    issues = [
        f"'{k}' on pages {', '.join(str(p + 1) for p in sorted(v))}"
        for k, v in list(repeated.items())[:5]
    ]
    return [
        CheckResult(
            name="Redundant entry in forms",
            standard="WCAG 3.3.7",
            result="WARN",
            details=(
                f"{len(repeated)} field(s) appear on multiple pages — "
                + "ensure pre-fill or selection is available: "
                + "; ".join(issues)
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_accessible_authentication
# ---------------------------------------------------------------------------


def check_accessible_authentication(ctx: AuditContext) -> list[CheckResult]:
    """WCAG 3.3.8: password fields are flagged for paste/autofill review.

    Reads :attr:`AuditContext.pdf`. Text fields (``/FT == /Tx``) with
    the ``/Ff`` Password flag (bit 14, 0x2000) are reported so authors
    confirm paste isn't blocked and password-managers can autofill.
    Mirrors pdfMax line ~6202 verbatim.
    """
    form_fields = _collect_form_fields(ctx.pdf)
    if not form_fields:
        return [
            CheckResult(
                name="Accessible authentication",
                standard="WCAG 3.3.8",
                result="NA",
                details="No interactive form fields found in document",
            )
        ]

    password_fields: list[str] = []
    for field in form_fields:
        ft_name = pikepdf_helpers.get_name(field, "/FT")
        if ft_name is None or str(ft_name) != "/Tx":
            continue
        ff = pikepdf_helpers.get_int(field, "/Ff") or 0
        if ff & _PASSWORD_FLAG_MASK:
            t_name = pikepdf_helpers.get_string(field, "/T") or "unnamed"
            password_fields.append(t_name)

    if password_fields:
        return [
            CheckResult(
                name="Accessible authentication",
                standard="WCAG 3.3.8",
                result="WARN",
                details=(
                    f"{len(password_fields)} password field(s) found"
                    + f" ({', '.join(password_fields[:5])}). Ensure"
                    + " paste is not blocked and password managers can"
                    + " auto-fill these fields."
                ),
            )
        ]
    return [
        CheckResult(
            name="Accessible authentication",
            standard="WCAG 3.3.8",
            result="PASS",
            details="No password fields requiring cognitive function test",
        )
    ]


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------


#: Phase 5.3's pipeline iterates this list in order. Phase 6's check
#: catalogue iterates the same list to enumerate every check name.
def check_required_fields_visually_indicated(
    ctx: AuditContext,
) -> list[CheckResult]:
    """WCAG 3.3.2, 1.3.1: a required field looks required.

    Mirrors pdfMax line ~11355. :func:`check_required_fields_flagged`
    asks the other half of the same question — whether a field a form
    treats as mandatory carries ``/Ff`` so assistive technology knows.
    This one asks whether anyone looking at the page can tell.

    The deterministic evidence is thin by construction: only the field's
    own name and tooltip, plus any legend in the document text. An
    asterisk drawn beside the field as page content satisfies the
    criterion and is invisible here, so a missing indicator WARNs for
    manual review rather than failing. An AI run supersedes this verdict
    with one made from the rendered page.
    """
    name = "Required fields visually indicated"
    standard = "WCAG 3.3.2, 1.3.1"
    data = ctx.required_fields
    if data is None:
        return [CheckResult(
            name=name, standard=standard, result="NA",
            details="No fields carry the /Ff Required flag",
        )]

    total = len(data.fields)
    missing = data.missing_indicator
    if missing:
        names = ", ".join(f.name or "unnamed" for f in missing[:5])
        return [CheckResult(
            name=name, standard=standard, result="WARN",
            details=(
                f"{len(missing)} of {total} required field(s) have no '*' or"
                f" 'required' in their accessible name/tooltip: {names}."
                " Manual review needed to verify visual indicators exist"
            ),
        )]
    return [CheckResult(
        name=name, standard=standard, result="PASS",
        details=(
            f"All {total} required field(s) have '*' or 'required' in their"
            + " accessible name/tooltip"
            + (
                f'. Legend found: "{data.legend_text}"'
                if data.has_legend else ""
            )
        ),
    )]


FORMS_CHECKS: list[Callable[[AuditContext], list[CheckResult]]] = [
    check_widget_annotations_inside_form_tags,
    check_no_xfa_forms_present,
    check_form_fields_labeled,
    check_required_fields_flagged,
    check_required_fields_visually_indicated,
    check_form_fields_tagged_in_structure,
    check_form_page_tab_order,
    check_form_field_names_unique,
    check_redundant_entry_in_forms,
    check_accessible_authentication,
]


__all__ = [
    "FORMS_CHECKS",
    "check_accessible_authentication",
    "check_form_field_names_unique",
    "check_form_fields_labeled",
    "check_form_fields_tagged_in_structure",
    "check_form_page_tab_order",
    "check_no_xfa_forms_present",
    "check_redundant_entry_in_forms",
    "check_required_fields_flagged",
    "check_required_fields_visually_indicated",
    "check_widget_annotations_inside_form_tags",
]
