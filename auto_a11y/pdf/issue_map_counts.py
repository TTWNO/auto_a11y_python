"""Tally FAIL/WARN counts from a PDF's cached pdfMax issue_map.json.

Project-detail and website-detail aggregations call this helper for
every audited :class:`~auto_a11y.models.pdf_document.PdfDocument` and
add the result to the page-level Mongo aggregation, so the global
"violations" / "warnings" totals reflect both HTML and PDF audits.

The helper is intentionally read-only and side-effect-free apart from
WARNING-level logging when an audited PDF has no readable cache. The
issue-map JSON remains the single source of truth — no per-PDF count
field is persisted.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TypeGuard

from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus
from auto_a11y.pdf.storage import PdfStorage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PdfIssueCounts:
    """Counts of FAIL (violations) and WARN (warnings) for one PDF.

    Frozen so callers can sum a list without worrying about aliasing;
    ``__add__`` returns a new instance. The "no audit yet" / "missing
    cache" / "malformed JSON" cases all collapse into ``PdfIssueCounts(0, 0)``,
    so callers don't need to branch.
    """

    violations: int
    warnings: int

    def __add__(self, other: "PdfIssueCounts") -> "PdfIssueCounts":
        return PdfIssueCounts(
            violations=self.violations + other.violations,
            warnings=self.warnings + other.warnings,
        )


_ZERO = PdfIssueCounts(0, 0)


def count_issues(pdf: PdfDocument, storage: PdfStorage) -> PdfIssueCounts:
    """Read the cached ``*_issue_map.json`` and tally FAIL/WARN.

    Returns :data:`_ZERO` when the PDF has not been audited, when the
    cache directory or file is missing, when the JSON is malformed, or
    when the file's top-level shape is unexpected. Logs a WARNING in
    the AUDITED-but-missing-cache and malformed-JSON cases so the
    operator notices the inconsistency without the failure 500-ing
    the page.

    Expected JSON shape::

        {"version": 1, "issues": [{"check_result": "FAIL", ...}, ...]}

    Per ``pdfMax/python/checker/pdf_accessibility_audit.py``. Any other
    top-level shape (bare list, missing ``"issues"`` key, non-list
    value at ``"issues"``) is treated as malformed → 0/0 + warning.
    Within the list, only entries where ``check_result`` is exactly
    ``"FAIL"`` or ``"WARN"`` count.
    """
    if pdf.status is not PdfDocumentStatus.AUDITED:
        return _ZERO

    pdf_path = storage.local_path(pdf)
    cache_dir = pdf_path.parent / "pdfmax-report"
    if not cache_dir.is_dir():
        logger.warning(
            "PDF %s is AUDITED but missing pdfmax-report cache at %s",
            pdf.id, cache_dir,
        )
        return _ZERO

    candidates = sorted(cache_dir.glob("*_issue_map.json"))
    if not candidates:
        logger.warning(
            "PDF %s is AUDITED but no issue_map.json found in %s",
            pdf.id, cache_dir,
        )
        return _ZERO

    return _tally(pdf, candidates[0])


def _is_str_obj_dict(val: object) -> TypeGuard[dict[str, object]]:
    """Return True iff *val* is a dict (JSON guarantees string keys).

    ``json.loads`` always produces ``str``-keyed dicts for JSON objects.
    The ``TypeGuard`` annotation informs pyright of the narrowed type;
    the runtime check is ``isinstance(val, dict)`` only, which is
    sufficient because the JSON spec disallows non-string keys.
    """
    return isinstance(val, dict)


def _is_obj_list(val: object) -> TypeGuard[list[object]]:
    """Return True iff *val* is a list (element type treated as object)."""
    return isinstance(val, list)


def _tally(pdf: PdfDocument, json_path: Path) -> PdfIssueCounts:
    try:
        raw = json_path.read_bytes()
    except OSError as exc:
        logger.warning(
            "PDF %s: failed to read issue_map.json at %s: %s",
            pdf.id, json_path, exc,
        )
        return _ZERO

    try:
        data: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning(
            "PDF %s: malformed issue_map.json at %s: %s",
            pdf.id, json_path, exc,
        )
        return _ZERO

    if not _is_str_obj_dict(data):
        logger.warning(
            "PDF %s: malformed issue_map.json at %s (top-level not an object)",
            pdf.id, json_path,
        )
        return _ZERO

    issues = data.get("issues")
    if not _is_obj_list(issues):
        # Missing key, or "issues" is not a list — both treated as zero.
        return _ZERO

    violations = 0
    warnings = 0
    for entry in issues:
        if not _is_str_obj_dict(entry):
            continue
        result = entry.get("check_result")
        if result == "FAIL":
            violations += 1
        elif result == "WARN":
            warnings += 1
    return PdfIssueCounts(violations=violations, warnings=warnings)
