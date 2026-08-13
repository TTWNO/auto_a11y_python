"""Plain-language executive summary and AI language detection.

Ported from pdfMax's ``generate_executive_summary_ai`` (~9184) and
``detect_language_ai`` (~4414).

The summary is written for the person who commissioned the audit rather than
the person who will fix it — the prompt explicitly bans "tagged PDF",
"structure tree", "PDF/UA" and the rest of the vocabulary that makes a report
unreadable to the manager or content owner who has to act on it. That
audience framing is the whole point of the pass; a deterministic counterpart
already exists in ``report_sections.build_executive_summary`` for audits
that run without AI.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from auto_a11y.pdf.audit.ai.client import AIClient, text_block
from auto_a11y.pdf.audit.ai.schemas import (
    EXECUTIVE_SUMMARY_SCHEMA,
    LANGUAGE_SCHEMA,
)
from auto_a11y.pdf.models import CheckResult

logger = logging.getLogger(__name__)

_SUMMARY_MAX_TOKENS = 8000
_LANGUAGE_MAX_TOKENS = 1500
_LANGUAGE_SAMPLE_CHARS = 3000
_ISO_639_1_RE = re.compile(r"^[a-z]{2}$")


def generate_executive_summary(
    client: AIClient,
    *,
    check_results: list[CheckResult],
    ai_counts: dict[str, int],
    pdf_filename: str,
) -> dict[str, Any] | None:
    """Write a non-technical executive summary of the audit."""
    passed = sum(1 for r in check_results if r.result == "PASS")
    failed = sum(1 for r in check_results if r.result == "FAIL")
    warned = sum(1 for r in check_results if r.result == "WARN")
    total = passed + failed + warned

    lines = [
        f"Document: {pdf_filename}",
        f"Results: {passed} passed, {failed} failed, {warned} warnings out of {total} checks",
    ]
    if sum(ai_counts.values()) > 0:
        lines.append(
            f"AI analysis found: {ai_counts.get('critical', 0)} critical, "
            + f"{ai_counts.get('important', 0)} important, "
            + f"{ai_counts.get('advisory', 0)} advisory issues"
        )
    lines.append("\nFailed checks:")
    lines += [f"- FAIL: {r.name} — {r.details}" for r in check_results if r.result == "FAIL"]
    lines.append("\nWarnings:")
    lines += [f"- WARN: {r.name} — {r.details}" for r in check_results if r.result == "WARN"]
    lines.append("\nPassed checks:")
    lines += [f"- PASS: {r.name}" for r in check_results if r.result == "PASS"]
    results_text = "\n".join(lines)

    prompt = (
        "You are writing an executive summary of a PDF accessibility audit for a non-technical audience — "
        "managers, content owners, or compliance officers who may not know PDF internals.\n\n"
        "Write in plain language. Avoid jargon like 'tagged PDF', 'structure tree', 'MCID', 'PDF/UA'. "
        "Instead explain the real-world impact: who can't use the document and why.\n\n"
        "The headline should be a single sentence verdict (e.g., 'This document has significant accessibility barriers' "
        "or 'This document is mostly accessible with minor issues').\n\n"
        "For priority_actions, list the 3-5 most important things to fix, in order of impact. "
        "Explain each in one sentence that a non-technical person can act on.\n\n"
        "For who_is_affected, describe which real users are impacted (e.g., 'People using screen readers cannot...').\n\n"
        "For estimated_effort, give a rough sense: 'Quick fixes (under an hour)', 'Moderate effort (a few hours)', "
        "or 'Significant rework needed'.\n\n"
        f"Here are the audit results:\n\n{results_text}"
    )

    return client.json_call(
        [text_block(prompt)],
        schema=EXECUTIVE_SUMMARY_SCHEMA,
        max_tokens=_SUMMARY_MAX_TOKENS,
        effort="medium",
    )


def detect_language(client: AIClient, text: str) -> str | None:
    """Detect a document's primary language as an ISO 639-1 code.

    Document-level only. The deterministic detector in
    :mod:`auto_a11y.pdf.language` handles the common cases; this is the
    fallback for text it cannot classify confidently.
    """
    sample = text[:_LANGUAGE_SAMPLE_CHARS]
    if not sample.strip():
        return None

    result = client.json_call(
        [text_block(
            "What language is this text written in? Reply with the ISO 639-1 "
            + "two-letter language code (e.g. en, fr, es, de, zh, ar, ja) and "
            + f"your confidence.\n\n{sample}"
        )],
        schema=LANGUAGE_SCHEMA,
        max_tokens=_LANGUAGE_MAX_TOKENS,
        effort="low",
    )
    if result is None:
        return None
    code = str(result.get("language_code") or "").strip().lower()[:2]
    return code if _ISO_639_1_RE.match(code) else None
