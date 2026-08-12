"""Semantic accessibility analysis — the main AI pass.

Ported from pdfMax's ``run_claude_semantic_analysis`` (line ~10392). Sends
the page image plus the document's structural inventories and asks for the
issues that mechanical checks cannot reach: markup that is missing rather
than malformed, alt text that is present but wrong, reading order that
parses but does not read, groupings a human would expect.

The prompt is transcribed. It was written and tuned against this exact
schema, and its instructions are specific enough that rewording it would
change the findings rather than tidy the code.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

from auto_a11y.pdf.audit.ai.client import AIClient, image_block, text_block
from auto_a11y.pdf.audit.ai.schemas import SEMANTIC_ANALYSIS_SCHEMA
from auto_a11y.pdf.audit.candidates import HeadingCandidate, ListCandidate
from auto_a11y.pdf.audit.structure import StructElement

logger = logging.getLogger(__name__)

_MAX_TOKENS = 16000
_TEXT_SUMMARY_LIMIT = 8000
_TAG_TREE_LIMIT = 6000
_READING_ORDER_LIMIT = 4000
_ALT_TEXT_LIMIT = 4000


def analyze_semantics(
    client: AIClient,
    *,
    pdf_path: Path,
    elements: list[StructElement],
    page_image: bytes | None,
    tag_tree_text: str,
    reading_order_text: str,
    heading_map_text: str,
    full_alt_text: str,
    form_inventory_text: str,
    doc_lang: str | None,
    heading_candidates: list[HeadingCandidate],
    list_candidates: list[ListCandidate],
) -> dict[str, Any] | None:
    """Run the semantic pass. Returns the parsed analysis, or None on failure."""
    prompt = _build_prompt(
        pdf_path=pdf_path,
        elements=elements,
        tag_tree_text=tag_tree_text,
        reading_order_text=reading_order_text,
        heading_map_text=heading_map_text,
        full_alt_text=full_alt_text,
        form_inventory_text=form_inventory_text,
        doc_lang=doc_lang,
        heading_candidates=heading_candidates,
        list_candidates=list_candidates,
    )

    content: list[dict[str, Any]] = []
    if page_image is not None:
        content.append(image_block(page_image))
    content.append(text_block(prompt))

    return client.json_call(
        content,
        schema=SEMANTIC_ANALYSIS_SCHEMA,
        max_tokens=_MAX_TOKENS,
        effort="high",
    )


def _build_prompt(
    *,
    pdf_path: Path,
    elements: list[StructElement],
    tag_tree_text: str,
    reading_order_text: str,
    heading_map_text: str,
    full_alt_text: str,
    form_inventory_text: str,
    doc_lang: str | None,
    heading_candidates: list[HeadingCandidate],
    list_candidates: list[ListCandidate],
) -> str:
    text_summary = _element_text_summary(elements)

    prompt = f"""You are an expert PDF accessibility auditor. Analyze this PDF document for accessibility issues that require semantic understanding — things purely mechanical checks cannot detect.

## Document Info
- **File**: {pdf_path.name}
- **Document language**: {doc_lang or "NOT SET"}
- **Structure elements**: {len(elements)}

## Tag Tree Structure
```
{tag_tree_text[:_TAG_TREE_LIMIT]}
```

## Reading Order (what a screen reader encounters)
```
{reading_order_text[:_READING_ORDER_LIMIT]}
```

## Heading Map
```
{heading_map_text}
```

## Full Alt Text on Elements
```
{full_alt_text[:_ALT_TEXT_LIMIT]}
```

## Extracted Text by Element
```
{text_summary}
```

## Form Field Inventory
```
{form_inventory_text if form_inventory_text else "No form fields detected in this document."}
```

## Instructions

Return a structured JSON analysis. For each issue found, provide:
- **category**: one of missing_semantic_markup, alt_text_quality, reading_order, content_grouping, form_accessibility, additional_concern
- **severity**: "critical" (makes content inaccessible), "important" (significantly degrades experience), or "advisory" (nice-to-have improvement)
- **title**: short descriptive title
- **elements**: which structure element indices are affected (e.g. "[7]-[12]", "[54]", or "" if general)
- **problem**: what is wrong
- **recommendation**: specific fix, including correct PDF tags/structure where applicable
- **impact**: how this affects assistive technology users

Also provide an overall_assessment summarizing the document's semantic accessibility quality.

Look for: missing list/table/section markup, alt text quality issues (too verbose, redundant, inaccurate), reading order problems, heading hierarchy issues, content grouping gaps, decorative elements that should be artifacts, images of text, and any other semantic issues.

