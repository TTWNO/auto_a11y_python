"""Tests for the per-Recording filesystem layout helper."""
from __future__ import annotations

from pathlib import Path

import pytest

from auto_a11y.audio.storage import (
    AudioStorage,
)
from auto_a11y.audio.errors import InvalidRecordingId, OutsideSlot


@pytest.fixture
def storage(tmp_path: Path) -> AudioStorage:
    return AudioStorage(root=tmp_path / "recordings")


def test_allocate_creates_directory_tree(storage: AudioStorage) -> None:
    allocated = storage.allocate("REC-20260519143022-a1b2c3")
    assert allocated.root.is_dir()
    assert (allocated.root / "audio").is_dir()
    assert (allocated.root / "vtt").is_dir()
    assert (allocated.root / "captions").is_dir()
    assert (allocated.root / "json").is_dir()
    assert (allocated.root / "html").is_dir()


def test_allocate_rejects_path_traversal(storage: AudioStorage) -> None:
    for bad in ["../etc/passwd", "REC-../", "x" * 200, ""]:
        with pytest.raises(InvalidRecordingId):
            storage.allocate(bad)


def test_paths_for_known_recording(storage: AudioStorage) -> None:
    a = storage.allocate("REC-20260519143022-a1b2c3")
    assert a.source_mp4 == a.root / "source.mp4"
    assert a.json_path(kind="issues", lang="en") == a.root / "json" / "REC-20260519143022-a1b2c3.issues.json"
    assert a.json_path(kind="painpoints", lang="fr") == a.root / "json" / "REC-20260519143022-a1b2c3.painpoints.fr.json"
    assert a.captions_vtt == a.root / "captions" / "REC-20260519143022-a1b2c3.vtt"
    assert a.callouts_mp4 == a.root / "video" / "REC-20260519143022-a1b2c3.callouts.mp4"


def test_write_atomic_rejects_path_outside_slot(
    storage: AudioStorage, tmp_path: Path
) -> None:
    slot = storage.allocate("REC-20260519143022-a1b2c3")
    outside = tmp_path / "evil" / "outside.txt"
    with pytest.raises(OutsideSlot):
        slot.write_atomic(outside, b"data")
    assert not outside.exists()


def test_write_atomic_writes_inside_slot(storage: AudioStorage) -> None:
    slot = storage.allocate("REC-20260519143022-a1b2c3")
    target = slot.audio_dir / "sample.bin"
    slot.write_atomic(target, b"hello")
    assert target.read_bytes() == b"hello"
