"""Tests for ffmpeg / ffprobe binary detection."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from auto_a11y.audio.errors import FfmpegMissing
from auto_a11y.audio.ffmpeg import detect_ffmpeg, detect_ffprobe, invalidate_detection_cache


@pytest.fixture(autouse=True)
def reset_cache() -> None:
    invalidate_detection_cache()


def test_detect_ffmpeg_finds_path_via_which() -> None:
    with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
        assert detect_ffmpeg() == "/usr/bin/ffmpeg"


def test_detect_ffmpeg_returns_none_when_missing() -> None:
    with patch("shutil.which", return_value=None):
        assert detect_ffmpeg() is None


def test_detect_ffmpeg_raises_when_required() -> None:
    with patch("shutil.which", return_value=None):
        with pytest.raises(FfmpegMissing):
            detect_ffmpeg(raise_if_missing=True)


def test_override_skips_detection() -> None:
    with patch("shutil.which", return_value=None):
        assert detect_ffmpeg(override="/opt/local/bin/ffmpeg") == "/opt/local/bin/ffmpeg"


def test_detection_cached_within_process() -> None:
    with patch("shutil.which", return_value="/usr/bin/ffmpeg") as which:
        detect_ffmpeg()
        detect_ffmpeg()
        assert which.call_count == 1  # second call hit the cache


def test_invalidate_clears_cache() -> None:
    with patch("shutil.which", return_value="/usr/bin/ffmpeg") as which:
        detect_ffmpeg()
        invalidate_detection_cache()
        detect_ffmpeg()
        assert which.call_count == 2


def test_detect_ffprobe_is_independent_helper() -> None:
    with patch("shutil.which", return_value="/usr/bin/ffprobe"):
        assert detect_ffprobe() == "/usr/bin/ffprobe"
