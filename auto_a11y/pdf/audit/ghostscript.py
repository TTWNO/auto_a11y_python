"""Ghostscript detection and page rendering.

Sole subprocess caller in the PDF audit package. All other audit modules
that need a raster image of a page go through `render_page_to_png`.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from auto_a11y.pdf.errors import GhostscriptMissing

_GS_SEARCH_NAMES = ['gs', 'gswin64c', 'gswin32c']
_cached_path: str | None = None
_cache_populated: bool = False


def invalidate_detection_cache() -> None:
    """Clear the detection cache. Useful for tests."""
    global _cached_path, _cache_populated
    _cached_path = None
    _cache_populated = False


def detect_ghostscript(
    *,
    override: str | None = None,
    raise_if_missing: bool = False,
) -> str | None:
    """Locate the Ghostscript binary on PATH.

    Args:
        override: If set, skip detection and use this path as-is.
        raise_if_missing: If True, raise GhostscriptMissing when not found.

    Returns:
        Path to the ghostscript executable, or None if not found.
    """
    global _cached_path, _cache_populated

    if override:
        return override

    if _cache_populated:
        if _cached_path is None and raise_if_missing:
            raise GhostscriptMissing(searched=_GS_SEARCH_NAMES)
        return _cached_path

    for name in _GS_SEARCH_NAMES:
        path = shutil.which(name)
        if path:
            _cached_path = path
            _cache_populated = True
            return path

    _cached_path = None
    _cache_populated = True
    if raise_if_missing:
        raise GhostscriptMissing(searched=_GS_SEARCH_NAMES)
    return None


def _require_ghostscript(override: str | None = None) -> str:
    """detect_ghostscript with raise_if_missing=True, narrowed to str."""
    path = detect_ghostscript(override=override, raise_if_missing=True)
    if path is None:  # defensive — detect_ghostscript should have raised
        raise GhostscriptMissing(searched=_GS_SEARCH_NAMES)
    return path


def render_page_to_png(
    pdf_path: Path,
    page_num: int,
    *,
    dpi: int = 150,
    timeout_seconds: int = 30,
    gs_path_override: str | None = None,
) -> bytes | None:
    """Render a single PDF page to PNG bytes via Ghostscript.

    Args:
        pdf_path: Path to the PDF file.
        page_num: Zero-based page index.
        dpi: Rendering DPI.
        timeout_seconds: Subprocess timeout.
        gs_path_override: If set, use this Ghostscript path instead of detection.

    Returns:
        PNG bytes, or None on failure.
    """
    gs_path = _require_ghostscript(gs_path_override)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        result = subprocess.run(
            [
                gs_path,
                "-dNOPAUSE",
                "-dBATCH",
                "-dSAFER",
                "-sDEVICE=png16m",
                f"-r{dpi}",
                f"-dFirstPage={page_num + 1}",
                f"-dLastPage={page_num + 1}",
                f"-sOutputFile={tmp_path}",
                str(pdf_path),
            ],
            capture_output=True,
            timeout=timeout_seconds,
        )
        if result.returncode != 0 or not tmp_path.exists():
            return None
        return tmp_path.read_bytes()
    except subprocess.TimeoutExpired:
        print(f"Warning: Ghostscript timed out on page {page_num}", file=sys.stderr)
        return None
    except FileNotFoundError:
        raise GhostscriptMissing(searched=[gs_path])
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
