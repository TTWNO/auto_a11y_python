"""Fixes for document-level properties: title, language, metadata, tagging flag.

These are the cheapest and safest fixes in the set — they write catalog
and info-dictionary entries without touching the structure tree — which is
why they are also the ones most often applied in bulk.

One deliberate divergence from the pdfMax original is recorded in
:func:`fix_language`.
"""
from __future__ import annotations

import re
from datetime import datetime

import pikepdf
from pikepdf import Dictionary, Name, String

from auto_a11y.pdf.fix.models import FixOptions, FixResult
from auto_a11y.pdf.language import detect_language_from_text

# Pages sampled when guessing the document language. The opening pages
# carry running prose; sampling the whole document would cost far more
# for no better guess.
_LANG_SAMPLE_PAGES = 3

# Characters of sampled text fed to the detector. Enough for a stable
# word-frequency ratio without holding a whole book in memory.
_LANG_SAMPLE_CHARS = 3000

# Minimum detector confidence before we write a guessed /Lang.
_LANG_MIN_CONFIDENCE = 0.7


def _ensure_dict(parent: pikepdf.Object, key: str, pdf: pikepdf.Pdf) -> pikepdf.Object:
    """Return ``parent[key]``, creating it as an indirect Dictionary if absent."""
    existing = parent.get(Name(key))
    if existing is not None:
        return existing
    created = pdf.make_indirect(Dictionary())
    parent[Name(key)] = created
    return created


def _entry_text(obj: pikepdf.Object, key: str) -> str:
    """Return ``obj[key]`` as stripped text, or ``""`` when absent."""
    value = obj.get(Name(key))
    return str(value).strip() if value is not None else ""


