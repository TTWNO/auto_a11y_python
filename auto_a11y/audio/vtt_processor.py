"""Merge per-segment VTT files into a single VTT with corrected timestamps.

Ported from pythonAudioA11y/vtt_processor.py - pure Python, no media calls.
"""
from __future__ import annotations

import re
from pathlib import Path


_CUE_TS = re.compile(
    r"^(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})(.*)$"
)


def _ts_to_seconds(ts: str) -> float:
    h, m, rest = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def _seconds_to_ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def merge_segment_vtts(
    *,
    segment_paths: list[Path],
    segment_offsets_s: list[float],
    output: Path,
) -> None:
    """Concatenate segment VTTs into one, adding the segment offset to every timestamp.

    Each ``segment_offsets_s[i]`` (in seconds) is added to every cue
    timestamp found in ``segment_paths[i]``. The merged file emits exactly
    one ``WEBVTT`` header at the top regardless of how many segments
    contributed.

    Raises:
        ValueError: if the two list parameters have different lengths.
    """
    if len(segment_paths) != len(segment_offsets_s):
        raise ValueError("segment_paths and segment_offsets_s must align")

    lines: list[str] = ["WEBVTT", ""]
    for i, (path, offset) in enumerate(zip(segment_paths, segment_offsets_s, strict=True)):
        if i > 0 and lines and lines[-1] != "":
            lines.append("")
        text = path.read_text()
        for line in text.splitlines():
            m = _CUE_TS.match(line)
            if m:
                start = _ts_to_seconds(m.group(1)) + offset
                end = _ts_to_seconds(m.group(2)) + offset
                lines.append(f"{_seconds_to_ts(start)} --> {_seconds_to_ts(end)}{m.group(3)}")
            elif line.strip().upper() == "WEBVTT":
                continue  # already emitted once at the top
            else:
                lines.append(line)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines))