### Form-specific checks (use category "form_accessibility")
If the document contains form fields, also analyze:
1. **Label quality**: Are the accessible names (/TU tooltips) actually descriptive? A tooltip like "Field 1" or "text_input" is not helpful — it should describe what information the user should enter (e.g. "First name", "Email address").
2. **Visual label-field association**: Looking at the page image, are visible text labels positioned near their corresponding form fields? Labels should be directly above or to the left of their fields.
3. **Required field indicator consistency**: If some fields are visually marked as required (e.g. with an asterisk *), do all required fields have the visual indicator AND the semantic Required flag? Inconsistency confuses users.
4. **Logical field grouping**: Are related fields (e.g. a set of radio buttons for a single question, or address fields) grouped together in the structure? They should share a common parent or be within a fieldset-equivalent grouping.
5. **Tab order vs visual layout**: Does the structure order of form fields match the visual top-to-bottom, left-to-right reading order? A field that visually appears first should also be first in the tab order.
6. **Form instructions**: Is there instructional text (e.g. "Fields marked with * are required") present and positioned before the first form field in the reading order?

Only report form issues if the document actually contains form fields."""

    prompt += _heading_candidates_section(heading_candidates)
    prompt += _list_candidates_section(list_candidates)
    return prompt


def _element_text_summary(elements: list[StructElement]) -> str:
    """Element-indexed text, leaves only, truncated to the prompt budget."""
    parts: list[str] = []
    for elem in elements:
        if elem.text_content and elem.mcids:
            parts.append(f"[{elem.index + 1}] {elem.resolved_tag}: {elem.text_content}")
    summary = "\n".join(parts)
    if len(summary) > _TEXT_SUMMARY_LIMIT:
        summary = summary[:_TEXT_SUMMARY_LIMIT] + "\n... (truncated)"
    return summary


def _heading_candidates_section(candidates: list[HeadingCandidate]) -> str:
    if not candidates:
        return """

## Potential Unmarked Headings

No candidates were identified by font analysis. Return an empty "unmarked_headings" array."""

    listing = "\n".join(
        (
            f'[{c.index}] {c.tag}: "{c.text}" '
            + f"({c.font_size:g}pt{', Bold' if c.is_bold else ''})"
        )
        for c in candidates
    )
    return f"""

## Potential Unmarked Headings

The following non-heading elements have visual characteristics (larger font size, bold weight) suggesting they may be unmarked headings. Evaluate each one using the page image and structural context.

In the "unmarked_headings" response array, include ONLY elements that genuinely function as section headings — they introduce a section and title the content below them. Do NOT include:
- Bold body text or emphasized phrases within paragraphs
- List items, labels, captions, or table headers
- Decorative or presentational text that doesn't introduce a section

For each confirmed heading, provide:
- element_index: the 0-based index (the number shown in brackets)
- suggested_level: heading level 1-6 that fits the document's hierarchy
- confidence: "high", "medium", or "low"
- reasoning: brief explanation of why this is a heading

Candidates:
```
{listing}
```"""


def _list_candidates_section(candidates: list[ListCandidate]) -> str:
    if not candidates:
        return """

## Potential Untagged Lists

No candidate sequences found. Return an empty "potential_lists" array."""

    groups: list[str] = []
    for group in candidates:
        items = "\n".join(
            f"  [{item.index}] P: \"{item.text}\"" for item in group.elements
        )
        groups.append(
            f"Group {group.group_id} ({group.pattern} pattern, "
            + f"parent [{group.parent_index + 1}]):\n{items}"
        )
    listing = "\n\n".join(groups)
    return f"""

## Potential Untagged Lists

The following sequences of consecutive P (paragraph) elements match list-marker patterns (bullets, numbers, letters). Using the page image and structure context, evaluate whether each group genuinely represents a list that should use L > LI structure.

In the "potential_lists" response array, include an entry for EACH group. For each:
- group_id: the group number shown above
- is_list: true if this genuinely represents a list, false if it's coincidental (e.g. paragraphs that happen to start with a number)
- confidence: "high", "medium", or "low"
- reasoning: brief explanation

Groups to evaluate:
```
{listing}
```"""


def severity_counts(analysis: dict[str, Any]) -> dict[str, int]:
    """Count the analysis's issues by severity.

    Unknown severities are ignored rather than bucketed into ``advisory`` —
    the schema constrains the field, so an unexpected value means something
    went wrong upstream and should not be silently reclassified.
    """
    counts = {"critical": 0, "important": 0, "advisory": 0}
    raw = analysis.get("issues")
    if not isinstance(raw, list):
        return counts
    for issue in cast("list[object]", raw):
        if not isinstance(issue, dict):
            continue
        severity = cast("dict[str, object]", issue).get("severity")
        if isinstance(severity, str) and severity in counts:
            counts[severity] += 1
    return counts
