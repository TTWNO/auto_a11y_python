"""Tests for the MP4-upload branch of ``recordings.upload_recording``.

Phase 7 of the audioA11y integration. Covers the happy path (MP4 in,
confirm page out), the validation errors (no language, oversized file),
and the ``/recordings/<id>/process`` endpoint (status flip + job submit).

``segmenter.probe_duration`` and ``task_runner.submit_task`` are patched
so the tests run without ffprobe and without spawning a real thread.
"""
from __future__ import annotations

import io
from typing import Any
from unittest.mock import MagicMock, patch

from bson import ObjectId
from flask.testing import FlaskClient


_MP4_BYTES = b'fake mp4 content for testing'
_MOV_BYTES = b'fake mov content for testing'


def _make_recording_mock(
    *,
    mongo_id: ObjectId | None = None,
    status: str = 'uploaded',
    recording_id: str = 'REC-20260520120000-abcdef',
    project_id: str = 'p-1',
) -> MagicMock:
    """Build a Recording mock for ``db.get_recording`` return values.

    The route reads ``.status``, ``.recording_id``, ``.project_id``, then
    mutates ``.status`` / ``.started_at`` before calling
    ``db.update_recording``. The MagicMock attributes track the writes so
    the test can assert against them.
    """
    rec = MagicMock()
    rec.mongo_id = mongo_id or ObjectId()
    rec.id = str(rec.mongo_id)
    rec.status = status
    rec.recording_id = recording_id
    rec.project_id = project_id
    return rec


def test_mp4_upload_creates_recording_and_renders_confirm(
    client: FlaskClient,
    mock_db: MagicMock,
) -> None:
    """Happy path: upload an MP4, get the confirm page back with the estimate."""
    with patch(
        'auto_a11y.audio.segmenter.probe_duration',
        return_value=120.0,
    ):
        resp = client.post(
            '/recordings/upload/video',
            data={
                'project_id': 'p-1',
                'title': 'Audit session 1',
                'audit_context': 'audit',
                'languages': ['en'],
                'speaker_remap_enabled': 'on',
                'video_file': (io.BytesIO(_MP4_BYTES), 'audit.mp4', 'video/mp4'),
            },
            content_type='multipart/form-data',
        )

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Confirm template includes the estimated cost + a Process now button.
    assert 'Estimated cost' in body
    assert 'Process now' in body
    # The Recording was persisted.
    assert mock_db.create_recording.called
    saved = mock_db.create_recording.call_args.args[0]
    assert saved.status == 'uploaded'
    assert saved.audit_context == 'audit'
    assert saved.analysis_languages == ['en']
    assert saved.speaker_remap_enabled is True
    assert saved.extended_context is False
    assert saved.callouts_requested is False
    assert saved.estimated_cost_usd is not None
    assert saved.estimated_cost_usd > 0


def test_mp4_upload_rejects_when_no_language_selected(
    client: FlaskClient,
    mock_db: MagicMock,
) -> None:
    """Missing language selection → 400 + no Recording row created."""
    with patch(
        'auto_a11y.audio.segmenter.probe_duration',
        return_value=120.0,
    ):
        resp = client.post(
            '/recordings/upload/video',
            data={
                'project_id': 'p-1',
                'title': 'No-language upload',
                'audit_context': 'audit',
                # No 'languages' field at all.
                'video_file': (io.BytesIO(_MP4_BYTES), 'audit.mp4', 'video/mp4'),
            },
            content_type='multipart/form-data',
        )

    assert resp.status_code == 400
    assert not mock_db.create_recording.called


def test_mp4_upload_rejects_over_size_cap(
    client: FlaskClient,
    mock_db: MagicMock,
    app: Any,
) -> None:
    """File size > AUDIO_MAX_SIZE_MB → 413 + no Recording row created."""
    # Squeeze the cap so we don't have to actually upload >5 GB.
    original_max = app.app_config.AUDIO_MAX_SIZE_MB
    app.app_config.AUDIO_MAX_SIZE_MB = 0  # any non-empty file exceeds 0 MB
    try:
        resp = client.post(
            '/recordings/upload/video',
            data={
                'project_id': 'p-1',
                'title': 'Oversized upload',
                'audit_context': 'audit',
                'languages': ['en'],
                'video_file': (io.BytesIO(_MP4_BYTES), 'audit.mp4', 'video/mp4'),
            },
            content_type='multipart/form-data',
        )
    finally:
        app.app_config.AUDIO_MAX_SIZE_MB = original_max

    assert resp.status_code == 413
    assert not mock_db.create_recording.called


def test_mov_upload_is_accepted(
    client: FlaskClient,
    mock_db: MagicMock,
) -> None:
    """A QuickTime ``.mov`` (the macOS default recording format) is accepted.

    Regression test for the Mac-upload bug: the upload was hard-locked to
    ``video/mp4`` / ``.mp4``, so a Mac colleague's ``.mov`` (MIME
    ``video/quicktime``) was rejected with a 400 before it ever reached
    ffprobe. The pipeline only needs the audio track, which ffmpeg reads
    from any container, so any file ffprobe can parse must be accepted.
    """
    with patch(
        'auto_a11y.audio.segmenter.probe_duration',
        return_value=120.0,
    ):
        resp = client.post(
            '/recordings/upload/video',
            data={
                'project_id': 'p-1',
                'title': 'Mac audit session',
                'audit_context': 'audit',
                'languages': ['en'],
                'video_file': (
                    io.BytesIO(_MOV_BYTES), 'audit.mov', 'video/quicktime',
                ),
            },
            content_type='multipart/form-data',
        )

    assert resp.status_code == 200
    assert mock_db.create_recording.called


