"""Unit tests for the clustering logic in speaker_identification.

The pyannote embedding extraction is mocked. Real-audio tests live
behind @pytest.mark.slow.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from auto_a11y.audio.speaker_identification import (
    SpeakerMapping,
    cluster_embeddings,
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
