"""Tests for ``auto_a11y.audio.callouts``.

Two layers:

1. Pure-function helpers (``parse_timecode_to_seconds``,
   ``parse_issues_to_callouts``, ``build_drawtext_filter``,
   ``build_chapters_metadata``) — exhaustive unit tests, no
   subprocess.
2. ``render_callouts_video`` — mocked-``subprocess.run`` tests assert
   the argv layout, failure paths translate to ``CalloutsError``, and
   the temp chapter file is cleaned up.

The real ffmpeg invocation is intentionally not covered here; Phase 11
exercises it as part of the manual walkthrough.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, TypeGuard
from unittest.mock import patch

import pytest

from auto_a11y.audio.callouts import (
    TITLE_CARD_DURATION_S,
    Callout,
    build_chapters_metadata,
    build_copyright_text,
    build_drawtext_filter,
    build_full_filtergraph,
    parse_issues_to_callouts,
    parse_timecode_to_seconds,
    render_callouts_video,
)
from auto_a11y.audio.errors import CalloutsError


def _is_obj_list(val: object) -> TypeGuard[list[object]]:
    """Pyright-friendly TypeGuard for ``isinstance(x, list)`` checks."""
    return isinstance(val, list)


# --- parse_timecode_to_seconds -----------------------------------------


def test_parse_timecode_hms() -> None:
    assert parse_timecode_to_seconds("00:01:23") == 83.0


def test_parse_timecode_ms() -> None:
    assert parse_timecode_to_seconds("01:23") == 83.0


def test_parse_timecode_with_milliseconds() -> None:
    assert parse_timecode_to_seconds("00:00:01.500") == 1.5


def test_parse_timecode_empty_returns_none() -> None:
    assert parse_timecode_to_seconds("") is None
    assert parse_timecode_to_seconds("   ") is None


def test_parse_timecode_malformed_returns_none() -> None:
    assert parse_timecode_to_seconds("not-a-time") is None
    assert parse_timecode_to_seconds("00:ab:00") is None


# --- parse_issues_to_callouts ------------------------------------------


def test_parse_issues_to_callouts_basic() -> None:
    issues: dict[str, object] = {
        "recording": "REC-x",
        "issues": [
            {
                "short_title": "Low contrast",
                "timecodes": [
                    {"start": "00:00:10", "end": "00:00:15", "duration": "00:00:05"}
                ],
            }
        ],
    }
    callouts = parse_issues_to_callouts(issues)
    assert len(callouts) == 1
    assert callouts[0].start_s == 10.0
    assert callouts[0].end_s == 15.0
    assert callouts[0].short_title == "Low contrast"


def test_parse_issues_to_callouts_multiple_timecodes_per_issue() -> None:
    """An issue with 2 timecode ranges emits 2 callouts."""
    issues: dict[str, object] = {
        "recording": "REC-x",
        "issues": [
            {
                "short_title": "Missing label",
                "timecodes": [
                    {"start": "00:00:10", "end": "00:00:15"},
                    {"start": "00:01:00", "end": "00:01:30"},
                ],
            }
        ],
    }
    callouts = parse_issues_to_callouts(issues)
    assert len(callouts) == 2
    assert callouts[0].start_s == 10.0
    assert callouts[1].start_s == 60.0
    # Both refer back to the same short_title.
    assert all(c.short_title == "Missing label" for c in callouts)


def test_parse_issues_to_callouts_skips_empty_short_title() -> None:
    issues: dict[str, object] = {
        "issues": [
            {
                "short_title": "",
                "timecodes": [{"start": "00:00:10", "end": "00:00:15"}],
            },
            {
                "short_title": "   ",
                "timecodes": [{"start": "00:00:20", "end": "00:00:25"}],
            },
            # No short_title at all.
            {
                "timecodes": [{"start": "00:00:30", "end": "00:00:35"}],
            },
        ],
    }
    callouts = parse_issues_to_callouts(issues)
    assert callouts == []


def test_parse_issues_to_callouts_skips_malformed_timecode() -> None:
    issues: dict[str, object] = {
        "issues": [
            {
                "short_title": "OK issue",
                "timecodes": [
                    {"start": "00:00:10", "end": "00:00:15"},   # kept
                    {"start": "bad", "end": "00:00:20"},          # dropped
                    {"start": "00:00:30", "end": ""},             # dropped
                    {"start": "00:00:40", "end": "00:00:30"},     # end <= start
                ],
            }
        ],
    }
    callouts = parse_issues_to_callouts(issues)
    assert len(callouts) == 1
    assert callouts[0].start_s == 10.0
    assert callouts[0].end_s == 15.0


def test_parse_issues_to_callouts_missing_issues_key_returns_empty() -> None:
    assert parse_issues_to_callouts({}) == []
    assert parse_issues_to_callouts({"issues": "not a list"}) == []


def test_parse_issues_to_callouts_skips_non_dict_entries() -> None:
    issues: dict[str, object] = {
        "issues": [
            "not a dict",
            None,
            {
                "short_title": "Real one",
                "timecodes": [{"start": "00:00:01", "end": "00:00:02"}],
            },
        ],
    }
    callouts = parse_issues_to_callouts(issues)
    assert len(callouts) == 1
    assert callouts[0].short_title == "Real one"


# --- build_drawtext_filter ---------------------------------------------


def test_build_drawtext_filter_empty_returns_null() -> None:
    assert build_drawtext_filter([]) == "null"


def test_build_drawtext_filter_single_callout_has_enable_clause() -> None:
    callouts = [Callout(start_s=10.0, end_s=15.0, short_title="Low contrast")]
    expr = build_drawtext_filter(callouts)
    assert "drawtext=" in expr
    assert "text='Low contrast'" in expr
    assert "between(t,10.000,15.000)" in expr


def test_build_drawtext_filter_multiple_callouts_chained_with_comma() -> None:
    callouts = [
        Callout(start_s=0.0, end_s=5.0, short_title="First"),
        Callout(start_s=10.0, end_s=15.0, short_title="Second"),
    ]
    expr = build_drawtext_filter(callouts)
    assert expr.count("drawtext=") == 2
    # The two drawtext expressions are comma-joined at the top level,
    # but ``between(...)`` itself contains a comma — assert both
    # texts are present.
    assert "text='First'" in expr
    assert "text='Second'" in expr


def test_build_drawtext_filter_escapes_special_chars() -> None:
    """Colons, single quotes, backslashes, and percent signs are escaped."""
    callouts = [Callout(start_s=0.0, end_s=5.0, short_title="Foo: bar'baz%qux")]
    expr = build_drawtext_filter(callouts)
    # Pull the text='…' segment out so we can inspect the escaped body.
    body = expr.split("text='", 1)[1].split("':fontsize", 1)[0]
    # Single quote escape uses the close+\'+reopen trick.
    assert "'\\''" in body
    # Colon and percent are backslash-escaped.
    assert "\\:" in body
    assert "\\%" in body


def test_build_drawtext_filter_normalises_smart_quotes() -> None:
    callouts = [Callout(start_s=0.0, end_s=5.0, short_title="Don’t skip")]
    expr = build_drawtext_filter(callouts)
    # The U+2019 right-single-quote becomes an ASCII apostrophe and is
    # then escaped via the close-quote-reopen-quote trick.
    assert "’" not in expr
    assert "Don'\\''t skip" in expr


# --- build_chapters_metadata -------------------------------------------


def test_build_chapters_metadata_header_present() -> None:
    body = build_chapters_metadata([])
    assert body.startswith(";FFMETADATA1\n")


def test_build_chapters_metadata_one_chapter_per_callout() -> None:
    callouts = [
        Callout(start_s=10.0, end_s=20.0, short_title="A"),
        Callout(start_s=30.0, end_s=40.0, short_title="B"),
    ]
    body = build_chapters_metadata(callouts)
    assert body.count("[CHAPTER]") == 2
    assert "title=A" in body
    assert "title=B" in body
    assert "START=10000" in body
    assert "END=20000" in body


def test_build_chapters_metadata_bumps_duplicate_starts() -> None:
    callouts = [
        Callout(start_s=10.0, end_s=15.0, short_title="A"),
        Callout(start_s=10.0, end_s=20.0, short_title="B"),
    ]
    body = build_chapters_metadata(callouts)
    # Second chapter's start was nudged +1 ms.
    assert "START=10000" in body
    assert "START=10001" in body


def test_build_chapters_metadata_offset_shifts_callout_times() -> None:
    """A start-card offset pushes every callout chapter forward by that many seconds."""
    callouts = [Callout(start_s=10.0, end_s=20.0, short_title="A")]
    body = build_chapters_metadata(callouts, offset_s=3.0)
    # 10s + 3s offset = 13000 ms; 20s + 3s = 23000 ms.
    assert "START=13000" in body
    assert "END=23000" in body
    assert "START=10000" not in body


def test_build_chapters_metadata_adds_video_start_chapter() -> None:
    """``add_title_chapter`` prepends a 'Video Start' chapter at 0:00."""
    callouts = [Callout(start_s=10.0, end_s=20.0, short_title="A")]
    body = build_chapters_metadata(callouts, offset_s=3.0, add_title_chapter=True)
    assert "title=Video Start" in body
    # The Video Start chapter begins at the very start of the output.
    assert "START=0\n" in body
    # Still two chapters total: Video Start + the offset callout.
    assert body.count("[CHAPTER]") == 2


def test_build_chapters_metadata_defaults_unchanged() -> None:
    """Default args reproduce the pre-existing (no-offset, no-title) output."""
    callouts = [Callout(start_s=10.0, end_s=20.0, short_title="A")]
    assert build_chapters_metadata(callouts) == build_chapters_metadata(
        callouts, offset_s=0.0, add_title_chapter=False
    )
    assert "title=Video Start" not in build_chapters_metadata(callouts)


# --- build_copyright_text ----------------------------------------------


def test_build_copyright_text_includes_year_and_cnib() -> None:
    text = build_copyright_text(2026)
    assert "2026" in text
    assert "CNIB" in text
    assert "©" in text


# --- build_full_filtergraph --------------------------------------------


def test_build_full_filtergraph_concatenates_three_segments() -> None:
    """Start card + main + end card are concatenated into [outv]/[outa]."""
    graph = build_full_filtergraph([], width=1920, height=1080, year=2026)
    assert "concat=n=3:v=1:a=1[outv][outa]" in graph


def test_build_full_filtergraph_uses_cnib_yellow_cards_and_copyright() -> None:
    graph = build_full_filtergraph([], width=1920, height=1080, year=2026)
    # Two yellow title-card backgrounds (start + end).
    assert graph.count("color=c=0xFFF000") == 2
    assert "Copyright © 2026 CNIB" in graph


def test_build_full_filtergraph_splits_logo_input_for_cards_and_watermark() -> None:
    graph = build_full_filtergraph([], width=1920, height=1080, year=2026)
    # The logo lives at input index 1 and is split for reuse.
    assert "[1:v]split=2" in graph
    # Persistent watermark anchored bottom-right with a 15px margin.
    assert "overlay=W-w-15:H-h-15" in graph


def test_build_full_filtergraph_main_passthrough_when_no_callouts() -> None:
    graph = build_full_filtergraph([], width=1920, height=1080, year=2026)
    assert "[0:v]null[" in graph


def test_build_full_filtergraph_includes_callout_drawtext() -> None:
    callouts = [Callout(start_s=10.0, end_s=15.0, short_title="Low contrast")]
    graph = build_full_filtergraph(callouts, width=1920, height=1080, year=2026)
    assert "drawtext=" in graph
    assert "text='Low contrast'" in graph
    assert "between(t,10.000,15.000)" in graph


def test_build_full_filtergraph_scales_logo_relative_to_width() -> None:
    """Card logo ~80% of width, watermark ~12% of width."""
    graph = build_full_filtergraph([], width=1000, height=1000, year=2026)
    assert "scale=800:-1" in graph   # 80% of 1000
    assert "scale=120:-1" in graph   # 12% of 1000


# --- render_callouts_video --------------------------------------------


_FAKE_DIMS: tuple[int, int, str] = (1920, 1080, "30/1")


def _ok_completed_process() -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def _fail_completed_process(stderr: str = "boom") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=stderr)


def test_logo_asset_is_present_and_valid_png() -> None:
    """The committed logo PNG must ship next to the module (it's bundled via rsync)."""
    import auto_a11y.audio.callouts as callouts_mod

    module_file = callouts_mod.__file__
    assert module_file is not None
    logo = Path(module_file).resolve().parent / "assets" / "accesslabs_logo.png"
    assert logo.exists(), f"missing bundled logo asset: {logo}"
    # PNG 8-byte signature — guards against committing a placeholder/SVG.
    assert logo.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_callouts_video_invokes_ffmpeg_with_filter_complex(tmp_path: Path) -> None:
    """Argv: ffmpeg + 3 inputs (source, logo PNG, chapters) + filter_complex + AAC."""
    source = tmp_path / "source.mp4"
    source.write_bytes(b"")  # existence is all we need; ffmpeg call is mocked
    output = tmp_path / "out.mp4"

    issues: list[dict[str, object]] = [
        {
            "short_title": "Low contrast",
            "timecodes": [{"start": "00:00:10", "end": "00:00:15"}],
        }
    ]

    captured_argv: list[list[str]] = []

    def _fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        _ = kwargs
        raw_argv: object = args[0]
        assert _is_obj_list(raw_argv)
        argv_strs: list[str] = [str(item) for item in raw_argv]
        captured_argv.append(argv_strs)
        # ffmpeg "succeeds" — also write a tiny stub output so the
        # post-check ``output_mp4.exists()`` passes.
        output.write_bytes(b"\x00")
        return _ok_completed_process()

    with patch("auto_a11y.audio.callouts.detect_ffmpeg", return_value="/usr/bin/ffmpeg"), \
         patch("auto_a11y.audio.callouts._probe_video_dimensions", return_value=_FAKE_DIMS), \
         patch("auto_a11y.audio.callouts.subprocess.run", side_effect=_fake_run):
        render_callouts_video(
            source_mp4=source,
            issues=issues,
            output_mp4=output,
            year=2026,
        )

    assert len(captured_argv) == 1
    argv = captured_argv[0]
    assert argv[0] == "/usr/bin/ffmpeg"
    # Three inputs in order: source MP4, logo PNG, chapter metadata file.
    i_indexes = [i for i, v in enumerate(argv) if v == "-i"]
    assert len(i_indexes) == 3
    assert argv[i_indexes[0] + 1] == str(source)
    assert argv[i_indexes[1] + 1].endswith("accesslabs_logo.png")
    assert argv[i_indexes[2] + 1].endswith(".txt")
    # Composited via filter_complex (title cards + watermark + callouts + concat).
    fc_idx = argv.index("-filter_complex")
    graph = argv[fc_idx + 1]
    assert "concat=n=3:v=1:a=1[outv][outa]" in graph
    assert "text='Low contrast'" in graph
    assert "Copyright © 2026 CNIB" in graph
    # Output streams are mapped from the concat outputs.
    assert "-map" in argv
    map_values = [argv[i + 1] for i, v in enumerate(argv) if v == "-map"]
    assert "[outv]" in map_values
    assert "[outa]" in map_values
    # Audio is re-encoded to AAC (concat needs uniform audio), not copied.
    ca_idx = argv.index("-c:a")
    assert argv[ca_idx + 1] == "aac"
    # Chapters are mapped from input 2.
    mc_idx = argv.index("-map_chapters")
    assert argv[mc_idx + 1] == "2"
    # Output path is the last positional arg.
    assert argv[-1] == str(output)


def test_render_callouts_video_with_no_issues_passes_main_through(tmp_path: Path) -> None:
    """Zero callouts → main video segment uses the ``null`` pass-through filter."""
    source = tmp_path / "source.mp4"
    source.write_bytes(b"")
    output = tmp_path / "out.mp4"

    captured_argv: list[list[str]] = []

    def _fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        _ = kwargs
        raw_argv: object = args[0]
        assert _is_obj_list(raw_argv)
        argv_strs: list[str] = [str(item) for item in raw_argv]
        captured_argv.append(argv_strs)
        output.write_bytes(b"\x00")
        return _ok_completed_process()

    with patch("auto_a11y.audio.callouts.detect_ffmpeg", return_value="/usr/bin/ffmpeg"), \
         patch("auto_a11y.audio.callouts._probe_video_dimensions", return_value=_FAKE_DIMS), \
         patch("auto_a11y.audio.callouts.subprocess.run", side_effect=_fake_run):
        render_callouts_video(
            source_mp4=source,
            issues=[],
            output_mp4=output,
        )

    argv = captured_argv[0]
    graph = argv[argv.index("-filter_complex") + 1]
    assert "[0:v]null[" in graph
    # Title cards are still present even with no callouts.
    assert "concat=n=3:v=1:a=1[outv][outa]" in graph


def test_render_callouts_video_raises_on_ffmpeg_failure(tmp_path: Path) -> None:
    """Non-zero ffmpeg exit code becomes ``CalloutsError``."""
    source = tmp_path / "source.mp4"
    source.write_bytes(b"")
    output = tmp_path / "out.mp4"

    with patch("auto_a11y.audio.callouts.detect_ffmpeg", return_value="/usr/bin/ffmpeg"), \
         patch("auto_a11y.audio.callouts._probe_video_dimensions", return_value=_FAKE_DIMS), \
         patch(
             "auto_a11y.audio.callouts.subprocess.run",
             return_value=_fail_completed_process("ffmpeg: invalid pixel format"),
         ):
        with pytest.raises(CalloutsError, match="ffmpeg exited with code 1"):
            render_callouts_video(
                source_mp4=source,
                issues=[],
                output_mp4=output,
            )


def test_render_callouts_video_raises_when_ffmpeg_missing(tmp_path: Path) -> None:
    """No ffmpeg binary on PATH → ``CalloutsError`` (we don't even try to spawn)."""
    source = tmp_path / "source.mp4"
    source.write_bytes(b"")
    output = tmp_path / "out.mp4"

    with patch("auto_a11y.audio.callouts.detect_ffmpeg", return_value=None):
        with pytest.raises(CalloutsError, match="ffmpeg not on PATH"):
            render_callouts_video(
                source_mp4=source,
                issues=[],
                output_mp4=output,
            )


def test_render_callouts_video_raises_when_logo_asset_missing(tmp_path: Path) -> None:
    """A missing logo PNG → ``CalloutsError`` (no silent fallback, unlike the original)."""
    source = tmp_path / "source.mp4"
    source.write_bytes(b"")
    output = tmp_path / "out.mp4"

    missing_logo = tmp_path / "nope" / "accesslabs_logo.png"

    with patch("auto_a11y.audio.callouts.detect_ffmpeg", return_value="/usr/bin/ffmpeg"), \
         patch("auto_a11y.audio.callouts._asset_path", return_value=missing_logo):
        with pytest.raises(CalloutsError, match="logo asset missing"):
            render_callouts_video(
                source_mp4=source,
                issues=[],
                output_mp4=output,
            )


def test_render_callouts_video_raises_when_output_not_produced(tmp_path: Path) -> None:
    """ffmpeg reports success but produces no output → ``CalloutsError``."""
    source = tmp_path / "source.mp4"
    source.write_bytes(b"")
    output = tmp_path / "out.mp4"  # deliberately don't create this in _fake_run

    with patch("auto_a11y.audio.callouts.detect_ffmpeg", return_value="/usr/bin/ffmpeg"), \
         patch("auto_a11y.audio.callouts._probe_video_dimensions", return_value=_FAKE_DIMS), \
         patch(
             "auto_a11y.audio.callouts.subprocess.run",
             return_value=_ok_completed_process(),
         ):
        with pytest.raises(CalloutsError, match="was not created"):
            render_callouts_video(
                source_mp4=source,
                issues=[],
                output_mp4=output,
            )


def test_render_callouts_video_cleans_up_chapter_file(tmp_path: Path) -> None:
    """The temp chapter metadata file is unlinked on the success path."""
    source = tmp_path / "source.mp4"
    source.write_bytes(b"")
    output = tmp_path / "out.mp4"

    captured_chapter_path: list[str] = []

    def _fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        _ = kwargs
        raw_argv: object = args[0]
        assert _is_obj_list(raw_argv)
        argv_strs: list[str] = [str(item) for item in raw_argv]
        # The chapter file is input index 2 (after source video + logo PNG).
        i_indexes = [i for i, v in enumerate(argv_strs) if v == "-i"]
        captured_chapter_path.append(argv_strs[i_indexes[2] + 1])
        output.write_bytes(b"\x00")
        return _ok_completed_process()

    with patch("auto_a11y.audio.callouts.detect_ffmpeg", return_value="/usr/bin/ffmpeg"), \
         patch("auto_a11y.audio.callouts._probe_video_dimensions", return_value=_FAKE_DIMS), \
         patch("auto_a11y.audio.callouts.subprocess.run", side_effect=_fake_run):
        render_callouts_video(
            source_mp4=source,
            issues=[],
            output_mp4=output,
        )

    assert len(captured_chapter_path) == 1
    chapter_path = Path(captured_chapter_path[0])
    # Temp file was unlinked in the ``finally`` block after subprocess.run returned.
    assert not chapter_path.exists()


def test_render_callouts_video_default_year_uses_current_year(tmp_path: Path) -> None:
    """When ``year`` is omitted, the copyright line uses the current year."""
    from datetime import datetime

    source = tmp_path / "source.mp4"
    source.write_bytes(b"")
    output = tmp_path / "out.mp4"
    expected_year = str(datetime.now().year)

    captured_argv: list[list[str]] = []

    def _fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        _ = kwargs
        raw_argv: object = args[0]
        assert _is_obj_list(raw_argv)
        captured_argv.append([str(item) for item in raw_argv])
        output.write_bytes(b"\x00")
        return _ok_completed_process()

    with patch("auto_a11y.audio.callouts.detect_ffmpeg", return_value="/usr/bin/ffmpeg"), \
         patch("auto_a11y.audio.callouts._probe_video_dimensions", return_value=_FAKE_DIMS), \
         patch("auto_a11y.audio.callouts.subprocess.run", side_effect=_fake_run):
        render_callouts_video(source_mp4=source, issues=[], output_mp4=output)

    graph = captured_argv[0][captured_argv[0].index("-filter_complex") + 1]
    assert f"Copyright © {expected_year} CNIB" in graph


def test_title_card_duration_constant_is_positive() -> None:
    assert TITLE_CARD_DURATION_S > 0
