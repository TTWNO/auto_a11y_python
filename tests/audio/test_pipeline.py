"""Tests for ``auto_a11y.audio.pipeline.run_pipeline``.

Strategy: each pipeline stage is patched with a mock that records the
call and writes the file the next stage expects. The tests assert:

- Each stage runs once, in the documented order (segmenting →
  transcribing → speaker_remap → merging_vtt → analyzing → callouts →
  importing).
- The ``progress`` callback fires once per stage with the correct
  (stage_name, current, total) triple.
- The optional stages (speaker_remap, callouts) honour their config
  toggles.
- The ``NotImplementedError`` fallback in stage C logs the warning and
  proceeds without remap (no exception bubbles out).
- The analyze loop iterates over the full contexts × languages × kinds
  cartesian product and writes one JSON file per combination.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from auto_a11y.audio.pipeline import PipelineConfig, run_pipeline
from auto_a11y.audio.segmenter import Segment
from auto_a11y.audio.speaker_identification import SpeakerMapping
from auto_a11y.audio.storage import AllocatedSlot
from auto_a11y.audio.transcription import TranscriptionResult, Word


def _make_slot(tmp_path: Path) -> AllocatedSlot:
    """Create a properly-named slot for tests.

    ``AllocatedSlot`` itself doesn't validate the id — that's
    ``AudioStorage.allocate``'s job. We construct directly here so we
    don't depend on a real ``data/recordings`` tree.
    """
    recording_id = "REC-20260520000000-abcdef"
    root = tmp_path / recording_id
    for sub in ("audio", "vtt", "captions", "json", "html", "video"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return AllocatedSlot(recording_id=recording_id, root=root)


def _seg(i: int, start: float, end: float) -> Segment:
    return Segment(index=i, start_s=start, end_s=end)


def _stub_transcription_result() -> TranscriptionResult:
    """One-word transcription so the VTT writer has content."""
    return TranscriptionResult(
        transcript="hello",
        words=[Word(text="hello", start=0.0, end=0.5, speaker=0)],
    )


def _stub_analysis_result(json_payload: dict[str, Any]) -> Any:
    """Return an object that quacks like ``AnalysisResult`` for the loop body."""
    class _Result:
        def __init__(self) -> None:
            self.json_payload = json_payload
            self.html_text = None
            self.cost_usd = 0.0
            self.input_tokens = 0
            self.output_tokens = 0
    return _Result()


@pytest.fixture
def slot(tmp_path: Path) -> AllocatedSlot:
    return _make_slot(tmp_path)


def _noop_progress(stage: str, current: int, total: int) -> None:
    """Drop-in ``ProgressCallback`` for tests that don't assert progress."""
    _ = (stage, current, total)


def _identity_remap(text: str, mapping: SpeakerMapping) -> str:
    """Drop-in replacement for ``remap_speaker_ids_in_vtt`` that returns input."""
    _ = mapping
    return text


def test_run_pipeline_runs_all_stages_in_order(slot: AllocatedSlot) -> None:
    """Happy path: every stage fires once in the documented order."""
    progress_calls: list[tuple[str, int, int]] = []

    def progress(stage: str, current: int, total: int) -> None:
        progress_calls.append((stage, current, total))

    segments = [_seg(0, 0.0, 60.0), _seg(1, 60.0, 120.0)]

    # ``split`` writes nothing — return our segments directly. The
    # transcribing stage then writes one VTT per segment, which the
    # merging stage reads. We seed both via mocks.
    with patch("auto_a11y.audio.pipeline.split", return_value=segments) as split_mock, \
         patch("auto_a11y.audio.pipeline.merge_segment_vtts") as merge_mock:
        transcriber = MagicMock()
        transcriber.transcribe.return_value = _stub_transcription_result()
        analyzer = MagicMock()
        analyzer.analyze.return_value = _stub_analysis_result(
            {"recording": slot.recording_id, "issues": []}
        )

        # merge_segment_vtts needs to write captions.vtt because the
        # analyze stage reads it back. Side-effect to do that.
        def _do_merge(*, segment_paths: list[Path], segment_offsets_s: list[float], output: Path) -> None:
            _ = (segment_paths, segment_offsets_s)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("WEBVTT\n", encoding="utf-8")
        merge_mock.side_effect = _do_merge

        run_pipeline(
            slot=slot,
            config=PipelineConfig(
                contexts=["audit"],
                languages=["en"],
                extended_context=False,
                speaker_remap_enabled=False,  # skip stage C entirely
                callouts_requested=False,     # skip stage F
                hf_token=None,
            ),
            transcriber=transcriber,
            analyzer=analyzer,
            progress=progress,
        )

    # Stages fire in order. callouts is skipped (config.callouts_requested=False).
    stage_names = [c[0] for c in progress_calls]
    assert stage_names == [
        "segmenting", "transcribing", "speaker_remap",
        "merging_vtt", "analyzing", "importing",
    ]
    # Every callback got total=7 (the full stages tuple).
    assert all(total == 7 for _, _, total in progress_calls)

    # Each upstream collaborator fired the expected number of times.
    split_mock.assert_called_once()
    assert transcriber.transcribe.call_count == 2  # one per segment
    merge_mock.assert_called_once()
    # 1 context × 1 language × 4 kinds = 4 analyze calls.
    assert analyzer.analyze.call_count == 4


