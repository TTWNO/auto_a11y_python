"""Heuristic candidate detection for unmarked headings and untagged lists.

Ported from pdfMax's ``find_heading_candidates`` (line ~7769) and
``find_list_candidates`` (line ~7862). Neither is a check on its own: each
produces a *shortlist* of elements that look like something they aren't
tagged as, which the AI semantic analysis then adjudicates with the page
image in hand.

The list finder was previously deferred — ``checks/lists.py`` names it as the
missing prerequisite for the "Untagged lists detected" check. It lives here
rather than in that module because both the check and the AI analysis consume
it, and neither should own the other's helper.

One shape difference from the original: pdfMax keys its font data by
``(font_name, font_size)`` with a per-pair character count, while this port's
:class:`~auto_a11y.pdf.audit.fonts.FontInfo` is keyed by name and carries a
*set* of sizes. Body size is therefore derived from the most-used font's
sizes rather than read off the winning pair directly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from auto_a11y.pdf.audit.fonts import FontAnalysis, is_bold_font
from auto_a11y.pdf.audit.structure import StructElement

# ---------------------------------------------------------------------------
# Unmarked headings
# ---------------------------------------------------------------------------

_HEADING_TAGS = frozenset({"H1", "H2", "H3", "H4", "H5", "H6"})

# Structural containers — promoting one of these to a heading would be a
# category error, not a fix.
_SKIP_TAGS = frozenset({
    "Document", "Part", "Sect", "Div", "Table", "TR", "TH", "TD",
    "L", "LI", "Lbl", "LBody", "TOC", "TOCI", "Form", "Link",
    "Annot", "Figure", "Art", "Note", "Reference", "BibEntry",
    "BlockQuote", "Caption", "Index", "NonStruct", "Private",
    "Ruby", "Warichu",
})

_MAX_CANDIDATE_TEXT = 120
_MAX_BOLD_TEXT = 80


@dataclass(frozen=True)
class HeadingCandidate:
    """A non-heading element whose typography suggests it heads a section."""

    index: int
    tag: str
    custom_tag: str
    text: str
    font_size: float
    is_bold: bool
    reason: str


def find_heading_candidates(
    elements: list[StructElement],
    font_analysis: FontAnalysis | None,
) -> list[HeadingCandidate]:
    """Shortlist elements that look like headings but are not tagged as one.

    Returns an empty list when font analysis is unavailable — without type
    sizes there is no signal to work from, and guessing from text length
    alone produced false positives in the original.
    """
    if font_analysis is None or not font_analysis.fonts:
        return []

    body_size = _body_font_size(font_analysis)
    if body_size <= 0:
        return []

    # Fuzzy text → (size, name) lookup, matching the original's approach of
    # joining structure elements to font runs by their leading characters.
    # Neither source carries a shared identifier, so this is the available join.
    text_to_font: dict[str, tuple[float, str]] = {}
    for info in font_analysis.fonts.values():
        sample = info.sample.strip()[:30].lower()
        if not sample or not info.sizes:
            continue
        text_to_font[sample] = (max(info.sizes), info.name)

    candidates: list[HeadingCandidate] = []
    for elem in elements:
        if elem.resolved_tag in _HEADING_TAGS or elem.resolved_tag in _SKIP_TAGS:
            continue
        text = (elem.text_content or elem.actual_text or "").strip()
        if not text or len(text) > _MAX_CANDIDATE_TEXT:
            continue
        # MCIDs mean the element actually carries marked content, i.e. it is
        # a leaf holding text rather than a wrapper around other elements.
        if not elem.mcids:
            continue

        matched = _match_font(text, text_to_font)
        if matched is None:
            continue
        font_size, font_name = matched
        bold = is_bold_font(font_name)

        reasons: list[str] = []
        if font_size >= body_size + 2:
            reasons.append(f"{font_size:g}pt (body: {body_size:g}pt)")
        if bold and len(text) <= _MAX_BOLD_TEXT and font_size >= body_size:
            reasons.append("bold, short text")
        if not reasons:
            continue

        candidates.append(HeadingCandidate(
            index=elem.index,
            tag=elem.resolved_tag,
            custom_tag=elem.custom_tag,
            text=text if len(text) <= _MAX_BOLD_TEXT else text[:77] + "...",
            font_size=font_size,
            is_bold=bold,
            reason="; ".join(reasons),
        ))

    candidates.sort(key=lambda c: c.index)
    return candidates


def _body_font_size(font_analysis: FontAnalysis) -> float:
    """The dominant text size — the most-used font's largest recorded size."""
    best_count = 0
    best_size = 0.0
    for info in font_analysis.fonts.values():
        if info.char_count > best_count and info.sizes:
            best_count = info.char_count
            best_size = max(info.sizes)
    return best_size


