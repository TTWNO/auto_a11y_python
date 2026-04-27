"""PDF image-XObject extractor.

Ports ``extract_images`` from pdfMax's
``python/checker/pdf_accessibility_audit.py`` into the auto_a11y codebase.

The extractor walks every page's ``/XObject`` resources, finds entries whose
``/Subtype`` is ``/Image``, and writes each one out as a PNG to
``output_dir`` (created if missing). The pdfMax original returned a list
of opaque ``dict[str, ...]`` records that mixed status booleans and
filenames; here we return a typed :class:`ExtractedImage` per
*successfully* extracted image and silently skip the rest. A separate
audit-check module is responsible for synthesising user-facing failures
for unreadable images.

Type narrowing rationale:

* Every Dictionary access is funnelled through
  :mod:`auto_a11y.pdf.audit.pikepdf_helpers` so the type checker sees
  concrete ``Dictionary``/``Name``/``Object`` types — never the bare
  ``pikepdf.Object`` that pikepdf's stub returns from ``__getitem__``.
* Image-mode determination is heuristic: the colourspace operand is
  rendered via ``str()`` and substring-matched, mirroring pdfMax's logic.
  A colourspace-aware decoder would inspect the array shape and lookup
  ICC profiles by reference; we are deliberately pinning pdfMax's
  behaviour so the audit pipeline produces equivalent output.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import pikepdf
from PIL import Image, UnidentifiedImageError

from auto_a11y.pdf.audit import pikepdf_helpers


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractedImage:
    """Metadata for one image successfully written to ``output_dir``.

    Attributes:
        index: 1-based image index across the document, in page order.
        filename: basename of the saved PNG (e.g. ``"doc_image_3.png"``).
            Always relative to ``output_dir`` — callers compose absolute
            paths themselves if they need them.
        width: Image width in pixels, taken from the XObject's ``/Width``.
        height: Image height in pixels, taken from ``/Height``.
    """

    index: int
    filename: str
    width: int
    height: int


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_images(
    pdf_path: Path,
    output_dir: Path,
) -> list[ExtractedImage]:
    """Extract every image XObject from ``pdf_path`` to ``output_dir`` as PNG.

    Walks each page's ``/Resources /XObject`` map. For entries whose
    ``/Subtype`` is ``/Image``:

    * ``/DCTDecode`` (JPEG) streams are decoded via PIL, converted to RGB
      if originally CMYK, and re-saved as PNG.
    * ``/FlateDecode`` and unfiltered streams are read through pikepdf's
      ``read_bytes`` (which transparently applies the filter chain) and
      re-wrapped via ``Image.frombytes`` using a colour mode inferred
      from the ``/ColorSpace`` operand.
    * Any other filter combination, or a stream that fails to decode for
      any reason (truncated data, unexpected colour space, PIL refusal),
      is silently skipped — the returned list contains entries only for
      images that were actually written.

    ``output_dir`` is created with ``parents=True`` if it doesn't already
    exist; pdfMax assumed it was created by a caller, but the auto_a11y
    pipeline calls this function with per-document directories that don't
    necessarily exist yet, so we materialise it ourselves.

    Args:
        pdf_path: Path to the source PDF. Opened read-only via pikepdf.
        output_dir: Directory to write PNGs to. Created if missing.

    Returns:
        A list of :class:`ExtractedImage`, one per successfully-saved
        PNG, in document order. If the PDF has no image XObjects (or
        every extraction failed), returns ``[]``.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    basename = pdf_path.stem
    extracted: list[ExtractedImage] = []
    img_index = 0

    with pikepdf.open(pdf_path) as pdf:
        for page in pdf.pages:
            resources = pikepdf_helpers.get_dict(page.obj, "/Resources")
            if resources is None:
                continue
            xobjects = pikepdf_helpers.get_dict(resources, "/XObject")
            if xobjects is None:
                continue

            for key in xobjects.keys():
                xobj_raw = xobjects[key]
                # Image XObjects are stored as ``pikepdf.Stream`` (the
                # bitmap data lives in the stream payload; the stream
                # dictionary carries /Subtype /Width /Height /…). Form
                # XObjects are also Streams — we filter those out below
                # via the /Subtype check. Anything that isn't a Stream
                # (a malformed null leaf, a stray Dictionary) is silently
                # skipped, mirroring pdfMax's tolerance.
                if not isinstance(xobj_raw, pikepdf.Stream):
                    continue
                subtype = pikepdf_helpers.get_name(xobj_raw, "/Subtype")
                if subtype is None or str(subtype) != "/Image":
                    continue

                img_index += 1
                width = pikepdf_helpers.get_int(xobj_raw, "/Width") or 0
                height = pikepdf_helpers.get_int(xobj_raw, "/Height") or 0
                filename = f"{basename}_image_{img_index}.png"
                filepath = output_dir / filename

                if _save_xobject_as_png(xobj_raw, width, height, filepath):
                    extracted.append(
                        ExtractedImage(
                            index=img_index,
                            filename=filename,
                            width=width,
                            height=height,
                        )
                    )

    return extracted


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _save_xobject_as_png(
    xobj: pikepdf.Stream,
    width: int,
    height: int,
    filepath: Path,
) -> bool:
    """Decode ``xobj`` and write it to ``filepath`` as PNG. Returns success.

    Splits the broad try/except in pdfMax's original into a narrow set of
    decode branches, each catching only the exceptions that branch can
    plausibly raise. Failure is silent — callers omit the image from the
    returned list.
    """
    try:
        filters = xobj.get(pikepdf.Name("/Filter"))
    except (pikepdf.PdfError, KeyError, AttributeError):
        return False
    filter_str = str(filters) if filters is not None else ""

    try:
        if "/DCTDecode" in filter_str:
            return _save_jpeg_xobject(xobj, filepath)
        if "/FlateDecode" in filter_str or not filter_str:
            return _save_flate_bitmap(xobj, width, height, filepath)
    except (pikepdf.PdfError, OSError, ValueError, TypeError, KeyError, AttributeError):
        return False

    # Other filters (CCITTFaxDecode, JPXDecode, JBIG2Decode, …) are not
    # supported by pdfMax's original. Return False so the audit can flag
    # them via a separate check.
    return False


