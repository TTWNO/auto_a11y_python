"""Tests for the ported Claude analysis passes.

No test here calls the API. Every pass goes through
:class:`~auto_a11y.pdf.audit.ai.client.AIClient`, so a fake Anthropic client
lets the tests assert on the two things that actually matter and cannot be
checked by reading the code: that a request carries what the prompt needs
(the schema, the page image, the right budget), and that a *bad* response —
refusal, malformed JSON, an exception — degrades to "no result" instead of
raising into the audit.

The candidate finders and the visual-reference scanner are deterministic and
are tested directly.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pikepdf
import pytest

from auto_a11y.pdf.audit.ai import client as ai_client
from auto_a11y.pdf.audit.ai.client import AIClient, AIUnavailable
from auto_a11y.pdf.audit.ai.schemas import SEMANTIC_ANALYSIS_SCHEMA
from auto_a11y.pdf.audit.ai.semantic import analyze_semantics, severity_counts
from auto_a11y.pdf.audit.ai.summary import detect_language
from auto_a11y.pdf.audit.ai.visual_references import scan_visual_references
from auto_a11y.pdf.audit.candidates import (
    find_heading_candidates,
    find_list_candidates,
)
from auto_a11y.pdf.audit.fonts import FontAnalysis, FontInfo
from auto_a11y.pdf.audit.structure import StructElement


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _text_block(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _message(
    text: str = "{}",
    *,
    stop_reason: str = "end_turn",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read: int = 0,
) -> MagicMock:
    message = MagicMock()
    message.content = [_text_block(text)]
    message.stop_reason = stop_reason
    message.usage.input_tokens = input_tokens
    message.usage.output_tokens = output_tokens
    message.usage.cache_read_input_tokens = cache_read
    message.usage.cache_creation_input_tokens = 0
    return message


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> AIClient:
    """An AIClient whose SDK calls are captured rather than sent."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    client = AIClient(model="claude-opus-5")
    client._client = MagicMock()  # noqa: SLF001 — the point of the fixture
    client._client.messages.create.return_value = _message()
    return client


def _element(
    index: int,
    tag: str,
    *,
    text: str = "",
    parent: int = -1,
    children: list[int] | None = None,
    mcids: list[int] | None = None,
    alt: str | None = None,
    actual: str | None = None,
) -> StructElement:
    return StructElement(
        index=index,
        custom_tag=f"/{tag}",
        resolved_tag=tag,
        alt_text=alt,
        actual_text=actual,
        lang=None,
        children_indices=children or [],
        mcids=mcids if mcids is not None else [index],
        parent_index=parent,
        # The walker attaches the real dictionary; nothing under test reads it.
        obj=pikepdf.Dictionary(),
        text_content=text,
    )


# ---------------------------------------------------------------------------
# Client behaviour — the failure paths
# ---------------------------------------------------------------------------


def test_missing_api_key_raises_rather_than_silently_skipping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit opt-in with no key must be reported, not swallowed.

    Returning a clean result here would tell the user the AI examined the
    document and found nothing.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    with pytest.raises(AIUnavailable, match="API key"):
        AIClient()


def test_refusal_returns_none_instead_of_reading_content(
    fake_client: AIClient,
) -> None:
    """A refusal is HTTP 200 with empty content — indexing it would raise."""
    refusal = _message(stop_reason="refusal")
    refusal.content = []
    fake_client._client.messages.create.return_value = refusal  # noqa: SLF001

    result = fake_client.json_call(
        [ai_client.text_block("hi")],
        schema=SEMANTIC_ANALYSIS_SCHEMA,
        max_tokens=1000,
    )
    assert result is None


def test_malformed_json_returns_none(fake_client: AIClient) -> None:
    fake_client._client.messages.create.return_value = _message(  # noqa: SLF001
        "not json at all"
    )
    result = fake_client.json_call(
        [ai_client.text_block("hi")],
        schema=SEMANTIC_ANALYSIS_SCHEMA,
        max_tokens=1000,
    )
    assert result is None


def test_api_exception_returns_none(fake_client: AIClient) -> None:
    """One failed call costs its own analysis, not the audit."""
    fake_client._client.messages.create.side_effect = RuntimeError("boom")  # noqa: SLF001
    result = fake_client.json_call(
        [ai_client.text_block("hi")],
        schema=SEMANTIC_ANALYSIS_SCHEMA,
        max_tokens=1000,
    )
    assert result is None


def test_usage_accumulates_across_calls(fake_client: AIClient) -> None:
    for _ in range(3):
        fake_client.json_call(
            [ai_client.text_block("hi")],
            schema=SEMANTIC_ANALYSIS_SCHEMA,
            max_tokens=1000,
        )
    assert fake_client.usage.calls == 3
    assert fake_client.usage.uncached_input_tokens == 300
    assert fake_client.usage.output_tokens == 150


