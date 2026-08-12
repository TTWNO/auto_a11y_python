"""Claude client plumbing shared by the PDF AI analyses.

One place that knows how to talk to the API, so the analysis modules carry
prompts and result-shaping and nothing else.

Two things differ deliberately from pdfMax's calls:

* **Model.** pdfMax pins every call to ``claude-sonnet-4-5-20250929``. This
  port defaults to ``claude-opus-5`` and reads ``CLAUDE_MODEL`` so the app's
  configured model wins.
* **Thinking.** Thinking is on by default on Opus 5, and disabling it is only
  valid at effort ``high`` or below. Rather than disable it for the cheap
  calls, they run at ``effort: "low"`` — that keeps cost down without the
  documented failure modes of a thinking-disabled request (internal tags
  leaking into a plain-text reply).

``max_tokens`` bounds thinking *and* the reply together, so every budget here
is larger than pdfMax's equivalent — its numbers were sized for a model that
did no thinking, and reusing them truncates the answer.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Literal, cast

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5"

Effort = Literal["low", "medium", "high", "xhigh", "max"]

# Above this budget the SDK's non-streaming path risks an HTTP timeout, so
# the call streams and reassembles instead.
_STREAM_ABOVE_MAX_TOKENS = 8000


class AIUnavailable(RuntimeError):
    """Raised when AI analysis was requested but cannot run.

    Carries the reason so the caller can tell the user *why* rather than
    silently producing an audit with no AI section.
    """


@dataclass
class Usage:
    """Token telemetry accumulated across every call in one audit."""

    cached_input_tokens: int = 0
    uncached_input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def add(self, usage: object) -> None:
        """Fold one response's ``usage`` block into the running total."""
        self.calls += 1
        self.cached_input_tokens += _int_attr(usage, "cache_read_input_tokens")
        self.uncached_input_tokens += _int_attr(usage, "input_tokens")
        self.uncached_input_tokens += _int_attr(
            usage, "cache_creation_input_tokens"
        )
        self.output_tokens += _int_attr(usage, "output_tokens")


@dataclass
class AIClient:
    """Thin wrapper over the Anthropic SDK for the PDF analyses."""

    api_key: str | None = None
    model: str = DEFAULT_MODEL
    usage: Usage = field(default_factory=Usage)
    _client: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        try:
            from anthropic import Anthropic
        except ImportError as exc:  # pragma: no cover — dependency is pinned
            raise AIUnavailable(
                "The anthropic package is not installed, so AI analysis cannot run."
            ) from exc

        key = self.api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get(
            "CLAUDE_API_KEY"
        )
        if not key:
            raise AIUnavailable(
                "No Claude API key is configured, so AI analysis cannot run."
            )
        self._client = Anthropic(api_key=key)

    # -- calls ---------------------------------------------------------

    def json_call(
        self,
        content: list[dict[str, Any]],
        *,
        schema: dict[str, Any],
        max_tokens: int,
        effort: Effort | None = None,
    ) -> dict[str, Any] | None:
        """Run one structured-output call and return the parsed object.

        Returns ``None`` when the call fails or the model declines — one
        failed analysis must not end the audit, and the caller reports the
        gap rather than presenting a partial result as complete.
        """
        message = self._send(
            content, max_tokens=max_tokens, effort=effort, schema=schema
        )
        if message is None:
            return None
        text = _first_text(message)
        if not text:
            return None
        try:
            parsed: object = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("AI returned unparseable JSON; skipping this analysis")
            return None
        if not isinstance(parsed, dict):
            return None
        return cast("dict[str, Any]", parsed)

    def _send(
        self,
        content: list[dict[str, Any]],
        *,
        max_tokens: int,
        effort: Effort | None,
        schema: dict[str, Any] | None,
    ) -> object | None:
        output_config: dict[str, Any] = {}
        if effort is not None:
            output_config["effort"] = effort
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": content}],
        }
        if output_config:
            kwargs["output_config"] = output_config

        try:
            if max_tokens > _STREAM_ABOVE_MAX_TOKENS:
                with self._client.messages.stream(**kwargs) as stream:
                    message = stream.get_final_message()
            else:
                message = self._client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 — one failed call is not fatal
            logger.warning("Claude call failed: %s: %s", type(exc).__name__, exc)
            return None

        self.usage.add(getattr(message, "usage", None))

        # A refusal returns HTTP 200 with an empty or partial body. Reading
        # content[0] unconditionally would raise or, worse, treat a refusal
        # as an answer.
        if getattr(message, "stop_reason", None) == "refusal":
            logger.warning(
                "Claude declined this analysis (%s)",
                getattr(getattr(message, "stop_details", None), "category", "unknown"),
            )
            return None
        return cast("object", message)


# ---------------------------------------------------------------------------
# Content-block helpers
# ---------------------------------------------------------------------------


def image_block(png_bytes: bytes) -> dict[str, Any]:
    """A base64 PNG image content block."""
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(png_bytes).decode("ascii"),
        },
    }


def text_block(text: str) -> dict[str, Any]:
    """A plain text content block."""
    return {"type": "text", "text": text}


def _first_text(message: object) -> str | None:
    """The first text block's content, or None if the reply carried none."""
    blocks = getattr(message, "content", None)
    if not isinstance(blocks, list):
        return None
    for block in cast("list[object]", blocks):
        if getattr(block, "type", None) == "text":
            value = getattr(block, "text", None)
            if isinstance(value, str):
                return value
    return None


def _int_attr(source: object, name: str) -> int:
    value = getattr(source, name, 0)
    return value if isinstance(value, int) else 0
