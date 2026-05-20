"""Tests for audio/vtt_processor.py — merging per-segment VTTs."""
from __future__ import annotations

from pathlib import Path

from auto_a11y.audio.vtt_processor import merge_segment_vtts


def test_merge_offsets_timestamps_per_segment(tmp_path: Path) -> None:
    s0 = tmp_path / "segment-0.vtt"
    s1 = tmp_path / "segment-1.vtt"
    s0.write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:05.000\n<v Speaker_0>Hello world\n\n"
    )
    s1.write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n<v Speaker_0>Second segment\n\n"
    )
    out = tmp_path / "merged.vtt"
    merge_segment_vtts(
        segment_paths=[s0, s1],
        segment_offsets_s=[0.0, 600.0],
        output=out,
    )
    txt = out.read_text()
    assert "WEBVTT" in txt
    assert "00:00:00.000 --> 00:00:05.000" in txt
    assert "00:10:01.000 --> 00:10:04.000" in txt  # offset by 600 s
    assert "Hello world" in txt
    assert "Second segment" in txt


def test_merge_preserves_cue_settings(tmp_path: Path) -> None:
    s0 = tmp_path / "segment-0.vtt"
    s0.write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:05.000 line:90% align:left\n<v Speaker_0>Hello\n\n"
    )
    out = tmp_path / "merged.vtt"
    merge_segment_vtts(segment_paths=[s0], segment_offsets_s=[0.0], output=out)
    text = out.read_text()
    assert "line:90% align:left" in text


def test_merge_inserts_blank_line_between_segments_when_missing(tmp_path: Path) -> None:
    """Segments without trailing blank line must be cleanly joined."""
    s0 = tmp_path / "segment-0.vtt"
    s1 = tmp_path / "segment-1.vtt"
    # s0 has NO trailing blank line — ends right after the cue text.
    s0.write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:05.000\n<v Speaker_0>First\n"
    )
    s1.write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n<v Speaker_0>Second\n"
    )
    out = tmp_path / "merged.vtt"
    merge_segment_vtts(
        segment_paths=[s0, s1], segment_offsets_s=[0.0, 100.0], output=out,
    )
    text = out.read_text()
    # First cue intact.
    assert "<v Speaker_0>First" in text
    # Second cue intact and timestamp offset.
    assert "00:01:41.000 --> 00:01:44.000" in text
    assert "<v Speaker_0>Second" in text
    # A blank line MUST separate the two cues.
    lines = text.splitlines()
    first_idx = lines.index("<v Speaker_0>First")
    second_ts_idx = lines.index("00:01:41.000 --> 00:01:44.000")
    assert any(lines[i] == "" for i in range(first_idx + 1, second_ts_idx)), \
        "expected at least one blank line between merged segments"
