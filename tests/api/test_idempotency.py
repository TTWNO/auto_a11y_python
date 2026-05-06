"""Tests for `auto_a11y.web.api.idempotency`.

These tests hit a real MongoDB instance per the testing strategy in
``docs/REST_API_ROADMAP.md`` §7.2 (no DB mocking). When ``mongod`` is
not reachable, every test in this module is skipped — the full suite
runs in CI where Mongo is provisioned.

Each test uses a unique ephemeral collection name so concurrent test
runs cannot interfere with each other.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from typing import Any

import pytest
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from auto_a11y.web.api.errors import ConflictError
from auto_a11y.web.api.idempotency import (
    TTL,
    IdempotencyStore,
    ensure_ttl_index,
)


def _mongo_uri() -> str:
    return os.environ.get("MONGODB_URI", "mongodb://localhost:27017/")


@pytest.fixture(scope="module")
def mongo_client() -> Generator[MongoClient[dict[str, Any]], None, None]:
    """Connect to mongod or skip the entire module."""
    client: MongoClient[dict[str, Any]] = MongoClient(
        _mongo_uri(), serverSelectionTimeoutMS=2000
    )
    try:
        client.admin.command("ping")
    except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
        client.close()
        pytest.skip(f"mongod not available at {_mongo_uri()}: {exc}")
    yield client
    client.close()


@pytest.fixture
def collection(
    mongo_client: MongoClient[dict[str, Any]],
) -> Generator[Collection[dict[str, Any]], None, None]:
    """Per-test ephemeral collection. Dropped on teardown."""
    db = mongo_client[f"auto_a11y_test_{uuid.uuid4().hex}"]
    coll: Collection[dict[str, Any]] = db.idempotency_keys
    ensure_ttl_index(coll)
    yield coll
    mongo_client.drop_database(db.name)


@pytest.fixture
def store(collection: Collection[dict[str, Any]]) -> IdempotencyStore:
    return IdempotencyStore(collection)


def test_get_returns_none_when_key_unknown(store: IdempotencyStore) -> None:
    assert store.get("never-used") is None


def test_record_persists_and_get_returns_record(store: IdempotencyStore) -> None:
    store.record(
        "key-1",
        request_hash="hash-1",
        response_status=202,
        response_body={"job_id": "abc"},
    )
    record = store.get("key-1")
    assert record is not None
    assert record.key == "key-1"
    assert record.response_status == 202
    assert record.response_body == {"job_id": "abc"}


def test_get_or_record_invokes_compute_on_first_call(
    store: IdempotencyStore,
) -> None:
    calls: list[None] = []

    def compute() -> tuple[int, dict[str, Any]]:
        calls.append(None)
        return 202, {"job_id": "first"}

    status, body = store.get_or_record(
        "key-2", request_body={"x": 1}, compute=compute
    )
    assert status == 202
    assert body == {"job_id": "first"}
    assert len(calls) == 1


def test_get_or_record_replays_on_repeat_with_same_body(
    store: IdempotencyStore,
) -> None:
    calls: list[None] = []

    def compute() -> tuple[int, dict[str, Any]]:
        calls.append(None)
        return 202, {"job_id": str(len(calls))}

    body = {"x": 1}
    first = store.get_or_record("key-3", request_body=body, compute=compute)
    second = store.get_or_record("key-3", request_body=body, compute=compute)

    assert first == second
    assert len(calls) == 1, "compute should only run once"


def test_get_or_record_raises_conflict_when_body_differs(
    store: IdempotencyStore,
) -> None:
    def compute() -> tuple[int, dict[str, Any]]:
        return 202, {"job_id": "x"}

    store.get_or_record("key-4", request_body={"x": 1}, compute=compute)
    with pytest.raises(ConflictError):
        store.get_or_record("key-4", request_body={"x": 2}, compute=compute)


def test_hash_request_is_stable_under_key_reordering() -> None:
    a = IdempotencyStore.hash_request({"a": 1, "b": 2})
    b = IdempotencyStore.hash_request({"b": 2, "a": 1})
    assert a == b


def test_hash_request_handles_none() -> None:
    assert (
        IdempotencyStore.hash_request(None)
        == IdempotencyStore.hash_request({})
    )


def test_ensure_ttl_index_creates_documented_index(
    collection: Collection[dict[str, Any]],
) -> None:
    info = collection.index_information()
    ttl_index = info.get("idempotency_keys_ttl")
    assert ttl_index is not None, "ensure_ttl_index should create idempotency_keys_ttl"
    assert ttl_index.get("expireAfterSeconds") == int(TTL.total_seconds())


def test_ensure_ttl_index_is_idempotent(
    collection: Collection[dict[str, Any]],
) -> None:
    """Calling at startup repeatedly must not raise."""
    ensure_ttl_index(collection)
    ensure_ttl_index(collection)
