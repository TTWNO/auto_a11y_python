"""Tests for ``auto_a11y.pdf.audit.required_fields`` and its check.

The collector's job is to gather what a form *declares* about which of
its fields are mandatory, and what its text says about how mandatory
fields are marked. Its blind spot — an asterisk drawn beside a field as
page content — is deliberate and is what the AI pass exists to cover, so
these tests pin the boundary as much as the behaviour.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf

from auto_a11y.pdf.audit.checks.forms import (
    check_required_fields_visually_indicated,
)
from auto_a11y.pdf.audit.required_fields import collect_required_fields
from auto_a11y.pdf.audit.structure import StructElement
from auto_a11y.pdf.models import AuditContext, CheckResult

_REQUIRED = 0x2


def _pdf_with_fields(fields: list[pikepdf.Dictionary]) -> pikepdf.Pdf:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.Root["/AcroForm"] = pdf.make_indirect(
        pikepdf.Dictionary(Fields=pikepdf.Array(
            [pdf.make_indirect(f) for f in fields]
        ))
    )
    return pdf


def _field(
    name: str, *, required: bool = True, tooltip: str = "", ft: str = "/Tx"
) -> pikepdf.Dictionary:
    field = pikepdf.Dictionary(
        FT=pikepdf.Name(ft),
        T=pikepdf.String(name),
        Rect=pikepdf.Array([0, 0, 100, 20]),
    )
    if required:
        field["/Ff"] = _REQUIRED
    if tooltip:
        field["/TU"] = pikepdf.String(tooltip)
    return field


def _para(index: int, text: str) -> StructElement:
    return StructElement(
        index=index,
        custom_tag="/P",
        resolved_tag="P",
        alt_text=None,
        actual_text=None,
        lang=None,
        children_indices=[],
        mcids=[],
        parent_index=-1,
        obj=pikepdf.Dictionary(),
        text_content=text,
    )


def _ctx(
    pdf: pikepdf.Pdf, elements: list[StructElement] | None = None
) -> AuditContext:
    ctx = AuditContext(
        pdf=pdf,
        pdf_path=Path("/tmp/test.pdf"),
        elements=elements or [],
        role_map={},
    )
    ctx.required_fields = collect_required_fields(pdf, ctx.elements)
    return ctx


def _only(results: list[CheckResult]) -> CheckResult:
    assert len(results) == 1, f"expected one CheckResult, got {len(results)}"
    return results[0]


# ---------------------------------------------------------------------------
# collect_required_fields
# ---------------------------------------------------------------------------


def test_collect_returns_none_without_an_acroform() -> None:
    assert collect_required_fields(pikepdf.Pdf.new(), []) is None


def test_collect_returns_none_when_no_field_is_required() -> None:
    pdf = _pdf_with_fields([_field("name", required=False)])
    assert collect_required_fields(pdf, []) is None


def test_collect_reads_only_fields_carrying_the_required_flag() -> None:
    pdf = _pdf_with_fields([
        _field("given", required=True),
        _field("middle", required=False),
    ])

    data = collect_required_fields(pdf, [])

    assert data is not None
    assert [f.name for f in data.fields] == ["given"]


def test_collect_finds_an_asterisk_in_the_field_name() -> None:
    pdf = _pdf_with_fields([_field("Surname *")])

    data = collect_required_fields(pdf, [])

    assert data is not None
    assert data.fields[0].has_indicator_in_metadata is True
    assert data.fields[0].indicator_detail == "* in name/tooltip"


def test_collect_finds_a_required_word_in_the_tooltip() -> None:
    pdf = _pdf_with_fields([_field("surname", tooltip="Surname (required)")])

    data = collect_required_fields(pdf, [])

    assert data is not None
    assert data.fields[0].indicator_detail == "required text in name/tooltip"


def test_collect_recognises_the_french_required_word() -> None:
    pdf = _pdf_with_fields([_field("nom", tooltip="Nom (obligatoire)")])

    data = collect_required_fields(pdf, [])

    assert data is not None
    assert data.fields[0].has_indicator_in_metadata is True


def test_collect_finds_an_english_legend_in_the_document_text() -> None:
    pdf = _pdf_with_fields([_field("surname")])
    elements = [_para(0, "Fields marked with * are required.")]

    data = collect_required_fields(pdf, elements)

    assert data is not None
    assert data.has_legend is True
    assert "*" in data.legend_text


def test_collect_finds_a_french_legend_in_the_document_text() -> None:
    pdf = _pdf_with_fields([_field("nom")])
    elements = [_para(0, "Les champs marqués d'un * sont obligatoires.")]

    data = collect_required_fields(pdf, elements)

    assert data is not None
    assert data.has_legend is True


def test_collect_reports_no_legend_when_the_text_says_nothing() -> None:
    pdf = _pdf_with_fields([_field("surname")])
    elements = [_para(0, "Please complete every part of this form.")]

    data = collect_required_fields(pdf, elements)

    assert data is not None
    assert data.has_legend is False
    assert data.legend_text == ""


def test_collect_splits_fields_by_whether_metadata_marks_them() -> None:
    pdf = _pdf_with_fields([_field("Surname *"), _field("given")])

    data = collect_required_fields(pdf, [])

    assert data is not None
    assert [f.name for f in data.with_indicator] == ["Surname *"]
    assert [f.name for f in data.missing_indicator] == ["given"]


# ---------------------------------------------------------------------------
# check_required_fields_visually_indicated
# ---------------------------------------------------------------------------


def test_check_is_not_applicable_when_nothing_is_required() -> None:
    pdf = _pdf_with_fields([_field("name", required=False)])
    res = _only(check_required_fields_visually_indicated(_ctx(pdf)))
    assert res.result == "NA"


def test_check_passes_when_every_required_field_is_marked() -> None:
    pdf = _pdf_with_fields([_field("Surname *"), _field("Given name *")])
    res = _only(check_required_fields_visually_indicated(_ctx(pdf)))
    assert res.result == "PASS"
    assert "All 2 required field(s)" in res.details


def test_check_reports_the_legend_when_one_was_found() -> None:
    pdf = _pdf_with_fields([_field("Surname *")])
    elements = [_para(0, "Fields marked with * are required.")]
    res = _only(check_required_fields_visually_indicated(_ctx(pdf, elements)))
    assert res.result == "PASS"
    assert "Legend found" in res.details


def test_check_warns_rather_than_fails_on_missing_metadata_indicators() -> None:
    """The indicator may be drawn on the page, which this cannot see."""
    pdf = _pdf_with_fields([_field("surname"), _field("Given name *")])

    res = _only(check_required_fields_visually_indicated(_ctx(pdf)))

    assert res.result == "WARN"
    assert "1 of 2 required field(s)" in res.details
    assert "Manual review" in res.details
