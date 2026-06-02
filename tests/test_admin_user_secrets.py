"""Unit tests for the admin Settings "Application secrets" merge/view helpers.

These cover the blank-handling rules that protect stored secrets:
- a blank secret field keeps the stored value (don't wipe a key on every edit),
- an explicit ``<field>_clear=on`` removes it,
- a blank plain field (Mongo URI / ffmpeg paths) clears the override.

Pure functions — no Flask app, DB, or filesystem required.
"""
from __future__ import annotations

from auto_a11y.core.user_settings import UserSettings
from auto_a11y.web.routes.admin_settings import (
    apply_user_secret_form,
    build_user_secrets_view,
)


def _existing() -> UserSettings:
    return UserSettings(
        mongodb_uri="mongodb://localhost:27017/",
        deepgram_api_key="dg-existing",
        anthropic_api_key="an-existing",
        ffmpeg_path="/usr/bin/ffmpeg",
        ffprobe_path=None,
        huggingface_token="hf-existing",
    )


def test_blank_secret_keeps_existing_value() -> None:
    """Submitting the form with the key field blank must not wipe the key."""
    result = apply_user_secret_form(_existing(), {"mongodb_uri": "mongodb://localhost:27017/"})
    assert result.anthropic_api_key == "an-existing"
    assert result.deepgram_api_key == "dg-existing"
    assert result.huggingface_token == "hf-existing"


def test_non_blank_secret_replaces_value() -> None:
    result = apply_user_secret_form(_existing(), {"anthropic_api_key": "  an-new  "})
    assert result.anthropic_api_key == "an-new"  # trimmed
    assert result.deepgram_api_key == "dg-existing"  # untouched


def test_clear_flag_removes_secret() -> None:
    result = apply_user_secret_form(
        _existing(),
        {"deepgram_api_key": "", "deepgram_api_key_clear": "on"},
    )
    assert result.deepgram_api_key is None
    assert result.anthropic_api_key == "an-existing"


def test_clear_flag_wins_over_submitted_value() -> None:
    """If both a new value and the clear flag arrive, clearing takes priority."""
    result = apply_user_secret_form(
        _existing(),
        {"anthropic_api_key": "ignored", "anthropic_api_key_clear": "on"},
    )
    assert result.anthropic_api_key is None


def test_blank_plain_field_clears_override() -> None:
    result = apply_user_secret_form(_existing(), {"mongodb_uri": "   "})
    assert result.mongodb_uri is None


def test_plain_field_value_is_trimmed() -> None:
    result = apply_user_secret_form(_existing(), {"ffmpeg_path": "  /opt/ffmpeg  "})
    assert result.ffmpeg_path == "/opt/ffmpeg"


def test_view_reports_set_flags_without_echoing_secrets() -> None:
    view = build_user_secrets_view(_existing())
    # Plain values are shown so they can be edited in place.
    assert view["mongodb_uri"] == "mongodb://localhost:27017/"
    assert view["ffmpeg_path"] == "/usr/bin/ffmpeg"
    assert view["ffprobe_path"] == ""  # None -> empty string for the input
    # Secrets are reported only as booleans; the values never reach the page.
    assert view["anthropic_set"] is True
    assert view["deepgram_set"] is True
    assert view["huggingface_set"] is True
    assert "an-existing" not in view.values()
    assert "dg-existing" not in view.values()


def test_view_unset_secrets() -> None:
    view = build_user_secrets_view(UserSettings())
    assert view["anthropic_set"] is False
    assert view["deepgram_set"] is False
    assert view["huggingface_set"] is False
    assert view["mongodb_uri"] == ""
