"""Async wrapper around :mod:`auto_a11y.audio.pipeline`.

Owns the side-effects the pure pipeline deliberately avoids:

- DB writes to :class:`Recording` (status transitions, progress,
  manifest version, started_at / finished_at, error_message).
- Anthropic / Deepgram SDK client construction (from :class:`AudioConfig`).
- Cancellation polling between pipeline stages.
- Mongo ingestion of the on-disk JSON outputs (stage G — handled by
  :func:`auto_a11y.importers.dictaphone_importer.import_pipeline_output`).

Status transitions
------------------

::

    uploaded → processing → complete                (happy path)
    uploaded → processing → cancelled               (user cancel)
    uploaded → processing → failed (error_message)  (exception)

Cancellation is request-via-DB: the caller (typically the upload UI
flipping ``Recording.status = "cancelling"``) sets the flag; the
runner notices on its next ``progress(...)`` heartbeat and raises a
private :class:`_Cancelled` exception that unwinds cleanly.
"""
from __future__ import annotations

import logging
from datetime import datetime

from auto_a11y.audio import __version__
from auto_a11y.audio.analysis import Analyzer
from auto_a11y.audio.config import AudioConfig
from auto_a11y.audio.errors import CalloutsError, ConfigurationError
from auto_a11y.audio.pipeline import PipelineConfig, run_pipeline
from auto_a11y.audio.storage import AudioStorage
from auto_a11y.audio.transcription import Transcriber
from auto_a11y.core.database import Database
from auto_a11y.importers.dictaphone_importer import import_pipeline_output

logger = logging.getLogger(__name__)


class _Cancelled(Exception):
    """Raised internally when the runner detects ``status == "cancelling"``.

    Private to this module — callers never see this exception; the
    runner catches it and translates to ``status="cancelled"``.
    """


def _make_anthropic_client(api_key: str) -> object:
    """Construct an Anthropic SDK client.

    Imported lazily so importing this module doesn't pull anthropic in
    until a run actually starts (matches the SDK boundary pattern used
    across the audio package).
    """
    from anthropic import Anthropic
    return Anthropic(api_key=api_key)


def _make_deepgram_client(api_key: str) -> object:
    """Construct a Deepgram v5 SDK client (lazy import; see ``_make_anthropic_client``)."""
    from deepgram import DeepgramClient
    return DeepgramClient(api_key=api_key)


