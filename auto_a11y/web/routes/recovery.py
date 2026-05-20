"""Settings Recovery blueprint.

Phase 10 of the audioA11y integration. When :func:`PreflightRegistry.run_all`
reports any failure at app startup, the Flask app factory:

1. Stores the failed :class:`CheckResult` list under
   ``app.config["PREFLIGHT_FAILURES"]``.
2. Registers *only* this blueprint (so the user never lands on a route
   that would 500 because, e.g., MongoDB is unreachable).
3. Registers a ``before_request`` middleware that 302s every non-static,
   non-``/recovery/`` URL to ``/recovery/``.

The blueprint exposes:

- ``GET  /recovery/``                 — the recovery page itself.
- ``POST /recovery/test/ffmpeg``      — re-run the ffmpeg check with the
  path the form supplied (without persisting it).
- ``POST /recovery/test/ffprobe``     — same, for ffprobe.
- ``POST /recovery/test/mongo``       — re-run the Mongo ping against a
  proposed URI without persisting it.
- ``POST /recovery/test/deepgram``    — verify a proposed Deepgram key
  is accepted by the SDK constructor (no API call).
- ``POST /recovery/test/anthropic``   — same, for Anthropic.
- ``POST /recovery/save``             — persist the form fields to the
  user-settings JSON file. The page then asks the user to quit and
  reopen because we don't restart Flask in-process.

The test endpoints intentionally do not write to disk so the user can
poke values until something works before committing to a save.
"""
from __future__ import annotations

import logging
import os

from flask import (
    Blueprint, Response, jsonify, render_template, request,
)

from auto_a11y.core import preflight_registrations, user_settings
from auto_a11y.core.user_settings import UserSettings
from auto_a11y.web.typed_app import get_preflight_failures

logger = logging.getLogger(__name__)

recovery_bp = Blueprint("recovery", __name__, url_prefix="/recovery")


def _form_value(name: str) -> str:
    """Return ``request.form[name]`` stripped, or empty string when absent."""
    raw = request.form.get(name, "")
    # Flask's MultiDict.get returns ``str`` for present form values (the
    # default is the second arg, also str here). Strip whitespace so a
    # user pasting a key with a trailing newline doesn't get rejected.
    return raw.strip()


def _with_env_override(env_var: str, value: str) -> str | None:
    """Set ``os.environ[env_var]`` to ``value`` and return the previous value.

    The test endpoints reuse the production check functions (which read
    from ``os.environ``) so we have to temporarily install the proposed
    value before calling them. The caller restores the previous value
    via :func:`_restore_env_override`.
    """
    previous = os.environ.get(env_var)
    os.environ[env_var] = value
    return previous


def _restore_env_override(env_var: str, previous: str | None) -> None:
    """Restore the env var to ``previous`` (deleting it if previously unset)."""
    if previous is None:
        os.environ.pop(env_var, None)
    else:
        os.environ[env_var] = previous


@recovery_bp.route("/", methods=["GET"])
def index() -> str:
    """Render the recovery page listing failed checks + edit form."""
    failures = get_preflight_failures()
    settings = user_settings.read()
    return render_template(
        "recovery/index.html",
        failures=failures,
        settings=settings,
        settings_path=str(user_settings.settings_file_path()),
    )


@recovery_bp.route("/test/ffmpeg", methods=["POST"])
def test_ffmpeg() -> Response:
    """Probe a candidate ffmpeg path (no save).

    Accepts ``ffmpeg_path`` from the form. If non-empty, validates that
    the path exists and is executable. If empty, falls back to ``shutil.which``.
    """
    return _test_binary_path(form_field="ffmpeg_path", default_name="ffmpeg")


@recovery_bp.route("/test/ffprobe", methods=["POST"])
def test_ffprobe() -> Response:
    """Probe a candidate ffprobe path (no save)."""
    return _test_binary_path(form_field="ffprobe_path", default_name="ffprobe")


