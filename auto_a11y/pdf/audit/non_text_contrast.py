"""Contrast of things that are not text — WCAG 1.4.11.

Text contrast has an unambiguous foreground and background, which is why
:mod:`auto_a11y.pdf.audit.colors` can settle it from colour pairs alone.
The parts of a page that are *not* text do not: a form field is visible
because of its border, or because its fill differs from the page, or
because of a line drawn under it, and losing one of those is only a
failure when the others are missing too.

Two collectors, matching pdfMax's two:

* :func:`extract_field_contrast` (pdfMax line ~1603) measures each
  AcroForm field twice — border against the page, and field fill against
  the page — because either one can be what makes the field findable.
* :func:`extract_graphical_contrast` (pdfMax line ~2077) measures drawn
  graphics: table rules, divider lines, chart bars. These are found with
  pdfminer's ``LTLine``/``LTRect``/``LTCurve`` objects rather than the
  structure tree, since none of them is required to be tagged.

Both are deliberately conservative about what they judge. A field with
no author-drawn border is exempt rather than failed — the reader's own
field highlighting is what makes it visible, and marking that a failure
would fail nearly every form ever produced. Hairlines below a quarter
point, page-border frames, and graphics that sit on top of a form field
are skipped for the same reason: they are not what the criterion is
about.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Protocol, runtime_checkable

import pikepdf
import wcag_contrast_ratio as wcr
from PIL import Image
from pdfminer.high_level import extract_pages
from pdfminer.layout import LAParams, LTCurve, LTItem, LTLine, LTRect
from pdfminer.pdfinterp import Color

from auto_a11y.pdf.audit import pikepdf_helpers
from auto_a11y.pdf.audit.colors import (
    RgbColor,
    ap_n_stream,
    build_page_lookups,
    field_label,
    flatten_form_fields,
    page_height_points,
    parse_color_array,
    resolve_field_page,
)
from auto_a11y.pdf.audit.rasterize import render_page_to_png
from auto_a11y.pdf.audit.structure import StructElement

logger = logging.getLogger(__name__)

__all__ = [
    "FieldContrast",
    "FieldContrastFinding",
    "FieldContrastSummary",
    "GraphicContrast",
    "GraphicContrastSummary",
    "GraphicFinding",
    "NonTextContrast",
    "collect_non_text_contrast",
    "summarise_fields",
    "summarise_graphics",
    "extract_field_contrast",
    "extract_graphical_contrast",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


#: WCAG 1.4.11 threshold for user-interface components and graphical
#: objects.
_THRESHOLD: float = 3.0

#: Rendered at 1:1 with PDF user space so a point is a pixel and the
#: sampling maths needs no scale factor.
_RENDER_DPI: int = 72

#: Distance in points outside a rect at which the page background is
#: sampled. Far enough to clear a border, near enough to still be the
#: same background.
_SAMPLE_OFFSET: float = 5.0

#: Strokes thinner than this are hairlines — a rendering artefact of
#: zero-width lines, not a design decision anyone can act on.
_MIN_LINEWIDTH: float = 0.25

#: A graphic touching this many page edges is the page frame.
_PAGE_EDGE_MARGIN: float = 15.0
_PAGE_FRAME_EDGES: int = 3

#: How many pages of graphics to inspect. Graphics scanning renders and
#: lays out each page, so it is the most expensive collector in the
#: audit; pdfMax caps it at five and so do we.
_MAX_GRAPHIC_PAGES: int = 5

#: Below this height a rect is a horizontal rule rather than a shape;
#: below this width, a vertical one.
_LINE_THICKNESS: float = 5.0

#: A rule spanning this share of the page width is a divider.
_DIVIDER_WIDTH_RATIO: float = 0.3

#: Degenerate-rect guard: a field smaller than this in either dimension
#: has no visible boundary to measure.
_MIN_FIELD_SIZE: float = 1.0

_WHITE: RgbColor = (1.0, 1.0, 1.0)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldContrastFinding:
    """Border and boundary contrast for one form field.

    Attributes:
        field_name: the field's ``/T``, or ``"unnamed"``.
        field_type: ``Text``, ``Button``, ``Dropdown``, ``Signature`` or
            the raw ``/FT`` when it is none of those.
        page: 0-based page index the field's widget sits on.
        is_readonly: the ``/Ff`` read-only bit. Read-only fields are
            exempt — nobody has to find one to fill it in.
        border_color: the border colour, or ``None`` when the field
            draws no border of its own.
        border_source: where the border colour came from — ``/MK/BC`` or
            ``/AP/N stroke``.
        field_bg_color: the field's own background.
        field_bg_source: ``/MK/BG``, ``sampled (field center)``, or
            ``default white``.
        page_bg_color: the page behind the field, sampled around it.
        border_contrast: border against page background, or ``None``
            when there is no border.
        boundary_contrast: field background against page background.
        border_pass: whether *border_contrast* reaches 3:1.
        boundary_pass: whether *boundary_contrast* reaches 3:1.
        rect: the widget rectangle, normalised so ``x0 < x1``.
        exempt_reason: ``readonly``, ``no_author_border``, or ``None``.
    """

    field_name: str
    field_type: str
    page: int
    is_readonly: bool
    border_color: RgbColor | None
    border_source: str | None
    field_bg_color: RgbColor
    field_bg_source: str
    page_bg_color: RgbColor
    border_contrast: float | None
    boundary_contrast: float | None
    border_pass: bool | None
    boundary_pass: bool | None
    rect: tuple[float, float, float, float]
    exempt_reason: str | None


@dataclass(frozen=True)
class FieldContrastSummary:
    """Counts across every field measured.

    Attributes:
        total_fields: fields measured, exempt ones included.
        fields_with_borders: non-exempt fields that draw a border.
        border_fails: non-exempt fields whose border misses 3:1.
        boundary_only_fails: non-exempt fields whose fill matches the
            page *and* whose border does not save them. This is the
            invisible-field count.
        boundary_info: non-exempt fields whose fill matches the page,
            border or no border. Reported rather than failed.
        exempt_count: fields excluded from judgement.
    """

    total_fields: int
    fields_with_borders: int
    border_fails: int
    boundary_only_fails: int
    boundary_info: int
    exempt_count: int


@dataclass(frozen=True)
class FieldContrast:
    """Everything the field pass found."""

    findings: list[FieldContrastFinding]
    summary: FieldContrastSummary


@dataclass(frozen=True)
class GraphicFinding:
    """Contrast for one drawn graphic.

    Attributes:
        category: ``table_border``, ``divider_line``, ``chart_element``
            or ``other_graphic``.
        page: 1-based page number, as printed in the report.
        bbox: the graphic's bounding box in PDF points.
        element_color: the stroke or fill colour that was measured.
        color_source: ``stroke`` or ``fill``.
        page_bg_color: the background sampled around the graphic.
        contrast_ratio: the measured ratio, rounded to two places.
        contrast_pass: whether it reaches 3:1.
        linewidth: the stroke width in points.
        description: a one-line human-readable summary.
    """

    category: str
    page: int
    bbox: tuple[float, float, float, float]
    element_color: RgbColor
    color_source: str
    page_bg_color: RgbColor
    contrast_ratio: float
    contrast_pass: bool
    linewidth: float
    description: str


@dataclass(frozen=True)
class GraphicContrastSummary:
    """Counts across every graphic measured."""

    total_elements: int
    table_borders: int
    divider_lines: int
    chart_elements: int
    other_graphics: int
    table_border_fails: int
    divider_fails: int
    chart_element_fails: int
    total_fails: int


@dataclass(frozen=True)
class GraphicContrast:
    """Everything the graphics pass found."""

    findings: list[GraphicFinding]
    summary: GraphicContrastSummary


@dataclass(frozen=True)
class NonTextContrast:
    """Both passes' output, as stored on the audit context.

    ``fields`` is ``None`` for a document with no AcroForm — a distinct
    state from "a form whose every field passed", and the check reports
    the two differently.
    """

    fields: FieldContrast | None
    graphics: GraphicContrast


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _clamp(color: RgbColor) -> RgbColor:
    """Clamp each component into [0, 1] so the ratio maths is defined."""
    return (
        min(1.0, max(0.0, color[0])),
        min(1.0, max(0.0, color[1])),
        min(1.0, max(0.0, color[2])),
    )


def _contrast(a: RgbColor, b: RgbColor) -> float | None:
    """WCAG contrast ratio, or ``None`` when the colours cannot be read."""
    try:
        return wcr.rgb(_clamp(a), _clamp(b))
    except (ValueError, TypeError):
        return None


def _hex(color: RgbColor) -> str:
    """Render an RGB triple as ``#RRGGBB``."""
    return "#{:02X}{:02X}{:02X}".format(
        int(color[0] * 255), int(color[1] * 255), int(color[2] * 255)
    )


