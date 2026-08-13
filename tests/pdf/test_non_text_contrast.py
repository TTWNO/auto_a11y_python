"""Tests for ``auto_a11y.pdf.audit.non_text_contrast`` and its check.

The exemptions are the interesting part. A field with no author-drawn
border is not a failure — the viewer's own field highlighting is what
makes it findable, and failing it would fail nearly every form ever
produced. A read-only field is not a failure either, because nobody has
to find one to fill it in. Both are counted and reported rather than
silently dropped, and these tests pin that.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf

from auto_a11y.pdf.audit.checks.color_contrast import check_non_text_contrast
from auto_a11y.pdf.audit.non_text_contrast import (
    FieldContrast,
    FieldContrastFinding,
    GraphicContrast,
    GraphicFinding,
    NonTextContrast,
    extract_field_contrast,
    summarise_fields,
    summarise_graphics,
)
from auto_a11y.pdf.models import AuditContext, CheckResult

_READ_ONLY = 0x1

_BLACK = (0.0, 0.0, 0.0)
_WHITE = (1.0, 1.0, 1.0)
_NEAR_WHITE = (0.95, 0.95, 0.95)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _finding(
    name: str = "field",
    *,
    border_color: tuple[float, float, float] | None = _BLACK,
    border_pass: bool | None = True,
    boundary_pass: bool | None = True,
    exempt_reason: str | None = None,
    page: int = 0,
) -> FieldContrastFinding:
    return FieldContrastFinding(
        field_name=name,
        field_type="Text",
        page=page,
        is_readonly=exempt_reason == "readonly",
        border_color=border_color,
        border_source="/MK/BC" if border_color else None,
        field_bg_color=_WHITE,
        field_bg_source="/MK/BG",
        page_bg_color=_WHITE,
        border_contrast=21.0 if border_pass else 1.2,
        boundary_contrast=21.0 if boundary_pass else 1.0,
        border_pass=border_pass,
        boundary_pass=boundary_pass,
        rect=(0.0, 0.0, 100.0, 20.0),
        exempt_reason=exempt_reason,
    )


def _graphic(*, passes: bool, category: str = "divider_line") -> GraphicFinding:
    return GraphicFinding(
        category=category,
        page=1,
        bbox=(0.0, 0.0, 100.0, 1.0),
        element_color=_BLACK if passes else _NEAR_WHITE,
        color_source="stroke",
        page_bg_color=_WHITE,
        contrast_ratio=21.0 if passes else 1.1,
        contrast_pass=passes,
        linewidth=1.0,
        description="Divider Line on page 1",
    )


def _data(
    fields: list[FieldContrastFinding] | None,
    graphics: list[GraphicFinding] | None = None,
) -> NonTextContrast:
    graphic_list = graphics or []
    return NonTextContrast(
        fields=(
            None if fields is None
            else FieldContrast(findings=fields, summary=summarise_fields(fields))
        ),
        graphics=GraphicContrast(
            findings=graphic_list,
            summary=summarise_graphics(graphic_list),
        ),
    )


def _ctx(data: NonTextContrast | None) -> AuditContext:
    ctx = AuditContext(
        pdf=pikepdf.Pdf.new(),
        pdf_path=Path("/tmp/test.pdf"),
        elements=[],
        role_map={},
    )
    ctx.non_text_contrast = data
    return ctx


def _only(results: list[CheckResult]) -> CheckResult:
    assert len(results) == 1, f"expected one CheckResult, got {len(results)}"
    return results[0]


# ---------------------------------------------------------------------------
# extract_field_contrast
# ---------------------------------------------------------------------------


def _pdf_with_field(field: pikepdf.Dictionary) -> pikepdf.Pdf:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.Root["/AcroForm"] = pdf.make_indirect(
        pikepdf.Dictionary(Fields=pikepdf.Array([pdf.make_indirect(field)]))
    )
    return pdf


def _widget(
    *,
    border: list[float] | None = None,
    background: list[float] | None = None,
    flags: int = 0,
) -> pikepdf.Dictionary:
    field = pikepdf.Dictionary(
        FT=pikepdf.Name("/Tx"),
        T=pikepdf.String("surname"),
        Rect=pikepdf.Array([100, 100, 300, 120]),
    )
    if flags:
        field["/Ff"] = flags
    mk: dict[str, object] = {}
    if border is not None:
        mk["/BC"] = pikepdf.Array(border)
    if background is not None:
        mk["/BG"] = pikepdf.Array(background)
    if mk:
        field["/MK"] = pikepdf.Dictionary(mk)
    return field


def test_extract_returns_none_without_an_acroform() -> None:
    assert extract_field_contrast(pikepdf.Pdf.new(), Path("/tmp/x.pdf")) is None


def test_extract_exempts_a_field_with_no_author_drawn_border() -> None:
    pdf = _pdf_with_field(_widget())

    data = extract_field_contrast(pdf, Path("/tmp/x.pdf"))

    assert data is not None
    assert data.findings[0].exempt_reason == "no_author_border"
    assert data.summary.exempt_count == 1


def test_extract_exempts_a_read_only_field_even_when_it_has_a_border() -> None:
    pdf = _pdf_with_field(_widget(border=[0, 0, 0], flags=_READ_ONLY))

    data = extract_field_contrast(pdf, Path("/tmp/x.pdf"))

    assert data is not None
    assert data.findings[0].exempt_reason == "readonly"
    assert data.findings[0].is_readonly is True


def test_extract_reads_an_explicit_border_colour() -> None:
    pdf = _pdf_with_field(_widget(border=[0, 0, 0], background=[1, 1, 1]))

    data = extract_field_contrast(pdf, Path("/tmp/x.pdf"))

    assert data is not None
    finding = data.findings[0]
    assert finding.border_color == _BLACK
    assert finding.border_source == "/MK/BC"
    assert finding.exempt_reason is None


def test_extract_measures_a_black_border_on_white_as_passing() -> None:
    pdf = _pdf_with_field(_widget(border=[0, 0, 0], background=[1, 1, 1]))

    data = extract_field_contrast(pdf, Path("/tmp/x.pdf"))

    assert data is not None
    assert data.findings[0].border_pass is True
    assert data.summary.border_fails == 0


def test_extract_measures_a_pale_border_on_white_as_failing() -> None:
    pdf = _pdf_with_field(_widget(border=[0.95, 0.95, 0.95], background=[1, 1, 1]))

    data = extract_field_contrast(pdf, Path("/tmp/x.pdf"))

    assert data is not None
    assert data.findings[0].border_pass is False
    assert data.summary.border_fails == 1


def test_extract_reads_a_grayscale_border_array() -> None:
    """One component is grey, three are RGB, four are CMYK."""
    pdf = _pdf_with_field(_widget(border=[0.0]))

    data = extract_field_contrast(pdf, Path("/tmp/x.pdf"))

    assert data is not None
    assert data.findings[0].border_color == _BLACK


# ---------------------------------------------------------------------------
# Summary arithmetic
# ---------------------------------------------------------------------------


def test_boundary_failure_does_not_count_when_the_border_passes() -> None:
    """A field whose fill matches the page is still findable by its border."""
    summary = summarise_fields([_finding(border_pass=True, boundary_pass=False)])

    assert summary.boundary_only_fails == 0
    assert summary.boundary_info == 1


def test_boundary_failure_counts_when_the_border_fails_too() -> None:
    summary = summarise_fields([_finding(border_pass=False, boundary_pass=False)])

    assert summary.boundary_only_fails == 1


def test_exempt_fields_are_excluded_from_every_failure_count() -> None:
    summary = summarise_fields([
        _finding(border_pass=False, boundary_pass=False, exempt_reason="readonly"),
    ])

    assert summary.border_fails == 0
    assert summary.boundary_only_fails == 0
    assert summary.exempt_count == 1
    assert summary.total_fields == 1


# ---------------------------------------------------------------------------
# check_non_text_contrast
# ---------------------------------------------------------------------------


def test_check_is_not_applicable_without_measurements() -> None:
    res = _only(check_non_text_contrast(_ctx(None)))
    assert res.result == "NA"


def test_check_is_not_applicable_with_neither_fields_nor_graphics() -> None:
    res = _only(check_non_text_contrast(_ctx(_data(None))))
    assert res.result == "NA"
    assert "No form fields or graphical elements" in res.details


def test_check_passes_on_graphics_alone() -> None:
    res = _only(check_non_text_contrast(_ctx(_data(None, [_graphic(passes=True)]))))
    assert res.result == "PASS"
    assert "No form fields" in res.details


def test_check_fails_on_a_failing_graphic_with_no_form() -> None:
    res = _only(check_non_text_contrast(_ctx(_data(None, [_graphic(passes=False)]))))
    assert res.result == "FAIL"
    assert "1 graphical element(s)" in res.details


def test_check_passes_when_every_non_exempt_field_passes() -> None:
    res = _only(check_non_text_contrast(_ctx(_data([_finding()]))))
    assert res.result == "PASS"
    assert "All 1 non-exempt field(s)" in res.details


def test_check_fails_and_names_each_kind_of_failure() -> None:
    data = _data(
        [
            _finding("a", border_pass=False),
            _finding("b", border_pass=False, boundary_pass=False),
        ],
        [_graphic(passes=False)],
    )

    res = _only(check_non_text_contrast(_ctx(data)))

    assert res.result == "FAIL"
    assert "2 field border(s) below 3:1" in res.details
    assert "1 field(s) with no visible boundary" in res.details
    assert "1 graphical element(s) below 3:1" in res.details


def test_check_is_not_applicable_when_every_field_is_exempt() -> None:
    """Exempt-only is "nothing to judge", not "everything passed"."""
    data = _data([_finding(exempt_reason="no_author_border", border_color=None)])

    res = _only(check_non_text_contrast(_ctx(data)))

    assert res.result == "NA"
    assert "exempt" in res.details
