"""Tests for the typed PDF image-XObject extractor.

Strategy: build small synthetic PDFs in-process via pikepdf. Image
XObjects are constructed by hand-rolling a stream with the appropriate
``/Subtype /Image`` plus pixel data — either raw RGB bytes wrapped in a
``/FlateDecode`` filter, or JPEG-encoded bytes wrapped in ``/DCTDecode``.
This keeps the test surface entirely in-process — no fixture PDFs on disk.

The PIL Image library is used both to *generate* the JPEG payload (so we
can exercise the ``/DCTDecode`` branch with real JPEG data) and to
*verify* that the extracted PNG round-trips back to a sensible image.
"""
from __future__ import annotations

import zlib
from io import BytesIO
from pathlib import Path
from typing import Protocol

import pikepdf
from PIL import Image

from auto_a11y.pdf.audit.images import ExtractedImage, extract_images


# ---------------------------------------------------------------------------
# Synthetic PDF helpers
# ---------------------------------------------------------------------------


class _StreamMaker(Protocol):
    """Typed view of ``pikepdf.Pdf.make_stream`` for kwargs-driven calls.

    Mirrors the helper in ``test_colors.py`` / ``test_content_streams.py``:
    pikepdf's stub declares ``Pdf.make_stream(d=None, **kwargs)`` without
    parameter types. Routing through a Protocol pins the signature for
    the type-checkers without an escape hatch.
    """

    def __call__(self, data: bytes, **kwargs: object) -> pikepdf.Stream: ...


def _typed_make_stream(pdf: pikepdf.Pdf, data: bytes, **kwargs: object) -> pikepdf.Stream:
    maker: _StreamMaker = getattr(pdf, "make_stream")
    return maker(data, **kwargs)


def _flate_image_xobject(
    pdf: pikepdf.Pdf,
    *,
    width: int,
    height: int,
    pixel: tuple[int, int, int] = (255, 0, 0),
) -> pikepdf.Stream:
    """Build a /FlateDecode RGB image XObject of the given dimensions."""
    raw = bytes(list(pixel)) * (width * height)
    compressed = zlib.compress(raw)
    return _typed_make_stream(
        pdf,
        compressed,
        Type=pikepdf.Name("/XObject"),
        Subtype=pikepdf.Name("/Image"),
        Width=width,
        Height=height,
        ColorSpace=pikepdf.Name("/DeviceRGB"),
        BitsPerComponent=8,
        Filter=pikepdf.Name("/FlateDecode"),
    )


def _jpeg_image_xobject(
    pdf: pikepdf.Pdf,
    *,
    width: int,
    height: int,
    pixel: tuple[int, int, int] = (0, 255, 0),
) -> pikepdf.Stream:
    """Build a /DCTDecode (JPEG) RGB image XObject."""
    img = Image.new("RGB", (width, height), pixel)
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return _typed_make_stream(
        pdf,
        buf.getvalue(),
        Type=pikepdf.Name("/XObject"),
        Subtype=pikepdf.Name("/Image"),
        Width=width,
        Height=height,
        ColorSpace=pikepdf.Name("/DeviceRGB"),
        BitsPerComponent=8,
        Filter=pikepdf.Name("/DCTDecode"),
    )


def _form_xobject(pdf: pikepdf.Pdf) -> pikepdf.Stream:
    """Build a Form-type XObject (NOT an image — should be ignored)."""
    return _typed_make_stream(
        pdf,
        b"q Q\n",
        Type=pikepdf.Name("/XObject"),
        Subtype=pikepdf.Name("/Form"),
        BBox=pikepdf.Array([0, 0, 10, 10]),
        Resources=pikepdf.Dictionary(),
    )


def _save_pdf(pdf: pikepdf.Pdf, tmp_path: Path, name: str = "doc.pdf") -> Path:
    """Persist a ``pikepdf.Pdf`` to ``tmp_path/name`` and return the path."""
    out = tmp_path / name
    buf = BytesIO()
    pdf.save(buf)
    out.write_bytes(buf.getvalue())
    return out


# ---------------------------------------------------------------------------
# Tests — empty / no-image cases
# ---------------------------------------------------------------------------


def test_extract_returns_empty_for_pdf_without_images(tmp_path: Path) -> None:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf_path = _save_pdf(pdf, tmp_path)

    out = tmp_path / "out"
    result = extract_images(pdf_path, out)

    assert result == []
    # output_dir is created even when nothing was extracted.
    assert out.is_dir()
    assert list(out.iterdir()) == []


