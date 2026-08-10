"""Page rasterisation for the colour and contrast checks.

Colour analysis needs a bitmap of each page. That used to come from Ghostscript
as a subprocess, which had two problems: Ghostscript is AGPL-3.0-or-later and
this application is distributed under a proprietary licence, and it is an
external binary the installer never shipped — so an audit on a machine without
it failed outright rather than losing only the colour checks.

PDFium (BSD-3-Clause, via the pypdfium2 wheel) renders in-process and ships its
own native library inside the wheel, so it is installed by the ordinary
requirements step and needs no bundling on any platform.

The contract is unchanged from the Ghostscript version: return PNG bytes, or
``None`` if the page cannot be rendered. Callers already treat ``None`` as "no
colour data for this page" and carry on.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# PDFium works in points at 72 dpi; this converts the caller's dpi to its scale.
_POINTS_PER_INCH = 72.0


def renderer_available() -> bool:
    """Whether page rasterisation can run.

    True in any normal install — pypdfium2 is a pinned requirement. It can be
    False in a stripped environment, and the health endpoint reports it so the
    cause is visible rather than showing up as every colour check returning
    nothing.
    """
    import importlib.util

    return importlib.util.find_spec("pypdfium2") is not None


def render_page_to_png(
    pdf_path: Path,
    page_num: int,
    *,
    dpi: int = 150,
) -> bytes | None:
    """Render a single PDF page to PNG bytes.

    Args:
        pdf_path: Path to the PDF file.
        page_num: Zero-based page index.
        dpi: Rendering DPI.

    Returns:
        PNG bytes, or None if the page could not be rendered.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError:
        logger.warning(
            "pypdfium2 is not installed; colour and contrast checks cannot rasterise pages. Install the pinned requirement to restore them."
        )
        return None

    document = None
    try:
        document = pdfium.PdfDocument(str(pdf_path))
        if page_num < 0 or page_num >= len(document):
            logger.warning(
                "Page %d is out of range for %s (%d pages)",
                page_num, pdf_path, len(document),
            )
            return None
        page = document[page_num]
        bitmap = page.render(scale=dpi / _POINTS_PER_INCH)
        buffer = io.BytesIO()
        bitmap.to_pil().save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception as exc:  # noqa: BLE001 — one bad page must not end the audit
        # Deliberately broad: a malformed page should cost its own colour data,
        # not the other twelve check modules. The Ghostscript version behaved
        # the same way by returning None on a non-zero exit.
        logger.warning("Could not render %s page %d: %s", pdf_path, page_num, exc)
        return None
    finally:
        if document is not None:
            try:
                document.close()
            except Exception:  # noqa: BLE001 — best-effort cleanup
                pass
