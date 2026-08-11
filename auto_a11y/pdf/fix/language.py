"""Fixes for language tagging beyond the document's own ``/Lang``.

A screen reader picks its pronunciation from the language declared
nearest the text it is reading. That makes two things matter: the values
have to be tags the reader recognises, and the places a reader looks —
the catalog, structure elements, outline entries — have to carry one.

An unrecognised tag is worse than none: the reader falls back to its
default voice without any indication, so a French document tagged
``"french"`` is read aloud in English exactly as if it were untagged,
while appearing correctly labelled to everyone checking the file.
"""
from __future__ import annotations

import pikepdf
from pikepdf import Name, String

from auto_a11y.pdf.audit.checks.language import validate_bcp47
from auto_a11y.pdf.fix._pdf_objects import is_content_ref, kids
from auto_a11y.pdf.fix.models import FixOptions, FixResult

# Values that are not BCP 47 but whose intent is unambiguous. Kept to
# cases with exactly one plausible reading: three-letter ISO 639-2 codes
# and English names of languages. Anything requiring a guess about
# region or script is left for a person.
_KNOWN_CORRECTIONS: dict[str, str] = {
    # ISO 639-2 → ISO 639-1, including the bibliographic variants
    "eng": "en", "fra": "fr", "fre": "fr", "deu": "de", "ger": "de",
    "spa": "es", "ita": "it", "por": "pt", "nld": "nl", "dut": "nl",
    "rus": "ru", "zho": "zh", "chi": "zh", "jpn": "ja", "kor": "ko",
    "ara": "ar", "hin": "hi", "pol": "pl", "swe": "sv", "nor": "no",
    "dan": "da", "fin": "fi", "tur": "tr", "ell": "el", "gre": "el",
    "heb": "he", "tha": "th", "vie": "vi", "ces": "cs", "cze": "cs",
    "ron": "ro", "rum": "ro", "hun": "hu", "ukr": "uk", "cat": "ca",
    # Language names, which authoring tools write when a user types one
    "english": "en", "french": "fr", "german": "de", "spanish": "es",
    "italian": "it", "portuguese": "pt", "dutch": "nl", "russian": "ru",
    "chinese": "zh", "japanese": "ja", "korean": "ko", "arabic": "ar",
}


def correct_language_tag(value: str) -> str | None:
    """Return a better BCP 47 tag for ``value``, or ``None`` to leave it.

    ``None`` covers both "already correct" and "not confidently
    correctable" — in either case nothing should be written.

    The known corrections are consulted *before* the well-formedness
    check, because the two ask different questions. ``"eng"`` matches the
    BCP 47 grammar perfectly well, so a syntax check passes it; but BCP 47
    requires the shortest available code, and ``"en"`` exists. Checking
    validity first would leave every three-letter code in place, which is
    the bug this ordering avoids.
    """
    text = value.strip()
    if not text:
        return None

    known = _KNOWN_CORRECTIONS.get(text.lower())
    if known is not None and known != text and validate_bcp47(known):
        return known

    if validate_bcp47(text):
        return None

    # en_US and en-US differ only by the separator an authoring tool used.
    hyphenated = text.replace("_", "-")
    if hyphenated != text and validate_bcp47(hyphenated):
        return hyphenated
    return None


def fix_lang_bcp47(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Correct ``/Lang`` values that are not valid BCP 47 tags.

    Applies to the document catalog and to every structure element that
    declares its own language.

    Only unambiguous corrections are made: a three-letter ISO code, an
    English language name, or an underscore where a hyphen belongs. A
    value that would need a guess about region or script is left alone
    and stays a reported fault, because writing the wrong region is
    invisible and silently changes pronunciation.
    """
    corrected = 0
    unfixable: list[str] = []

    def correct_on(node: pikepdf.Object) -> None:
        nonlocal corrected
        value = node.get(Name("/Lang"))
        if value is None:
            return
        text = str(value).strip()
        if not text:
            return
        replacement = correct_language_tag(text)
        if replacement is not None:
            node[Name("/Lang")] = String(replacement)
            corrected += 1
        elif not validate_bcp47(text):
            unfixable.append(text)

    correct_on(pdf.Root)

    def walk(node: pikepdf.Object) -> None:
        correct_on(node)
        for child in kids(node) or []:
            if isinstance(child, pikepdf.Dictionary) and not is_content_ref(child):
                walk(child)

    struct_root = pdf.Root.get(Name("/StructTreeRoot"))
    if struct_root is not None:
        for root in kids(struct_root) or []:
            if isinstance(root, pikepdf.Dictionary):
                walk(root)

    if corrected and unfixable:
        return FixResult(
            "fix_lang_bcp47", True,
            f"Corrected {corrected} invalid /Lang value(s); left "
            + f"{len(unfixable)} that need a decision: "
            + ", ".join(sorted(set(unfixable))),
        )
    if corrected:
        return FixResult(
            "fix_lang_bcp47", True,
            f"Corrected {corrected} invalid /Lang value(s)",
        )
    if unfixable:
        return FixResult(
            "fix_lang_bcp47", False,
            "Could not correct these /Lang value(s) without guessing: "
            + ", ".join(sorted(set(unfixable))),
        )
    return FixResult(
        "fix_lang_bcp47", True, "All /Lang values are already valid BCP 47",
    )


def fix_outline_lang(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Declare a language on bookmark entries that have none.

    Bookmark titles sit outside the page content, so they do not inherit
    a language from anything a reader has been reading — an untagged
    entry is announced in the reader's default voice.

    Divergence from pdfMax, which stamps ``"en"`` on every entry when the
    document declares nothing. Uses the document's own ``/Lang``, or the
    language supplied with the fix, and otherwise declines: labelling a
    French document's bookmarks as English makes the panel read wrongly
    while looking correctly tagged.
    """
    declared = pdf.Root.get(Name("/Lang"))
    language = str(declared).strip() if declared is not None else ""
    if not language:
        language = (opts.lang or "").strip()
    if not language:
        return FixResult(
            "fix_outline_lang", False,
            "The document declares no /Lang and no language was supplied,"
            + " so bookmark entries have nothing to inherit. Apply"
            + " fix_language first, which derives one from the document"
            + " text.",
        )

    outlines = pdf.Root.get(Name("/Outlines"))
    if outlines is None:
        return FixResult("fix_outline_lang", True, "No bookmarks in document")

    tagged = 0

    def walk(entry: pikepdf.Object) -> None:
        nonlocal tagged
        existing = entry.get(Name("/Lang"))
        if existing is None or not str(existing).strip():
            entry[Name("/Lang")] = String(language)
            tagged += 1
        child = entry.get(Name("/First"))
        while child is not None:
            walk(child)
            child = child.get(Name("/Next"))

    sibling = outlines.get(Name("/First"))
    while sibling is not None:
        walk(sibling)
        sibling = sibling.get(Name("/Next"))

    if tagged:
        return FixResult(
            "fix_outline_lang", True,
            f'Set /Lang="{language}" on {tagged} bookmark entry/entries',
        )
    return FixResult(
        "fix_outline_lang", True, "All bookmark entries already declare a language",
    )
