"""Vision analyses: images of text, alt-text adequacy, and use of colour.

Ported from pdfMax's ``detect_text_in_images`` (~3870),
``check_alt_text_adequacy`` (~4060) and ``analyze_color_use_ai`` (~9714).
These are the passes that need to *see* the document, and the ones that
justify the AI toggle — no amount of structure walking tells you whether a
figure's alt text is accurate or whether a chart's legend is colour-only.

Cost note: alt-text adequacy and image-text detection are one call per
image, as in the original. There is no cap; a document with many figures
costs proportionally more, which is the honest behaviour for an opt-in
feature. The colour pass keeps pdfMax's own three-page cap because sending
every page of a long document was never its design.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

from auto_a11y.pdf.audit.ai.client import AIClient, image_block, text_block
from auto_a11y.pdf.audit.ai.schemas import (
    ALT_TEXT_ADEQUACY_SCHEMA,
    COLOR_USE_ANALYSIS_SCHEMA,
    IMAGE_TEXT_SCHEMA,
    PAGE_IMAGE_TEXT_SCHEMA,
)
from auto_a11y.pdf.audit.ai.visual_references import VisualReference
from auto_a11y.pdf.audit.images import ExtractedImage
from auto_a11y.pdf.audit.rasterize import render_page_to_png
from auto_a11y.pdf.audit.structure import StructElement

logger = logging.getLogger(__name__)

_IMAGE_TEXT_MAX_TOKENS = 2000
_PAGE_TEXT_MAX_TOKENS = 4000
_ALT_TEXT_MAX_TOKENS = 3000
_COLOR_MAX_TOKENS = 8000

_FIGURE_TAGS = ("Figure", "Art")
_TEXT_CHILD_TAGS = ("P", "Span", "L", "LI", "Table", "TR", "TD", "TH")


# ---------------------------------------------------------------------------
# Images of text (WCAG 1.4.5)
# ---------------------------------------------------------------------------


def detect_images_of_text(
    client: AIClient,
    *,
    pdf_path: Path,
    elements: list[StructElement],
    images: list[ExtractedImage],
    images_dir: Path,
) -> dict[str, Any]:
    """Read text out of extracted images and off the rendered page.

    Returns a section payload: per-image detections, whether each one's text
    is reflected in any alt text, and page-level findings for text that looks
    rasterised.
    """
    per_image: list[dict[str, Any]] = []
    for image in images:
        path = images_dir / image.filename
        if not path.is_file():
            continue
        try:
            png = path.read_bytes()
        except OSError:
            continue

        result = client.json_call(
            [
                image_block(png),
                text_block(
                    "Does this image contain any text? If yes, transcribe ALL "
                    + "text you can read in the image exactly as it appears. "
                    + "Set contains_text to false and leave text empty if "
                    + "there is no text."
                ),
            ],
            schema=IMAGE_TEXT_SCHEMA,
            max_tokens=_IMAGE_TEXT_MAX_TOKENS,
            effort="low",
        )
        if result is None:
            per_image.append({
                "filename": image.filename,
                "width": image.width,
                "height": image.height,
                "contains_text": None,
                "text": "",
                "text_in_alt": None,
            })
            continue

        contains = bool(result.get("contains_text"))
        detected = str(result.get("text") or "")
        per_image.append({
            "filename": image.filename,
            "width": image.width,
            "height": image.height,
            "contains_text": contains,
            "text": detected,
            "text_in_alt": (
                _text_appears_in_alt(detected, elements) if contains else None
            ),
        })

    page_findings = _detect_page_images_of_text(client, pdf_path=pdf_path)

    uncovered = sum(
        1 for row in per_image
        if row["contains_text"] and row["text_in_alt"] is False
    )
    return {
        "images": per_image,
        "page_findings": page_findings,
        "images_with_text_not_in_alt": uncovered,
        "total_findings": uncovered + len(page_findings),
    }


def _detect_page_images_of_text(
    client: AIClient, *, pdf_path: Path
) -> list[dict[str, Any]]:
    """Ask whether any text on page 1 is rendered as graphics rather than text."""
    png = render_page_to_png(pdf_path, 0, dpi=200)
    if png is None:
        return []

    result = client.json_call(
        [
            image_block(png),
            text_block(
                """Look at this PDF page rendering. Identify any text that appears to be rendered as part of an image or graphic rather than as real selectable text. Common examples:
