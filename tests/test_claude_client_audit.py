"""Audit-driven regression tests for ``auto_a11y.ai.claude_client``.

Covers three bugs found in a code audit:

* Bug A: the system prompt was dropped on the streaming (extended-thinking)
  path -- only the non-streaming branch passed ``system=``.
* Bug B: ``_extract_json`` used first-``{`` to last-``}`` slicing, which
  mangled responses containing prose, thinking asides, or a second JSON
  block.
* Bug C: the Anthropic SDK clients were constructed without ``max_retries``,
  so a single transient failure aborted the whole analysis.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, cast

import pytest

from auto_a11y.ai.claude_client import ClaudeClient

# ``_extract_json`` is a protected static method; bind it through a typed
# alias so the tests can exercise it without triggering pyright's
# private-usage diagnostic and without any suppression comment.
_extract_json: Callable[[str], dict[str, Any]] = cast(
    Callable[[str], dict[str, Any]],
    getattr(ClaudeClient, "_extract_json"),
)


# ---------------------------------------------------------------------------
# Minimal async stub for the Anthropic streaming API.
#
# We avoid contacting the network by replacing the client's
# ``async_client.messages.stream`` with a recorder that captures the kwargs
# it was called with and yields no events.
# ---------------------------------------------------------------------------


class _FakeStream:
    """Async context manager that behaves like an empty event stream."""

    async def __aenter__(self) -> "_FakeStream":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def __aiter__(self) -> "_FakeStream":
        return self

    async def __anext__(self) -> object:
        raise StopAsyncIteration


class _RecordingMessages:
    """Records the kwargs passed to ``stream``."""

    def __init__(self) -> None:
        self.stream_kwargs: dict[str, Any] | None = None

    def stream(self, **kwargs: Any) -> _FakeStream:
        self.stream_kwargs = kwargs
        return _FakeStream()


def _make_client() -> ClaudeClient:
    """Build a ClaudeClient with a dummy key and extended thinking enabled."""
    from auto_a11y.ai.claude_client import ClaudeConfig

    config = ClaudeConfig(api_key="dummy-key", use_extended_thinking=True)
    return ClaudeClient(config)


# ---------------------------------------------------------------------------
# Bug A: system prompt must be sent on the streaming path.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_path_sends_system_prompt() -> None:
    client = _make_client()
    recorder = _RecordingMessages()
    # Swap the real messages namespace for our recorder. ``setattr`` keeps the
    # assignment dynamic so strict type checkers do not flag the attribute type
    # (and we avoid any suppression comment).
    setattr(client.async_client, "messages", recorder)

    messages: list[Any] = [{"role": "user", "content": "hi"}]
    # ``_stream_response`` is protected; call it through a typed alias to avoid
    # pyright's private-usage diagnostic without any suppression comment.
    stream_response: Callable[[list[Any]], Awaitable[tuple[str, str | None]]] = cast(
        Callable[[list[Any]], Awaitable[tuple[str, str | None]]],
        getattr(client, "_stream_response"),
    )
    await stream_response(messages)

    assert recorder.stream_kwargs is not None, "stream() was never called"
    assert recorder.stream_kwargs.get("system") == client.system_prompt


# ---------------------------------------------------------------------------
# Bug B: robust JSON extraction.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        # (i) fenced ```json block surrounded by prose with stray braces.
        (
            "Here is my analysis { not json } and the result:\n"
            + "```json\n"
            + '{"errors": [{"code": "ErrNoAlt"}], "ok": true}\n'
            + "```\n"
            + "Hope that helps! {trailing}",
            {"errors": [{"code": "ErrNoAlt"}], "ok": True},
        ),
        # (ii) thinking prose, then a single JSON object (no fence).
        (
            "Let me think about this carefully. The image shows... "
            + 'Final answer: {"findings": [], "count": 0}',
            {"findings": [], "count": 0},
        ),
        # (iii) two JSON objects -- the first complete top-level one wins.
        (
            '{"first": 1, "nested": {"a": 2}}\n'
            + 'and then {"second": 2}',
            {"first": 1, "nested": {"a": 2}},
        ),
        # braces inside string literals must not confuse the scanner.
        (
            'prose {"msg": "a } brace { in a string", "n": 1} tail',
            {"msg": "a } brace { in a string", "n": 1},
        ),
    ],
)
def test_extract_json_returns_correct_object(text: str, expected: dict[str, Any]) -> None:
    result = _extract_json(text)
    assert result == expected
    assert "raw_response" not in result


def test_extract_json_falls_back_when_unparseable() -> None:
    text = "No JSON here at all, just prose."
    result = _extract_json(text)
    assert result == {"raw_response": text}


def test_extract_json_falls_back_on_broken_object() -> None:
    # An opening brace with no matching close and no valid content.
    text = "broken { not: valid json at all"
    result = _extract_json(text)
    assert result == {"raw_response": text}


# ---------------------------------------------------------------------------
# Bug C: SDK clients must be constructed with max_retries.
# ---------------------------------------------------------------------------


def test_clients_constructed_with_max_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    import auto_a11y.ai.claude_client as mod

    captured: list[dict[str, Any]] = []

    class _FakeAnthropic:
        def __init__(self, **kwargs: Any) -> None:
            captured.append(kwargs)

    monkeypatch.setattr(mod, "AsyncAnthropic", _FakeAnthropic)
    monkeypatch.setattr(mod, "Anthropic", _FakeAnthropic)

    config = mod.ClaudeConfig(api_key="dummy-key")
    mod.ClaudeClient(config)

    assert len(captured) == 2, "expected both async and sync clients constructed"
    for kwargs in captured:
        assert "max_retries" in kwargs, f"max_retries missing from {kwargs.keys()}"
        assert isinstance(kwargs["max_retries"], int)
        assert kwargs["max_retries"] >= 1