def test_run_pipeline_writes_one_json_per_language_kind(slot: AllocatedSlot) -> None:
    """Analyze stage emits one JSON file per (language, kind).

    NOTE: ``AllocatedSlot.json_path`` doesn't include ``context`` in the
    filename — multiple contexts would clobber each other on disk.
    The runner only ever passes a single context (``[rec.audit_context]``)
    so this is not a problem in practice; the pipeline still iterates
    ``contexts × languages × kinds`` and we lean on the runner to keep
    ``contexts`` length 1. This test pins the per-language × kind shape
    (1 context × 2 languages × 4 kinds = 8 files).
    """
    segments = [_seg(0, 0.0, 30.0)]
    with patch("auto_a11y.audio.pipeline.split", return_value=segments), \
         patch("auto_a11y.audio.pipeline.merge_segment_vtts") as merge_mock:
        transcriber = MagicMock()
        transcriber.transcribe.return_value = _stub_transcription_result()
        analyzer = MagicMock()
        analyzer.analyze.return_value = _stub_analysis_result(
            {"recording": slot.recording_id, "issues": [{"title": "X"}]}
        )

        def _do_merge(*, segment_paths: list[Path], segment_offsets_s: list[float], output: Path) -> None:
            _ = (segment_paths, segment_offsets_s)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("WEBVTT\n", encoding="utf-8")
        merge_mock.side_effect = _do_merge

        run_pipeline(
            slot=slot,
            config=PipelineConfig(
                contexts=["audit"],
                languages=["en", "fr"],
                extended_context=False,
                speaker_remap_enabled=False,
                callouts_requested=False,
                hf_token=None,
            ),
            transcriber=transcriber,
            analyzer=analyzer,
            progress=_noop_progress,
        )

    # 1 context × 2 languages × 4 kinds = 8 JSON files on disk.
    json_files = sorted(slot.json_dir.glob(f"{slot.recording_id}.*.json"))
    assert len(json_files) == 8, [p.name for p in json_files]


def test_speaker_remap_disabled_skips_build_mapping(slot: AllocatedSlot) -> None:
    """When ``speaker_remap_enabled=False`` we don't call build_mapping."""
    segments = [_seg(0, 0.0, 30.0)]
    with patch("auto_a11y.audio.pipeline.split", return_value=segments), \
         patch("auto_a11y.audio.pipeline.merge_segment_vtts") as merge_mock, \
         patch("auto_a11y.audio.pipeline.build_mapping") as build_mock:
        transcriber = MagicMock()
        transcriber.transcribe.return_value = _stub_transcription_result()
        analyzer = MagicMock()
        analyzer.analyze.return_value = _stub_analysis_result(
            {"recording": slot.recording_id, "issues": []}
        )

        def _do_merge(*, segment_paths: list[Path], segment_offsets_s: list[float], output: Path) -> None:
            _ = (segment_paths, segment_offsets_s)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("WEBVTT\n", encoding="utf-8")
        merge_mock.side_effect = _do_merge

        run_pipeline(
            slot=slot,
            config=PipelineConfig(
                contexts=["audit"],
                languages=["en"],
                extended_context=False,
                speaker_remap_enabled=False,
                callouts_requested=False,
                hf_token=None,
            ),
            transcriber=transcriber,
            analyzer=analyzer,
            progress=_noop_progress,
        )

    build_mock.assert_not_called()


