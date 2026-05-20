"""Tests for ``auto_a11y.audio.runner.VideoRunner``.

These tests mock the pipeline entry point and the DB so we can assert
the runner's status / progress / cancellation behaviour without
touching ffmpeg, Deepgram, Anthropic, or Mongo.

Scenarios covered:

- Happy path: ``uploaded`` → ``processing`` → ``complete``, with
  ``manifest_version`` and ``finished_at`` populated.
- Cancellation: pre-set ``status="cancelling"`` so the first progress
  heartbeat raises ``_Cancelled``; final status is ``"cancelled"`` and
  no exception escapes.
- Failure: pipeline raises a generic exception; final status is
  ``"failed"`` with ``error_message`` populated, then the exception is
  re-raised so the JobManager can record the job failure.
- Recording-missing: ``get_recording_by_recording_id`` returns ``None``;
  runner logs and returns without raising.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from auto_a11y.audio import __version__
from auto_a11y.audio.config import AudioConfig
from auto_a11y.audio.storage import AllocatedSlot
from auto_a11y.audio.runner import VideoRunner
from auto_a11y.models.recording import Recording


@pytest.fixture
def slot(tmp_path: Path) -> AllocatedSlot:
    recording_id = "REC-20260520000000-abcdef"
    root = tmp_path / recording_id
    for sub in ("audio", "vtt", "captions", "json", "html", "video"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return AllocatedSlot(recording_id=recording_id, root=root)


def _make_audio_config(tmp_path: Path) -> AudioConfig:
    return AudioConfig(
        deepgram_api_key="dg-key",
        anthropic_api_key="an-key",
        claude_model="claude-opus-4-7",
        deepgram_model="nova-3",
        huggingface_token=None,
        data_dir=tmp_path,
    )


def _make_recording(recording_id: str) -> Recording:
    """Recording with the audio-pipeline defaults; ``status='uploaded'``."""
    return Recording(
        recording_id=recording_id,
        title="Test recording",
        project_id="proj-1",
    )


def test_runner_happy_path_sets_status_complete(slot: AllocatedSlot, tmp_path: Path) -> None:
    """``run`` flips uploaded → processing → complete on success."""
    recording_id = slot.recording_id
    rec = _make_recording(recording_id)

    # The DB mock tracks updates so we can assert the final state. We
    # simulate ``get_recording_by_recording_id`` always returning the
    # same in-memory ``rec`` (no fresh-fetch cancel races for this test).
    db = MagicMock()
    db.get_recording_by_recording_id.return_value = rec
    db.update_recording.return_value = True

    storage = MagicMock()
    storage.get.return_value = slot

    config = _make_audio_config(tmp_path)
    runner = VideoRunner(db=db, storage=storage, config=config)

    with patch("auto_a11y.audio.runner._make_anthropic_client", return_value=object()), \
         patch("auto_a11y.audio.runner._make_deepgram_client", return_value=object()), \
         patch("auto_a11y.audio.runner.run_pipeline") as pipeline_mock, \
         patch("auto_a11y.audio.runner.import_pipeline_output", return_value=[]):
        # Pipeline does nothing (we don't need to walk the actual stages here).
        pipeline_mock.return_value = None
        asyncio.run(runner.run(recording_id))

    assert rec.status == "complete"
    assert rec.error_message is None
    assert rec.finished_at is not None
    assert rec.manifest_version == __version__
    # update_recording was called at least 3 times: ``processing`` start,
    # then ``complete`` finish (and possibly progress beats — pipeline is mocked,
    # so no progress callbacks).
    assert db.update_recording.call_count >= 2


def test_runner_cancellation_sets_status_cancelled(slot: AllocatedSlot, tmp_path: Path) -> None:
    """A cancel-flag set out-of-band unwinds to status='cancelled'.

    The runner re-fetches the Recording on each progress heartbeat;
    we simulate that by having ``get_recording_by_recording_id``
    return a fresh recording with status='cancelling' so the first
    heartbeat raises ``_Cancelled``.
    """
    recording_id = slot.recording_id
    rec = _make_recording(recording_id)
    cancelled_rec = _make_recording(recording_id)
    cancelled_rec.status = "cancelling"

    # First call: runner load. Subsequent calls: progress check sees cancel.
    db = MagicMock()
    db.get_recording_by_recording_id.side_effect = [rec, cancelled_rec, cancelled_rec, cancelled_rec]
    db.update_recording.return_value = True

    storage = MagicMock()
    storage.get.return_value = slot

    config = _make_audio_config(tmp_path)
    runner = VideoRunner(db=db, storage=storage, config=config)

    # The pipeline fires the progress callback once (which raises
    # _Cancelled on the cancel-fetch). We simulate this with a side
    # effect that calls progress(...) once.
    def _fake_pipeline(*, slot: AllocatedSlot, config: Any, transcriber: Any, analyzer: Any, progress: Any) -> None:
        _ = (slot, config, transcriber, analyzer)
        progress("segmenting", 1, 7)  # triggers cancel check
    with patch("auto_a11y.audio.runner._make_anthropic_client", return_value=object()), \
         patch("auto_a11y.audio.runner._make_deepgram_client", return_value=object()), \
         patch("auto_a11y.audio.runner.run_pipeline", side_effect=_fake_pipeline), \
         patch("auto_a11y.audio.runner.import_pipeline_output", return_value=[]) as import_mock:
        # No exception should escape.
        asyncio.run(runner.run(recording_id))

    assert rec.status == "cancelled"
    assert rec.finished_at is not None
    # We bailed before stage G — importer must NOT have been called.
    import_mock.assert_not_called()


def test_runner_failure_sets_status_failed_with_message(slot: AllocatedSlot, tmp_path: Path) -> None:
    """A generic pipeline exception → status='failed' + error_message + reraise."""
    recording_id = slot.recording_id
    rec = _make_recording(recording_id)

    db = MagicMock()
    db.get_recording_by_recording_id.return_value = rec
    db.update_recording.return_value = True

    storage = MagicMock()
    storage.get.return_value = slot

    config = _make_audio_config(tmp_path)
    runner = VideoRunner(db=db, storage=storage, config=config)

    boom = RuntimeError("ffmpeg crashed")
    with patch("auto_a11y.audio.runner._make_anthropic_client", return_value=object()), \
         patch("auto_a11y.audio.runner._make_deepgram_client", return_value=object()), \
         patch("auto_a11y.audio.runner.run_pipeline", side_effect=boom), \
         patch("auto_a11y.audio.runner.import_pipeline_output", return_value=[]):
        with pytest.raises(RuntimeError, match="ffmpeg crashed"):
            asyncio.run(runner.run(recording_id))

    assert rec.status == "failed"
    assert rec.error_message == "ffmpeg crashed"
    assert rec.finished_at is not None


def test_runner_missing_recording_returns_cleanly(slot: AllocatedSlot, tmp_path: Path) -> None:
    """``run`` logs and returns when the Recording isn't found.

    No exception escapes; no DB writes happen.
    """
    db = MagicMock()
    db.get_recording_by_recording_id.return_value = None

    storage = MagicMock()

    config = _make_audio_config(tmp_path)
    runner = VideoRunner(db=db, storage=storage, config=config)

    # Pipeline / clients must NOT be invoked.
    with patch("auto_a11y.audio.runner._make_anthropic_client") as anthropic_mock, \
         patch("auto_a11y.audio.runner._make_deepgram_client") as deepgram_mock, \
         patch("auto_a11y.audio.runner.run_pipeline") as pipeline_mock:
        asyncio.run(runner.run("REC-not-real"))

    anthropic_mock.assert_not_called()
    deepgram_mock.assert_not_called()
    pipeline_mock.assert_not_called()
    db.update_recording.assert_not_called()


def test_runner_progress_callback_records_stage(slot: AllocatedSlot, tmp_path: Path) -> None:
    """Progress callback writes ``rec.progress`` and persists via update_recording.

    Inspects ``rec`` after the run to confirm the last-seen stage is
    recorded; the runner's success path overwrites status to 'complete'
    but ``progress`` lingers (the final progress beat ran ``importing``).
    """
    recording_id = slot.recording_id
    rec = _make_recording(recording_id)

    db = MagicMock()
    db.get_recording_by_recording_id.return_value = rec
    db.update_recording.return_value = True

    storage = MagicMock()
    storage.get.return_value = slot

    config = _make_audio_config(tmp_path)
    runner = VideoRunner(db=db, storage=storage, config=config)

    def _fake_pipeline(*, slot: AllocatedSlot, config: Any, transcriber: Any, analyzer: Any, progress: Any) -> None:
        _ = (slot, config, transcriber, analyzer)
        progress("segmenting", 1, 7)
        progress("transcribing", 2, 7)

    with patch("auto_a11y.audio.runner._make_anthropic_client", return_value=object()), \
         patch("auto_a11y.audio.runner._make_deepgram_client", return_value=object()), \
         patch("auto_a11y.audio.runner.run_pipeline", side_effect=_fake_pipeline), \
         patch("auto_a11y.audio.runner.import_pipeline_output", return_value=[]):
        asyncio.run(runner.run(recording_id))

    assert rec.status == "complete"
    # Progress dict last-recorded should be ``transcribing`` (last beat
    # before pipeline returned).
    assert rec.progress is not None
    assert rec.progress.get("stage") == "transcribing"
    assert rec.progress.get("current") == 2
    assert rec.progress.get("total") == 7
    started_at = rec.progress.get("started_at")
    assert isinstance(started_at, datetime)
