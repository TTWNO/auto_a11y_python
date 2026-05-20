"""Tests for the Settings Recovery blueprint (Phase 10).

Builds a Flask app via :func:`create_app` with an intentionally broken
``MONGODB_URI`` so preflight fails; verifies:

1. Every non-``/recovery/`` path 302s to ``/recovery/``.
2. The recovery page itself renders the failed Mongo check.
3. ``POST /recovery/test/mongo`` returns ``{ok: false}`` for a bad URI.
4. ``POST /recovery/test/mongo`` returns ``{ok: true}`` for a known-good URI
   (we monkey-patch the Mongo check function so no real Mongo is needed).
5. ``POST /recovery/save`` writes the JSON settings file and reports success.

The user-settings file path is monkey-patched onto ``tmp_path`` so the
real ``~/.config/auto_a11y/settings.json`` is never touched.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from auto_a11y.core import user_settings
from auto_a11y.core.preflight import CheckOutcome


@pytest.fixture
def recovery_settings_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Redirect ``user_settings.settings_file_path()`` to a temp dir."""
    target = tmp_path / "settings.json"
    monkeypatch.setattr(
        user_settings, "settings_file_path", lambda: target
    )
    return target


@pytest.fixture
def broken_mongo_config() -> Any:
    """Minimal config object create_app() needs, with a broken Mongo URI."""
    class _Cfg:
        SECRET_KEY = "test-secret"
        DEBUG = False
        SESSION_COOKIE_SECURE = False
        CORS_ORIGINS = ""
        RATELIMIT_DEFAULT = "1000/minute"
        # Port 9999 has no Mongo listening on it.
        MONGODB_URI = "mongodb://127.0.0.1:9999"
        DATABASE_NAME = "auto_a11y_recovery_test"
        SHOW_ERROR_CODES = False
        MICROSOFT_SSO_ENABLED = False
        GOOGLE_SSO_ENABLED = False
        SMTP_ENABLED = False
        SCHEDULER_ENABLED = False
        DESKTOP_MODE = False
        AUTH_ENABLED = False
        PDF_STORAGE_DIR = "/tmp/pdf"
        PDF_AUDIT_MAX_PARALLEL = 1
        PDF_MAX_SIZE_MB = 100
        GHOSTSCRIPT_PATH = None
        AUDIO_STORAGE_DIR = "/tmp/audio"
    return _Cfg()


