"""Tests for page rasterisation (auto_a11y.pdf.audit.rasterize).

The contract that matters here is not "it renders" but "it never takes the
audit down with it". Rasterisation feeds only the colour and contrast checks;
when it cannot produce a bitmap the other twelve check modules must still run.
Under Ghostscript that was not true — a missing binary failed the whole
document — which is the regression these tests exist to prevent.
"""
from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pikepdf
import pytest

from auto_a11y.pdf.audit.rasterize import render_page_to_png, renderer_available


@pytest.fixture
def two_page_pdf(tmp_path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    for _ in range(2):
        pdf.add_blank_page(page_size=(200, 100))
    out = tmp_path / "two_pages.pdf"
    pdf.save(out)
    return out


def test_renderer_is_available_in_a_normal_install() -> None:
    # pypdfium2 is a pinned requirement, so this is only False in a stripped env.
    assert renderer_available() is True


def test_renders_a_page_to_png_bytes(two_page_pdf: Path) -> None:
    png = render_page_to_png(two_page_pdf, 0, dpi=72)
    assert png is not None
    assert png.startswith(b"\x89PNG\r\n\x1a\n"), "should be a real PNG"


def test_dpi_controls_the_raster_size(two_page_pdf: Path) -> None:
    from PIL import Image

    small = render_page_to_png(two_page_pdf, 0, dpi=72)
    large = render_page_to_png(two_page_pdf, 0, dpi=144)
    assert small is not None and large is not None
    w_small = Image.open(io.BytesIO(small)).width
    w_large = Image.open(io.BytesIO(large)).width
    # 200pt at 72dpi is ~200px; doubling the dpi should roughly double it.
    assert w_large > w_small * 1.8


def test_out_of_range_page_returns_none_rather_than_raising(two_page_pdf: Path) -> None:
    assert render_page_to_png(two_page_pdf, 99, dpi=72) is None
    assert render_page_to_png(two_page_pdf, -1, dpi=72) is None


def test_missing_file_returns_none_rather_than_raising(tmp_path: Path) -> None:
    assert render_page_to_png(tmp_path / "nope.pdf", 0, dpi=72) is None


def test_corrupt_file_returns_none_rather_than_raising(tmp_path: Path) -> None:
    bad = tmp_path / "corrupt.pdf"
    bad.write_bytes(b"%PDF-1.7\nthis is not a pdf body")
    assert render_page_to_png(bad, 0, dpi=72) is None


def test_absent_renderer_returns_none_rather_than_raising(two_page_pdf: Path) -> None:
    """The whole point: no renderer costs colour data, not the audit.

    Ghostscript's absence used to raise out of run_audit and mark the document
    AUDIT_FAILED. A missing renderer must now be indistinguishable, to callers,
    from a page that simply could not be rasterised.
    """
    with patch.dict("sys.modules", {"pypdfium2": None}):
        assert render_page_to_png(two_page_pdf, 0, dpi=72) is None
