"""
Claude AI client wrapper for API communication
"""

from __future__ import annotations

import logging
import base64
import json
import re
from typing import Any, ClassVar, Literal, cast
from dataclasses import dataclass
import asyncio
from anthropic import AsyncAnthropic, Anthropic
from anthropic.types import (
    ImageBlockParam,
    MessageParam,
    RawContentBlockDeltaEvent,
    TextBlockParam,
    TextDelta,
    ThinkingDelta,
)

# Type alias for image MIME types accepted by the Anthropic API.
_ImageMediaType = Literal["image/jpeg", "image/png", "image/gif", "image/webp"]

logger = logging.getLogger(__name__)


@dataclass
class ClaudeConfig:
    """Claude AI configuration"""
    api_key: str
    model: str = "claude-opus-5"  # Opus 5 with adaptive thinking
    max_tokens: int = 16000
    # budget_tokens / temperature are retained for backward compatibility but are no
    # longer sent: Opus 4.7+/4.8 use adaptive thinking and reject both a fixed thinking
    # budget and any sampling parameter (temperature/top_p/top_k) with a 400.
    budget_tokens: int = 5000
    temperature: float = 1.0
    timeout: int = 120
    use_extended_thinking: bool = True

    # Available models (updated to latest versions)
    MODELS: ClassVar[dict[str, str]] = {
        'opus-5': 'claude-opus-5',                        # Opus 5 (default; adaptive thinking)
        'sonnet-5': 'claude-sonnet-5',                    # Sonnet 5
        'haiku-4.5': 'claude-haiku-4-5',                  # Haiku 4.5 (fastest current)
        'opus-4.8': 'claude-opus-4-8',                    # Opus 4.8
        'opus-4': 'claude-opus-4-20250514',               # Opus 4 (deprecated)
        'sonnet-4': 'claude-sonnet-4-20250514',           # Sonnet 4
        'sonnet-4.5': 'claude-sonnet-4-5-20250929',       # Sonnet 4.5
        'sonnet-3.5': 'claude-3-5-sonnet-20241022',       # Sonnet 3.5
        'haiku-3.5': 'claude-3-5-haiku-20241022',         # Haiku 3.5 (fastest)
        # Legacy models
        'opus-3': 'claude-3-opus-20240229',               # Opus 3
        'sonnet-3': 'claude-3-sonnet-20240229',           # Older Sonnet
        'haiku-3': 'claude-3-haiku-20240307'              # Older Haiku
    }