def _render_page(pdf_path: Path, page_num: int) -> Image.Image | None:
    """Render one page as an RGB image, or ``None`` if it will not render."""
    try:
        png = render_page_to_png(pdf_path, page_num, dpi=_RENDER_DPI)
    except (OSError, ValueError, TypeError):
        return None
    if png is None:
        return None
    try:
        return Image.open(BytesIO(png)).convert("RGB")
    except (OSError, ValueError, TypeError):
        return None


def _sample_point(
    img: Image.Image, page_height: float, x: float, y: float, radius: int = 3
) -> RgbColor:
    """Median colour of a square around a PDF coordinate.

    The raster's origin is top-left and the PDF's is bottom-left, hence
    the y flip. A median over a neighbourhood rather than a single pixel
    read, so anti-aliasing on a nearby edge cannot decide the answer.
    """
    width, height = img.size
    px = max(0, min(int(x), width - 1))
    py = max(0, min(int(page_height - y), height - 1))

    reds: list[int] = []
    greens: list[int] = []
    blues: list[int] = []
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            sx = max(0, min(px + dx, width - 1))
            sy = max(0, min(py + dy, height - 1))
            pixel = img.getpixel((sx, sy))
            # An RGB image yields a 3-tuple; a mode we did not convert
            # to would yield a scalar, and there is nothing to sample.
            if not isinstance(pixel, tuple) or len(pixel) < 3:
                continue
            reds.append(int(pixel[0]))
            greens.append(int(pixel[1]))
            blues.append(int(pixel[2]))

    if not reds:
        return _WHITE
    mid = len(reds) // 2
    return (
        sorted(reds)[mid] / 255.0,
        sorted(greens)[mid] / 255.0,
        sorted(blues)[mid] / 255.0,
    )