def _test_binary_path(*, form_field: str, default_name: str) -> Response:
    """Shared body for the ffmpeg / ffprobe test endpoints."""
    candidate = _form_value(form_field)
    if candidate:
        if not os.path.isfile(candidate):
            return jsonify({
                "ok": False,
                "message": f"No file at {candidate!s}.",
            })
        if not os.access(candidate, os.X_OK):
            return jsonify({
                "ok": False,
                "message": f"{candidate!s} is not executable.",
            })
        return jsonify({"ok": True, "message": candidate})
    # Empty form value — fall back to PATH lookup.
    import shutil
    found = shutil.which(default_name)
    if found:
        return jsonify({"ok": True, "message": found})
    return jsonify({
        "ok": False,
        "message": f"{default_name} not found on PATH.",
    })


@recovery_bp.route("/test/mongo", methods=["POST"])
def test_mongo() -> Response:
    """Verify a candidate Mongo URI without saving it."""
    candidate = _form_value("mongodb_uri")
    if not candidate:
        return jsonify({
            "ok": False,
            "message": "Provide a MongoDB URI.",
        })
    previous = _with_env_override("MONGODB_URI", candidate)
    try:
        outcome = preflight_registrations.run_mongo_check()
    finally:
        _restore_env_override("MONGODB_URI", previous)
    return jsonify({
        "ok": outcome.ok_,
        "message": outcome.remediation or "Connection succeeded.",
    })


@recovery_bp.route("/test/deepgram", methods=["POST"])
def test_deepgram() -> Response:
    """Verify a candidate Deepgram key without saving it.

    No API call is made — see :func:`_deepgram_check` for rationale.
    """
    candidate = _form_value("deepgram_api_key")
    if not candidate:
        return jsonify({
            "ok": False,
            "message": "Provide a Deepgram API key.",
        })
    previous = _with_env_override("DEEPGRAM_API_KEY", candidate)
    try:
        outcome = preflight_registrations.run_deepgram_check()
    finally:
        _restore_env_override("DEEPGRAM_API_KEY", previous)
    return jsonify({
        "ok": outcome.ok_,
        "message": outcome.remediation or "Key accepted by SDK.",
    })


@recovery_bp.route("/test/anthropic", methods=["POST"])
def test_anthropic() -> Response:
    """Verify a candidate Anthropic key without saving it (no API call)."""
    candidate = _form_value("anthropic_api_key")
    if not candidate:
        return jsonify({
            "ok": False,
            "message": "Provide an Anthropic API key.",
        })
    previous = _with_env_override("ANTHROPIC_API_KEY", candidate)
    try:
        outcome = preflight_registrations.run_anthropic_check()
    finally:
        _restore_env_override("ANTHROPIC_API_KEY", previous)
    return jsonify({
        "ok": outcome.ok_,
        "message": outcome.remediation or "Key accepted by SDK.",
    })


def _empty_to_none(value: str) -> str | None:
    """Map empty/whitespace strings to ``None`` for ``UserSettings``."""
    if not value:
        return None
    return value


@recovery_bp.route("/save", methods=["POST"])
def save() -> Response:
    """Persist the form fields to the user-settings JSON file."""
    settings = UserSettings(
        mongodb_uri=_empty_to_none(_form_value("mongodb_uri")),
        deepgram_api_key=_empty_to_none(_form_value("deepgram_api_key")),
        anthropic_api_key=_empty_to_none(_form_value("anthropic_api_key")),
        ffmpeg_path=_empty_to_none(_form_value("ffmpeg_path")),
        ffprobe_path=_empty_to_none(_form_value("ffprobe_path")),
        huggingface_token=_empty_to_none(_form_value("huggingface_token")),
    )
    try:
        user_settings.write(settings)
    except OSError as exc:
        logger.exception("Failed to write user settings file")
        return jsonify({
            "ok": False,
            "message": f"Failed to write settings file: {exc}",
        })
    return jsonify({
        "ok": True,
        "message": (
            "Settings saved. Quit and reopen the application to apply the "
            "new configuration."
        ),
        "path": str(user_settings.settings_file_path()),
    })
