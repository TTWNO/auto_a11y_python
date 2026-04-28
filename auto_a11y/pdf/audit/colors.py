"""PDF text and form-field color extraction.

Ports two top-level functions from pdfMax's
``python/checker/pdf_accessibility_audit.py``:

* :func:`extract_text_colors` — uses pdfminer.six for character-level
  ``(foreground, background)`` color pairs, with backgrounds sampled from
  Ghostscript-rendered page rasters.
* :func:`extract_form_field_colors` — extracts text + background colors
  from form-widget /DA, /MK and /AP appearance metadata, falling back to
  rendered-page sampling when no explicit /MK /BG is present.

Both originals returned ``dict[tuple[...], dict[str, Any]]`` and used
``Any`` heavily for the inner records. We replace the inner dicts with
typed dataclasses (:class:`ColorPairInfo`, :class:`FgOnlyColorInfo`,
:class:`FormColorPairInfo`) so the zero-escape-hatch policy holds end to
end.

Type narrowing rationale:

* pdfminer.six ships ``py.typed``; ``LTPage`` is ``LTContainer[LTComponent]``.
  ``collect_chars_with_positions`` walks the layout via ``isinstance``
  checks against :class:`LTChar` and :class:`LTContainer`, so the
  recursion sees concrete types throughout.
* ``LTChar.graphicstate.ncolor`` is typed
  ``float | tuple[float, float, float] | tuple[float, float, float, float] | None``
  (pdfminer's ``Color`` Union plus ``Optional``); we narrow with
  ``isinstance`` rather than coercing blindly. The CMYK→RGB collapse
  matches pdfMax's behaviour.
* PIL's ``Image.getpixel`` returns ``float | tuple[int, ...] | None``;
  we narrow to a concrete RGB triple at the boundary.
* pikepdf's ``Object`` reads are funnelled through
  :mod:`auto_a11y.pdf.audit.pikepdf_helpers` whenever a Dictionary access
  is involved, matching the pattern used by structure.py and
  content_streams.py.
* Content-stream operands arrive as ``pikepdf.Object`` per the stub but
  may be ``int``, ``float``, ``Decimal`` or other primitives at runtime
  (pikepdf unwraps numeric primitives at the array boundary). We coerce
  through :func:`_coerce_numeric_operand` which checks ``isinstance``
  against the numeric tower before calling ``float()`` — no escape
  hatches.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Protocol, TypeAlias, runtime_checkable

import pikepdf
from pdfminer.high_level import extract_pages
from pdfminer.layout import LAParams, LTChar, LTItem
from pdfminer.pdftypes import PDFException
from pdfminer.psparser import PSException
from PIL import Image

from auto_a11y.pdf.audit import pikepdf_helpers
from auto_a11y.pdf.audit.ghostscript import render_page_to_png

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


#: Normalised RGB triple, components in [0.0, 1.0].
RgbColor: TypeAlias = tuple[float, float, float]


@dataclass
class ColorPairInfo:
    """Aggregate per ``(foreground, background)`` color pair.

    Replaces pdfMax's ``dict[str, Any]`` shape:
    ``{"count", "sample", "size_min", "size_max", "pages"}``.
    """

    count: int = 0
    sample: str = ""
    size_min: float = 999.0
    size_max: float = 0.0
    pages: set[int] = field(default_factory=set[int])


@dataclass
class FgOnlyColorInfo:
    """Aggregate per foreground color only (legacy backward-compat shape)."""

    count: int = 0
    sample: str = ""
    size_min: float = 999.0
    size_max: float = 0.0


@dataclass
class FormColorPairInfo:
    """Aggregate per ``(foreground, background)`` color pair for form widgets.

    Mirrors :class:`ColorPairInfo` but carries ``is_form=True`` so report
    consumers can distinguish form-widget colors from running-text colors.
    """

    count: int = 0
    sample: str = ""
    size_min: float = 999.0
    size_max: float = 0.0
    pages: set[int] = field(default_factory=set[int])
    is_form: bool = True


# A single character entry collected from the layout walk.
@dataclass
class CharEntry:
    fg: RgbColor
    char: str
    size: float
    x0: float
    y0: float
    x1: float
    y1: float


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# 1:1 with PDF user-space points — keeps coordinate math trivial.
_RENDER_DPI = 72

# Snap RGB components to nearest 1/50 (i.e. 0.02). Matches pdfMax's
# noise-reduction quantisation for raster background samples.
_QUANT = 50

_WHITE: RgbColor = (1.0, 1.0, 1.0)
_BLACK: RgbColor = (0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# Helpers — color normalisation + raster sampling
# ---------------------------------------------------------------------------


def quantize_rgb(rgb: RgbColor) -> RgbColor:
    """Snap each component to the nearest 1/50 (0.02) increment."""
    return (
        round(rgb[0] * _QUANT) / _QUANT,
        round(rgb[1] * _QUANT) / _QUANT,
        round(rgb[2] * _QUANT) / _QUANT,
    )


def cmyk_to_rgb(c: float, m: float, y: float, k: float) -> RgbColor:
    """Rough CMYK→RGB collapse used throughout pdfMax (no ICC profile)."""
    return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))


def ncolor_to_rgb(
    color: float | tuple[float, float, float] | tuple[float, float, float, float] | None,
) -> RgbColor | None:
    """Normalise pdfminer's graphic-state non-stroking color to RGB.

    ``ncolor`` is typed in pdfminer as
    ``Color = float | (r,g,b) | (c,m,y,k)``, optional. We narrow each
    branch explicitly. Bool is a Python ``int`` subclass and would
    pass ``isinstance(_, float)`` via the numeric tower; rejecting it
    here is defensive — pdfminer never produces booleans for ncolor.
    """
    if color is None:
        return None
    if isinstance(color, bool):
        return None
    if isinstance(color, (int, float)):
        g = float(color)
        return (g, g, g)
    # tuple branch: 3-tuple = RGB, 4-tuple = CMYK.
    # We bind ``vals`` as ``tuple[float, ...]`` so that pyright (which
    # narrows the tuple union) and ty (which doesn't fully refine via
    # ``len(_) == 4``) both accept the indexed reads.
    vals: tuple[float, ...] = tuple(float(v) for v in color)
    if len(vals) == 3:
        return (vals[0], vals[1], vals[2])
    if len(vals) == 4:
        return cmyk_to_rgb(vals[0], vals[1], vals[2], vals[3])
    return None


def _pixel_to_rgb(pixel: float | tuple[int, ...] | None) -> tuple[int, int, int] | None:
    """Narrow PIL.Image.getpixel's return to a concrete (R, G, B) triple.

    PIL's stub union accommodates 'L' (float), 'RGB'/'RGBA' (tuple), and
    error returns (None). We only ever call this on RGB-mode images, so
    the tuple branch is the fast path; the others map to ``None`` and
    callers fall back to the page-default white.
    """
    if pixel is None or isinstance(pixel, float):
        return None
    # int included for 'L' mode under some Pillow versions; reject.
    if isinstance(pixel, int):
        return None
    if len(pixel) < 3:
        return None
    return (int(pixel[0]), int(pixel[1]), int(pixel[2]))


def _median_rgb(samples: list[tuple[int, int, int]]) -> RgbColor:
    """Median per channel, rescaled to [0.0, 1.0]."""
    r_vals = sorted(p[0] for p in samples)
    g_vals = sorted(p[1] for p in samples)
    b_vals = sorted(p[2] for p in samples)
    mid = len(samples) // 2
    return (r_vals[mid] / 255.0, g_vals[mid] / 255.0, b_vals[mid] / 255.0)


def sample_background(
    img: Image.Image,
    img_w: int,
    img_h: int,
    page_height: float,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
) -> RgbColor:
    """Sample background color from a rendered page just above a glyph.

    PDF coordinates have origin bottom-left; the rendered raster has
    origin top-left, so y is flipped. The sample point is offset above
    the character's top edge by 30% of its height to avoid landing on
    the glyph itself. A 3x3 neighbourhood is averaged (median per
    channel) to suppress single-pixel noise.

    Mirrors pdfMax's ``sample_background`` byte-for-byte.
    """
    char_height = y1 - y0
    sample_y_pdf = y1 + char_height * 0.3
    sample_x_pdf = (x0 + x1) / 2

    px = int(sample_x_pdf)
    py = int(page_height - sample_y_pdf)
    px = max(0, min(px, img_w - 1))
    py = max(0, min(py, img_h - 1))

    samples: list[tuple[int, int, int]] = []
    for dx in range(-1, 2):
        for dy in range(-1, 2):
            sx = max(0, min(px + dx, img_w - 1))
            sy = max(0, min(py + dy, img_h - 1))
            rgb = _pixel_to_rgb(img.getpixel((sx, sy)))
            if rgb is not None:
                samples.append(rgb)

    if not samples:
        return _WHITE
    return _median_rgb(samples)


def _render_page_image(
    pdf_path: Path,
    page_num: int,
    *,
    gs_path_override: str | None,
) -> Image.Image | None:
    """Render a page via Ghostscript and load it as an RGB PIL image.

    Returns ``None`` when Ghostscript fails or the PNG can't be parsed.
    Mirrors the Phase 2 helper inside pdfMax's ``extract_text_colors``,
    but routes through :func:`auto_a11y.pdf.audit.ghostscript.render_page_to_png`
    rather than calling the gs subprocess directly.
    """
    try:
        png_bytes = render_page_to_png(
            pdf_path,
            page_num,
            dpi=_RENDER_DPI,
            gs_path_override=gs_path_override,
        )
    except (OSError, ValueError, TypeError):
        return None
    if png_bytes is None:
        return None
    try:
        return Image.open(BytesIO(png_bytes)).convert("RGB")
    except (OSError, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# pdfminer layout walker
# ---------------------------------------------------------------------------


@runtime_checkable
class _LayoutContainerLike(Protocol):
    """Structural view of pdfminer.layout containers.

    pdfminer parameterises :class:`LTContainer` as
    ``LTContainer[LTItemT]`` with a different bound at every level —
    ``LTPage`` holds ``LTComponent``, ``LTTextLine`` holds
    ``LTChar | LTAnno``, and so on. A bare ``isinstance(_, LTContainer)``
    narrowing yields ``LTContainer[Unknown]`` under pyright strict, which
    leaks into the recursive walk's argument types.

    This Protocol captures the only thing we actually want: a
    layout-tree node we can iterate, with each element typed as
    ``LTItem``. ``LTItem`` is the common base for every layout element
    pdfminer emits (containers, characters, annotations, lines), so it
    is sound to recurse on it.

    The ``runtime_checkable`` decorator lets us use ``isinstance`` to
    narrow against the Protocol at runtime (Python checks for the
    method's presence). Combined with an explicit ``LTContainer``
    isinstance check, this gives us a typed iteration without leaking
    ``Unknown`` types to pyright strict.
    """

    def __iter__(self) -> Iterator[LTItem]: ...


def collect_chars_with_positions(
    element: object,
    chars: list[CharEntry],
) -> None:
    """Recursively collect character + position data from a pdfminer layout.

    ``element`` is intentionally typed ``object`` for the inner recursion —
    pdfminer's layout tree is heterogeneous (``LTPage`` contains
    ``LTTextBoxHorizontal`` which contains ``LTTextLineHorizontal`` which
    contains ``LTChar`` plus ``LTAnno`` separators). ``isinstance`` is
    the only sound narrowing strategy at the LTChar leaf; for the
    container branch we narrow via :class:`_LayoutContainerLike` so the
    iteration is typed.
    """
    if isinstance(element, LTChar):
        try:
            fg = ncolor_to_rgb(element.graphicstate.ncolor)
        except (AttributeError, TypeError, ValueError):
            return
        if fg is None:
            return
        try:
            chars.append(
                CharEntry(
                    fg=fg,
                    char=element.get_text(),
                    size=float(element.size),
                    x0=float(element.x0),
                    y0=float(element.y0),
                    x1=float(element.x1),
                    y1=float(element.y1),
                )
            )
        except (AttributeError, TypeError, ValueError):
            return
        return

    # Narrowing the container branch via the runtime-checkable Protocol
    # gives pyright the Iterator[LTItem] signature it needs. The bare
    # ``isinstance(_, LTContainer)`` check would yield
    # LTContainer[Unknown] (the TypeVar can't be inferred at runtime).
    # The Protocol membership narrowing is sound here because every
    # pdfminer layout container exposes ``__iter__`` and yields
    # LTItem subclasses.
    if isinstance(element, _LayoutContainerLike):
        for child in element:
            collect_chars_with_positions(child, chars)


def _update_fg_only(
    fg_only: dict[RgbColor, FgOnlyColorInfo], entry: CharEntry
) -> None:
    """Update the legacy fg-only aggregate for a single character."""
    info = fg_only.get(entry.fg)
    if info is None:
        info = FgOnlyColorInfo()
        fg_only[entry.fg] = info
    info.count += 1
    if len(info.sample) < 100:
        info.sample += entry.char
    info.size_min = min(info.size_min, entry.size)
    info.size_max = max(info.size_max, entry.size)


# ---------------------------------------------------------------------------
# Public API: extract_text_colors
# ---------------------------------------------------------------------------


def extract_text_colors(
    pdf_path: Path,
    *,
    gs_path_override: str | None = None,
    progress: Callable[[str, float], None] | None = None,
) -> tuple[
    dict[tuple[RgbColor, RgbColor], ColorPairInfo],
    dict[RgbColor, FgOnlyColorInfo],
]:
    """Extract character-level (foreground, background) color pairs from a PDF.

    Returns ``(color_pairs, fg_only_colors)``:

    * ``color_pairs`` keyed by ``(fg_rgb, bg_rgb)`` tuples (each rgb tuple
      is normalised 0.0-1.0 floats). Backgrounds come from sampling a
      Ghostscript-rendered raster of each page; if rendering fails the
      pair is recorded against pure white ``(1.0, 1.0, 1.0)``.
    * ``fg_only_colors`` keyed by foreground rgb only. Provided for
      backward compatibility with pdfMax consumers that pre-date the
      background detection pass.

    ``gs_path_override`` lets callers (notably tests) point at an
    explicit Ghostscript binary; passing ``None`` uses
    :func:`auto_a11y.pdf.audit.ghostscript.detect_ghostscript`'s cached
    auto-detection.
    """
    fg_only: dict[RgbColor, FgOnlyColorInfo] = {}
    page_chars: list[tuple[int, float, list[CharEntry]]] = []

    laparams = LAParams(
        line_margin=0.5,
        word_margin=0.1,
        char_margin=2.0,
        boxes_flow=0.5,
    )

    try:
        for page_num, page_layout in enumerate(
            extract_pages(pdf_path, laparams=laparams)
        ):
            chars: list[CharEntry] = []
            collect_chars_with_positions(page_layout, chars)
            page_chars.append((page_num, float(page_layout.height), chars))
            for entry in chars:
                _update_fg_only(fg_only, entry)
    # pdfminer raises PSException / PDFException for malformed PDFs;
    # OSError covers I/O failures, the rest are defensive against
    # garbage operands inside the layout walker. We mirror pdfMax's
    # broad swallow but log so the failure is visible.
    except (PSException, PDFException, OSError, ValueError, TypeError, AssertionError) as exc:
        logger.warning("pdfminer failed to parse %s: %s", pdf_path, exc)
        return {}, {}

    if not page_chars:
        return {}, fg_only

    # Phase 2: render each page that has at least one char. This is the
    # long pole on multi-page PDFs (one Ghostscript subprocess per page),
    # so we tick the progress callback per page.
    pages_with_chars = [
        (pn, ph, ch) for (pn, ph, ch) in page_chars if ch
    ]
    total_renders = max(1, len(pages_with_chars))
    page_images: dict[int, Image.Image | None] = {}
    for idx, (page_num, _page_height, _chars) in enumerate(pages_with_chars):
        if progress is not None:
            progress(
                f"Extracting colours: page {idx + 1} of {total_renders}",
                idx / total_renders,
            )
        if page_num not in page_images:
            page_images[page_num] = _render_page_image(
                pdf_path, page_num, gs_path_override=gs_path_override
            )
    if progress is not None:
        progress("Sampling backgrounds", 1.0)

    # Phase 3: pair each character with its sampled background.
    color_pairs: dict[tuple[RgbColor, RgbColor], ColorPairInfo] = {}
    for page_num, page_height, chars in page_chars:
        img = page_images.get(page_num)
        img_w = img.width if img is not None else 0
        img_h = img.height if img is not None else 0

        for entry in chars:
            if img is not None:
                raw_bg = sample_background(
                    img, img_w, img_h, page_height,
                    entry.x0, entry.y0, entry.x1, entry.y1,
                )
                bg = quantize_rgb(raw_bg)
            else:
                bg = _WHITE

            pair_key = (entry.fg, bg)
            info = color_pairs.get(pair_key)
            if info is None:
                info = ColorPairInfo()
                color_pairs[pair_key] = info
            info.count += 1
            if len(info.sample) < 100:
                info.sample += entry.char
            info.size_min = min(info.size_min, entry.size)
            info.size_max = max(info.size_max, entry.size)
            info.pages.add(page_num + 1)

    for img in page_images.values():
        if img is not None:
            img.close()

    return color_pairs, fg_only


# ---------------------------------------------------------------------------
# Form-field colour helpers
# ---------------------------------------------------------------------------


def parse_da_string(da_str: str) -> tuple[RgbColor | None, float | None]:
    """Parse a /DA (Default Appearance) string for fill colour and font size.

    /DA strings are tiny PDF content streams, e.g. ``"0 0 0 rg /Helv 12 Tf"``.
    We tokenise on whitespace and look for ``rg`` (RGB), ``g`` (grey),
    ``k`` (CMYK), ``Tf`` (font + size). Anything else is ignored. The
    behaviour matches pdfMax's ``parse_da_string`` exactly.
    """
    if not da_str:
        return None, None

    tokens = da_str.split()
    fg_color: RgbColor | None = None
    font_size: float | None = None

    for i, tok in enumerate(tokens):
        if tok == "rg" and i >= 3:
            try:
                fg_color = (
                    float(tokens[i - 3]),
                    float(tokens[i - 2]),
                    float(tokens[i - 1]),
                )
            except (ValueError, IndexError):
                pass
        elif tok == "g" and i >= 1:
            try:
                gray = float(tokens[i - 1])
                fg_color = (gray, gray, gray)
            except (ValueError, IndexError):
                pass
        elif tok == "k" and i >= 4:
            try:
                fg_color = cmyk_to_rgb(
                    float(tokens[i - 4]),
                    float(tokens[i - 3]),
                    float(tokens[i - 2]),
                    float(tokens[i - 1]),
                )
            except (ValueError, IndexError):
                pass
        elif tok == "Tf" and i >= 2:
            try:
                font_size = float(tokens[i - 1])
            except (ValueError, IndexError):
                pass

    return fg_color, font_size


def parse_color_array(arr: pikepdf.Object | None) -> RgbColor | None:
    """Parse a PDF colour array (e.g. /MK /BG, /MK /BC) to an RGB tuple.

    Accepts 1-element grey, 3-element RGB, 4-element CMYK arrays. Returns
    ``None`` for any other length or non-numeric content. Each entry is
    coerced through :func:`_coerce_numeric_operand` so non-numeric
    contents (Names, Strings, …) reject cleanly.
    """
    if arr is None:
        return None
    if not isinstance(arr, pikepdf.Array):
        return None
    vals: list[float] = []
    for i in range(len(arr)):
        v = _coerce_numeric_operand(arr[i])
        if v is None:
            return None
        vals.append(v)

    if len(vals) == 1:
        return (vals[0], vals[0], vals[0])
    if len(vals) == 3:
        return (vals[0], vals[1], vals[2])
    if len(vals) == 4:
        return cmyk_to_rgb(vals[0], vals[1], vals[2], vals[3])
    return None


def _ap_text_operands_snippet(operands: list[object]) -> str:
    """Extract a short text snippet from a Tj/TJ operand list.

    Tj operands are a single ``pikepdf.String`` (rendered text); TJ
    operands are an ``Array`` of alternating strings and positioning
    numbers. Either way we collect the visible-text portion and clip
    to 30 characters.
    """
    if not operands:
        return ""
    first = operands[0]
    if isinstance(first, (pikepdf.String, str)):
        try:
            return str(first)[:30]
        except (UnicodeDecodeError, ValueError, TypeError):
            return ""
    if isinstance(first, pikepdf.Array):
        parts: list[str] = []
        for i in range(len(first)):
            item = first[i]
            if isinstance(item, (pikepdf.String, str)):
                try:
                    parts.append(str(item))
                except (UnicodeDecodeError, ValueError, TypeError):
                    pass
        return "".join(parts)[:30]
    return ""


@dataclass
class ApTextColorEntry:
    fg: RgbColor
    font_size: float
    snippet: str


def _coerce_numeric_operand(item: object) -> float | None:
    """Return a content-stream operand as ``float`` if it's numeric, else ``None``.

    pikepdf's stub types every operand as ``Object``, but at runtime
    numeric primitives are unwrapped: integer operands arrive as ``int``,
    real operands as ``Decimal``, and occasionally ``float``. ``bool`` is
    rejected explicitly because it inherits from ``int`` in Python and
    we don't want to silently read True/False as 1.0/0.0.
    """
    if isinstance(item, bool):
        return None
    if isinstance(item, (int, float, Decimal)):
        return float(item)
    return None


def parse_ap_stream_colors(ap_stream: pikepdf.Object) -> list[ApTextColorEntry]:
    """Parse an /AP /N appearance stream for text fill colours.

    Tracks the graphics state (fill colour, font size) and emits one
    entry per *distinct* fill colour seen at a text-show operator.
    Mirrors pdfMax's ``parse_ap_stream_colors``.
    """
    try:
        content = pikepdf.parse_content_stream(ap_stream)
    except (pikepdf.PdfError, ValueError, TypeError):
        return []

    fill_color: RgbColor = _BLACK
    font_size: float = 12.0
    results: list[ApTextColorEntry] = []
    seen: set[RgbColor] = set()

    for inst in content:
        try:
            op = str(inst.operator)
        except (UnicodeDecodeError, ValueError):
            continue
        # The stub types operands as ``_ObjectList`` whose elements are
        # ``pikepdf.Object``; at runtime numeric operands are bare
        # ``int``/``float``/``Decimal``. We materialise to a
        # list[object] so the type checker permits the isinstance
        # narrowings inside _coerce_numeric_operand.
        operands: list[object] = list(inst.operands)

        if op == "rg" and len(operands) >= 3:
            r = _coerce_numeric_operand(operands[0])
            g = _coerce_numeric_operand(operands[1])
            b = _coerce_numeric_operand(operands[2])
            if r is not None and g is not None and b is not None:
                fill_color = (r, g, b)
        elif op == "g" and len(operands) >= 1:
            gray = _coerce_numeric_operand(operands[0])
            if gray is not None:
                fill_color = (gray, gray, gray)
        elif op == "k" and len(operands) >= 4:
            c = _coerce_numeric_operand(operands[0])
            m = _coerce_numeric_operand(operands[1])
            y = _coerce_numeric_operand(operands[2])
            k = _coerce_numeric_operand(operands[3])
            if c is not None and m is not None and y is not None and k is not None:
                fill_color = cmyk_to_rgb(c, m, y, k)
        elif op == "Tf" and len(operands) >= 2:
            sz = _coerce_numeric_operand(operands[1])
            if sz is not None:
                font_size = sz
        elif op in ("Tj", "TJ", "'", '"'):
            color_key = (
                round(fill_color[0], 4),
                round(fill_color[1], 4),
                round(fill_color[2], 4),
            )
            if color_key not in seen:
                seen.add(color_key)
                snippet = _ap_text_operands_snippet(operands)
                results.append(
                    ApTextColorEntry(fg=fill_color, font_size=font_size, snippet=snippet)
                )

    return results


# ---------------------------------------------------------------------------
# Form-field walking
# ---------------------------------------------------------------------------


def _flatten_form_fields(raw_fields: pikepdf.Array) -> list[pikepdf.Dictionary]:
    """Flatten the AcroForm /Fields tree into terminal field dictionaries.

    Mirrors pdfMax's flatten loop: a node with /Kids and no /FT is an
    intermediate (logical group); a node with /FT is terminal. Defensive
    against fields that are not Dictionary-shaped.
    """
    flat: list[pikepdf.Dictionary] = []
    stack: list[pikepdf.Object] = []
    for i in range(len(raw_fields)):
        stack.append(raw_fields[i])
    while stack:
        node = stack.pop(0)
        if not isinstance(node, pikepdf.Dictionary):
            continue
        kids = pikepdf_helpers.get_array(node, "/Kids")
        ft = pikepdf_helpers.get_name(node, "/FT")
        if kids is not None and ft is None:
            for ki in range(len(kids)):
                stack.append(kids[ki])
        else:
            flat.append(node)
    return flat


_FT_LABELS: dict[str, str] = {
    "/Tx": "Text",
    "/Btn": "Button",
    "/Ch": "Dropdown",
    "/Sig": "Signature",
}


def _field_label(field_dict: pikepdf.Dictionary) -> tuple[str, str]:
    """Read ``(field_name, field_type_label)`` for diagnostic display."""
    name = pikepdf_helpers.get_string(field_dict, "/T") or "unnamed"
    ft = pikepdf_helpers.get_name(field_dict, "/FT")
    if ft is None:
        ft_label = "Field"
    else:
        ft_str = str(ft)
        ft_label = _FT_LABELS.get(ft_str, ft_str.lstrip("/") or "Field")
    return name, ft_label


def _field_da_string(
    field_dict: pikepdf.Dictionary, acro_da: str
) -> str:
    """Resolve the /DA string for a field, falling back to AcroForm-level /DA."""
    direct = pikepdf_helpers.get_string(field_dict, "/DA")
    if direct is not None:
        return direct
    return acro_da


def _resolve_field_page(
    field_dict: pikepdf.Dictionary,
    page_obj_to_num: dict[int, int],
    annot_obj_to_page: dict[int, int],
) -> int:
    """Determine the page index a field annotation lives on.

    Order of attempts: explicit /P → annot-membership scan → 0.
    """
    try:
        page_ref = field_dict["/P"]
    except KeyError:
        page_ref = None
    if page_ref is not None:
        idx = page_obj_to_num.get(id(page_ref))
        if idx is not None:
            return idx
    idx = annot_obj_to_page.get(id(field_dict))
    if idx is not None:
        return idx
    return 0


def _ap_n_stream(field_dict: pikepdf.Dictionary) -> pikepdf.Object | None:
    """Return the /AP /N stream if it's a stream (not a per-state Dictionary).

    Form fields can either store a single normal-appearance stream at
    /AP/N or a Dictionary keyed by appearance-state name. We only handle
    the stream case here — the dict case requires picking an active
    state, which pdfMax punts on.
    """
    ap = pikepdf_helpers.get_dict(field_dict, "/AP")
    if ap is None:
        return None
    try:
        n = ap["/N"]
    except KeyError:
        return None
    if isinstance(n, pikepdf.Dictionary):
        return None
    return n


def _build_page_lookups(
    pdf: pikepdf.Pdf,
) -> tuple[dict[int, int], dict[int, int]]:
    """Build ``id(page_obj) → index`` and ``id(annot) → page_index`` maps."""
    page_obj_to_num: dict[int, int] = {}
    for i, page in enumerate(pdf.pages):
        page_obj_to_num[id(page.obj)] = i

    annot_obj_to_page: dict[int, int] = {}
    for pg_num, page in enumerate(pdf.pages):
        try:
            annots_obj = page.obj["/Annots"]
        except KeyError:
            continue
        if not isinstance(annots_obj, pikepdf.Array):
            continue
        for ai in range(len(annots_obj)):
            annot = annots_obj[ai]
            try:
                annot_obj_to_page[id(annot)] = pg_num
            except (AttributeError, TypeError):
                pass
    return page_obj_to_num, annot_obj_to_page


def _add_form_color_pair(
    pairs: dict[tuple[RgbColor, RgbColor], FormColorPairInfo],
    fg: RgbColor,
    size: float,
    label: str,
    bg: RgbColor,
    page_num: int,
) -> None:
    """Record a single fg/bg pair in the form-color aggregate."""
    fg_q = quantize_rgb(fg)
    pair_key = (fg_q, bg)
    info = pairs.get(pair_key)
    if info is None:
        info = FormColorPairInfo()
        pairs[pair_key] = info
    info.count += 1
    if len(info.sample) < 100:
        if info.sample:
            info.sample += ", "
        info.sample += label
    info.size_min = min(info.size_min, size)
    info.size_max = max(info.size_max, size)
    info.pages.add(page_num + 1)


def _field_rect(field_dict: pikepdf.Dictionary) -> tuple[float, float, float, float] | None:
    """Read /Rect as ``(x0, y0, x1, y1)``; ``None`` if missing or malformed."""
    rect = pikepdf_helpers.get_array(field_dict, "/Rect")
    if rect is None or len(rect) < 4:
        return None
    x0 = _coerce_numeric_operand(rect[0])
    y0 = _coerce_numeric_operand(rect[1])
    x1 = _coerce_numeric_operand(rect[2])
    y1 = _coerce_numeric_operand(rect[3])
    if x0 is None or y0 is None or x1 is None or y1 is None:
        return None
    return (x0, y0, x1, y1)


def _page_height(pdf: pikepdf.Pdf, page_num: int) -> float | None:
    """Read the page height in PDF points from /MediaBox[3].

    Falls through to ``page.mediabox`` (which inherits via the page
    tree) if a direct /MediaBox isn't present on the page dictionary.
    """
    if page_num < 0 or page_num >= len(pdf.pages):
        return None
    page = pdf.pages[page_num]
    mediabox = pikepdf_helpers.get_array(page.obj, "/MediaBox")
    if mediabox is None or len(mediabox) < 4:
        # mediabox is a property that resolves the inherited box.
        try:
            inherited = page.mediabox
        except (AttributeError, KeyError):
            return None
        if len(inherited) < 4:
            return None
        return _coerce_numeric_operand(inherited[3])
    return _coerce_numeric_operand(mediabox[3])


# ---------------------------------------------------------------------------
# Public API: extract_form_field_colors
# ---------------------------------------------------------------------------


def extract_form_field_colors(
    pdf: pikepdf.Pdf,
    pdf_path: Path,
    *,
    gs_path_override: str | None = None,
) -> dict[tuple[RgbColor, RgbColor], FormColorPairInfo]:
    """Extract text & background colours from form-widget appearance metadata.

    Reads /DA (Default Appearance) for text colour and font size, /MK /BG
    for explicit widget background colour, and /AP /N appearance streams
    for the actual rendered text colours (which catches placeholders and
    custom-styled buttons that diverge from /DA). When no explicit /MK
    /BG is present, falls back to sampling a Ghostscript-rendered raster
    of the page at the field's rect.

    Returns ``{(fg_rgb, bg_rgb): FormColorPairInfo}``.
    """
    acroform = pikepdf_helpers.get_dict(pdf.Root, "/AcroForm")
    if acroform is None:
        return {}
    raw_fields = pikepdf_helpers.get_array(acroform, "/Fields")
    if raw_fields is None or len(raw_fields) == 0:
        return {}

    form_fields = _flatten_form_fields(raw_fields)
    if not form_fields:
        return {}

    acro_da = pikepdf_helpers.get_string(acroform, "/DA") or ""
    page_obj_to_num, annot_obj_to_page = _build_page_lookups(pdf)
    page_images: dict[int, Image.Image | None] = {}
    form_pairs: dict[tuple[RgbColor, RgbColor], FormColorPairInfo] = {}

    try:
        for field_dict in form_fields:
            field_name, ft_label = _field_label(field_dict)

            # --- Foreground entries: /DA + /AP /N (deduped) ----------
            fg_entries: list[tuple[RgbColor, float, str]] = []
            da_str = _field_da_string(field_dict, acro_da)
            da_fg, da_size = parse_da_string(da_str)
            if da_fg is not None:
                fg_entries.append(
                    (da_fg, da_size if da_size is not None else 12.0,
                     f"[Form: {field_name} ({ft_label})]")
                )

            n_stream = _ap_n_stream(field_dict)
            if n_stream is not None:
                ap_colors = parse_ap_stream_colors(n_stream)
                da_fg_q = quantize_rgb(da_fg) if da_fg is not None else None
                for ap_entry in ap_colors:
                    ap_fg_q = quantize_rgb(ap_entry.fg)
                    if ap_fg_q != da_fg_q:
                        snippet = ap_entry.snippet[:20]
                        label = f"[Form/AP: {field_name}"
                        if snippet:
                            label += f' "{snippet}"'
                        label += "]"
                        fg_entries.append((ap_entry.fg, ap_entry.font_size, label))

            if not fg_entries:
                continue

            # --- Background colour ------------------------------------
            bg_color: RgbColor | None = None
            mk = pikepdf_helpers.get_dict(field_dict, "/MK")
            if mk is not None:
                try:
                    bg_array = mk["/BG"]
                except KeyError:
                    bg_array = None
                bg_color = parse_color_array(bg_array)

            page_num = _resolve_field_page(field_dict, page_obj_to_num, annot_obj_to_page)

            if bg_color is None:
                bg_color = _sample_field_background(
                    pdf=pdf,
                    pdf_path=pdf_path,
                    field_dict=field_dict,
                    page_num=page_num,
                    page_images=page_images,
                    gs_path_override=gs_path_override,
                )

            if bg_color is None:
                bg_color = _WHITE

            for fg, size, label in fg_entries:
                _add_form_color_pair(form_pairs, fg, size, label, bg_color, page_num)
    finally:
        for img in page_images.values():
            if img is not None:
                img.close()

    return form_pairs


def _sample_field_background(
    *,
    pdf: pikepdf.Pdf,
    pdf_path: Path,
    field_dict: pikepdf.Dictionary,
    page_num: int,
    page_images: dict[int, Image.Image | None],
    gs_path_override: str | None,
) -> RgbColor | None:
    """Sample a field-area background from a Ghostscript-rendered raster.

    Returns ``None`` when the field has no rect, the page can't be
    rendered, or the page height is unknown — callers fall back to white.
    """
    rect = _field_rect(field_dict)
    if rect is None:
        return None
    x0, y0, x1, y1 = rect

    if page_num not in page_images:
        page_images[page_num] = _render_page_image(
            pdf_path, page_num, gs_path_override=gs_path_override
        )
    img = page_images.get(page_num)
    if img is None:
        return None

    page_height = _page_height(pdf, page_num)
    if page_height is None:
        return None

    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2
    half_h = (y1 - y0) / 2
    raw = sample_background(
        img, img.width, img.height, page_height,
        cx - 1, cy - half_h, cx + 1, cy + half_h,
    )
    return quantize_rgb(raw)
