"""JSON schemas for the AI analyses.

Transcribed from pdfMax's ``pdf_accessibility_audit.py`` — the schema shapes
are the contract the prompts were written against, so they carry across
field-for-field rather than being redesigned. Every object sets
``additionalProperties: False`` and lists every property in ``required``,
which is what the structured-outputs feature needs to constrain generation.
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Semantic analysis — pdfMax AI_ANALYSIS_SCHEMA
# ---------------------------------------------------------------------------

SEMANTIC_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": [
                            "missing_semantic_markup",
                            "alt_text_quality",
                            "reading_order",
                            "content_grouping",
                            "form_accessibility",
                            "additional_concern",
                        ],
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "important", "advisory"],
                    },
                    "title": {"type": "string"},
                    "elements": {"type": "string"},
                    "problem": {"type": "string"},
                    "recommendation": {"type": "string"},
                    "impact": {"type": "string"},
                },
                "required": [
                    "category", "severity", "title", "elements",
                    "problem", "recommendation", "impact",
                ],
                "additionalProperties": False,
            },
        },
        "overall_assessment": {"type": "string"},
        "unmarked_headings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "element_index": {"type": "integer"},
                    "suggested_level": {"type": "integer"},
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                    "reasoning": {"type": "string"},
                },
                "required": [
                    "element_index", "suggested_level", "confidence", "reasoning",
                ],
                "additionalProperties": False,
            },
        },
        "potential_lists": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "group_id": {"type": "integer"},
                    "is_list": {"type": "boolean"},
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                    "reasoning": {"type": "string"},
                },
                "required": ["group_id", "is_list", "confidence", "reasoning"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "issues", "overall_assessment", "unmarked_headings", "potential_lists",
    ],
    "additionalProperties": False,
}

# ---------------------------------------------------------------------------
# Alt-text adequacy — pdfMax ALT_TEXT_ADEQUACY_SCHEMA
# ---------------------------------------------------------------------------

ALT_TEXT_ADEQUACY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "assessments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "element_index": {"type": "integer"},
                    "accuracy": {"type": "integer"},
                    "completeness": {"type": "integer"},
                    "context_relevance": {"type": "integer"},
                    "conciseness": {"type": "integer"},
                    "overall": {"type": "integer"},
                    "is_adequate": {"type": "boolean"},
                    "issues": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "element_index", "accuracy", "completeness",
                    "context_relevance", "conciseness", "overall",
                    "is_adequate", "issues",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["assessments"],
    "additionalProperties": False,
}

# ---------------------------------------------------------------------------
# Images of text
# ---------------------------------------------------------------------------
# pdfMax asked for free text here ("NO_TEXT" / "ALL_REAL_TEXT" sentinels) and
# then string-matched the reply. Constraining it to a schema removes the
# sentinel parsing — a model that phrases the negative case differently no
# longer reads as a positive finding.

IMAGE_TEXT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "contains_text": {"type": "boolean"},
        "text": {"type": "string"},
    },
    "required": ["contains_text", "text"],
    "additionalProperties": False,
}

PAGE_IMAGE_TEXT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "location": {"type": "string"},
                    "is_image_of_text": {"type": "boolean"},
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                },
                "required": [
                    "text", "location", "is_image_of_text", "confidence",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["findings"],
    "additionalProperties": False,
}

# ---------------------------------------------------------------------------
# WCAG 1.4.1 use of colour — pdfMax COLOR_USE_ANALYSIS_SCHEMA
# ---------------------------------------------------------------------------

COLOR_USE_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": [
                            "text_referencing_color",
                            "chart_graph_infographic",
                            "color_coded_content",
                            "form_color_indicator",
                            "link_color_only",
                            "other_color_dependence",
                        ],
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["fail", "warning"],
                    },
                    "page": {"type": "integer"},
                    "description": {"type": "string"},
                    "has_non_color_alternative": {"type": "boolean"},
                    "what_color_conveys": {"type": "string"},
                    "recommendation": {"type": "string"},
                },
                "required": [
                    "category", "severity", "page", "description",
                    "has_non_color_alternative", "what_color_conveys",
                    "recommendation",
                ],
                "additionalProperties": False,
            },
        },
        "overall_assessment": {"type": "string"},
        "pass": {"type": "boolean"},
    },
    "required": ["findings", "overall_assessment", "pass"],
    "additionalProperties": False,
}

# ---------------------------------------------------------------------------
# Executive summary — pdfMax EXECUTIVE_SUMMARY_SCHEMA
# ---------------------------------------------------------------------------

EXECUTIVE_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "overall_rating": {
            "type": "string",
            "enum": ["good", "needs_work", "poor"],
        },
        "headline": {"type": "string"},
        "plain_language_summary": {"type": "string"},
        "key_strengths": {"type": "array", "items": {"type": "string"}},
        "priority_actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["action", "why"],
                "additionalProperties": False,
            },
        },
        "who_is_affected": {"type": "string"},
        "estimated_effort": {"type": "string"},
    },
    "required": [
        "overall_rating", "headline", "plain_language_summary",
        "key_strengths", "priority_actions", "who_is_affected",
        "estimated_effort",
    ],
    "additionalProperties": False,
}

# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

LANGUAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "language_code": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["language_code", "confidence"],
    "additionalProperties": False,
}

# ---------------------------------------------------------------------------
# Non-text contrast (WCAG 1.4.11)
# ---------------------------------------------------------------------------

NON_TEXT_CONTRAST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": [
                            "form_field_border", "form_field_boundary",
                            "button_boundary", "graphical_element",
                            "icon", "divider_line", "chart_element",
                        ],
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["fail", "warning", "pass"],
                    },
                    "page": {"type": "integer"},
                    "description": {"type": "string"},
                    "recommendation": {"type": "string"},
                },
                "required": [
                    "category", "severity", "page", "description",
                    "recommendation",
                ],
                "additionalProperties": False,
            },
        },
        "overall_assessment": {"type": "string"},
        "pass": {"type": "boolean"},
    },
    "required": ["findings", "overall_assessment", "pass"],
    "additionalProperties": False,
}

# ---------------------------------------------------------------------------
# Required-field visual indicators (WCAG 3.3.2)
# ---------------------------------------------------------------------------

REQUIRED_INDICATOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "has_legend": {"type": "boolean"},
        "legend_text": {"type": "string"},
        "field_results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field_name": {"type": "string"},
                    "has_visual_indicator": {"type": "boolean"},
                    "indicator_type": {
                        "type": "string",
                        "enum": ["asterisk", "text", "icon", "color", "none"],
                    },
                    "indicator_description": {"type": "string"},
                },
                "required": [
                    "field_name", "has_visual_indicator", "indicator_type",
                    "indicator_description",
                ],
                "additionalProperties": False,
            },
        },
        "visual_only_required": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "page": {"type": "integer"},
                },
                "required": ["description", "page"],
                "additionalProperties": False,
            },
        },
        "overall_assessment": {"type": "string"},
        "pass": {"type": "boolean"},
    },
    "required": [
        "has_legend", "legend_text", "field_results",
        "visual_only_required", "overall_assessment", "pass",
    ],
    "additionalProperties": False,
}