def test_extract_creates_output_dir_when_missing(tmp_path: Path) -> None:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf_path = _save_pdf(pdf, tmp_path)

    out = tmp_path / "deeply" / "nested" / "out"
    assert not out.exists()

    extract_images(pdf_path, out)
    assert out.is_dir()


def test_extract_ignores_non_image_xobjects(tmp_path: Path) -> None:
    """Form XObjects (Subtype /Form) must not be extracted as images."""
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Resources = pikepdf.Dictionary(
        XObject=pikepdf.Dictionary(Fm1=_form_xobject(pdf))
    )
    pdf_path = _save_pdf(pdf, tmp_path)

    result = extract_images(pdf_path, tmp_path / "out")
    assert result == []


# ---------------------------------------------------------------------------
# Tests — single image extraction
# ---------------------------------------------------------------------------


def test_extract_single_flate_rgb_image(tmp_path: Path) -> None:
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Resources = pikepdf.Dictionary(
        XObject=pikepdf.Dictionary(
            Im1=_flate_image_xobject(pdf, width=10, height=8, pixel=(255, 0, 0))
        )
    )
    pdf_path = _save_pdf(pdf, tmp_path, name="my.pdf")

    result = extract_images(pdf_path, tmp_path / "out")

    assert len(result) == 1
    entry = result[0]
    assert entry == ExtractedImage(index=1, filename="my_image_1.png", width=10, height=8)

    written = tmp_path / "out" / entry.filename
    assert written.is_file()
    with Image.open(written) as img:
        assert img.size == (10, 8)
        # The PNG must round-trip to RGB (or RGBA — PIL may save as either).
        assert img.mode in {"RGB", "RGBA"}


def test_extract_single_jpeg_image(tmp_path: Path) -> None:
    """The /DCTDecode branch must run JPEG → PIL → PNG."""
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Resources = pikepdf.Dictionary(
        XObject=pikepdf.Dictionary(
            Im1=_jpeg_image_xobject(pdf, width=12, height=12, pixel=(0, 200, 50))
        )
    )
    pdf_path = _save_pdf(pdf, tmp_path, name="jpg.pdf")

    result = extract_images(pdf_path, tmp_path / "out")

    assert len(result) == 1
    entry = result[0]
    assert entry.width == 12 and entry.height == 12
    assert entry.filename == "jpg_image_1.png"

    written = tmp_path / "out" / entry.filename
    assert written.is_file()
    with Image.open(written) as img:
        assert img.size == (12, 12)


# ---------------------------------------------------------------------------
# Tests — multi-image and multi-page indexing
# ---------------------------------------------------------------------------


def test_extract_indexes_increment_across_pages(tmp_path: Path) -> None:
    """The 1-based index must continue across page boundaries."""
    pdf = pikepdf.Pdf.new()

    p1 = pdf.add_blank_page(page_size=(612, 792))
    p1.Resources = pikepdf.Dictionary(
        XObject=pikepdf.Dictionary(
            Im1=_flate_image_xobject(pdf, width=4, height=4, pixel=(255, 0, 0)),
        )
    )

    p2 = pdf.add_blank_page(page_size=(612, 792))
    p2.Resources = pikepdf.Dictionary(
        XObject=pikepdf.Dictionary(
            Im2=_jpeg_image_xobject(pdf, width=6, height=6, pixel=(0, 255, 0)),
        )
    )

    p3 = pdf.add_blank_page(page_size=(612, 792))
    p3.Resources = pikepdf.Dictionary(
        XObject=pikepdf.Dictionary(
            Im3=_flate_image_xobject(pdf, width=8, height=8, pixel=(0, 0, 255)),
        )
    )

    pdf_path = _save_pdf(pdf, tmp_path, name="multi.pdf")
    result = extract_images(pdf_path, tmp_path / "out")

    assert [e.index for e in result] == [1, 2, 3]
    assert [e.filename for e in result] == [
        "multi_image_1.png",
        "multi_image_2.png",
        "multi_image_3.png",
    ]
    assert [(e.width, e.height) for e in result] == [(4, 4), (6, 6), (8, 8)]
    for e in result:
        assert (tmp_path / "out" / e.filename).is_file()


def test_extract_handles_multiple_images_on_one_page(tmp_path: Path) -> None:
    """Two images on a single page both get extracted, with distinct indices."""
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Resources = pikepdf.Dictionary(
        XObject=pikepdf.Dictionary(
            Im1=_flate_image_xobject(pdf, width=5, height=5, pixel=(10, 20, 30)),
            Im2=_jpeg_image_xobject(pdf, width=7, height=7, pixel=(40, 50, 60)),
        )
    )
    pdf_path = _save_pdf(pdf, tmp_path, name="two.pdf")

    result = extract_images(pdf_path, tmp_path / "out")
    assert len(result) == 2
    assert {e.index for e in result} == {1, 2}
    # Filenames stay 1-based and sequential regardless of dict iteration order.
    assert {e.filename for e in result} == {"two_image_1.png", "two_image_2.png"}


