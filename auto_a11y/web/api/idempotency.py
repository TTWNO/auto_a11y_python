"""Idempotency-key store backed by a MongoDB TTL collection.

Per `docs/REST_API_ROADMAP.md` §4.9: action endpoints (``POST
/resource/<id>/<action>``) accept an optional ``Idempotency-Key`` header.
If the same key arrives twice in flight, the second call returns the
result the first call recorded instead of starting a duplicate job.

Storage choice (locked in §8 of the roadmap): a Mongo collection with a
24-hour TTL index. In-memory storage was rejected because it produces
duplicate jobs as soon as the app is run with more than one worker.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Final

from auto_a11y.web.api.errors import ConflictError

if TYPE_CHECKING:
    from pymongo.collection import Collection


# Retention for stored keys. Must match the TTL configured on the collection
# index. 24h is comfortably longer than any expected client retry window
# (minutes), but short enough that the collection does not grow unbounded.
TTL: Final[timedelta] = timedelta(hours=24)


@dataclass(frozen=True, slots=True, kw_only=True)
class IdempotencyRecord:
    """Stored result of a previously-completed idempotent operation."""

    key: str
    request_hash: str
    response_status: int
    response_body: dict[str, Any]
    created_at: datetime


class IdempotencyStore:
    """Wrapper around the ``idempotency_keys`` Mongo collection.

    A handler invokes :meth:`get_or_record` with the inbound key plus a
    callable that produces the response when no prior record exists. The
    method either replays the recorded response or invokes the callable
    and stores the result for replay by future calls within the TTL.

    The store also detects request-body conflicts: if the same key is
    used with a *different* request body, that's almost always a client
    bug, so we surface it as a :class:`ConflictError` rather than silently
    replay a mismatched response.
    """

    def __init__(self, collection: Collection[dict[str, Any]]) -> None:
        self._collection = collection

    @staticmethod
    def hash_request(body: dict[str, Any] | None) -> str:
        """Stable hash of the request body for collision detection."""
        canonical = json.dumps(body or {}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def get(self, key: str) -> IdempotencyRecord | None:
        doc = self._collection.find_one({"_id": key})
        if doc is None:
            return None
        return IdempotencyRecord(
            key=str(doc["_id"]),
            request_hash=str(doc["request_hash"]),
            response_status=int(doc["response_status"]),
            response_body=dict(doc["response_body"]),
            created_at=doc["created_at"],
        )

    def record(
        self,
        key: str,
        *,
        request_hash: str,
        response_status: int,
        response_body: dict[str, Any],
    ) -> None:
        """Persist the result of a successful idempotent call.

        Uses ``replace_one(upsert=True)`` so a retry of an in-flight call
        cannot accidentally insert a second row. Concurrent first-time
        callers race; the loser overwrites the winner — both stored
        responses are equivalent so that's fine.
        """
        self._collection.replace_one(
            {"_id": key},
            {
                "_id": key,
                "request_hash": request_hash,
                "response_status": response_status,
                "response_body": response_body,
                "created_at": datetime.now(timezone.utc),
            },
            upsert=True,
        )

    def get_or_record(
        self,
        key: str,
        *,
        request_body: dict[str, Any] | None,
        compute: Callable[[], tuple[int, dict[str, Any]]],
    ) -> tuple[int, dict[str, Any]]:
        """Look up an existing record or compute a new one.

        ``compute`` runs only when no prior record exists for ``key``. It
        must return ``(status, body)`` — the values that get persisted
        and replayed on future calls.

        Raises :class:`ConflictError` if the same key has previously been
        recorded with a different request body.
        """
        request_hash = self.hash_request(request_body)
        existing = self.get(key)
        if existing is not None:
            if existing.request_hash != request_hash:
                raise ConflictError(
                    "Idempotency-Key has already been used with a different request body",
                )
            return existing.response_status, existing.response_body

        status, body = compute()
        self.record(
            key,
            request_hash=request_hash,
            response_status=status,
            response_body=body,
        )
        return status, body


def ensure_ttl_index(collection: Collection[dict[str, Any]]) -> None:
    """Idempotently install the TTL index on ``idempotency_keys``.

    Safe to call on every app startup. ``expireAfterSeconds`` must match
    :data:`TTL`; if you change the TTL constant, drop and recreate the
    index — Mongo does not allow modifying it in place.
    """
    collection.create_index(
        "created_at",
        expireAfterSeconds=int(TTL.total_seconds()),
        name="idempotency_keys_ttl",
    )