def test_speaker_remap_falls_back_when_embed_stub_raises(slot: AllocatedSlot) -> None:
    """Stage C catches NotImplementedError from the TODO_PHASE4 stub.

    ``build_mapping`` currently raises NotImplementedError via
    ``_embed_speaker_audio``. The pipeline must catch it and proceed
    with no-remap so end-to-end runs aren't blocked on the Phase 4
    follow-up.
    """
    segments = [_seg(0, 0.0, 30.0)]
    with patch("auto_a11y.audio.pipeline.split", return_value=segments), \
         patch("auto_a11y.audio.pipeline.merge_segment_vtts") as merge_mock, \
         patch(
             "auto_a11y.audio.pipeline.build_mapping",
             side_effect=NotImplementedError("TODO_PHASE4 stub"),
         ) as build_mock:
        transcriber = MagicMock()
        transcriber.transcribe.return_value = _stub_transcription_result()
        analyzer = MagicMock()
        analyzer.analyze.return_value = _stub_analysis_result(
            {"recording": slot.recording_id, "issues": []}
        )

        def _do_merge(*, segment_paths: list[Path], segment_offsets_s: list[float], output: Path) -> None:
            _ = (segment_paths, segment_offsets_s)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("WEBVTT\n", encoding="utf-8")
        merge_mock.side_effect = _do_merge

        # Must NOT raise.
        run_pipeline(
            slot=slot,
            config=PipelineConfig(
                contexts=["audit"],
                languages=["en"],
                extended_context=False,
                speaker_remap_enabled=True,  # opt in
                callouts_requested=False,
                hf_token="token",
            ),
            transcriber=transcriber,
            analyzer=analyzer,
            progress=_noop_progress,
        )

    build_mock.assert_called_once()
    # speaker_map should NOT have been written because we hit the fallback.
    assert not slot.speaker_map.exists()


def test_speaker_remap_writes_mapping_when_build_succeeds(slot: AllocatedSlot) -> None:
    """When ``build_mapping`` returns a SpeakerMapping, we persist it."""
    segments = [_seg(0, 0.0, 30.0)]
    mapping = SpeakerMapping(mapping={"Segment_0_Speaker_0": 1})

    with patch("auto_a11y.audio.pipeline.split", return_value=segments), \
         patch("auto_a11y.audio.pipeline.merge_segment_vtts") as merge_mock, \
         patch("auto_a11y.audio.pipeline.build_mapping", return_value=mapping), \
         patch(
             "auto_a11y.audio.pipeline.remap_speaker_ids_in_vtt",
             side_effect=_identity_remap,
         ) as remap_mock:
        transcriber = MagicMock()
        transcriber.transcribe.return_value = _stub_transcription_result()
        analyzer = MagicMock()
        analyzer.analyze.return_value = _stub_analysis_result(
            {"recording": slot.recording_id, "issues": []}
        )

        def _do_merge(*, segment_paths: list[Path], segment_offsets_s: list[float], output: Path) -> None:
            _ = (segment_paths, segment_offsets_s)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("WEBVTT\n", encoding="utf-8")
        merge_mock.side_effect = _do_merge

        run_pipeline(
            slot=slot,
            config=PipelineConfig(
                contexts=["audit"],
                languages=["en"],
                extended_context=False,
                speaker_remap_enabled=True,
                callouts_requested=False,
                hf_token="token",
            ),
            transcriber=transcriber,
            analyzer=analyzer,
            progress=_noop_progress,
        )

    assert slot.speaker_map.exists()
    loaded = json.loads(slot.speaker_map.read_text())
    assert loaded == {"speakers": {"Segment_0_Speaker_0": 1}}
    # Remap was applied to the merged VTT.
    remap_mock.assert_called_once()


def test_callouts_requested_emits_progress_marker(slot: AllocatedSlot) -> None:
    """``callouts`` stage fires the progress callback when requested.

    Phase 6 doesn't render callouts (Phase 9 does); it just emits the
    progress marker so the UI can label the bar.
    """
    progress_calls: list[str] = []

    def progress(stage: str, current: int, total: int) -> None:
        _ = (current, total)
        progress_calls.append(stage)

    segments = [_seg(0, 0.0, 30.0)]
    with patch("auto_a11y.audio.pipeline.split", return_value=segments), \
         patch("auto_a11y.audio.pipeline.merge_segment_vtts") as merge_mock:
        transcriber = MagicMock()
        transcriber.transcribe.return_value = _stub_transcription_result()
        analyzer = MagicMock()
        analyzer.analyze.return_value = _stub_analysis_result(
            {"recording": slot.recording_id, "issues": []}
        )

        def _do_merge(*, segment_paths: list[Path], segment_offsets_s: list[float], output: Path) -> None:
            _ = (segment_paths, segment_offsets_s)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("WEBVTT\n", encoding="utf-8")
        merge_mock.side_effect = _do_merge

        run_pipeline(
            slot=slot,
            config=PipelineConfig(
                contexts=["audit"],
                languages=["en"],
                extended_context=False,
                speaker_remap_enabled=False,
                callouts_requested=True,
                hf_token=None,
            ),
            transcriber=transcriber,
            analyzer=analyzer,
            progress=progress,
        )

    assert "callouts" in progress_calls
    # ``callouts`` comes after ``analyzing`` and before ``importing``.
    analyzing_idx = progress_calls.index("analyzing")
    callouts_idx = progress_calls.index("callouts")
    importing_idx = progress_calls.index("importing")
    assert analyzing_idx < callouts_idx < importing_idx
