"""Cross-segment speaker remapping.

Ported from ``pythonAudioA11y/speaker_identification.py``:
- Extract one embedding per (segment, speaker) using pyannote.audio.
- Cluster embeddings with sklearn ``AgglomerativeClustering``
  (cosine, average linkage, threshold 0.7).
- Replace per-segment speaker labels with global labels in the merged VTT.

Heavy dependencies (torch, pyannote.audio, scikit-learn) are imported
LAZILY inside the functions that need them so the rest of the audio
package doesn't pay the import cost when ``--skip-speaker-remap`` is in
effect.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeGuard

import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)


def _is_str_obj_dict(val: object) -> TypeGuard[dict[str, object]]:
    """Narrow a JSON-loaded value to ``dict[str, object]``.

    ``json.loads`` always produces ``str``-keyed dicts for JSON objects;
    the runtime check is ``isinstance(val, dict)`` only.
    """
    return isinstance(val, dict)


def _is_obj_list(val: object) -> TypeGuard[list[object]]:
    """Narrow a JSON-loaded value to ``list[object]``."""
    return isinstance(val, list)


def _speaker_id(word: dict[str, Any]) -> int | None:
    """Extract the integer speaker id from a word record, or ``None``.

    Word records come from ``transcription.write_words_json``; the
    ``speaker`` field is normally an ``int`` but be defensive — old VTT
    or external data may have it as a string. Any other shape is
    skipped.
    """
    raw = word.get("speaker")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return None
    return None


@dataclass(frozen=True)
class SpeakerMapping:
    """Maps ``Segment_<i>_Speaker_<j>`` strings to global speaker IDs."""

    mapping: dict[str, int]

    def to_dict(self) -> dict[str, dict[str, int]]:
        """Serialise to a JSON-friendly dict (round-trips with ``from_path``)."""
        return {"speakers": dict(self.mapping)}

    @classmethod
    def from_path(cls, path: Path) -> SpeakerMapping:
        """Load a ``SpeakerMapping`` previously serialised with ``to_dict``."""
        raw: object = json.loads(path.read_text())
        if not _is_str_obj_dict(raw):
            raise ValueError(
                f"Expected JSON object at {path}, got {type(raw).__name__}"
            )
        speakers_obj = raw.get("speakers")
        if not _is_str_obj_dict(speakers_obj):
            raise ValueError(
                f"Expected 'speakers' to be a JSON object in {path}"
            )
        mapping: dict[str, int] = {}
        for k, v in speakers_obj.items():
            if not isinstance(v, (int, str)):
                msg = (
                    f"Expected int speaker id at {path}[{k!r}], "
                    + f"got {type(v).__name__}"
                )
                raise ValueError(msg)
            mapping[k] = int(v)
        return cls(mapping=mapping)


def cluster_embeddings(
    embeddings: NDArray[np.floating[Any]],
    *,
    cosine_threshold: float = 0.7,
) -> list[int]:
    """Agglomerative clustering on cosine distance.

    Returns one cluster label per row of ``embeddings``. Single-row inputs
    short-circuit (no clustering possible) and return ``[0]``.
    """
    from sklearn.cluster import AgglomerativeClustering  # lazy import

    if len(embeddings) == 1:
        return [0]

    model = AgglomerativeClustering(
        n_clusters=None,
        metric="cosine",
        linkage="average",
        distance_threshold=1.0 - cosine_threshold,
    )
    labels: NDArray[np.integer[Any]] = model.fit_predict(embeddings)
    return [int(x) for x in labels]


_VOICE_TAG = re.compile(r"<v\s+([^>]+)>")


def remap_speaker_ids_in_vtt(vtt: str, mapping: SpeakerMapping) -> str:
    """Replace ``<v Segment_i_Speaker_j>`` with ``<v Speaker_<global>>``.

    Voice tags whose name is not in ``mapping`` are passed through
    unchanged, so a partial remap (or one with stray names) is safe.
    """
    def sub(m: re.Match[str]) -> str:
        old = m.group(1).strip()
        if old in mapping.mapping:
            return f"<v Speaker_{mapping.mapping[old]}>"
        return m.group(0)

    return _VOICE_TAG.sub(sub, vtt)


def extract_speaker_embeddings(
    *,
    segment_audio_paths: list[Path],
    words_paths: list[Path],
    hf_token: str | None = None,
) -> tuple[list[str], NDArray[np.floating[Any]]]:
    """Compute one embedding vector per (segment, speaker).

    Returns ``(labels, embeddings)`` where ``labels[i]`` is
    ``Segment_<seg>_Speaker_<spk>`` and ``embeddings`` is an ``(N, D)``
    numpy array (or an empty array of shape ``(0,)`` when no speakers
    are found).

    The pyannote.audio model load is a multi-gigabyte download. The
    embedding extraction is stubbed for now — see ``_embed_speaker_audio``.

    TODO_PHASE4: port the per-speaker audio-snippet embedding logic from
    ``pythonAudioA11y/speaker_identification.py`` lines 135-163
    (``_extract_speaker_embedding_from_audio``). Until that lands the
    end-to-end pipeline stage falls back to ``--skip-speaker-remap``.
    """
    from pyannote.audio import Model  # lazy import — multi-GB model load

    if len(segment_audio_paths) != len(words_paths):
        raise ValueError("segment_audio_paths and words_paths must align")

    model: object = Model.from_pretrained("pyannote/embedding", token=hf_token)
    if model is None:
        msg = (
            "pyannote.audio Model.from_pretrained returned None for "
            + "'pyannote/embedding' — check HF_TOKEN and model availability."
        )
        raise RuntimeError(msg)
    labels: list[str] = []
    vectors: list[NDArray[np.floating[Any]]] = []
    for seg_idx, (audio_path, words_path) in enumerate(
        zip(segment_audio_paths, words_paths, strict=True)
    ):
        if not audio_path.exists() or not words_path.exists():
            continue
        raw: object = json.loads(words_path.read_text())
        if not _is_obj_list(raw):
            logger.warning("Skipping %s: expected list payload", words_path)
            continue
        # SDK boundary: word records are our own JSON output from
        # transcription.py:write_words_json. ``Any`` is permitted per
        # CLAUDE.md for parameter types at SDK boundaries; downstream
        # ``_embed_speaker_audio`` validates the per-field shape.
        words: list[dict[str, Any]] = []
        for entry in raw:
            if not _is_str_obj_dict(entry):
                continue
            words.append({k: entry[k] for k in entry})
        speaker_ids: set[int] = set()
        for w in words:
            sid = _speaker_id(w)
            if sid is not None:
                speaker_ids.add(sid)
        for spk in sorted(speaker_ids):
            vec = _embed_speaker_audio(model, audio_path, words, spk)
            labels.append(f"Segment_{seg_idx}_Speaker_{spk}")
            vectors.append(vec)
    if not vectors:
        return labels, np.zeros((0,), dtype=np.float64)
    return labels, np.asarray(vectors)


def _embed_speaker_audio(
    model: object,
    audio_path: Path,
    words: list[dict[str, Any]],
    speaker: int,
) -> NDArray[np.floating[Any]]:
    """Extract one speaker's audio in one segment, return the embedding vector.

    TODO_PHASE4: port the pyannote ``Inference(model, window="whole")``
    + ``.crop(audio_path, Segment(start, end))`` + np.mean averaging
    logic from
    ``pythonAudioA11y/speaker_identification.py``
    lines 135-163 (``_extract_speaker_embedding_from_audio``). The
    current stub means ``build_mapping`` cannot run end-to-end yet, and
    the pipeline falls back to ``--skip-speaker-remap`` until this is
    filled in.
    """
    msg = (
        "TODO_PHASE4: port _embed_speaker_audio from "
        + "pythonAudioA11y/speaker_identification.py"
    )
    raise NotImplementedError(msg)


def build_mapping(
    *,
    segment_audio_paths: list[Path],
    words_paths: list[Path],
    cosine_threshold: float = 0.7,
    hf_token: str | None = None,
) -> SpeakerMapping:
    """End-to-end orchestrator: extract embeddings, cluster, return mapping.

    Returns an empty ``SpeakerMapping`` when no speakers were found
    across any segment (e.g. all segments missing or no diarization).
    """
    labels, embeddings = extract_speaker_embeddings(
        segment_audio_paths=segment_audio_paths,
        words_paths=words_paths,
        hf_token=hf_token,
    )
    if len(labels) == 0:
        return SpeakerMapping(mapping={})
    cluster_labels = cluster_embeddings(embeddings, cosine_threshold=cosine_threshold)
    return SpeakerMapping(mapping=dict(zip(labels, cluster_labels, strict=True)))
