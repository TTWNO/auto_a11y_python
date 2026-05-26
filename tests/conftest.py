"""Pytest configuration: add the repo root to sys.path so top-level modules import."""
import sys
from collections.abc import Generator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def reset_job_manager_singleton() -> Generator[None, None, None]:
    """Reset the process-global ``JobManager`` singleton around every test.

    ``JobManager`` (auto_a11y/core/job_manager.py) is a module-level
    singleton that caches the ``Database`` it was first constructed with and
    early-returns from ``__init__`` on subsequent calls. Under pytest-xdist
    ``--dist loadfile`` one worker runs many test files in a single process,
    so a ``JobManager`` built in one file would otherwise carry a
    now-closed ``MongoClient`` into the next file's tests, raising
    ``InvalidOperation: Cannot use MongoClient after close`` (see the
    per-file resets several API test modules previously had to copy).
    Clearing the singleton before and after each test guarantees the next
    ``JobManager(database)`` rebinds to a live client.
    """
    from auto_a11y.core.job_manager import JobManager

    setattr(JobManager, "_instance", None)
    yield
    setattr(JobManager, "_instance", None)