def fix_title(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Set ``/Title`` and enable ``/ViewerPreferences /DisplayDocTitle``.

    Both halves matter: a title the viewer never displays still leaves the
    filename in the window title and the task switcher, which is what a
    screen reader announces when the document opens.

    Falls back to deriving a title from the filename when the user supplies
    none and the document has none.
    """
    info = _ensure_dict(pdf.trailer, "/Info", pdf)

    user_title = (opts.title or "").strip()
    if user_title:
        title = user_title
        info[Name("/Title")] = String(title)
    else:
        title = _entry_text(info, "/Title")
        if not title:
            # "annual_report-2025.pdf" → "Annual Report 2025"
            title = re.sub(r"[_\-]+", " ", opts.pdf_path.stem).strip().title()
            info[Name("/Title")] = String(title)

    viewer_prefs = _ensure_dict(pdf.Root, "/ViewerPreferences", pdf)
    viewer_prefs[Name("/DisplayDocTitle")] = True

    return FixResult(
        "fix_title", True,
        f'Title set to "{title}", DisplayDocTitle enabled',
    )


def _sample_text(pdf: pikepdf.Pdf) -> str:
    """Concatenate show-text operands from the opening pages.

    Deliberately crude: this feeds a word-frequency guess, which needs a
    representative bag of words rather than correct reading order.
    """
    parts: list[str] = []
    for index, page in enumerate(pdf.pages):
        if index >= _LANG_SAMPLE_PAGES:
            break
        try:
            for inst in pikepdf.parse_content_stream(page):
                if isinstance(inst, pikepdf.ContentStreamInlineImage):
                    continue
                if str(inst.operator) not in ("Tj", "TJ"):
                    continue
                for operand in inst.operands:
                    if isinstance(operand, pikepdf.Array):
                        # TJ interleaves strings with kerning numbers.
                        # Indexed rather than iterated, matching the rest
                        # of the audit engine — pikepdf types Array
                        # iteration as Iterable, not Iterator.
                        for position in range(len(operand)):
                            item = operand[position]
                            if isinstance(item, pikepdf.String):
                                parts.append(str(item))
                    elif isinstance(operand, pikepdf.String):
                        parts.append(str(operand))
        except (pikepdf.PdfError, ValueError):
            # A page whose content stream will not parse simply
            # contributes nothing to the sample.
            continue
    return " ".join(parts)[:_LANG_SAMPLE_CHARS]


def fix_language(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Set the document ``/Lang`` from user input, or a confident guess.

    Divergence from pdfMax: where its fixer falls back to ``"en"`` for any
    document it cannot identify, this one declines and asks. Declaring the
    wrong language is not a neutral default — it makes a screen reader
    pronounce the entire document with the wrong voice, which is worse for
    the reader than declaring nothing and is invisible to the person who
    applied the fix.
    """
    existing = _entry_text(pdf.Root, "/Lang")
    if existing:
        return FixResult(
            "fix_language", True, f"Language already set: {existing}",
        )

    lang_code = (opts.lang or "").strip()
    if lang_code:
        pdf.Root[Name("/Lang")] = String(lang_code)
        return FixResult(
            "fix_language", True, f'Document language set to "{lang_code}"',
        )

    guess, confidence = detect_language_from_text(_sample_text(pdf))
    if guess in ("en", "fr") and confidence > _LANG_MIN_CONFIDENCE:
        pdf.Root[Name("/Lang")] = String(guess)
        return FixResult(
            "fix_language", True,
            f'Document language detected as "{guess}"'
            + f" (confidence {confidence:.0%}) and set",
        )

    return FixResult(
        "fix_language", False,
        "Could not identify the document language with confidence."
        + " Choose the language and apply this fix again — guessing"
        + " would make a screen reader read the document in the wrong"
        + " voice.",
    )


def fix_mark_info(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Set ``/MarkInfo /Marked true`` — only when a structure tree exists.

    The flag is a claim that the document is tagged. Setting it on an
    untagged document makes the file assert an accessibility it does not
    have, which is worse than the honest failure, so this declines.
    """
    struct_root = pdf.Root.get(Name("/StructTreeRoot"))
    has_structure = (
        struct_root is not None and struct_root.get(Name("/K")) is not None
    )
    if not has_structure:
        return FixResult(
            "fix_mark_info", False,
            "Cannot mark as tagged: no structure tree exists. Add"
            + " tags first — marking an untagged document as tagged"
            + " makes it claim an accessibility it does not have.",
        )

    mark_info = _ensure_dict(pdf.Root, "/MarkInfo", pdf)
    mark_info[Name("/Marked")] = True
    return FixResult("fix_mark_info", True, "/MarkInfo/Marked set to true")


def fix_metadata(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Populate the info dictionary's descriptive entries.

    User-supplied values overwrite what is there; absent ones are filled
    only where a default is meaningful. ``/Title`` has no default here
    because :func:`fix_title` owns deriving one.
    """
    info = _ensure_dict(pdf.trailer, "/Info", pdf)

    # (PDF key, user value, default when the entry is missing entirely)
    fields: list[tuple[str, str | None, str | None]] = [
        ("/Title", opts.title, None),
        ("/Author", opts.author, "Unknown"),
        ("/Creator", opts.creator, "Unknown"),
        ("/Producer", opts.producer, None),
        ("/CreationDate", opts.creation_date, None),
    ]

    updated: list[str] = []
    for pdf_key, user_value, default in fields:
        supplied = (user_value or "").strip()
        current = _entry_text(info, pdf_key)
        if supplied and supplied != current:
            info[Name(pdf_key)] = String(supplied)
            updated.append(pdf_key)
        elif not current and default:
            info[Name(pdf_key)] = String(default)
            updated.append(pdf_key)

    if not _entry_text(info, "/CreationDate"):
        info[Name("/CreationDate")] = String(
            datetime.now().strftime("D:%Y%m%d%H%M%S")
        )
        updated.append("/CreationDate")

    if updated:
        return FixResult(
            "fix_metadata", True, f"Set metadata: {', '.join(updated)}",
        )
    return FixResult(
        "fix_metadata", True, "All metadata fields already present",
    )
