"""Tests for SchedulerService.get_next_run_times.

Regression coverage for two bugs that were present in the original
implementation:

1. It appended the *probe* timestamp (``next_time``) to the result list
   instead of the actual computed fire time, so the returned list did not
   reflect the trigger's real fire times.
2. It advanced the probe with ``next_time.replace(second=next_time.second + 1)``,
   which raises ``ValueError`` whenever ``second`` reaches 60 (e.g. a probe
   landing on second 59). That crashed the whole method for certain triggers.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from auto_a11y.core.database import Database
from auto_a11y.core.scheduler import SchedulerService
from auto_a11y.models import ScheduleType
# Aliased import: pytest would otherwise try to collect the ``TestSchedule``
# model dataclass as a test class and emit a PytestCollectionWarning.
from auto_a11y.models import TestSchedule as ScheduleModel
from auto_a11y.models.schedule import PresetConfig


def _make_database(schedule: ScheduleModel | None) -> Database:
    """Build a Database without a live Mongo connection.

    ``get_next_run_times`` only calls ``database.get_test_schedule``, so we
    construct the real ``Database`` type via ``__new__`` (bypassing the
    connecting ``__init__``) and override that single method. Using the real
    type keeps the static type checkers satisfied with no suppressions.
    """
    db = Database.__new__(Database)

    def _get_test_schedule(schedule_id: str) -> ScheduleModel | None:
        return schedule

    # Bind a same-signature replacement for the one method exercised here.
    # ``setattr`` (value typed ``object``) avoids a method-assign type error.
    setattr(db, "get_test_schedule", _get_test_schedule)
    return db


def _make_service(schedule: ScheduleModel | None) -> SchedulerService:
    """Return the SchedulerService with its database serving ``schedule``.

    ``SchedulerService`` is a singleton, so we cannot rely on the constructor
    to (re)bind the database on subsequent calls — ``__init__`` short-circuits
    once initialised. We therefore set the public ``database`` attribute
    explicitly so each test sees its own schedule.
    """
    database = _make_database(schedule)
    service = SchedulerService(database=database, config=None)
    service.database = database
    return service


def _make_schedule(
    schedule_type: ScheduleType,
    *,
    cron_expression: str | None = None,
    time: str = "02:00",
    timezone: str = "America/Toronto",
) -> ScheduleModel:
    return ScheduleModel(
        website_id="w1",
        name="test",
        schedule_type=schedule_type,
        cron_expression=cron_expression,
        preset_config=PresetConfig(time=time, timezone=timezone),
    )


def test_returns_actual_fire_times_for_daily() -> None:
    """The returned times must be the trigger's real fire times, not probes."""
    schedule = _make_schedule(ScheduleType.DAILY, time="02:30", timezone="UTC")
    service = _make_service(schedule)

    times = service.get_next_run_times("anything", count=4)

    assert len(times) == 4
    # Strictly increasing.
    assert all(times[i] < times[i + 1] for i in range(len(times) - 1))
    # A daily cron fires exactly 24h apart.
    for i in range(len(times) - 1):
        assert times[i + 1] - times[i] == timedelta(days=1)
    # Each fire time is at the configured time-of-day (02:30), proving these
    # are the computed fire times and not arbitrary probe timestamps.
    for t in times:
        assert (t.hour, t.minute, t.second) == (2, 30, 0)


def test_returns_actual_fire_times_for_hourly_cron() -> None:
    """Hourly cron should produce fire times spaced exactly one hour apart."""
    schedule = _make_schedule(
        ScheduleType.CRON, cron_expression="0 * * * *", timezone="UTC"
    )
    service = _make_service(schedule)

    times = service.get_next_run_times("anything", count=3)

    assert len(times) == 3
    for i in range(len(times) - 1):
        assert times[i + 1] - times[i] == timedelta(hours=1)
    for t in times:
        assert (t.minute, t.second) == (0, 0)


def test_count_limit_respected() -> None:
    schedule = _make_schedule(ScheduleType.CRON, cron_expression="0 * * * *", timezone="UTC")
    service = _make_service(schedule)
    assert len(service.get_next_run_times("anything", count=7)) == 7


def test_does_not_crash_when_probe_lands_on_second_59(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A probe landing on second 59 must not raise ValueError.

    The original code advanced the probe via ``replace(second=second + 1)``,
    which raises ``ValueError("second must be in 0..59")`` the moment the
    probe's ``second`` field reaches 59 and gets incremented to 60. We pin the
    starting clock to ``...:59`` so the very first advance would overflow under
    the buggy implementation.
    """
    import auto_a11y.core.scheduler as scheduler_module

    pinned = datetime(2026, 6, 1, 12, 0, 59, tzinfo=ZoneInfo("UTC"))

    class _PinnedClock:
        """Stand-in exposing only ``now`` (the sole attr the SUT touches)."""

        @staticmethod
        def now(tz: object = None) -> datetime:
            return pinned

    monkeypatch.setattr(scheduler_module, "datetime", _PinnedClock)

    schedule = _make_schedule(
        ScheduleType.CRON, cron_expression="* * * * *", timezone="UTC"
    )
    service = _make_service(schedule)

    # Should not raise ValueError("second must be in 0..59").
    times = service.get_next_run_times("anything", count=5)

    assert len(times) == 5
    # Per-minute cron: spaced exactly one minute apart, on second 0.
    for i in range(len(times) - 1):
        assert times[i + 1] - times[i] == timedelta(minutes=1)
    for t in times:
        assert t.second == 0


def test_empty_when_no_schedule() -> None:
    service = _make_service(None)
    assert service.get_next_run_times("missing") == []
