"""Fixes for footnote and endnote identifiers.

A ``<Note>`` carries an ``/ID`` so that a reference in the text can point
at it and a reader can be taken back to where they left off. Two notes
sharing an identifier make that association ambiguous: the reference
resolves to whichever the reader's software finds first.
"""
from __future__ import annotations

import pikepdf
from pikepdf import Name, String

from auto_a11y.pdf.audit.structure import StructElement, walk_structure_tree
from auto_a11y.pdf.fix.models import FixOptions, FixResult


def fix_note_ids(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Give every ``<Note>`` an identifier no other note shares.

    Notes are visited in document order; the first holder of an
    identifier keeps it and later claimants are renamed. Keeping the
    first matters because anything already pointing at that identifier —
    a reference elsewhere in the document — still resolves.

    Divergence from pdfMax, which assigns a fresh UUID to every note
    involved in a collision, including the first. That discards a working
    identifier along with the broken ones, and its UUIDs make the fix
    non-reproducible: applying the same fixes to the same document twice
    produces different files, so the output cannot be compared or
    verified. Identifiers here are sequential and stable.
    """
    elements, _role_map = walk_structure_tree(pdf)
    if not elements:
        return FixResult("fix_note_ids", False, "No structure tree found")

    notes = [e for e in elements if e.resolved_tag == "Note"]
    if not notes:
        return FixResult("fix_note_ids", True, "No notes in document")

    def identifier_of(note: StructElement) -> str:
        value = note.obj.get(Name("/ID"))
        return str(value).strip() if value is not None else ""

    # Every identifier the document already holds, including ones on notes
    # further down. A generated identifier has to avoid all of them, not
    # only the ones seen so far — otherwise "note-1" could be minted for
    # the first note and collide with a later note that already has it.
    reserved = {identifier_of(note) for note in notes if identifier_of(note)}

    claimed: set[str] = set()
    assigned = 0
    counter = 0
    for note in notes:
        identifier = identifier_of(note)
        if identifier and identifier not in claimed:
            claimed.add(identifier)
            continue

        counter += 1
        candidate = f"note-{counter}"
        while candidate in reserved or candidate in claimed:
            counter += 1
            candidate = f"note-{counter}"
        note.obj[Name("/ID")] = String(candidate)
        claimed.add(candidate)
        assigned += 1

    if assigned:
        return FixResult(
            "fix_note_ids", True,
            f"Gave {assigned} note(s) a unique identifier",
        )
    return FixResult(
        "fix_note_ids", True, "Every note already has a unique identifier",
    )
