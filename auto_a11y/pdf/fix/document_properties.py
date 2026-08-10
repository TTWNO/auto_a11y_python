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

# Pages sampled when deriving the document language. Reading every page
# of a long report costs far more than it improves the verdict, so we take
# a representative set — see :func:`_sample_pages` for why they are spread
# through the document rather than taken from the front.
_LANG_SAMPLE_PAGES = 5

# Characters of sampled text fed to the detector. Enough for a stable
# word-frequency ratio without holding a whole book in memory.
_LANG_SAMPLE_CHARS = 6000


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


def _sample_pages(page_count: int) -> list[int]:
    """Pick up to :data:`_LANG_SAMPLE_PAGES` page indices spread evenly.

    Taking the first N pages is the cheap choice and the wrong one: page
    one of a report is often a cover — a title, a logo and a date — which
    carries almost no function words, and a bilingual document commonly
    front-loads one language. Spreading the sample across the document
    costs the same and represents it far better.
    """
    if page_count <= _LANG_SAMPLE_PAGES:
        return list(range(page_count))
    step = page_count / _LANG_SAMPLE_PAGES
    return [min(page_count - 1, int(i * step)) for i in range(_LANG_SAMPLE_PAGES)]


def _sample_text(pdf: pikepdf.Pdf) -> str:
    """Concatenate show-text operands from a representative set of pages.

    Deliberately crude: this feeds a word-frequency verdict, which needs a
    representative bag of words rather than correct reading order.
    """
    parts: list[str] = []
    wanted = set(_sample_pages(len(pdf.pages)))
    for index, page in enumerate(pdf.pages):
        if index not in wanted:
            continue
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
    """Set the document ``/Lang``: declared, else supplied, else derived.

    Resolution order is declared → supplied → derived from the document's
    own text, and it never stops to ask. This fix has to run unattended
    across whole sites, so a prompt would stall a queue of hundreds of
    documents; instead the derived language and the evidence for it go
    into the result, where the report shows them.

    A document with no extractable text — a pure scan, an empty file —
    yields no verdict. That case reports what happened and leaves ``/Lang``
    unset rather than inventing a value, because a wrong declaration makes
    a screen reader read the whole document in the wrong voice and nothing
    downstream would reveal it was a guess.
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

    derived, share = detect_language_from_text(_sample_text(pdf))
    if derived in ("en", "fr"):
        pdf.Root[Name("/Lang")] = String(derived)
        return FixResult(
            "fix_language", True,
            f'Document language derived as "{derived}" and set'
            + f" ({share:.0%} of sampled words matched)",
        )

    return FixResult(
        "fix_language", False,
        "No language could be derived from the document text"
        + f" ({derived}); /Lang left unset. Supply a language to set it"
        + " explicitly.",
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
