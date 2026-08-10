"""Shared PDF language detection.

Used by both the scraper (cheap hint extracted from the catalog) and the
audit engine (thorough analysis with text-sample fallbacks).

Two layers live here: :func:`detect_pdf_language` reads the catalog
``/Lang``, which is authoritative when present, and
:func:`detect_language_from_text` guesses from a text sample for the
common case of a document that declares nothing at all.
"""
from __future__ import annotations

import logging
import re
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

# ---------------------------------------------------------------------------
# Word-frequency detection
# ---------------------------------------------------------------------------

# Common function words, used to tell English from French by how often
# they appear. Ported from pdfMax's checker: deduplicated (its French list
# repeated two entries) and sorted so future diffs stay readable.
_ENGLISH_WORDS: frozenset[str] = frozenset({
    "a", "about", "after", "again", "against", "all", "also", "always",
    "am", "an", "and", "another", "any", "are", "as", "at", "back",
    "be", "because", "become", "been", "before", "being", "between",
    "both", "but", "by", "can", "children", "come", "could", "day",
    "did", "different", "do", "does", "down", "during", "each", "early",
    "end", "even", "every", "find", "first", "following", "for", "from",
    "get", "give", "go", "good", "great", "had", "hand", "has", "have",
    "having", "he", "head", "help", "her", "here", "high", "him", "his",
    "home", "house", "how", "however", "i", "if", "important", "in",
    "information", "into", "is", "it", "its", "just", "know", "large",
    "last", "life", "like", "long", "look", "made", "make", "many",
    "me", "might", "more", "most", "much", "must", "my", "need",
    "never", "new", "next", "no", "not", "now", "number", "of", "off",
    "often", "old", "on", "one", "only", "or", "other", "our", "out",
    "over", "own", "part", "people", "place", "point", "right", "same",
    "say", "see", "she", "should", "since", "small", "so", "some",
    "state", "still", "such", "take", "than", "that", "the", "their",
    "them", "then", "there", "these", "they", "thing", "think", "this",
    "those", "though", "three", "through", "time", "to", "too", "two",
    "under", "until", "up", "us", "use", "very", "want", "was", "way",
    "we", "well", "were", "what", "when", "where", "which", "while",
    "who", "why", "will", "with", "without", "work", "world", "would",
    "year", "you", "young", "your"
})


_FRENCH_WORDS: frozenset[str] = frozenset({
    "ainsi", "alors", "annee", "apres", "assez", "au", "aucun", "aussi",
    "autour", "autre", "aux", "avant", "avec", "avoir", "beaucoup",
    "bien", "ce", "cela", "cependant", "ces", "cette", "ceux", "chaque",
    "chez", "chose", "comme", "comment", "contre", "dans", "de", "deja",
    "depuis", "dernier", "des", "devant", "dire", "donc", "dont", "du",
    "elle", "elles", "en", "encore", "enfin", "entre", "est", "et",
    "ete", "etre", "faire", "fait", "femme", "fois", "gens",
    "gouvernement", "grand", "homme", "ici", "il", "ils", "jamais",
    "je", "jour", "jusqu", "la", "le", "les", "leur", "leurs", "lui",
    "maintenant", "mais", "me", "meme", "merci", "moins", "mois", "mon",
    "monde", "ne", "nom", "notre", "nous", "nouveau", "on", "ont",
    "par", "parce", "part", "pas", "pays", "pendant", "petit", "peu",
    "peut", "plus", "plusieurs", "pour", "pourquoi", "quand", "que",
    "quel", "quelque", "qui", "reste", "rien", "sans", "se", "ses",
    "seul", "seulement", "si", "son", "sont", "sous", "souvent", "sur",
    "surtout", "tant", "temps", "toujours", "tous", "tout", "toute",
    "tres", "trop", "un", "une", "vers", "vie", "voir", "votre", "vous"
})

TextLanguageGuess = Literal['en', 'fr', 'url', 'email', 'ambiguous', 'unknown']

_WORD_RE = re.compile(r"[a-z\u00e0-\u00ff]+")
_URL_RE = re.compile(r"https?://|www\.|\.ca/|\.com/|\.org/")
_EMAIL_RE = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}")

# Below this many words, a string that looks like a URL or an email is
# almost certainly *only* that, so reporting it as prose in some language
# would be wrong. Above it, an incidental URL shouldn't outvote the prose
# around it.
_SHORT_TEXT_WORDS = 15

# A language needs at least this share of the text to be claimed at all;
# below it the sample is treated as too weak to call either way.
_MIN_RATIO = 0.1


def detect_language_from_text(text: str) -> tuple[TextLanguageGuess, float]:
    """Guess whether ``text`` is English or French from word frequency.

    Returns the guess and a confidence between 0.0 and 1.0, where the
    confidence is the share of words matching the winning language's
    function-word list.

    The non-language verdicts matter to callers: ``'url'`` and ``'email'``
    mean the sample is a machine-readable token rather than prose, and
    ``'ambiguous'`` means both languages scored equally — neither should
    be written into a PDF's ``/Lang``, because declaring the wrong
    language makes a screen reader pronounce the whole document with the
    wrong voice.
    """
    if not text or len(text.strip()) < 3:
        return ('unknown', 0.0)

    words = _WORD_RE.findall(text.lower())
    total = len(words)
    if total == 0:
        return ('unknown', 0.0)

    lowered = text.lower()
    if total < _SHORT_TEXT_WORDS:
        if _URL_RE.search(lowered):
            return ('url', 0.9)
        if _EMAIL_RE.search(lowered):
            return ('email', 0.9)

    en_ratio = sum(1 for w in words if w in _ENGLISH_WORDS) / total
    fr_ratio = sum(1 for w in words if w in _FRENCH_WORDS) / total

    if en_ratio > fr_ratio and en_ratio > _MIN_RATIO:
        return ('en', min(1.0, en_ratio))
    if fr_ratio > en_ratio and fr_ratio > _MIN_RATIO:
        return ('fr', min(1.0, fr_ratio))
    if en_ratio == fr_ratio and en_ratio > 0:
        return ('ambiguous', en_ratio)
    return ('unknown', 0.0)
