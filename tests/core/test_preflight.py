"""Unit tests for the preflight registry's required/optional semantics.

A fresh desktop (DMG) launch has no Deepgram/Anthropic API keys configured.
Those are optional capabilities — their absence must not force the whole app
into Settings Recovery mode. Only *required* checks (e.g. MongoDB) may block
startup. These tests pin that contract at the registry layer, independent of
Flask or a live MongoDB.
"""
from __future__ import annotations

from auto_a11y.core.preflight import (
    Check,
    CheckOutcome,
    PreflightRegistry,
)


def _ok() -> CheckOutcome:
    return CheckOutcome.ok()


def _fail(msg: str = "nope") -> CheckOutcome:
    return CheckOutcome.failed(msg)


def test_check_defaults_to_required() -> None:
    """An unannotated check is required (back-compat with existing checks)."""
    check = Check(name="x", description="d", run=_ok)
    assert check.required is True


def test_run_all_propagates_required_flag() -> None:
    reg = PreflightRegistry()
    reg.replace_all([
        Check(name="req", description="d", run=_fail, required=True),
        Check(name="opt", description="d", run=_fail, required=False),
    ])
    by_name = {r.name: r for r in reg.run_all().results}
    assert by_name["req"].required is True
    assert by_name["opt"].required is False


def test_blocking_failures_excludes_optional() -> None:
    """A failing optional check must not appear in blocking_failures."""
    reg = PreflightRegistry()
    reg.replace_all([
        Check(name="mongodb", description="d", run=_fail, required=True),
        Check(name="anthropic", description="d", run=_fail, required=False),
        Check(name="deepgram", description="d", run=_fail, required=False),
    ])
    result = reg.run_all()

    assert [r.name for r in result.blocking_failures] == ["mongodb"]
    assert {r.name for r in result.optional_failures} == {"anthropic", "deepgram"}
    # all_passed stays honest: it still reports any failure at all.
    assert result.all_passed is False


def test_optional_only_failures_do_not_block() -> None:
    """The fresh-DMG case: Mongo ok, both API keys missing → nothing blocks."""
    reg = PreflightRegistry()
    reg.replace_all([
        Check(name="mongodb", description="d", run=_ok, required=True),
        Check(name="anthropic", description="d", run=_fail, required=False),
        Check(name="deepgram", description="d", run=_fail, required=False),
    ])
    result = reg.run_all()

    assert result.blocking_failures == []
    assert result.all_passed is False  # optional failures are still failures
    assert {r.name for r in result.optional_failures} == {"anthropic", "deepgram"}


def test_internal_error_in_optional_check_is_non_blocking() -> None:
    """A crash inside an optional check is reported, but still optional."""
    def boom() -> CheckOutcome:
        raise RuntimeError("kaboom")

    reg = PreflightRegistry()
    reg.replace_all([
        Check(name="opt", description="d", run=boom, required=False),
    ])
    result = reg.run_all()

    assert result.blocking_failures == []
    assert [r.name for r in result.optional_failures] == ["opt"]