def _match_font(
    text: str, text_to_font: dict[str, tuple[float, str]]
) -> tuple[float, str] | None:
    """Best fuzzy font match for an element's leading text, or None."""
    text_clean = text[:30].lower()
    best: tuple[float, str] | None = None
    best_overlap = 0
    for snippet, font_info in text_to_font.items():
        if text_clean[:15] in snippet or snippet[:15] in text_clean:
            overlap = len(set(text_clean.split()) & set(snippet.split()))
            if overlap > best_overlap:
                best_overlap = overlap
                best = font_info
    return best


# ---------------------------------------------------------------------------
# Untagged lists
# ---------------------------------------------------------------------------

_BULLET_RE = re.compile(r"^\s*[•\-\*→►◦▪▸‣⁃]\s+")
_NUMBERED_RE = re.compile(r"^\s*\d{1,3}[.)]\s+")
_LETTERED_RE = re.compile(r"^\s*[a-zA-Z][.)]\s+")
_ROMAN_RE = re.compile(r"^\s*(?:i{1,3}|iv|vi{0,3}|ix|x{1,3})[.)]\s+", re.IGNORECASE)

_MIN_RUN = 2
"""A single marker-prefixed paragraph is not a list; two consecutive ones
are the shortest run that can be."""


@dataclass(frozen=True)
class ListItemPreview:
    """One paragraph inside a candidate run."""

    index: int
    text: str


@dataclass(frozen=True)
class ListCandidate:
    """A run of sibling paragraphs whose text carries list markers."""

    group_id: int
    parent_index: int
    pattern: str
    elements: list[ListItemPreview] = field(default_factory=list[ListItemPreview])


def find_list_candidates(elements: list[StructElement]) -> list[ListCandidate]:
    """Find runs of sibling ``P`` elements that read as list items.

    A run breaks on any non-``P`` sibling, any empty paragraph, any
    paragraph with no marker, and any change of marker style — so a
    bulleted run followed by a numbered run yields two candidates rather
    than one mixed group.
    """
    parent_children: dict[int, list[StructElement]] = {}
    for elem in elements:
        if elem.parent_index >= 0:
            parent_children.setdefault(elem.parent_index, []).append(elem)

    candidates: list[ListCandidate] = []
    group_id = 0

    def flush(
        run: list[StructElement], pattern: str | None, parent_index: int
    ) -> None:
        nonlocal group_id
        if len(run) < _MIN_RUN or pattern is None:
            return
        previews = [
            ListItemPreview(index=e.index, text=_preview(e))
            for e in run
        ]
        candidates.append(ListCandidate(
            group_id=group_id,
            parent_index=parent_index,
            pattern=pattern,
            elements=previews,
        ))
        group_id += 1

    for parent_index, children in parent_children.items():
        run: list[StructElement] = []
        pattern: str | None = None
        for child in children:
            if child.resolved_tag != "P":
                flush(run, pattern, parent_index)
                run, pattern = [], None
                continue
            # /ActualText is the authoritative text where present — it
            # sidesteps font-encoding damage in MCID-extracted text.
            text = (child.actual_text or child.text_content or "").strip()
            if not text:
                flush(run, pattern, parent_index)
                run, pattern = [], None
                continue
            found = _classify(text)
            if found is None:
                flush(run, pattern, parent_index)
                run, pattern = [], None
            elif pattern is None or pattern == found:
                run.append(child)
                pattern = found
            else:
                flush(run, pattern, parent_index)
                run, pattern = [child], found
        flush(run, pattern, parent_index)

    candidates.sort(key=lambda c: c.group_id)
    return candidates


def _classify(text: str) -> str | None:
    """The marker style a paragraph starts with, or None."""
    if _BULLET_RE.match(text):
        return "bullet"
    if _NUMBERED_RE.match(text):
        return "numbered"
    if _LETTERED_RE.match(text):
        return "lettered"
    if _ROMAN_RE.match(text):
        return "roman"
    return None


def _preview(elem: StructElement) -> str:
    text = (elem.actual_text or elem.text_content or "").strip()
    return text if len(text) <= 80 else text[:77] + "..."
