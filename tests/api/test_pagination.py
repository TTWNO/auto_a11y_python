"""Tests for `auto_a11y.web.api.pagination`.

Coverage focus:

- :class:`Cursor` round-trips through encode/decode.
- Decode raises :class:`ValidationError` on malformed input.
- :func:`parse_limit` handles missing, bad, and clamped values.
- :func:`paginate` emits ``next_cursor`` when more rows remain, and
  drops it when the caller has reached the end.
"""
from __future__ import annotations

from typing import Any

import pytest

from auto_a11y.web.api.errors import ValidationError
from auto_a11y.web.api.pagination import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    Cursor,
    paginate,
    parse_limit,
)


@pytest.mark.parametrize(
    "cursor",
    [
        Cursor(last_id="507f1f77bcf86cd799439011"),
        Cursor(last_id="abc", sort_value="2026-05-06T12:00:00Z"),
    ],
)
def test_cursor_encode_decode_round_trip(cursor: Cursor) -> None:
    decoded = Cursor.decode(cursor.encode())
    assert decoded == cursor


@pytest.mark.parametrize(
    "bad",
    [
        "not-base64!@#",
        "Zm9v",  # base64 of "foo" — not JSON
        "eyJmb28iOiAiYmFyIn0",  # base64 of '{"foo": "bar"}' — missing last_id
        "eyJsYXN0X2lkIjogMTIzfQ",  # base64 of '{"last_id": 123}' — wrong type
    ],
)
def test_cursor_decode_rejects_malformed_input(bad: str) -> None:
    with pytest.raises(ValidationError):
        Cursor.decode(bad)


def test_cursor_decode_rejects_non_string_sort_value() -> None:
    """If ``sort_value`` is present, it must be a string."""
    # base64 of '{"last_id": "x", "sort_value": 42}'
    token = "eyJsYXN0X2lkIjogIngiLCAic29ydF92YWx1ZSI6IDQyfQ"
    with pytest.raises(ValidationError):
        Cursor.decode(token)


def test_parse_limit_returns_default_when_missing() -> None:
    assert parse_limit(None) == DEFAULT_LIMIT
    assert parse_limit("") == DEFAULT_LIMIT


def test_parse_limit_clamps_to_max() -> None:
    assert parse_limit(str(MAX_LIMIT * 10)) == MAX_LIMIT


def test_parse_limit_rejects_non_integer() -> None:
    with pytest.raises(ValidationError):
        parse_limit("abc")


def test_parse_limit_rejects_zero_and_negative() -> None:
    with pytest.raises(ValidationError):
        parse_limit("0")
    with pytest.raises(ValidationError):
        parse_limit("-5")


def _id(item: dict[str, Any]) -> str:
    return str(item["id"])


def test_paginate_emits_cursor_when_more_rows_remain() -> None:
    items = [{"id": str(i)} for i in range(11)]  # caller fetched limit+1
    page = paginate(items, limit=10, get_id=_id)

    assert len(page["items"]) == 10
    assert page["items"][-1]["id"] == "9"
    assert page["next_cursor"] is not None
    decoded = Cursor.decode(page["next_cursor"])
    assert decoded.last_id == "9"


def test_paginate_omits_cursor_on_last_page() -> None:
    items = [{"id": str(i)} for i in range(5)]
    page = paginate(items, limit=10, get_id=_id)

    assert len(page["items"]) == 5
    assert page["next_cursor"] is None


def test_paginate_omits_cursor_when_empty() -> None:
    page = paginate([], limit=10, get_id=_id)
    assert page["items"] == []
    assert page["next_cursor"] is None