class ClaudeClient:
    """Wrapper for Claude AI API client"""

    def __init__(self, config: ClaudeConfig) -> None:
        """
        Initialize Claude client

        Args:
            config: Claude configuration
        """
        self.config: ClaudeConfig = config
        # Prompt caching is still a beta header; interleaved-thinking and output-128k are
        # no longer needed on Opus 4.7+/4.8 (adaptive thinking interleaves automatically,
        # and 128k output is built in), so they are dropped to avoid sending stale betas.
        beta_features = "prompt-caching-2024-07-31"
        # Let the SDK transparently retry transient failures (429/5xx/timeouts)
        # with exponential backoff so a single blip does not abort an analysis.
        max_retries: int = getattr(config, "max_retries", 3)
        self.async_client: AsyncAnthropic = AsyncAnthropic(
            api_key=config.api_key,
            default_headers={"anthropic-beta": beta_features},
            max_retries=max_retries,
        )
        self.sync_client: Anthropic = Anthropic(
            api_key=config.api_key,
            default_headers={"anthropic-beta": beta_features},
            max_retries=max_retries,
        )
        self.system_prompt: str = self._get_system_prompt()

        logger.info(f"Claude client initialized with model: {config.model}, beta features: {beta_features}")

    def _get_system_prompt(self) -> str:
        """Get system prompt for accessibility analysis"""
        return """You are a pedantic and thoughtful assistant focused on digital
        accessibility concerns. You check results in as many ways as possible for
        accuracy. You are an expert in WCAG 2.1 Level A, AA, and AAA guidelines
        and modern web accessibility best practices.

        When analyzing web pages:
        1. Be precise about accessibility issues
        2. Reference specific WCAG criteria when applicable
        3. Provide actionable recommendations
        4. Consider different disability types (visual, motor, cognitive, auditory)
        5. Focus on barriers that prevent users from accessing content or functionality
        6. Always respond with valid JSON when requested"""

    async def _stream_response(
        self,
        messages: list[MessageParam],
        *,
        collect_thinking: bool = False,
    ) -> tuple[str, str | None]:
        """
        Send a streaming request to the Claude API.

        Builds thinking / system params from ``self.config``
        and streams the response, returning the concatenated text output
        and (optionally) the extended-thinking trace.

        Returns:
            ``(response_text, thinking_text)`` where *thinking_text* is
            ``None`` when extended thinking is disabled or produced no output.
        """
        collected_text: list[str] = []
        collected_thinking: list[str] = []

        if self.config.use_extended_thinking:
            async with self.async_client.messages.stream(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                messages=messages,
                system=self.system_prompt,
                # Adaptive thinking: Opus 4.7+/4.8 reject the old
                # {"type": "enabled", "budget_tokens": N} form and any sampling
                # parameter (temperature/top_p/top_k) with a 400.
                thinking={"type": "adaptive"},
            ) as stream:
                async for event in stream:
                    if isinstance(event, RawContentBlockDeltaEvent):
                        delta = event.delta
                        if collect_thinking and isinstance(delta, ThinkingDelta):
                            collected_thinking.append(delta.thinking)
                        elif isinstance(delta, TextDelta):
                            collected_text.append(delta.text)

            response_text = ''.join(collected_text)
            thinking_text = ''.join(collected_thinking) if collected_thinking else None
        else:
            message = await self.async_client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                messages=messages,
                system=self.system_prompt,
            )
            response_text = ""
            thinking_text = None
            for block in message.content:
                if hasattr(block, 'text') and block.type == "text":
                    response_text = str(block.text)
                elif hasattr(block, 'thinking') and block.type == "thinking":
                    thinking_text = str(block.thinking)

        return response_text, thinking_text

    @staticmethod
    def _scan_balanced_objects(text: str) -> list[str]:
        """Return every top-level ``{...}`` substring in *text*.

        Performs a single left-to-right pass tracking brace depth while
        respecting JSON string literals (so braces inside strings, and
        escaped quotes, are ignored). Each complete top-level object is
        captured in source order; unterminated trailing objects are skipped.
        """
        objects: list[str] = []
        depth = 0
        start = -1
        in_string = False
        escaped = False

        for index, char in enumerate(text):
            if in_string:
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == '{':
                if depth == 0:
                    start = index
                depth += 1
            elif char == '}':
                if depth > 0:
                    depth -= 1
                    if depth == 0 and start != -1:
                        objects.append(text[start:index + 1])
                        start = -1

        return objects

    @staticmethod
    def _extract_json(response_text: str) -> dict[str, Any]:
        """Extract the first usable JSON object from *response_text*.

        Strategy (in order):

        1. Look for a fenced ```json ... ``` (or bare ``` ... ```) code block
           and parse its contents.
        2. Otherwise, scan for the first complete top-level ``{...}`` object
           using a balanced-brace scanner that respects string literals, and
           parse the first one that decodes to a JSON object.

        Falls back to ``{'raw_response': response_text}`` when no valid JSON
        object can be found, preserving the original return contract.
        """
        candidates: list[str] = []

        # (1) Fenced code blocks first -- the model is instructed to emit JSON,
        # and a fence is the strongest signal of where it lives.
        fence_pattern = re.compile(
            r"```(?:json)?\s*(.*?)```",
            re.IGNORECASE | re.DOTALL,
        )
        for fenced in fence_pattern.findall(response_text):
            candidates.extend(ClaudeClient._scan_balanced_objects(fenced))

        # (2) Balanced-brace scan over the response with fenced regions removed.
        # De-fencing first means a malformed fence body (unbalanced or junk
        # braces) can no longer leave the scanner stuck at depth > 0 and
        # swallow a valid object that appears after the fence. This covers the
        # no-fence case and any objects in the prose outside fences.
        defenced = fence_pattern.sub(" ", response_text)
        candidates.extend(ClaudeClient._scan_balanced_objects(defenced))

        for candidate in candidates:
            try:
                parsed: object = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                # ``json.loads`` returns an object with unknown key/value types;
                # JSON object keys are always strings, so rebuild as the
                # ``dict[str, Any]`` the contract promises.
                result: dict[str, Any] = {
                    str(key): value
                    for key, value in cast("dict[object, object]", parsed).items()
                }
                return result

        logger.warning("Failed to parse JSON from Claude response")
        return {'raw_response': response_text}

    @staticmethod
    def _detect_image_format(image_data: bytes, fallback: str) -> _ImageMediaType:
        """Return the MIME type for *image_data* based on magic bytes."""
        if image_data[:8] == b'\x89PNG\r\n\x1a\n':
            return "image/png"
        if image_data[:2] == b'\xff\xd8':
            return "image/jpeg"
        # Fallback may be any string; cast to the literal union expected by the API.
        return cast(_ImageMediaType, fallback)

    async def analyze_with_image(
        self,
        image_data: bytes,
        prompt: str,
        image_format: str = "image/jpeg",
    ) -> dict[str, Any]:
        """
        Analyze an image with a text prompt

        Args:
            image_data: Image bytes
            prompt: Analysis prompt
            image_format: MIME type of image

        Returns:
            Analysis results as dictionary
        """
        try:
            actual_format = self._detect_image_format(image_data, image_format)
            image_base64 = base64.b64encode(image_data).decode('utf-8')

            image_block = ImageBlockParam(
                type="image",
                source={"type": "base64", "media_type": actual_format, "data": image_base64},
                cache_control={"type": "ephemeral"},
            )
            text_block = TextBlockParam(type="text", text=prompt)

            messages: list[MessageParam] = [{
                "role": "user",
                "content": [image_block, text_block],
            }]

            response_text, _ = await self._stream_response(messages)
            return self._extract_json(response_text)

        except Exception as e:
            logger.error(f"Claude API error: {e}")
            raise

    async def analyze_html(
        self,
        html: str,
        prompt: str,
    ) -> dict[str, Any]:
        """
        Analyze HTML content

        Args:
            html: HTML content
            prompt: Analysis prompt

        Returns:
            Analysis results
        """
        try:
            messages: list[MessageParam] = [{
                "role": "user",
                "content": f"{prompt}\n\nHTML:\n{html}",
            }]

            response_text, _ = await self._stream_response(messages)
            return self._extract_json(response_text)

        except Exception as e:
            logger.error(f"Claude API error: {e}")
            raise

    async def analyze_with_image_and_html(
        self,
        image_data: bytes,
        html: str,
        prompt: str,
        image_format: str = "image/jpeg",
    ) -> dict[str, Any]:
        """
        Analyze both image and HTML content together

        Args:
            image_data: Screenshot bytes
            html: HTML content
            prompt: Analysis prompt
            image_format: MIME type

        Returns:
            Analysis results
        """
        try:
            actual_format = self._detect_image_format(image_data, image_format)
            if actual_format == "image/png":
                logger.debug("Detected PNG image format")
            elif actual_format == "image/jpeg":
                logger.debug("Detected JPEG image format")
            else:
                logger.debug(f"Using provided format: {image_format}")

            image_base64 = base64.b64encode(image_data).decode('utf-8')

            image_block = ImageBlockParam(
                type="image",
                source={"type": "base64", "media_type": actual_format, "data": image_base64},
                cache_control={"type": "ephemeral"},
            )
            html_block = TextBlockParam(
                type="text",
                text=f"HTML Content:\n{html}",
                cache_control={"type": "ephemeral"},
            )
            prompt_block = TextBlockParam(type="text", text=prompt)

            messages: list[MessageParam] = [{
                "role": "user",
                "content": [image_block, html_block, prompt_block],
            }]

            if self.config.use_extended_thinking:
                logger.warning(
                    f"Using adaptive thinking - max_tokens: {self.config.max_tokens}"
                )

            response_text, thinking_text = await self._stream_response(
                messages, collect_thinking=True,
            )

            # DEBUG: Save raw response to file
            import os
            from datetime import datetime
            debug_dir = "ai_debug"
            if not os.path.exists(debug_dir):
                os.makedirs(debug_dir)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            # Save the prompt and response
            with open(f"{debug_dir}/ai_response_{timestamp}.txt", "w") as f:
                f.write("=" * 60 + "\n")
                f.write("PROMPT:\n")
                f.write("=" * 60 + "\n")
                f.write(prompt + "\n")
                f.write("=" * 60 + "\n")
                f.write(f"HTML length: {len(html)} chars (cached)\n")
                f.write(f"Image size: {len(image_data)} bytes (cached)\n")
                if thinking_text:
                    f.write("=" * 60 + "\n")
                    f.write("THINKING:\n")
                    f.write("=" * 60 + "\n")
                    f.write(thinking_text + "\n")
                f.write("=" * 60 + "\n")
                f.write("RAW RESPONSE:\n")
                f.write("=" * 60 + "\n")
                f.write(response_text + "\n")

            logger.info(f"DEBUG: Saved AI response to ai_debug/ai_response_{timestamp}.txt")

            return self._extract_json(response_text)

        except Exception as e:
            logger.error(f"Claude API error: {e}")
            raise

    async def aclose(self) -> None:
        """Close the async client properly"""
        try:
            await self.async_client.close()
        except Exception as e:
            logger.debug(f"Error closing Claude client: {e}")

    def get_token_estimate(self, text: str) -> int:
        """
        Estimate token count for text

        Args:
            text: Text to estimate

        Returns:
            Estimated token count
        """
        # Rough estimate: 1 token ~ 4 characters
        return len(text) // 4

    async def batch_analyze(
        self,
        analyses: list[dict[str, Any]],
        max_concurrent: int = 3,
    ) -> list[dict[str, Any]]:
        """
        Run multiple analyses in parallel with rate limiting

        Args:
            analyses: List of analysis requests
            max_concurrent: Maximum concurrent requests

        Returns:
            List of results
        """
        semaphore = asyncio.Semaphore(max_concurrent)

        async def run_with_limit(analysis: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                if 'image' in analysis:
                    return await self.analyze_with_image(
                        analysis['image'],
                        analysis['prompt'],
                    )
                else:
                    return await self.analyze_html(
                        analysis['html'],
                        analysis['prompt'],
                    )

        tasks = [run_with_limit(a) for a in analyses]
        raw_results: list[dict[str, Any] | BaseException] = await asyncio.gather(
            *tasks, return_exceptions=True,
        )

        # Convert BaseException entries to error dicts so callers always get
        # list[dict[str, Any]] back.
        results: list[dict[str, Any]] = []
        for r in raw_results:
            if isinstance(r, BaseException):
                logger.error(f"Batch analysis task failed: {r}")
                results.append({'error': str(r)})
            else:
                results.append(r)
        return results
