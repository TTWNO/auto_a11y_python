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
    Callout,
    build_chapters_metadata,
    build_drawtext_filter,
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


# --- render_callouts_video --------------------------------------------


def _ok_completed_process() -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def _fail_completed_process(stderr: str = "boom") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=stderr)


def test_render_callouts_video_invokes_ffmpeg_with_expected_args(tmp_path: Path) -> None:
    """Argv layout: ffmpeg + -i source + -i chapters + -vf <filter> + libx264 + audio-copy."""
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
         patch("auto_a11y.audio.callouts.subprocess.run", side_effect=_fake_run):
        render_callouts_video(
            source_mp4=source,
            issues=issues,
            output_mp4=output,
        )

    assert len(captured_argv) == 1
    argv = captured_argv[0]
    # First arg is the resolved ffmpeg path.
    assert argv[0] == "/usr/bin/ffmpeg"
    # Inputs in order: source MP4, then chapter metadata file.
    assert "-i" in argv
    i_indexes = [i for i, v in enumerate(argv) if v == "-i"]
    assert len(i_indexes) == 2
    assert argv[i_indexes[0] + 1] == str(source)
    # Chapter file lives in a tempdir; just assert it's a path that ends in .txt.
    chapter_arg = argv[i_indexes[1] + 1]
    assert chapter_arg.endswith(".txt")
    # Video re-encode with the drawtext filter.
    vf_idx = argv.index("-vf")
    assert "drawtext=" in argv[vf_idx + 1]
    # Audio is stream-copied.
    ca_idx = argv.index("-c:a")
    assert argv[ca_idx + 1] == "copy"
    # Chapters are mapped from input 1.
    mc_idx = argv.index("-map_chapters")
    assert argv[mc_idx + 1] == "1"
    # Output path is the last positional arg.
    assert argv[-1] == str(output)


def test_render_callouts_video_with_no_issues_uses_null_filter(tmp_path: Path) -> None:
    """Zero callouts → drawtext filter expression is ``null`` (pass-through)."""
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
         patch("auto_a11y.audio.callouts.subprocess.run", side_effect=_fake_run):
        render_callouts_video(
            source_mp4=source,
            issues=[],
            output_mp4=output,
        )

    argv = captured_argv[0]
    vf_idx = argv.index("-vf")
    assert argv[vf_idx + 1] == "null"


def test_render_callouts_video_raises_on_ffmpeg_failure(tmp_path: Path) -> None:
    """Non-zero ffmpeg exit code becomes ``CalloutsError``."""
    source = tmp_path / "source.mp4"
    source.write_bytes(b"")
    output = tmp_path / "out.mp4"

    with patch("auto_a11y.audio.callouts.detect_ffmpeg", return_value="/usr/bin/ffmpeg"), \
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


def test_render_callouts_video_raises_when_output_not_produced(tmp_path: Path) -> None:
    """ffmpeg reports success but produces no output → ``CalloutsError``."""
    source = tmp_path / "source.mp4"
    source.write_bytes(b"")
    output = tmp_path / "out.mp4"  # deliberately don't create this in _fake_run

    with patch("auto_a11y.audio.callouts.detect_ffmpeg", return_value="/usr/bin/ffmpeg"), \
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
        # Snapshot the chapter file argv (input 2's value) before ffmpeg pretends to consume it.
        i_indexes = [i for i, v in enumerate(argv_strs) if v == "-i"]
        captured_chapter_path.append(argv_strs[i_indexes[1] + 1])
        output.write_bytes(b"\x00")
        return _ok_completed_process()

    with patch("auto_a11y.audio.callouts.detect_ffmpeg", return_value="/usr/bin/ffmpeg"), \
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
