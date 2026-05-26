"""Unit tests for the clustering logic in speaker_identification.

The pyannote embedding extraction is mocked. Real-audio tests live
behind @pytest.mark.slow.
"""
from __future__ import annotations

import json
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from numpy.typing import NDArray

from auto_a11y.audio.speaker_identification import (
    SpeakerMapping,
    SpeakerRemapUnavailable,
    cluster_embeddings,
    extract_speaker_embeddings,
    remap_speaker_ids_in_vtt,
)


def test_cluster_embeddings_groups_similar_vectors() -> None:
    # Three vectors: two near identical, one orthogonal.
    embeddings = np.array([
        [1.0, 0.0, 0.0],
        [1.0, 0.01, 0.0],
        [0.0, 1.0, 0.0],
    ])
    labels = cluster_embeddings(embeddings, cosine_threshold=0.7)
    assert labels[0] == labels[1]
    assert labels[0] != labels[2]


def test_cluster_embeddings_single_vector_returns_single_label() -> None:
    embeddings = np.array([[1.0, 0.0, 0.0]])
    labels = cluster_embeddings(embeddings, cosine_threshold=0.7)
    assert labels == [0]


def test_remap_uses_mapping() -> None:
    mapping = SpeakerMapping(mapping={
        "Segment_0_Speaker_0": 0,
        "Segment_0_Speaker_1": 1,
        "Segment_1_Speaker_0": 1,
        "Segment_1_Speaker_1": 0,
    })
    vtt = (
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:01.000\n"
        "<v Segment_0_Speaker_0>Hi\n\n"
        "00:00:10.000 --> 00:00:11.000\n"
        "<v Segment_1_Speaker_1>Hi back\n\n"
    )
    remapped = remap_speaker_ids_in_vtt(vtt, mapping)
    assert "<v Speaker_0>Hi" in remapped
    assert "<v Speaker_0>Hi back" in remapped


def test_remap_passes_through_unmapped_voice_tags() -> None:
    """Unknown speaker names are left as-is."""
    mapping = SpeakerMapping(mapping={"Segment_0_Speaker_0": 0})
    vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n<v Unknown>Hi\n\n"
    remapped = remap_speaker_ids_in_vtt(vtt, mapping)
    assert "<v Unknown>" in remapped


def test_speaker_mapping_round_trip(tmp_path: Path) -> None:
    mapping = SpeakerMapping(mapping={"Segment_0_Speaker_0": 0, "Segment_0_Speaker_1": 1})
    path = tmp_path / "map.json"
    path.write_text(json.dumps(mapping.to_dict()))
    loaded = SpeakerMapping.from_path(path)
    assert loaded.mapping == mapping.mapping


# --- extract_speaker_embeddings (pyannote faked, exercised end-to-end) -----
#
# pyannote.audio / pyannote.core are replaced with fakes so no real (gated,
# multi-GB) model loads. The fakes let us drive the public function and
# observe span grouping, averaging, the short-span skip, the crop-failure
# skip, and the HuggingFace-download → SpeakerRemapUnavailable translation.


def _good_model(checkpoint: str, **kwargs: object) -> object:
    """A ``Model.from_pretrained`` that succeeds (returns a non-None model)."""
    return object()


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    from_pretrained: Callable[..., object],
    crop: Callable[[object, object], NDArray[np.floating[Any]]] | None = None,
) -> None:
    fake_audio = types.ModuleType("pyannote.audio")
    fake_core = types.ModuleType("pyannote.core")

    class _Segment:
        def __init__(self, start: float = 0.0, end: float = 0.0) -> None:
            self.start = start
            self.end = end

    class _Model:
        pass

    class _Inference:
        def __init__(self, model: object, window: str = "whole") -> None:
            self.model = model
            self.window = window

        def crop(self, file: object, chunk: object) -> NDArray[np.floating[Any]]:
            if crop is None:
                raise AssertionError("crop unexpectedly called")
            return crop(file, chunk)

    # ``from_pretrained`` is called as ``Model.from_pretrained(checkpoint,
    # token=...)`` — no ``cls`` needed, so a staticmethod suffices.
    setattr(_Model, "from_pretrained", staticmethod(from_pretrained))
    setattr(fake_audio, "Model", _Model)
    setattr(fake_audio, "Inference", _Inference)
    setattr(fake_core, "Segment", _Segment)
    monkeypatch.setitem(sys.modules, "pyannote.audio", fake_audio)
    monkeypatch.setitem(sys.modules, "pyannote.core", fake_core)


