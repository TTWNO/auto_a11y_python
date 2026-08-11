"""Tests for note identifiers.

Two properties matter: identifiers end up unique, and applying the fix
twice to the same document produces the same file. The second is what
lets someone diff a fixed PDF against a re-run to confirm nothing else
changed.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, String

from auto_a11y.pdf.audit.structure import walk_structure_tree
from auto_a11y.pdf.fix.models import FixOptions
from auto_a11y.pdf.fix.notes import fix_note_ids


@pytest.fixture
def opts(tmp_path: Path) -> FixOptions:
    return FixOptions(pdf_path=tmp_path / "doc.pdf")


def _doc(*ids: str | None) -> pikepdf.Pdf:
    """A structure tree of notes, each with the given /ID or none."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    kids: list[pikepdf.Dictionary] = []
    for identifier in ids:
        node = pdf.make_indirect(
            Dictionary(Type=Name("/StructElem"), S=Name("/Note"))
        )
        if identifier is not None:
            node[Name("/ID")] = String(identifier)
        kids.append(node)
    struct_root = pdf.make_indirect(Dictionary(Type=Name("/StructTreeRoot")))
    struct_root[Name("/K")] = Array(kids)
    pdf.Root[Name("/StructTreeRoot")] = struct_root
    return pdf


def _ids(pdf: pikepdf.Pdf) -> list[str]:
    elements, _ = walk_structure_tree(pdf)
    out: list[str] = []
    for element in elements:
        if element.resolved_tag != "Note":
            continue
        value = element.obj.get(Name("/ID"))
        out.append(str(value) if value is not None else "")
    return out


def test_notes_without_identifiers_are_given_them(opts: FixOptions) -> None:
    pdf = _doc(None, None)

    result = fix_note_ids(pdf, opts)

    assert result.success
    assert _ids(pdf) == ["note-1", "note-2"]


def test_the_first_holder_of_a_duplicate_keeps_it(opts: FixOptions) -> None:
    """Anything already pointing at that identifier still resolves.

    pdfMax renames every note in a collision, including the first, which
    discards a working identifier along with the broken ones.
    """
    pdf = _doc("footnote-a", "footnote-a")

    result = fix_note_ids(pdf, opts)

    assert result.success
    identifiers = _ids(pdf)
    assert identifiers[0] == "footnote-a"
    assert identifiers[1] != "footnote-a"


def test_unique_identifiers_are_left_alone(opts: FixOptions) -> None:
    pdf = _doc("a", "b")

    result = fix_note_ids(pdf, opts)

    assert result.success
    assert _ids(pdf) == ["a", "b"]
    assert "already has a unique identifier" in result.description


def test_generated_identifiers_avoid_existing_ones(opts: FixOptions) -> None:
    pdf = _doc("note-1", None)

    fix_note_ids(pdf, opts)

    identifiers = _ids(pdf)
    assert identifiers[0] == "note-1"
    assert len(set(identifiers)) == 2, "the generated one must not collide"


def test_the_fix_is_reproducible(opts: FixOptions) -> None:
    """The divergence from pdfMax's UUIDs.

    Applying the same fixes to the same document twice must produce the
    same output, or the result cannot be compared against a re-run.
    """
    first = _ids(_run(opts))
    second = _ids(_run(opts))

    assert first == second


def _run(opts: FixOptions) -> pikepdf.Pdf:
    pdf = _doc(None, "dup", "dup", None)
    fix_note_ids(pdf, opts)
    return pdf


def test_no_notes_is_not_a_failure(opts: FixOptions) -> None:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    struct_root = pdf.make_indirect(Dictionary(Type=Name("/StructTreeRoot")))
    struct_root[Name("/K")] = Array([
        pdf.make_indirect(Dictionary(Type=Name("/StructElem"), S=Name("/P")))
    ])
    pdf.Root[Name("/StructTreeRoot")] = struct_root

    result = fix_note_ids(pdf, opts)

    assert result.success
    assert "No notes" in result.description
