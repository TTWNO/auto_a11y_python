"""Pure-function cost calculator tests."""
from __future__ import annotations

from auto_a11y.audio.cost import (
    DEFAULT_PRICING_MODEL,
    AnthropicUsage,
    cost_for_anthropic_call,
    cost_for_deepgram_minutes,
    estimate_cost,
)


def test_default_pricing_model_is_opus_5() -> None:
    """Opus 5 is the first-class priced model (no fallback warning path)."""
    assert DEFAULT_PRICING_MODEL == "claude-opus-5"
    usage = AnthropicUsage(input_tokens=10_000, output_tokens=2_000)
    cost = cost_for_anthropic_call(
        usage, model="claude-opus-5", extended_context=False
    )
    # 10000 in @ $5/M = $0.05; 2000 out @ $25/M = $0.05; total $0.10 — Opus 5
    # is priced identically to Opus 4.8 and 4.7, so the rates are unchanged.
    assert abs(cost - 0.10) < 1e-9


def _approx(actual: float, expected: float, tol: float = 1e-9) -> bool:
    """Float-equality with absolute tolerance.

    Used in place of ``pytest.approx`` because the latter is typed
    loosely under pyright strict (``Unknown`` member types) — replacing
    it avoids the type noise while preserving test intent.
    """
    return abs(actual - expected) < tol


def test_anthropic_cost_no_extended_context() -> None:
    usage = AnthropicUsage(
        input_tokens=10_000, output_tokens=2_000,
        cache_read_tokens=0, cache_write_tokens=0,
    )
    cost = cost_for_anthropic_call(
        usage, model="claude-opus-4-7", extended_context=False
    )
    # 10000 in @ $5/M = $0.05; 2000 out @ $25/M = $0.05; total $0.10
    assert _approx(cost, 0.10)


def test_anthropic_cost_with_cache_hits() -> None:
    usage = AnthropicUsage(
        input_tokens=0, output_tokens=1000,
        cache_read_tokens=100_000, cache_write_tokens=0,
    )
    cost = cost_for_anthropic_call(
        usage, model="claude-opus-4-7", extended_context=False
    )
    # cache_read @ 10% of $5/M = $0.50/M; 100k × $0.50/M = $0.05
    # 1000 out @ $25/M = $0.025; total $0.075
    assert _approx(cost, 0.075)


def test_deepgram_cost() -> None:
    cost = cost_for_deepgram_minutes(60.0)  # 60 minutes
    # nova-3 with diarization: $0.0043/min
    assert _approx(cost, 0.258)


def test_estimate_total() -> None:
    estimate = estimate_cost(
        duration_s=600.0,            # 10 min video
        contexts=["audit"],
        languages=["en"],
        extended_context=False,
        callouts=False,
    )
    # Deepgram 10 min ≈ $0.043
    # 4 passes × 2000 tokens × $5/M input + 500 × $25/M output ≈ $0.10
    assert estimate.total_usd > 0.05
    assert estimate.total_usd < 1.0
    assert "deepgram" in estimate.breakdown
    assert "claude_en" in estimate.breakdown
