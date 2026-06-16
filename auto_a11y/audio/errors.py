"""Typed audio-pipeline exceptions."""
from __future__ import annotations


class AudioPipelineError(Exception):
    """Base class for all audioA11y pipeline errors."""


class InvalidRecordingId(AudioPipelineError):
    """Recording id doesn't match the REC-YYYYMMDDHHMMSS-{6 hex} format."""


class FfmpegMissing(AudioPipelineError):
    """ffmpeg (or ffprobe) binary not on PATH and no override given."""

    def __init__(self, searched: list[str]) -> None:
        super().__init__("ffmpeg not found on PATH; searched: " + ", ".join(searched))
        self.searched = searched


class ConfigurationError(AudioPipelineError):
    """A required API key (Deepgram / Anthropic) is missing or blank.

    Raised before any network call so a fresh install with no keys fails
    fast with an actionable message instead of a cryptic
    ``Illegal header value b'Token '`` from the HTTP client.
    """


class TranscriptionError(AudioPipelineError):
    """Wraps Deepgram failures after retries are exhausted."""


class AnalysisError(AudioPipelineError):
    """Wraps Anthropic failures after retries are exhausted."""


class CalloutsError(AudioPipelineError):
    """Callouts ffmpeg-overlay rendering failed. NEVER fails the whole job."""


class OutsideSlot(AudioPipelineError):
    """Path is not inside the AllocatedSlot's root."""