- Text within logo images (e.g., organization names in logos)
- Text overlaid on images or colored backgrounds that might be rasterized
- Handwritten text (signatures with text)
- Text within icons or decorative graphics

For each instance, report what the text says, where it appears on the page (top/middle/bottom, left/right), whether it is likely an image of text rather than real text, and your confidence.

If all text appears to be real selectable text, return an empty findings array."""
            ),
        ],
        schema=PAGE_IMAGE_TEXT_SCHEMA,
        max_tokens=_PAGE_TEXT_MAX_TOKENS,
        effort="low",
    )
    if result is None:
        return []
    raw = result.get("findings")
    if not isinstance(raw, list):
        return []
    findings: list[dict[str, Any]] = []
    for item in cast("list[object]", raw):
        if not isinstance(item, dict):
            continue
        entry = cast("dict[str, Any]", item)
        # Only genuine images-of-text; the model also reports real text it
        # considered and ruled out, which is not a finding.
        if entry.get("is_image_of_text"):
            findings.append(entry)
    return findings


def _text_appears_in_alt(detected: str, elements: list[StructElement]) -> bool:
    """Whether a figure's alt text covers text found inside the image.

    Word-overlap rather than substring: alt text legitimately paraphrases,
    so requiring an exact match would flag every well-written description.
    """
    detected_words = {w for w in detected.lower().split() if len(w) > 2}
    if not detected_words:
        return False
    threshold = min(2, len(detected_words))
    for elem in elements:
        if elem.resolved_tag not in _FIGURE_TAGS or not elem.alt_text:
            continue
        overlap = detected_words & set(elem.alt_text.lower().split())
        if len(overlap) >= threshold:
            return True
    return False


# ---------------------------------------------------------------------------
# Alt-text adequacy (WCAG 1.1.1)
# ---------------------------------------------------------------------------


def assess_alt_text(
    client: AIClient,
    *,
    elements: list[StructElement],
    images: list[ExtractedImage],
    images_dir: Path,
    doc_title: str,
) -> dict[str, Any]:
    """Score each figure's alt text against the image and its context."""
    figures = _figures_with_alt_text(elements, images)
    if not figures:
        return {"assessments": [], "total": 0, "inadequate": 0, "errors": 0}

    assessments: list[dict[str, Any]] = []
    for elem, image_file in figures:
        content: list[dict[str, Any]] = []
        if image_file:
            path = images_dir / image_file
            if path.is_file():
                try:
                    content.append(image_block(path.read_bytes()))
                except OSError:
                    pass

        content.append(text_block(_alt_text_prompt(elem, elements, doc_title)))

        result = client.json_call(
            content,
            schema=ALT_TEXT_ADEQUACY_SCHEMA,
            max_tokens=_ALT_TEXT_MAX_TOKENS,
            effort="low",
        )
        if result is None:
            assessments.append({
                "element_index": elem.index + 1,
                "tag": elem.custom_tag.lstrip("/")[:30],
                "alt_text": elem.alt_text or "",
                "error": "Assessment failed",
            })
            continue

        raw = result.get("assessments")
        rows = cast("list[object]", raw) if isinstance(raw, list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            entry = dict(cast("dict[str, Any]", row))
            entry["tag"] = elem.custom_tag.lstrip("/")[:30]
            entry["alt_text"] = elem.alt_text or ""
            assessments.append(entry)

    scored = [a for a in assessments if "error" not in a]
    errors = [a for a in assessments if "error" in a]
    inadequate = [a for a in scored if not a.get("is_adequate", True)]
    return {
        "assessments": assessments,
        "total": len(figures),
        "inadequate": len(inadequate),
        "errors": len(errors),
    }


def _figures_with_alt_text(
    elements: list[StructElement], images: list[ExtractedImage]
) -> list[tuple[StructElement, str]]:
    """Graphic figures carrying alt text, paired with their extracted image.

    Figures that contain text children are structural containers rather than
    graphics — alt text is not required on them, so they are not scored.
    The image pairing walks both sequences in order, which is the same
    positional mapping the image inventory uses.
    """
    pairs: list[tuple[StructElement, str]] = []
    image_index = 0
    for elem in elements:
        if elem.resolved_tag not in _FIGURE_TAGS:
            continue
        has_text_children = any(
            elements[ci].resolved_tag in _TEXT_CHILD_TAGS
            for ci in elem.children_indices
            if 0 <= ci < len(elements)
        )
        if has_text_children:
            continue
        filename = ""
        if image_index < len(images):
            filename = images[image_index].filename
            image_index += 1
        if elem.alt_text and elem.alt_text.strip():
            pairs.append((elem, filename))
    return pairs


def _alt_text_prompt(
    elem: StructElement, elements: list[StructElement], doc_title: str
) -> str:
    context_parts: list[str] = []
    if doc_title:
        context_parts.append(f"Document title: {doc_title}")
    heading = _nearest_heading(elem, elements)
    if heading:
        context_parts.append(f"Section heading: {heading}")
    preceding, following = _sibling_context(elem, elements)
    if preceding:
        context_parts.append("Text before figure: " + " ".join(preceding))
    if following:
        context_parts.append("Text after figure: " + " ".join(following))
    context_text = (
        "\n".join(context_parts) if context_parts
        else "No surrounding context available."
    )

    return (
        "You are an accessibility expert evaluating the quality of alt text "
        "on a PDF image element.\n\n"
        f"Current alt text: \"{elem.alt_text}\"\n\n"
        f"Document context:\n{context_text}\n\n"
        "Score this alt text on each criterion from 1 (worst) to 10 (best):\n"
        "- **accuracy**: Does the alt text correctly describe what is in the image?\n"
        "- **completeness**: Does it convey the essential information the image communicates?\n"
        "- **context_relevance**: Is it appropriate for the surrounding document content?\n"
        "- **conciseness**: Is it appropriately brief without losing meaning?\n"
        "- **overall**: Overall quality considering all factors.\n\n"
        "Set is_adequate to true if overall >= 6, false otherwise.\n"
        "List specific issues (empty array if none).\n\n"
        "Return JSON with a single entry in the assessments array for "
        f"element_index {elem.index + 1}."
    )


def _nearest_heading(elem: StructElement, elements: list[StructElement]) -> str:
    """The last heading before this element in document order."""
    heading_tags = {"H", "H1", "H2", "H3", "H4", "H5", "H6"}
    for i in range(elem.index - 1, -1, -1):
        if i >= len(elements):
            continue
        if elements[i].resolved_tag in heading_tags:
            text = elements[i].text_content or elements[i].alt_text or ""
            if text.strip():
                return text.strip()[:200]
    return ""


def _sibling_context(
    elem: StructElement, elements: list[StructElement]
) -> tuple[list[str], list[str]]:
    """Text of the siblings immediately around this element."""
    preceding: list[str] = []
    following: list[str] = []
    if 0 <= elem.parent_index < len(elements):
        parent = elements[elem.parent_index]
        seen_self = False
        for ci in parent.children_indices:
            if not (0 <= ci < len(elements)):
                continue
            if ci == elem.index:
                seen_self = True
                continue
            text = (elements[ci].text_content or elements[ci].alt_text or "").strip()
            if not text:
                continue
            if seen_self:
                following.append(text[:200])
            else:
                preceding.append(text[:200])
    return preceding[-3:], following[:3]


# ---------------------------------------------------------------------------
# Use of colour (WCAG 1.4.1)
# ---------------------------------------------------------------------------


def analyze_color_use(
    client: AIClient,
    *,
    pdf_path: Path,
    page_count: int,
    color_summary: str,
    visual_references: list[VisualReference],
    form_inventory_text: str,
    doc_lang: str | None,
    max_pages: int = 3,
) -> dict[str, Any] | None:
    """Assess whether colour alone carries meaning anywhere in the document."""
    pages = _pages_to_render(page_count, max_pages)
    content: list[dict[str, Any]] = []
    rendered: list[int] = []
    for page in pages:
        png = render_page_to_png(pdf_path, page, dpi=150)
        if png is None:
            continue
        content.append(text_block(f"[Page {page + 1} of {page_count}]"))
        content.append(image_block(png))
        rendered.append(page)

    if not rendered:
        logger.warning("No pages could be rendered; skipping colour-use analysis")
        return None

    content.append(text_block(_color_prompt(
        pages=rendered,
        page_count=page_count,
        color_summary=color_summary,
        visual_references=visual_references,
        form_inventory_text=form_inventory_text,
        doc_lang=doc_lang,
    )))

    return client.json_call(
        content,
        schema=COLOR_USE_ANALYSIS_SCHEMA,
        max_tokens=_COLOR_MAX_TOKENS,
        effort="high",
    )


def _pages_to_render(page_count: int, max_pages: int) -> list[int]:
    """Page 1 always, then sequential pages up to the cap."""
    return list(range(min(max_pages, max(page_count, 1))))


def _color_prompt(
    *,
    pages: list[int],
    page_count: int,
    color_summary: str,
    visual_references: list[VisualReference],
    form_inventory_text: str,
    doc_lang: str | None,
) -> str:
    ref_lines = [
        (
            f"- [{ref.category}] Element [{ref.element_index}] ({ref.tag}): "
            + f'"{ref.phrase}" — Context: "{ref.context}"'
        )
        for ref in visual_references[:15]
    ]
    ref_summary = (
        "\n".join(ref_lines) if ref_lines
        else "No visual-reference phrases detected in text."
    )
    pages_desc = ", ".join(str(p + 1) for p in pages)

    lang_note = ""
    if doc_lang:
        code = doc_lang.lower()[:2]
        if code == "fr":
            lang_note = (
                "\n\n## Document Language\n"
                "This document is in **French**. Look for color and visual references in French, "
                "such as: \"en rouge\", \"surligné en\", \"les champs obligatoires sont en rouge\", "
                "\"cliquez sur le bouton rouge\", \"codé par couleur\", \"les erreurs sont affichées "
                "en rouge\", \"texte en gras\", \"voir la section surlignée\"."
            )
        elif code != "en":
            lang_note = (
                f"\n\n## Document Language\n"
                f"This document is in **{doc_lang}**. Look for color and visual references "
                f"in this language as well as in English or French."
            )

    return f"""You are an expert PDF accessibility auditor analyzing a document for WCAG 1.4.1 "Use of Color" compliance.

WCAG 1.4.1 requires that color is NOT used as the sole visual means of conveying information, indicating an action, prompting a response, or distinguishing a visual element. Information conveyed by color differences must also be available through other means (text labels, patterns, shapes, underlines, icons, etc.).
{lang_note}
## Pages Provided
Page images attached: pages {pages_desc} (of {page_count} total).

## Text Color Palette
These non-black/white text colors appear in the document:
{color_summary}

## Pre-Scan: Text Referencing Visual Characteristics
An automated scan found these phrases that reference colors or visual styles:
{ref_summary}

## Form Fields
{form_inventory_text if form_inventory_text else "No form fields detected."}

## Instructions

Examine each page image carefully. Identify ALL instances where color may be the sole means of conveying information. For each finding, determine whether a non-color alternative exists.

Specifically look for:

1. **Text that references color or visual characteristics** — phrases in English (e.g. "click the red button", "required fields are in red", "errors shown in red") or French (e.g. "cliquez sur le bouton rouge", "les champs obligatoires sont en rouge", "les erreurs sont affichées en rouge") or any other language present. Is the information also conveyed through text labels, icons, or other non-color means?

2. **Charts, graphs, and infographics** — data visualizations that use color to differentiate data series, segments, or categories. Are there also patterns (hatching, dots), direct labels on data, or other non-color differentiators?

3. **Color-coded content** — text or elements where different colors indicate different categories, statuses, or importance levels (e.g., error messages only in red without "Error:"/"Erreur:" prefix, status indicators using only colored dots). Does each colored item also have a text label or icon?

4. **Form field indicators** — required fields marked only by red color or red asterisks without accompanying "(required)"/"(obligatoire)" text.

5. **Links** — hyperlinks distinguishable from surrounding body text only by blue color with no underline, bold, or other visual distinction.

For severity:
- "fail": Color IS the sole means of conveying information (no non-color alternative present)
- "warning": Color is the primary means but some partial redundancy may exist

If the document has NO color-use issues, return an empty findings array and pass=true."""
