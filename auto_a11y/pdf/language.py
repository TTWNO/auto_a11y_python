"""Shared PDF language detection.

Used by both the scraper (cheap hint extracted from the catalog) and the
audit engine (thorough analysis with text-sample fallbacks). This minimal
implementation handles only the catalog ``/Lang`` extraction; the
word-frequency and AI fallbacks land alongside the audit engine port in
Phase 3.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal

import pikepdf

logger = logging.getLogger(__name__)


DetectionMethod = Literal['catalog', 'info_dict', 'word_frequency', 'ai', 'none']


@dataclass(frozen=True)
class LanguageDetection:
    """Result of detecting language on a PDF."""

    declared_lang: str | None
    detected_lang: str | None
    confidence: float | None
    method: DetectionMethod
    error: str | None


def detect_pdf_language(source: Path | BinaryIO) -> LanguageDetection:
    """Detect the language of a PDF.

    The current implementation reads ``/Lang`` from the document catalog
    (the cheapest, most authoritative source). When ``/Lang`` is absent the
    result reports ``method='none'``; future phases will add info-dict,
    word-frequency, and AI fallbacks.

    Args:
        source: A filesystem path or open binary stream pointing at a PDF.

    Returns:
        A ``LanguageDetection`` describing what was found. The ``error``
        field is set when pikepdf cannot parse the input.
    """
    try:
        with pikepdf.open(source) as pdf:
            try:
                lang_obj = pdf.Root["/Lang"]
            except KeyError:
                return LanguageDetection(None, None, None, 'none', None)
            return LanguageDetection(
                declared_lang=str(lang_obj),
                detected_lang=None,
                confidence=None,
                method='catalog',
                error=None,
            )
    except pikepdf.PdfError as exc:
        return LanguageDetection(None, None, None, 'none', str(exc))
