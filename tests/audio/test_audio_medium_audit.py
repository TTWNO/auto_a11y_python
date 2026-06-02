"""Regression tests for two MEDIUM audit findings in the audio pipeline.

Bug A — ``cost_for_anthropic_call`` hard-failed (``NotImplementedError``)
for any model other than ``claude-opus-4-7``. Because it is called
*after* a paid Claude response, an operator setting ``CLAUDE_MODEL`` to
anything else would lose every analysis result purely over cost
bookkeeping. The fix falls back to the known rate and logs a warning;
cost bookkeeping must NEVER fail the analysis.

Bug B — the runner's ``progress(...)`` heartbeat re-fetched ``fresh``
only to check the cancel flag, then mutated and persisted the stale
outer-scope ``rec`` snapshot. Any field the web layer changed
out-of-band during processing was clobbered on the next heartbeat
(lost-update race). The fix applies the progress update to ``fresh``.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from auto_a11y.audio.config import AudioConfig
from auto_a11y.audio.cost import AnthropicUsage, cost_for_anthropic_call
from auto_a11y.audio.runner import VideoRunner
from auto_a11y.audio.storage import AllocatedSlot
from auto_a11y.models.recording import Recording


def _approx(actual: float, expected: float, tol: float = 1e-9) -> bool:
    return abs(actual - expected) < tol


# --------------------------------------------------------------------------
# Bug A: unknown-model cost falls back instead of raising
# --------------------------------------------------------------------------


def test_known_model_rate_unchanged() -> None:
    """The exact rate for the known model must not change."""
    usage = AnthropicUsage(
        input_tokens=10_000, output_tokens=2_000,
        cache_read_tokens=0, cache_write_tokens=0,
    )
    cost = cost_for_anthropic_call(
        usage, model="claude-opus-4-7", extended_context=False
    )
    # 10000 in @ $5/M = $0.05; 2000 out @ $25/M = $0.05; total $0.10
    assert _approx(cost, 0.10)


def test_unknown_model_does_not_raise_and_uses_fallback_rate(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unknown model must NOT raise; it prices at the fallback rate and warns."""
    usage = AnthropicUsage(
        input_tokens=10_000, output_tokens=2_000,
        cache_read_tokens=0, cache_write_tokens=0,
    )
    with caplog.at_level(logging.WARNING):
        cost = cost_for_anthropic_call(
            usage, model="claude-some-future-model", extended_context=False
        )
    # Fallback rate is the known Opus 4.7 rate: same $0.10 as the known model.
    assert _approx(cost, 0.10)
    assert cost > 0.0
    # A warning naming the unknown model + fallback was logged.
    assert any(
        record.levelno == logging.WARNING
        and "claude-some-future-model" in record.getMessage()
        for record in caplog.records
    )


# --------------------------------------------------------------------------
# Bug B: progress callback persists the freshly-fetched record, not the
# stale snapshot — so out-of-band web-layer edits are not clobbered.
# --------------------------------------------------------------------------


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
    return Recording(
        recording_id=recording_id,
        title="Test recording",
        project_id="proj-1",
    )


def test_progress_callback_does_not_clobber_out_of_band_field(
    slot: AllocatedSlot, tmp_path: Path
) -> None:
    """A field changed out-of-band by the web layer survives a progress beat.

    The runner loads ``rec`` at start (snapshot with title='Test recording').
    During processing the web layer renames the recording out-of-band, so the
    DB's fresh fetch returns a record with title='Renamed by web layer'. After
    the progress heartbeat the persisted record must carry the NEW title — the
    update must be based on ``fresh``, not the stale ``rec`` snapshot.
    """
    recording_id = slot.recording_id
    rec = _make_recording(recording_id)  # snapshot at run start

    # Distinct fresh object returned by the progress-beat fetch, carrying an
    # out-of-band edit the web layer made after the runner snapshotted ``rec``.
    fresh = _make_recording(recording_id)
    fresh.title = "Renamed by web layer"

    db = MagicMock()
    # First fetch: runner load -> rec. Subsequent fetches (progress beats) -> fresh.
    db.get_recording_by_recording_id.side_effect = [rec, fresh, fresh, fresh, fresh]
    db.update_recording.return_value = True

    storage = MagicMock()
    storage.get.return_value = slot

    persisted: list[Recording] = []

    def _capture(recording: Recording) -> bool:
        # Snapshot the title at the moment of persistence so a later in-place
        # mutation can't mask a clobber.
        persisted.append(recording)
        return True

    db.update_recording.side_effect = _capture

    config = _make_audio_config(tmp_path)
    runner = VideoRunner(db=db, storage=storage, config=config)

    def _fake_pipeline(
        *, slot: AllocatedSlot, config: Any, transcriber: Any, analyzer: Any, progress: Any
    ) -> None:
        _ = (slot, config, transcriber, analyzer)
        progress("segmenting", 1, 7)

    with patch("auto_a11y.audio.runner._make_anthropic_client", return_value=object()), \
         patch("auto_a11y.audio.runner._make_deepgram_client", return_value=object()), \
         patch("auto_a11y.audio.runner.run_pipeline", side_effect=_fake_pipeline), \
         patch("auto_a11y.audio.runner.import_pipeline_output", return_value=[]):
        asyncio.run(runner.run(recording_id))

    # The progress beat must have set progress on ``fresh`` (the freshly fetched
    # record), and persisting it must carry the out-of-band title.
    assert fresh.progress is not None
    assert fresh.progress.get("stage") == "segmenting"

    # The stale snapshot must NOT have been mutated by the progress beat.
    assert rec.progress is None

    # At least one persisted record carried the out-of-band title (i.e. fresh
    # was the object written), and none persisted the stale snapshot title with
    # a progress dict attached (which would prove the stale rec was clobbered).
    progress_writes = [r for r in persisted if r.progress is not None]
    assert progress_writes, "expected at least one progress persistence"
    assert all(r.title == "Renamed by web layer" for r in progress_writes)
