"""Per-Recording filesystem layout for the audioA11y pipeline.

Layout (under ``root``):

    <root>/<recording_id>/
        source.mp4
        audio/                  # per-segment .m4a clips
        vtt/                    # per-segment Deepgram VTT + words.json
        captions/<recording_id>.vtt
        captions/<recording_id>.speakers.json
        json/                   # final analysis JSON (issues, painpoints, ...)
        html/                   # rendered HTML for in-app viewers
        video/<recording_id>.callouts.mp4
        manifest.json
        job.log

Atomicity: :meth:`AllocatedSlot.write_atomic` does write-temp-then-rename
with fsync'd parent (mirrors :mod:`auto_a11y.pdf.storage`). It enforces
that the target path stays inside the slot's root.
"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from auto_a11y.audio.errors import InvalidRecordingId, OutsideSlot

_RECORDING_ID_RE = re.compile(r"^REC-[0-9]{14}-[a-f0-9]{6}$")


def _validate_recording_id(recording_id: str) -> None:
    """Raise InvalidRecordingId unless ``recording_id`` matches the canonical format."""
    if not _RECORDING_ID_RE.fullmatch(recording_id):
        raise InvalidRecordingId(
            f"Recording id {recording_id!r} does not match REC-YYYYMMDDHHMMSS-{{6 hex}}"
        )


@dataclass(frozen=True)
class AllocatedSlot:
    """Filesystem paths for one Recording.

    Created via :meth:`AudioStorage.allocate` (which creates the directory
    tree) or :meth:`AudioStorage.get` (paths only, no IO).
    """

    recording_id: str
    root: Path

    # --- top-level artifacts ---
    @property
    def source_mp4(self) -> Path:
        return self.root / "source.mp4"

    @property
    def manifest(self) -> Path:
        return self.root / "manifest.json"

    @property
    def job_log(self) -> Path:
        return self.root / "job.log"

    # --- sub-directories ---
    @property
    def audio_dir(self) -> Path:
        return self.root / "audio"

    @property
    def vtt_dir(self) -> Path:
        return self.root / "vtt"

    @property
    def captions_dir(self) -> Path:
        return self.root / "captions"

    @property
    def json_dir(self) -> Path:
        return self.root / "json"

    @property
    def html_dir(self) -> Path:
        return self.root / "html"

    @property
    def video_dir(self) -> Path:
        return self.root / "video"

    # --- merged outputs ---
    @property
    def captions_vtt(self) -> Path:
        return self.captions_dir / f"{self.recording_id}.vtt"

    @property
    def speaker_map(self) -> Path:
        return self.captions_dir / f"{self.recording_id}.speakers.json"

    @property
    def callouts_mp4(self) -> Path:
        return self.video_dir / f"{self.recording_id}.callouts.mp4"

    # --- atomic write ---
    def write_atomic(self, path: Path, content: bytes) -> None:
        """Write ``content`` to ``path`` atomically (temp + rename + fsync).

        ``path`` MUST resolve to a location inside this slot's :attr:`root`;
        otherwise :class:`OutsideSlot` is raised before any IO happens.
        Parent directory is created if missing. Mirrors
        :meth:`auto_a11y.pdf.storage.PdfStorage.write_pdf_bytes`.
        """
        resolved_root = self.root.resolve()
        # Use ``strict=False`` so the target file does not need to exist yet.
        resolved_path = path.resolve()
        if not resolved_path.is_relative_to(resolved_root):
            raise OutsideSlot(
                f"Path {path!s} is not inside the AllocatedSlot root {self.root!s}"
            )
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = resolved_path.with_suffix(resolved_path.suffix + ".tmp")
        with open(tmp_path, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, resolved_path)
        # Best-effort fsync of the parent directory for rename durability;
        # Windows and some other platforms disallow directory fsync, so swallow OSError.
        try:
            dir_fd = os.open(resolved_path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass

    # --- per-segment helpers ---
    def segment_m4a(self, index: int) -> Path:
        return self.audio_dir / f"segment_{index:04d}.m4a"

    def segment_vtt(self, index: int) -> Path:
        return self.vtt_dir / f"segment_{index:04d}.vtt"

    def segment_words(self, index: int) -> Path:
        return self.vtt_dir / f"segment_{index:04d}.words.json"

    # --- structured output helpers ---
    def json_path(self, *, kind: str, lang: str) -> Path:
        """JSON output path.

        English is the canonical language and is written without a lang
        suffix; all other languages get ``<kind>.<lang>.json``.
        """
        if lang == "en":
            return self.json_dir / f"{self.recording_id}.{kind}.json"
        return self.json_dir / f"{self.recording_id}.{kind}.{lang}.json"

    def html_path(self, *, kind: str, lang: str) -> Path:
        """HTML output path (same lang convention as json_path)."""
        if lang == "en":
            return self.html_dir / f"{self.recording_id}.{kind}.html"
        return self.html_dir / f"{self.recording_id}.{kind}.{lang}.html"


class AudioStorage:
    """Filesystem-backed per-Recording storage."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _slot(self, recording_id: str) -> AllocatedSlot:
        return AllocatedSlot(recording_id=recording_id, root=self.root / recording_id)

    def allocate(self, recording_id: str) -> AllocatedSlot:
        """Validate id, create the per-Recording directory tree, return the slot."""
        _validate_recording_id(recording_id)
        slot = self._slot(recording_id)
        slot.root.mkdir(parents=True, exist_ok=True)
        for sub in (
            slot.audio_dir,
            slot.vtt_dir,
            slot.captions_dir,
            slot.json_dir,
            slot.html_dir,
            slot.video_dir,
        ):
            sub.mkdir(exist_ok=True)
        return slot

    def get(self, recording_id: str) -> AllocatedSlot:
        """Validate id and return the slot. Does not touch the filesystem."""
        _validate_recording_id(recording_id)
        return self._slot(recording_id)

    def delete(self, recording_id: str) -> None:
        """Recursively delete the per-Recording directory. Idempotent."""
        _validate_recording_id(recording_id)
        target = self.root / recording_id
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
