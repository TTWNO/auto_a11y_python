"""Deepgram-based per-segment transcription.

Ported (and upgraded) from pythonAudioA11y/transcription.py:
- Deepgram model upgraded from nova-2 to nova-3 (spec decision).
- Retry loop: 1 s -> 2 s -> 4 s (matches source).
- Output includes per-word timing + speaker label for downstream diarization.

Uses the deepgram-sdk v5 call shape:
    response = client.listen.v1.media.transcribe_file(
        request=audio_buffer,
        model="nova-3",
        smart_format=True,
        diarize=True,
        ...
    )
Response is a typed object accessed via attributes
(response.results.channels[0].alternatives[0].transcript / .words),
NOT a dict. The v3-style client.listen.rest.v("1") chain and
PrerecordedOptions wrapper do not exist in v5.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from auto_a11y.audio.errors import TranscriptionError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Word:
    """Per-word timing record produced by Deepgram."""

    text: str
    """The punctuated_word from Deepgram (falls back to .word if missing)."""

    start: float
    end: float
    speaker: int


@dataclass(frozen=True)
class TranscriptionResult:
    """One segment's Deepgram output, normalized for downstream use."""

    transcript: str
    words: list[Word]


class Transcriber:
    """Wraps a deepgram-sdk v5 client with retry + response normalization.

    The client is typed Any so tests can pass a MagicMock with the v5 call
    shape (`client.listen.v1.media.transcribe_file(...)`) without depending
    on the real SDK's nominal types.
    """

    def __init__(
        self,
        *,
        client: Any,
        model: str = "nova-3",
        max_retries: int = 3,
        language: str = "en",
        timeout_min_seconds: int = 300,
    ) -> None:
        self._client = client
        self._model = model
        self._max_retries = max_retries
        self._language = language
        self._timeout_min_seconds = timeout_min_seconds

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        """Transcribe one audio segment with diarization + smart formatting.

        Retries up to `max_retries` times with exponential backoff
        (1 s -> 2 s -> 4 s ...). Raises TranscriptionError if all attempts
        fail.
        """
        file_size_mb = audio_path.stat().st_size / (1024 * 1024)
        # Mirror pythonAudioA11y's dynamic timeout: max(300s, file_size_mb * 60).
        timeout_seconds = max(self._timeout_min_seconds, int(file_size_mb * 60))

        backoff = 1.0
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                with open(audio_path, "rb") as fp:
                    audio_buffer = fp.read()
                response = self._client.listen.v1.media.transcribe_file(
                    request=audio_buffer,
                    model=self._model,
                    language=self._language,
                    smart_format=True,
                    diarize=True,
                    punctuate=True,
                    paragraphs=True,
                    utterances=True,
                    request_options={"timeout_in_seconds": timeout_seconds},
                )
                return self._parse(response)
            except Exception as e:  # noqa: BLE001 — SDK raises bare RuntimeError + httpx exceptions
                last_exc = e
                logger.warning("Deepgram attempt %d failed: %s", attempt + 1, e)
                if attempt < self._max_retries - 1:
                    time.sleep(backoff)
                    backoff *= 2
        raise TranscriptionError(
            f"Deepgram failed after {self._max_retries} attempts"
        ) from last_exc

    @staticmethod
    def _parse(response: Any) -> TranscriptionResult:
        """Parse the v5 SDK's typed response object into our internal records.

        Uses attribute access (not dict indexing) because the v5 SDK returns
        Pydantic-style models. Falls back gracefully for missing optional
        fields.
        """
        try:
            alt = response.results.channels[0].alternatives[0]
            transcript = str(getattr(alt, "transcript", ""))
            words_attr = getattr(alt, "words", []) or []
            words = [
                Word(
                    text=str(
                        getattr(w, "punctuated_word", None) or getattr(w, "word", "")
                    ),
                    start=float(w.start),
                    end=float(w.end),
                    speaker=int(getattr(w, "speaker", 0) or 0),
                )
                for w in words_attr
            ]
            return TranscriptionResult(transcript=transcript, words=words)
        except (AttributeError, IndexError, TypeError, ValueError) as e:
            raise TranscriptionError(f"Malformed Deepgram response: {e}") from e


def words_to_vtt(words: list[Word], *, offset_s: float = 0.0) -> str:
    """Render a list of Word records as a WebVTT string.

    Adjacent same-speaker words are grouped into a single cue
    (`<v Speaker_N>`-tagged). `offset_s` is added to every timestamp,
    which lets a segment's local times be lifted onto the full-recording
    timeline when stitching merged outputs.
    """
    out: list[str] = ["WEBVTT", ""]
    if not words:
        return "\n".join(out)

    current_speaker = words[0].speaker
    cue_words: list[Word] = []

    def flush() -> None:
        if not cue_words:
            return
        start = _fmt_ts(cue_words[0].start + offset_s)
        end = _fmt_ts(cue_words[-1].end + offset_s)
        text = " ".join(w.text for w in cue_words)
        out.append(f"{start} --> {end}")
        out.append(f"<v Speaker_{current_speaker}>{text}")
        out.append("")

    for w in words:
        if w.speaker != current_speaker:
            flush()
            cue_words = [w]
            current_speaker = w.speaker
        else:
            cue_words.append(w)
    flush()
    return "\n".join(out)


def _fmt_ts(seconds: float) -> str:
    """Format a float second count as ``HH:MM:SS.mmm`` (WebVTT).

    Rounds to whole milliseconds *first*, then derives h/m/s/ms from the
    integer total, so the seconds field can never round up to ``60`` and
    emit an invalid cue like ``00:01:60.000``.
    """
    total_ms = round(seconds * 1000)
    h = total_ms // 3_600_000
    m = (total_ms // 60_000) % 60
    s = (total_ms // 1000) % 60
    ms = total_ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def write_words_json(words: list[Word], path: Path) -> None:
    """Persist words to a `.words` file alongside the VTT (downstream speaker remap)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {"text": w.text, "start": w.start, "end": w.end, "speaker": w.speaker}
        for w in words
    ]
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
