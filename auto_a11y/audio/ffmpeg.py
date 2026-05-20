"""ffmpeg / ffprobe detection.

Mirrors :mod:`auto_a11y.pdf.audit.ghostscript`: a small caching detector
with an ``override`` escape hatch and a ``raise_if_missing`` toggle.
The duration probe and silence-detection wrappers come in Phase 2.

At import time the module also registers preflight checks for ffmpeg
and ffprobe with the central :mod:`auto_a11y.core.preflight` registry,
so app startup can surface a remediation message when the binaries are
missing.
"""
from __future__ import annotations

import logging
import shutil

from auto_a11y.audio.errors import FfmpegMissing
from auto_a11y.core.preflight import Check, CheckOutcome, get_registry

logger = logging.getLogger(__name__)

_FFMPEG_NAMES = ["ffmpeg", "ffmpeg.exe"]
_FFPROBE_NAMES = ["ffprobe", "ffprobe.exe"]

_cached_ffmpeg: str | None = None
_ffmpeg_cache_populated: bool = False

_cached_ffprobe: str | None = None
_ffprobe_cache_populated: bool = False


def invalidate_detection_cache() -> None:
    """Clear the detection caches. Useful for tests."""
    global _cached_ffmpeg, _ffmpeg_cache_populated
    global _cached_ffprobe, _ffprobe_cache_populated
    _cached_ffmpeg = None
    _ffmpeg_cache_populated = False
    _cached_ffprobe = None
    _ffprobe_cache_populated = False


def _detect(names: list[str]) -> str | None:
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return None


def detect_ffmpeg(
    *,
    override: str | None = None,
    raise_if_missing: bool = False,
) -> str | None:
    """Locate the ffmpeg binary on PATH.

    Args:
        override: If set, skip detection and use this path as-is.
        raise_if_missing: If True, raise FfmpegMissing when not found.

    Returns:
        Path to the ffmpeg executable, or None if not found and not required.
    """
    global _cached_ffmpeg, _ffmpeg_cache_populated
    if override:
        return override
    if not _ffmpeg_cache_populated:
        _cached_ffmpeg = _detect(_FFMPEG_NAMES)
        _ffmpeg_cache_populated = True
    if _cached_ffmpeg is None and raise_if_missing:
        raise FfmpegMissing(searched=_FFMPEG_NAMES)
    return _cached_ffmpeg


def detect_ffprobe(
    *,
    override: str | None = None,
    raise_if_missing: bool = False,
) -> str | None:
    """Locate the ffprobe binary on PATH.

    Args:
        override: If set, skip detection and use this path as-is.
        raise_if_missing: If True, raise FfmpegMissing when not found.

    Returns:
        Path to the ffprobe executable, or None if not found and not required.
    """
    global _cached_ffprobe, _ffprobe_cache_populated
    if override:
        return override
    if not _ffprobe_cache_populated:
        _cached_ffprobe = _detect(_FFPROBE_NAMES)
        _ffprobe_cache_populated = True
    if _cached_ffprobe is None and raise_if_missing:
        raise FfmpegMissing(searched=_FFPROBE_NAMES)
    return _cached_ffprobe


# --- Preflight registration ---------------------------------------------------


def run_ffmpeg_check() -> CheckOutcome:
    if detect_ffmpeg() is None:
        return CheckOutcome.failed(
            "Install ffmpeg and ensure it is on PATH (e.g., `apt install ffmpeg` or `brew install ffmpeg`).",
        )
    return CheckOutcome.ok()


def run_ffprobe_check() -> CheckOutcome:
    if detect_ffprobe() is None:
        return CheckOutcome.failed(
            "Install ffprobe (ships with ffmpeg) and ensure it is on PATH."
        )
    return CheckOutcome.ok()


get_registry().register(Check(
    name="ffmpeg",
    description="ffmpeg binary on PATH (required for audio extraction).",
    run=run_ffmpeg_check,
))
get_registry().register(Check(
    name="ffprobe",
    description="ffprobe binary on PATH (required for duration probing).",
    run=run_ffprobe_check,
))
