"""Fixes for page annotations: descriptions, language, and removals.

An annotation is a thing laid over the page — a link, a note, a stamp, a
form widget. Assistive technology reaches them through the page's
``/Annots`` array, and announces ``/Contents`` as the annotation's
description. One with no ``/Contents`` is announced by type alone, so a
reader hears "link" or "note" with no idea what it leads to or says.
"""
from __future__ import annotations

import pikepdf
from pikepdf import Array, Name, String

from auto_a11y.pdf.fix._pdf_objects import items
from auto_a11y.pdf.fix.models import FixOptions, FixResult


def _page_annots(page: pikepdf.Page) -> list[pikepdf.Object]:
    return items(page.obj.get(Name("/Annots")))


def _write_annots(page: pikepdf.Page, annots: list[pikepdf.Object]) -> None:
    if annots:
        page.obj[Name("/Annots")] = Array(annots)
    elif Name("/Annots") in page.obj:
        del page.obj[Name("/Annots")]


def fix_trapnet(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Remove TrapNet annotations.

    Trapping is a prepress instruction for the printing press — how much
    inks should overlap. It carries nothing for a reader, and PDF 2.0
    deprecated it outright, so it is removed rather than described.
    """
    removed = 0
    for page in pdf.pages:
        annots = _page_annots(page)
        if not annots:
            continue
        kept = [
            annot for annot in annots
            if str(annot.get(Name("/Subtype")) or "") != "/TrapNet"
        ]
        if len(kept) != len(annots):
            removed += len(annots) - len(kept)
            _write_annots(page, kept)

    if removed:
        return FixResult(
            "fix_trapnet", True, f"Removed {removed} TrapNet annotation(s)",
        )
    return FixResult("fix_trapnet", True, "No TrapNet annotations found")


def fix_ref_xobjects(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Remove Reference XObjects from page resources.

    A Reference XObject imports a page from another file at render time.
    Its content is therefore absent from this document's structure tree,
    so nothing describes it to a screen reader and nothing can.

    Caveat carried over from the original: only the resource entry is
    removed, not the ``Do`` operator in the content stream that invokes
    it. The invocation becomes a reference to a name that is no longer
    defined, which readers ignore, but the stream is no longer strictly
    well-formed. Rewriting content streams is a larger change than this
    fix takes on.
    """
    removed = 0
    for page in pdf.pages:
        resources = page.obj.get(Name("/Resources"))
        if resources is None:
            continue
        xobjects = resources.get(Name("/XObject"))
        if xobjects is None:
            continue
        referencing = [
            key for key in xobjects.keys()
            if Name("/Ref") in xobjects[key]
        ]
        for key in referencing:
            del xobjects[key]
            removed += 1

    if removed:
        return FixResult(
            "fix_ref_xobjects", True,
            f"Removed {removed} Reference XObject(s); their Do operators"
            + " remain in the content stream and now resolve to nothing",
        )
    return FixResult("fix_ref_xobjects", True, "No Reference XObjects found")


def fix_annot_descriptions(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Set ``/Contents`` on annotations from user-supplied descriptions.

    Keys are ``"page:position"``, both counting from 1 to match what the
    report prints. The original counted pages from 0 while the report
    showed them from 1, so every description landed on the page before
    the one it was written for.
    """
    if not opts.annot_descriptions_map:
        return FixResult(
            "fix_annot_descriptions", False,
            "No annotation descriptions provided",
        )

    written = 0
    problems: list[str] = []
    for key, description in sorted(opts.annot_descriptions_map.items()):
        page_part, _, position_part = key.partition(":")
        try:
            page_number = int(page_part)
            position = int(position_part)
        except (TypeError, ValueError):
            problems.append(f'"{key}" is not page:position')
            continue
        if page_number < 1 or page_number > len(pdf.pages):
            problems.append(f"page {page_number} does not exist")
            continue

        annots = _page_annots(pdf.pages[page_number - 1])
        if position < 1 or position > len(annots):
            problems.append(
                f"page {page_number} has no annotation {position}"
            )
            continue

        annots[position - 1][Name("/Contents")] = String(description)
        written += 1

    total = len(opts.annot_descriptions_map)
    if written and not problems:
        return FixResult(
            "fix_annot_descriptions", True,
            f"Set /Contents on {written} annotation(s)",
        )
    if written:
        return FixResult(
            "fix_annot_descriptions", True,
            f"Set /Contents on {written} of {total} annotation(s) — "
            + "; ".join(problems),
        )
    return FixResult(
        "fix_annot_descriptions", False,
        "Set no descriptions — " + "; ".join(problems),
    )


def fix_annot_contents_lang(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Give annotation text a determinable language.

    Only needed where the document declares no ``/Lang``; with one set
    every annotation inherits it. Annotations on a page that declares its
    own language are also left alone, since they inherit that.
    """
    declared = pdf.Root.get(Name("/Lang"))
    if declared is not None and str(declared).strip():
        return FixResult(
            "fix_annot_contents_lang", True,
            "Document /Lang already set — annotations inherit it",
        )

    lang = (opts.lang or "").strip()
    if not lang:
        return FixResult(
            "fix_annot_contents_lang", False,
            "The document declares no /Lang and no language was supplied."
            + " Apply fix_language first, which derives one from the"
            + " document text.",
        )

    tagged = 0
    for page in pdf.pages:
        page_lang = page.obj.get(Name("/Lang"))
        if page_lang is not None and str(page_lang).strip():
            continue
        for annot in _page_annots(page):
            contents = annot.get(Name("/Contents"))
            if contents is None or not str(contents).strip():
                continue
            if annot.get(Name("/Lang")) is not None:
                continue
            annot[Name("/Lang")] = String(lang)
            tagged += 1

    return FixResult(
        "fix_annot_contents_lang", True,
        f'Set /Lang="{lang}" on {tagged} annotation(s)',
    )
