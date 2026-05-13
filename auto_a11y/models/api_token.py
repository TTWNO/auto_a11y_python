"""API token model for Bearer-auth on /api/v1.

The legacy auth flow is session-based (Flask-Login cookie). SPAs and
CLI clients need a Bearer-auth alternative — :class:`ApiToken` is
that.

Lifecycle:

1. ``POST /api/v1/auth/login`` (email+password or via SSO callback)
   mints a fresh token. The raw token string is returned **once** in
   the response body; only the SHA-256 hash is persisted.
2. Subsequent requests send ``Authorization: Bearer <raw_token>``.
   The matching middleware (``require_authenticated``) hashes the
   incoming raw value and looks up the document by ``token_hash``.
3. ``POST /api/v1/auth/logout`` revokes the active token by setting
   ``revoked_at``. Tokens past ``expires_at`` are also rejected at
   validation time.

The hash-only storage means a database breach can't be used to log
in as a user — the attacker would also need the raw token text the
user holds in memory / their HTTP client.

Token format: 32 bytes from :func:`secrets.token_urlsafe` (~43 base64
characters), prefixed with ``a11y_`` so the type is recognisable in
logs and grep without revealing the secret.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from bson import ObjectId


TOKEN_PREFIX = "a11y_"
TOKEN_LIFETIME_DAYS = 30


def hash_token(raw: str) -> str:
    """SHA-256 of the raw token string, hex-encoded.

    A single round of SHA-256 is fine for API tokens — the input is
    high-entropy (32 random bytes), so we don't need the
    work-factor protection bcrypt/scrypt give to passwords.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_raw_token() -> str:
    """Return a fresh URL-safe token string prefixed with ``a11y_``."""
    return f"{TOKEN_PREFIX}{secrets.token_urlsafe(32)}"


@dataclass
class ApiToken:
    """One Bearer-auth credential for one user.

    Stored shape (Mongo collection ``api_tokens``):

    - ``user_id``: the :class:`AppUser` id this token authenticates as.
    - ``token_hash``: SHA-256 of the raw token. The raw token is
      never persisted — clients must save the value returned at
      create time.
    - ``description``: free-form label for the operator (e.g.
      "ipad development" or the SSO provider name) — surfaced in
      the token list UI but not load-bearing.
    - ``created_at``: when the token was minted.
    - ``last_used_at``: stamped on every successful validation so
      stale tokens can be pruned.
    - ``expires_at``: hard cap; tokens are rejected after this.
    - ``revoked_at``: set by logout / admin revocation; non-null
      means rejected regardless of ``expires_at``.
    """

    user_id: str
    token_hash: str
    description: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    last_used_at: datetime | None = None
    expires_at: datetime = field(
        default_factory=lambda: datetime.now() + timedelta(
            days=TOKEN_LIFETIME_DAYS,
        ),
    )
    revoked_at: datetime | None = None

    _id: ObjectId | None = None

    @property
    def id(self) -> str | None:
        return str(self._id) if self._id else None

    @property
    def mongo_id(self) -> ObjectId | None:
        return self._id

    @mongo_id.setter
    def mongo_id(self, value: ObjectId | None) -> None:
        self._id = value

    @property
    def is_valid(self) -> bool:
        """True when the token is not revoked and not expired."""
        if self.revoked_at is not None:
            return False
        return datetime.now() < self.expires_at

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "user_id": self.user_id,
            "token_hash": self.token_hash,
            "description": self.description,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "expires_at": self.expires_at,
            "revoked_at": self.revoked_at,
        }
        if self._id:
            data["_id"] = self._id
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApiToken:
        return cls(
            user_id=data["user_id"],
            token_hash=data["token_hash"],
            description=data.get("description"),
            created_at=data.get("created_at", datetime.now()),
            last_used_at=data.get("last_used_at"),
            expires_at=data.get(
                "expires_at",
                datetime.now() + timedelta(days=TOKEN_LIFETIME_DAYS),
            ),
            revoked_at=data.get("revoked_at"),
            _id=data.get("_id"),
        )


__all__ = [
    "TOKEN_LIFETIME_DAYS",
    "TOKEN_PREFIX",
    "ApiToken",
    "generate_raw_token",
    "hash_token",
]
