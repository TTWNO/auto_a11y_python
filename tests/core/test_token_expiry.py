"""Timezone-correctness tests for security-token timestamps.

MongoDB stores datetimes as UTC and returns them either naive-UTC or
(under a ``tz_aware`` client) tz-aware. The token models therefore must:

1. Create ``created_at`` / ``expires_at`` as tz-aware UTC.
2. Compare expiry in a way that is correct whether ``expires_at`` comes
   back naive-UTC or tz-aware — and never raise ``TypeError`` from a
   naive-vs-aware comparison.

These tests exercise the model layer directly (no DB needed).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from auto_a11y.models.api_token import (
    TOKEN_LIFETIME_DAYS, ApiToken, hash_token,
)
from auto_a11y.models.share_token import ShareToken, TokenScope


# ---------------------------------------------------------------------------
# Creation produces tz-aware UTC timestamps
# ---------------------------------------------------------------------------


def test_api_token_default_timestamps_are_tz_aware_utc() -> None:
    token = ApiToken(user_id="u", token_hash=hash_token("a11y_aaa"))
    assert token.created_at.tzinfo is not None
    assert token.created_at.utcoffset() == timedelta(0)
    assert token.expires_at.tzinfo is not None
    assert token.expires_at.utcoffset() == timedelta(0)
    # Default lifetime is honoured.
    delta = token.expires_at - token.created_at
    assert abs(delta - timedelta(days=TOKEN_LIFETIME_DAYS)) < timedelta(minutes=1)


def test_share_token_default_created_at_is_tz_aware_utc() -> None:
    token = ShareToken(
        scope=TokenScope.PROJECT,
        scope_id="p",
        created_by="u",
        label="x",
    )
    assert token.created_at.tzinfo is not None
    assert token.created_at.utcoffset() == timedelta(0)


# ---------------------------------------------------------------------------
# ApiToken.is_valid normalises aware/naive expires_at
# ---------------------------------------------------------------------------


def test_api_token_aware_past_expiry_is_invalid() -> None:
    """tz-aware past expires_at → invalid, no TypeError."""
    token = ApiToken(
        user_id="u",
        token_hash=hash_token("a11y_aaa"),
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    assert not token.is_valid


def test_api_token_naive_future_expiry_is_valid() -> None:
    """naive (UTC) future expires_at → valid, no TypeError."""
    token = ApiToken(
        user_id="u",
        token_hash=hash_token("a11y_aaa"),
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
    )
    assert token.is_valid


def test_api_token_naive_past_expiry_is_invalid() -> None:
    """naive (UTC) past expires_at → invalid."""
    token = ApiToken(
        user_id="u",
        token_hash=hash_token("a11y_aaa"),
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1),
    )
    assert not token.is_valid


def test_api_token_aware_future_expiry_is_valid() -> None:
    token = ApiToken(
        user_id="u",
        token_hash=hash_token("a11y_aaa"),
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    assert token.is_valid


# ---------------------------------------------------------------------------
# ShareToken.is_expired / is_valid normalise aware/naive expires_at
# ---------------------------------------------------------------------------


def test_share_token_aware_past_expiry_is_expired() -> None:
    token = ShareToken(
        scope=TokenScope.PROJECT,
        scope_id="p",
        created_by="u",
        label="x",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    assert token.is_expired
    assert not token.is_valid


def test_share_token_naive_future_expiry_is_valid() -> None:
    token = ShareToken(
        scope=TokenScope.PROJECT,
        scope_id="p",
        created_by="u",
        label="x",
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
    )
    assert not token.is_expired
    assert token.is_valid


def test_share_token_naive_past_expiry_is_expired() -> None:
    token = ShareToken(
        scope=TokenScope.PROJECT,
        scope_id="p",
        created_by="u",
        label="x",
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1),
    )
    assert token.is_expired
    assert not token.is_valid


def test_share_token_none_expiry_never_expires() -> None:
    token = ShareToken(
        scope=TokenScope.PROJECT,
        scope_id="p",
        created_by="u",
        label="x",
        expires_at=None,
    )
    assert not token.is_expired
    assert token.is_valid