def _median_of(samples: list[RgbColor]) -> RgbColor:
    """Middle-two mean per channel across four samples.

    Four samples around a rect can include one that landed on adjacent
    ink; averaging the middle two per channel discards that outlier
    without discarding a genuinely two-tone background.
    """
    reds = sorted(s[0] for s in samples)
    greens = sorted(s[1] for s in samples)
    blues = sorted(s[2] for s in samples)
    lo = (len(samples) - 1) // 2
    hi = len(samples) // 2
    return (
        (reds[lo] + reds[hi]) / 2,
        (greens[lo] + greens[hi]) / 2,
        (blues[lo] + blues[hi]) / 2,
    )


# ---------------------------------------------------------------------------
# Field borders
# ---------------------------------------------------------------------------


def _border_from_appearance(ap_stream: pikepdf.Object) -> RgbColor | None:
    """The stroke colour a field's appearance stream draws its border in.

    Mirrors pdfMax's ``_parse_ap_stream_border_color`` (line ~1343). A
    border is a rectangle followed by a stroke, so the colour that
    counts is whichever stroke colour is in force when a ``re`` is
    stroked. The first one found is the border; later strokes are
    decoration inside the field.
    """
    try:
        content = pikepdf.parse_content_stream(ap_stream)
    except (pikepdf.PdfError, ValueError, TypeError):
        return None

    stroke: RgbColor | None = None
    saw_rect = False
    for instruction in content:
        if isinstance(instruction, pikepdf.ContentStreamInlineImage):
            continue
        operator = str(instruction.operator)
        # The stub types operands as ``_ObjectList``; materialising it as
        # a plain list is the pattern colors.py already established, and
        # at runtime numeric operands arrive as bare int/Decimal anyway.
        operands: list[object] = list(instruction.operands)

        if operator == "RG" and len(operands) >= 3:
            stroke = _operands_rgb(operands[:3])
        elif operator == "G" and len(operands) >= 1:
            gray = _numeric(operands[0])
            stroke = None if gray is None else (gray, gray, gray)
        elif operator == "K" and len(operands) >= 4:
            stroke = _operands_cmyk(operands[:4])
        elif operator == "re":
            saw_rect = True
        elif (
            operator in ("S", "s", "B", "B*", "b", "b*")
            and saw_rect
            and stroke is not None
        ):
            return stroke
    return None


def _numeric(operand: object) -> float | None:
    """Coerce a content-stream operand to a float, or ``None``."""
    if isinstance(operand, (int, float)):
        return float(operand)
    if isinstance(operand, pikepdf.Object):
        try:
            return float(operand)
        except (TypeError, ValueError):
            return None
    return None


