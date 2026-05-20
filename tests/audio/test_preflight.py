"""Tests for the central preflight-check registry."""
from __future__ import annotations

from auto_a11y.core.preflight import (
    Check,
    CheckOutcome,
    PreflightRegistry,
)


def test_registry_runs_all_checks_and_reports_ok() -> None:
    registry = PreflightRegistry()
    registry.register(Check(
        name="one",
        description="first",
        run=lambda: CheckOutcome.ok(),
    ))
    registry.register(Check(
        name="two",
        description="second",
        run=lambda: CheckOutcome.ok(),
    ))
    result = registry.run_all()
    assert result.all_passed
    assert result.failures == []
    assert [r.name for r in result.results] == ["one", "two"]


def test_registry_surfaces_check_failures() -> None:
    registry = PreflightRegistry()
    registry.register(Check(
        name="bad",
        description="will fail",
        run=lambda: CheckOutcome.failed("Install the thing."),
    ))
    result = registry.run_all()
    assert not result.all_passed
    assert len(result.failures) == 1
    assert result.failures[0].name == "bad"
    assert result.failures[0].passed is False
    assert result.failures[0].remediation == "Install the thing."


def test_registry_catches_unexpected_exceptions() -> None:
    def boom() -> CheckOutcome:
        raise RuntimeError("kaboom")

    registry = PreflightRegistry()
    registry.register(Check(
        name="explodes",
        description="raises an exception",
        run=boom,
    ))
    result = registry.run_all()
    assert not result.all_passed
    assert len(result.failures) == 1
    assert result.failures[0].name == "explodes"
    # The remediation must NOT leak the stack-trace text (e.g. "kaboom")
    # into a user-facing message; the trace is sent to the logger instead.
    assert "kaboom" not in result.failures[0].remediation
    assert "internal error" in result.failures[0].remediation


def test_register_is_idempotent_on_name() -> None:
    reg = PreflightRegistry()
    reg.register(Check(name="dup", description="first", run=lambda: CheckOutcome.ok()))
    reg.register(Check(name="dup", description="second-DROPPED", run=lambda: CheckOutcome.ok()))
    result = reg.run_all()
    assert len(result.results) == 1
    assert result.results[0].description == "first"
