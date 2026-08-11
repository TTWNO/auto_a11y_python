"""Which check offers which fix, and what the fix needs from the user.

This is the table the report is built against: a failing check shows a
"Fix this" control when it appears here, and the control collects
whatever :attr:`FixableCheck.inputs` describes before the fix runs.

Two things are deliberate about the shape.

Labels are Fluent message IDs rather than English strings. pdfMax's
equivalent hard-codes the wording, which is fine for an English-only
tool; here every label a user reads has to exist in both languages, and
an ID makes the missing half a test failure rather than an English string
appearing in a French UI.

Input kinds are named rather than free-form. A fix either needs nothing,
a fixed set of fields, or one field per flagged element — and the last of
those needs the report to supply the element list, so the UI has to be
able to tell them apart without special-casing individual fixes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

#: How the UI should collect a fix's input.
#:
#: * ``fields`` — the fixed list in :attr:`FixableCheck.fields`.
#: * ``metadata`` — the document's title, author, creator and producer.
#: * ``per-element`` — one text box per element the check flagged, keyed
#:   by the element reference the report printed.
#: * ``headings`` — a level picker per flagged heading.
#: * ``table-cells`` — a header/data picker per flagged cell.
#: * ``list-candidates`` — runs of paragraphs the check believes are
#:   lists, for the user to confirm.
InputKind: TypeAlias = Literal[
    "none", "fields", "metadata", "per-element", "headings",
    "table-cells", "list-candidates",
]


@dataclass(frozen=True)
class FixFieldOption:
    """One choice in a select field."""

    value: str
    label_id: str


@dataclass(frozen=True)
class FixField:
    """A single input a fix needs.

    ``show_when`` makes a field conditional on another field's value —
    the custom-language box only appears once "Other" is chosen.
    """

    key: str
    label_id: str
    kind: Literal["text", "select"]
    placeholder_id: str | None = None
    help_id: str | None = None
    options: tuple[FixFieldOption, ...] = ()
    show_when: tuple[str, str] | None = None


@dataclass(frozen=True)
class FixableCheck:
    """The fix a failing check offers, and what it needs first."""

    fix_id: str
    inputs: InputKind = "none"
    fields: tuple[FixField, ...] = ()
    #: Applied when the check reported a warning rather than a failure,
    #: where the lesser fault has a lesser remedy.
    warn_fix_id: str | None = None


_LANGUAGE_OPTIONS = (
    FixFieldOption("en", "pdf-fix-language-english"),
    FixFieldOption("fr", "pdf-fix-language-french"),
    FixFieldOption("es", "pdf-fix-language-spanish"),
    FixFieldOption("other", "pdf-fix-language-other"),
)

_LABEL_STYLE_OPTIONS = (
    FixFieldOption("none", "pdf-fix-label-style-structural"),
    FixFieldOption("bullet", "pdf-fix-label-style-bullet"),
    FixFieldOption("numbered", "pdf-fix-label-style-numbered"),
)


#: Check name → the fix it offers. Names are the audit engine's own,
#: matching :data:`~auto_a11y.pdf.translation.check_mapper.CHECK_CATALOGUE`.
FIXABLE_CHECKS: dict[str, FixableCheck] = {
    # Document properties
    "Document title set": FixableCheck(
        fix_id="fix_title",
        warn_fix_id="fix_display_doc_title",
        inputs="fields",
        fields=(
            FixField(
                key="title",
                label_id="pdf-fix-field-title",
                kind="text",
                placeholder_id="pdf-fix-field-title-placeholder",
                help_id="pdf-fix-field-title-help",
            ),
        ),
    ),
    "Document language set": FixableCheck(
        fix_id="fix_language",
        inputs="fields",
        fields=(
            FixField(
                key="lang",
                label_id="pdf-fix-field-language",
                kind="select",
                options=_LANGUAGE_OPTIONS,
                help_id="pdf-fix-field-language-help",
            ),
            FixField(
                key="lang_custom",
                label_id="pdf-fix-field-language-custom",
                kind="text",
                placeholder_id="pdf-fix-field-language-custom-placeholder",
                show_when=("lang", "other"),
            ),
        ),
    ),
    "PDF is tagged": FixableCheck(fix_id="fix_mark_info"),
    "Metadata completeness": FixableCheck(
        fix_id="fix_metadata", inputs="metadata",
    ),
    "Accessibility permission not restricted": FixableCheck(
        fix_id="fix_accessibility_permission",
    ),
    "Page labels consistent": FixableCheck(fix_id="fix_page_labels"),

    # Metadata and conformance
    "XMP metadata stream present": FixableCheck(fix_id="fix_xmp_metadata"),
    "XMP dc:title present": FixableCheck(fix_id="fix_xmp_title"),
    "PDF/UA identifier": FixableCheck(fix_id="fix_pdfua_identifier"),
    "No suspect tags": FixableCheck(fix_id="fix_suspects"),
    "Document metadata language determinable": FixableCheck(
        fix_id="fix_metadata_lang",
    ),
    "PDF/UA-2 accessibility declarations in XMP": FixableCheck(
        fix_id="fix_pdfua2_xmp",
    ),
    "PDF 2.0 namespace in structure tree": FixableCheck(
        fix_id="fix_pdf20_namespace",
    ),
    "PDF/UA-2 requires PDF 2.0": FixableCheck(fix_id="fix_pdf_version_20"),

    # Alternative text
    "Alt text on all Figure/Art tags": FixableCheck(
        fix_id="fix_alt_text", inputs="per-element",
    ),
    "Formula elements have alt text or ActualText": FixableCheck(
        fix_id="fix_formula_alt", inputs="per-element",
    ),

    # Headings
    "Heading hierarchy valid": FixableCheck(
        fix_id="fix_heading_levels", inputs="headings",
    ),
    "No multiple headings per node": FixableCheck(
        fix_id="fix_heading_containers",
    ),
    "No mixed heading tag types": FixableCheck(fix_id="fix_generic_headings"),

    # Tables
    "Table headers defined": FixableCheck(
        fix_id="fix_table_headers", inputs="table-cells",
    ),
    "Table header scope defined": FixableCheck(fix_id="fix_table_scope"),
    "Table structure sections": FixableCheck(fix_id="fix_table_sections"),
    "No empty tables": FixableCheck(fix_id="fix_empty_tables"),
    "Table captions": FixableCheck(
        fix_id="fix_table_captions", inputs="per-element",
    ),

    # Lists
    "List structure valid": FixableCheck(fix_id="fix_list_structure"),
    "List nesting valid": FixableCheck(fix_id="fix_list_nesting"),
    "No empty lists": FixableCheck(fix_id="fix_empty_lists"),
    "Untagged lists detected": FixableCheck(
        fix_id="fix_paragraphs_to_list", inputs="list-candidates",
    ),
    "List item labels": FixableCheck(
        fix_id="fix_list_labels",
        inputs="fields",
        fields=(
            FixField(
                key="list_label_style",
                label_id="pdf-fix-field-label-style",
                kind="select",
                options=_LABEL_STYLE_OPTIONS,
                help_id="pdf-fix-field-label-style-help",
            ),
        ),
    ),

    # Forms
    "Form fields labeled": FixableCheck(fix_id="fix_form_labels"),
    "Form field names unique": FixableCheck(fix_id="fix_field_names_unique"),
    "Required fields flagged": FixableCheck(fix_id="fix_required_fields"),
    "Form page tab order": FixableCheck(fix_id="fix_form_tab_order"),
    "Tab order follows structure": FixableCheck(fix_id="fix_tab_order"),
    "Annotation tab order on all annotated pages": FixableCheck(
        fix_id="fix_tab_order",
    ),
    "Widget annotations inside Form tags": FixableCheck(
        fix_id="fix_widget_form_tags",
    ),
    "Form fields tagged in structure": FixableCheck(
        fix_id="fix_widget_form_tags",
    ),
    "Form field tooltip language determinable": FixableCheck(
        fix_id="fix_form_field_lang",
    ),
    "No XFA forms present": FixableCheck(fix_id="fix_remove_xfa"),

    # Annotations and links
    "Link annotations inside Link tags": FixableCheck(
        fix_id="fix_link_annotations",
    ),
    "Link annotations have content": FixableCheck(
        fix_id="fix_link_content", inputs="per-element",
    ),
    "Link annotations have Contents key": FixableCheck(
        fix_id="fix_link_annot_contents",
    ),
    "Non-link/widget annotations tagged": FixableCheck(
        fix_id="fix_annot_tagged",
    ),
    "Visible annotations have alt descriptions": FixableCheck(
        fix_id="fix_annot_descriptions", inputs="per-element",
    ),
    "Annotation contents language determinable": FixableCheck(
        fix_id="fix_annot_contents_lang",
    ),
    "No TrapNet annotations": FixableCheck(fix_id="fix_trapnet"),

    # Structure
    "Bookmarks present": FixableCheck(fix_id="fix_bookmarks"),
    "TOC structure valid": FixableCheck(fix_id="fix_toc_structure"),
    "Note tags have unique IDs": FixableCheck(fix_id="fix_note_ids"),
    "No empty tags": FixableCheck(fix_id="fix_empty_tags"),
    "Correct nesting": FixableCheck(fix_id="fix_correct_nesting"),
    "No Reference XObjects": FixableCheck(fix_id="fix_ref_xobjects"),
    "Optional content groups have Name": FixableCheck(fix_id="fix_ocg_names"),
    "Optional content has no AS entry": FixableCheck(fix_id="fix_ocg_as"),
    "Embedded files have F and UF keys": FixableCheck(
        fix_id="fix_embedded_files",
    ),

    # Role mapping and language
    "Role mapping valid": FixableCheck(fix_id="fix_role_mapping"),
    "No circular role mappings": FixableCheck(fix_id="fix_circular_roles"),
    "Standard tags not remapped": FixableCheck(fix_id="fix_standard_remap"),
    "Lang values are valid BCP 47": FixableCheck(fix_id="fix_lang_bcp47"),
}


#: Checks that offer a fix but do not exist in the audit engine yet.
#:
#: Their fixes are ported and working; what is missing is the check that
#: would surface them, which lands with the outstanding audit checks.
#: Listed explicitly so the completeness test can tell "not implemented
#: yet" from "typo in a check name".
PENDING_CHECKS: frozenset[str] = frozenset({
    "Accessibility permission not restricted",
    "Untagged lists detected",
})


#: Fixes no check offers, and why.
#:
#: ``fix_outline_lang`` sets a language on bookmark entries. Nothing in
#: the audit engine — or in pdfMax's — checks for it, so no report row
#: can offer it. It stays in the registry because a caller applying a
#: language pass wants it alongside fix_language and fix_metadata_lang;
#: it is simply not reachable from a finding.
UNOFFERED_FIXES: frozenset[str] = frozenset({"fix_outline_lang"})


def fix_for_check(name: str, result: str) -> FixableCheck | None:
    """The fix a check offers for a given verdict, if any.

    ``result`` picks between the failure remedy and the lesser one where
    a check defines both — a document with a title but no
    ``/DisplayDocTitle`` warns rather than fails, and needs only the
    viewer preference set.
    """
    entry = FIXABLE_CHECKS.get(name)
    if entry is None:
        return None
    if result == "WARN" and entry.warn_fix_id is not None:
        return FixableCheck(
            fix_id=entry.warn_fix_id,
            inputs=entry.inputs,
            fields=entry.fields,
        )
    if result in ("FAIL", "WARN"):
        return entry
    return None