def _operands_rgb(operands: list[object]) -> RgbColor | None:
    values = [_numeric(o) for o in operands]
    if any(v is None for v in values):
        return None
    red, green, blue = (v for v in values if v is not None)
    return (red, green, blue)


def _operands_cmyk(operands: list[object]) -> RgbColor | None:
    values = [_numeric(o) for o in operands]
    if any(v is None for v in values):
        return None
    cyan, magenta, yellow, black = (v for v in values if v is not None)
    return (
        (1 - cyan) * (1 - black),
        (1 - magenta) * (1 - black),
        (1 - yellow) * (1 - black),
    )


def _normalised_rect(
    field: pikepdf.Dictionary,
) -> tuple[float, float, float, float] | None:
    """The widget rect with corners ordered, or ``None`` if unusable."""
    rect = pikepdf_helpers.get_array(field, "/Rect")
    if rect is None or len(rect) < 4:
        return None
    values = [_numeric(rect[i]) for i in range(4)]
    if any(v is None for v in values):
        return None
    x0, y0, x1, y1 = (v for v in values if v is not None)
    if x0 > x1:
        x0, x1 = x1, x0
    if y0 > y1:
        y0, y1 = y1, y0
    if x1 - x0 < _MIN_FIELD_SIZE or y1 - y0 < _MIN_FIELD_SIZE:
        return None
    return (x0, y0, x1, y1)


def extract_field_contrast(
    pdf: pikepdf.Pdf, pdf_path: Path
) -> FieldContrast | None:
    """Measure every AcroForm field's border and boundary contrast.

    Returns ``None`` when the document has no form fields at all.
    Mirrors pdfMax line ~1603.
    """
    acroform = pikepdf_helpers.get_dict(pdf.Root, "/AcroForm")
    if acroform is None:
        return None
    raw_fields = pikepdf_helpers.get_array(acroform, "/Fields")
    if raw_fields is None or len(raw_fields) == 0:
        return None
    fields = flatten_form_fields(raw_fields)
    if not fields:
        return None

    page_obj_to_num, annot_obj_to_page = build_page_lookups(pdf)
    page_images: dict[int, Image.Image | None] = {}
    findings: list[FieldContrastFinding] = []

    try:
        for field in fields:
            rect = _normalised_rect(field)
            if rect is None:
                continue
            x0, y0, x1, y1 = rect
            field_name, field_type = field_label(field)

            flags = pikepdf_helpers.get_int(field, "/Ff") or 0
            is_readonly = bool(flags & 1)
            exempt_reason = "readonly" if is_readonly else None

            mk = pikepdf_helpers.get_dict(field, "/MK")
            border_color, border_source = _field_border(field, mk)
            if border_color is None and exempt_reason is None:
                exempt_reason = "no_author_border"

            page_num = resolve_field_page(
                field, page_obj_to_num, annot_obj_to_page
            )
            if page_num not in page_images:
                page_images[page_num] = _render_page(pdf_path, page_num)
            img = page_images[page_num]
            page_height = page_height_points(pdf, page_num)

            field_bg, field_bg_source = _field_background(
                mk, img, page_height, (x0 + x1) / 2, (y0 + y1) / 2
            )
            page_bg = _page_background(img, page_height, rect)

            border_contrast = (
                None if border_color is None
                else _contrast(border_color, page_bg)
            )
            boundary_contrast = _contrast(field_bg, page_bg)

            findings.append(FieldContrastFinding(
                field_name=field_name,
                field_type=field_type,
                page=page_num,
                is_readonly=is_readonly,
                border_color=border_color,
                border_source=border_source,
                field_bg_color=field_bg,
                field_bg_source=field_bg_source,
                page_bg_color=page_bg,
                border_contrast=border_contrast,
                boundary_contrast=boundary_contrast,
                border_pass=(
                    None if border_contrast is None
                    else border_contrast >= _THRESHOLD
                ),
                boundary_pass=(
                    None if boundary_contrast is None
                    else boundary_contrast >= _THRESHOLD
                ),
                rect=rect,
                exempt_reason=exempt_reason,
            ))
    finally:
        for image in page_images.values():
            if image is not None:
                image.close()

    return FieldContrast(findings=findings, summary=summarise_fields(findings))


def _field_border(
    field: pikepdf.Dictionary, mk: pikepdf.Dictionary | None
) -> tuple[RgbColor | None, str | None]:
    """The field's border colour and where it was read from."""
    if mk is not None:
        try:
            declared = parse_color_array(mk["/BC"])
        except KeyError:
            declared = None
        if declared is not None:
            return declared, "/MK/BC"

    stream = ap_n_stream(field)
    if stream is not None:
        drawn = _border_from_appearance(stream)
        if drawn is not None:
            return drawn, "/AP/N stroke"
    return None, None


