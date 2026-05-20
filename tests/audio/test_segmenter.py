"""Unit tests for audio/segmenter.py — pure-Python helpers.

Integration tests against real ffmpeg live below the @pytest.mark.ffmpeg
marker and skip if no fixture MP4 exists.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from auto_a11y.audio.segmenter import (
    Segment,
    SilencePoint,
    parse_silencedetect_output,
    pick_split_points,
    segments_from_splits,
)


def test_parse_silencedetect_output() -> None:
    raw = (
        "[silencedetect @ 0x...] silence_start: 5.234\n"
        "[silencedetect @ 0x...] silence_end: 5.890 | silence_duration: 0.656\n"
        "[silencedetect @ 0x...] silence_start: 120.001\n"
        "[silencedetect @ 0x...] silence_end: 120.500 | silence_duration: 0.499\n"
    )
    points = parse_silencedetect_output(raw)
    assert points == [
        SilencePoint(start=5.234, end=5.890, duration=0.656),
        SilencePoint(start=120.001, end=120.500, duration=0.499),
    ]


def test_pick_split_points_uses_silence_near_target() -> None:
    silences = [
        SilencePoint(start=200.0, end=200.5, duration=0.5),
        SilencePoint(start=597.0, end=597.7, duration=0.7),
        SilencePoint(start=1205.0, end=1205.4, duration=0.4),
        SilencePoint(start=1799.0, end=1799.8, duration=0.8),
    ]
    splits = pick_split_points(total_duration=2400.0, silences=silences, target_s=600.0, window_s=30.0)
    assert splits == [597.7, 1205.4, 1799.8]


def test_pick_split_points_falls_back_to_target_when_no_silence_in_window() -> None:
    silences = [SilencePoint(start=10.0, end=10.5, duration=0.5)]
    splits = pick_split_points(total_duration=1200.0, silences=silences, target_s=600.0, window_s=30.0)
    assert splits == [600.0]


def test_pick_split_points_drops_last_when_tail_too_short() -> None:
    # 1250s total, target 600s → 3 segments → 2 ideal splits at 600, 1200.
    # No silences; both splits are hard. After splitting, tail = 1250 - 1200 = 50s.
    # 50s < min_last_segment_s=300s → drop the 1200 split.
    splits = pick_split_points(
        total_duration=1250.0,
        silences=[],
        target_s=600.0,
        window_s=30.0,
        min_last_segment_s=300.0,
    )
    assert splits == [600.0]


def test_pick_split_points_keeps_last_when_tail_long_enough() -> None:
    # 1800s total, target 600s → 3 segments → 2 ideal splits at 600, 1200.
    # Tail = 1800 - 1200 = 600s ≥ 300s → keep both.
    splits = pick_split_points(
        total_duration=1800.0, silences=[], target_s=600.0, window_s=30.0,
        min_last_segment_s=300.0,
    )
    assert splits == [600.0, 1200.0]


def test_pick_split_points_drop_uses_nudged_value() -> None:
    # Silence nudges the 1200 split forward to 1205.4 (within window).
    # Tail = 1250 - 1205.4 = 44.6s < 300s → still drop.
    splits = pick_split_points(
        total_duration=1250.0,
        silences=[SilencePoint(start=1205.0, end=1205.4, duration=0.4)],
        target_s=600.0, window_s=30.0,
        min_last_segment_s=300.0,
    )
    assert splits == [600.0]


def test_segment_duration_s_property() -> None:
    seg = Segment(index=0, start_s=10.0, end_s=85.5)
    assert seg.duration_s == 75.5


def test_pick_split_points_when_total_under_target() -> None:
    # Recording shorter than one target segment → no splits at all.
    splits = pick_split_points(
        total_duration=300.0, silences=[], target_s=600.0, window_s=30.0,
    )
    assert splits == []


def test_parse_silencedetect_output_handles_unmatched_start() -> None:
    # silence_start with no matching silence_end → discarded.
    raw = (
        "[silencedetect] silence_start: 100.0\n"
        "[silencedetect] silence_start: 200.0\n"
        "[silencedetect] silence_end: 200.5 | silence_duration: 0.5\n"
    )
    points = parse_silencedetect_output(raw)
    # First silence_start is paired with the only silence_end; second start lingers.
    assert len(points) == 1
    assert points[0].start == 100.0
    assert points[0].end == 200.5


def test_segments_from_splits() -> None:
    segments = segments_from_splits(total_duration=1500.0, splits=[600.0, 1200.0])
    assert segments == [
        Segment(index=0, start_s=0.0, end_s=600.0),
        Segment(index=1, start_s=600.0, end_s=1200.0),
        Segment(index=2, start_s=1200.0, end_s=1500.0),
    ]


@pytest.mark.ffmpeg
def test_split_against_real_fixture(tmp_path: Path) -> None:
    """End-to-end: real ffmpeg, fixture MP4 (~30 s), verify segments produced."""
    fixture = Path(__file__).parent / "fixtures" / "short_audit.mp4"
    if not fixture.exists():
        pytest.skip(f"fixture not present: {fixture} (run scripts/fetch_audio_fixtures.py)")

    from auto_a11y.audio.segmenter import split
    from auto_a11y.audio.storage import AudioStorage

    storage = AudioStorage(root=tmp_path / "recordings")
    slot = storage.allocate("REC-20260519143022-a1b2c3")

    segments = split(fixture, slot, target_s=10.0)
    assert len(segments) >= 2
    assert slot.segment_m4a(0).is_file()
    manifest = json.loads((slot.audio_dir / "segments.json").read_text())
    assert manifest[0]["index"] == 0
