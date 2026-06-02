"""
Audit-driven regression tests for ClaudeAnalyzer.

Covers two bugs found in a code audit:

Bug A (MEDIUM): ``ClaudeAnalyzer`` read ``CLAUDE_MAX_TOKENS`` /
``CLAUDE_BUDGET_TOKENS`` from config with no validation that
``budget_tokens < max_tokens``. The Anthropic extended-thinking API requires
``budget_tokens`` strictly less than ``max_tokens``; a config where budget
>= max would error at request time. The analyzer now clamps the budget to
``max_tokens - 1`` (and logs a warning when it has to).

Bug B (LOW): ``_count_by_type`` claimed (per its comment) to group findings by
analyzer name, but actually grouped by ``finding.touchpoint`` — the mapped
touchpoint. Distinct analysis types that share one touchpoint collapsed into a
single bucket. It now groups by the actual AI analysis type recorded in
``metadata['ai_analysis_type']`` (the authoritative field already used by the
result processor), matching the comment's intent.
"""

from __future__ import annotations

from typing import Callable

import pytest

# Load core before touching the analyzer package to keep the import chain happy
# (mirrors the pattern used by test_result_processor_audit.py).
import auto_a11y.core as _core_preload

del _core_preload

from auto_a11y.ai.claude_analyzer import ClaudeAnalyzer
from auto_a11y.models import Violation, ImpactLevel


def _make_analyzer(monkeypatch: pytest.MonkeyPatch, *, max_tokens: int, budget_tokens: int) -> ClaudeAnalyzer:
    """Construct a ClaudeAnalyzer with config token values overridden."""
    from config import config as app_config

    monkeypatch.setattr(app_config, "CLAUDE_MAX_TOKENS", max_tokens, raising=False)
    monkeypatch.setattr(app_config, "CLAUDE_BUDGET_TOKENS", budget_tokens, raising=False)
    # Disable thinking-specific config noise; construction makes no network calls.
    monkeypatch.setattr(app_config, "CLAUDE_USE_THINKING", True, raising=False)
    return ClaudeAnalyzer(api_key="test-dummy-key")


def _count_by_type(analyzer: ClaudeAnalyzer, findings: list[Violation]) -> dict[str, int]:
    """Call the protected ``_count_by_type`` without tripping reportPrivateUsage."""
    method: Callable[[list[Violation]], dict[str, int]] = getattr(analyzer, "_count_by_type")
    return method(findings)


# --- Bug A: budget clamp --------------------------------------------------


def test_budget_clamped_when_equal_to_max(monkeypatch: pytest.MonkeyPatch) -> None:
    analyzer = _make_analyzer(monkeypatch, max_tokens=16000, budget_tokens=16000)
    assert analyzer.client.config.max_tokens == 16000
    assert analyzer.client.config.budget_tokens == 15999


def test_budget_clamped_when_greater_than_max(monkeypatch: pytest.MonkeyPatch) -> None:
    analyzer = _make_analyzer(monkeypatch, max_tokens=8000, budget_tokens=20000)
    assert analyzer.client.config.budget_tokens == 7999


def test_budget_unchanged_when_below_max(monkeypatch: pytest.MonkeyPatch) -> None:
    analyzer = _make_analyzer(monkeypatch, max_tokens=16000, budget_tokens=10000)
    assert analyzer.client.config.max_tokens == 16000
    assert analyzer.client.config.budget_tokens == 10000


# --- Bug B: _count_by_type groups by analysis type ------------------------


def _ai_violation(analysis_type: str, touchpoint: str) -> Violation:
    return Violation(
        id="AI_ErrSomething",
        impact=ImpactLevel.HIGH,
        touchpoint=touchpoint,
        description="x",
        metadata={"ai_analysis_type": analysis_type},
    )


def test_count_by_type_separates_analysis_types_sharing_touchpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analyzer = _make_analyzer(monkeypatch, max_tokens=16000, budget_tokens=10000)

    # Two distinct analysis types that both map to the same touchpoint
    # ('reading_order' and 'modals' historically collapsed in older code, but
    # the point is: same touchpoint, different analysis type).
    findings = [
        _ai_violation("reading_order", "focus-management"),
        _ai_violation("reading_order", "focus-management"),
        _ai_violation("modals", "focus-management"),
    ]

    counts: dict[str, int] = _count_by_type(analyzer, findings)

    assert counts == {"reading_order": 2, "modals": 1}


def test_count_by_type_falls_back_to_touchpoint_without_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analyzer = _make_analyzer(monkeypatch, max_tokens=16000, budget_tokens=10000)

    finding = Violation(
        id="AI_ErrSomething",
        impact=ImpactLevel.HIGH,
        touchpoint="headings",
        description="x",
        metadata={},
    )

    counts: dict[str, int] = _count_by_type(analyzer, [finding])

    # No ai_analysis_type recorded -> fall back to touchpoint so nothing is lost.
    assert counts == {"headings": 1}
