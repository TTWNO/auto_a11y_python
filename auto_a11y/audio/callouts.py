"""ffmpeg ``drawtext``-overlay rendering for issue callouts.

Optional Stage F of the audioA11y pipeline. Takes the ``issues`` JSON
payload (Claude's analyze output) and the source MP4, produces an
annotated MP4 at ``slot.callouts_mp4`` with: a CNIB-yellow start title
card (AccessLabs logo + copyright), the source video carrying callout
text overlays plus a persistent bottom-right logo watermark, an identical
end title card, and (best-effort) chapter markers.

Failure NEVER fails the whole job — the runner catches
:class:`CalloutsError` raised here and sets
``Recording.callouts_status = "failed"`` while the rest of the audit
completes normally. See ``auto_a11y/audio/runner.py``.

Ported from ``pythonAudioA11y/video_processor.py`` (the original
"Dictaphone"), but with two deliberate differences that make it work
inside the packaged macOS ``.app``:

- The logo is a pre-rasterised PNG shipped in ``assets/`` and resolved
  relative to this module (``_asset_path``), NOT a CWD-relative SVG. The
  bundled static ffmpeg can't decode SVG and the app doesn't bundle
  rsvg-convert/ImageMagick, so the original's approach silently produced
  no logo in the build. See the title-card constants below.
- Title cards + watermark + callouts are composed in a single
  ``-filter_complex`` pass (concat filter), avoiding the original's
  fragile render-then-concat-demuxer step that requires codec/resolution
  matching across separately-encoded clips.

Text wrapping / overlap-avoidance are still NOT implemented — the
drawtext expression renders ``short_title`` in a fixed position for the
full timecode range.

The pure helpers (``build_drawtext_filter``, ``build_full_filtergraph``,
``build_chapters_metadata`` …) are unit-tested in isolation; the
subprocess-touching :func:`render_callouts_video` is covered by
mocked-``subprocess.run`` tests that assert the argv layout.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TypeGuard

from auto_a11y.audio.errors import CalloutsError
from auto_a11y.audio.ffmpeg import detect_ffmpeg, detect_ffprobe

logger = logging.getLogger(__name__)

# --- title-card / watermark constants -----------------------------------
#
# Ported from ``pythonAudioA11y/video_processor.py`` (the original
# "Dictaphone"), which rendered a CNIB-yellow start title page with the
# AccessLabs logo + copyright line and a persistent bottom-right logo
# watermark. We render an identical card at BOTH the start and end of the
# annotated video, plus the watermark over the main content.
#
# The logo is shipped as a pre-rasterised PNG inside this package
# (``assets/accesslabs_logo.png``) rather than the SVG the original used:
# the bundled static ffmpeg cannot decode SVG (no librsvg) and the macOS
# app does not bundle rsvg-convert / ImageMagick, so a committed PNG
# resolved relative to this module is the only thing that works inside
# the packaged ``.app`` (CWD-relative paths do NOT — that was the bug).

TITLE_CARD_DURATION_S: float = 3.0
_TITLE_CARD_BG_COLOR = "0xFFF000"  # CNIB yellow
_CARD_LOGO_WIDTH_FRAC = 0.8        # title-card logo width as a fraction of frame width
_WATERMARK_WIDTH_FRAC = 0.12       # corner-watermark logo width as a fraction of frame width
_WATERMARK_MARGIN_PX = 15          # bottom-right inset, matches the original
_COPYRIGHT_FONT_FRAC = 0.04        # copyright font size as a fraction of frame height
_LOGO_ASSET_NAME = "accesslabs_logo.png"

# Callouts re-encode the full video with libx264, so this is the slowest
# ffmpeg pass in the pipeline. The timeout is generous, but a process that
# blows past it is hung — abort with a CalloutsError rather than block the
# job forever (Stage F never fails the whole job on a callouts error).
RENDER_TIMEOUT_SECONDS = 3600


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


def build_chapters_metadata(
    callouts: list[Callout],
    *,
    offset_s: float = 0.0,
    add_title_chapter: bool = False,
) -> str:
    """Build an ffmpeg ``FFMETADATA1`` chapter file body.

    Each callout becomes one chapter spanning its timecode range. We
    bump duplicate start times by 1 ms so the chapter list is strictly
    monotonic (ffmpeg drops chapters with non-monotonic boundaries).

    Args:
        offset_s: Seconds to add to every callout timestamp. Use this
            when a start title card of duration ``offset_s`` is prepended
            to the video so the chapters still line up with the content.
        add_title_chapter: When True, prepend a "Video Start" chapter at
            0:00 spanning the title card (``[0, offset_s)``), matching the
            original Dictaphone behaviour.
    """
    lines: list[str] = [";FFMETADATA1"]
    offset_ms = int(offset_s * 1000)
    seen_starts_ms: set[int] = set()

    if add_title_chapter:
        title_end_ms = offset_ms if offset_ms > 0 else 1
        seen_starts_ms.add(0)
        lines.append("[CHAPTER]")
        lines.append("TIMEBASE=1/1000")
        lines.append("START=0")
        lines.append(f"END={title_end_ms}")
        lines.append("title=Video Start")
        lines.append("")

    sorted_callouts = sorted(callouts, key=lambda c: c.start_s)
    for c in sorted_callouts:
        start_ms = int(c.start_s * 1000) + offset_ms
        end_ms = int(c.end_s * 1000) + offset_ms
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


def build_copyright_text(year: int) -> str:
    """Return the title-card copyright line, matching the original card."""
    return f"Copyright © {year} CNIB"


def _asset_path(name: str) -> Path:
    """Resolve a bundled asset path relative to THIS module.

    Anchored to ``__file__`` (not the process CWD) so it resolves both in
    a dev checkout and inside the packaged macOS ``.app``, where the
    Python tree is rsync'd to ``Resources/app`` and the working directory
    is not the app dir. This is the fix for the original's CWD-relative
    ``'accesslabs_logo.svg'`` lookup that silently failed in the bundle.
    """
    return Path(__file__).resolve().parent / "assets" / name


def build_full_filtergraph(
    callouts: list[Callout],
    *,
    width: int,
    height: int,
    year: int,
    logo_input_index: int = 1,
    title_duration: float = TITLE_CARD_DURATION_S,
    font_size: int = _DEFAULT_FONT_SIZE,
    fps: str = "30/1",
) -> str:
    """Build the ffmpeg ``-filter_complex`` graph for the annotated video.

    Concatenates three segments into ``[outv]`` / ``[outa]``:

    1. A start title card: CNIB-yellow background, centred AccessLabs
       logo, copyright line.
    2. The source video (input 0) with callout overlays plus a persistent
       bottom-right logo watermark.
    3. An end title card identical to the start card.

    The logo at ``[{logo_input_index}:v]`` is split and scaled into the
    two card copies and the watermark copy (an ffmpeg link label can only
    be consumed once, hence the ``split`` filters). All three video
    segments are normalised to ``yuv420p`` / square pixels / ``fps`` and
    all audio to stereo 44.1 kHz so the ``concat`` filter accepts them.
    """
    li = logo_input_index
    card_logo_w = max(1, int(width * _CARD_LOGO_WIDTH_FRAC))
    wm_logo_w = max(1, int(width * _WATERMARK_WIDTH_FRAC))
    copyright_fs = max(12, int(height * _COPYRIGHT_FONT_FRAC))
    logo_y_off = int(height * 0.1)
    copyright_y = int(height * 0.85)
    dur = f"{title_duration:.3f}"
    copyright_text = _escape_drawtext_text(build_copyright_text(year))

    chains: list[str] = []

    # Split + scale the logo: two card copies (start/end) + one watermark.
    chains.append(f"[{li}:v]split=2[logo_cards][logo_wm]")
    chains.append(f"[logo_cards]scale={card_logo_w}:-1,split=2[clogo_s][clogo_e]")
    chains.append(f"[logo_wm]scale={wm_logo_w}:-1[wlogo]")

    # Start + end cards (identical layout).
    for tag, logo_lbl, v_out in (
        ("s", "clogo_s", "startv"),
        ("e", "clogo_e", "endv"),
    ):
        chains.append(
            f"color=c={_TITLE_CARD_BG_COLOR}:s={width}x{height}:r={fps}:d={dur}[bg_{tag}]"
        )
        chains.append(
            f"[bg_{tag}][{logo_lbl}]overlay=(W-w)/2:(H-h)/2-{logo_y_off}[ov_{tag}]"
        )
        chains.append(
            f"[ov_{tag}]drawtext=text='{copyright_text}':fontsize={copyright_fs}:"
            + f"fontcolor=black:x=(w-text_w)/2:y={copyright_y},"
            + f"format=yuv420p,setsar=1[{v_out}]"
        )

    # Main video: callout overlays, then the persistent bottom-right watermark.
    main_drawtext = build_drawtext_filter(callouts, font_size=font_size)
    chains.append(f"[0:v]{main_drawtext}[main_dt]")
    chains.append(
        f"[main_dt][wlogo]overlay=W-w-{_WATERMARK_MARGIN_PX}:H-h-{_WATERMARK_MARGIN_PX},"
        + "format=yuv420p,setsar=1[mainv]"
    )

    # Audio: bounded silence for the cards + normalised source audio.
    chains.append(f"anullsrc=channel_layout=stereo:sample_rate=44100:d={dur}[starta]")
    chains.append(f"anullsrc=channel_layout=stereo:sample_rate=44100:d={dur}[enda]")
    chains.append("[0:a]aformat=sample_rates=44100:channel_layouts=stereo[maina]")

    # Concatenate start + main + end (video + audio together).
    chains.append(
        "[startv][starta][mainv][maina][endv][enda]concat=n=3:v=1:a=1[outv][outa]"
    )

    return ";".join(chains)


def _probe_video_dimensions(source_mp4: Path) -> tuple[int, int, str]:
    """Probe ``source_mp4`` for ``(width, height, fps)`` via ffprobe.

    ``fps`` is returned as an ffmpeg rate string (e.g. ``"30/1"``) so the
    generated title cards share the source's frame rate — the ``concat``
    filter needs matching frame rates across segments. Raises
    :class:`CalloutsError` if ffprobe is missing, fails, or returns
    unparseable output.
    """
    ffprobe = detect_ffprobe()
    if ffprobe is None:
        raise CalloutsError("ffprobe not on PATH; cannot probe video dimensions")
    cmd = [
        ffprobe,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate",
        "-of", "csv=s=,:p=0",
        str(source_mp4),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise CalloutsError(f"ffprobe invocation failed: {e}") from e
    if result.returncode != 0:
        stderr_tail = result.stderr.strip()[:500] if result.stderr else ""
        raise CalloutsError(
            f"ffprobe failed to read {source_mp4}: {stderr_tail}"
        )
    parts = result.stdout.strip().split(",")
    if len(parts) < 2:
        raise CalloutsError(
            f"ffprobe returned no dimensions for {source_mp4}: {result.stdout!r}"
        )
    try:
        probed_width = int(parts[0])
        probed_height = int(parts[1])
    except ValueError as e:
        raise CalloutsError(
            f"ffprobe returned non-integer dimensions: {result.stdout!r}"
        ) from e
    raw_fps = parts[2] if len(parts) >= 3 else ""
    fps = raw_fps if raw_fps and raw_fps != "0/0" else "30/1"
    return probed_width, probed_height, fps


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
    year: int | None = None,
) -> None:
    """Render an annotated MP4 with title cards, callouts, and a watermark.

    1. Probe the source dimensions / frame rate (ffprobe).
    2. Parse the issues list into :class:`Callout` records.
    3. Build the ``-filter_complex`` graph: a CNIB-yellow start title
       card, the source video with callout overlays + a bottom-right logo
       watermark, and an identical end title card, concatenated together.
    4. Write a temporary ``FFMETADATA1`` chapter file (callout chapters
       shifted past the start card, plus a "Video Start" chapter).
    5. Shell out to ffmpeg: re-encode video with libx264 (and audio to
       AAC, since the synthesised card audio must match the source for
       ``concat``), embedding chapters via ``-map_chapters``.

    ``mp4box_path`` is accepted but currently unused — see the module
    docstring for the rationale. ``year`` defaults to the current year
    (for the copyright line on the title cards).

    Raises:
        :class:`CalloutsError` on any subprocess failure (ffmpeg/ffprobe
        binary missing, logo asset missing, non-zero exit, output not
        produced). Stage F in :mod:`auto_a11y.audio.pipeline` wraps this
        in a try/except so the whole job doesn't fail.
    """
    _ = mp4box_path  # see module docstring — reserved for future use.

    ffmpeg = detect_ffmpeg()
    if ffmpeg is None:
        raise CalloutsError("ffmpeg not on PATH; cannot render callouts")

    logo_path = _asset_path(_LOGO_ASSET_NAME)
    if not logo_path.exists():
        raise CalloutsError(
            f"title/watermark logo asset missing: {logo_path}"
        )

    width, height, fps = _probe_video_dimensions(source_mp4)

    if year is None:
        year = datetime.now().year

    callouts = parse_issues_to_callouts(_coerce_issues_to_payload(issues))
    filtergraph = build_full_filtergraph(
        callouts,
        width=width,
        height=height,
        year=year,
        logo_input_index=1,
        fps=fps,
    )
    chapters_body = build_chapters_metadata(
        callouts,
        offset_s=TITLE_CARD_DURATION_S,
        add_title_chapter=True,
    )

    output_mp4.parent.mkdir(parents=True, exist_ok=True)

    # Write chapters to a temp file so ffmpeg's ``-i metadata`` flag
    # can pick them up. We clean it up in ``finally`` regardless of
    # subprocess outcome. The chapter file is input index 2 (after the
    # source video and the logo PNG).
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
            "-i", str(logo_path),
            "-i", str(chapter_path),
            "-filter_complex", filtergraph,
            "-map", "[outv]",
            "-map", "[outa]",
            "-map_metadata", "2",
            "-map_chapters", "2",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            str(output_mp4),
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=RENDER_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as e:
            raise CalloutsError(
                f"ffmpeg timed out after {RENDER_TIMEOUT_SECONDS}s "
                + "rendering callouts; source may be corrupt or the encode hung"
            ) from e
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