def _write_segment(
    tmp_path: Path, name: str, words: list[dict[str, Any]]
) -> tuple[Path, Path]:
    audio = tmp_path / f"{name}.m4a"
    audio.write_bytes(b"not-real-audio")  # crop is faked; content is irrelevant
    words_path = tmp_path / f"{name}.words"
    words_path.write_text(json.dumps(words))
    return audio, words_path


def test_extract_embeddings_translates_download_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A gated/offline HF error surfaces as SpeakerRemapUnavailable."""
    from huggingface_hub.errors import LocalEntryNotFoundError

    def _boom(checkpoint: str, **kwargs: object) -> object:
        raise LocalEntryNotFoundError("offline, no cache")

    _install_fakes(monkeypatch, from_pretrained=_boom)
    with pytest.raises(SpeakerRemapUnavailable):
        extract_speaker_embeddings(segment_audio_paths=[], words_paths=[])


def test_extract_embeddings_raises_when_model_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _none(checkpoint: str, **kwargs: object) -> object:
        return None

    _install_fakes(monkeypatch, from_pretrained=_none)
    with pytest.raises(SpeakerRemapUnavailable):
        extract_speaker_embeddings(segment_audio_paths=[], words_paths=[])


def test_extract_embeddings_one_label_per_segment_speaker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Speakers are grouped into contiguous spans; one label per speaker."""
    calls: list[int] = []

    def _crop(file: object, chunk: object) -> NDArray[np.floating[Any]]:
        calls.append(1)
        return np.array([1.0, 1.0], dtype=np.float64)

    _install_fakes(monkeypatch, from_pretrained=_good_model, crop=_crop)
    words: list[dict[str, Any]] = [
        {"text": "a", "start": 0.0, "end": 1.0, "speaker": 0},
        {"text": "b", "start": 1.0, "end": 2.0, "speaker": 1},
        {"text": "c", "start": 2.0, "end": 3.0, "speaker": 0},  # 2nd spk-0 span
    ]
    audio, words_path = _write_segment(tmp_path, "seg0", words)

    labels, embeddings = extract_speaker_embeddings(
        segment_audio_paths=[audio], words_paths=[words_path],
    )

    assert labels == ["Segment_0_Speaker_0", "Segment_0_Speaker_1"]
    assert embeddings.shape == (2, 2)
    # speaker 0 -> two 1.0s spans, speaker 1 -> one 1.0s span = 3 crops.
    assert len(calls) == 3


def test_extract_embeddings_skips_speaker_with_only_short_audio(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _crop(file: object, chunk: object) -> NDArray[np.floating[Any]]:
        return np.array([1.0], dtype=np.float64)

    _install_fakes(monkeypatch, from_pretrained=_good_model, crop=_crop)
    words: list[dict[str, Any]] = [
        {"start": 0.0, "end": 0.1, "speaker": 0},  # 0.1s < _MIN_UTTERANCE_S
        {"start": 1.0, "end": 3.0, "speaker": 1},  # 2.0s -> kept
    ]
    audio, words_path = _write_segment(tmp_path, "seg0", words)

    labels, embeddings = extract_speaker_embeddings(
        segment_audio_paths=[audio], words_paths=[words_path],
    )

    assert labels == ["Segment_0_Speaker_1"]
    assert embeddings.shape == (1, 1)


def test_extract_embeddings_drops_speaker_when_crop_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A crop that raises must not abort the run — the speaker is dropped."""

    def _crop(file: object, chunk: object) -> NDArray[np.floating[Any]]:
        raise RuntimeError("crop blew up on an awkward span")

    _install_fakes(monkeypatch, from_pretrained=_good_model, crop=_crop)
    words: list[dict[str, Any]] = [{"start": 0.0, "end": 2.0, "speaker": 0}]
    audio, words_path = _write_segment(tmp_path, "seg0", words)

    labels, embeddings = extract_speaker_embeddings(
        segment_audio_paths=[audio], words_paths=[words_path],
    )

    assert labels == []
    assert embeddings.shape == (0,)
