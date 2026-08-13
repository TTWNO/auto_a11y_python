"""Vision passes for the two checks a collector can only half-answer.

Both of these criteria are about what a sighted user can see, and both
have a deterministic half that stops short of the page:

* **Non-text contrast** (WCAG 1.4.11) —
  :mod:`auto_a11y.pdf.audit.non_text_contrast` measures declared colours
  and sampled pixels, which settles form-field borders but not whether a
  chart's bars, an icon, or a divider is distinguishable in context.
* **Required-field indicators** (WCAG 3.3.2) —
  :mod:`auto_a11y.pdf.audit.required_fields` reads names, tooltips and
  legend text. An asterisk drawn beside a field as ordinary page content
  satisfies the criterion and is invisible to every one of those.

Ported from pdfMax's ``analyze_non_text_contrast_ai`` (line ~10113) and
``analyze_required_indicators_ai`` (line ~9953). Each renders the pages
that matter — the ones with Tier 1 failures first — and hands Claude the
deterministic findings as a table, so the model is adjudicating measured
numbers rather than guessing at colours from a screenshot.

Both return ``None`` on any failure. A pass that could not run leaves
the deterministic verdict standing rather than replacing it with a
guess.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from auto_a11y.pdf.audit.ai.client import AIClient, image_block, text_block
from auto_a11y.pdf.audit.ai.schemas import (
    NON_TEXT_CONTRAST_SCHEMA,
    REQUIRED_INDICATOR_SCHEMA,
)
from auto_a11y.pdf.audit.non_text_contrast import (
    FieldContrastFinding,
    GraphicFinding,
    NonTextContrast,
)
from auto_a11y.pdf.audit.rasterize import render_page_to_png
from auto_a11y.pdf.audit.required_fields import RequiredFields

logger = logging.getLogger(__name__)

__all__ = ["analyze_non_text_contrast", "analyze_required_indicators"]

_CONTRAST_MAX_TOKENS = 4096
_REQUIRED_MAX_TOKENS = 4096

#: Pages sent per pass. pdfMax's cap, kept: these are full-page images
#: at render resolution, and three is where the cost stops paying for
#: itself.
_MAX_PAGES = 3

#: Contrast judgement needs to see hairlines, so it renders finer than
#: the required-indicator pass, which only needs to read labels.
_CONTRAST_DPI = 150
_REQUIRED_DPI = 100


def _hex(color: tuple[float, float, float] | None) -> str:
    if color is None:
        return "none"
    return "#{:02X}{:02X}{:02X}".format(
        int(color[0] * 255), int(color[1] * 255), int(color[2] * 255)
    )


def _render(
    pdf_path: Path, pages: list[int], page_count: int, dpi: int
) -> list[dict[str, Any]]:
    """Content blocks for the given pages, labelled as the model sees them."""
    content: list[dict[str, Any]] = []
    for page in pages:
        if page >= page_count:
            continue
        png = render_page_to_png(pdf_path, page, dpi=dpi)
        if png is None:
            continue
        content.append(text_block(f"[Page {page + 1} of {page_count}]"))
        content.append(image_block(png))
    return content


# ---------------------------------------------------------------------------
# Non-text contrast
# ---------------------------------------------------------------------------


def analyze_non_text_contrast(
    client: AIClient,
    *,
    pdf_path: Path,
    page_count: int,
    data: NonTextContrast,
    form_inventory_text: str,
    doc_lang: str | None,
    max_pages: int = _MAX_PAGES,
) -> dict[str, Any] | None:
    """Verify field visibility and find low-contrast graphics on the page."""
    pages = _contrast_pages(data)
    content = _render(pdf_path, pages, page_count, _CONTRAST_DPI)
    if not content:
        logger.warning("No pages rendered; skipping non-text contrast analysis")
        return None

    content.append(text_block(_contrast_prompt(
        data=data,
        form_inventory_text=form_inventory_text,
        doc_lang=doc_lang,
    )))
    return client.json_call(
        content,
        schema=NON_TEXT_CONTRAST_SCHEMA,
        max_tokens=_CONTRAST_MAX_TOKENS,
        effort="high",
    )


def _contrast_pages(data: NonTextContrast) -> list[int]:
    """Pages worth sending, failures first.

    A page with a measured failure is where the model's judgement is
    most needed; pages that merely carry fields or graphics come after,
    and page 1 is the fallback when there is nothing either way.
    """
    failing: list[int] = []
    carrying: list[int] = []

    def note(page: int, failed: bool) -> None:
        target = failing if failed else carrying
        if page not in target:
            target.append(page)

    if data.fields is not None:
        for field in data.fields.findings:
            if field.exempt_reason is not None:
                continue
            failed = field.border_pass is False or field.boundary_pass is False
            note(field.page, failed)
    for graphic in data.graphics.findings:
        note(graphic.page - 1, not graphic.contrast_pass)

    ordered = sorted(failing)
    ordered += [p for p in sorted(carrying) if p not in ordered]
    return ordered[:_MAX_PAGES] or [0]


def _contrast_prompt(
    *,
    data: NonTextContrast,
    form_inventory_text: str,
    doc_lang: str | None,
) -> str:
    lang_context = ""
    if doc_lang:
        lang_context = f"\nThe document language is '{doc_lang}'."
        if doc_lang.lower().startswith("fr"):
            lang_context += (
                " The document is in French. Write your descriptions and "
                "recommendations in English, but reference French "
                "text/labels as they appear."
            )

    inventory = form_inventory_text[:2000] if form_inventory_text else (
        "No form inventory available."
    )
    return f"""You are a WCAG 2.2 accessibility expert evaluating **non-text contrast** (WCAG 1.4.11).

