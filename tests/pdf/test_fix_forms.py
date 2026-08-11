"""Tests for the form-field fixes.

Forms are the part of a PDF someone has to operate rather than read, so
these fixes are judged on whether the form becomes completable: fields
that announce what they are, a tab order that follows the page, and names
that don't make two fields share one value.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, String

from auto_a11y.pdf.fix._pdf_objects import flatten_form_fields
from auto_a11y.pdf.fix.forms import (
    fix_field_names_unique,
    fix_form_field_lang,
    fix_form_labels,
    fix_form_tab_order,
    fix_required_fields,
    fix_tab_order,
)
from auto_a11y.pdf.fix.models import FixOptions


@pytest.fixture
def opts(tmp_path: Path) -> FixOptions:
    return FixOptions(pdf_path=tmp_path / "form.pdf")


def _pdf_with_fields(*fields: Dictionary, pages: int = 1) -> pikepdf.Pdf:
    """A PDF whose AcroForm holds ``fields`` as terminal fields."""
    pdf = pikepdf.Pdf.new()
    for _ in range(pages):
        pdf.add_blank_page(page_size=(200, 200))
    indirect = [pdf.make_indirect(f) for f in fields]
    acroform = pdf.make_indirect(Dictionary(Fields=Array(indirect)))
    pdf.Root[Name("/AcroForm")] = acroform
    return pdf


def _text_field(**entries: object) -> Dictionary:
    """A terminal text field. Passed as a mapping rather than as kwargs
    because pikepdf types ``Dictionary``'s keyword form loosely."""
    fields: dict[str, object] = {"/FT": Name("/Tx")}
    fields.update({f"/{key}": value for key, value in entries.items()})
    return Dictionary(fields)


# ---------------------------------------------------------------------------
# Field-tree flattening
# ---------------------------------------------------------------------------

