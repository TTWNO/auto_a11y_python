"""PDF visual reading-order data collectors.

Ports five geometry-on-numbers helpers from pdfMax's
``python/checker/pdf_accessibility_audit.py`` (lines ~3248-3745):

* :func:`extract_visual_positions` — pdfminer text-block extraction with
  page-relative ``y_top`` (origin moved to top-left, increases downward).
* :func:`detect_columns` — horizontal clustering of blocks into one or
  more :class:`Column` ranges per page using a 15%-of-page-width gap
  threshold.
* :func:`match_elements_to_positions` — best-effort matcher from a
  :class:`StructElement` (carrying tag/text/MCIDs) to a visual block,
  using a two-pass leaf-then-container strategy and prefix/word-overlap
  scoring.
* :func:`compute_visual_reading_order` — sorts matched element positions
  into row bands (15-pt vertical tolerance) and reads columns left to
  right inside each band.
* :func:`compare_reading_orders` — Spearman-like rank-correlation of the
  PDF's stored structure order against the computed visual order, plus a
  list of nearby-pair inversions and a per-element annotation map.

The orchestrating ``build_visual_reading_order_report`` from pdfMax
(line 3748) is **not** ported here; that function returns a
``CheckResult`` plus an HTML section, both of which are check-module /
pipeline concerns and live in Phase 4 / Phase 5 instead.

Type-policy notes:

* pdfMax used dicts ``{"page", "x0", ..., "x_center", "y_top"}`` for
  visual blocks and matched positions. We replace those with two frozen
  dataclasses (:class:`VisualBlock`, :class:`ElementPosition`); the
  derived quantities ``y_top`` and ``x_center`` are exposed as
  ``@property`` so the visual coordinate system is computed in one
  place. ``y_top`` requires a page height — for blocks we substitute
  ``y_top = -y1`` so that "smaller y_top means higher on the page" still
  holds within a single page (the absolute origin doesn't matter for
  ordering). For matched element positions we re-compute ``y_top`` from
  the source block's ``y_top`` value at match time.

  *Wait — that's wrong.* ``y_top`` in pdfMax depends on
  ``page_height - y1`` so that visual order is "small y_top first". We
  replicate this faithfully by carrying the page height alongside the
  blocks (already returned as :class:`PageDimensions`) and computing
  ``y_top`` lazily on the property. For an :class:`ElementPosition` we
  store ``y_top`` directly because it's set at match time — we already
  know the block's page height.
* :class:`ReadingOrderMismatch` is a frozen dataclass keyed only on the
  two element indices; the original carried five additional descriptive
  fields ("first_text", "second_text", "first_pos", "second_pos",
  "page") that are presentation-layer concerns and belong in the Phase
  4 reading-order check module — not here.
* :func:`compare_reading_orders` returns ``dict[int, str]`` for
  annotations — a per-element human-readable note ("OK" or
  "MISMATCH (struct=N, visual=M)") that the original buried inside a
  list of dicts. Mapping shape is preserved; presentation richness is
  deliberately reduced to fit within the typed dataclass surface.
* No ``_LayoutContainerLike`` Protocol is needed here — unlike
  ``fonts.py`` and ``colors.py``, :func:`extract_visual_positions` only
  iterates the top-level ``LTPage`` (whose ``__iter__`` is already
  typed as ``Iterator[LTComponent]``) and filters with ``isinstance``
  checks against ``LTTextBox`` / ``LTTextLine``. Recursion into nested
  containers isn't required, so the Protocol-narrowing pattern those
  modules use doesn't apply here.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pdfminer.high_level import extract_pages
from pdfminer.layout import LAParams, LTTextBox, LTTextLine
from pdfminer.pdftypes import PDFException
from pdfminer.psparser import PSException

from auto_a11y.pdf.audit.structure import StructElement

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PageDimensions:
    """Width and height of a single page, in PDF user-space points."""

    width: float
    height: float


@dataclass(frozen=True)
class VisualBlock:
    """A pdfminer-derived text block with visual position.

    ``y_top`` is *distance from the top of the page* — i.e.
    ``page_height - y1`` — so that smaller values are higher on the page.
    pdfMax computed this on-the-fly per block; we materialise it on the
    dataclass so callers don't have to thread page heights through.
    ``x_center`` is the horizontal midpoint of the bounding box.
    """

    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    y_top: float
    text: str

    @property
    def x_center(self) -> float:
        return (self.x0 + self.x1) / 2.0


@dataclass(frozen=True)
class Column:
    """A horizontal column on a page, identified by clustering visual blocks.

    ``block_indices`` are positions into the flat ``list[VisualBlock]``
    returned by :func:`extract_visual_positions` — useful for downstream
    consumers that want to know which blocks fell inside this column.
    """

    page: int
    x0: float
    x1: float
    block_indices: list[int] = field(default_factory=list[int])


@dataclass(frozen=True)
class ElementPosition:
    """The visual position assigned to a structure element after matching.

    Mirrors :class:`VisualBlock` minus the text — the source text lives
    on the structure element itself.
    """

    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    y_top: float

    @property
    def x_center(self) -> float:
        return (self.x0 + self.x1) / 2.0


@dataclass(frozen=True)
class ReadingOrderMismatch:
    """A single nearby-pair inversion between structure and visual order.

    ``struct_first`` is the element index that appears earlier in the
    structure tree; ``struct_second`` later. The inversion occurs because
    in the visual order, ``struct_second`` precedes ``struct_first``
    (i.e. ``visual_first`` and ``visual_second`` carry the same indices
    in swapped slots so callers can read the flipped sequence directly).
    """

    struct_first: int
    struct_second: int
    visual_first: int
    visual_second: int


# ---------------------------------------------------------------------------
# extract_visual_positions
# ---------------------------------------------------------------------------


def extract_visual_positions(
    pdf_path: Path,
    *,
    progress: Callable[[str, float], None] | None = None,
) -> tuple[list[VisualBlock], list[PageDimensions]]:
    """Extract pdfminer-derived text-block positions for every page.

    Returns ``(blocks, page_dims)``:

    * ``blocks`` — flat list across all pages, each carrying its
      0-indexed page number.
    * ``page_dims`` — one :class:`PageDimensions` per page in page
      order; ``page_dims[i]`` corresponds to page ``i``.

    Mirrors pdfMax's ``LAParams(line_margin=0.5, word_margin=0.1,
    char_margin=2.0, boxes_flow=None)`` and its ``len(text) > 1`` filter
    (drops single-character blocks). On parse error returns
    ``([], [])`` — pdfMax used a bare ``except`` for the same purpose.
    """
    blocks: list[VisualBlock] = []
    page_dims: list[PageDimensions] = []

    laparams = LAParams(
        line_margin=0.5,
        word_margin=0.1,
        char_margin=2.0,
        boxes_flow=None,
    )

    try:
        for page_idx, page_layout in enumerate(
            extract_pages(pdf_path, laparams=laparams)
        ):
            if progress is not None:
                # We can't know the page count up front (pdfminer streams
                # pages one at a time). Use a fading-asymptote fraction
                # so the bar still moves on every page without claiming
                # we're done; the caller's outer band caps it cleanly.
                approx_fraction = 1.0 - (1.0 / (1 + 0.05 * (page_idx + 1)))
                progress(
                    f"Reading order: parsing page {page_idx + 1}",
                    approx_fraction,
                )
            page_height = float(page_layout.height)
            page_width = float(page_layout.width)
            page_dims.append(PageDimensions(width=page_width, height=page_height))
            for element in page_layout:
                if not isinstance(element, (LTTextBox, LTTextLine)):
                    continue
                text = element.get_text().strip()
                if not text or len(text) <= 1:
                    continue
                x0 = float(element.x0)
                y0 = float(element.y0)
                x1 = float(element.x1)
                y1 = float(element.y1)
                blocks.append(
                    VisualBlock(
                        page=page_idx,
                        x0=x0,
                        y0=y0,
                        x1=x1,
                        y1=y1,
                        y_top=page_height - y1,
                        text=text,
                    )
                )
    # Mirror pdfMax's defensive swallow — malformed PDFs degrade
    # gracefully. We narrow the catch-all to the specific pdfminer /
    # IO / value-coercion failures that can actually surface.
    except (PSException, PDFException, OSError, ValueError, TypeError, AssertionError) as exc:
        logger.warning("pdfminer failed to extract visual positions from %s: %s", pdf_path, exc)
        return [], []

    return blocks, page_dims


# ---------------------------------------------------------------------------
# detect_columns
# ---------------------------------------------------------------------------


# pdfMax uses 15% of page width as the gap threshold for splitting columns.
_COLUMN_GAP_FRACTION: float = 0.15


def detect_columns(blocks: list[VisualBlock], page_width: float) -> list[Column]:
    """Cluster blocks by horizontal x-center to detect a column layout.

    Algorithm (mirrors pdfMax):

    1. Take the unique sorted x-centers of all blocks (rounded to int).
    2. Find consecutive-x-center gaps larger than ``page_width *
       0.15`` — those are column boundaries.
    3. Build column ranges from ``[0, gap1, gap2, ..., page_width]``.

    Deviations from pdfMax:

    * pdfMax returned ``[(0, page_width)]`` for empty/zero-width input;
      we return ``[]`` instead — empty input is "no usable signal" and
      callers should treat the column list as advisory. (The downstream
      :func:`compute_visual_reading_order` already handles an empty
      column list by treating every element as column 0, so behaviour
      is equivalent.)
    * The original returned ``list[tuple[float, float]]``; we wrap each
      in a :class:`Column` and additionally record which block indices
      fell inside that column — extra information that pdfMax's caller
      had to recompute on the fly.
    """
    if not blocks or page_width <= 0:
        return []

    # Unique sorted x-centers. ``round`` returns an ``int`` here because
    # the second arg defaults to None — that matches pdfMax's
    # ``round(b["x_center"])`` exactly.
    x_centers = sorted({round(b.x_center) for b in blocks})
    if len(x_centers) < 2:
        # Single column spanning the whole page width.
        return _build_columns_from_boundaries(blocks, [0.0, page_width])

    gaps: list[float] = []
    for i in range(1, len(x_centers)):
        gap = x_centers[i] - x_centers[i - 1]
        if gap > page_width * _COLUMN_GAP_FRACTION:
            gaps.append((x_centers[i - 1] + x_centers[i]) / 2.0)

    boundaries: list[float] = [0.0] + gaps + [page_width]
    return _build_columns_from_boundaries(blocks, boundaries)


def _build_columns_from_boundaries(
    blocks: list[VisualBlock], boundaries: list[float]
) -> list[Column]:
    """Materialise :class:`Column`s from a list of column-boundary x values.

    Each block is assigned to the (single) column whose ``[x0, x1]``
    contains its ``x_center``. Blocks that miss every range — should
    not happen given boundaries cover ``[0, page_width]`` — fall into
    the first column.
    """
    columns: list[Column] = []
    # Group blocks by page (boundaries are global per-page concept; we
    # always emit columns per-page so multi-page docs don't smear
    # left/right-column membership across page boundaries).
    by_page: dict[int, list[tuple[int, VisualBlock]]] = {}
    for idx, b in enumerate(blocks):
        by_page.setdefault(b.page, []).append((idx, b))

    for page_num in sorted(by_page.keys()):
        page_blocks = by_page[page_num]
        for i in range(len(boundaries) - 1):
            x_min = boundaries[i]
            x_max = boundaries[i + 1]
            indices: list[int] = []
            for global_idx, block in page_blocks:
                if x_min <= block.x_center <= x_max:
                    indices.append(global_idx)
            columns.append(
                Column(
                    page=page_num,
                    x0=x_min,
                    x1=x_max,
                    block_indices=indices,
                )
            )

    return columns


# ---------------------------------------------------------------------------
# match_elements_to_positions
# ---------------------------------------------------------------------------


_WHITESPACE_RE = re.compile(r"\s+")
# Tags whose text spans a whole document/section and so should not be
# matched to a single visual block.
_SKIP_MATCHING_TAGS: frozenset[str] = frozenset({"Document", "Part", "Sect"})


def _normalize_ws(s: str) -> str:
    """Collapse runs of whitespace to a single space (pdfMax helper)."""
    return _WHITESPACE_RE.sub(" ", s).strip()


def _match_element_to_block(
    elem: StructElement, visual_blocks: list[VisualBlock]
) -> VisualBlock | None:
    """Find the best matching visual block for one structure element.

    Two scoring strategies (per pdfMax):

    1. **Prefix match** — the first 15 chars of either text appears as a
       substring of the other.
    2. **Word overlap** — for elements with 2+ words, at least 50% of
       element words appear in the block's words.

    Among matched blocks, the one with the largest word-set intersection
    wins. Returns ``None`` if no block matches.
    """
    text = elem.text_content or elem.alt_text or ""
    if not text or len(text.strip()) < 2:
        return None

    text_clean = _normalize_ws(text)[:40].lower()
    text_words = set(text_clean.split())

    best_match: VisualBlock | None = None
    best_score = 0

    for block in visual_blocks:
        block_text = _normalize_ws(block.text)[:80].lower()
        block_words = set(block_text.split())

        # Strategy 1: prefix substring match.
        matched = text_clean[:15] in block_text or block_text[:15] in text_clean

        # Strategy 2: word overlap (≥ 2 words, ≥ 50% covered).
        if not matched and len(text_words) >= 2:
            common = text_words & block_words
            if len(common) >= max(2, int(len(text_words) * 0.5)):
                matched = True

        if matched:
            overlap = len(text_words & block_words)
            if overlap > best_score:
                best_score = overlap
                best_match = block

    if best_match is not None and best_score >= 1:
        return best_match
    return None


def _block_to_position(block: VisualBlock) -> ElementPosition:
    """Pin a structure element's :class:`ElementPosition` to its matched block."""
    return ElementPosition(
        page=block.page,
        x0=block.x0,
        y0=block.y0,
        x1=block.x1,
        y1=block.y1,
        y_top=block.y_top,
    )