@pytest.fixture
def recovery_app(
    broken_mongo_config: Any,
    recovery_settings_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Flask:
    """Build the Flask app via :func:`create_app` in recovery mode.

    All Deepgram / Anthropic / ffmpeg checks are monkey-patched to OK so
    the only remaining failure is the Mongo one; that keeps the test
    focused on the recovery routing rather than on which check fired.
    """
    _ = recovery_settings_path
    # The preflight Mongo check reads MONGODB_URI from os.environ — the
    # broken URI on the config dataclass alone isn't enough when the test
    # process inherits a working MONGODB_URI from its shell. Force the
    # check to see the broken value too.
    monkeypatch.setenv("MONGODB_URI", broken_mongo_config.MONGODB_URI)
    from auto_a11y.core import preflight_registrations
    from auto_a11y.audio import ffmpeg as audio_ffmpeg

    # Force-replace the registered checks with a deterministic set: the
    # real Mongo check (so it fails against the broken URI on port 9999),
    # plus stubbed ok-checks for everything else. This keeps the test
    # focused on the recovery flow rather than on which check fired.
    from auto_a11y.core.preflight import Check, get_registry
    _ = audio_ffmpeg  # imported for side-effect (registers ffmpeg checks)
    reg = get_registry()
    reg.replace_all([
        Check(
            name="mongodb",
            description=(
                "MongoDB connection (MONGODB_URI must accept admin ping)."
            ),
            run=preflight_registrations.run_mongo_check,
        ),
        Check(
            name="deepgram",
            description="Deepgram API key.",
            run=lambda: CheckOutcome.ok(),
        ),
        Check(
            name="anthropic",
            description="Anthropic API key.",
            run=lambda: CheckOutcome.ok(),
        ),
        Check(
            name="ffmpeg",
            description="ffmpeg binary on PATH.",
            run=lambda: CheckOutcome.ok(),
        ),
        Check(
            name="ffprobe",
            description="ffprobe binary on PATH.",
            run=lambda: CheckOutcome.ok(),
        ),
    ])

    from auto_a11y.web.app import create_app
    app = create_app(broken_mongo_config)
    app.testing = True
    # The settings-recovery endpoints are exempt from CSRF in production
    # (see ``_build_recovery_only_app``), but the test client posts raw
    # form data without round-tripping a token. Disable CSRF wholesale
    # here so we exercise the route logic, not Flask-WTF behaviour.
    app.config["WTF_CSRF_ENABLED"] = False
    return app


def test_non_recovery_path_redirects_to_recovery(recovery_app: Flask) -> None:
    """Every URL outside /recovery and /static 302s to /recovery/."""
    client = recovery_app.test_client()
    resp = client.get("/projects/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/recovery/")


def test_static_path_is_not_redirected(recovery_app: Flask) -> None:
    """``/static/...`` requests aren't redirected (they 404 instead)."""
    client = recovery_app.test_client()
    resp = client.get("/static/css/style.css", follow_redirects=False)
    # Whether or not the file exists, the path is not 302'd.
    assert resp.status_code != 302


def test_recovery_index_lists_failed_mongo_check(recovery_app: Flask) -> None:
    """GET /recovery/ → 200 with the failed Mongo check listed."""
    client = recovery_app.test_client()
    resp = client.get("/recovery/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "MongoDB" in body
    assert "Settings Recovery" in body


def test_test_mongo_endpoint_rejects_bad_uri(recovery_app: Flask) -> None:
    """POST /recovery/test/mongo with a bad URI → JSON {ok: false}."""
    client = recovery_app.test_client()
    resp = client.post(
        "/recovery/test/mongo",
        data={"mongodb_uri": "mongodb://127.0.0.1:9999"},
    )
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload is not None
    assert payload["ok"] is False
    assert "could not reach mongodb" in payload["message"].lower()


def test_test_mongo_endpoint_accepts_good_uri(
    recovery_app: Flask,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /recovery/test/mongo with a working URI → JSON {ok: true}.

    We stub the underlying check so the test doesn't depend on a real
    running Mongo — the route's job is to invoke the check and serialise
    its outcome, not to test pymongo itself.
    """
    from auto_a11y.core import preflight_registrations
    monkeypatch.setattr(
        preflight_registrations, "run_mongo_check",
        lambda: CheckOutcome.ok(),
    )
    client = recovery_app.test_client()
    resp = client.post(
        "/recovery/test/mongo",
        data={"mongodb_uri": "mongodb://localhost:27017/"},
    )
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload is not None
    assert payload["ok"] is True


def test_test_mongo_endpoint_blank_uri_returns_error(
    recovery_app: Flask,
) -> None:
    """Empty form value → ``ok=False`` with a friendly message."""
    client = recovery_app.test_client()
    resp = client.post("/recovery/test/mongo", data={"mongodb_uri": ""})
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload is not None
    assert payload["ok"] is False


def test_save_writes_settings_file(
    recovery_app: Flask,
    recovery_settings_path: Path,
) -> None:
    """POST /recovery/save persists the form fields to the JSON file."""
    client = recovery_app.test_client()
    resp = client.post(
        "/recovery/save",
        data={
            "mongodb_uri": "mongodb://localhost:27017/",
            "anthropic_api_key": "sk-ant-test",
            "deepgram_api_key": "dg-test",
            "ffmpeg_path": "",
            "ffprobe_path": "",
            "huggingface_token": "",
        },
    )
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload is not None
    assert payload["ok"] is True
    assert "quit and reopen" in payload["message"].lower()
    assert recovery_settings_path.is_file()
    saved = json.loads(recovery_settings_path.read_text(encoding="utf-8"))
    assert saved["mongodb_uri"] == "mongodb://localhost:27017/"
    assert saved["anthropic_api_key"] == "sk-ant-test"
    assert saved["deepgram_api_key"] == "dg-test"
    # Blank fields persist as None to indicate "no override".
    assert saved["ffmpeg_path"] is None
    assert saved["huggingface_token"] is None


def test_user_settings_round_trip(
    recovery_settings_path: Path,
) -> None:
    """write() then read() yields the same UserSettings dataclass."""
    _ = recovery_settings_path
    settings = user_settings.UserSettings(
        mongodb_uri="mongodb://example/",
        anthropic_api_key="sk-ant-x",
        deepgram_api_key=None,
        ffmpeg_path="/usr/local/bin/ffmpeg",
        ffprobe_path=None,
        huggingface_token=None,
    )
    user_settings.write(settings)
    loaded = user_settings.read()
    assert loaded == settings


def test_apply_to_environment_overlays_env_vars(
    recovery_settings_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-None fields land on os.environ; None fields don't clobber existing vars."""
    _ = recovery_settings_path
    monkeypatch.setenv("MONGODB_URI", "preexisting")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = user_settings.UserSettings(
        mongodb_uri="mongodb://from-file/",
        anthropic_api_key=None,
    )
    user_settings.apply_to_environment(settings)
    import os
    assert os.environ["MONGODB_URI"] == "mongodb://from-file/"
    # None field did not create or clobber.
    assert "ANTHROPIC_API_KEY" not in os.environ
