"""Cost calculator for the audioA11y pipeline.

Pricing constants are point-in-time captures from the current Anthropic
and Deepgram rate sheets — see ``AS_OF`` below. The cost panel renders a
"Pricing as of …" line so users know the rates may be stale.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal


AS_OF: date = date(2026, 5, 19)


# === Anthropic Opus 4.7 (per million tokens) ===
OPUS_47_INPUT_USD_PER_M = 5.0
OPUS_47_OUTPUT_USD_PER_M = 25.0
OPUS_47_EXTENDED_INPUT_USD_PER_M = 10.0   # 2× above 200K tokens
OPUS_47_EXTENDED_OUTPUT_USD_PER_M = 37.5  # 1.5× above 200K tokens
CACHE_READ_RATIO = 0.10
CACHE_WRITE_RATIO = 1.25

EXTENDED_CONTEXT_THRESHOLD = 200_000

# === Deepgram nova-3 with diarization ===
DEEPGRAM_NOVA3_USD_PER_MINUTE = 0.0043

# === Heuristic constants for estimation ===
# Empirical: VTT tokens per minute of speech (observed in pythonAudioA11y runs).
TOKENS_PER_MINUTE_HEURISTIC = 200
# 4 analysis passes per (context × language): issues, painpoints, takeaways, assertions.
ANALYSIS_PASSES_PER_LANGUAGE = 4
# Typical Claude output tokens per analysis pass.
TYPICAL_OUTPUT_TOKENS_PER_PASS = 500


@dataclass(frozen=True)
class AnthropicUsage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class DeepgramUsage:
    minutes: float


def _empty_breakdown() -> dict[str, float]:
    """Default factory for ``CostBreakdown.breakdown`` — typed for pyright strict."""
    return {}


@dataclass(frozen=True)
class CostBreakdown:
    total_usd: float
    breakdown: dict[str, float] = field(default_factory=_empty_breakdown)

    def __add__(self, other: CostBreakdown) -> CostBreakdown:
        merged: dict[str, float] = dict(self.breakdown)
        for k, v in other.breakdown.items():
            merged[k] = merged.get(k, 0.0) + v
        return CostBreakdown(total_usd=self.total_usd + other.total_usd, breakdown=merged)


def cost_for_anthropic_call(
    usage: AnthropicUsage, *, model: str, extended_context: bool
) -> float:
    """Compute USD cost of one Anthropic call.

    Only ``claude-opus-4-7`` is implemented. Adding another model means
    adding its constants above and a branch here.
    """
    if model != "claude-opus-4-7":
        raise NotImplementedError(
            f"pricing constants only defined for claude-opus-4-7; got {model!r}"
        )

    if extended_context:
        std_in = min(usage.input_tokens, EXTENDED_CONTEXT_THRESHOLD)
        ext_in = max(0, usage.input_tokens - EXTENDED_CONTEXT_THRESHOLD)
        std_out = min(usage.output_tokens, EXTENDED_CONTEXT_THRESHOLD)
        ext_out = max(0, usage.output_tokens - EXTENDED_CONTEXT_THRESHOLD)
        in_cost = (
            (std_in / 1_000_000) * OPUS_47_INPUT_USD_PER_M
            + (ext_in / 1_000_000) * OPUS_47_EXTENDED_INPUT_USD_PER_M
        )
        out_cost = (
            (std_out / 1_000_000) * OPUS_47_OUTPUT_USD_PER_M
            + (ext_out / 1_000_000) * OPUS_47_EXTENDED_OUTPUT_USD_PER_M
        )
    else:
        in_cost = (usage.input_tokens / 1_000_000) * OPUS_47_INPUT_USD_PER_M
        out_cost = (usage.output_tokens / 1_000_000) * OPUS_47_OUTPUT_USD_PER_M

    cache_read_cost = (
        (usage.cache_read_tokens / 1_000_000)
        * OPUS_47_INPUT_USD_PER_M
        * CACHE_READ_RATIO
    )
    cache_write_cost = (
        (usage.cache_write_tokens / 1_000_000)
        * OPUS_47_INPUT_USD_PER_M
        * CACHE_WRITE_RATIO
    )

    return in_cost + out_cost + cache_read_cost + cache_write_cost


def cost_for_deepgram_minutes(minutes: float) -> float:
    """USD cost of transcribing ``minutes`` minutes of audio with nova-3 + diarization."""
    return minutes * DEEPGRAM_NOVA3_USD_PER_MINUTE


def estimate_cost(
    *,
    duration_s: float,
    contexts: list[Literal["audit", "livedExperience", "navilens"]],
    languages: list[Literal["en", "fr"]],
    extended_context: bool,
    callouts: bool,
) -> CostBreakdown:
    """Rough pre-flight estimate.

    Heuristic: VTT ≈ ``TOKENS_PER_MINUTE_HEURISTIC`` tokens per minute of speech.
    Each Claude pass uses the whole VTT as input plus ~``TYPICAL_OUTPUT_TOKENS_PER_PASS``
    output tokens. ``callouts`` adds only ffmpeg time, no API cost.

    Note: ``contexts`` is currently ignored — the estimator assumes one
    set of 4 passes per language; the actual analyze loop honours
    ``contexts``. The estimate is a lower bound for multi-context runs.
    """
    _ = contexts  # explicit non-use; reserved for a future multi-context estimate
    _ = callouts  # no API cost — only ffmpeg time

    duration_min = duration_s / 60.0
    deepgram = cost_for_deepgram_minutes(duration_min)

    breakdown: dict[str, float] = {"deepgram": deepgram}

    estimated_vtt_tokens = int(duration_min * TOKENS_PER_MINUTE_HEURISTIC)
    for lang in languages:
        per_lang = 0.0
        for _pass in range(ANALYSIS_PASSES_PER_LANGUAGE):
            usage = AnthropicUsage(
                input_tokens=estimated_vtt_tokens,
                output_tokens=TYPICAL_OUTPUT_TOKENS_PER_PASS,
            )
            per_lang += cost_for_anthropic_call(
                usage, model="claude-opus-4-7", extended_context=extended_context,
            )
        breakdown[f"claude_{lang}"] = per_lang

    total = sum(breakdown.values())
    return CostBreakdown(total_usd=total, breakdown=breakdown)