def match_elements_to_positions(
    elements: list[StructElement],
    visual_blocks: list[VisualBlock],
) -> dict[int, ElementPosition]:
    """Match each structure element to a visual block's position.

    Two-pass approach (mirrors pdfMax):

    1. Leaf elements / elements with direct MCIDs are matched first
       (highest precision).
    2. Container elements (no MCIDs but with children) are matched
       afterwards using their aggregated ``text_content``.

    Document/Part/Sect elements are never matched — their text spans the
    whole page or document and is too broad for visual location.

    Returns a ``{element.index: ElementPosition}`` map; elements with no
    match are absent from the result.
    """
    positions: dict[int, ElementPosition] = {}
    containers: list[StructElement] = []

    # Pass 1: leaf elements + elements with direct MCIDs.
    for elem in elements:
        if elem.resolved_tag in _SKIP_MATCHING_TAGS:
            continue
        if not elem.mcids and elem.children_indices:
            containers.append(elem)
            continue

        match = _match_element_to_block(elem, visual_blocks)
        if match is not None:
            positions[elem.index] = _block_to_position(match)

    # Pass 2: containers not yet positioned.
    for elem in containers:
        if elem.index in positions:
            continue
        match = _match_element_to_block(elem, visual_blocks)
        if match is not None:
            positions[elem.index] = _block_to_position(match)

    return positions


