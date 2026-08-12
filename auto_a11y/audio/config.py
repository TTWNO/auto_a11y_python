"""AudioA11y pipeline configuration.

Reads from auto_a11y's existing config (env / .env / user-settings file).
No subprocess calls; just type-safe accessors.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AudioConfig:
    deepgram_api_key: str
    anthropic_api_key: str
    claude_model: str          # e.g., "claude-opus-5"
    deepgram_model: str        # e.g., "nova-3"
    huggingface_token: str | None  # for pyannote.audio model auth; optional
    data_dir: Path             # absolute path; "data/recordings/" lives under this

    @classmethod
    def from_env(cls) -> AudioConfig:
        return cls(
            deepgram_api_key=os.environ.get("DEEPGRAM_API_KEY", ""),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            claude_model=os.environ.get("CLAUDE_MODEL", "claude-opus-5"),
            deepgram_model=os.environ.get("DEEPGRAM_MODEL", "nova-3"),
            huggingface_token=os.environ.get("HF_TOKEN") or None,
            data_dir=Path(os.environ.get("AUTO_A11Y_DATA_DIR", "data")),
        )

    def missing_api_keys(self) -> list[str]:
        """Human-readable names of the API keys required to process a recording.

        An end-to-end run needs both Deepgram (transcription) and
        Anthropic (analysis). A blank/whitespace key is treated as
        missing because the SDKs would otherwise build an empty
        ``Authorization: Token`` header and fail deep in the HTTP client
        with a cryptic ``Illegal header value`` instead of a clear
        "configure your key" message. Returns the unconfigured services in
        pipeline order (Deepgram before Anthropic); empty when both are set.
        """
        missing: list[str] = []
        if not self.deepgram_api_key.strip():
            missing.append("Deepgram")
        if not self.anthropic_api_key.strip():
            missing.append("Anthropic")
        return missing