def test_flatten_walks_through_grouping_nodes() -> None:
    """Intermediate nodes group children under a name; only leaves are fields."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    leaf_a = pdf.make_indirect(_text_field(T=String("street")))
    leaf_b = pdf.make_indirect(_text_field(T=String("city")))
    group = pdf.make_indirect(Dictionary(T=String("address"),
                                         Kids=Array([leaf_a, leaf_b])))
    pdf.Root[Name("/AcroForm")] = pdf.make_indirect(
        Dictionary(Fields=Array([group]))
    )

    fields = flatten_form_fields(pdf)

    assert len(fields) == 2
    assert {str(f[Name("/T")]) for f in fields} == {"street", "city"}


def test_flatten_returns_nothing_without_a_form() -> None:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))

    assert flatten_form_fields(pdf) == []


# ---------------------------------------------------------------------------
# fix_form_labels
# ---------------------------------------------------------------------------

def test_labels_are_derived_from_the_field_name(opts: FixOptions) -> None:
    pdf = _pdf_with_fields(_text_field(T=String("client_address_1")))

    result = fix_form_labels(pdf, opts)

    assert result.success
    # "client_address_1" is the data model's name; the trailing index is a
    # position in a repeating group, not part of the label.
    assert str(flatten_form_fields(pdf)[0][Name("/TU")]) == "client address"


def test_labels_leave_an_existing_tooltip_alone(opts: FixOptions) -> None:
    pdf = _pdf_with_fields(
        _text_field(T=String("fld_1"), TU=String("Postal code"))
    )

    fix_form_labels(pdf, opts)

    assert str(flatten_form_fields(pdf)[0][Name("/TU")]) == "Postal code"


def test_labels_says_the_tooltip_needs_review(opts: FixOptions) -> None:
    # A name-derived tooltip is better than silence but worse than a real
    # label, and the result has to say so or nobody will improve it.
    pdf = _pdf_with_fields(_text_field(T=String("dob")))

    result = fix_form_labels(pdf, opts)

    assert "review" in result.description.lower()


def test_labels_reports_no_form(opts: FixOptions) -> None:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))

    result = fix_form_labels(pdf, opts)

    assert result.success
    assert "No form fields" in result.description


# ---------------------------------------------------------------------------
# Tab order
# ---------------------------------------------------------------------------

def test_form_tab_order_only_touches_pages_with_widgets(
    opts: FixOptions,
) -> None:
    pdf = _pdf_with_fields(_text_field(T=String("a")), pages=2)
    widget = pdf.make_indirect(Dictionary(Subtype=Name("/Widget")))
    pdf.pages[1].obj[Name("/Annots")] = Array([widget])

    result = fix_form_tab_order(pdf, opts)

    assert result.success
    assert Name("/Tabs") not in pdf.pages[0].obj
    assert str(pdf.pages[1].obj[Name("/Tabs")]) == "/S"


def test_tab_order_sets_every_page(opts: FixOptions) -> None:
    # Links are tabbed to as well, so pages without form fields matter too.
    pdf = _pdf_with_fields(_text_field(T=String("a")), pages=3)

    result = fix_tab_order(pdf, opts)

    assert result.success
    assert all(str(p.obj[Name("/Tabs")]) == "/S" for p in pdf.pages)


def test_tab_order_is_idempotent(opts: FixOptions) -> None:
    pdf = _pdf_with_fields(_text_field(T=String("a")), pages=2)
    fix_tab_order(pdf, opts)

    result = fix_tab_order(pdf, opts)

    assert result.success
    assert "already" in result.description


# ---------------------------------------------------------------------------
# fix_required_fields
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("wording", ["Name (required)", "Nom (obligatoire)"])
def test_required_is_flagged_from_explicit_wording(
    wording: str, opts: FixOptions
) -> None:
    pdf = _pdf_with_fields(_text_field(T=String("f1"), TU=String(wording)))

    result = fix_required_fields(pdf, opts)

    assert result.success
    assert int(flatten_form_fields(pdf)[0][Name("/Ff")]) & 2


def test_required_ignores_an_asterisk(opts: FixOptions) -> None:
    """An asterisk is a visual convention, not a statement of requiredness.

    Flagging on it would set a binding constraint from a decoration.
    """
    pdf = _pdf_with_fields(_text_field(T=String("f1"), TU=String("Name *")))

    fix_required_fields(pdf, opts)

    assert Name("/Ff") not in flatten_form_fields(pdf)[0]


def test_required_preserves_other_flag_bits(opts: FixOptions) -> None:
    # /Ff is a bitfield; bit 13 (4096) is Multiline. Overwriting rather
    # than OR-ing would silently turn a textarea back into one line.
    pdf = _pdf_with_fields(
        _text_field(T=String("notes required"), Ff=4096)
    )

    fix_required_fields(pdf, opts)

    flags = int(flatten_form_fields(pdf)[0][Name("/Ff")])
    assert flags & 2, "Required set"
    assert flags & 4096, "Multiline preserved"


# ---------------------------------------------------------------------------
# fix_field_names_unique
# ---------------------------------------------------------------------------

def test_duplicate_names_are_made_unique(opts: FixOptions) -> None:
    # Viewers treat two terminal fields sharing /T as one field with two
    # appearances, so the second can never hold its own value.
    pdf = _pdf_with_fields(
        _text_field(T=String("email")),
        _text_field(T=String("email")),
        _text_field(T=String("email")),
    )

    result = fix_field_names_unique(pdf, opts)

    assert result.success
    names = [str(f[Name("/T")]) for f in flatten_form_fields(pdf)]
    assert names == ["email", "email_2", "email_3"]


def test_renaming_carries_a_matching_tooltip_with_it(opts: FixOptions) -> None:
    # Leaving the old tooltip behind would label two different fields
    # identically — the same confusion one layer up.
    pdf = _pdf_with_fields(
        _text_field(T=String("email")),
        _text_field(T=String("email"), TU=String("email")),
    )

    fix_field_names_unique(pdf, opts)

    second = flatten_form_fields(pdf)[1]
    assert str(second[Name("/T")]) == "email_2"
    assert str(second[Name("/TU")]) == "email_2"


def test_renaming_leaves_a_real_tooltip_alone(opts: FixOptions) -> None:
    pdf = _pdf_with_fields(
        _text_field(T=String("email")),
        _text_field(T=String("email"), TU=String("Work email address")),
    )

    fix_field_names_unique(pdf, opts)

    second = flatten_form_fields(pdf)[1]
    assert str(second[Name("/T")]) == "email_2"
    assert str(second[Name("/TU")]) == "Work email address"


def test_renaming_does_not_collide_with_an_existing_name(
    opts: FixOptions,
) -> None:
    pdf = _pdf_with_fields(
        _text_field(T=String("email")),
        _text_field(T=String("email_2")),
        _text_field(T=String("email")),
    )

    fix_field_names_unique(pdf, opts)

    names = [str(f[Name("/T")]) for f in flatten_form_fields(pdf)]
    assert len(set(names)) == 3, f"names must stay distinct, got {names}"


# ---------------------------------------------------------------------------
# fix_form_field_lang
# ---------------------------------------------------------------------------

def test_field_lang_is_a_no_op_when_the_document_declares_one(
    opts: FixOptions,
) -> None:
    pdf = _pdf_with_fields(_text_field(T=String("a"), TU=String("Name")))
    pdf.Root[Name("/Lang")] = String("en")

    result = fix_form_field_lang(pdf, opts)

    assert result.success
    assert "inherit" in result.description


def test_field_lang_declines_without_a_language(opts: FixOptions) -> None:
    pdf = _pdf_with_fields(_text_field(T=String("a"), TU=String("Name")))

    result = fix_form_field_lang(pdf, opts)

    assert not result.success
    assert "fix_language" in result.description


def test_field_lang_tags_only_fields_with_a_tooltip(tmp_path: Path) -> None:
    pdf = _pdf_with_fields(
        _text_field(T=String("a"), TU=String("Name")),
        _text_field(T=String("b")),
    )

    result = fix_form_field_lang(
        pdf, FixOptions(pdf_path=tmp_path / "f.pdf", lang="fr")
    )

    assert result.success
    fields = flatten_form_fields(pdf)
    assert str(fields[0][Name("/Lang")]) == "fr"
    assert Name("/Lang") not in fields[1], "nothing to announce, nothing to tag"