# ---------------------------------------------------------------------------
# compute_visual_reading_order
# ---------------------------------------------------------------------------


# Vertical tolerance (in PDF points) for grouping elements into the same
# row band. ~5mm at 72dpi → 15pt — same value pdfMax uses.
_ROW_TOLERANCE: float = 15.0


@dataclass
class _OrderEntry:
    """Internal record threaded through the row-banding sort."""

    index: int
    y_top: float
    column: int


def compute_visual_reading_order(
    elements: list[StructElement],
    positions: dict[int, ElementPosition],
    columns: list[Column],
) -> list[int]:
    """Compute the expected reading order from positions + columns.

    Algorithm (mirrors pdfMax):

    1. Assign each positioned element to the column whose ``[x0, x1]``
       brackets its ``x_center``; default to column 0 if none match.
    2. Sort elements primarily by ``y_top`` (top to bottom).
    3. Group consecutive elements within ``ROW_TOLERANCE`` (15pt) into
       row bands.
    4. Inside each band, sort by ``(column, y_top)`` so the left column
       reads first within the band and ties on column break by exact y.

    Returns a list of element indices in visual order. Elements without
    a position entry are not included.

    The ``elements`` argument is not directly consulted — it's accepted
    for parity with pdfMax's signature and to give callers a single
    place to thread the structure list (useful for future extensions
    that want, e.g., to weight by tag).
    """
    del elements  # unused; see docstring.

    if not positions:
        return []

    entries: list[_OrderEntry] = []
    for idx, pos in positions.items():
        col_idx = 0
        for ci, column in enumerate(columns):
            if column.page != pos.page:
                continue
            if column.x0 <= pos.x_center <= column.x1:
                col_idx = ci
                break
        entries.append(_OrderEntry(index=idx, y_top=pos.y_top, column=col_idx))

    # Sort by y_top first (top to bottom).
    entries.sort(key=lambda e: e.y_top)

    # Group into row bands.
    row_bands: list[list[_OrderEntry]] = []
    current_band: list[_OrderEntry] = []
    band_y: float | None = None

    for entry in entries:
        if band_y is None or abs(entry.y_top - band_y) <= _ROW_TOLERANCE:
            current_band.append(entry)
            if band_y is None:
                band_y = entry.y_top
            else:
                # Use the topmost element as the band's reference y.
                band_y = min(band_y, entry.y_top)
        else:
            row_bands.append(current_band)
            current_band = [entry]
            band_y = entry.y_top
    if current_band:
        row_bands.append(current_band)

    # Within each row band, sort by (column, y_top).
    visual_order: list[int] = []
    for band in row_bands:
        band.sort(key=lambda e: (e.column, e.y_top))
        visual_order.extend(e.index for e in band)

    return visual_order


