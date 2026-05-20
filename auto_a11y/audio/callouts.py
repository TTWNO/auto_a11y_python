"""ffmpeg ``drawtext``-overlay rendering for issue callouts.

Optional Stage F of the audioA11y pipeline. Takes the ``issues`` JSON
payload (Claude's analyze output) and the source MP4, produces an
annotated MP4 at ``slot.callouts_mp4`` with text overlays at each
issue's timecode range and (best-effort) chapter markers.

Failure NEVER fails the whole job — the runner catches
:class:`CalloutsError` raised here and sets
``Recording.callouts_status = "failed"`` while the rest of the audit
completes normally. See ``auto_a11y/audio/runner.py``.

Ported (loosely) from ``pythonAudioA11y/video_processor.py``. We
deliberately keep the implementation much smaller than the source:

- No watermark, no title page, no two-pass re-encode.
- Chapters embedded via ffmpeg's ``-map_chapters`` from a temp
  metadata file (MP4Box fallback is documented as future work; the
  binary is not commonly installed on the deployment targets).
- Text wrapping / overlap-avoidance are NOT implemented here — the
  drawtext expression simply renders ``short_title`` in the same
  position for the full timecode range. If a future iteration wants
  the visually-rich layout from the source, the pure-function helpers
  below give it a clean starting point.

The two public pure functions are unit-tested in isolation; the
subprocess-touching :func:`render_callouts_video` is covered by
mocked-``subprocess.run`` tests that assert the argv layout.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TypeGuard

from auto_a11y.audio.errors import CalloutsError
from auto_a11y.audio.ffmpeg import detect_ffmpeg

logger = logging.getLogger(__name__)


def _is_str_obj_dict(val: object) -> TypeGuard[dict[str, object]]:
    """Narrow ``object`` to ``dict[str, object]`` for nested JSON values.

    ``json.loads`` always produces ``str``-keyed dicts for JSON
    objects; the runtime check is ``isinstance(val, dict)`` only.
    Pyright won't narrow ``raw_tc.get("start")`` past ``Unknown``
    without this TypeGuard.
    """
    return isinstance(val, dict)


def _is_obj_list(val: object) -> TypeGuard[list[object]]:
    """Narrow ``object`` to ``list[object]`` for nested JSON values."""
    return isinstance(val, list)


# Single drawtext font size for now. The source video processor
# computed a size proportional to video height; we keep things simple
# and let ffmpeg use a sensible default size that's readable on 1080p.
_DEFAULT_FONT_SIZE = 36


@dataclass(frozen=True)
class Callout:
    """One issue rendered as a text overlay at a specific timecode range.

    Multiple ``Callout`` records can refer back to the same source
    issue — :func:`parse_issues_to_callouts` emits one record per
    ``timecodes[]`` entry on the issue.
    """

    start_s: float
    end_s: float
    short_title: str


# --- pure helpers --------------------------------------------------------


def parse_timecode_to_seconds(timecode: str) -> float | None:
    """Convert a ``"HH:MM:SS"`` (or ``"MM:SS"``) string to seconds.

    Returns ``None`` if the string is empty or doesn't parse. Accepts
    optional ``.mmm`` milliseconds. Returning ``None`` (rather than
    raising) lets the parse loop skip a single malformed timecode
    without aborting the whole callouts stage.
    """
    if not timecode:
        return None
    text = timecode.strip()
    if not text:
        return None
    # Split off optional .mmm milliseconds.
    main_part, _, ms_part = text.partition(".")
    parts = main_part.split(":")
    try:
        if len(parts) == 3:
            hours, minutes, seconds = (int(p) for p in parts)
            total = hours * 3600 + minutes * 60 + seconds
        elif len(parts) == 2:
            minutes, seconds = (int(p) for p in parts)
            total = minutes * 60 + seconds
        elif len(parts) == 1:
            total = int(parts[0])
        else:
            return None
        if ms_part:
            # Pad / truncate to exactly 3 digits so "1" → 100 ms, "12" → 120 ms.
            ms_digits = (ms_part + "000")[:3]
            total_seconds = float(total) + int(ms_digits) / 1000.0
        else:
            total_seconds = float(total)
    except ValueError:
        return None
    return total_seconds


def parse_issues_to_callouts(issues_json: dict[str, object]) -> list[Callout]:
    """Extract ``Callout`` records from a dictaphone-shape issues payload.

    Expected shape::

        {
          "recording": "...",
          "issues": [
            {
              "short_title": "Low contrast",
              "timecodes": [
                {"start": "00:00:10", "end": "00:00:15", "duration": "00:00:05"}
              ]
            }
          ]
        }

    Each issue can have multiple ``timecodes[]`` entries; one
    :class:`Callout` is emitted per range. Issues without a
    ``short_title``, without any timecodes, or with malformed
    ``start``/``end`` strings are skipped silently (the callouts stage
    is best-effort).
    """
    raw_issues = issues_json.get("issues")
    if not _is_obj_list(raw_issues):
        return []
    out: list[Callout] = []
    for raw_issue_obj in raw_issues:
        if not _is_str_obj_dict(raw_issue_obj):
            continue
        short_title_obj = raw_issue_obj.get("short_title")
        if not isinstance(short_title_obj, str):
            continue
        short_title = short_title_obj.strip()
        if not short_title:
            continue
        raw_timecodes = raw_issue_obj.get("timecodes")
        if not _is_obj_list(raw_timecodes):
            continue
        for raw_tc_obj in raw_timecodes:
            if not _is_str_obj_dict(raw_tc_obj):
                continue
            start_obj = raw_tc_obj.get("start")
            end_obj = raw_tc_obj.get("end")
            if not isinstance(start_obj, str) or not isinstance(end_obj, str):
                continue
            start_s = parse_timecode_to_seconds(start_obj)
            end_s = parse_timecode_to_seconds(end_obj)
            if start_s is None or end_s is None:
                continue
            if end_s <= start_s:
                continue
            out.append(Callout(start_s=start_s, end_s=end_s, short_title=short_title))
    return out


def _escape_drawtext_text(text: str) -> str:
    """Escape ``text`` for use inside ``drawtext`` ``text='...'``.

    ffmpeg's filtergraph parser treats backslash, single quote, colon,
    and percent-sign specially inside a drawtext expression. The full
    rules are documented at
    https://ffmpeg.org/ffmpeg-filters.html#drawtext — backslash is the
    escape character; a single quote inside a single-quoted string is
    expressed by closing the quote, inserting an escaped quote, and
    reopening: ``foo'bar`` → ``foo'\\''bar``.

    We also normalise Unicode "smart quotes" to ASCII so the filter
    string stays in the ffmpeg-safe character set (drawtext defaults
    to a sans-serif fontconfig font that doesn't always include the
    typographic variants).
    """
    # Normalise smart quotes.
    text = (
        text.replace("‘", "'")
            .replace("’", "'")
            .replace("“", '"')
            .replace("”", '"')
    )
    # Order matters: backslash first, then the rest.
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("%", "\\%")
    # Single-quote-inside-single-quoted-string trick.
    text = text.replace("'", "'\\''")
    return text


def build_drawtext_filter(
    callouts: list[Callout],
    *,
    font_size: int = _DEFAULT_FONT_SIZE,
) -> str:
    """Build a single ffmpeg ``-vf`` filter string overlaying all callouts.

    Each callout becomes a ``drawtext=...`` expression with an
    ``enable=between(t,start,end)`` clause so the overlay is only
    visible during its timecode range. Multiple drawtext expressions
    are chained with commas (filtergraph composition).

    Returns ``"null"`` (ffmpeg's pass-through video filter) when the
    callouts list is empty, so callers can always pass the result
    through unconditionally.
    """
    if not callouts:
        return "null"
    parts: list[str] = []
    for c in callouts:
        escaped = _escape_drawtext_text(c.short_title)
        # Layout: white text on a semi-opaque dark-red box near the top.
        # Margins are expressed in pixels relative to the input frame;
        # ffmpeg's drawtext expression language exposes W/H for frame
        # width / height.
        parts.append(
            "drawtext="
            + f"text='{escaped}':"
            + f"fontsize={font_size}:"
            + "fontcolor=white:"
            + "box=1:"
            + "boxcolor=0x8B0000@0.85:"
            + "boxborderw=10:"
            + "x=(w-text_w)/2:"
            + "y=h*0.08:"
            + f"enable='between(t,{c.start_s:.3f},{c.end_s:.3f})'"
        )
    return ",".join(parts)


def build_chapters_metadata(callouts: list[Callout]) -> str:
    """Build an ffmpeg ``FFMETADATA1`` chapter file body.

    Each callout becomes one chapter spanning its timecode range. We
    bump duplicate start times by 1 ms so the chapter list is strictly
    monotonic (ffmpeg drops chapters with non-monotonic boundaries).
    """
    lines: list[str] = [";FFMETADATA1"]
    sorted_callouts = sorted(callouts, key=lambda c: c.start_s)
    seen_starts_ms: set[int] = set()
    for c in sorted_callouts:
        start_ms = int(c.start_s * 1000)
        end_ms = int(c.end_s * 1000)
        while start_ms in seen_starts_ms:
            start_ms += 1
        seen_starts_ms.add(start_ms)
        if end_ms <= start_ms:
            end_ms = start_ms + 1
        title = c.short_title.replace("\n", " ").replace("\r", "")
        lines.append("[CHAPTER]")
        lines.append("TIMEBASE=1/1000")
        lines.append(f"START={start_ms}")
        lines.append(f"END={end_ms}")
        lines.append(f"title={title}")
        lines.append("")
    return "\n".join(lines) + "\n"


# --- subprocess + chapter embedding -------------------------------------


def _coerce_issues_to_payload(issues: list[dict[str, object]]) -> dict[str, object]:
    """Wrap a list of issue dicts in the ``{"issues": [...]}`` envelope.

    :func:`parse_issues_to_callouts` accepts the full payload shape so
    callers that already have just the inner list don't have to
    reconstruct the envelope.
    """
    return {"issues": list(issues)}


def render_callouts_video(
    *,
    source_mp4: Path,
    issues: list[dict[str, object]],
    output_mp4: Path,
    mp4box_path: str | None = None,
) -> None:
    """Render an annotated MP4 with text overlays at each issue's timecodes.

    1. Parse the issues list into :class:`Callout` records.
    2. Build the ``drawtext`` filter expression for the ``-vf`` arg.
    3. Write a temporary ``FFMETADATA1`` chapter file.
    4. Shell out to ffmpeg: re-encode video with libx264 + the
       drawtext filter, stream-copy audio, embed chapters via
       ``-map_chapters``.

    ``mp4box_path`` is accepted but currently unused — see the module
    docstring for the rationale. The signature is kept stable so a
    future iteration can plug MP4Box in without churning callers.

    Raises:
        :class:`CalloutsError` on any subprocess failure (ffmpeg
        binary missing, non-zero exit, output not produced). Stage F
        in :mod:`auto_a11y.audio.pipeline` wraps this in a try/except
        so the whole job doesn't fail.
    """
    _ = mp4box_path  # see module docstring — reserved for future use.

    ffmpeg = detect_ffmpeg()
    if ffmpeg is None:
        raise CalloutsError("ffmpeg not on PATH; cannot render callouts")

    callouts = parse_issues_to_callouts(_coerce_issues_to_payload(issues))
    drawtext_filter = build_drawtext_filter(callouts)
    chapters_body = build_chapters_metadata(callouts)

    output_mp4.parent.mkdir(parents=True, exist_ok=True)

    # Write chapters to a temp file so ffmpeg's ``-i metadata`` flag
    # can pick them up. We clean it up in ``finally`` regardless of
    # subprocess outcome.
    chapter_fd, chapter_path_str = tempfile.mkstemp(
        suffix=".txt", prefix="callouts_chapters_"
    )
    chapter_path = Path(chapter_path_str)
    try:
        with os.fdopen(chapter_fd, "w", encoding="utf-8") as f:
            f.write(chapters_body)

        cmd: list[str] = [
            ffmpeg,
            "-y",
            "-i", str(source_mp4),
            "-i", str(chapter_path),
            "-map_metadata", "1",
            "-map_chapters", "1",
            "-vf", drawtext_filter,
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(output_mp4),
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as e:
            raise CalloutsError(
                f"ffmpeg invocation failed: {e}"
            ) from e

        if result.returncode != 0:
            stderr_tail = result.stderr[-2000:] if result.stderr else ""
            raise CalloutsError(
                f"ffmpeg exited with code {result.returncode} "
                + f"while rendering callouts: {stderr_tail}"
            )

        if not output_mp4.exists():
            raise CalloutsError(
                f"ffmpeg returned success but {output_mp4} was not created"
            )
    finally:
        try:
            chapter_path.unlink(missing_ok=True)
        except OSError:
            pass
