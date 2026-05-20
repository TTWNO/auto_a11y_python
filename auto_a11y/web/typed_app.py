"""Typed accessors for Flask current_app dynamic attributes.

Flask's ``current_app`` is typed as ``Flask`` which does not include the
dynamic attributes we attach in ``create_app`` (``db``, ``app_config``,
``test_config``, ``scheduler``).  Accessing them directly causes hundreds
of mypy ``attr-defined`` errors.

This module provides thin typed wrappers so route code can import::

    from auto_a11y.web.typed_app import get_db, get_app_config

and get properly-typed values without needing ``# type: ignore`` markers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from flask import Flask, Response, current_app
from flask import redirect as _flask_redirect

if TYPE_CHECKING:
    from auto_a11y.audio.runner import VideoRunner
    from auto_a11y.audio.storage import AudioStorage
    from auto_a11y.core.database import Database
    from auto_a11y.core.job_manager import JobManager
    from auto_a11y.core.preflight import CheckResult
    from auto_a11y.core.scheduler import SchedulerService
    from auto_a11y.testing.pdf_runner import PdfRunner
    from auto_a11y.web.api.idempotency import IdempotencyStore
    from config import Config


def get_db() -> Database:
    """Return ``current_app.db`` (a :class:`Database` instance)."""
    return cast("Database", getattr(current_app, "db"))


def get_app_config() -> Config:
    """Return ``current_app.app_config`` (the project configuration object)."""
    return cast("Config", getattr(current_app, "app_config"))


def get_test_config() -> Any:
    """Return ``current_app.test_config``."""
    return getattr(current_app, "test_config")


def get_job_manager() -> JobManager:
    """Return ``current_app.job_manager`` (a :class:`JobManager` instance)."""
    return cast("JobManager", getattr(current_app, "job_manager"))


def get_scheduler() -> SchedulerService | None:
    """Return ``current_app.scheduler`` (may be ``None``)."""
    return cast("SchedulerService | None", getattr(current_app, "scheduler", None))


def get_pdf_runner() -> PdfRunner | None:
    """Return ``current_app.pdf_runner`` (a :class:`PdfRunner` instance, or ``None``).

    The runner is set during :func:`auto_a11y.web.app.create_app`. May be
    ``None`` in test contexts where the runner has not been wired up.
    """
    return cast("PdfRunner | None", getattr(current_app, "pdf_runner", None))


def get_audio_storage() -> AudioStorage | None:
    """Return ``current_app.audio_storage`` (an :class:`AudioStorage` or ``None``).

    Set during :func:`auto_a11y.web.app.create_app`. May be ``None`` in
    test contexts where the audio surface has not been wired up.
    """
    return cast("AudioStorage | None", getattr(current_app, "audio_storage", None))


def get_video_runner() -> VideoRunner | None:
    """Return ``current_app.video_runner`` (a :class:`VideoRunner` or ``None``).

    Set during :func:`auto_a11y.web.app.create_app`. May be ``None`` in
    test contexts where the audio surface has not been wired up.
    """
    return cast("VideoRunner | None", getattr(current_app, "video_runner", None))


def get_preflight_failures() -> list[CheckResult]:
    """Return ``current_app.preflight_failures`` (set by recovery-mode startup).

    Empty list when preflight passed (the attribute is not set in that
    case). Used by :mod:`auto_a11y.web.routes.recovery` to render the
    list of failed checks on the recovery page.
    """
    from auto_a11y.core.preflight import CheckResult as _CheckResult
    raw: Any = getattr(current_app, "preflight_failures", None)
    if not isinstance(raw, list):
        return []
    raw_list = cast("list[object]", raw)
    return [f for f in raw_list if isinstance(f, _CheckResult)]


def get_idempotency_store() -> IdempotencyStore:
    """Return the REST API idempotency store.

    Backed by the ``idempotency_keys`` Mongo collection on the active
    ``Database`` (TTL index installed at app startup, see
    :mod:`auto_a11y.web.api.idempotency`).
    """
    from auto_a11y.web.api.idempotency import IdempotencyStore as _Store
    return _Store(get_db().idempotency_keys)


def get_flask_app() -> Flask:
    """Return the real Flask app object (unwrapped proxy)."""
    app: Any = current_app
    result: Flask = getattr(app, '_get_current_object')()
    return result


def redirect(location: str, code: int = 302) -> Response:
    """Typed wrapper around :func:`flask.redirect`.

    Flask's ``redirect()`` is annotated as returning
    ``werkzeug.wrappers.Response`` (the base class), but at runtime --
    inside an application context -- it returns ``flask.Response`` via
    ``current_app.redirect()``.  This wrapper simply casts the return
    value so callers can use ``flask.Response`` in their annotations
    without mypy complaining.
    """
    return cast(Response, _flask_redirect(location, code))