def _field_background(
    mk: pikepdf.Dictionary | None,
    img: Image.Image | None,
    page_height: float | None,
    center_x: float,
    center_y: float,
) -> tuple[RgbColor, str]:
    """The field's own background colour and where it was read from."""
    if mk is not None:
        try:
            declared = parse_color_array(mk["/BG"])
        except KeyError:
            declared = None
        if declared is not None:
            return declared, "/MK/BG"
    if img is not None and page_height is not None:
        return (
            _sample_point(img, page_height, center_x, center_y, radius=3),
            "sampled (field center)",
        )
    return _WHITE, "default white"


def _page_background(
    img: Image.Image | None,
    page_height: float | None,
    rect: tuple[float, float, float, float],
) -> RgbColor:
    """The page colour around a rect, from four points just outside it."""
    if img is None or page_height is None:
        return _WHITE
    x0, y0, x1, y1 = rect
    mid_x = (x0 + x1) / 2
    mid_y = (y0 + y1) / 2
    points = [
        (mid_x, y1 + _SAMPLE_OFFSET),
        (mid_x, y0 - _SAMPLE_OFFSET),
        (x0 - _SAMPLE_OFFSET, mid_y),
        (x1 + _SAMPLE_OFFSET, mid_y),
    ]
    return _median_of([
        _sample_point(img, page_height, x, y, radius=2) for x, y in points
    ])


def summarise_fields(
    findings: list[FieldContrastFinding],
) -> FieldContrastSummary:
    """Roll per-field measurements up into the counts the check reads."""
    judged = [f for f in findings if f.exempt_reason is None]
    return FieldContrastSummary(
        total_fields=len(findings),
        fields_with_borders=sum(
            1 for f in judged if f.border_color is not None
        ),
        border_fails=sum(1 for f in judged if f.border_pass is False),
        # A field whose fill matches the page is still findable when its
        # border passes, so a boundary failure only counts when the
        # border does not rescue it.
        boundary_only_fails=sum(
            1 for f in judged
            if f.boundary_pass is False and f.border_pass is not True
        ),
        boundary_info=sum(1 for f in judged if f.boundary_pass is False),
        exempt_count=sum(1 for f in findings if f.exempt_reason is not None),
    )


# ---------------------------------------------------------------------------
# Drawn graphics
# ---------------------------------------------------------------------------


@runtime_checkable
class _LayoutContainerLike(Protocol):
    """Structural view of a pdfminer layout container.

    Same reasoning as
    :class:`auto_a11y.pdf.audit.colors._LayoutContainerLike`: pdfminer
    parameterises ``LTContainer`` differently at every level, so
    narrowing on the Protocol is what keeps the recursive walk typed.
    """

    def __iter__(self) -> Iterator[LTItem]: ...


def _iter_layout(element: object) -> Iterator[LTItem]:
    """Yield every node of a pdfminer layout tree, depth first."""
    if isinstance(element, LTItem):
        yield element
    if isinstance(element, _LayoutContainerLike):
        for child in element:
            yield from _iter_layout(child)


def _pdfminer_color(color: Color | None) -> RgbColor | None:
    """Normalise pdfminer's several colour shapes to an RGB triple.

    pdfminer reports a colour as a float (grey) or as a 3- or 4-tuple
    (RGB or CMYK), depending on the colour space the graphic was drawn
    in. Anything else — an unexpected length, a non-numeric slot — is
    reported as unknown rather than guessed at, and the graphic is
    skipped.
    """
    if color is None:
        return None
    if isinstance(color, (int, float)):
        gray = float(color)
        return (gray, gray, gray)

    values = [_numeric(part) for part in color]
    numbers = [v for v in values if v is not None]
    if len(numbers) != len(values):
        return None
    if len(numbers) == 3:
        return (numbers[0], numbers[1], numbers[2])
    if len(numbers) == 4:
        cyan, magenta, yellow, black = numbers
        return (
            (1 - cyan) * (1 - black),
            (1 - magenta) * (1 - black),
            (1 - yellow) * (1 - black),
        )
    return None


