"""Audit-driven regression tests for ``JobManager``.

Covers two fixes from a code audit of ``auto_a11y/core/job_manager.py``:

* **Bug A** — ``update_job_status`` must persist ``started_at`` on the first
  RUNNING transition even when no progress payload is supplied. The original
  code stuffed ``started_at`` into ``$setOnInsert`` and then called
  ``update_one`` *without* ``upsert=True``, so the value was silently dropped.
* **Bug B** — ``request_cancellation`` must guard the PENDING/RUNNING status
  check atomically inside the ``update_one`` filter instead of doing a
  separate read-then-write, which races with concurrent transitions.

The Mongo-backed assertions skip when ``mongod`` is unreachable. A pure-Python
assertion on the constructed update document runs unconditionally so there is a
real RED -> GREEN signal without a database.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from typing import Any

import pytest
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from auto_a11y.core.database import Database
from auto_a11y.core.job_manager import (
    JobManager,
    JobStatus,
    JobType,
    build_status_update,
)


# --------------------------------------------------------------------------- #
# Pure-Python (no Mongo) — always runs                                        #
# --------------------------------------------------------------------------- #

def test_build_status_update_sets_started_at_for_running_without_progress() -> None:
    """Bug A: RUNNING with no progress must set started_at via $set, not
    $setOnInsert (which is dropped on a non-upsert update_one)."""
    update_doc = build_status_update(JobStatus.RUNNING, progress=None)

    assert '$set' in update_doc
    assert 'started_at' in update_doc['$set']
    assert update_doc['$set']['started_at'] is not None
    # $setOnInsert must not be relied upon here (it never fires without upsert).
    assert '$setOnInsert' not in update_doc


def test_build_status_update_sets_started_at_for_running_with_progress() -> None:
    """RUNNING with progress also sets started_at (unchanged behaviour)."""
    update_doc = build_status_update(
        JobStatus.RUNNING, progress={'current': 1, 'total': 2}
    )
    assert update_doc['$set']['started_at'] is not None
    assert update_doc['$set']['progress'] == {'current': 1, 'total': 2}


def test_build_status_update_completed_sets_completed_at() -> None:
    update_doc = build_status_update(JobStatus.COMPLETED, progress=None)
    assert 'completed_at' in update_doc['$set']
    assert 'started_at' not in update_doc['$set']


# --------------------------------------------------------------------------- #
# Mongo-backed — skip if mongod unavailable                                   #
# --------------------------------------------------------------------------- #

def _mongo_uri() -> str:
    return os.environ.get("MONGODB_URI", "mongodb://localhost:27017/")


@pytest.fixture(scope="module")
def mongo_check() -> None:
    client: MongoClient[dict[str, Any]] = MongoClient(
        _mongo_uri(), serverSelectionTimeoutMS=2000
    )
    try:
        client.admin.command("ping")
    except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
        client.close()
        pytest.skip(f"mongod not available at {_mongo_uri()}: {exc}")
    client.close()


@pytest.fixture
def database(mongo_check: None) -> Generator[Database, None, None]:
    db_name = f"auto_a11y_jobmgr_test_{uuid.uuid4().hex}"
    db = Database(_mongo_uri(), db_name)
    yield db
    db.client.drop_database(db_name)
    db.close()


@pytest.fixture
def job_manager(database: Database) -> JobManager:
    # The autouse conftest fixture resets the singleton around each test, so
    # constructing here binds to this test's fresh database.
    return JobManager(database)


def test_running_without_progress_persists_started_at(
    job_manager: JobManager,
) -> None:
    """Bug A regression: a RUNNING transition with no progress must write
    started_at to the document."""
    job_id = f"job-{uuid.uuid4().hex}"
    job_manager.create_job(job_id, JobType.REPORT_GENERATION)

    assert job_manager.update_job_status(job_id, JobStatus.RUNNING) is True

    job = job_manager.get_job(job_id)
    assert job is not None
    assert job['status'] == JobStatus.RUNNING.value
    assert job['started_at'] is not None


def test_request_cancellation_modifies_running_job(
    job_manager: JobManager,
) -> None:
    """Bug B: a running job can be cancelled atomically."""
    job_id = f"job-{uuid.uuid4().hex}"
    job_manager.create_job(job_id, JobType.TESTING)
    job_manager.update_job_status(job_id, JobStatus.RUNNING)

    assert job_manager.request_cancellation(job_id, requested_by="tester") is True

    job = job_manager.get_job(job_id)
    assert job is not None
    assert job['status'] == JobStatus.CANCELLING.value
    assert job['cancellation_requested'] is True


def test_request_cancellation_rejects_completed_job(
    job_manager: JobManager,
) -> None:
    """Bug B: the $in status filter prevents cancelling a completed job."""
    job_id = f"job-{uuid.uuid4().hex}"
    job_manager.create_job(job_id, JobType.TESTING)
    job_manager.update_job_status(job_id, JobStatus.COMPLETED)

    assert job_manager.request_cancellation(job_id) is False

    job = job_manager.get_job(job_id)
    assert job is not None
    assert job['status'] == JobStatus.COMPLETED.value


def test_request_cancellation_missing_job_returns_false(
    job_manager: JobManager,
) -> None:
    assert job_manager.request_cancellation("does-not-exist") is False
