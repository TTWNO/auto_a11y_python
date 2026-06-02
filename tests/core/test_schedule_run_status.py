"""Regression tests for :meth:`Database.update_test_schedule_run_status`.

The previous implementation built the Mongo update as
``{"$set": ..., "$inc": {"run_count": 1} if RUNNING else {}}``. For any
non-RUNNING status this sent ``"$inc": {}``, which MongoDB rejects with
``'$inc' is empty``. The scheduler then recorded every *successful* run as
FAILED. These tests pin the fix:

* a pure unit test of the update-dict builder (no Mongo required), and
* an end-to-end test against a real MongoDB (skipped if unavailable).
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from datetime import datetime
from typing import Any

import pytest
from bson import ObjectId
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from auto_a11y.core.database import Database, build_schedule_run_status_update
from auto_a11y.models.schedule import ScheduleRunStatus


# --------------------------------------------------------------------------
# Pure unit tests of the update-dict builder (no Mongo needed).
# --------------------------------------------------------------------------

def test_running_update_includes_inc() -> None:
    now = datetime.now()
    update = build_schedule_run_status_update(
        job_id="job-1", status=ScheduleRunStatus.RUNNING, now=now
    )
    assert update["$inc"] == {"run_count": 1}
    assert update["$set"]["last_run_status"] == "running"
    assert update["$set"]["last_run_at"] == now


@pytest.mark.parametrize(
    "status",
    [
        ScheduleRunStatus.SUCCESS,
        ScheduleRunStatus.FAILED,
        ScheduleRunStatus.CANCELLED,
    ],
)
def test_terminal_status_has_no_inc(status: ScheduleRunStatus) -> None:
    """A terminal status must NOT carry a ``$inc`` key (empty or otherwise)."""
    update = build_schedule_run_status_update(
        job_id="job-1", status=status, now=datetime.now()
    )
    assert "$inc" not in update
    assert update["$set"]["last_run_status"] == status.value
    # last_run_at is only stamped when the run starts.
    assert "last_run_at" not in update["$set"]


def test_next_run_at_recorded_when_provided() -> None:
    nxt = datetime.now()
    update = build_schedule_run_status_update(
        job_id="job-1",
        status=ScheduleRunStatus.SUCCESS,
        now=datetime.now(),
        next_run_at=nxt,
    )
    assert update["$set"]["next_run_at"] == nxt
    assert "$inc" not in update


# --------------------------------------------------------------------------
# End-to-end test against a real MongoDB (skips if mongod is unavailable).
# --------------------------------------------------------------------------

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
    db_name = f"auto_a11y_sched_test_{uuid.uuid4().hex}"
    db = Database(_mongo_uri(), db_name)
    yield db
    db.client.drop_database(db_name)
    db.close()


def test_recording_success_does_not_raise_and_persists(database: Database) -> None:
    """Recording a SUCCESS run must succeed and persist the SUCCESS status.

    Against the buggy code this update raised ``'$inc' is empty`` from
    MongoDB, so the scheduler mis-recorded successful runs as failures.
    """
    schedule_id = ObjectId()
    database.test_schedules.insert_one(
        {
            "_id": schedule_id,
            "website_id": "w1",
            "enabled": True,
            "run_count": 0,
            "last_run_status": None,
            "updated_at": datetime.now(),
        }
    )

    ok = database.update_test_schedule_run_status(
        schedule_id=str(schedule_id),
        job_id="job-abc",
        status=ScheduleRunStatus.SUCCESS,
    )

    assert ok is True
    doc = database.test_schedules.find_one({"_id": schedule_id})
    assert doc is not None
    assert doc["last_run_status"] == ScheduleRunStatus.SUCCESS.value
    assert doc["last_run_job_id"] == "job-abc"
    # SUCCESS must not bump run_count.
    assert doc["run_count"] == 0


def test_running_then_success_lifecycle(database: Database) -> None:
    """RUNNING bumps run_count and stamps last_run_at; SUCCESS finalises it."""
    schedule_id = ObjectId()
    database.test_schedules.insert_one(
        {
            "_id": schedule_id,
            "website_id": "w1",
            "enabled": True,
            "run_count": 0,
            "updated_at": datetime.now(),
        }
    )

    assert database.update_test_schedule_run_status(
        schedule_id=str(schedule_id),
        job_id="job-run",
        status=ScheduleRunStatus.RUNNING,
    )
    mid = database.test_schedules.find_one({"_id": schedule_id})
    assert mid is not None
    assert mid["run_count"] == 1
    assert mid["last_run_status"] == ScheduleRunStatus.RUNNING.value
    assert mid.get("last_run_at") is not None

    assert database.update_test_schedule_run_status(
        schedule_id=str(schedule_id),
        job_id="job-run",
        status=ScheduleRunStatus.SUCCESS,
    )
    final = database.test_schedules.find_one({"_id": schedule_id})
    assert final is not None
    assert final["last_run_status"] == ScheduleRunStatus.SUCCESS.value
    # run_count stays at 1 — SUCCESS does not increment.
    assert final["run_count"] == 1