def _save_jpeg_xobject(xobj: pikepdf.Stream, filepath: Path) -> bool:
    """Decode a /DCTDecode (JPEG) stream and save as PNG.

    PIL handles the JPEG decode directly. CMYK JPEGs are converted to RGB
    so the saved PNG is web-friendly.
    """
    try:
        raw_data = xobj.read_raw_bytes()
    except (pikepdf.PdfError, AttributeError):
        return False

    try:
        with Image.open(BytesIO(raw_data)) as img:
            if img.mode == "CMYK":
                converted = img.convert("RGB")
                try:
                    converted.save(filepath, "PNG")
                finally:
                    converted.close()
            else:
                img.save(filepath, "PNG")
    except (OSError, ValueError, UnidentifiedImageError):
        return False
    return True


def _save_flate_bitmap(
    xobj: pikepdf.Stream,
    width: int,
    height: int,
    filepath: Path,
) -> bool:
    """Decode a /FlateDecode (or unfiltered) raw bitmap and save as PNG.

    pikepdf's ``read_bytes`` applies the filter chain transparently, so
    the bytes returned are already inflated. We then have to figure out
    the pixel layout ourselves: the colour space governs samples-per-
    pixel, but pdfMax's original takes a heuristic shortcut and just
    substring-matches on ``str(/ColorSpace)`` for ``RGB`` / ``CMYK``,
    falling back to grayscale ``L``. This is wrong for indexed and ICC
    colourspaces, but it's the behaviour we are pinning.
    """
    if width <= 0 or height <= 0:
        return False

    try:
        decoded = xobj.read_bytes()
    except (pikepdf.PdfError, AttributeError, OSError):
        return False

    try:
        color_space = xobj.get(pikepdf.Name("/ColorSpace"))
    except (pikepdf.PdfError, KeyError, AttributeError):
        color_space = None
    cs_str = str(color_space) if color_space is not None else ""

    if "/DeviceRGB" in cs_str or "RGB" in cs_str:
        mode = "RGB"
    elif "/DeviceCMYK" in cs_str or "CMYK" in cs_str:
        mode = "CMYK"
    else:
        mode = "L"

    expected = width * height * len(mode)
    if len(decoded) < expected:
        return False

    try:
        img = Image.frombytes(mode, (width, height), decoded[:expected])
    except (ValueError, OSError):
        return False

    try:
        if mode == "CMYK":
            converted = img.convert("RGB")
            try:
                converted.save(filepath, "PNG")
            finally:
                converted.close()
        else:
            img.save(filepath, "PNG")
    except (OSError, ValueError):
        return False
    finally:
        img.close()
    return True
