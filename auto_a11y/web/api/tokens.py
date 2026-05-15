"""API-token helpers for /api/v1 Bearer auth.

Bridges :class:`auto_a11y.models.api_token.ApiToken` and the request
context. Three operations:

- :func:`mint_token` — create a fresh token for ``user_id`` and
  return both the raw value (returned **once** to the client) and the
  persisted :class:`ApiToken` document.
- :func:`resolve_token` — validate an incoming ``Authorization:
  Bearer <raw>`` header. Returns the :class:`AppUser` on success;
  ``None`` on every failure (unknown / expired / revoked) so the
  caller can't distinguish those cases. Stamps ``last_used_at``.
- :func:`revoke_token` — set the active token's ``revoked_at`` so it
  fails the next :func:`resolve_token`.

Token strings never appear in logs — only the SHA-256 hashes do.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from auto_a11y.models.api_token import (
    ApiToken, generate_raw_token, hash_token,
)

if TYPE_CHECKING:
    from auto_a11y.core.database import Database
    from auto_a11y.models.app_user import AppUser


def mint_token(
    database: Database,
    *,
    user_id: str,
    description: str | None = None,
) -> tuple[str, ApiToken]:
    """Create a fresh token for ``user_id``.

    Returns ``(raw_token, persisted_record)``. The raw token is the
    only time the client ever sees the secret value — store the
    hash, hand the raw back, and never look up by raw again.

    The persisted record is returned so the caller can include the
    ``id``, ``expires_at``, and ``description`` in the login response.
    """
    raw = generate_raw_token()
    token = ApiToken(
        user_id=user_id,
        token_hash=hash_token(raw),
        description=description,
    )
    database.create_api_token(token)
    return raw, token


def resolve_token(database: Database, raw_token: str) -> AppUser | None:
    """Validate ``raw_token`` and return its :class:`AppUser`.

    Returns ``None`` for every failure path (unknown, expired,
    revoked, user-deleted) so the caller emits a generic
    Unauthorized without leaking which reason fired.

    On success, stamps ``last_used_at`` so admin tooling can prune
    stale tokens. The bump is best-effort — a failed DB update is
    not treated as auth failure.
    """
    if not raw_token:
        return None
    token = database.get_api_token_by_hash(hash_token(raw_token))
    if token is None or not token.is_valid:
        return None
    user = database.get_app_user(token.user_id)
    if user is None or not user.is_active:
        return None

    token.last_used_at = datetime.now()
    try:
        database.update_api_token(token)
    except Exception:  # noqa: BLE001
        # last_used_at is observational — don't break login flow on
        # a transient mongo hiccup.
        pass
    return user


def revoke_token_by_hash(
    database: Database, raw_token: str,
) -> bool:
    """Revoke ``raw_token`` if it exists. Idempotent; returns whether a
    record was touched (False for unknown/already-revoked tokens)."""
    if not raw_token:
        return False
    token = database.get_api_token_by_hash(hash_token(raw_token))
    if token is None or token.revoked_at is not None:
        return False
    token.revoked_at = datetime.now()
    return database.update_api_token(token)


def extract_bearer(authorization_header: str | None) -> str | None:
    """Pull the raw token out of an ``Authorization: Bearer …`` header.

    Returns ``None`` when the header is missing, doesn't start with
    ``Bearer ``, or has an empty token. Case-sensitive on the scheme
    (per RFC 6750 §2.1 the scheme is case-insensitive in practice
    but ``Bearer`` is the canonical form and we match the bare-bones
    pattern intentionally to keep parsing tight).
    """
    if not authorization_header:
        return None
    parts = authorization_header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    raw = parts[1].strip()
    return raw or None


__all__ = [
    "extract_bearer",
    "mint_token",
    "resolve_token",
    "revoke_token_by_hash",
]
