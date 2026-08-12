"""Pre-scan for text that leans on colour or styling to carry meaning.

Ported from pdfMax's ``VISUAL_REFERENCE_PATTERNS`` / ``scan_visual_reference_phrases``
(line ~8548). Deterministic and free: it finds the phrases ("required fields
are in red", "les champs obligatoires sont en rouge") that make a WCAG 1.4.1
problem *likely*, and hands them to the AI colour pass as leads rather than
verdicts — a document can say "shown in red" and still label every item.

The patterns cover English and French because that is what the source covers
and what this product is used against; a phrase in another language is caught
by the AI pass reading the page image, not here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from auto_a11y.pdf.audit.structure import StructElement

VISUAL_REFERENCE_PATTERNS: dict[str, tuple[str, ...]] = {
    "color_reference": (
        # English: instructional use of colour names as information carriers
        r'\b(highlighted?\s+in|marked?\s+in|shown?\s+in|displayed?\s+in|indicated?\s+by|coded?\s+in)\s+(red|green|blue|orange|yellow|purple|pink|gray|grey|colou?r)',
        r'\b(click|press|select|tap|see|refer\s+to)\s+(the\s+)?(red|green|blue|orange|yellow|purple)\s+(button|link|text|icon|section|item|area|box|dot|circle|indicator)',
        r'\bcolou?r[\s-]coded\b',
        r'\b(red|green|blue|orange|yellow|purple)\s+(means?|indicates?|signifies?|represents?|shows?)\b',
        r'\b(in|with)\s+(red|green|blue|orange|yellow|purple)\s+(text|font|colou?r)\b',
        # French: references to colour names
        r'\b(surligné|marqué|affiché|indiqué|mis)\s+(en|par)\s+(rouge|vert|bleu|orange|jaune|violet|rose|gris|couleur)',
        r'\b(cliquez|appuyez|sélectionnez|voir|consultez)\s+(sur\s+)?(le\s+|la\s+|l[’\']?)?(bouton|lien|texte|icône|section|élément|zone)\s+(rouge|vert|bleu|orange|jaune|violet)',
        r'\bcodé\s+(par|en)\s+couleur',
        r'\b(le\s+)?(rouge|vert|bleu|orange|jaune|violet)\s+(signifie|indique|représente|montre)\b',
        r'\b(en|avec|de)\s+(rouge|vert|bleu|orange|jaune|violet)\b',
    ),
    "visual_style_reference": (
        # English: references to visual styling as the sole indicator
        r'\b(bold(ed)?|italic(ized)?|underlined?|highlighted?|shaded)\s+(text|items?|fields?|entries?|words?)\b.{0,30}\b(indicate|mean|are|show|represent)',
        r'\bsee\s+the\s+(highlighted?|bold(ed)?|underlined?|colou?red|shaded)\s+(section|area|text|part|item)',
        r'\b(items?\s+in\s+bold|bold\s+items?)\s+(are|indicate|show|mean)\b',
        # French: references to visual styling
        r'\b(texte|éléments?|champs?|entrées?|mots?)\s+en\s+(gras|italique|soulign[ée]|surbrillance)\b.{0,30}\b(indique|signifie|sont|montre|représente)',
        r'\bvoir\s+(la|le|les|l[’\']?)\s+(section|zone|texte|partie|élément)\s+(en\s+)?(surbrillance|gras|soulign[ée]|couleur)',
        r'\b(éléments?\s+en\s+gras|en\s+gras)\s+(sont|indiquent?|montrent?|signifient?)\b',
    ),
    "status_by_appearance": (
        # English: status communicated by visual appearance alone
        r'\b(errors?|required|mandatory|optional|complete[d]?|active|inactive|enabled|disabled)\s+(are|is)\s+(shown|displayed|indicated|marked|highlighted)\s+(in|by|with)\s+(red|green|blue|orange|yellow|colou?r|bold)',
        r'\brequired\s+fields?\b.{0,15}\b(in\s+)?(red|colou?r)\b|\brequired\b.{0,15}\b(shown|displayed|marked|indicated|highlighted)\s+(in|by)\s+(red|colou?r)',
        r'\b(red|colored?)\s+asterisk',
        # French: status communicated by visual appearance
        r'\b(erreurs?|obligatoires?|requis|facultati[fv]|complété|acti[fv]|inacti[fv]|activé|désactivé)\s+(sont|est)\s+(affichée?s?|indiquée?s?|marquée?s?|surlignée?s?)\s+(en|par|avec)\s+(rouge|vert|bleu|orange|jaune|couleur|gras)',
        r'\bchamps?\s+obligatoires?\b.{0,15}\b(en\s+)?(rouge|couleur)\b|\bobligatoires?\b.{0,15}\b(affichée?s?|indiquée?s?|marquée?s?|surlignée?s?)\s+(en|par)\s+(rouge|couleur)',
        r'\bastérisque\s+(rouge|colorée?)\b',
    ),
}

_MIN_TEXT_LENGTH = 5
_CONTEXT_WINDOW = 40


@dataclass(frozen=True)
class VisualReference:
    """One phrase that refers to colour or styling to convey meaning."""

    element_index: int
    tag: str
    phrase: str
    context: str
    category: str


def scan_visual_references(
    elements: list[StructElement],
) -> list[VisualReference]:
    """Find colour/style-referencing phrases across the structure tree.

    Figure and Art alt text is skipped when the element carries no text of
    its own: alt text *describes an image*, so "a red warning triangle" is a
    correct description rather than an instruction that depends on colour.
    """
    compiled = {
        category: [re.compile(p, re.IGNORECASE) for p in patterns]
        for category, patterns in VISUAL_REFERENCE_PATTERNS.items()
    }

    found: list[VisualReference] = []
    for elem in elements:
        if elem.resolved_tag in ("Figure", "Art") and not elem.text_content:
            continue
        text = elem.text_content or ""
        if len(text.strip()) < _MIN_TEXT_LENGTH:
            continue

        for category, patterns in compiled.items():
            for pattern in patterns:
                for match in pattern.finditer(text):
                    found.append(VisualReference(
                        element_index=elem.index,
                        tag=elem.resolved_tag,
                        phrase=match.group(0),
                        context=_context(text, match.start(), match.end()),
                        category=category,
                    ))

    # One phrase can match several patterns in a category; report it once.
    seen: set[tuple[int, str]] = set()
    deduped: list[VisualReference] = []
    for ref in found:
        key = (ref.element_index, ref.phrase.lower())
        if key not in seen:
            seen.add(key)
            deduped.append(ref)
    return deduped


def _context(text: str, start: int, end: int) -> str:
    """The matched phrase with surrounding words, ellipsised at the edges."""
    left = max(0, start - _CONTEXT_WINDOW)
    right = min(len(text), end + _CONTEXT_WINDOW)
    context = text[left:right].replace("\n", " ").strip()
    if left > 0:
        context = "..." + context
    if right < len(text):
        context = context + "..."
    return context
