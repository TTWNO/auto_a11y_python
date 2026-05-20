"""Cross-platform user-settings file for desktop deployments.

Phase 10 of the audioA11y integration. Stores the small set of
secrets/paths the user needs to configure (Mongo URI, Deepgram /
Anthropic API keys, optional ffmpeg/ffprobe overrides, optional
HuggingFace token) in a JSON file under the OS-appropriate per-user
config directory:

- Linux / macOS: ``$XDG_CONFIG_HOME/auto_a11y/settings.json`` (or
  ``~/.config/auto_a11y/settings.json`` when ``XDG_CONFIG_HOME`` is unset).
- Windows: ``%APPDATA%\\auto_a11y\\settings.json`` (falls back to
  ``~/AppData/Roaming/auto_a11y/settings.json`` when ``APPDATA`` is unset).

App startup calls :func:`apply_to_environment` *before* the
:mod:`auto_a11y.core.preflight` registry runs, so user-saved values are
visible to :class:`AudioConfig.from_env`, the Mongo URI check, etc.

The Settings Recovery blueprint (``auto_a11y.web.routes.recovery``)
uses :func:`read` / :func:`write` to surface and persist the values
when preflight fails.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UserSettings:
    """User-overridable secrets/paths read at app startup.

    All fields are optional. A field set to ``None`` means "no override";
    the matching ``os.environ`` value (or the binary on ``PATH`` for the
    ``ffmpeg_path`` / ``ffprobe_path`` fields) applies instead.
    """

    mongodb_uri: str | None = None
    deepgram_api_key: str | None = None
    anthropic_api_key: str | None = None
    ffmpeg_path: str | None = None
    ffprobe_path: str | None = None
    huggingface_token: str | None = None


# Environment-variable names the dataclass fields overlay. Kept here (not
# duplicated at the call sites) so adding a new field is a one-line change.
_ENV_VAR_FOR_FIELD: dict[str, str] = {
    "mongodb_uri": "MONGODB_URI",
    "deepgram_api_key": "DEEPGRAM_API_KEY",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "ffmpeg_path": "FFMPEG_PATH",
    "ffprobe_path": "FFPROBE_PATH",
    "huggingface_token": "HF_TOKEN",
}


def _config_root() -> Path:
    """Return the OS-appropriate per-user config directory for auto_a11y."""
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "auto_a11y"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "auto_a11y"


def settings_file_path() -> Path:
    """OS-appropriate path to the user-settings JSON file.

    Does not create the file or any parent directory.
    """
    return _config_root() / "settings.json"


def read() -> UserSettings:
    """Read the user-settings file. Return all-``None`` on any failure.

    A missing file, an unreadable file, or invalid JSON all collapse to
    the default :class:`UserSettings` so that startup doesn't crash on a
    bad settings file — preflight failures will surface the relevant
    remediation instead.
    """
    path = settings_file_path()
    if not path.is_file():
        return UserSettings()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read settings file %s: %s", path, exc)
        return UserSettings()
    try:
        parsed: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Settings file %s is not valid JSON: %s", path, exc)
        return UserSettings()
    if not isinstance(parsed, dict):
        logger.warning("Settings file %s root is not a JSON object", path)
        return UserSettings()
    # ``parsed`` is typed as ``Any`` here because :func:`json.loads` returns
    # ``Any``; build a fresh ``dict[str, str | None]`` from the keys we
    # care about so the remainder of the function operates on a fully-typed
    # value rather than a partially-inferred ``dict[Unknown, Unknown]``.
    kwargs: dict[str, str | None] = {}
    for field in fields(UserSettings):
        candidate: object = _lookup_string(parsed, field.name)
        if isinstance(candidate, str) and candidate:
            kwargs[field.name] = candidate
        else:
            kwargs[field.name] = None
    return UserSettings(**kwargs)


def _lookup_string(mapping: Any, key: str) -> object:
    """Return ``mapping[key]`` (an ``object``), or ``None`` when absent.

    Isolated helper so the post-:func:`json.loads` ``Any`` value is
    flattened to ``object`` in exactly one spot rather than infecting
    the calling function's type with ``Unknown``.
    """
    if not isinstance(mapping, dict):
        return None
    typed_mapping = cast("dict[str, object]", mapping)
    return typed_mapping.get(key)


def write(settings: UserSettings) -> None:
    """Write the user-settings file atomically.

    Creates the parent directory (and any intermediate parents) if it
    does not already exist. The write is tempfile + ``os.replace`` so a
    crash or power loss mid-write cannot leave a half-formed JSON file
    behind.
    """
    path = settings_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, str | None] = asdict(settings)
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    # Tempfile in the same directory so ``os.replace`` is atomic on POSIX
    # and Windows (cross-device rename otherwise raises ``OSError``).
    fd, tmp_name = tempfile.mkstemp(
        prefix=".settings-", suffix=".json.tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(serialized)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        # Best-effort cleanup of the orphan tempfile on failure.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    # Best-effort fsync of the parent directory for rename durability;
    # Windows disallows directory fsync so swallow the resulting OSError.
    try:
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


def apply_to_environment(settings: UserSettings) -> None:
    """Overlay non-``None`` fields onto :data:`os.environ`.

    Called at app startup *before* :func:`PreflightRegistry.run_all` so
    the user-saved Mongo URI / API keys are visible to the checks. Empty
    or ``None`` fields leave the existing env var (if any) untouched.
    """
    for field_name, env_var in _ENV_VAR_FOR_FIELD.items():
        value = getattr(settings, field_name)
        if isinstance(value, str) and value:
            os.environ[env_var] = value
