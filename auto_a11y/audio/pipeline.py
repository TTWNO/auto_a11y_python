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
                         mapping JSON to ``slot.speaker_map``. Loading
                         the gated ``pyannote/embedding`` model needs an
                         ``HF_TOKEN`` whose account has accepted the
                         model terms; when it can't be loaded (no/invalid
                         token, or offline) :func:`build_mapping` raises
                         :class:`SpeakerRemapUnavailable`, which the
                         pipeline catches to fall back to no-remap so the
                         rest of the run still proceeds.

D. ``merging_vtt``    — :func:`merge_segment_vtts` writes
                         ``slot.captions_vtt``. If stage C produced a
                         mapping, the merged file is post-processed in
                         place via :func:`remap_speaker_ids_in_vtt`.

E. ``analyzing``      — for each (context, language, kind) tuple:
                         :meth:`Analyzer.analyze` → JSON payload →
                         ``slot.json_path(kind=, lang=)`` written
                         atomically.

F. ``callouts``       — optional, gated on ``config.callouts_requested``.
                         :func:`_render_callouts_stage` reads the
                         English ``issues`` JSON and shells out to
                         ffmpeg via
                         :func:`auto_a11y.audio.callouts.render_callouts_video`.
                         Raises :class:`CalloutsError` on failure; the
                         runner catches it and marks only
                         ``Recording.callouts_status``.

G. ``importing``      — no-op here. The runner handles Mongo ingestion
                         via :func:`import_pipeline_output`.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable, Literal, TypeGuard

from auto_a11y.audio.analysis import Analyzer
from auto_a11y.audio.callouts import render_callouts_video
from auto_a11y.audio.errors import CalloutsError
from auto_a11y.audio.prompts import Context, Kind
from auto_a11y.audio.segmenter import Segment, split
from auto_a11y.audio.speaker_identification import (
    SpeakerMapping,
    SpeakerRemapUnavailable,
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
        except SpeakerRemapUnavailable as exc:
            # The gated pyannote model couldn't be loaded (no/invalid
            # HF_TOKEN, terms not accepted, or offline). Fall back to
            # no-remap so the rest of the audit proceeds; the merged VTT
            # keeps its per-segment ``Segment_i_Speaker_j`` voice tags.
            logger.warning(
                "speaker_remap unavailable (%s); falling back to no-remap. "
                + "Merged VTT will retain per-segment speaker tags.", exc,
            )
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
        _render_callouts_stage(slot=slot)

    # === G — importing =============================================
    progress("importing", 7, total)
    # Mongo ingestion lives in the runner; this stage only writes the
    # progress marker so callers know we've finished the disk work.


def _is_str_obj_dict(val: object) -> TypeGuard[dict[str, object]]:
    """Narrow a ``json.loads`` result to ``dict[str, object]``.

    Matches the same helper in :mod:`auto_a11y.audio.analysis`.
    ``json.loads`` always produces ``str``-keyed dicts for JSON objects;
    the runtime check is ``isinstance(val, dict)`` only.
    """
    return isinstance(val, dict)


def _is_obj_list(val: object) -> TypeGuard[list[object]]:
    """Narrow ``object`` to ``list[object]`` for nested JSON values."""
    return isinstance(val, list)


def _render_callouts_stage(*, slot: AllocatedSlot) -> None:
    """Render the optional callouts video from the English issues JSON.

    Reads ``slot.json_path(kind="issues", lang="en")`` (the canonical
    issues output written by Stage E), parses it, and shells out to
    ``ffmpeg`` via :func:`render_callouts_video`. Failures are
    re-raised as :class:`CalloutsError` so the runner can catch them
    specifically and mark only ``Recording.callouts_status`` (not the
    whole job) as ``"failed"``.
    """
    issues_path = slot.json_path(kind="issues", lang="en")
    if not issues_path.exists():
        # No English issues file — Stage E either skipped English or
        # the runner is running with an unusual config. Nothing to
        # render; not a failure.
        logger.info(
            "callouts: no issues JSON at %s; skipping render", issues_path
        )
        return

    try:
        issues_text = issues_path.read_text(encoding="utf-8")
        issues_raw: object = json.loads(issues_text)
    except (OSError, json.JSONDecodeError) as exc:
        raise CalloutsError(
            f"Could not read or parse issues JSON at {issues_path}: {exc}"
        ) from exc

    issues_list: list[dict[str, object]] = []
    if _is_str_obj_dict(issues_raw):
        raw_issues = issues_raw.get("issues")
        if _is_obj_list(raw_issues):
            for entry in raw_issues:
                if _is_str_obj_dict(entry):
                    issues_list.append(entry)

    render_callouts_video(
        source_mp4=slot.source_mp4,
        issues=issues_list,
        output_mp4=slot.callouts_mp4,
    )


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
