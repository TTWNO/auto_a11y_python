"""Regression tests for two LOW-severity audio audit fixes.

Bug A — ffmpeg/ffprobe subprocesses run with a ``timeout=`` and translate
``subprocess.TimeoutExpired`` into a domain pipeline error instead of letting
it propagate raw.

Bug B — ``_fmt_ts`` / ``_seconds_to_ts`` must never emit an invalid ``:60.xxx``
seconds field when the fractional part rounds up across a minute boundary.
"""
from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from auto_a11y.audio.errors import AudioPipelineError, CalloutsError
from auto_a11y.audio.segmenter import (
    Segment,
    detect_silences,
    extract_segment,
    probe_duration,
)
from auto_a11y.audio import transcription, vtt_processor
from auto_a11y.audio.storage import AllocatedSlot


# --------------------------------------------------------------------------
# Bug A — ffmpeg/ffprobe timeouts
# --------------------------------------------------------------------------


def _timeout(kwargs: dict[str, Any]) -> subprocess.TimeoutExpired:
    """Build a TimeoutExpired matching the timeout= the code passed."""
    return subprocess.TimeoutExpired(cmd="ffmpeg", timeout=float(kwargs.get("timeout", 0.0)))


def test_probe_duration_passes_timeout_and_wraps_timeout_expired(tmp_path: Path) -> None:
    src = tmp_path / "in.mp4"
    src.write_bytes(b"")
    captured: dict[str, object] = {}

    def _fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        raise _timeout(kwargs)

    with patch("auto_a11y.audio.segmenter.detect_ffprobe", return_value="/usr/bin/ffprobe"), \
         patch("auto_a11y.audio.segmenter.subprocess.run", side_effect=_fake_run):
        with pytest.raises(AudioPipelineError):
            probe_duration(src)

    assert "timeout" in captured, "subprocess.run must be called with a timeout= kwarg"
    timeout_val = captured["timeout"]
    assert isinstance(timeout_val, (int, float)) and timeout_val > 0


def test_detect_silences_passes_timeout_and_wraps_timeout_expired(tmp_path: Path) -> None:
    src = tmp_path / "in.mp4"
    src.write_bytes(b"")
    captured: dict[str, object] = {}

    def _fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        raise _timeout(kwargs)

    with patch("auto_a11y.audio.segmenter.detect_ffmpeg", return_value="/usr/bin/ffmpeg"), \
         patch("auto_a11y.audio.segmenter.subprocess.run", side_effect=_fake_run):
        with pytest.raises(AudioPipelineError):
            detect_silences(src)

    assert "timeout" in captured
    timeout_val = captured["timeout"]
    assert isinstance(timeout_val, (int, float)) and timeout_val > 0


def test_extract_segment_passes_timeout_and_wraps_timeout_expired(tmp_path: Path) -> None:
    from auto_a11y.audio.storage import AudioStorage

    storage = AudioStorage(root=tmp_path / "recordings")
    slot: AllocatedSlot = storage.allocate("REC-20260519143022-a1b2c3")
    src = tmp_path / "in.mp4"
    src.write_bytes(b"")
    captured: dict[str, object] = {}

    def _fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        raise _timeout(kwargs)

    with patch("auto_a11y.audio.segmenter.detect_ffmpeg", return_value="/usr/bin/ffmpeg"), \
         patch("auto_a11y.audio.segmenter.subprocess.run", side_effect=_fake_run):
        with pytest.raises(AudioPipelineError):
            extract_segment(
                input_mp4=src,
                slot=slot,
                segment=Segment(index=0, start_s=0.0, end_s=10.0),
            )

    assert "timeout" in captured
    timeout_val = captured["timeout"]
    assert isinstance(timeout_val, (int, float)) and timeout_val > 0


def test_render_callouts_video_passes_timeout_and_wraps_timeout_expired(tmp_path: Path) -> None:
    from auto_a11y.audio.callouts import render_callouts_video

    source = tmp_path / "source.mp4"
    source.write_bytes(b"")
    output = tmp_path / "out.mp4"
    captured: dict[str, object] = {}

    def _fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        raise _timeout(kwargs)

    with patch("auto_a11y.audio.callouts.detect_ffmpeg", return_value="/usr/bin/ffmpeg"), \
         patch("auto_a11y.audio.callouts.subprocess.run", side_effect=_fake_run):
        with pytest.raises(CalloutsError):
            render_callouts_video(source_mp4=source, issues=[], output_mp4=output)

    assert "timeout" in captured
    timeout_val = captured["timeout"]
    assert isinstance(timeout_val, (int, float)) and timeout_val > 0


# --------------------------------------------------------------------------
# Bug B — timestamp :60 rollover
# --------------------------------------------------------------------------

# Both formatters share the WebVTT ``HH:MM:SS.mmm`` output contract; the two
# helpers live in different modules but must round identically. They are
# module-private (leading underscore), so we bind them through ``getattr`` to
# a typed ``Callable`` — reaching in by name directly would be a private-usage
# access, and this keeps the test focused on their rounding contract.
_fmt_ts: Callable[[float], str] = getattr(transcription, "_fmt_ts")
_seconds_to_ts: Callable[[float], str] = getattr(vtt_processor, "_seconds_to_ts")
_FORMATTERS: list[Callable[[float], str]] = [_fmt_ts, _seconds_to_ts]


@pytest.mark.parametrize("fmt", _FORMATTERS)
def test_timestamp_rollover_never_emits_60_seconds(fmt: Callable[[float], str]) -> None:
    # 119.9999s rounds the seconds field up to 60 with the naive
    # ``seconds % 60`` approach; must roll the minute over instead.
    ts = fmt(119.9999)
    assert ts == "00:02:00.000", ts


@pytest.mark.parametrize("fmt", _FORMATTERS)
def test_timestamp_normal_value_format_preserved(fmt: Callable[[float], str]) -> None:
    assert fmt(65.5) == "00:01:05.500"


@pytest.mark.parametrize("fmt", _FORMATTERS)
def test_timestamp_hour_rollover(fmt: Callable[[float], str]) -> None:
    # 3599.9999s → seconds rounds to 60 → minute to 60 → hour rolls over.
    assert fmt(3599.9999) == "01:00:00.000"


@pytest.mark.parametrize("fmt", _FORMATTERS)
def test_timestamp_zero(fmt: Callable[[float], str]) -> None:
    assert fmt(0.0) == "00:00:00.000"
