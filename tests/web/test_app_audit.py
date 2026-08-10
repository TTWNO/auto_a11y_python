"""Regression tests for two ``auto_a11y/web/app.py`` audit findings.

Bug A — ``/set-language/<language>`` reported HTTP 200 ``success`` for an
unsupported language (e.g. ``de``) even though the language was silently
ignored. The route must now reject unsupported values with HTTP 400 and
an ``error`` status, while still applying + reporting success for the
supported set (``en`` / ``fr``).

Bug B — In Settings Recovery mode the ``force_recovery`` ``before_request``
interceptor 302-redirected *every* path (except ``/recovery`` / ``/static``)
to ``/recovery/``, including the ``/health`` endpoint a load balancer hits.
``/health`` must now be let through to its normal handler so monitors get a
real signal instead of an HTML redirect.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

import auto_a11y.web.app as appmod
from auto_a11y.core.preflight import Check, CheckOutcome, get_registry


def _fake_database(*_args: object, **_kwargs: object) -> MagicMock:
    """Stand in for :class:`auto_a11y.core.database.Database`.

    The audit routes never touch the DB, so a bare ``MagicMock`` is enough;
    this avoids needing a live MongoDB for ``create_app`` to complete.
    """
    return MagicMock()


class _Cfg:
    """Minimal config object ``create_app()`` needs for a passing app."""

    SECRET_KEY = "test-secret-deterministic-for-tests-only"
    DEBUG = False
    SESSION_COOKIE_SECURE = False
    CORS_ORIGINS = ""
    RATELIMIT_DEFAULT = "1000/minute"
    MONGODB_URI = "mongodb://127.0.0.1:9999"
    DATABASE_NAME = "auto_a11y_app_audit_test"
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
    AUDIO_STORAGE_DIR = "/tmp/audio"


@pytest.fixture
def passing_app(monkeypatch: pytest.MonkeyPatch) -> Flask:
    """Full app via :func:`create_app` with preflight stubbed all-OK.

    The heavy collaborators (``Database`` and the task / PDF / video
    runners) are mocked so no live MongoDB or background threads are
    needed; we only exercise the lightweight inline routes.
    """
    get_registry().replace_all([
        Check(name="ok", description="stub", run=lambda: CheckOutcome.ok()),
    ])
    monkeypatch.setattr(appmod, "Database", _fake_database)
    with patch("auto_a11y.core.migrate_groups.run_migration"), \
            patch("auto_a11y.core.task_runner.task_runner"), \
            patch("auto_a11y.testing.pdf_runner.PdfRunner"), \
            patch("auto_a11y.audio.runner.VideoRunner"):
        app = appmod.create_app(_Cfg())
    app.testing = True
    app.config["WTF_CSRF_ENABLED"] = False
    return app


# --- Bug A: /set-language validation ---------------------------------------


def test_set_language_supported_applies_and_reports_success(
    passing_app: Flask,
) -> None:
    """``/set-language/fr`` → 200 success and the session language updates."""
    client = passing_app.test_client()
    resp = client.get("/set-language/fr")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload is not None
    assert payload["status"] == "success"
    assert payload["language"] == "fr"
    with client.session_transaction() as sess:
        assert sess.get("language") == "fr"


def test_set_language_unsupported_rejected_and_session_untouched(
    passing_app: Flask,
) -> None:
    """``/set-language/de`` → 400 error and the session language is NOT set."""
    client = passing_app.test_client()
    # Seed a known-good language first so we can prove the bad request does
    # not clobber or alter the stored value.
    client.get("/set-language/en")
    resp = client.get("/set-language/de")
    assert resp.status_code == 400
    payload = resp.get_json()
    assert payload is not None
    assert payload["status"] == "error"
    with client.session_transaction() as sess:
        assert sess.get("language") == "en"


# --- Bug B: /health exempt from recovery redirect --------------------------


@pytest.fixture
def recovery_app(monkeypatch: pytest.MonkeyPatch) -> Flask:
    """Full app forced into Settings Recovery mode via a failing check."""
    get_registry().replace_all([
        Check(
            name="forced-fail",
            description="always fails to trigger recovery mode",
            run=lambda: CheckOutcome.failed("forced failure for test"),
        ),
    ])
    monkeypatch.setattr(appmod, "Database", _fake_database)
    app = appmod.create_app(_Cfg())
    app.testing = True
    app.config["WTF_CSRF_ENABLED"] = False
    return app


def test_recovery_redirects_normal_path(recovery_app: Flask) -> None:
    """A normal path is still 302'd to /recovery/ in recovery mode."""
    client = recovery_app.test_client()
    resp = client.get("/projects/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/recovery/")


def test_recovery_does_not_redirect_health(recovery_app: Flask) -> None:
    """``/health`` is NOT redirected to /recovery in recovery mode.

    A load balancer hitting ``/health`` must get a real signal, not a 302
    to an HTML recovery page. We accept any non-redirect status (the
    normal 200 health payload, or a 503 if recovery chooses to surface
    unhealthiness) — what matters is that it is not a 302 to /recovery.
    """
    client = recovery_app.test_client()
    resp = client.get("/health", follow_redirects=False)
    assert resp.status_code != 302
    location = resp.headers.get("Location")
    assert location is None or "/recovery" not in location