def test_arbitrary_video_container_is_accepted(
    client: FlaskClient,
    mock_db: MagicMock,
) -> None:
    """Format is validated by ffprobe, not by the extension/MIME allowlist.

    A ``.webm`` upload (neither ``.mp4`` nor ``.mov``) is accepted because
    ffprobe can read it; this locks in the "accept any video, let ffprobe
    be the gate" decision rather than a fixed container allowlist.
    """
    with patch(
        'auto_a11y.audio.segmenter.probe_duration',
        return_value=90.0,
    ):
        resp = client.post(
            '/recordings/upload/video',
            data={
                'project_id': 'p-1',
                'audit_context': 'audit',
                'languages': ['en'],
                'video_file': (
                    io.BytesIO(b'fake webm content'), 'audit.webm', 'video/webm',
                ),
            },
            content_type='multipart/form-data',
        )

    assert resp.status_code == 200
    assert mock_db.create_recording.called


def test_upload_rejected_when_ffprobe_cannot_read_file(
    client: FlaskClient,
    mock_db: MagicMock,
) -> None:
    """A file ffprobe can't parse → 400, no Recording row created.

    ffprobe is the real gate now that the extension/MIME allowlist is
    gone: an upload that isn't a readable media file must still be
    refused, not persisted.
    """
    from auto_a11y.audio.errors import AudioPipelineError

    with patch(
        'auto_a11y.audio.segmenter.probe_duration',
        side_effect=AudioPipelineError('unreadable'),
    ):
        resp = client.post(
            '/recordings/upload/video',
            data={
                'project_id': 'p-1',
                'audit_context': 'audit',
                'languages': ['en'],
                'video_file': (
                    io.BytesIO(b'this is not a video'), 'notes.txt', 'text/plain',
                ),
            },
            content_type='multipart/form-data',
        )

    assert resp.status_code == 400
    assert not mock_db.create_recording.called


def test_process_endpoint_flips_status_and_submits_job(
    client: FlaskClient,
    mock_db: MagicMock,
) -> None:
    """POST /recordings/<id>/process → submits a VIDEO_PROCESSING job, redirects 302."""
    rec = _make_recording_mock(status='uploaded')
    mock_db.get_recording.return_value = rec

    with patch(
        'auto_a11y.web.routes.recordings.task_runner.submit_task',
        return_value='video_processing_REC-xxxx_abcd',
    ) as submit_task, patch(
        'auto_a11y.web.routes.recordings.JobManager'
    ) as job_manager_cls:
        manager = MagicMock()
        job_manager_cls.get_instance.return_value = manager

        resp = client.post(f'/recordings/{rec.id}/process')

    assert resp.status_code == 302
    assert f'/recordings/{rec.id}' in resp.headers['Location']

    # Status flipped to "processing" before submission.
    assert rec.status == 'processing'
    assert mock_db.update_recording.called

    # Job record created with the right job type + metadata.
    assert job_manager_cls.get_instance.called
    manager.create_job.assert_called_once()
    create_kwargs = manager.create_job.call_args.kwargs
    from auto_a11y.core.job_manager import JobType
    assert create_kwargs['job_type'] == JobType.VIDEO_PROCESSING
    assert create_kwargs['metadata'] == {'recording_id': rec.recording_id}

    # Task submitted to the runner pool.
    submit_task.assert_called_once()


def test_process_endpoint_refuses_when_api_keys_missing(
    client: FlaskClient,
    mock_db: MagicMock,
    mock_video_runner: MagicMock,
) -> None:
    """No Deepgram/Anthropic key → refuse to start: no status flip, no job.

    Regression test for the Mac colleague's failure: with no API keys the
    pipeline used to start and die at transcription with a cryptic
    ``Illegal header value b'Token '``. The process route must instead
    refuse up front and tell the user to configure their keys.
    """
    rec = _make_recording_mock(status='uploaded')
    mock_db.get_recording.return_value = rec
    mock_video_runner.missing_api_keys.return_value = ['Deepgram', 'Anthropic']

    with patch(
        'auto_a11y.web.routes.recordings.task_runner.submit_task',
    ) as submit_task, patch(
        'auto_a11y.web.routes.recordings.JobManager'
    ) as job_manager_cls:
        resp = client.post(f'/recordings/{rec.id}/process')

    # Redirect back to the detail page with a flashed error.
    assert resp.status_code == 302
    assert f'/recordings/{rec.id}' in resp.headers['Location']
    # Crucially: nothing was started.
    assert rec.status == 'uploaded'
    assert not mock_db.update_recording.called
    assert not job_manager_cls.get_instance.called
    submit_task.assert_not_called()


def test_process_endpoint_rejects_non_uploaded_status(
    client: FlaskClient,
    mock_db: MagicMock,
) -> None:
    """A Recording that's already processing/complete is not re-submittable."""
    rec = _make_recording_mock(status='processing')
    mock_db.get_recording.return_value = rec

    resp = client.post(f'/recordings/{rec.id}/process')

    # 302 redirect to the detail page (with a flashed warning).
    assert resp.status_code == 302
    # No job was created — the runner's create_job stays untouched.
    # We don't assert against JobManager directly because the route bails
    # before constructing it; rec.status must be unchanged.
    assert rec.status == 'processing'
