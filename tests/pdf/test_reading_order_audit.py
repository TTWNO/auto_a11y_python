"""Regression tests for two audit findings in
``auto_a11y/pdf/audit/reading_order.py``.

Bug A — ``detect_columns`` used to compute a single global column-boundary
set from the x-centers of every block across every page, then apply that
one boundary list to all pages. A document whose page 1 is single-column
and page 2 is two-column would therefore mis-assign blocks on both pages.
The fix derives column boundaries *per page*.

Bug B — ``compare_reading_orders`` could return a negative correlation when
the nearby-pair inversion count exceeded the (small-``n``) maximum, which
made a printed ``{correlation:.0%}`` read as a negative percentage. The fix
clamps the correlation into ``[0.0, 1.0]``.
"""
from __future__ import annotations

from auto_a11y.pdf.audit.reading_order import (
    ElementPosition,
    VisualBlock,
    compare_reading_orders,
    detect_columns,
)
from auto_a11y.pdf.audit.structure import StructElement
import pikepdf


_TEST_PAGE_HEIGHT: float = 792.0
_PAGE_WIDTH: float = 612.0


def _block(
    page: int,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    text: str,
    page_height: float = _TEST_PAGE_HEIGHT,
) -> VisualBlock:
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
    return ElementPosition(
        page=page,
        x0=x0,
        y0=y0,
        x1=x1,
        y1=y1,
        y_top=page_height - y1,
    )


def _struct_elem(index: int, *, text: str = "") -> StructElement:
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


# ---------------------------------------------------------------------------
# Bug A — per-page column detection
# ---------------------------------------------------------------------------


def test_detect_columns_per_page_mixed_layout() -> None:
    """Page 1 single-column, page 2 two-column → boundaries derived per page.

    Page 1 blocks are all centered (~x_center 306) so page 1 must collapse
    to a single column. Page 2 blocks split into a left cluster (~125) and
    a right cluster (~475), so page 2 must yield two columns.

    Before the fix, the global x-center distribution merged page 1's center
    cluster with page 2's two clusters, producing global boundaries that
    split page 1 into multiple columns (and could mis-bracket page 2).
    """
    blocks = [
        # Page 1 — single column, centered around x_center 306.
        _block(0, 256, 700, 356, 720, "P1 line one"),
        _block(0, 256, 680, 356, 690, "P1 line two longer"),
        _block(0, 256, 660, 356, 670, "P1 line three"),
        # Page 2 — two columns: left ~125, right ~475.
        _block(1, 50, 700, 200, 720, "P2 left one"),
        _block(1, 50, 680, 200, 690, "P2 left two"),
        _block(1, 400, 700, 550, 720, "P2 right one"),
        _block(1, 400, 680, 550, 690, "P2 right two"),
    ]
    cols = detect_columns(blocks, page_width=_PAGE_WIDTH)

    page0_cols = [c for c in cols if c.page == 0]
    page1_cols = [c for c in cols if c.page == 1]

    # Page 1 is single-column: exactly one column holding all three blocks.
    assert len(page0_cols) == 1
    assert sorted(page0_cols[0].block_indices) == [0, 1, 2]

    # Page 2 is two-column: two columns splitting the four blocks.
    assert len(page1_cols) == 2
    p1_ordered = sorted(page1_cols, key=lambda c: c.x0)
    assert set(p1_ordered[0].block_indices) == {3, 4}
    assert set(p1_ordered[1].block_indices) == {5, 6}


def test_detect_columns_single_page_two_columns_unchanged() -> None:
    """Existing single-page two-column behaviour is preserved."""
    blocks = [
        _block(0, 50, 700, 200, 720, "L1"),
        _block(0, 50, 680, 200, 690, "L2"),
        _block(0, 400, 700, 550, 720, "R1"),
        _block(0, 400, 680, 550, 690, "R2"),
    ]
    cols = detect_columns(blocks, page_width=_PAGE_WIDTH)
    assert len(cols) == 2
    ordered = sorted(cols, key=lambda c: c.x0)
    assert set(ordered[0].block_indices) == {0, 1}
    assert set(ordered[1].block_indices) == {2, 3}


# ---------------------------------------------------------------------------
# Bug B — correlation clamp
# ---------------------------------------------------------------------------


def test_compare_reading_orders_correlation_never_negative() -> None:
    """A heavily inverted small-``n`` order must not yield a negative score.

    With ``n`` small, ``max_inversions = n * min(n, 5) / 2`` can be smaller
    than the actual nearby-pair inversion count, so the raw
    ``1 - inversions / max_inversions`` goes negative. The result must be
    clamped to ``>= 0.0``.
    """
    elements = [_struct_elem(i, text=chr(ord("A") + i)) for i in range(4)]
    positions = {i: _pos(0, 50, 700 - i * 20, 100, 720 - i * 20) for i in range(4)}
    # Structure order 0,1,2,3; visual order fully reversed → maximal
    # inversions within the nearby window.
    correlation, _mismatches, _annotations = compare_reading_orders(
        elements,
        structure_order_indices=[0, 1, 2, 3],
        visual_order_indices=[3, 2, 1, 0],
        positions=positions,
    )
    assert correlation >= 0.0
    assert correlation <= 1.0


def test_compare_reading_orders_correlation_within_unit_interval() -> None:
    """Identical orders still produce 1.0 (upper bound preserved)."""
    elements = [_struct_elem(i, text=chr(ord("A") + i)) for i in range(3)]
    positions = {i: _pos(0, 50, 700 - i * 20, 100, 720 - i * 20) for i in range(3)}
    correlation, _mismatches, _annotations = compare_reading_orders(
        elements,
        structure_order_indices=[0, 1, 2],
        visual_order_indices=[0, 1, 2],
        positions=positions,
    )
    assert correlation == 1.0
