"""Fixes for interactive form fields and tab order.

A form is the part of a PDF a person has to operate rather than read, so
the failures here are the ones that stop someone completing a task: a
field a screen reader announces only as "text field", a tab sequence that
jumps around the page, two fields sharing a name so their values collide.
"""
from __future__ import annotations

import re

import pikepdf
from pikepdf import Name, String

from auto_a11y.pdf.fix._pdf_objects import (
    entry_int,
    entry_text,
    flatten_form_fields,
    items,
)
from auto_a11y.pdf.fix.models import FixOptions, FixResult

# Words that mark a field as required in the languages this tool serves.
# Matched against both the field name and its tooltip.
_REQUIRED_WORDS = re.compile(
    r"\b(required|mandatory|obligatoire|requis)\b", re.IGNORECASE
)

# Bit 2 of /Ff is the Required flag (PDF 32000-1 table 221).
_FF_REQUIRED = 2

_NO_FORM = "No form fields in document"


def _tooltip_from_field_name(name: str) -> str:
    """Turn a field name into something worth reading aloud.

    Field names are written for the form's data model, not for a person:
    ``client_address_1`` rather than "Client address". This makes the
    obvious substitutions and drops a trailing index, which is a position
    in a repeating group rather than part of the label.
    """
    cleaned = name.replace("_", " ").replace(".", " ")
    cleaned = re.sub(r"\s+\d+$", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def fix_form_labels(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Give unlabelled fields a ``/TU`` tooltip derived from ``/T``.

    ``/TU`` is what a screen reader announces for a field. Without it the
    user hears only the field type, so a form becomes a sequence of
    identical "edit text" prompts.

    A name-derived tooltip is a poor label — it is the data model's word,
    not the author's — but it carries more meaning than nothing, and the
    result says where it came from so a reviewer knows to improve it.
    """
    fields = flatten_form_fields(pdf)
    if not fields:
        return FixResult("fix_form_labels", True, _NO_FORM)

    labelled = 0
    for field in fields:
        if entry_text(field, "/TU"):
            continue
        tooltip = _tooltip_from_field_name(entry_text(field, "/T"))
        if not tooltip:
            continue
        field[Name("/TU")] = String(tooltip)
        labelled += 1

    if labelled:
        return FixResult(
            "fix_form_labels", True,
            f"Added a /TU tooltip to {labelled} form field(s), derived from"
            + " the field name — review them for wording",
        )
    return FixResult(
        "fix_form_labels", True, "All form fields already have /TU tooltips",
    )


def _page_has_widget(page: pikepdf.Page) -> bool:
    """True when the page carries at least one form widget annotation."""
    for annot in items(page.obj.get(Name("/Annots"))):
        subtype = annot.get(Name("/Subtype"))
        if subtype is not None and str(subtype) == "/Widget":
            return True
    return False


def _set_tabs_structural(page: pikepdf.Page) -> bool:
    """Set ``/Tabs /S`` on a page. Returns True if it changed anything."""
    tabs = page.obj.get(Name("/Tabs"))
    if tabs is not None and str(tabs) == "/S":
        return False
    page.obj[Name("/Tabs")] = Name("/S")
    return True


def fix_form_tab_order(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Set ``/Tabs /S`` on pages that contain form widgets.

    Without it the tab sequence follows the order annotations happen to
    sit in the page's array, which is creation order — so tabbing through
    a form can jump from the top of the page to the bottom and back.
    ``/S`` ties it to the structure tree instead, which is the order the
    document is read in.
    """
    changed = [
        number
        for number, page in enumerate(pdf.pages, start=1)
        if _page_has_widget(page) and _set_tabs_structural(page)
    ]
    if changed:
        joined = ", ".join(str(n) for n in changed)
        return FixResult(
            "fix_form_tab_order", True, f"Set /Tabs = /S on page(s): {joined}",
        )
    return FixResult(
        "fix_form_tab_order", True, "All form pages already have /Tabs = /S",
    )


def fix_tab_order(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Set ``/Tabs /S`` on every page, not only those carrying widgets.

    Links and other annotations are tabbed to as well, so the ordering
    matters on pages with no form fields at all.
    """
    changed = sum(1 for page in pdf.pages if _set_tabs_structural(page))
    if changed:
        return FixResult(
            "fix_tab_order", True, f"Set /Tabs = /S on {changed} page(s)",
        )
    return FixResult(
        "fix_tab_order", True, "All pages already have /Tabs = /S",
    )


def fix_required_fields(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Flag fields as required where the name or tooltip says they are.

    A form that shows "Name (required)" visually but never sets the
    Required bit tells a screen reader user nothing, and they discover
    the requirement only when submission fails.

    Only acts on wording that is explicit in one of the two languages
    this tool serves; it does not infer requiredness from asterisks or
    styling, which are too easily coincidental.
    """
    fields = flatten_form_fields(pdf)
    if not fields:
        return FixResult("fix_required_fields", True, _NO_FORM)

    flagged = 0
    for field in fields:
        flags = entry_int(field, "/Ff")
        if flags & _FF_REQUIRED:
            continue
        if _REQUIRED_WORDS.search(entry_text(field, "/T")) or _REQUIRED_WORDS.search(
            entry_text(field, "/TU")
        ):
            field[Name("/Ff")] = flags | _FF_REQUIRED
            flagged += 1

    if flagged:
        return FixResult(
            "fix_required_fields", True,
            f"Set the Required flag on {flagged} form field(s)",
        )
    return FixResult(
        "fix_required_fields", True, "No fields needed a Required flag update",
    )


def fix_field_names_unique(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Rename duplicate field names so each field is addressable.

    Two terminal fields sharing a ``/T`` are treated by viewers as one
    field with two appearances: typing in one fills the other. That is
    occasionally intended, but where it is not, the second field can
    never hold its own value.

    A tooltip that merely repeated the old name is renamed with it —
    leaving it behind would label two different fields identically, which
    is the same confusion one layer up.
    """
    fields = flatten_form_fields(pdf)
    if not fields:
        return FixResult("fix_field_names_unique", True, _NO_FORM)

    taken: set[str] = set()
    renamed = 0
    for field in fields:
        name = entry_text(field, "/T")
        if not name:
            continue
        if name not in taken:
            taken.add(name)
            continue

        suffix = 2
        candidate = f"{name}_{suffix}"
        while candidate in taken:
            suffix += 1
            candidate = f"{name}_{suffix}"
        taken.add(candidate)

        if entry_text(field, "/TU") == name:
            field[Name("/TU")] = String(candidate)
        field[Name("/T")] = String(candidate)
        renamed += 1

    if renamed:
        return FixResult(
            "fix_field_names_unique", True,
            f"Renamed {renamed} duplicate field name(s)",
        )
    return FixResult(
        "fix_field_names_unique", True,
        "All form field names are already unique",
    )


def fix_form_field_lang(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Give form-field tooltips a determinable language.

    Only needed when the document declares no ``/Lang`` of its own; with
    one set, every field inherits it and this is a no-op.
    """
    if entry_text(pdf.Root, "/Lang"):
        return FixResult(
            "fix_form_field_lang", True,
            "Document /Lang already set — form fields inherit it",
        )

    lang = (opts.lang or "").strip()
    if not lang:
        return FixResult(
            "fix_form_field_lang", False,
            "The document declares no /Lang and no language was supplied,"
            + " so field tooltips have no determinable language. Apply"
            + " fix_language first, or supply one.",
        )

    fields = flatten_form_fields(pdf)
    if not fields:
        return FixResult("fix_form_field_lang", True, _NO_FORM)

    tagged = 0
    for field in fields:
        if field.get(Name("/TU")) is None:
            continue
        if field.get(Name("/Lang")) is None:
            field[Name("/Lang")] = String(lang)
            tagged += 1

    return FixResult(
        "fix_form_field_lang", True,
        f'Set /Lang="{lang}" on {tagged} form field(s)',
    )
