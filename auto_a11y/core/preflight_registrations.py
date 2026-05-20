"""Register MongoDB / Deepgram / Anthropic preflight checks at import.

ffmpeg + ffprobe are registered by :mod:`auto_a11y.audio.ffmpeg`
(Phase 1). This module covers the remaining checks the Settings
Recovery blueprint (Phase 10) surfaces:

- MongoDB: tries ``MongoClient.admin.command("ping")`` against the
  effective ``MONGODB_URI`` with a tight ``serverSelectionTimeoutMS``
  so startup doesn't block on a typo for the production 30-second
  default.
- Deepgram / Anthropic: confirm the env var is set and that the SDK
  client constructor accepts it. Deliberately *do not* call the API —
  that would cost money on every restart.

The module is imported from :func:`auto_a11y.web.app.create_app` so
its module-level :func:`get_registry().register(...)` calls fire
exactly when the Flask app is built, never before. Registration is
idempotent (see :meth:`PreflightRegistry.register`).
"""
from __future__ import annotations

import logging
import os

from auto_a11y.core.preflight import Check, CheckOutcome, get_registry

logger = logging.getLogger(__name__)

# Tight ping timeout so a typo'd URI surfaces in <2s instead of the
# pymongo default of 30s. We also pass it as ``connectTimeoutMS`` because
# ``serverSelectionTimeoutMS`` only covers SDAM, not the TCP handshake.
_MONGO_PING_TIMEOUT_MS = 1500


def run_mongo_check() -> CheckOutcome:
    """Verify the effective ``MONGODB_URI`` accepts ``admin.command('ping')``."""
    uri = os.environ.get("MONGODB_URI", "").strip()
    if not uri:
        return CheckOutcome.failed(
            "MONGODB_URI is not set. Provide a MongoDB connection string"
            + " (e.g. mongodb://localhost:27017/) in the user settings file"
            + " or environment."
        )
    # Lazy import — keeps preflight import cheap and avoids pulling pymongo
    # into modules that only need the user_settings layer.
    from typing import Any
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError
    try:
        client: MongoClient[dict[str, Any]] = MongoClient(
            uri,
            serverSelectionTimeoutMS=_MONGO_PING_TIMEOUT_MS,
            connectTimeoutMS=_MONGO_PING_TIMEOUT_MS,
            socketTimeoutMS=_MONGO_PING_TIMEOUT_MS,
        )
        try:
            client.admin.command("ping")
        finally:
            client.close()
    except PyMongoError as exc:
        return CheckOutcome.failed(
            f"Could not reach MongoDB at {uri!s}: {exc}. Check the URI and"
            + " ensure the server is running."
        )
    return CheckOutcome.ok()


def run_deepgram_check() -> CheckOutcome:
    """Verify ``DEEPGRAM_API_KEY`` is set and the SDK accepts it.

    The SDK accepts any non-empty string at construction time, so this
    really just guards against the env var being unset/blank — but it
    also gives us a defined failure mode if the SDK ever starts doing
    eager validation.
    """
    key = os.environ.get("DEEPGRAM_API_KEY", "").strip()
    if not key:
        return CheckOutcome.failed(
            "DEEPGRAM_API_KEY is not set. Add your Deepgram API key in"
            + " the user settings file."
        )
    try:
        from deepgram import DeepgramClient
        DeepgramClient(api_key=key)
    except Exception as exc:  # pragma: no cover — defensive
        return CheckOutcome.failed(
            f"Deepgram SDK rejected the configured key: {exc}"
        )
    return CheckOutcome.ok()


def run_anthropic_check() -> CheckOutcome:
    """Verify ``ANTHROPIC_API_KEY`` is set and the SDK accepts it.

    As with Deepgram, no API call is made — that would cost money on
    every startup. The check is `env var present + constructor doesn't
    raise`.
    """
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return CheckOutcome.failed(
            "ANTHROPIC_API_KEY is not set. Add your Anthropic API key in"
            + " the user settings file."
        )
    try:
        from anthropic import Anthropic
        Anthropic(api_key=key)
    except Exception as exc:  # pragma: no cover — defensive
        return CheckOutcome.failed(
            f"Anthropic SDK rejected the configured key: {exc}"
        )
    return CheckOutcome.ok()


def register_default_checks() -> None:
    """Register the Mongo / Deepgram / Anthropic checks.

    Idempotent: :meth:`PreflightRegistry.register` skips duplicates by
    ``check.name``. Importing :mod:`auto_a11y.audio.ffmpeg` (done by
    :func:`auto_a11y.web.app.create_app`) registers the ffmpeg /
    ffprobe checks separately.
    """
    registry = get_registry()
    registry.register(Check(
        name="mongodb",
        description="MongoDB connection (MONGODB_URI must accept admin ping).",
        run=run_mongo_check,
    ))
    registry.register(Check(
        name="deepgram",
        description="Deepgram API key (DEEPGRAM_API_KEY required for transcription).",
        run=run_deepgram_check,
    ))
    registry.register(Check(
        name="anthropic",
        description="Anthropic API key (ANTHROPIC_API_KEY required for analysis).",
        run=run_anthropic_check,
    ))


# Register on import. App startup imports this module from create_app(),
# which means every Flask process picks up the checks; module re-imports
# under pytest are harmless because register() dedupes on name.
register_default_checks()