# ---------------------------------------------------------------------------
# compare_reading_orders
# ---------------------------------------------------------------------------


# Maximum window size for nearby-pair inversion counting (matches pdfMax).
_NEARBY_WINDOW: int = 5
# Annotation tolerance: |struct_pos - visual_rank| > this counts as MISMATCH.
_ANNOTATION_TOLERANCE: int = 2


def compare_reading_orders(
    elements: list[StructElement],
    structure_order_indices: list[int],
    visual_order_indices: list[int],
    positions: dict[int, ElementPosition],
) -> tuple[float, list[ReadingOrderMismatch], dict[int, str]]:
    """Compare structure order against visual reading order.

    Returns a 3-tuple ``(correlation, mismatches, annotations)``:

    * ``correlation`` — a Spearman-like score in ``[0.0, 1.0]``.
      ``1.0`` means structure and visual order agree on every adjacent
      pair within the 5-element nearby window pdfMax checks.
    * ``mismatches`` — list of :class:`ReadingOrderMismatch`, one per
      detected nearby-pair inversion.
    * ``annotations`` — ``{element.index: str}`` mapping each element
      that appears in both orders to a human-readable note. The note is
      ``"OK"`` when ``|struct_pos - visual_rank| <= 2`` and
      ``"MISMATCH (struct=N, visual=M)"`` otherwise — same threshold
      pdfMax used for its annotated-row table.

    pdfMax's algorithm has a subtlety worth pinning: it counts
    inversions only between elements whose structure-order positions are
    within 5 of each other (``min(i + 5, n)``). For documents where the
    structure tree is wildly out of order, pairs separated by 6+
    structure-positions are *not* counted as mismatches. This is
    intentional — the goal is to detect localised reading-order glitches
    (a misnested H2, a swapped paragraph) rather than wholesale
    rearrangements (which other audit checks already flag).

    ``elements`` is currently unused but kept in the signature for
    parity with pdfMax — future extensions may use it to enrich
    annotations with element tags.
    """
    del elements  # unused; see docstring.

    if not visual_order_indices or not structure_order_indices:
        return 1.0, [], {}

    struct_rank: dict[int, int] = {idx: rank for rank, idx in enumerate(structure_order_indices)}
    visual_rank: dict[int, int] = {idx: rank for rank, idx in enumerate(visual_order_indices)}

    common = set(struct_rank.keys()) & set(visual_rank.keys())
    if len(common) < 2:
        return 1.0, [], {}

    # Sort common elements by their structure rank — that's the order in
    # which we walk pairs.
    common_list = sorted(common, key=lambda x: struct_rank[x])
    n = len(common_list)

    mismatches: list[ReadingOrderMismatch] = []
    inversions = 0
    for i in range(n):
        for j in range(i + 1, min(i + _NEARBY_WINDOW, n)):
            idx_i = common_list[i]
            idx_j = common_list[j]
            if visual_rank[idx_i] > visual_rank[idx_j]:
                inversions += 1
                mismatches.append(
                    ReadingOrderMismatch(
                        struct_first=idx_i,
                        struct_second=idx_j,
                        visual_first=idx_j,
                        visual_second=idx_i,
                    )
                )

    # pdfMax's correlation formula: 1 - (inversions / (n * 5 / 2)).
    # n*min(n,5)/2 collapses to n*5/2 once n ≥ 5; for smaller n the
    # window itself shrinks and the divisor becomes n*(n-1)/2-like.
    max_inversions = n * min(n, _NEARBY_WINDOW) / 2.0
    if max_inversions > 0:
        correlation = 1.0 - (inversions / max_inversions)
    else:
        correlation = 1.0

    annotations: dict[int, str] = {}
    for struct_pos, idx in enumerate(structure_order_indices):
        if idx not in visual_rank or idx not in positions:
            continue
        v_rank = visual_rank[idx]
        if abs(struct_pos - v_rank) <= _ANNOTATION_TOLERANCE:
            annotations[idx] = "OK"
        else:
            annotations[idx] = f"MISMATCH (struct={struct_pos + 1}, visual={v_rank + 1})"

    return correlation, mismatches, annotations
