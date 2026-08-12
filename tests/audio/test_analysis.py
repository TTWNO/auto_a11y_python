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


def _client_returning_sequence(messages: list[object]) -> MagicMock:
    """Client whose successive ``stream(...).get_final_message()`` calls
    return each message in turn — one per analyze attempt — so the retry
    path can be exercised.
    """
    client = MagicMock()
    stream_ctx = MagicMock()
    stream_ctx.__enter__.return_value.get_final_message.side_effect = list(messages)
    client.messages.stream.return_value = stream_ctx
    return client


def test_analyzer_retries_on_malformed_json_then_succeeds() -> None:
    """A complete-but-unparseable response is retried; a valid retry wins.

    The dominant parse-failure cause (truncation) is handled separately;
    this covers the residual flaky-generation case the production logs showed
    ("Expecting ',' delimiter" on a non-truncated body).
    """
    bad = _fake_message('{"recording": "x", "issues": [}')  # complete, invalid
    good = _fake_message(json.dumps({"recording": "x", "issues": []}))
    client = _client_returning_sequence([bad, good])
    analyzer = Analyzer(client=client, model="claude-opus-5")
    result = analyzer.analyze(
        vtt="W", context="audit", kind="issues", language="en", recording_id="REC-retry"
    )
    assert result.json_payload["recording"] == "x"
    assert client.messages.stream.call_count == 2


def test_analyzer_raises_after_exhausting_parse_retries() -> None:
    """Every attempt malformed → AnalysisError after the retry budget."""
    bad = '{"recording": "x", "issues": [}'
    client = _client_returning_sequence([_fake_message(bad), _fake_message(bad)])
    analyzer = Analyzer(client=client, model="claude-opus-5")
    with pytest.raises(AnalysisError):
        analyzer.analyze(
            vtt="W", context="audit", kind="issues", language="en", recording_id="REC-bad"
        )
    assert client.messages.stream.call_count == 2


def test_analyzer_does_not_retry_on_truncation() -> None:
    """Truncation is not retryable — re-rolling would just truncate again."""
    truncated = _fake_message('{"recording": "x"', stop_reason="max_tokens")
    client = _client_returning_sequence([truncated, _fake_message("{}")])
    analyzer = Analyzer(client=client, model="claude-opus-5")
    with pytest.raises(AnalysisError) as excinfo:
        analyzer.analyze(
            vtt="W", context="audit", kind="issues", language="en", recording_id="REC-trunc"
        )
    assert "truncat" in str(excinfo.value).lower()
    assert client.messages.stream.call_count == 1


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


def test_analyzer_requests_the_full_128k_output_budget() -> None:
    """Use the model's full 128K output ceiling for a pass.

    Regression for the truncation bug: a full audit's JSON (~22.5 KB ≈ 8000
    tokens) was cut off at max_tokens=8000 and failed to parse. We now
    request the Opus 128K output ceiling (streamed).
    """
    client = _client_returning(
        _fake_message(json.dumps({"recording": "x", "issues": []}))
    )
    analyzer = Analyzer(
        client=client, model="claude-opus-5", extended_context=False
    )
    analyzer.analyze(
        vtt="W", context="audit", kind="issues", language="en", recording_id="X"
    )
    assert client.messages.stream.call_args.kwargs["max_tokens"] == 128000


def test_analyzer_defaults_to_opus_5() -> None:
    """A default-constructed Analyzer targets claude-opus-5."""
    client = _client_returning(
        _fake_message(json.dumps({"recording": "x", "issues": []}))
    )
    analyzer = Analyzer(client=client)  # no explicit model → default
    analyzer.analyze(
        vtt="W", context="audit", kind="issues", language="en", recording_id="X"
    )
    assert client.messages.stream.call_args.kwargs["model"] == "claude-opus-5"


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
