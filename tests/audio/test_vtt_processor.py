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