def test_large_budgets_stream_to_avoid_http_timeouts(
    fake_client: AIClient,
) -> None:
    """Above the streaming threshold the SDK's non-streaming path can time out."""
    stream_ctx = MagicMock()
    stream_ctx.__enter__.return_value.get_final_message.return_value = _message()
    fake_client._client.messages.stream.return_value = stream_ctx  # noqa: SLF001

    fake_client.json_call(
        [ai_client.text_block("hi")],
        schema=SEMANTIC_ANALYSIS_SCHEMA,
        max_tokens=16000,
    )
    assert fake_client._client.messages.stream.called  # noqa: SLF001
    assert not fake_client._client.messages.create.called  # noqa: SLF001


# ---------------------------------------------------------------------------
# Semantic pass
# ---------------------------------------------------------------------------


def test_semantic_call_sends_schema_image_and_effort(
    fake_client: AIClient, tmp_path: Path
) -> None:
    stream_ctx = MagicMock()
    stream_ctx.__enter__.return_value.get_final_message.return_value = _message(
        json.dumps({
            "issues": [],
            "overall_assessment": "fine",
            "unmarked_headings": [],
            "potential_lists": [],
        })
    )
    fake_client._client.messages.stream.return_value = stream_ctx  # noqa: SLF001

    result = analyze_semantics(
        fake_client,
        pdf_path=tmp_path / "doc.pdf",
        elements=[_element(0, "P", text="Hello")],
        page_image=b"\x89PNG-fake",
        tag_tree_text="tree",
        reading_order_text="order",
        heading_map_text="headings",
        full_alt_text="alts",
        form_inventory_text="",
        doc_lang="en",
        heading_candidates=[],
        list_candidates=[],
    )
    assert result is not None

    kwargs = fake_client._client.messages.stream.call_args.kwargs  # noqa: SLF001
    assert kwargs["model"] == "claude-opus-5"
    assert kwargs["output_config"]["format"]["schema"] is SEMANTIC_ANALYSIS_SCHEMA
    assert kwargs["output_config"]["effort"] == "high"

    blocks = kwargs["messages"][0]["content"]
    assert blocks[0]["type"] == "image", "the page render must reach the model"
    assert "Hello" in blocks[1]["text"], "element text must reach the prompt"


def test_semantic_prompt_asks_for_empty_arrays_when_no_candidates(
    fake_client: AIClient, tmp_path: Path
) -> None:
    """Without the explicit instruction the model invents candidates."""
    stream_ctx = MagicMock()
    stream_ctx.__enter__.return_value.get_final_message.return_value = _message("{}")
    fake_client._client.messages.stream.return_value = stream_ctx  # noqa: SLF001

    analyze_semantics(
        fake_client,
        pdf_path=tmp_path / "doc.pdf",
        elements=[],
        page_image=None,
        tag_tree_text="",
        reading_order_text="",
        heading_map_text="",
        full_alt_text="",
        form_inventory_text="",
        doc_lang=None,
        heading_candidates=[],
        list_candidates=[],
    )
    prompt = fake_client._client.messages.stream.call_args.kwargs[  # noqa: SLF001
        "messages"
    ][0]["content"][0]["text"]
    assert 'empty "unmarked_headings" array' in prompt
    assert 'empty "potential_lists" array' in prompt


def test_severity_counts_ignores_unknown_severities() -> None:
    """An out-of-schema severity is a bug upstream, not an advisory finding."""
    analysis: dict[str, Any] = {
        "issues": [
            {"severity": "critical"},
            {"severity": "critical"},
            {"severity": "important"},
            {"severity": "nonsense"},
        ]
    }
    assert severity_counts(analysis) == {
        "critical": 2, "important": 1, "advisory": 0,
    }


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("fr", "fr"),
        ("FR", "fr"),
        ("french", "fr"),   # first two chars, per the original
        ("x", None),        # too short to be ISO 639-1
        ("123", None),      # not letters
    ],
)
def test_language_detection_validates_the_code(
    fake_client: AIClient, reply: str, expected: str | None
) -> None:
    fake_client._client.messages.create.return_value = _message(  # noqa: SLF001
        json.dumps({"language_code": reply, "confidence": "high"})
    )
    assert detect_language(fake_client, "Bonjour le monde") == expected


def test_language_detection_skips_empty_text(fake_client: AIClient) -> None:
    """No text means nothing to classify — don't pay for the call."""
    assert detect_language(fake_client, "   ") is None
    assert not fake_client._client.messages.create.called  # noqa: SLF001


# ---------------------------------------------------------------------------
# Heading candidates
# ---------------------------------------------------------------------------


def _font_analysis(*fonts: FontInfo) -> FontAnalysis:
    return FontAnalysis(
        fonts={f.name: f for f in fonts},
        rotations=[], italic_runs=[], line_spacings=[], alignments=[],
    )


