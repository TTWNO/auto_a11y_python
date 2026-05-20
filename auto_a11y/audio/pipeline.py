"""Sequential orchestration of the audioA11y pipeline (stages A → G).

This module is pure logic over filesystem paths. No DB access, no
Anthropic / Deepgram client instantiation — those live in
:mod:`auto_a11y.audio.runner` which wraps this in async + JobManager
plumbing and injects pre-configured ``transcriber`` / ``analyzer``
collaborators.

Progress is reported via a callable injected at the entry point. The
runner hooks this to write ``Recording.progress`` after every stage.

Stages
------

A. ``segmenting``     — :func:`auto_a11y.audio.segmenter.split` extracts
                         per-segment m4a clips and writes
                         ``audio/segments.json``.

B. ``transcribing``   — per-segment :class:`Transcriber.transcribe` →
                         segment VTT + ``words.json`` files.

C. ``speaker_remap``  — optional, gated on
                         ``config.speaker_remap_enabled``.
                         :func:`build_mapping` extracts pyannote
                         embeddings and clusters them; writes the
                         mapping JSON to ``slot.speaker_map``.
                         **Phase 4 caveat:** ``_embed_speaker_audio``
                         is currently ``NotImplementedError``. The
                         pipeline catches that and falls back to no-
                         remap so end-to-end runs proceed.

D. ``merging_vtt``    — :func:`merge_segment_vtts` writes
                         ``slot.captions_vtt``. If stage C produced a
                         mapping, the merged file is post-processed in
                         place via :func:`remap_speaker_ids_in_vtt`.

E. ``analyzing``      — for each (context, language, kind) tuple:
                         :meth:`Analyzer.analyze` → JSON payload →
                         ``slot.json_path(kind=, lang=)`` written
                         atomically.

F. ``callouts``       — optional, gated on ``config.callouts_requested``.
                         **No-op for Phase 6** — Phase 9 implements the
                         ffmpeg overlay renderer.

G. ``importing``      — no-op here. The runner handles Mongo ingestion
                         via :func:`import_pipeline_output`.

TODO_PHASE4: when ``_embed_speaker_audio`` is implemented, stage C will
stop falling back. The fallback log line is the trigger for ripping the
``try / except NotImplementedError`` out.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable, Literal

from auto_a11y.audio.analysis import Analyzer
from auto_a11y.audio.prompts import Context, Kind
from auto_a11y.audio.segmenter import Segment, split
from auto_a11y.audio.speaker_identification import (
    SpeakerMapping,
    build_mapping,
    remap_speaker_ids_in_vtt,
)
from auto_a11y.audio.storage import AllocatedSlot
from auto_a11y.audio.transcription import Transcriber, words_to_vtt, write_words_json
from auto_a11y.audio.vtt_processor import merge_segment_vtts

logger = logging.getLogger(__name__)


# Stage names, ordered. Surfaced via the progress callback so the UI can
# label its progress bar. Kept in sync with the runner's localized
# ``audio-stage-*`` Fluent ids (added in Phase 7).
STAGES: tuple[str, ...] = (
    "segmenting",
    "transcribing",
    "speaker_remap",
    "merging_vtt",
    "analyzing",
    "callouts",
    "importing",
)


# The four analysis kinds, in their canonical order.
_ANALYSIS_KINDS: tuple[Kind, ...] = (
    "issues",
    "painpoints",
    "takeaways",
    "assertions",
)


@dataclass(frozen=True)
class PipelineConfig:
    """Per-run pipeline options.

    Plumbed through from :class:`Recording`. ``hf_token`` is the
    HuggingFace token for pyannote.audio model auth (only used when
    ``speaker_remap_enabled`` is true).
    """

    contexts: list[Context]
    languages: list[Literal["en", "fr"]]
    extended_context: bool
    speaker_remap_enabled: bool
    callouts_requested: bool
    hf_token: str | None


# (stage_name, current, total) — total is ``len(STAGES)``.
ProgressCallback = Callable[[str, int, int], None]


def run_pipeline(
    *,
    slot: AllocatedSlot,
    config: PipelineConfig,
    transcriber: Transcriber,
    analyzer: Analyzer,
    progress: ProgressCallback,
) -> None:
    """Run stages A → G in order.

    The callouts stage (F) is a no-op until Phase 9 lands; ``stage G``
    (Mongo ingestion) is the runner's responsibility — this function
    only writes JSON files to disk.

    Raises:
        Whatever ``segmenter`` / ``transcriber`` / ``analyzer`` raise.
        The runner catches and translates these to ``Recording.status``
        transitions.
    """
    total = len(STAGES)

    # === A — segmenting ============================================
    progress("segmenting", 1, total)
    segments = split(slot.source_mp4, slot)

    # === B — transcribing ==========================================
    progress("transcribing", 2, total)
    for seg in segments:
        transcription = transcriber.transcribe(slot.segment_m4a(seg.index))
        vtt_text = words_to_vtt(transcription.words)
        slot.write_atomic(slot.segment_vtt(seg.index), vtt_text.encode("utf-8"))
        # ``write_words_json`` writes directly (not via ``write_atomic``);
        # it's our own format and the path lives under the slot root, so
        # the OutsideSlot guard isn't needed here.
        write_words_json(transcription.words, slot.segment_words(seg.index))

    # === C — speaker_remap (optional) ==============================
    progress("speaker_remap", 3, total)
    mapping: SpeakerMapping | None = None
    if config.speaker_remap_enabled:
        try:
            mapping = build_mapping(
                segment_audio_paths=[slot.segment_m4a(s.index) for s in segments],
                words_paths=[slot.segment_words(s.index) for s in segments],
                hf_token=config.hf_token,
            )
            slot.write_atomic(
                slot.speaker_map,
                json.dumps(mapping.to_dict(), indent=2).encode("utf-8"),
            )
        except NotImplementedError:
            # TODO_PHASE4: ``_embed_speaker_audio`` is a stub. Fall back
            # to no-remap so end-to-end runs proceed; the merged VTT
            # keeps its per-segment ``Segment_i_Speaker_j`` voice tags.
            msg = (
                "speaker_remap: _embed_speaker_audio is a TODO_PHASE4 stub; "
                + "falling back to no-remap. Merged VTT will retain per-segment "
                + "speaker tags."
            )
            logger.warning(msg)
            mapping = None

    # === D — merging_vtt ===========================================
    progress("merging_vtt", 4, total)
    _merge_and_optionally_remap(slot=slot, segments=segments, mapping=mapping)

    # === E — analyzing =============================================
    progress("analyzing", 5, total)
    merged_text = slot.captions_vtt.read_text(encoding="utf-8")
    for ctx in config.contexts:
        for lang in config.languages:
            for kind in _ANALYSIS_KINDS:
                analysis = analyzer.analyze(
                    vtt=merged_text,
                    context=ctx,
                    kind=kind,
                    language=lang,
                    recording_id=slot.recording_id,
                )
                payload_bytes = json.dumps(
                    analysis.json_payload, indent=2, ensure_ascii=False
                ).encode("utf-8")
                slot.write_atomic(
                    slot.json_path(kind=kind, lang=lang),
                    payload_bytes,
                )

    # === F — callouts (optional) ===================================
    if config.callouts_requested:
        progress("callouts", 6, total)
        # TODO_PHASE9: render callouts video here. The runner / UI tag
        # ``Recording.callouts_status`` separately, so a future fill-in
        # of this branch doesn't need to touch any other phase.
        logger.info(
            "callouts requested but stage not implemented (TODO_PHASE9); skipping"
        )

    # === G — importing =============================================
    progress("importing", 7, total)
    # Mongo ingestion lives in the runner; this stage only writes the
    # progress marker so callers know we've finished the disk work.


def _merge_and_optionally_remap(
    *,
    slot: AllocatedSlot,
    segments: list[Segment],
    mapping: SpeakerMapping | None,
) -> None:
    """Concatenate per-segment VTTs and optionally remap speaker tags.

    Writes :attr:`AllocatedSlot.captions_vtt`. If ``mapping`` is non-
    ``None``, the merged file is post-processed in place via
    :func:`remap_speaker_ids_in_vtt`. We could merge to memory + remap
    + write atomically; instead we use the existing ``merge_segment_vtts``
    file-writer (which we've stabilized in Phase 3) and re-read + rewrite
    if remap is needed. The extra read/write is cheap relative to the
    Deepgram and Anthropic calls that bookend this stage.
    """
    segment_paths = [slot.segment_vtt(s.index) for s in segments]
    segment_offsets = [s.start_s for s in segments]
    merge_segment_vtts(
        segment_paths=segment_paths,
        segment_offsets_s=segment_offsets,
        output=slot.captions_vtt,
    )
    if mapping is None:
        return
    text = slot.captions_vtt.read_text(encoding="utf-8")
    remapped = remap_speaker_ids_in_vtt(text, mapping)
    slot.write_atomic(slot.captions_vtt, remapped.encode("utf-8"))
