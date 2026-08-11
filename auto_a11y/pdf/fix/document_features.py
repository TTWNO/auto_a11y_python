"""Fixes for document-level features: layers, page labels, attachments, XFA.

These sit outside the structure tree, on features a document either uses
or does not. What they have in common is that each one, misconfigured,
produces content a reader cannot name or reach: a layer with no name, a
page whose number the viewer cannot show, an attachment whose filename is
undecodable, a form whose fields exist only in a technology no current
reader supports.
"""
from __future__ import annotations

import pikepdf
from pikepdf import Array, Dictionary, Name, String

from auto_a11y.pdf.fix._pdf_objects import items
from auto_a11y.pdf.fix.models import FixOptions, FixResult


def fix_ocg_names(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Give every optional content group a ``/Name``.

    Optional content groups are the document's layers, and the ``/Name``
    is what the viewer shows in its layers panel. An unnamed one appears
    as a blank row: a reader can toggle it but cannot know what they are
    turning off.

    Names are positional (``Layer 3`` is the third group) rather than
    sequential over the unnamed ones, so the label matches the row's
    place in the panel.
    """
    ocprops = pdf.Root.get(Name("/OCProperties"))
    if ocprops is None:
        return FixResult("fix_ocg_names", True, "No optional content in document")

    groups = items(ocprops.get(Name("/OCGs")))
    if not groups:
        return FixResult("fix_ocg_names", True, "No optional content groups defined")

    named = 0
    for position, group in enumerate(groups, start=1):
        existing = group.get(Name("/Name"))
        if existing is not None and str(existing).strip():
            continue
        group[Name("/Name")] = String(f"Layer {position}")
        named += 1

    if named:
        return FixResult(
            "fix_ocg_names", True, f"Named {named} unnamed layer(s)",
        )
    return FixResult("fix_ocg_names", True, "All layers already have names")


def fix_ocg_as(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Remove ``/AS`` from the default optional-content configuration.

    ``/AS`` makes layers switch themselves on and off based on how the
    document is being used — printed rather than viewed, or zoomed past a
    threshold. Content that appears and disappears on its own cannot be
    relied on by anyone, and a screen reader has no way to report that it
    happened.
    """
    ocprops = pdf.Root.get(Name("/OCProperties"))
    if ocprops is None:
        return FixResult("fix_ocg_as", True, "No optional content in document")

    default_config = ocprops.get(Name("/D"))
    if default_config is None:
        return FixResult(
            "fix_ocg_as", True, "No default configuration — nothing to fix",
        )
    if Name("/AS") not in default_config:
        return FixResult("fix_ocg_as", True, "No /AS entry — no action needed")

    del default_config[Name("/AS")]
    return FixResult(
        "fix_ocg_as", True,
        "Removed /AS from the default configuration — layers no longer"
        + " switch themselves by usage",
    )


def fix_page_labels(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Make ``/PageLabels`` cover the document from its first page.

    Page labels are what the viewer shows in its page box — "iv", "12",
    "A-3" — and what a reader quotes when telling someone where to look.
    A label range that starts partway through leaves the earlier pages
    with no label at all, so the number on the page and the number in the
    viewer disagree.

    Only ever prepends a range for the uncovered pages; existing ranges
    keep their numbering styles.
    """
    page_labels = pdf.Root.get(Name("/PageLabels"))
    if page_labels is None:
        return FixResult("fix_page_labels", True, "No page labels in document")

    decimal_from_start: list[object] = [0, Dictionary(S=Name("/D"))]

    entries = items(page_labels.get(Name("/Nums")))
    if len(entries) < 2:
        page_labels[Name("/Nums")] = Array(decimal_from_start)
        return FixResult(
            "fix_page_labels", True,
            "Replaced an empty or malformed label range with decimal"
            + " numbering from the first page",
        )

    try:
        first_index = int(entries[0])
    except (TypeError, ValueError):
        page_labels[Name("/Nums")] = Array(decimal_from_start)
        return FixResult(
            "fix_page_labels", True,
            "Replaced a label range with a non-numeric start with decimal"
            + " numbering from the first page",
        )

    if first_index == 0:
        return FixResult(
            "fix_page_labels", True, "Page labels already cover the first page",
        )

    page_labels[Name("/Nums")] = Array([*decimal_from_start, *entries])
    return FixResult(
        "fix_page_labels", True,
        f"Added decimal numbering for the first {first_index} page(s), which"
        + " had no label range",
    )


def _repair_file_specs(node: pikepdf.Object) -> int:
    """Give each file spec in a name tree both ``/F`` and ``/UF``."""
    repaired = 0
    entries = items(node.get(Name("/Names")))
    for position in range(0, len(entries) - 1, 2):
        key, spec = entries[position], entries[position + 1]
        if not isinstance(spec, pikepdf.Dictionary):
            continue

        ascii_name = spec.get(Name("/F"))
        unicode_name = spec.get(Name("/UF"))
        ascii_text = str(ascii_name).strip() if ascii_name is not None else ""
        unicode_text = str(unicode_name).strip() if unicode_name is not None else ""

        if ascii_text and unicode_text:
            continue
        if ascii_text:
            spec[Name("/UF")] = String(ascii_text)
        elif unicode_text:
            spec[Name("/F")] = String(unicode_text)
        else:
            derived = str(key).strip() or "unnamed"
            spec[Name("/F")] = String(derived)
            spec[Name("/UF")] = String(derived)
        repaired += 1

    for kid in items(node.get(Name("/Kids"))):
        if isinstance(kid, pikepdf.Dictionary):
            repaired += _repair_file_specs(kid)
    return repaired


def fix_embedded_files(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Give every attachment both an ASCII and a Unicode filename.

    ``/F`` holds the name in a legacy encoding and ``/UF`` in Unicode.
    A viewer that reads only one and finds it missing shows the
    attachment with no name, so a reader is offered a file they cannot
    identify before opening it.
    """
    names = pdf.Root.get(Name("/Names"))
    if names is None:
        return FixResult("fix_embedded_files", True, "No attachments in document")

    embedded = names.get(Name("/EmbeddedFiles"))
    if embedded is None:
        return FixResult("fix_embedded_files", True, "No attachments in document")

    repaired = _repair_file_specs(embedded)
    if repaired:
        return FixResult(
            "fix_embedded_files", True,
            f"Completed the filenames on {repaired} attachment(s)",
        )
    return FixResult(
        "fix_embedded_files", True,
        "All attachments already have both filenames",
    )


def fix_remove_xfa(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Remove XFA form data, where AcroForm fields exist to fall back on.

    XFA is a superseded form technology that no current viewer supports
    well and no assistive technology reads. Where a document carries both
    XFA and ordinary AcroForm fields, removing the XFA leaves the working
    form behind.

    Where it carries XFA alone, the fields exist nowhere else and removing
    it would delete the form outright — so this declines and says what to
    do instead. That refusal is the whole safety of the fix.
    """
    acroform = pdf.Root.get(Name("/AcroForm"))
    if acroform is None:
        return FixResult("fix_remove_xfa", True, "No form in document")
    if acroform.get(Name("/XFA")) is None:
        return FixResult("fix_remove_xfa", True, "No XFA data present")

    field_count = len(items(acroform.get(Name("/Fields"))))
    if field_count == 0:
        return FixResult(
            "fix_remove_xfa", False,
            "This is a pure XFA form: its fields exist only as XFA, so"
            + " removing it would delete the form entirely. Rebuild the"
            + " form as an ordinary AcroForm instead.",
        )

    del acroform[Name("/XFA")]
    return FixResult(
        "fix_remove_xfa", True,
        f"Removed XFA form data; {field_count} AcroForm field(s) preserved",
    )
