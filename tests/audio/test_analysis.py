"""Tests for audio/analysis.py — mocked Anthropic SDK."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from auto_a11y.audio.analysis import AnalysisResult, Analyzer
from auto_a11y.audio.errors import AnalysisError


def _fake_message(text: str, *, stop_reason: str = "end_turn") -> object:
    """Build a fake Anthropic message response.

    Uses ``SimpleNamespace`` so reading a wrong attribute name raises
    ``AttributeError`` instead of auto-creating a ``MagicMock``.
    """
    block = SimpleNamespace(text=text)
    usage = SimpleNamespace(
        input_tokens=1000,
        output_tokens=200,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    return SimpleNamespace(content=[block], usage=usage, stop_reason=stop_reason)


def _client_returning(message_obj: object) -> MagicMock:
    """A MagicMock Anthropic client whose ``messages.stream(...)`` context
    manager yields a stream whose ``get_final_message()`` returns ``message_obj``.

    The analyzer streams the request (large outputs would otherwise hit the
    SDK's non-streaming timeout guard) and reads the assembled message via
    ``stream.get_final_message()``.
    """
    client = MagicMock()
    stream_ctx = MagicMock()
    stream_ctx.__enter__.return_value.get_final_message.return_value = message_obj
    client.messages.stream.return_value = stream_ctx
    return client


def test_analyzer_uses_correct_prompt_for_context() -> None:
    issues_payload = json.dumps({"recording": "REC-x", "issues": []})
    client = _client_returning(_fake_message(issues_payload))

    analyzer = Analyzer(
        client=client, model="claude-opus-4-7", extended_context=False
    )
    result = analyzer.analyze(
        vtt="WEBVTT\n",
        context="audit",
        kind="issues",
        language="en",
        recording_id="REC-test",
    )
    assert isinstance(result, AnalysisResult)
    assert result.json_payload["recording"] == "REC-x"

    sent_kwargs = client.messages.stream.call_args.kwargs
    messages: list[dict[str, Any]] = sent_kwargs["messages"]
    joined = " ".join(
        m["content"] for m in messages if isinstance(m["content"], str)
    )
    assert "END_OF_PROMPT" in joined  # heuristics + audit_issues both end with the marker


def test_analyzer_french_pass_prepends_french_instruction() -> None:
    client = _client_returning(
        _fake_message(json.dumps({"recording": "x", "issues": []}))
    )
    analyzer = Analyzer(
        client=client, model="claude-opus-4-7", extended_context=False
    )
    analyzer.analyze(
        vtt="W", context="audit", kind="issues", language="fr", recording_id="X"
    )

    messages = client.messages.stream.call_args.kwargs["messages"]
    joined = " ".join(
        m["content"] for m in messages if isinstance(m["content"], str)
    )
    # The Analyzer prepends a French-output instruction at the prompt head.
    assert "français" in joined or "French" in joined


def test_analyzer_records_cost_in_result() -> None:
    client = _client_returning(
        _fake_message(json.dumps({"recording": "x", "issues": []}))
    )
    analyzer = Analyzer(
        client=client, model="claude-opus-4-7", extended_context=False
    )
    result = analyzer.analyze(
        vtt="W", context="audit", kind="issues", language="en", recording_id="X"
    )
    # 1000 in × $5/M = $0.005; 200 out × $25/M = $0.005; total $0.01
    assert result.cost_usd > 0


def test_analyzer_requests_a_large_token_budget() -> None:
    """The analysis JSON for a full audit needs far more than the old 8000.

    Regression for the truncation bug: a full audit's JSON (~22.5 KB ≈ 8000
    tokens) was cut off at max_tokens=8000 and failed to parse. The budget
    must be generously above that.
    """
    client = _client_returning(
        _fake_message(json.dumps({"recording": "x", "issues": []}))
    )
    analyzer = Analyzer(
        client=client, model="claude-opus-4-7", extended_context=False
    )
    analyzer.analyze(
        vtt="W", context="audit", kind="issues", language="en", recording_id="X"
    )
    assert client.messages.stream.call_args.kwargs["max_tokens"] >= 16000


def test_analyzer_raises_clear_error_when_output_truncated() -> None:
    """A response that hit max_tokens must fail with an actionable message.

    Without the guard, the truncated (incomplete) JSON reaches json.loads and
    surfaces as a cryptic "Expecting ',' delimiter" — the exact symptom seen
    in production. The error must name truncation instead.
    """
    truncated = '{"recording": "x", "issues": [{"title": "incomplete"'
    client = _client_returning(_fake_message(truncated, stop_reason="max_tokens"))
    analyzer = Analyzer(
        client=client, model="claude-opus-4-7", extended_context=False
    )
    with pytest.raises(AnalysisError) as excinfo:
        analyzer.analyze(
            vtt="W", context="audit", kind="issues", language="en", recording_id="REC-trunc"
        )
    assert "truncat" in str(excinfo.value).lower()