def _rects_overlap(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> bool:
    """Whether two rects share any area."""
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _rect_inside(
    inner: tuple[float, float, float, float],
    outer: tuple[float, float, float, float],
) -> bool:
    """Whether *inner* belongs to *outer*'s region.

    A line has no area, so containment is meaningless for one; for those
    the test is whether the line runs along the region — its fixed
    coordinate inside the region's span, its extent overlapping. For a
    shape it is whether most of it (>30%) falls inside.
    """
    width = inner[2] - inner[0]
    height = inner[3] - inner[1]

    if width < 1 or height < 1:
        if height < 1:
            mid_y = (inner[1] + inner[3]) / 2
            if not outer[1] - 5 <= mid_y <= outer[3] + 5:
                return False
            return not (inner[2] < outer[0] - 20 or inner[0] > outer[2] + 20)
        mid_x = (inner[0] + inner[2]) / 2
        if not outer[0] - 5 <= mid_x <= outer[2] + 5:
            return False
        return not (inner[3] < outer[1] - 20 or inner[1] > outer[3] + 20)

    ix0 = max(inner[0], outer[0])
    iy0 = max(inner[1], outer[1])
    ix1 = min(inner[2], outer[2])
    iy1 = min(inner[3], outer[3])
    if ix0 >= ix1 or iy0 >= iy1:
        return False
    intersection = (ix1 - ix0) * (iy1 - iy0)
    return intersection / max(width * height, 0.001) > 0.3


def _form_field_rects(
    pdf: pikepdf.Pdf,
) -> dict[int, list[tuple[float, float, float, float]]]:
    """Widget rectangles per page index, for excluding field chrome.

    A field's own border is measured by :func:`extract_field_contrast`,
    which knows it is a field; the graphics pass would otherwise measure
    the same stroke again with none of that context.
    """
    acroform = pikepdf_helpers.get_dict(pdf.Root, "/AcroForm")
    if acroform is None:
        return {}
    raw_fields = pikepdf_helpers.get_array(acroform, "/Fields")
    if raw_fields is None or len(raw_fields) == 0:
        return {}

    page_obj_to_num, annot_obj_to_page = build_page_lookups(pdf)
    rects: dict[int, list[tuple[float, float, float, float]]] = {}
    for field in flatten_form_fields(raw_fields):
        rect = _normalised_rect(field)
        if rect is None:
            continue
        page_num = resolve_field_page(field, page_obj_to_num, annot_obj_to_page)
        rects.setdefault(page_num, []).append(rect)
    return rects


def _table_regions(
    elements: list[StructElement], pdf_path: Path
) -> dict[int, list[tuple[float, float, float, float]]]:
    """Approximate page regions occupied by tagged tables.

    A table has no geometry in the structure tree, so its region is
    inferred: collect the text of every ``Table`` element, find where
    pdfminer laid that text out, and take the box around the matches.
    Used only to classify a rule as a table border rather than a
    divider, so an approximate box is sufficient.
    """
    from pdfminer.layout import LTTextBox, LTTextLine

    snippets: dict[int, set[str]] = {}
    for element in elements:
        if element.resolved_tag != "Table":
            continue
        page_index = _element_page(element, elements)
        for descendant in _descendants(element, elements):
            text = (
                (descendant.text_content or "").strip()
                or (descendant.actual_text or "").strip()
            )
            if text:
                snippets.setdefault(page_index, set()).add(text[:50])

    if not snippets:
        return {}

    regions: dict[int, list[tuple[float, float, float, float]]] = {}
    laparams = LAParams(line_margin=0.5, word_margin=0.1, char_margin=2.0)
    try:
        for page_index, layout in enumerate(
            extract_pages(str(pdf_path), laparams=laparams)
        ):
            wanted = snippets.get(page_index)
            if not wanted:
                continue
            boxes: list[tuple[float, float, float, float]] = []
            for node in _iter_layout(layout):
                if not isinstance(node, (LTTextBox, LTTextLine)):
                    continue
                text = node.get_text().strip()[:50]
                if text and any(s in text or text in s for s in wanted):
                    boxes.append(node.bbox)
            if boxes:
                pad = 10
                regions.setdefault(page_index, []).append((
                    min(b[0] for b in boxes) - pad,
                    min(b[1] for b in boxes) - pad,
                    max(b[2] for b in boxes) + pad,
                    max(b[3] for b in boxes) + pad,
                ))
    except (OSError, ValueError, TypeError, AssertionError) as exc:
        logger.debug("Table region detection failed: %s", exc)
    return regions


def _descendants(
    element: StructElement, elements: list[StructElement]
) -> list[StructElement]:
    """Every descendant of *element*, breadth first, cycle-safe."""
    out: list[StructElement] = []
    seen: set[int] = set()
    stack = list(element.children_indices)
    while stack:
        index = stack.pop(0)
        if index in seen or index < 0 or index >= len(elements):
            continue
        seen.add(index)
        child = elements[index]
        out.append(child)
        stack.extend(child.children_indices)
    return out


def _element_page(
    element: StructElement, elements: list[StructElement]
) -> int:
    """The page index an element's content sits on; 0 when unknown."""
    for candidate in [element, *_descendants(element, elements)]:
        for page_index in candidate.mcid_page_map.values():
            return page_index
    return 0


def _classify(
    node: LTItem,
    bbox: tuple[float, float, float, float],
    page_width: float,
    in_table: bool,
    is_filled: bool,
) -> str:
    """Name what kind of graphic this is, for reporting and counting."""
    if in_table:
        return "table_border"
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    if abs(height) < 2 and width > page_width * _DIVIDER_WIDTH_RATIO:
        return "divider_line"
    if isinstance(node, LTRect) and is_filled:
        return "chart_element"
    return "other_graphic"


def _graphic_color(
    node: LTCurve, is_filled: bool, is_stroked: bool
) -> tuple[RgbColor | None, str]:
    """The colour a graphic is drawn in, preferring the operative one."""
    fill = _pdfminer_color(node.non_stroking_color)
    stroke = _pdfminer_color(node.stroking_color)
    if is_filled and not is_stroked and fill is not None:
        return fill, "fill"
    if is_stroked and stroke is not None:
        return stroke, "stroke"
    if fill is not None:
        return fill, "fill"
    return stroke, "stroke"


def _graphic_background(
    img: Image.Image | None,
    page_width: float,
    page_height: float,
    bbox: tuple[float, float, float, float],
) -> RgbColor:
    """The page colour around a graphic, sampled along its open sides."""
    if img is None:
        return _WHITE
    x0, y0, x1, y1 = bbox
    width = x1 - x0
    height = y1 - y0
    mid_x, mid_y = (x0 + x1) / 2, (y0 + y1) / 2

    if height < _LINE_THICKNESS:
        points = [(mid_x, y1 + _SAMPLE_OFFSET), (mid_x, y0 - _SAMPLE_OFFSET)]
    elif width < _LINE_THICKNESS:
        points = [(x0 - _SAMPLE_OFFSET, mid_y), (x1 + _SAMPLE_OFFSET, mid_y)]
    else:
        points = [
            (mid_x, y1 + _SAMPLE_OFFSET),
            (mid_x, y0 - _SAMPLE_OFFSET),
            (x0 - _SAMPLE_OFFSET, mid_y),
            (x1 + _SAMPLE_OFFSET, mid_y),
        ]

    sampled = [
        _sample_point(img, page_height, x, y, radius=2)
        for x, y in points
        if 0 <= x <= page_width and 0 <= y <= page_height
    ]
    if not sampled:
        return _WHITE
    return (
        sum(s[0] for s in sampled) / len(sampled),
        sum(s[1] for s in sampled) / len(sampled),
        sum(s[2] for s in sampled) / len(sampled),
    )


def _is_page_frame(
    bbox: tuple[float, float, float, float],
    page_width: float,
    page_height: float,
) -> bool:
    """Whether a graphic is the page's own border rather than content."""
    x0, y0, x1, y1 = bbox
    edges = 0
    if x0 < _PAGE_EDGE_MARGIN:
        edges += 1
    if y0 < _PAGE_EDGE_MARGIN:
        edges += 1
    if x1 > page_width - _PAGE_EDGE_MARGIN:
        edges += 1
    if y1 > page_height - _PAGE_EDGE_MARGIN:
        edges += 1
    return edges >= _PAGE_FRAME_EDGES


def extract_graphical_contrast(
    pdf_path: Path,
    pdf: pikepdf.Pdf,
    elements: list[StructElement],
    *,
    max_pages: int = _MAX_GRAPHIC_PAGES,
) -> GraphicContrast:
    """Measure drawn graphics against the page behind them.

    Mirrors pdfMax line ~2077. Covers the first *max_pages* pages; a
    document's graphical vocabulary repeats, so the cap costs coverage
    of instances rather than of kinds.
    """
    regions = _table_regions(elements, pdf_path)
    field_rects = _form_field_rects(pdf)
    page_images: dict[int, Image.Image | None] = {}
    findings: list[GraphicFinding] = []

    laparams = LAParams(line_margin=0.5, word_margin=0.1, char_margin=2.0)
    try:
        for page_index, layout in enumerate(
            extract_pages(str(pdf_path), laparams=laparams)
        ):
            if page_index >= max_pages:
                break
            graphics = [
                node for node in _iter_layout(layout)
                if isinstance(node, (LTLine, LTRect, LTCurve))
            ]
            if not graphics:
                continue

            if page_index not in page_images:
                page_images[page_index] = _render_page(pdf_path, page_index)
            img = page_images[page_index]
            page_width = float(layout.width)
            page_height = float(layout.height)

            for node in graphics:
                finding = _measure_graphic(
                    node,
                    img=img,
                    page_index=page_index,
                    page_width=page_width,
                    page_height=page_height,
                    field_rects=field_rects.get(page_index, []),
                    table_regions=regions.get(page_index, []),
                )
                if finding is not None:
                    findings.append(finding)
    except (OSError, ValueError, TypeError, AssertionError) as exc:
        # A layout engine that will not parse tells us nothing about
        # contrast; the audit reports what it did measure instead of
        # failing outright.
        logger.debug("Graphical contrast scan stopped early: %s", exc)
    finally:
        for image in page_images.values():
            if image is not None:
                image.close()

    return GraphicContrast(
        findings=findings, summary=summarise_graphics(findings)
    )


def _measure_graphic(
    node: LTCurve,
    *,
    img: Image.Image | None,
    page_index: int,
    page_width: float,
    page_height: float,
    field_rects: list[tuple[float, float, float, float]],
    table_regions: list[tuple[float, float, float, float]],
) -> GraphicFinding | None:
    """Measure one graphic, or ``None`` when it is out of scope."""
    bbox = (
        float(node.bbox[0]), float(node.bbox[1]),
        float(node.bbox[2]), float(node.bbox[3]),
    )
    linewidth = float(node.linewidth)
    if linewidth < _MIN_LINEWIDTH:
        return None
    if _is_page_frame(bbox, page_width, page_height):
        return None
    if any(_rects_overlap(bbox, rect) for rect in field_rects):
        return None

    in_table = any(_rect_inside(bbox, region) for region in table_regions)
    is_stroked = bool(node.stroke)
    is_filled = bool(node.fill)
    color, source = _graphic_color(node, is_filled, is_stroked)
    if color is None:
        return None

    background = _graphic_background(img, page_width, page_height, bbox)
    ratio = _contrast(color, background)
    if ratio is None:
        ratio = 1.0
    category = _classify(node, bbox, page_width, in_table, is_filled)
    label = category.replace("_", " ").title()
    return GraphicFinding(
        category=category,
        page=page_index + 1,
        bbox=(
            round(bbox[0], 1), round(bbox[1], 1),
            round(bbox[2], 1), round(bbox[3], 1),
        ),
        element_color=color,
        color_source=source,
        page_bg_color=background,
        contrast_ratio=round(ratio, 2),
        contrast_pass=ratio >= _THRESHOLD,
        linewidth=round(linewidth, 2),
        description=(
            f"{label} on page {page_index + 1}: {source} {_hex(color)}"
            f" on {_hex(background)}"
        ),
    )


def summarise_graphics(
    findings: list[GraphicFinding],
) -> GraphicContrastSummary:
    """Roll graphic measurements up into per-category counts."""
    def of(category: str) -> list[GraphicFinding]:
        return [f for f in findings if f.category == category]

    borders = of("table_border")
    dividers = of("divider_line")
    charts = of("chart_element")
    return GraphicContrastSummary(
        total_elements=len(findings),
        table_borders=len(borders),
        divider_lines=len(dividers),
        chart_elements=len(charts),
        other_graphics=len(of("other_graphic")),
        table_border_fails=sum(1 for f in borders if not f.contrast_pass),
        divider_fails=sum(1 for f in dividers if not f.contrast_pass),
        chart_element_fails=sum(1 for f in charts if not f.contrast_pass),
        total_fails=sum(1 for f in findings if not f.contrast_pass),
    )


def collect_non_text_contrast(
    pdf: pikepdf.Pdf, pdf_path: Path, elements: list[StructElement]
) -> NonTextContrast:
    """Run both passes and bundle their output for the audit context."""
    return NonTextContrast(
        fields=extract_field_contrast(pdf, pdf_path),
        graphics=extract_graphical_contrast(pdf_path, pdf, elements),
    )
