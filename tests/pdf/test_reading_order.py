"""Tests for the PDF visual reading-order data collectors.

Most helpers operate on plain dataclasses (``VisualBlock``, ``Column``,
``ElementPosition``, ``StructElement``); those tests build the inputs by
hand. :func:`extract_visual_positions` is exercised against a tiny
synthetic PDF built with pikepdf, mirroring the pattern used by
``tests.pdf.test_fonts``.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Protocol

import pikepdf

from auto_a11y.pdf.audit.reading_order import (
    Column,
    ElementPosition,
    ReadingOrderMismatch,
    VisualBlock,
    compare_reading_orders,
    compute_visual_reading_order,
    detect_columns,
    extract_visual_positions,
    match_elements_to_positions,
)
from auto_a11y.pdf.audit.structure import StructElement


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StreamMaker(Protocol):
    """Typed view of ``pikepdf.Pdf.make_stream`` for single-arg calls."""

    def __call__(self, data: bytes) -> pikepdf.Stream: ...


def _typed_make_stream(pdf: pikepdf.Pdf, data: bytes) -> pikepdf.Stream:
    maker: _StreamMaker = getattr(pdf, "make_stream")
    return maker(data)


def _font_dict(base_font: str) -> pikepdf.Dictionary:
    return pikepdf.Dictionary(
        Type=pikepdf.Name("/Font"),
        Subtype=pikepdf.Name("/Type1"),
        BaseFont=pikepdf.Name("/" + base_font),
        Encoding=pikepdf.Name("/WinAnsiEncoding"),
    )


def _make_pdf(content: bytes, page_size: tuple[float, float] = (612, 792)) -> bytes:
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=page_size)
    font_entries: dict[str, pikepdf.Object] = {"/F1": _font_dict("Helvetica")}
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(font_entries))
    page.Contents = _typed_make_stream(pdf, content)
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


_TEST_PAGE_HEIGHT: float = 792.0


def _block(
    page: int,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    text: str,
    page_height: float = _TEST_PAGE_HEIGHT,
) -> VisualBlock:
    """Build a VisualBlock with y_top computed from the page height."""
    return VisualBlock(
        page=page,
        x0=x0,
        y0=y0,
        x1=x1,
        y1=y1,
        y_top=page_height - y1,
        text=text,
    )


def _pos(
    page: int,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    page_height: float = _TEST_PAGE_HEIGHT,
) -> ElementPosition:
    """Build an ElementPosition with y_top computed from the page height."""
    return ElementPosition(
        page=page,
        x0=x0,
        y0=y0,
        x1=x1,
        y1=y1,
        y_top=page_height - y1,
    )


def _struct_elem(
    index: int,
    *,
    tag: str = "P",
    text: str = "",
    alt: str | None = None,
    mcids: list[int] | None = None,
    children: list[int] | None = None,
    parent: int = -1,
) -> StructElement:
    """Build a minimal StructElement.

    The walker normally fills ``obj`` with the source pikepdf.Dictionary;
    for unit tests of the geometry helpers it suffices to supply an empty
    Dictionary — the helpers under test never inspect ``elem.obj``.
    """
    return StructElement(
        index=index,
        custom_tag="/" + tag,
        resolved_tag=tag,
        alt_text=alt,
        actual_text=None,
        lang=None,
        children_indices=list(children) if children else [],
        mcids=list(mcids) if mcids else [],
        parent_index=parent,
        obj=pikepdf.Dictionary(),
        text_content=text,
    )


# ---------------------------------------------------------------------------
# detect_columns
# ---------------------------------------------------------------------------


def test_detect_columns_empty_input_returns_empty() -> None:
    cols = detect_columns([], page_width=612.0)
    assert cols == []


def test_detect_columns_zero_page_width_returns_empty() -> None:
    """When page_width is missing/zero, mirror pdfMax's degenerate fallback.

    The original returned ``[(0, page_width)]`` (i.e. one column at width
    0) for both empty-block and zero-width cases. Our typed port treats
    these as 'no usable signal' and returns an empty list — see the
    module docstring on the deviation. The point is to detect the edge
    case explicitly.
    """
    # Some text at a single x-center → still no columns without a width.
    cols = detect_columns([_block(0, 50, 700, 100, 720, "x")], page_width=0.0)
    assert cols == []


def test_detect_columns_single_column() -> None:
    """All blocks clustered in one horizontal band → one Column."""
    blocks = [
        _block(0, 50, 700, 200, 720, "Line 1"),
        _block(0, 50, 680, 220, 690, "Line 2 longer"),
        _block(0, 50, 660, 210, 670, "Line 3"),
    ]
    cols = detect_columns(blocks, page_width=612.0)
    assert len(cols) == 1
    only = cols[0]
    assert only.page == 0
    assert only.x0 == 0.0
    assert only.x1 == 612.0
    assert sorted(only.block_indices) == [0, 1, 2]


def test_detect_columns_two_columns() -> None:
    """Blocks split between left and right halves → two Columns."""
    # x_centers: left ~125, right ~475 → gap > 15% of 612 (≥ 92pt).
    blocks = [
        _block(0, 50, 700, 200, 720, "L1"),    # x_center 125
        _block(0, 50, 680, 200, 690, "L2"),    # x_center 125
        _block(0, 400, 700, 550, 720, "R1"),   # x_center 475
        _block(0, 400, 680, 550, 690, "R2"),   # x_center 475
    ]
    cols = detect_columns(blocks, page_width=612.0)
    assert len(cols) == 2
    assert cols[0].x0 < cols[1].x0  # ordered left-to-right
    # Left block_indices should reference the left blocks, right the right.
    assert set(cols[0].block_indices) == {0, 1}
    assert set(cols[1].block_indices) == {2, 3}


def test_detect_columns_minor_x_jitter_stays_one_column() -> None:
    """Small x variations within a single column should NOT split it."""
    # All x_centers within ~10pt → below 15% × 612 ≈ 92pt threshold.
    blocks = [
        _block(0, 50, 700, 200, 720, "A"),
        _block(0, 55, 680, 205, 690, "B"),
        _block(0, 48, 660, 198, 670, "C"),
    ]
    cols = detect_columns(blocks, page_width=612.0)
    assert len(cols) == 1


# ---------------------------------------------------------------------------
# match_elements_to_positions
# ---------------------------------------------------------------------------


def test_match_elements_to_positions_empty() -> None:
    """No elements, no blocks → empty mapping."""
    assert match_elements_to_positions([], []) == {}


def test_match_elements_to_positions_skips_document_part_sect() -> None:
    """Document/Part/Sect containers should never be matched."""
    elements = [
        _struct_elem(0, tag="Document", text="The Document Title"),
        _struct_elem(1, tag="Part", text="Section A"),
        _struct_elem(2, tag="Sect", text="Subsection"),
    ]
    blocks = [
        _block(0, 50, 700, 250, 720, "The Document Title"),
        _block(0, 50, 680, 200, 690, "Section A"),
        _block(0, 50, 660, 210, 670, "Subsection"),
    ]
    positions = match_elements_to_positions(elements, blocks)
    assert positions == {}


def test_match_elements_to_positions_no_text_no_alt_no_match() -> None:
    """Elements with no text/alt and no MCIDs → no matches."""
    elements = [_struct_elem(0, tag="P", text="")]
    blocks = [_block(0, 50, 700, 200, 720, "anything")]
    positions = match_elements_to_positions(elements, blocks)
    assert positions == {}


def test_match_elements_to_positions_matches_by_text_overlap() -> None:
    """Element whose text overlaps a block's text gets that block's bbox."""
    elements = [
        _struct_elem(0, tag="P", text="Hello visual world example"),
    ]
    blocks = [
        _block(0, 50, 700, 250, 720, "Hello visual world example"),
        _block(0, 50, 680, 200, 690, "totally unrelated content"),
    ]
    positions = match_elements_to_positions(elements, blocks)
    assert 0 in positions
    pos = positions[0]
    assert isinstance(pos, ElementPosition)
    assert pos.page == 0
    assert pos.x0 == 50.0
    assert pos.x1 == 250.0


def test_match_elements_to_positions_falls_back_to_alt_text() -> None:
    """A Figure with no text but with /Alt should match via alt_text."""
    elements = [
        _struct_elem(0, tag="Figure", text="", alt="A red barn in winter"),
    ]
    blocks = [_block(0, 50, 700, 250, 720, "A red barn in winter")]
    positions = match_elements_to_positions(elements, blocks)
    assert 0 in positions


def test_match_elements_to_positions_container_pass_two() -> None:
    """A container (no MCIDs, has children) is matched in pass 2 via aggregate text."""
    container = _struct_elem(0, tag="Sect", text="", children=[1])
    # Override: Sect should be skipped, so use Div instead — Div is not in
    # the skip list. Rebuild.
    container = _struct_elem(0, tag="Div", text="Combined text", children=[1])
    leaf = _struct_elem(1, tag="Span", text="Combined text", parent=0)
    elements = [container, leaf]
    blocks = [_block(0, 50, 700, 250, 720, "Combined text")]
    positions = match_elements_to_positions(elements, blocks)
    # Both should match.
    assert 0 in positions
    assert 1 in positions


# ---------------------------------------------------------------------------
# compute_visual_reading_order
# ---------------------------------------------------------------------------


def test_compute_visual_reading_order_empty_positions() -> None:
    """Empty positions → empty order."""
    assert compute_visual_reading_order([], {}, []) == []


def test_compute_visual_reading_order_top_to_bottom_single_column() -> None:
    """Single column: blocks ordered by y_top (top to bottom)."""
    elements = [
        _struct_elem(0, tag="H1", text="Top"),
        _struct_elem(1, tag="P", text="Middle"),
        _struct_elem(2, tag="P", text="Bottom"),
    ]
    # Page height 792 (default). y_top = 792 - y1.
    # Element 0 at y1=780 → y_top=12  (highest visually).
    # Element 1 at y1=500 → y_top=292.
    # Element 2 at y1=200 → y_top=592.
    positions: dict[int, ElementPosition] = {
        0: _pos(0, 50, 770, 200, 780),
        1: _pos(0, 50, 490, 200, 500),
        2: _pos(0, 50, 190, 200, 200),
    }
    columns = [Column(page=0, x0=0, x1=612, block_indices=[])]
    order = compute_visual_reading_order(elements, positions, columns)
    assert order == [0, 1, 2]


def test_compute_visual_reading_order_two_columns_left_first() -> None:
    """Two columns at the same y: left column read before right."""
    elements = [
        _struct_elem(0, tag="P", text="Right"),
        _struct_elem(1, tag="P", text="Left"),
    ]
    # Same y1 → same y_top, so column ordering takes over.
    positions = {
        0: _pos(0, 400, 700, 550, 720),
        1: _pos(0, 50, 700, 200, 720),
    }
    columns = [
        Column(page=0, x0=0, x1=300, block_indices=[]),
        Column(page=0, x0=300, x1=612, block_indices=[]),
    ]
    order = compute_visual_reading_order(elements, positions, columns)
    # Left (1) comes before right (0).
    assert order == [1, 0]


# ---------------------------------------------------------------------------
# compare_reading_orders
# ---------------------------------------------------------------------------


def test_compare_reading_orders_identical_returns_full_correlation() -> None:
    elements = [
        _struct_elem(0, tag="P", text="A"),
        _struct_elem(1, tag="P", text="B"),
        _struct_elem(2, tag="P", text="C"),
    ]
    positions = {
        0: _pos(0, 50, 700, 100, 720),
        1: _pos(0, 50, 680, 100, 700),
        2: _pos(0, 50, 660, 100, 680),
    }
    correlation, mismatches, annotations = compare_reading_orders(
        elements,
        structure_order_indices=[0, 1, 2],
        visual_order_indices=[0, 1, 2],
        positions=positions,
    )
    assert correlation == 1.0
    assert mismatches == []
    # Annotations should describe each element with status "OK".
    for idx in (0, 1, 2):
        assert "OK" in annotations[idx]


def test_compare_reading_orders_swapped_pairs_lower_correlation() -> None:
    elements = [
        _struct_elem(0, tag="P", text="A"),
        _struct_elem(1, tag="P", text="B"),
        _struct_elem(2, tag="P", text="C"),
    ]
    positions = {
        0: _pos(0, 50, 700, 100, 720),
        1: _pos(0, 50, 680, 100, 700),
        2: _pos(0, 50, 660, 100, 680),
    }
    # Structure says A, B, C; visual says A, C, B → one inversion (B vs C).
    correlation, mismatches, _annotations = compare_reading_orders(
        elements,
        structure_order_indices=[0, 1, 2],
        visual_order_indices=[0, 2, 1],
        positions=positions,
    )
    assert correlation < 1.0
    assert correlation >= 0.0
    assert len(mismatches) >= 1
    m = mismatches[0]
    assert isinstance(m, ReadingOrderMismatch)
    # The struct_first/struct_second should reference the inverted pair.
    pair = (m.struct_first, m.struct_second)
    assert pair == (1, 2)


def test_compare_reading_orders_empty_orders() -> None:
    """Empty orders → correlation 1.0, empty mismatches & annotations."""
    correlation, mismatches, annotations = compare_reading_orders(
        [], [], [], {},
    )
    assert correlation == 1.0
    assert mismatches == []
    assert annotations == {}


# ---------------------------------------------------------------------------
# extract_visual_positions (real PDF round-trip)
# ---------------------------------------------------------------------------


def test_extract_visual_positions_single_page_text(tmp_path: Path) -> None:
    """A 1-page PDF with one text run produces 1+ VisualBlocks with plausible coords."""
    content = b"BT /F1 12 Tf 100 700 Td (Hello world) Tj ET\n"
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(_make_pdf(content))

    blocks, page_dims = extract_visual_positions(pdf_path)

    assert len(page_dims) == 1
    dim = page_dims[0]
    assert dim.width > 0
    assert dim.height > 0
    assert len(blocks) >= 1
    b = blocks[0]
    assert isinstance(b, VisualBlock)
    assert b.page == 0
    assert "Hello" in b.text
    # 100,700 was the text origin; the bbox should bracket it.
    assert b.x0 < b.x1
    assert b.y0 < b.y1


def test_extract_visual_positions_corrupt_pdf_returns_empty(tmp_path: Path) -> None:
    """Malformed PDF degrades gracefully — empty results, no raise."""
    out = tmp_path / "garbage.pdf"
    out.write_bytes(b"%PDF-1.4\nthis is not a valid pdf\n%%EOF\n")
    blocks, page_dims = extract_visual_positions(out)
    assert blocks == []
    assert page_dims == []
