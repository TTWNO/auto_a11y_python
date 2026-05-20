"""Shared fixtures for ``tests/web/``.

Mirrors the pattern from ``tests/pdf/test_pdf_routes.py``: a session-
scoped Flask app with stub templates, mocks for the database and the
audio runner, and a per-test client. Fluent is initialised on the real
translations directory so ``ftl(...)`` resolves the audio.ftl IDs added
in Phase 7.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from flask import Blueprint, Flask
from flask.testing import FlaskClient


@pytest.fixture(scope='session')
def session_template_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Tmp template folder with stub bodies for the recordings routes.

    Session-scoped so we don't pay the I/O cost per test. We only stub
    the templates that the upload + process routes actually render; the
    production templates are exercised in their own renderer tests.
    """
    base = tmp_path_factory.mktemp("web_route_templates")
    base.joinpath("base.html").write_text(
        "<html><body>{% block content %}{% endblock %}</body></html>"
    )
    rec_dir = base / "recordings"
    rec_dir.mkdir()
    # The route only consults the form GET handler when rendering this;
    # the field IDs don't matter for the route assertions in this suite.
    rec_dir.joinpath("upload.html").write_text(
        "<html><body>recordings upload</body></html>"
    )
    rec_dir.joinpath("list.html").write_text(
        "<html><body>recordings list</body></html>"
    )
    rec_dir.joinpath("detail.html").write_text(
        "<html><body>recording {{ recording.recording_id }}</body></html>"
    )
    rec_dir.joinpath("combined.html").write_text(
        "<html><body>combined</body></html>"
    )
    rec_dir.joinpath("upload_confirm.html").write_text(
        (
            "<html><body>"
            + "{{ ftl('audio-confirm-estimated-cost') }} "
            + "${{ '%.4f' | format(estimate.total_usd) }} "
            + "{{ ftl('audio-confirm-process-button') }} "
            + "<form method='POST' action='/recordings/{{ recording.id }}/process'>"
            + "<button>process</button></form>"
            + "</body></html>"
        )
    )
    return base


@pytest.fixture(scope='session')
def session_storage_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Session-scoped storage root for the AudioStorage used by tests."""
    return tmp_path_factory.mktemp("audio_storage")


@pytest.fixture(scope='session')
def app(
    session_template_dir: Path,
    session_storage_dir: Path,
) -> Flask:
    """Flask app, Fluent initialised, recordings blueprint registered.

    Session-scoped so ``init_fluent`` (loads 5000+ FTL messages) runs
    once. ``app.db`` / ``app.audio_storage`` / ``app.video_runner`` are
    placeholders here — per-test fixtures replace them so each test sees
    fresh mocks.
    """
    from auto_a11y.audio.storage import AudioStorage

    flask_app = Flask(
        __name__,
        template_folder=str(session_template_dir),
    )
    flask_app.testing = True
    flask_app.secret_key = 'test'

    cfg = MagicMock()
    cfg.AUDIO_STORAGE_DIR = str(session_storage_dir)
    cfg.AUDIO_MAX_SIZE_MB = 5120
    setattr(flask_app, 'app_config', cfg)

    setattr(flask_app, 'db', MagicMock())
    setattr(flask_app, 'audio_storage', AudioStorage(root=session_storage_dir))
    setattr(flask_app, 'video_runner', MagicMock())

    from auto_a11y.web.fluent import init_fluent
    init_fluent(flask_app)

    # Stub the projects endpoints the recordings routes link to via
    # url_for. The real blueprints aren't needed.
    def _list_projects_stub() -> str:
        return ''

    def _view_project_stub(project_id: str) -> str:
        _ = project_id
        return ''

    projects_stub = Blueprint('projects', __name__)
    projects_stub.add_url_rule(
        '/projects/', endpoint='list_projects', view_func=_list_projects_stub,
    )
    projects_stub.add_url_rule(
        '/projects/<project_id>',
        endpoint='view_project',
        view_func=_view_project_stub,
    )
    flask_app.register_blueprint(projects_stub)

    from auto_a11y.web.routes.recordings import recordings_bp
    flask_app.register_blueprint(recordings_bp, url_prefix='/recordings')

    return flask_app


@pytest.fixture
def mock_db(app: Flask) -> Iterator[MagicMock]:
    """Per-test fresh :class:`Database` mock attached to ``app.db``."""
    db = MagicMock()
    db.get_all_projects.return_value = []
    db.get_recording.return_value = None
    db.get_recording_by_recording_id.return_value = None

    project = MagicMock()
    project.id = 'p-1'
    project.name = 'Test Project'
    project.recording_ids = []
    db.get_project.return_value = project

    def _create_recording(recording: Any) -> str:
        from bson import ObjectId
        oid = ObjectId()
        recording.mongo_id = oid
        return str(oid)

    db.create_recording.side_effect = _create_recording
    db.update_recording.return_value = True
    db.update_project.return_value = True
    setattr(app, 'db', db)
    yield db


@pytest.fixture
def mock_video_runner(app: Flask) -> Iterator[MagicMock]:
    """Per-test fresh :class:`VideoRunner` mock attached to ``app.video_runner``.

    We type-spec it against the real class so the route's
    ``isinstance(runner, _VideoRunner)`` check in the worker would pass —
    not that the worker fires in these tests (we patch ``submit_task``).
    """
    from auto_a11y.audio.runner import VideoRunner
    runner = MagicMock(spec=VideoRunner)
    setattr(app, 'video_runner', runner)
    yield runner


@pytest.fixture
def client(
    app: Flask,
    mock_db: MagicMock,
    mock_video_runner: MagicMock,
) -> FlaskClient:
    """Flask test client with all per-test mocks installed."""
    _ = mock_db, mock_video_runner
    return app.test_client()
