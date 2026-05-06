"""Cursor-based pagination for REST list endpoints.

Cursors are opaque base64-encoded JSON blobs that carry the values
needed to resume scanning a sorted query — typically the ``_id`` of the
last returned row, plus any sort-key tiebreaker. They are NOT meant to
be parsed by clients; treat them as opaque tokens.

Page-number pagination is intentionally not supported. ``limit`` is
clamped server-side to keep individual responses bounded.
"""
from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypedDict, TypeVar, cast

from auto_a11y.web.api.errors import FieldError, ValidationError

T = TypeVar("T")


# Bound the maximum page size so a single request cannot exhaust memory or
# blow past Mongo's 16MB document limit when a handler accidentally projects
# a large field. Increase only with a deliberate performance review.
DEFAULT_LIMIT: int = 50
MAX_LIMIT: int = 200


class Page(TypedDict):
    """Wire shape for a paginated response.

    ``items`` is the list of resources; ``next_cursor`` is the opaque token
    to pass back as ``?cursor=`` for the next page (or ``None`` when the
    caller has reached the end).
    """

    items: list[Any]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class Cursor:
    """The decoded contents of a cursor token.

    Most callers only need ``last_id``. ``sort_value`` carries the value of
    the secondary sort key (e.g. ``created_at``) when the primary sort is
    not ``_id``, so resumption is correct across rows with equal sort
    values.
    """

    last_id: str
    sort_value: str | None = None

    def encode(self) -> str:
        payload: dict[str, str] = {"last_id": self.last_id}
        if self.sort_value is not None:
            payload["sort_value"] = self.sort_value
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    @classmethod
    def decode(cls, token: str) -> Cursor:
        """Decode a cursor token. Raises :class:`ValidationError` if malformed."""
        try:
            padded = token + "=" * (-len(token) % 4)
            raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise ValidationError(
                "cursor is not valid base64",
                errors=(
                    FieldError(field="cursor", code="invalid_encoding", message=str(exc)),
                ),
            ) from exc
        payload_any: Any
        try:
            payload_any = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValidationError(
                "cursor payload is not valid JSON",
                errors=(
                    FieldError(field="cursor", code="invalid_payload", message=str(exc)),
                ),
            ) from exc
        if not isinstance(payload_any, dict) or "last_id" not in payload_any:
            raise ValidationError(
                "cursor is missing required keys",
                errors=(
                    FieldError(
                        field="cursor",
                        code="missing_keys",
                        message="last_id is required",
                    ),
                ),
            )
        payload = cast(dict[str, Any], payload_any)
        last_id_raw: Any = payload["last_id"]
        if not isinstance(last_id_raw, str):
            raise ValidationError(
                "cursor.last_id must be a string",
                errors=(
                    FieldError(
                        field="cursor.last_id", code="invalid_type", message="must be string"
                    ),
                ),
            )
        last_id: str = last_id_raw
        sort_value_raw: Any = payload.get("sort_value")
        sort_value: str | None
        if sort_value_raw is None:
            sort_value = None
        elif isinstance(sort_value_raw, str):
            sort_value = sort_value_raw
        else:
            raise ValidationError(
                "cursor.sort_value must be a string when present",
                errors=(
                    FieldError(
                        field="cursor.sort_value",
                        code="invalid_type",
                        message="must be string",
                    ),
                ),
            )
        return cls(last_id=last_id, sort_value=sort_value)


def parse_limit(raw: str | None) -> int:
    """Parse and clamp a ``?limit=`` query string.

    Empty/missing → :data:`DEFAULT_LIMIT`. Non-integer or non-positive →
    :class:`ValidationError`. Above :data:`MAX_LIMIT` is clamped silently
    rather than rejected, because the cap is a server-side defense, not a
    contract clients need to observe.
    """
    if raw is None or raw == "":
        return DEFAULT_LIMIT
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValidationError(
            "limit must be an integer",
            errors=(
                FieldError(field="limit", code="invalid_type", message=str(exc)),
            ),
        ) from exc
    if value <= 0:
        raise ValidationError(
            "limit must be positive",
            errors=(
                FieldError(field="limit", code="out_of_range", message="must be > 0"),
            ),
        )
    return min(value, MAX_LIMIT)


def paginate(items: list[T], *, limit: int, get_id: Callable[[T], str]) -> Page:
    """Build a :class:`Page` from a fetched list of items.

    The caller is expected to have queried Mongo for ``limit + 1`` rows. If
    the list is full (``len(items) > limit``), the trailing row is dropped
    and a cursor pointing at the last *kept* row is emitted.

    ``get_id`` extracts the ``last_id`` field for the cursor. The cursor
    payload is always a string — Mongo ObjectIds must be stringified by
    the caller.
    """
    has_more = len(items) > limit
    page_items: list[T] = items[:limit] if has_more else items

    next_cursor: str | None
    if has_more and page_items:
        next_cursor = Cursor(last_id=get_id(page_items[-1])).encode()
    else:
        next_cursor = None

    return Page(items=list(page_items), next_cursor=next_cursor)