class VideoRunner:
    """Owns one end-to-end pipeline run keyed by ``Recording.recording_id``.

    Construct one instance per worker process. The :meth:`run` coroutine
    is the entry point ``JobManager`` invokes for ``VIDEO_PROCESSING``
    jobs.
    """

    def __init__(
        self,
        *,
        db: Database,
        storage: AudioStorage,
        config: AudioConfig,
    ) -> None:
        self._db = db
        self._storage = storage
        self._config = config

    def missing_api_keys(self) -> list[str]:
        """Names of required API keys not configured (delegates to config).

        The web layer calls this before submitting a job so it can refuse
        to start a run that would only fail at transcription time.
        """
        return self._config.missing_api_keys()

    async def run(self, recording_id: str) -> None:
        """Process the Recording with ``recording_id`` end-to-end.

        Async wrapper around the synchronous pipeline so the JobManager
        scheduler can await it. The pipeline itself runs in the calling
        event loop (no thread pool) — its hot loops are dominated by
        network I/O (Deepgram / Anthropic) which the SDK clients drive
        synchronously inside subprocess / HTTP. Future work could
        offload to a worker thread; for now the simpler shape matches
        the PDF runner.

        Bails cleanly when the Recording isn't found, logs and returns.
        """
        rec = self._db.get_recording_by_recording_id(recording_id)
        if rec is None:
            logger.error("VideoRunner: no Recording for id %s", recording_id)
            return
        slot = self._storage.get(recording_id)

        # Fail fast on missing API keys: build no client and start no
        # network call. An empty key would otherwise produce a cryptic
        # ``Illegal header value b'Token '`` deep in httpx after 3 retries
        # (see config.missing_api_keys). Record an actionable failure the
        # detail page can show instead.
        missing = self._config.missing_api_keys()
        if missing:
            joined = " and ".join(missing)
            plural = len(missing) > 1
            msg = (
                f"{joined} API key{'s' if plural else ''} "
                f"{'are' if plural else 'is'} not configured. Add "
                f"{'them' if plural else 'it'} in Settings to process recordings."
            )
            logger.error(
                "VideoRunner: %s (recording %s)", msg, recording_id
            )
            rec.status = "failed"
            rec.error_message = msg
            rec.finished_at = datetime.now()
            self._db.update_recording(rec)
            raise ConfigurationError(msg)

        anthropic_client = _make_anthropic_client(self._config.anthropic_api_key)
        deepgram_client = _make_deepgram_client(self._config.deepgram_api_key)

        started_at = datetime.now()
        rec.status = "processing"
        rec.started_at = started_at
        rec.manifest_version = __version__
        rec.error_message = None
        # Phase 9: callouts_status transitions. The upload route sets
        # this to "pending" when the box is checked, but be defensive
        # in case the runner is re-entered (e.g. retry) — re-assert
        # the right starting value here.
        rec.callouts_status = "pending" if rec.callouts_requested else "not-requested"
        self._db.update_recording(rec)

        def progress(stage: str, current: int, total: int) -> None:
            """Per-stage callback: cancel-check, then persist progress.

            We re-fetch the Recording (rather than trusting ``rec`` in
            scope) because the cancel flag is set out-of-band by the
            web layer. We then mutate and persist that freshly-fetched
            record — NOT the stale ``rec`` snapshot taken at run start —
            so other fields the web layer changed out-of-band (titles,
            tags, etc.) survive the heartbeat instead of being clobbered
            (lost-update race).
            """
            fresh = self._db.get_recording_by_recording_id(recording_id)
            if fresh is None:
                # Record vanished mid-run (deleted out-of-band); nothing to
                # persist. Don't fall back to the stale snapshot.
                return
            if fresh.status == "cancelling":
                raise _Cancelled()
            elapsed_ms = int(
                (datetime.now() - started_at).total_seconds() * 1000
            )
            fresh.progress = {
                "stage": stage,
                "current": current,
                "total": total,
                "started_at": started_at,
                "elapsed_ms": elapsed_ms,
            }
            self._db.update_recording(fresh)

        try:
            try:
                run_pipeline(
                    slot=slot,
                    config=PipelineConfig(
                        contexts=[rec.audit_context],
                        languages=list(rec.analysis_languages),
                        extended_context=rec.extended_context,
                        speaker_remap_enabled=rec.speaker_remap_enabled,
                        callouts_requested=rec.callouts_requested,
                        hf_token=self._config.huggingface_token,
                    ),
                    transcriber=Transcriber(
                        client=deepgram_client,
                        model=self._config.deepgram_model,
                    ),
                    analyzer=Analyzer(
                        client=anthropic_client,
                        model=self._config.claude_model,
                        extended_context=rec.extended_context,
                    ),
                    progress=progress,
                )
            except CalloutsError as callouts_exc:
                # Phase 9: Stage F failures NEVER fail the whole job.
                # The audit's transcripts, issues, painpoints,
                # takeaways, and assertions are all on disk already
                # (Stage E ran before Stage F). Tag the recording so
                # the UI can show a "rendering failed" badge, then
                # fall through to Stage G ingestion + status=complete.
                logger.warning(
                    "VideoRunner: callouts rendering failed for %s: %s",
                    recording_id, callouts_exc,
                )
                rec.callouts_status = "failed"
            else:
                if rec.callouts_requested:
                    rec.callouts_status = "complete"

            # === Stage G — Mongo ingestion ============================
            # The pipeline only writes JSON files; importing them into
            # Mongo is the runner's responsibility (we own the DB
            # handle).
            import_pipeline_output(self._db, slot, rec)

            # Honour a cancel that landed after the last progress heartbeat:
            # the web layer's only out-of-band write to a recording during
            # processing is `status` (-> "cancelling"), and a heartbeat may
            # not have observed it before the pipeline finished. Persisting
            # `rec` here is a full replace, so re-check the live status and
            # don't overwrite a pending cancel with "complete".
            live = self._db.get_recording_by_recording_id(recording_id)
            if live is not None and live.status == "cancelling":
                rec.status = "cancelled"
            else:
                rec.status = "complete"
            rec.finished_at = datetime.now()
            self._db.update_recording(rec)
        except _Cancelled:
            logger.info("VideoRunner: cancelled by user for %s", recording_id)
            rec.status = "cancelled"
            rec.finished_at = datetime.now()
            self._db.update_recording(rec)
        except Exception as exc:
            logger.exception("VideoRunner failed for %s", recording_id)
            rec.status = "failed"
            rec.error_message = str(exc)
            rec.finished_at = datetime.now()
            self._db.update_recording(rec)
            raise
