"""Tests for audio/transcription.py.

Live Deepgram tests live in test_transcription_live.py (marked
@pytest.mark.deepgram, skipped without DEEPGRAM_API_KEY).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from auto_a11y.audio.errors import TranscriptionError
from auto_a11y.audio.transcription import (
    Transcriber,
    TranscriptionResult,
    Word,
    words_to_vtt,
    write_words_json,
)


def _fake_deepgram_response() -> object:
    """Build a fake response object matching the v5 SDK's PrerecordedResponse shape.

    The v5 SDK returns a typed object accessed via attributes
    (response.results.channels[0].alternatives[0].transcript / .words).
    """
    word_a = MagicMock(start=0.0, end=0.5, speaker=0, punctuated_word="Hello", word="Hello")
    word_b = MagicMock(start=0.6, end=1.1, speaker=0, punctuated_word="world", word="world")
    alt = MagicMock(transcript="Hello world", words=[word_a, word_b])
    channel = MagicMock(alternatives=[alt])
    results = MagicMock(channels=[channel])
    return MagicMock(results=results)


def _make_audio_file(path: Path) -> Path:
    """Create a tiny stand-in audio file so the transcriber can stat() it."""
    path.write_bytes(b"\x00" * 1024)
    return path


def test_transcribe_returns_words_and_transcript(tmp_path: Path) -> None:
    sdk = MagicMock()
    sdk.listen.v1.media.transcribe_file.return_value = _fake_deepgram_response()
    transcriber = Transcriber(client=sdk, model="nova-3")

    audio = _make_audio_file(tmp_path / "fake.m4a")
    result = transcriber.transcribe(audio)

    assert isinstance(result, TranscriptionResult)
    assert result.transcript == "Hello world"
    assert len(result.words) == 2
    assert result.words[0].start == 0.0
    assert result.words[0].speaker == 0
    assert result.words[0].text == "Hello"


def test_transcribe_retries_on_5xx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sdk = MagicMock()
    transcribe_file = sdk.listen.v1.media.transcribe_file
    # Two failures, then success
    transcribe_file.side_effect = [
        RuntimeError("Deepgram 503"),
        RuntimeError("Deepgram 503"),
        _fake_deepgram_response(),
    ]
    def _no_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr("auto_a11y.audio.transcription.time.sleep", _no_sleep)

    transcriber = Transcriber(client=sdk, model="nova-3", max_retries=3)
    audio = _make_audio_file(tmp_path / "fake.m4a")
    result = transcriber.transcribe(audio)

    assert result.transcript == "Hello world"
    assert transcribe_file.call_count == 3


def test_transcribe_fails_after_max_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sdk = MagicMock()
    sdk.listen.v1.media.transcribe_file.side_effect = RuntimeError("Deepgram 503")
    def _no_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr("auto_a11y.audio.transcription.time.sleep", _no_sleep)

    transcriber = Transcriber(client=sdk, model="nova-3", max_retries=3)
    audio = _make_audio_file(tmp_path / "fake.m4a")

    with pytest.raises(TranscriptionError):
        transcriber.transcribe(audio)


def test_words_to_vtt_produces_well_formed_output(tmp_path: Path) -> None:
    """words_to_vtt() groups same-speaker words into cues with offset applied."""
    words = [
        Word(text="Hello", start=0.0, end=0.5, speaker=0),
        Word(text="world", start=0.6, end=1.1, speaker=0),
        Word(text="Hi", start=1.5, end=1.8, speaker=1),
    ]
    vtt_text = words_to_vtt(words, offset_s=10.0)

    assert vtt_text.startswith("WEBVTT")
    # First cue: speaker 0, offset by 10 s
    assert "00:00:10.000 --> 00:00:11.100" in vtt_text
    assert "<v Speaker_0>Hello world" in vtt_text
    # Second cue: speaker 1, offset by 10 s
    assert "00:00:11.500 --> 00:00:11.800" in vtt_text
    assert "<v Speaker_1>Hi" in vtt_text

    # And write_words_json round-trips back to disk
    out = tmp_path / "out.words"
    write_words_json(words, out)
    assert out.exists()
    import json as _json
    payload = _json.loads(out.read_text())
    assert payload[0] == {"text": "Hello", "start": 0.0, "end": 0.5, "speaker": 0}
    assert payload[2]["speaker"] == 1
