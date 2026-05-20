"""Silence-aware audio extraction and segmentation.

Ported from ``pythonAudioA11y/audio_processor.py``. The pure-Python pieces
(:func:`parse_silencedetect_output`, :func:`pick_split_points`,
:func:`segments_from_splits`) are unit-tested in isolation; the ffmpeg-
touching wrappers (:func:`probe_duration`, :func:`detect_silences`,
:func:`extract_segment`, :func:`split`) are covered by the
``@pytest.mark.ffmpeg`` integration tests.

The default split target is 600 s (10 minutes), with a ±30 s window to
land the boundary on a natural silence. Deepgram handles ~10 minute
segments far more reliably than huge files, so the value is deliberate.
"""
from __future__ import annotations

import json
import logging
import math
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from auto_a11y.audio.ffmpeg import detect_ffmpeg, detect_ffprobe
from auto_a11y.audio.storage import AllocatedSlot

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SilencePoint:
    """One silence period detected by ffmpeg's ``silencedetect`` filter."""

    start: float
    end: float
    duration: float


@dataclass(frozen=True)
class Segment:
    """One audio segment, identified by index and [start_s, end_s)."""

    index: int
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


_SILENCE_START = re.compile(r"silence_start:\s*([\d.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)")


def parse_silencedetect_output(stderr: str) -> list[SilencePoint]:
    """Pair up ``silence_start`` / ``silence_end`` lines into SilencePoint records.

    ffmpeg's ``-af silencedetect`` emits two lines per silence period::

        [silencedetect @ 0x...] silence_start: 5.234
        [silencedetect @ 0x...] silence_end: 5.890 | silence_duration: 0.656

    Unmatched ``silence_start`` lines (e.g., file ends mid-silence) are
    discarded.
    """
    starts: list[float] = []
    points: list[SilencePoint] = []
    for line in stderr.splitlines():
        m_start = _SILENCE_START.search(line)
        if m_start:
            starts.append(float(m_start.group(1)))
            continue
        m_end = _SILENCE_END.search(line)
        if m_end and starts:
            start = starts.pop(0)
            points.append(
                SilencePoint(
                    start=start,
                    end=float(m_end.group(1)),
                    duration=float(m_end.group(2)),
                )
            )
    return points


def pick_split_points(
    *,
    total_duration: float,
    silences: list[SilencePoint],
    target_s: float = 600.0,
    window_s: float = 30.0,
    min_last_segment_s: float = 300.0,
) -> list[float]:
    """Pick split points near each multiple of ``target_s``.

    For each target boundary (``target_s``, ``2 * target_s``, ...), find
    the silence whose ``end`` is closest within ±``window_s``. Falls
    back to a hard split at the target if no silence is found in the
    window. Returns the list of split timestamps (using the *end* of
    each chosen silence so the next segment starts on speech, not in
    the silence).

    The number of ideal boundaries is ``ceil(total_duration / target_s)
    - 1`` — i.e., enough to produce ``ceil(total_duration / target_s)``
    segments. The trailing piece is never assigned its own boundary, so
    a 2400 s source split at 600 s produces 3 splits (4 segments), not 4.

    After silence-nudging, if the trailing segment (between the final
    split and ``total_duration``) would be shorter than
    ``min_last_segment_s``, the final split is dropped so the tail gets
    absorbed into the previous segment. Mirrors
    ``pythonAudioA11y/audio_processor.py:148-154``.
    """
    num_segments = math.ceil(total_duration / target_s)
    ideal_points = [i * target_s for i in range(1, num_segments)]

    splits: list[float] = []
    for ideal in ideal_points:
        candidates = [
            s for s in silences
            if abs(s.end - ideal) <= window_s and s.end < total_duration
        ]
        if candidates:
            best = min(candidates, key=lambda s: abs(s.end - ideal))
            splits.append(best.end)
        else:
            splits.append(ideal)

    # Match source: re-check the tail *after* silence-nudging may have
    # moved the final split. If the trailing segment is too short, drop
    # the final split so it merges into the previous segment.
    if splits and (total_duration - splits[-1]) < min_last_segment_s:
        splits.pop()
    return splits