**Requirement**: UI components and meaningful graphical objects must have at least **3:1 contrast** against adjacent colors.
{lang_context}

**Exemptions** (do NOT flag these):
- Inactive/disabled controls
- Controls whose appearance is determined entirely by the PDF viewer (no author styling)
- Purely decorative elements (ornamental borders, background patterns)
- Logos and branding where specific colors are essential

**Your task**:
1. **Verify form field borders**: Check if form field borders are visually distinguishable from the page background. Compare with the automated findings below.
2. **Detect graphical elements**: Look for charts, icons, divider lines, data visualization elements, and other meaningful graphical objects. Assess whether they have sufficient contrast against their background.
3. **Check button boundaries**: Buttons should be visually identifiable as interactive controls.

{_tier1_table(data)}

For each issue found, categorize it and provide a concrete recommendation.

**Form field context**:
{inventory}

Be precise about page numbers and field names. Only flag genuine contrast failures — do not flag fields that are clearly visible."""


def _tier1_table(data: NonTextContrast) -> str:
    """The measured findings, as markdown tables the model can reason over."""
    lines = [
        "Automated Tier 1 findings (border/boundary contrast at 3:1 threshold):",
        "| Field | Type | Page | Border Color | Page BG | Border Ratio |"
        + " Boundary Ratio | Status |",
        "|-------|------|------|-------------|---------|-------------|"
        + "---------------|--------|",
    ]
    if data.fields is not None:
        for field in data.fields.findings:
            if field.exempt_reason is not None:
                continue
            lines.append(_field_row(field))

    if data.graphics.findings:
        lines.append("\nAutomated graphical element findings (3:1 threshold):")
        lines.append("| Category | Page | Color | BG | Ratio | Status |")
        lines.append("|----------|------|-------|----|-------|--------|")
        for graphic in data.graphics.findings:
            lines.append(_graphic_row(graphic))
    return "\n".join(lines)


def _field_row(field: FieldContrastFinding) -> str:
    border = (
        f"{field.border_contrast:.2f}:1"
        if field.border_contrast is not None else "—"
    )
    boundary = (
        f"{field.boundary_contrast:.2f}:1"
        if field.boundary_contrast is not None else "—"
    )
    status = ""
    if field.border_pass is False:
        status = "BORDER FAIL"
    if field.boundary_pass is False and field.border_pass is not True:
        status += (" + " if status else "") + "BOUNDARY FAIL"
    return (
        f"| {field.field_name} | {field.field_type} | {field.page + 1} |"
        f" {_hex(field.border_color)} | {_hex(field.page_bg_color)} |"
        f" {border} | {boundary} | {status or 'PASS'} |"
    )


def _graphic_row(graphic: GraphicFinding) -> str:
    label = graphic.category.replace("_", " ").title()
    return (
        f"| {label} | {graphic.page} | {_hex(graphic.element_color)} |"
        f" {_hex(graphic.page_bg_color)} | {graphic.contrast_ratio:.2f}:1 |"
        f" {'PASS' if graphic.contrast_pass else 'FAIL'} |"
    )


# ---------------------------------------------------------------------------
# Required-field visual indicators
# ---------------------------------------------------------------------------


def analyze_required_indicators(
    client: AIClient,
    *,
    pdf_path: Path,
    page_count: int,
    data: RequiredFields,
    form_inventory_text: str,
    doc_lang: str | None,
    max_pages: int = _MAX_PAGES,
) -> dict[str, Any] | None:
    """Look at the pages and say whether each required field looks required."""
    if not data.fields:
        return None

    pages = sorted({f.page for f in data.fields})[:max_pages] or [0]
    content = _render(pdf_path, pages, page_count, _REQUIRED_DPI)
    if not content:
        logger.warning(
            "No pages rendered; skipping required-indicator analysis"
        )
        return None

    content.append(text_block(_required_prompt(
        data=data,
        form_inventory_text=form_inventory_text,
        doc_lang=doc_lang,
    )))
    return client.json_call(
        content,
        schema=REQUIRED_INDICATOR_SCHEMA,
        max_tokens=_REQUIRED_MAX_TOKENS,
        effort="high",
    )


def _required_prompt(
    *,
    data: RequiredFields,
    form_inventory_text: str,
    doc_lang: str | None,
) -> str:
    rows = [
        "| # | Field Name | Type | Page | Tooltip |"
        + " Has * or 'required' in metadata |",
        "|---|-----------|------|------|---------|"
        + "-------------------------------|",
    ]
    for number, field in enumerate(data.fields, start=1):
        rows.append(
            f"| {number} | {field.name} | {field.field_type} |"
            + f" {field.page + 1} | {field.tooltip} | {field.indicator_detail} |"
        )
    field_table = "\n".join(rows)

    lang_note = ""
    if doc_lang:
        code = doc_lang.lower()[:2]
        if code == "fr":
            lang_note = (
                "\n## Document Language\n"
                "This document is in **French**. Look for required field "
                "indicators in French: \"(obligatoire)\", \"(requis)\", and "
                "legends such as \"Les champs marqués d'un * sont "
                "obligatoires\", \"* indique un champ obligatoire\", "
                "\"Tous les champs sont obligatoires\".\n"
            )
        elif code != "en":
            lang_note = (
                f"\n## Document Language\n"
                f"This document is in **{doc_lang}**. Look for required field "
                f"indicators in this language as well as English or French "
                f"equivalents.\n"
            )

    inventory = form_inventory_text or "No form inventory available."
    return f"""You are analyzing a PDF form for WCAG 3.3.2 compliance — specifically whether
fields marked as "required" programmatically also have clear visual indicators.
{lang_note}
## Required Fields (from PDF metadata — these have the /Ff Required flag set)

{field_table}

## Form Inventory

{inventory}

## Instructions

For each required field listed above, examine the page image and determine:
1. Is there a visible asterisk (*) next to the field's label text?
2. Is the word "required"/"(required)" or "obligatoire"/"(obligatoire)" shown near the field?
3. Is there any other visual indicator (bold label, red border, icon, color change)?

Also check:
- Is there a legend/instruction text visible on any page? Examples in English: "Fields marked with * are required", "* indicates required". Examples in French: "Les champs marqués d'un * sont obligatoires", "* indique un champ obligatoire".
- Are there any fields that appear visually marked as required (e.g. have an asterisk) but are NOT in the required fields list? These would be fields with visual indicators but missing the semantic /Ff Required flag.

Set "pass" to true ONLY if every required field has a clear visual indicator (asterisk, text, or icon) AND there are no fields visually marked as required that lack the semantic flag.
Set "pass" to false if any required field lacks a visual indicator, or if there are mismatched visual-only indicators."""
