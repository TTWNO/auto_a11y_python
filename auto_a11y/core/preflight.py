"""Central preflight check registry.

Each pipeline subsystem (ffmpeg, Deepgram, Anthropic, Mongo) registers
a :class:`Check` at import time. App startup calls
:meth:`PreflightRegistry.run_all` and routes failures to the Settings
Recovery blueprint (see Phase 10 of the audioA11y integration plan).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckOutcome:
    """Result of running a single Check."""

    ok_: bool
    remediation: str = ""

    @classmethod
    def ok(cls) -> CheckOutcome:
        return cls(ok_=True)

    @classmethod
    def failed(cls, remediation: str) -> CheckOutcome:
        return cls(ok_=False, remediation=remediation)


@dataclass(frozen=True)
class Check:
    """A startup check: name, human description, and a zero-arg callable."""

    name: str
    description: str
    run: Callable[[], CheckOutcome]


@dataclass(frozen=True)
class CheckResult:
    """The flattened outcome for one Check (used in PreflightResult)."""

    name: str
    description: str
    ok_: bool
    remediation: str


@dataclass(frozen=True)
class PreflightResult:
    """Aggregate of all CheckResults from a single run_all() invocation."""

    results: list[CheckResult]

    @property
    def all_passed(self) -> bool:
        return all(r.ok_ for r in self.results)

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if not r.ok_]


class PreflightRegistry:
    """A registry of startup checks.

    A module-level singleton (:func:`get_registry`) is provided so that
    pipeline subsystems can register checks at import time.
    """

    def __init__(self) -> None:
        self._checks: list[Check] = []

    def register(self, check: Check) -> None:
        self._checks.append(check)

    def run_all(self) -> PreflightResult:
        results: list[CheckResult] = []
        for check in self._checks:
            try:
                outcome = check.run()
                results.append(CheckResult(
                    name=check.name,
                    description=check.description,
                    ok_=outcome.ok_,
                    remediation=outcome.remediation,
                ))
            except Exception as e:
                results.append(CheckResult(
                    name=check.name,
                    description=check.description,
                    ok_=False,
                    remediation=f"{type(e).__name__}: {e}",
                ))
        return PreflightResult(results=results)


_registry = PreflightRegistry()


def get_registry() -> PreflightRegistry:
    """Module-level singleton accessor.

    Pipeline subsystems call this at import time to register their checks.
    """
    return _registry