def segments_from_splits(*, total_duration: float, splits: list[float]) -> list[Segment]:
    """Convert a sorted list of split timestamps into Segment records.

    N splits produce N+1 segments. The first segment starts at 0, the
    last segment ends at ``total_duration``.
    """
    out: list[Segment] = []
    last = 0.0
    for i, s in enumerate(splits):
        out.append(Segment(index=i, start_s=last, end_s=s))
        last = s
    out.append(Segment(index=len(splits), start_s=last, end_s=total_duration))
    return out


def probe_duration(input_mp4: Path) -> float:
    """Use ffprobe to return the source's duration in seconds."""
    ffprobe = detect_ffprobe(raise_if_missing=True)
    assert ffprobe is not None  # raise_if_missing=True guarantees non-None
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [
            ffprobe,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(input_mp4),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def detect_silences(
    input_mp4: Path,
    *,
    noise_db: int = -30,
    min_silence_s: float = 0.4,
) -> list[SilencePoint]:
    """Run ffmpeg with ``-af silencedetect`` and parse the stderr output."""
    ffmpeg = detect_ffmpeg(raise_if_missing=True)
    assert ffmpeg is not None  # raise_if_missing=True guarantees non-None
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [
            ffmpeg,
            "-i", str(input_mp4),
            "-af", f"silencedetect=noise={noise_db}dB:d={min_silence_s}",
            "-f", "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    parsed = parse_silencedetect_output(result.stderr)
    if result.returncode != 0 and not parsed:
        # ffmpeg exited with error AND produced no silence detections —
        # likely a corrupt or unreadable input file. Log so the runner
        # can surface it (the pipeline still falls back to ideal points).
        logger.warning(
            "detect_silences: ffmpeg returncode=%s for %s; stderr tail: %s",
            result.returncode, input_mp4.name, result.stderr[-500:],
        )
    return parsed


def extract_segment(
    *,
    input_mp4: Path,
    slot: AllocatedSlot,
    segment: Segment,
) -> Path:
    """Extract one segment as an m4a, stream-copying the source audio."""
    ffmpeg = detect_ffmpeg(raise_if_missing=True)
    assert ffmpeg is not None  # raise_if_missing=True guarantees non-None
    out = slot.segment_m4a(segment.index)
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i", str(input_mp4),
            "-ss", f"{segment.start_s:.3f}",
            "-to", f"{segment.end_s:.3f}",
            # Stream-copy: no re-encode. Matches
            # pythonAudioA11y/audio_processor.py and avoids generational
            # lossy re-encode of source AAC; ~30-60x faster on a typical
            # 1-hour audit recording.
            "-vn", "-acodec", "copy", "-map", "0:a",
            str(out),
        ],
        capture_output=True,
        check=True,
    )
    return out


def split(
    input_mp4: Path,
    slot: AllocatedSlot,
    *,
    target_s: float = 600.0,
) -> list[Segment]:
    """Main entry point: probe → detect silences → pick splits → extract.

    Writes ``slot.audio_dir / "segments.json"`` as the manifest of all
    segments produced. Returns the list of :class:`Segment` records.
    """
    duration = probe_duration(input_mp4)
    silences = detect_silences(input_mp4)
    splits_at = pick_split_points(
        total_duration=duration,
        silences=silences,
        target_s=target_s,
    )
    segments = segments_from_splits(total_duration=duration, splits=splits_at)

    # Write the manifest *before* extraction: it's planning data, so a
    # crash mid-loop still leaves an inspectable record of what was
    # supposed to be extracted. ``segment_m4a(i).exists()`` is the
    # source of truth for what actually got produced.
    manifest_path = slot.audio_dir / "segments.json"
    manifest_bytes = json.dumps(
        [asdict(s) for s in segments], indent=2
    ).encode("utf-8")
    slot.write_atomic(manifest_path, manifest_bytes)

    for seg in segments:
        extract_segment(input_mp4=input_mp4, slot=slot, segment=seg)

    return segments
