"""Tests for audio/analysis.py — mocked Anthropic SDK."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from auto_a11y.audio.analysis import AnalysisResult, Analyzer


def _fake_response(text: str) -> object:
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
    return SimpleNamespace(content=[block], usage=usage)


def test_analyzer_uses_correct_prompt_for_context() -> None:
    client = MagicMock()
    issues_payload = json.dumps({"recording": "REC-x", "issues": []})
    client.messages.create.return_value = _fake_response(issues_payload)

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

    sent_kwargs = client.messages.create.call_args.kwargs
    messages: list[dict[str, Any]] = sent_kwargs["messages"]
    joined = " ".join(
        m["content"] for m in messages if isinstance(m["content"], str)
    )
    assert "END_OF_PROMPT" in joined  # heuristics + audit_issues both end with the marker


def test_analyzer_french_pass_prepends_french_instruction() -> None:
    client = MagicMock()
    client.messages.create.return_value = _fake_response(
        json.dumps({"recording": "x", "issues": []})
    )
    analyzer = Analyzer(
        client=client, model="claude-opus-4-7", extended_context=False
    )
    analyzer.analyze(
        vtt="W", context="audit", kind="issues", language="fr", recording_id="X"
    )

    messages = client.messages.create.call_args.kwargs["messages"]
    joined = " ".join(
        m["content"] for m in messages if isinstance(m["content"], str)
    )
    # The Analyzer prepends a French-output instruction at the prompt head.
    assert "français" in joined or "French" in joined


def test_analyzer_records_cost_in_result() -> None:
    client = MagicMock()
    client.messages.create.return_value = _fake_response(
        json.dumps({"recording": "x", "issues": []})
    )
    analyzer = Analyzer(
        client=client, model="claude-opus-4-7", extended_context=False
    )
    result = analyzer.analyze(
        vtt="W", context="audit", kind="issues", language="en", recording_id="X"
    )
    # 1000 in × $5/M = $0.005; 200 out × $25/M = $0.005; total $0.01
    assert result.cost_usd > 0
