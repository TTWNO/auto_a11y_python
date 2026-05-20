"""Claude-based analysis of a VTT transcript.

Ported from ``pythonAudioA11y/analysis.py`` with adjustments:

- Prompts loaded from ``auto_a11y/audio/prompts/*.txt`` (separate files,
  snapshot-tested) rather than inlined in source.
- Cost recorded per call via ``auto_a11y/audio/cost.py``.
- Anthropic SDK kwargs parameterised via ``AudioConfig``.
- French output requested at runtime via a prompt prefix; the prompt
  files themselves are English-only.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal, TypeGuard

from auto_a11y.audio.cost import AnthropicUsage, cost_for_anthropic_call
from auto_a11y.audio.errors import AnalysisError
from auto_a11y.audio.prompts import Context, Kind, load as load_prompt, load_heuristics

logger = logging.getLogger(__name__)


Language = Literal["en", "fr"]


_FRENCH_PREFIX = (
    "IMPORTANT: Produce ALL output in French (français). All field values, "
    "all natural-language content, in French.\n\n"
)


def _is_str_obj_dict(val: object) -> TypeGuard[dict[str, object]]:
    """Narrow a ``json.loads`` result to ``dict[str, object]``.

    ``json.loads`` always produces ``str``-keyed dicts for JSON objects;
    the runtime check is ``isinstance(val, dict)`` only. Matches the
    same helper in ``auto_a11y/audio/speaker_identification.py``.
    """
    return isinstance(val, dict)


@dataclass(frozen=True)
class AnalysisResult:
    json_payload: dict[str, object]
    html_text: str | None
    cost_usd: float
    input_tokens: int
    output_tokens: int


class Analyzer:
    """Wraps a single Claude analyze() call per (context, kind, language).

    The ``client`` is the Anthropic SDK client. ``: Any`` per CLAUDE.md
    is permitted at SDK boundaries — the SDK response is a typed union
    that's awkward to nominally satisfy in tests via ``MagicMock``.
    """

    def __init__(
        self,
        *,
        client: Any,
        model: str = "claude-opus-4-7",
        extended_context: bool = False,
    ) -> None:
        self._client = client
        self._model = model
        self._extended_context = extended_context

    def analyze(
        self,
        *,
        vtt: str,
        context: Context,
        kind: Kind,
        language: Language,
        recording_id: str,
    ) -> AnalysisResult:
        """Run one analyze pass and return the parsed result + cost."""
        prompt_body = load_prompt(context, kind)
        heuristics = load_heuristics()
        prefix = "" if language == "en" else _FRENCH_PREFIX
        full_prompt = (
            prefix + heuristics + "\n\n" + prompt_body + "\n\nTRANSCRIPT:\n" + vtt
        )

        betas = ["prompt-caching-2024-07-31"]
        if self._extended_context:
            betas.append("output-128k-2025-02-19")

        message = self._client.messages.create(
            model=self._model,
            max_tokens=8000,
            messages=[{"role": "user", "content": full_prompt}],
            extra_headers={"anthropic-beta": ",".join(betas)},
        )

        # Extract content text. Anthropic SDK returns a list of content
        # blocks; only text-bearing blocks contribute to the payload.
        text_blocks: list[str] = []
        for block in message.content:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                text_blocks.append(text)
        raw = "".join(text_blocks)

        json_payload = self._extract_json(raw, recording_id)

        usage = AnthropicUsage(
            input_tokens=int(getattr(message.usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(message.usage, "output_tokens", 0) or 0),
            cache_read_tokens=int(
                getattr(message.usage, "cache_read_input_tokens", 0) or 0
            ),
            cache_write_tokens=int(
                getattr(message.usage, "cache_creation_input_tokens", 0) or 0
            ),
        )
        cost = cost_for_anthropic_call(
            usage, model=self._model, extended_context=self._extended_context,
        )

        return AnalysisResult(
            json_payload=json_payload,
            html_text=None,
            cost_usd=cost,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )

    @staticmethod
    def _extract_json(text: str, recording_id: str) -> dict[str, object]:
        """Find the JSON object in the response.

        Prompts instruct Claude to emit either a raw JSON object or one
        inside a ```json … ``` code fence. Try the fence first; fall back
        to the outermost ``{ … }``.
        """
        fence = re.search(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
        if fence:
            payload = fence.group(1)
        else:
            first = text.find("{")
            last = text.rfind("}")
            if first == -1 or last == -1 or last <= first:
                raise AnalysisError(
                    f"No JSON object in response for recording {recording_id}"
                )
            payload = text[first : last + 1]
        try:
            raw: object = json.loads(payload)
        except json.JSONDecodeError as e:
            raise AnalysisError(
                f"Failed to parse JSON for recording {recording_id}: {e}"
            ) from e
        if not _is_str_obj_dict(raw):
            raise AnalysisError(
                f"JSON payload for {recording_id} is not a JSON object (got {type(raw).__name__})"
            )
        return raw
