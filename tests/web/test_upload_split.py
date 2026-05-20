"""Tests for the split recording-upload routes.

The combined ``/recordings/upload`` page was split (2026-05) into a video
page (``/recordings/upload/video``) and a Dictaphone-JSON page
(``/recordings/upload/json``), with the bare ``/recordings/upload`` route
redirecting to the video page. These tests cover the routing/redirect, the
two GET pages, the video page's non-MP4 guard, and that the JSON-import
branch still imports after being rehomed onto ``upload_json``.
"""
from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

from flask.testing import FlaskClient


def test_upload_redirects_to_video(client: FlaskClient, mock_db: MagicMock) -> None:
    """GET /recordings/upload → 302 to the video form (back-compat)."""
    _ = mock_db
    resp = client.get('/recordings/upload')
    assert resp.status_code == 302
    assert '/recordings/upload/video' in resp.headers['Location']


def test_upload_video_get_renders(client: FlaskClient, mock_db: MagicMock) -> None:
    """GET /recordings/upload/video → 200."""
    _ = mock_db
    resp = client.get('/recordings/upload/video')
    assert resp.status_code == 200


def test_upload_json_get_renders(client: FlaskClient, mock_db: MagicMock) -> None:
    """GET /recordings/upload/json → 200."""
    _ = mock_db
    resp = client.get('/recordings/upload/json')
    assert resp.status_code == 200


def test_video_route_rejects_non_mp4(client: FlaskClient, mock_db: MagicMock) -> None:
    """A non-MP4 upload to the video route is refused (400), no Recording made."""
    resp = client.post(
        '/recordings/upload/video',
        data={
            'project_id': 'p-1',
            'languages': ['en'],
            # A JSON file, not an MP4 — wrong page.
            'video_file': (io.BytesIO(b'{}'), 'issues.json', 'application/json'),
        },
        content_type='multipart/form-data',
    )
    assert resp.status_code == 400
    assert not mock_db.create_recording.called


def test_json_import_still_works_on_json_route(
    client: FlaskClient,
    mock_db: MagicMock,
) -> None:
    """POST a Dictaphone JSON to /recordings/upload/json → imports + redirects."""
    recording = MagicMock()
    recording.recording_id = 'NED-A'

    payload = json.dumps({'recording': 'NED-A', 'issues': []})

    with patch(
        'auto_a11y.web.routes.recordings.DictaphoneImporter'
    ) as importer_cls:
        importer = importer_cls.return_value
        importer.import_from_file.return_value = (recording, [])

        resp = client.post(
            '/recordings/upload/json',
            data={
                'project_id': 'p-1',
                'json_file_en': (
                    io.BytesIO(payload.encode()),
                    'ned-a.json',
                    'application/json',
                ),
            },
            content_type='multipart/form-data',
        )

    assert resp.status_code == 302
    assert mock_db.create_recording.called
    importer.import_from_file.assert_called_once()


def test_json_route_rejects_missing_english_file(
    client: FlaskClient,
    mock_db: MagicMock,
) -> None:
    """No English JSON on the JSON route → flashed error redirect, no import."""
    resp = client.post(
        '/recordings/upload/json',
        data={'project_id': 'p-1'},
        content_type='multipart/form-data',
    )
    assert resp.status_code == 302
    assert not mock_db.create_recording.called