# ---------------------------------------------------------------------------
# Tests — failure tolerance
# ---------------------------------------------------------------------------


def test_extract_skips_corrupt_image_silently(tmp_path: Path) -> None:
    """A truncated FlateDecode stream must not crash the extractor."""
    pdf = pikepdf.Pdf.new()
    # Build a stream whose Width/Height claim 100x100 RGB pixels but whose
    # decoded payload is far smaller. The Flate branch's expected-vs-actual
    # length check rejects this and returns False.
    too_small = zlib.compress(b"\x00\x00\x00")
    bad_xobj = _typed_make_stream(
        pdf,
        too_small,
        Type=pikepdf.Name("/XObject"),
        Subtype=pikepdf.Name("/Image"),
        Width=100,
        Height=100,
        ColorSpace=pikepdf.Name("/DeviceRGB"),
        BitsPerComponent=8,
        Filter=pikepdf.Name("/FlateDecode"),
    )
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im1=bad_xobj))
    pdf_path = _save_pdf(pdf, tmp_path, name="bad.pdf")

    out = tmp_path / "out"
    result = extract_images(pdf_path, out)

    # Failed extraction → no entry returned, no file written.
    assert result == []
    assert list(out.iterdir()) == []


def test_extract_skips_unsupported_filter(tmp_path: Path) -> None:
    """An image with /CCITTFaxDecode is not supported and must be skipped."""
    pdf = pikepdf.Pdf.new()
    fax_xobj = _typed_make_stream(
        pdf,
        b"\x00\x00\x00\x00",
        Type=pikepdf.Name("/XObject"),
        Subtype=pikepdf.Name("/Image"),
        Width=10,
        Height=10,
        ColorSpace=pikepdf.Name("/DeviceGray"),
        BitsPerComponent=1,
        Filter=pikepdf.Name("/CCITTFaxDecode"),
    )
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im1=fax_xobj))
    pdf_path = _save_pdf(pdf, tmp_path, name="fax.pdf")

    result = extract_images(pdf_path, tmp_path / "out")
    assert result == []


def test_extract_skips_image_with_zero_dimensions(tmp_path: Path) -> None:
    """An image with 0 width or 0 height is unrecoverable and must be skipped."""
    pdf = pikepdf.Pdf.new()
    bad_xobj = _typed_make_stream(
        pdf,
        zlib.compress(b""),
        Type=pikepdf.Name("/XObject"),
        Subtype=pikepdf.Name("/Image"),
        Width=0,
        Height=0,
        ColorSpace=pikepdf.Name("/DeviceRGB"),
        BitsPerComponent=8,
        Filter=pikepdf.Name("/FlateDecode"),
    )
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im1=bad_xobj))
    pdf_path = _save_pdf(pdf, tmp_path, name="zero.pdf")

    result = extract_images(pdf_path, tmp_path / "out")
    assert result == []


def test_extract_continues_after_corrupt_image(tmp_path: Path) -> None:
    """One bad image on a page must not abort extraction of subsequent images."""
    pdf = pikepdf.Pdf.new()

    bad_xobj = _typed_make_stream(
        pdf,
        zlib.compress(b"\x00"),
        Type=pikepdf.Name("/XObject"),
        Subtype=pikepdf.Name("/Image"),
        Width=100,
        Height=100,
        ColorSpace=pikepdf.Name("/DeviceRGB"),
        BitsPerComponent=8,
        Filter=pikepdf.Name("/FlateDecode"),
    )
    good_xobj = _flate_image_xobject(pdf, width=4, height=4, pixel=(11, 22, 33))

    page = pdf.add_blank_page(page_size=(612, 792))
    page.Resources = pikepdf.Dictionary(
        XObject=pikepdf.Dictionary(Im1=bad_xobj, Im2=good_xobj)
    )
    pdf_path = _save_pdf(pdf, tmp_path, name="mixed.pdf")

    result = extract_images(pdf_path, tmp_path / "out")
    # Only the good image survives. The img_index counter still advances
    # for the failed image (matching pdfMax's behaviour: the global index
    # counts attempted images), so the surviving entry's index is either
    # 1 or 2 depending on which order pikepdf iterates the XObject dict
    # — we don't pin the order.
    assert len(result) == 1
    assert result[0].index in {1, 2}
    assert result[0].width == 4 and result[0].height == 4
    assert (tmp_path / "out" / result[0].filename).is_file()
