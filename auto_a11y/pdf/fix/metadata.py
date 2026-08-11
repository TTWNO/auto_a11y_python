"""Fixes for XMP metadata, viewer preferences and conformance claims.

Everything here writes into the document's own description of itself
rather than its content, which makes one distinction load-bearing: some
of these entries are *statements about the file's structure*, and writing
one that isn't true is worse than leaving it absent. A reader who trusts
a PDF/UA claim and finds an untagged document has been actively misled;
one who sees no claim simply knows nothing.
"""
from __future__ import annotations

import pikepdf
from pikepdf import Name, String

from auto_a11y.pdf.fix._pdf_objects import ensure_dict, entry_text
from auto_a11y.pdf.fix.models import FixOptions, FixResult

# Characters of a title echoed back in a result message.
_TITLE_ECHO = 80


def _is_tagged(pdf: pikepdf.Pdf) -> bool:
    """True when the document has both a structure tree and the Marked flag."""
    struct_root = pdf.Root.get(Name("/StructTreeRoot"))
    if struct_root is None or struct_root.get(Name("/K")) is None:
        return False
    mark_info = pdf.Root.get(Name("/MarkInfo"))
    if mark_info is None:
        return False
    marked = mark_info.get(Name("/Marked"))
    return marked is not None and bool(marked)


def fix_display_doc_title(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Make the viewer show the document title rather than the filename.

    Without this, the window title and task switcher show ``scan_v3.pdf``,
    which is also what a screen reader announces when the document opens.
    """
    viewer_prefs = ensure_dict(pdf.Root, "/ViewerPreferences", pdf)
    existing = viewer_prefs.get(Name("/DisplayDocTitle"))
    if existing is not None and bool(existing):
        return FixResult(
            "fix_display_doc_title", True, "DisplayDocTitle already set",
        )

    viewer_prefs[Name("/DisplayDocTitle")] = True
    return FixResult(
        "fix_display_doc_title", True,
        "Set ViewerPreferences.DisplayDocTitle = true",
    )


def fix_pdfua_identifier(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Declare PDF/UA-1 conformance in XMP — but only if it could be true.

    Divergence from pdfMax, which writes the identifier unconditionally.
    ``pdfuaid:part`` is not a hint or a target; it is the document
    asserting that it meets PDF/UA-1, and conforming checkers and assistive
    technology take it at its word. Stamping it onto an untagged file
    produces a document that lies about itself, which is worse than one
    that claims nothing — the same reasoning pdfMax already applies to
    ``fix_mark_info``, which refuses to set ``/Marked`` without tags.

    Tagging is the prerequisite this can actually verify, so it is the bar:
    a tagged document may still fail other PDF/UA requirements, but an
    untagged one cannot possibly pass.
    """
    if not _is_tagged(pdf):
        return FixResult(
            "fix_pdfua_identifier", False,
            "Not declaring PDF/UA-1 conformance: the document has no"
            + " structure tree or is not marked as tagged, so the claim"
            + " would be false. Add tags first.",
        )

    with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
        if "pdfuaid:part" in meta:
            return FixResult(
                "fix_pdfua_identifier", True,
                "PDF/UA identifier already present",
            )
        meta["pdfuaid:part"] = "1"

    return FixResult(
        "fix_pdfua_identifier", True,
        "Added the PDF/UA-1 identifier (pdfuaid:part=1) to XMP metadata",
    )


def fix_suspects(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Clear ``/MarkInfo /Suspects``, asserting the tags are trustworthy.

    ``/Suspects true`` is a document saying its own tagging may not match
    its content — usually written by a tool that auto-tagged and could not
    vouch for the result. Clearing it is a human vouching instead, so this
    only removes the flag; it never sets it.
    """
    mark_info = pdf.Root.get(Name("/MarkInfo"))
    if mark_info is None:
        return FixResult(
            "fix_suspects", True, "No /MarkInfo — nothing to clear",
        )

    suspects = mark_info.get(Name("/Suspects"))
    if suspects is None or not bool(suspects):
        return FixResult(
            "fix_suspects", True, "/Suspects not set — no action needed",
        )

    del mark_info[Name("/Suspects")]
    return FixResult(
        "fix_suspects", True,
        "Cleared the /Suspects flag from /MarkInfo — the tags are now"
        + " asserted to match the content",
    )


def fix_xmp_title(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Mirror the document title into XMP ``dc:title``.

    Two places record a title and conforming readers may consult either,
    so a document with one and not the other is inconsistent about its
    own name.
    """
    title = (opts.title or "").strip()
    if not title:
        info = pdf.trailer.get(Name("/Info"))
        if info is not None:
            title = entry_text(info, "/Title")

    if not title:
        return FixResult(
            "fix_xmp_title", False,
            "No title available — supply one, or apply fix_title first to"
            + " derive it from the filename.",
        )

    with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
        meta["dc:title"] = title

    return FixResult(
        "fix_xmp_title", True,
        f'dc:title set to "{title[:_TITLE_ECHO]}" in XMP metadata',
    )


def fix_metadata_lang(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Make the language of the document's metadata determinable.

    The catalog ``/Lang`` covers the metadata as well as the content, so
    setting it is the whole fix; where it is already set there is nothing
    to do.
    """
    declared = entry_text(pdf.Root, "/Lang")
    if declared:
        return FixResult(
            "fix_metadata_lang", True,
            f'Document /Lang is "{declared}" — metadata language is'
            + " determinable",
        )

    lang = (opts.lang or "").strip()
    if not lang:
        return FixResult(
            "fix_metadata_lang", False,
            "The document declares no /Lang and no language was supplied."
            + " Apply fix_language first, which derives one from the"
            + " document text.",
        )

    pdf.Root[Name("/Lang")] = String(lang)
    return FixResult(
        "fix_metadata_lang", True,
        f'Set document /Lang="{lang}" — metadata language is now'
        + " determinable",
    )


def fix_accessibility_permission(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Report that the saved copy carries no accessibility restriction.

    Nothing is written here. Some PDFs set permission bits that forbid
    text extraction, which blocks assistive technology outright; pikepdf
    saves without encryption, so the copy the fixer writes has no such
    restriction. This exists so the run records that.
    """
    if pdf.trailer.get(Name("/Encrypt")) is None:
        return FixResult(
            "fix_accessibility_permission", True,
            "No encryption — accessibility was already unrestricted",
        )
    return FixResult(
        "fix_accessibility_permission", True,
        "The source was encrypted; the saved copy is not, so its"
        + " accessibility restrictions are gone",
    )
