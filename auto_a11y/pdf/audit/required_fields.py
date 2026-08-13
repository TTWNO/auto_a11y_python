"""Which form fields are required, and whether a reader can tell.

The ``/Ff`` Required flag tells assistive technology a field is
mandatory. It tells a sighted user nothing — for them the signal is an
asterisk, the word "required", or a legend at the top of the form saying
what the asterisks mean. WCAG 3.3.2 wants both, and this collector
gathers what a check needs to say whether both are present.

Ported from pdfMax's ``check_required_field_indicators`` (line ~8241).
Two halves: the per-field metadata scan, which sees only what the field
itself declares, and the legend scan, which looks through the document's
text for an instruction covering all of them. The legend matters because
a form with a legend needs no per-field wording — the asterisks alone
carry the meaning once something has explained them.

What this cannot see is the page. An asterisk drawn beside a field as
page text — not in the field's name, not in its tooltip — is invisible
here and visible to a reader. That gap is what pdfMax's AI pass exists
to close, and why the deterministic verdict warns rather than fails.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pikepdf

from auto_a11y.pdf.audit import pikepdf_helpers
from auto_a11y.pdf.audit.colors import (
    build_page_lookups,
    field_label,
    flatten_form_fields,
    resolve_field_page,
)
from auto_a11y.pdf.audit.structure import StructElement

__all__ = [
    "RequiredFieldInfo",
    "RequiredFields",
    "collect_required_fields",
]


#: ``/Ff`` bit 2 — the Required flag (PDF 1.7 §12.7.3.1, Table 221).
_REQUIRED_FLAG: int = 0x2

#: Words that mark a field as required when they appear in its name or
#: tooltip. Both official languages, since the forms this audits are
#: bilingual.
_REQUIRED_WORDS: tuple[str, ...] = (
    "required", "mandatory", "obligatoire", "requis",
)

#: Instructions that explain a form-wide required-field convention.
#: Transcribed from pdfMax's ``legend_patterns`` (line ~8357).
_LEGEND_PATTERNS: tuple[re.Pattern[str], ...] = (
    # English
    re.compile(
        r"\*\s*(indicates?|marks?|denotes?|means?)\s+"
        + r"(required|mandatory|obligatoire)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(required|mandatory|obligatoire)\s+(fields?|items?|questions?)\s+"
        + r"(are\s+)?(marked|indicated|shown|denoted)\s+(with|by)\s+\*",
        re.IGNORECASE,
    ),
    re.compile(
        r"fields?\s+marked\s+with\s+(\*|an?\s+asterisk)\s+(are|is)\s+"
        + r"(required|mandatory)",
        re.IGNORECASE,
    ),
    re.compile(r"all\s+fields?\s+(are\s+)?required", re.IGNORECASE),
    re.compile(r"\*\s*=\s*(required|mandatory|obligatoire)", re.IGNORECASE),
    # French
    re.compile(
        r"\*\s*(indique|marque|signifie|désigne)\s+(un\s+champ\s+)?"
        + r"(obligatoire|requis)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(champs?|zones?)\s+(obligatoires?|requis)\s+(sont\s+)?"
        + r"(marquée?s?|indiquée?s?)\s+(par|avec|d[’']?un)\s+\*",
        re.IGNORECASE,
    ),
    re.compile(
        r"(champs?|zones?)\s+marquée?s?\s+(d[’']?un\s+)?"
        + r"(astérisque|\*)\s+(sont\s+)?(obligatoires?|requis)",
        re.IGNORECASE,
    ),
    re.compile(r"tous\s+les\s+champs\s+sont\s+obligatoires", re.IGNORECASE),
    re.compile(
        r"les\s+champs\s+(marquée?s?|identifiée?s?|indiquée?s?)\s+"
        + r"(par|avec|d[’']?)\s+(un\s+)?(astérisque|\*)\s+sont\s+"
        + r"(obligatoires?|requis)",
        re.IGNORECASE,
    ),
)

#: Characters of surrounding text kept either side of a legend match, so
#: the report can show the instruction in context.
_LEGEND_CONTEXT: int = 20


@dataclass(frozen=True)
class RequiredFieldInfo:
    """One field carrying the ``/Ff`` Required flag.

    Attributes:
        name: the field's ``/T``.
        tooltip: the field's ``/TU``, or ``""``.
        field_type: ``Text``, ``Button``, ``Dropdown``, ``Signature`` or
            the raw ``/FT``.
        page: 0-based page index the widget sits on.
        rect: the widget rectangle, or ``None`` when it has none.
        has_indicator_in_metadata: whether the name or tooltip carries an
            asterisk or a required-word.
        indicator_detail: which of the two matched, or ``"none"``.
    """

    name: str
    tooltip: str
    field_type: str
    page: int
    rect: tuple[float, float, float, float] | None
    has_indicator_in_metadata: bool
    indicator_detail: str


@dataclass(frozen=True)
class RequiredFields:
    """Every required field, plus whatever legend explains them.

    Attributes:
        fields: the required fields, in AcroForm order.
        has_legend: whether the document text explains a required-field
            convention.
        legend_text: the matched instruction with surrounding context.
    """

    fields: list[RequiredFieldInfo]
    has_legend: bool
    legend_text: str

    @property
    def with_indicator(self) -> list[RequiredFieldInfo]:
        """Required fields whose own metadata says so."""
        return [f for f in self.fields if f.has_indicator_in_metadata]

    @property
    def missing_indicator(self) -> list[RequiredFieldInfo]:
        """Required fields whose metadata gives a reader no hint."""
        return [f for f in self.fields if not f.has_indicator_in_metadata]


def collect_required_fields(
    pdf: pikepdf.Pdf, elements: list[StructElement]
) -> RequiredFields | None:
    """Collect required fields and any legend explaining them.

    Returns ``None`` when the document has no AcroForm, no fields, or no
    field with the Required flag — all three mean the check has nothing
    to judge, and the check reports that as ``NA`` rather than a pass.
    """
    acroform = pikepdf_helpers.get_dict(pdf.Root, "/AcroForm")
    if acroform is None:
        return None
    raw_fields = pikepdf_helpers.get_array(acroform, "/Fields")
    if raw_fields is None or len(raw_fields) == 0:
        return None
    flat = flatten_form_fields(raw_fields)
    if not flat:
        return None

    page_obj_to_num, annot_obj_to_page = build_page_lookups(pdf)
    required: list[RequiredFieldInfo] = []
    for field in flat:
        flags = pikepdf_helpers.get_int(field, "/Ff") or 0
        if not flags & _REQUIRED_FLAG:
            continue

        name = pikepdf_helpers.get_string(field, "/T") or ""
        tooltip = pikepdf_helpers.get_string(field, "/TU") or ""
        _, field_type = field_label(field)
        combined = f"{tooltip} {name}".lower()
        has_star = "*" in combined
        has_word = any(word in combined for word in _REQUIRED_WORDS)
        required.append(RequiredFieldInfo(
            name=name,
            tooltip=tooltip,
            field_type=field_type,
            page=resolve_field_page(field, page_obj_to_num, annot_obj_to_page),
            rect=_rect(field),
            has_indicator_in_metadata=has_star or has_word,
            indicator_detail=(
                "* in name/tooltip" if has_star
                else "required text in name/tooltip" if has_word
                else "none"
            ),
        ))

    if not required:
        return None

    has_legend, legend_text = _find_legend(elements)
    return RequiredFields(
        fields=required, has_legend=has_legend, legend_text=legend_text
    )


def _rect(
    field: pikepdf.Dictionary,
) -> tuple[float, float, float, float] | None:
    """The widget rectangle as four floats, or ``None``."""
    rect = pikepdf_helpers.get_array(field, "/Rect")
    if rect is None or len(rect) < 4:
        return None
    try:
        values = [float(rect[i]) for i in range(4)]
    except (TypeError, ValueError):
        return None
    return (values[0], values[1], values[2], values[3])


def _find_legend(elements: list[StructElement]) -> tuple[bool, str]:
    """The first required-field instruction in the document text."""
    for element in elements:
        text = (element.text_content or "").strip()
        if not text:
            continue
        for pattern in _LEGEND_PATTERNS:
            match = pattern.search(text)
            if match is None:
                continue
            start = max(0, match.start() - _LEGEND_CONTEXT)
            end = min(len(text), match.end() + _LEGEND_CONTEXT)
            return True, text[start:end].strip()
    return False, ""
