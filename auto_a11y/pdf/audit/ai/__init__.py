"""Claude-powered analysis for the PDF audit.

Six passes, ported from pdfMax, run by :func:`analyze`:

============================  =========  =====================================
Pass                          Kind       What it catches
============================  =========  =====================================
Semantic analysis             vision     Markup that is missing rather than
                                         malformed; unmarked headings and
                                         untagged lists, adjudicated against
                                         the page image
Alt-text adequacy             vision     Alt text that exists but is wrong,
                                         padded, or ignores its context
Images of text                vision     Text baked into graphics (WCAG 1.4.5)
Use of colour                 vision     Colour as the sole carrier of
                                         meaning (WCAG 1.4.1)
Executive summary             text       A verdict a non-technical owner can
                                         act on
Language detection            text       Fallback when the deterministic
                                         detector cannot classify the text
============================  =========  =====================================

Every pass degrades on its own: a failed call logs and returns nothing rather
than raising, so one bad response costs its own section and not the audit.
Whether AI ran at all is reported separately from whether it found anything —
:attr:`~auto_a11y.pdf.models.AIAnalysisResult.model` names the model, and
``ai_sections`` records which passes produced output.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal, cast

import pikepdf

from auto_a11y.pdf.audit.ai.client import (
    DEFAULT_MODEL,
    AIClient,
    AIUnavailable,
    Usage,
)
from auto_a11y.pdf.audit.ai.schemas import (
    ALT_TEXT_ADEQUACY_SCHEMA,
    COLOR_USE_ANALYSIS_SCHEMA,
    EXECUTIVE_SUMMARY_SCHEMA,
    IMAGE_TEXT_SCHEMA,
    LANGUAGE_SCHEMA,
    PAGE_IMAGE_TEXT_SCHEMA,
    SEMANTIC_ANALYSIS_SCHEMA,
)
from auto_a11y.pdf.audit.ai.semantic import analyze_semantics, severity_counts
from auto_a11y.pdf.audit.ai.summary import detect_language, generate_executive_summary
from auto_a11y.pdf.audit.ai.vision import (
    analyze_color_use,
    assess_alt_text,
    detect_images_of_text,
)
from auto_a11y.pdf.audit.ai.visual_references import (
    VisualReference,
    scan_visual_references,
)
from auto_a11y.pdf.audit.candidates import (
    find_heading_candidates,
    find_list_candidates,
)
from auto_a11y.pdf.audit.rasterize import render_page_to_png
from auto_a11y.pdf.models import AIAnalysisResult, AIFinding, AuditContext, CheckResult

logger = logging.getLogger(__name__)

__all__ = [
    "ALT_TEXT_ADEQUACY_SCHEMA",
    "COLOR_USE_ANALYSIS_SCHEMA",
    "DEFAULT_MODEL",
    "EXECUTIVE_SUMMARY_SCHEMA",
    "IMAGE_TEXT_SCHEMA",
    "LANGUAGE_SCHEMA",
    "PAGE_IMAGE_TEXT_SCHEMA",
    "SEMANTIC_ANALYSIS_SCHEMA",
    "AIClient",
    "AIUnavailable",
    "Usage",
    "VisualReference",
    "analyze",
    "analyze_color_use",
    "assess_alt_text",
    "analyze_semantics",
    "detect_images_of_text",
    "detect_language",
    "find_heading_candidates",
    "find_list_candidates",
    "generate_executive_summary",
    "scan_visual_references",
    "severity_counts",
]

# Maps the semantic pass's severities onto AIFinding's own scale. pdfMax's
# three levels do not include an "info" tier, so none is produced here.
_SEVERITY_MAP: dict[str, Literal["high", "medium", "low", "info"]] = {
    "critical": "high",
    "important": "medium",
    "advisory": "low",
}


def analyze(
    ctx: AuditContext,
    *,
    check_results: list[CheckResult],
    report_sections: dict[str, object],
    images_dir: Path | None,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
) -> tuple[AIAnalysisResult, dict[str, object]]:
    """Run every AI pass over an audited document.

    Returns the :class:`AIAnalysisResult` for the audit plus a dict of extra
    report sections (``alt_text_adequacy``, ``images_of_text``,
    ``color_use``, ``ai_executive_summary``) for the renderer.

    Raises:
        AIUnavailable: no API key, or the SDK is missing. The caller decides
            whether that is fatal — for an explicit opt-in it should be
            surfaced, not swallowed.
    """
    client = AIClient(api_key=api_key, model=model)
    sections: dict[str, object] = {}

    page_image = render_page_to_png(ctx.pdf_path, 0, dpi=150)
    heading_candidates = find_heading_candidates(ctx.elements, ctx.font_analysis)
    list_candidates = find_list_candidates(ctx.elements)

    # ---- Semantic pass ------------------------------------------------
    semantic = analyze_semantics(
        client,
        pdf_path=ctx.pdf_path,
        elements=ctx.elements,
        page_image=page_image,
        tag_tree_text=_section_text(report_sections, "tag_tree"),
        reading_order_text=_section_text(report_sections, "reading_order"),
        heading_map_text=_section_text(report_sections, "heading_map"),
        full_alt_text=_section_text(report_sections, "full_alt_text"),
        form_inventory_text=_section_text(report_sections, "form_inventory"),
        doc_lang=_declared_lang(ctx.pdf),
        heading_candidates=heading_candidates,
        list_candidates=list_candidates,
    )
    counts = severity_counts(semantic) if semantic else {
        "critical": 0, "important": 0, "advisory": 0,
    }
    findings = _findings_from_semantic(semantic) if semantic else []
    if semantic is not None:
        sections["ai_semantic"] = semantic

    # ---- Vision passes -------------------------------------------------
    if images_dir is not None and ctx.images:
        sections["images_of_text"] = detect_images_of_text(
            client,
            pdf_path=ctx.pdf_path,
            elements=ctx.elements,
            images=ctx.images,
            images_dir=images_dir,
        )
        sections["alt_text_adequacy"] = assess_alt_text(
            client,
            elements=ctx.elements,
            images=ctx.images,
            images_dir=images_dir,
            doc_title=_doc_title(ctx.pdf),
        )

    color = analyze_color_use(
        client,
        pdf_path=ctx.pdf_path,
        page_count=len(ctx.pdf.pages),
        color_summary=_color_summary(report_sections),
        visual_references=scan_visual_references(ctx.elements),
        form_inventory_text=_section_text(report_sections, "form_inventory"),
        doc_lang=_declared_lang(ctx.pdf),
    )
    if color is not None:
        sections["color_use"] = color
        findings.extend(_findings_from_color(color))

    # ---- Executive summary --------------------------------------------
    summary = generate_executive_summary(
        client,
        check_results=check_results,
        ai_counts=counts,
        pdf_filename=ctx.pdf_path.name,
    )
    if summary is not None:
        sections["ai_executive_summary"] = summary

    executive_summary = ""
    if summary is not None:
        headline = summary.get("headline")
        body = summary.get("plain_language_summary")
        executive_summary = "\n\n".join(
            part for part in (headline, body) if isinstance(part, str) and part
        )
    elif semantic is not None:
        assessment = semantic.get("overall_assessment")
        if isinstance(assessment, str):
            executive_summary = assessment

    result = AIAnalysisResult(
        findings=findings,
        executive_summary=executive_summary,
        overall_severity=_overall_severity(findings),
        model=client.model,
        cached_input_tokens=client.usage.cached_input_tokens,
        uncached_input_tokens=client.usage.uncached_input_tokens,
        output_tokens=client.usage.output_tokens,
    )
    logger.info(
        "AI analysis complete: %d finding(s) across %d call(s) on %s",
        len(findings), client.usage.calls, client.model,
    )
    return result, sections


# ---------------------------------------------------------------------------
# Result shaping
# ---------------------------------------------------------------------------


def _findings_from_semantic(analysis: dict[str, Any]) -> list[AIFinding]:
    raw = analysis.get("issues")
    if not isinstance(raw, list):
        return []
    findings: list[AIFinding] = []
    for item in cast("list[object]", raw):
        if not isinstance(item, dict):
            continue
        issue = cast("dict[str, Any]", item)
        severity = _SEVERITY_MAP.get(str(issue.get("severity")), "low")
        description = "\n\n".join(
            str(issue.get(key) or "")
            for key in ("problem", "recommendation", "impact")
            if issue.get(key)
        )
        findings.append(AIFinding(
            category=str(issue.get("category") or "additional_concern"),
            severity=severity,
            title=str(issue.get("title") or "Untitled finding"),
            description=description,
            element_index=_first_element_index(str(issue.get("elements") or "")),
        ))
    return findings


def _findings_from_color(analysis: dict[str, Any]) -> list[AIFinding]:
    raw = analysis.get("findings")
    if not isinstance(raw, list):
        return []
    findings: list[AIFinding] = []
    for item in cast("list[object]", raw):
        if not isinstance(item, dict):
            continue
        entry = cast("dict[str, Any]", item)
        page = entry.get("page")
        findings.append(AIFinding(
            category="color_use",
            severity="high" if entry.get("severity") == "fail" else "medium",
            title=str(entry.get("category") or "Use of colour"),
            description="\n\n".join(
                str(entry.get(key) or "")
                for key in ("description", "what_color_conveys", "recommendation")
                if entry.get(key)
            ),
            page=page if isinstance(page, int) else None,
        ))
    return findings


def _first_element_index(elements_text: str) -> int | None:
    """Pull the first structure-element index out of a string like ``"[7]-[12]"``."""
    import re

    match = re.search(r"\[(\d+)\]", elements_text)
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _overall_severity(
    findings: list[AIFinding],
) -> Literal["high", "medium", "low", "none"]:
    if any(f.severity == "high" for f in findings):
        return "high"
    if any(f.severity == "medium" for f in findings):
        return "medium"
    if findings:
        return "low"
    return "none"


# ---------------------------------------------------------------------------
# Report-section adapters
# ---------------------------------------------------------------------------


def _section_text(sections: dict[str, object], key: str) -> str:
    """Flatten a structured report section into prompt-ready text.

    The sections are dicts of rows built for the HTML renderer; the prompts
    were written against pdfMax's plain-text equivalents, so each row is
    rendered as one line here.
    """
    section = sections.get(key)
    if not isinstance(section, dict):
        return ""
    payload = cast("dict[str, object]", section)
    for field in ("rows", "items", "entries"):
        value = payload.get(field)
        if isinstance(value, list):
            return "\n".join(
                _row_text(row) for row in cast("list[object]", value)
            )
    return ""


def _row_text(row: object) -> str:
    if isinstance(row, dict):
        cells = cast("dict[str, object]", row)
        return " | ".join(f"{k}={v}" for k, v in cells.items())
    return str(row)


def _color_summary(sections: dict[str, object]) -> str:
    text = _section_text(sections, "color_contrast")
    return text or "No significant non-black/white text colors detected."


def _declared_lang(pdf: pikepdf.Pdf) -> str | None:
    try:
        lang = pdf.Root.get("/Lang")
    except Exception:  # noqa: BLE001 — a malformed catalog is not fatal here
        return None
    return str(lang) if lang is not None else None


def _doc_title(pdf: pikepdf.Pdf) -> str:
    try:
        with pdf.open_metadata() as meta:
            # pikepdf's metadata mapping is untyped; narrow before use.
            title: object = cast("dict[str, object]", meta).get("dc:title")
            if isinstance(title, str):
                return title.strip()
    except Exception:  # noqa: BLE001 — metadata is optional
        pass
    return ""