def test_heading_candidates_need_font_data() -> None:
    """Without type sizes there is no signal; guessing produced false positives."""
    assert find_heading_candidates([_element(0, "P", text="Title")], None) == []


def test_heading_candidate_found_for_larger_text() -> None:
    fonts = _font_analysis(
        FontInfo(name="Body", sample="body text here", sizes={10.0}, char_count=5000),
        FontInfo(name="Big", sample="chapter one", sizes={18.0}, char_count=50),
    )
    elements = [_element(0, "P", text="Chapter One")]
    candidates = find_heading_candidates(elements, fonts)
    assert len(candidates) == 1
    assert candidates[0].index == 0
    assert "18pt" in candidates[0].reason


def test_existing_headings_and_containers_are_not_candidates() -> None:
    fonts = _font_analysis(
        FontInfo(name="Body", sample="body text here", sizes={10.0}, char_count=5000),
        FontInfo(name="Big", sample="chapter one", sizes={18.0}, char_count=50),
    )
    # Already a heading, and a table cell — neither should be promoted.
    elements = [
        _element(0, "H1", text="Chapter One"),
        _element(1, "TD", text="Chapter One"),
    ]
    assert find_heading_candidates(elements, fonts) == []


# ---------------------------------------------------------------------------
# List candidates
# ---------------------------------------------------------------------------


def test_consecutive_bulleted_paragraphs_are_a_candidate() -> None:
    elements = [
        _element(0, "Sect", children=[1, 2, 3], mcids=[]),
        _element(1, "P", text="• First", parent=0),
        _element(2, "P", text="• Second", parent=0),
        _element(3, "P", text="• Third", parent=0),
    ]
    groups = find_list_candidates(elements)
    assert len(groups) == 1
    assert groups[0].pattern == "bullet"
    assert [e.index for e in groups[0].elements] == [1, 2, 3]


def test_a_single_marker_paragraph_is_not_a_list() -> None:
    elements = [
        _element(0, "Sect", children=[1, 2], mcids=[]),
        _element(1, "P", text="• Lonely", parent=0),
        _element(2, "P", text="Ordinary prose", parent=0),
    ]
    assert find_list_candidates(elements) == []


def test_changing_marker_style_splits_the_run() -> None:
    """A bulleted run followed by a numbered run is two lists, not one."""
    elements = [
        _element(0, "Sect", children=[1, 2, 3, 4], mcids=[]),
        _element(1, "P", text="• A", parent=0),
        _element(2, "P", text="• B", parent=0),
        _element(3, "P", text="1. One", parent=0),
        _element(4, "P", text="2. Two", parent=0),
    ]
    groups = find_list_candidates(elements)
    assert [g.pattern for g in groups] == ["bullet", "numbered"]


def test_actual_text_wins_over_extracted_text() -> None:
    """/ActualText sidesteps font-encoding damage in MCID extraction."""
    elements = [
        _element(0, "Sect", children=[1, 2], mcids=[]),
        _element(1, "P", text="�� garbled", parent=0, actual="• First"),
        _element(2, "P", text="�� garbled", parent=0, actual="• Second"),
    ]
    groups = find_list_candidates(elements)
    assert len(groups) == 1
    assert groups[0].pattern == "bullet"


# ---------------------------------------------------------------------------
# Visual reference scan
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Required fields are shown in red",
        "Click the red button to continue",
        "This content is colour-coded by category",
    ],
)
def test_english_colour_references_are_found(text: str) -> None:
    refs = scan_visual_references([_element(0, "P", text=text)])
    assert refs, f"expected a match for {text!r}"


@pytest.mark.parametrize(
    "text",
    [
        "Les champs obligatoires sont en rouge",
        "Cliquez sur le bouton rouge",
        "Le contenu est codé par couleur",
    ],
)
def test_french_colour_references_are_found(text: str) -> None:
    """French matters here — this is a bilingual product."""
    refs = scan_visual_references([_element(0, "P", text=text)])
    assert refs, f"expected a match for {text!r}"


def test_figure_alt_text_is_not_a_colour_reference() -> None:
    """Alt text *describes* an image; "a red triangle" is correct, not a fault."""
    figure = _element(0, "Figure", alt="A red warning triangle in red")
    assert scan_visual_references([figure]) == []


def test_plain_prose_yields_nothing() -> None:
    elements = [_element(0, "P", text="The quarterly results were positive.")]
    assert scan_visual_references(elements) == []


def test_duplicate_phrases_are_reported_once() -> None:
    text = "Errors are shown in red. Errors are shown in red."
    refs = scan_visual_references([_element(0, "P", text=text)])
    phrases = [r.phrase.lower() for r in refs]
    assert len(phrases) == len(set(phrases))
