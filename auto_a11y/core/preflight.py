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
    passed: bool
    remediation: str


@dataclass(frozen=True)
class PreflightResult:
    """Aggregate of all CheckResults from a single run_all() invocation."""

    results: list[CheckResult]

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed]


class PreflightRegistry:
    """A registry of startup checks.

    A module-level singleton (:func:`get_registry`) is provided so that
    pipeline subsystems can register checks at import time.
    """

    def __init__(self) -> None:
        self._checks: list[Check] = []

    def register(self, check: Check) -> None:
        """Register a preflight check. Idempotent on ``check.name``.

        Subsystems register at import time; module re-imports (e.g. under
        the test runner) must not produce duplicate entries.
        """
        if any(existing.name == check.name for existing in self._checks):
            return
        self._checks.append(check)

    def run_all(self) -> PreflightResult:
        results: list[CheckResult] = []
        for check in self._checks:
            try:
                outcome = check.run()
                results.append(CheckResult(
                    name=check.name,
                    description=check.description,
                    passed=outcome.ok_,
                    remediation=outcome.remediation,
                ))
            except Exception:
                logger.exception(
                    "Preflight check %r raised an internal error", check.name
                )
                results.append(CheckResult(
                    name=check.name,
                    description=check.description,
                    passed=False,
                    remediation=(
                        f"Preflight check '{check.name}' raised an internal error; "
                        "see application logs for details."
                    ),
                ))
        return PreflightResult(results=results)


_registry = PreflightRegistry()


def get_registry() -> PreflightRegistry:
    """Module-level singleton accessor.

    Pipeline subsystems call this at import time to register their checks.
    """
    return _registry
