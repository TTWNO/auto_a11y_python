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
from typing import TYPE_CHECKING, Any, Protocol, TypeGuard

import numpy as np
from numpy.typing import NDArray

if TYPE_CHECKING:
    from pyannote.audio import Inference
    from pyannote.core import Segment

logger = logging.getLogger(__name__)


class _SupportsCrop(Protocol):
    """The slice of ``pyannote.audio.Inference`` that embedding needs.

    Typing :func:`_embed_speaker_audio` against this protocol (rather than
    the concrete ``Inference``) keeps the heavy pyannote import lazy and
    lets tests pass a lightweight crop stub. The real ``Inference``
    satisfies it structurally.
    """

    def crop(
        self, file: str | Path, chunk: Segment
    ) -> NDArray[np.floating[Any]]: ...

# Utterances shorter than this are skipped before embedding: the
# pyannote model needs a little context, and sub-half-second crops add
# noise (and sometimes raise) for no benefit.
_MIN_UTTERANCE_S = 0.5


class SpeakerRemapUnavailable(Exception):
    """Raised when cross-segment speaker remapping cannot run.

    Signals a *recoverable* condition — most commonly that the gated
    ``pyannote/embedding`` model could not be obtained (no/invalid
    ``HF_TOKEN``, or the model's terms have not been accepted) or the
    machine is offline. Callers catch this and fall back to no-remap so
    the rest of the audit still completes; the merged VTT then keeps its
    per-segment ``Segment_i_Speaker_j`` voice tags.
    """


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

    Loads the gated ``pyannote/embedding`` model (a multi-gigabyte
    download on first use that requires an ``HF_TOKEN`` whose account has
    accepted the model's terms at
    https://huggingface.co/pyannote/embedding) and runs
    ``Inference(window="whole")`` over each speaker's utterances.

    Raises :class:`SpeakerRemapUnavailable` when the model cannot be
    obtained (missing/invalid token, gated terms not accepted, or
    offline) so callers can fall back to no-remap instead of failing the
    whole audit.
    """
    if len(segment_audio_paths) != len(words_paths):
        raise ValueError("segment_audio_paths and words_paths must align")

    inference = _load_embedding_inference(hf_token)
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
        # CLAUDE.md for parameter types at SDK boundaries; the helpers
        # below validate each per-field shape.
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
            vec = _embed_speaker_audio(inference, audio_path, words, spk)
            if vec is None:
                # Speaker had no usable audio in this segment (no spans
                # long enough, or every crop failed). Skip — keeping
                # labels and vectors aligned for clustering.
                continue
            labels.append(f"Segment_{seg_idx}_Speaker_{spk}")
            vectors.append(vec)
    if not vectors:
        return labels, np.zeros((0,), dtype=np.float64)
    return labels, np.asarray(vectors)


def _load_embedding_inference(hf_token: str | None) -> Inference:
    """Load ``pyannote/embedding`` and wrap it in a whole-window Inference.

    Translates HuggingFace download failures (gated repo / auth / offline)
    into :class:`SpeakerRemapUnavailable` so the pipeline degrades to
    no-remap rather than crashing.
    """
    from huggingface_hub.errors import HfHubHTTPError, LocalEntryNotFoundError
    from pyannote.audio import Inference, Model

    try:
        model = Model.from_pretrained("pyannote/embedding", token=hf_token)
    except (HfHubHTTPError, LocalEntryNotFoundError) as exc:
        raise SpeakerRemapUnavailable(
            "Could not load the gated 'pyannote/embedding' model. Set HF_TOKEN "
            + "to a HuggingFace token whose account has accepted the model "
            + "terms at https://huggingface.co/pyannote/embedding (and check "
            + f"network access). Underlying error: {exc}"
        ) from exc
    if model is None:
        raise SpeakerRemapUnavailable(
            "pyannote.audio Model.from_pretrained returned None for "
            + "'pyannote/embedding'."
        )
    return Inference(model, window="whole")


def _embed_speaker_audio(
    inference: _SupportsCrop,
    audio_path: Path,
    words: list[dict[str, Any]],
    speaker: int,
) -> NDArray[np.floating[Any]] | None:
    """Return the mean embedding for one speaker within one segment.

    Groups the speaker's words into contiguous utterance spans (mirroring
    the cue grouping in ``transcription.words_to_vtt``), embeds each span
    with ``Inference.crop`` (window="whole"), and averages the per-span
    vectors. Returns ``None`` when the speaker has no usable audio — no
    spans long enough, or every crop failed.

    Ported from ``pythonAudioA11y/speaker_identification.py``
    ``_extract_speaker_embedding_from_audio`` (lines 135-163).
    """
    from pyannote.core import Segment

    vectors: list[NDArray[np.floating[Any]]] = []
    for start, end in _speaker_utterance_spans(words, speaker):
        if end - start < _MIN_UTTERANCE_S:
            continue
        try:
            vec = inference.crop(audio_path, Segment(start, end))
        except Exception as exc:
            # pyannote/torch raise a wide, version-dependent set of
            # exceptions on awkward crops (e.g. a span near the file
            # boundary). A single bad utterance must not abort the whole
            # audit, so we log and skip it rather than narrowing to a
            # fragile exception tuple.
            logger.warning(
                "Embedding crop failed for %s [%.2f-%.2f]: %s",
                audio_path.name, start, end, exc,
            )
            continue
        vectors.append(np.asarray(vec, dtype=np.float64).ravel())
    if not vectors:
        return None
    stacked: NDArray[np.float64] = np.asarray(vectors, dtype=np.float64)
    return np.asarray(stacked.mean(axis=0), dtype=np.float64)


def _word_time(word: dict[str, Any], key: str) -> float | None:
    """Read a float timestamp (``start``/``end``) from a word record.

    Word records come from ``transcription.write_words_json`` where the
    value is a ``float``; be defensive about ints and numeric strings,
    and skip anything else.
    """
    raw = word.get(key)
    if isinstance(raw, bool):  # bool is an int subclass — not a timestamp
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def _speaker_utterance_spans(
    words: list[dict[str, Any]], speaker: int
) -> list[tuple[float, float]]:
    """Contiguous ``(start, end)`` spans where ``speaker`` is talking.

    Adjacent words by the same speaker form one span (matching the cue
    grouping in ``transcription.words_to_vtt``). Words with a missing or
    invalid speaker or timestamp break the current run and are skipped.
    """
    spans: list[tuple[float, float]] = []
    run_start: float | None = None
    run_end: float | None = None

    def flush() -> None:
        nonlocal run_start, run_end
        if run_start is not None and run_end is not None:
            spans.append((run_start, run_end))
        run_start = run_end = None

    for w in words:
        start = _word_time(w, "start")
        end = _word_time(w, "end")
        if _speaker_id(w) == speaker and start is not None and end is not None:
            if run_start is None:
                run_start = start
            run_end = end
        else:
            flush()
    flush()
    return spans


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
