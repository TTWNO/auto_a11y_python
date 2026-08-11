"""Language-related accessibility checks.

Checks ported from pdfMax's
``python/checker/pdf_accessibility_audit.py`` (lines ~4453-5400):

* :func:`check_document_language` — document catalog ``/Lang`` set
  (WCAG 3.1.1, PDF/UA).
* :func:`check_lang_values_valid_bcp47` — every ``/Lang`` value (catalog
  and structure-element level) parses as a valid BCP 47 tag
  (Matterhorn 11-003).
* :func:`check_annotation_language_determinable` — every annotation
  with ``/Contents`` has a determinable language via element ``/Lang``,
  page ``/Lang``, or document ``/Lang`` (Matterhorn 11-004).
* :func:`check_form_field_tooltip_language_determinable` — every
  AcroForm field with a ``/TU`` tooltip has a determinable language via
  field ``/Lang`` or document ``/Lang`` (Matterhorn 11-005).
* :func:`check_metadata_language_determinable` — document language is
  determinable from either the catalog ``/Lang`` or an XMP ``xml:lang``
  attribute on the metadata stream (Matterhorn 11-006).
* :func:`check_abbreviation_expansion` — heuristically-detected
  abbreviation spans carry an ``/E`` expansion attribute, either
  directly or via an ``/A`` attribute dictionary (WCAG 3.1.4 / PDF8).

Mirrors the convention established in
:mod:`auto_a11y.pdf.audit.checks.headings`: each check is a plain
function ``(ctx) -> list[CheckResult]`` and the module exposes a
:data:`LANGUAGE_CHECKS` registry list. ``CheckResult`` ``name`` and
``standard`` strings match pdfMax verbatim so Phase 6's check catalogue
can map them.

Deferred from this commit:

* ``Language of parts markup`` (WCAG 3.1.2) — pdfMax inspects each
  element's text content with a ``detect_language`` helper (and an
  optional Claude inference fallback) to flag foreign-language passages
  that lack ``/Lang``. Neither helper is exposed on
  :class:`AuditContext` yet. TODO(phase 4 followup): add a text
  language-detection helper to :mod:`auto_a11y.pdf.language` and surface
  its output on :class:`AuditContext` before porting this check.
* ``Cross-language link targets identified`` (WCAG 3.1.2 best practice)
  — same dependency on text-content language inference, plus URL
  pattern matching for ``/en/``, ``/fr/`` etc. TODO(phase 4 followup):
  port together with the ``Language of parts markup`` check.
"""
from __future__ import annotations

import re
from collections.abc import Callable

import pikepdf

from auto_a11y.pdf.audit import pikepdf_helpers
from auto_a11y.pdf.models import AuditContext, CheckResult


# ---------------------------------------------------------------------------
# Module-private constants
# ---------------------------------------------------------------------------


#: BCP 47 language tag matcher (Matterhorn 11-003). Mirrors pdfMax's
#: ``_BCP47_RE`` verbatim — primary language, optional script/region,
#: variants, extensions, and private-use subtags.
_BCP47_RE: re.Pattern[str] = re.compile(
    r"^[a-zA-Z]{2,3}"
    + r"(?:-[a-zA-Z]{4})?"
    + r"(?:-(?:[a-zA-Z]{2}|[0-9]{3}))?"
    + r"(?:-(?:[a-zA-Z0-9]{5,8}|[0-9][a-zA-Z0-9]{3}))*"
    + r"(?:-[a-zA-Z](?:-[a-zA-Z0-9]{2,8})+)*"
    + r"(?:-x(?:-[a-zA-Z0-9]{1,8})+)?$",
    re.IGNORECASE,
)

#: Grandfathered BCP 47 tags that don't match the standard pattern but
#: are still considered valid (RFC 5646 §2.2.8). Mirrors pdfMax's
#: ``_BCP47_GRANDFATHERED`` set.
_BCP47_GRANDFATHERED: frozenset[str] = frozenset(
    {
        "i-ami", "i-bnn", "i-default", "i-enochian", "i-hak",
        "i-klingon", "i-lux", "i-mingo", "i-navajo", "i-pwn",
        "i-tao", "i-tay", "i-tsu",
        "sgn-be-fr", "sgn-be-nl", "sgn-ch-de",
        "art-lojban", "cel-gaulish", "no-bok", "no-nyn",
        "zh-guoyu", "zh-hakka", "zh-min", "zh-min-nan", "zh-xiang",
        "en-gb-oed",
    }
)

#: Maximum number of abbreviation characters for the heuristic in
#: :func:`check_abbreviation_expansion`. Mirrors pdfMax's 2-6 range.
_ABBREV_MIN_LEN: int = 2
_ABBREV_MAX_LEN: int = 6
#: Fraction of characters that must be uppercase for a span to be
#: considered abbreviation-like. Mirrors pdfMax's 0.6 threshold.
_ABBREV_UPPER_FRACTION: float = 0.6


def _validate_bcp47(lang_str: str) -> bool:
    """Return ``True`` if ``lang_str`` parses as a valid BCP 47 tag.

    Whitespace is stripped; empty strings are invalid. Lowercased
    grandfathered tags are accepted; otherwise the pattern is matched
    case-insensitively. Mirrors pdfMax's ``validate_bcp47``.
    """
    if not lang_str or not lang_str.strip():
        return False
    s = lang_str.strip()
    if s.lower() in _BCP47_GRANDFATHERED:
        return True
    return bool(_BCP47_RE.match(s))


def _doc_lang(pdf: pikepdf.Pdf) -> str | None:
    """Return the document catalog ``/Lang`` value, or ``None`` if absent.

    Returns ``None`` for both "no /Lang" and "/Lang present but empty"
    so callers can treat both as "language not declared".
    """
    try:
        lang_obj = pdf.Root["/Lang"]
    except KeyError:
        return None
    rendered = str(lang_obj)
    if not rendered.strip():
        return None
    return rendered


# ---------------------------------------------------------------------------
# check_document_language
# ---------------------------------------------------------------------------


def check_document_language(ctx: AuditContext) -> list[CheckResult]:
    """Document catalog ``/Lang`` attribute is set (WCAG 3.1.1, PDF/UA).

    Reads :attr:`AuditContext.pdf`. Returns PASS when ``/Lang`` is
    present and non-blank, FAIL otherwise. Mirrors pdfMax verbatim.
    """
    lang = _doc_lang(ctx.pdf)
    if lang is not None:
        return [
            CheckResult(
                name="Document language set",
                standard="PDF/UA, WCAG 3.1.1",
                result="PASS",
                details=f'Language: "{lang}"',
            )
        ]
    return [
        CheckResult(
            name="Document language set",
            standard="PDF/UA, WCAG 3.1.1",
            result="FAIL",
            details=(
                "No /Lang attribute in document catalog. Screen readers"
                " cannot determine text language."
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_lang_values_valid_bcp47
# ---------------------------------------------------------------------------


def check_lang_values_valid_bcp47(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 11-003: every ``/Lang`` value parses as valid BCP 47.

    Collects the catalog-level ``/Lang`` (from :attr:`AuditContext.pdf`)
    plus every structure-element ``/Lang`` (from
    :attr:`AuditContext.elements`), validates each, and reports any
    invalid entries with their source location.
    """
    all_lang_values: list[tuple[str, str]] = []

    doc_lang = _doc_lang(ctx.pdf)
    if doc_lang is not None:
        all_lang_values.append(("Document catalog", doc_lang))

    for elem in ctx.elements:
        if elem.lang:
            all_lang_values.append(
                (f"[{elem.index}] {elem.resolved_tag}", elem.lang)
            )

    if not all_lang_values:
        return [
            CheckResult(
                name="Lang values are valid BCP 47",
                standard="Matterhorn 11-003",
                result="NA",
                details="No /Lang values found in document",
            )
        ]

    invalid_langs = [
        (source, val) for source, val in all_lang_values
        if not _validate_bcp47(val)
    ]
    if not invalid_langs:
        return [
            CheckResult(
                name="Lang values are valid BCP 47",
                standard="Matterhorn 11-003",
                result="PASS",
                details=(
                    f"All {len(all_lang_values)} /Lang value(s) are"
                    + " valid BCP 47 tags"
                ),
            )
        ]

    examples = "; ".join(f'{src}: "{val}"' for src, val in invalid_langs[:5])
    return [
        CheckResult(
            name="Lang values are valid BCP 47",
            standard="Matterhorn 11-003",
            result="FAIL",
            details=f"{len(invalid_langs)} invalid BCP 47 tag(s): {examples}",
        )
    ]


# ---------------------------------------------------------------------------
# check_annotation_language_determinable
# ---------------------------------------------------------------------------


def _iter_page_annots(
    page_obj: pikepdf.Dictionary,
) -> list[pikepdf.Dictionary]:
    """Yield every dictionary-shaped annotation on a page.

    The annotation array may legitimately contain non-dictionary
    entries (e.g. dangling indirect references) — those are skipped
    rather than raising.
    """
    annots = pikepdf_helpers.get_array(page_obj, "/Annots")
    if annots is None:
        return []
    out: list[pikepdf.Dictionary] = []
    for ai in range(len(annots)):
        item = annots[ai]
        if isinstance(item, pikepdf.Dictionary):
            out.append(item)
    return out


def check_annotation_language_determinable(
    ctx: AuditContext,
) -> list[CheckResult]:
    """Matterhorn 11-004: annotation contents language is determinable.

    For every annotation with a ``/Contents`` text payload, the language
    must be readable from the annotation's own ``/Lang``, the parent
    page's ``/Lang``, or the document catalog's ``/Lang``. Annotations
    without ``/Contents`` are out of scope.
    """
    doc_has_lang = _doc_lang(ctx.pdf) is not None
    annots_missing_lang: list[str] = []
    annot_contents_total = 0

    for page_idx, page in enumerate(ctx.pdf.pages):
        page_num = page_idx + 1
        page_lang = pikepdf_helpers.get_string(page.obj, "/Lang")
        for annot in _iter_page_annots(page.obj):
            contents = pikepdf_helpers.get_string(annot, "/Contents")
            if contents is None:
                continue
            annot_contents_total += 1
            annot_lang = pikepdf_helpers.get_string(annot, "/Lang")
            if not annot_lang and not page_lang and not doc_has_lang:
                subtype_name = pikepdf_helpers.get_name(annot, "/Subtype")
                subtype = (
                    str(subtype_name).lstrip("/")
                    if subtype_name is not None
                    else ""
                )
                annots_missing_lang.append(f"p.{page_num} {subtype}")

    if annot_contents_total == 0:
        return [
            CheckResult(
                name="Annotation contents language determinable",
                standard="Matterhorn 11-004",
                result="NA",
                details="No annotations with /Contents text found",
            )
        ]
    if not annots_missing_lang:
        return [
            CheckResult(
                name="Annotation contents language determinable",
                standard="Matterhorn 11-004",
                result="PASS",
                details=(
                    f"All {annot_contents_total} annotation(s) with"
                    + " /Contents have determinable language"
                ),
            )
        ]
    return [
        CheckResult(
            name="Annotation contents language determinable",
            standard="Matterhorn 11-004",
            result="FAIL",
            details=(
                f"{len(annots_missing_lang)} annotation(s) with /Contents"
                + " have no determinable language: "
                + ", ".join(annots_missing_lang[:5])
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_form_field_tooltip_language_determinable
# ---------------------------------------------------------------------------


def _flatten_form_fields(
    raw_fields: pikepdf.Array,
) -> list[pikepdf.Dictionary]:
    """Flatten the AcroForm /Fields tree into terminal field dictionaries.

    Mirrors the helper in :mod:`auto_a11y.pdf.audit.colors`: a node with
    ``/Kids`` and no ``/FT`` is an intermediate (logical group); a node
    with ``/FT`` is terminal. Defensive against entries that are not
    Dictionary-shaped.
    """
    flat: list[pikepdf.Dictionary] = []
    stack: list[pikepdf.Object] = []
    for i in range(len(raw_fields)):
        stack.append(raw_fields[i])
    while stack:
        node = stack.pop(0)
        if not isinstance(node, pikepdf.Dictionary):
            continue
        kids = pikepdf_helpers.get_array(node, "/Kids")
        ft = pikepdf_helpers.get_name(node, "/FT")
        if kids is not None and ft is None:
            for ki in range(len(kids)):
                stack.append(kids[ki])
        else:
            flat.append(node)
    return flat


def check_form_field_tooltip_language_determinable(
    ctx: AuditContext,
) -> list[CheckResult]:
    """Matterhorn 11-005: form-field tooltip language is determinable.

    Walks the AcroForm /Fields tree (flattening intermediate group
    nodes), and for every terminal field carrying a ``/TU`` tooltip
    requires a language source — the field's own ``/Lang`` or the
    document catalog's ``/Lang``. Fields without ``/TU`` are out of
    scope.
    """
    doc_has_lang = _doc_lang(ctx.pdf) is not None

    acroform = pikepdf_helpers.get_dict(ctx.pdf.Root, "/AcroForm")
    if acroform is None:
        return [
            CheckResult(
                name="Form field tooltip language determinable",
                standard="Matterhorn 11-005",
                result="NA",
                details="No form fields with /TU tooltip found",
            )
        ]
    raw_fields = pikepdf_helpers.get_array(acroform, "/Fields")
    if raw_fields is None or len(raw_fields) == 0:
        return [
            CheckResult(
                name="Form field tooltip language determinable",
                standard="Matterhorn 11-005",
                result="NA",
                details="No form fields with /TU tooltip found",
            )
        ]

    fields_missing_lang: list[str] = []
    fields_with_tu_total = 0
    for field_dict in _flatten_form_fields(raw_fields):
        tu = pikepdf_helpers.get_string(field_dict, "/TU")
        if tu is None:
            continue
        fields_with_tu_total += 1
        field_lang = pikepdf_helpers.get_string(field_dict, "/Lang")
        if not field_lang and not doc_has_lang:
            t_name = (
                pikepdf_helpers.get_string(field_dict, "/T") or "(unnamed)"
            )
            fields_missing_lang.append(t_name)

    if fields_with_tu_total == 0:
        return [
            CheckResult(
                name="Form field tooltip language determinable",
                standard="Matterhorn 11-005",
                result="NA",
                details="No form fields with /TU tooltip found",
            )
        ]
    if not fields_missing_lang:
        return [
            CheckResult(
                name="Form field tooltip language determinable",
                standard="Matterhorn 11-005",
                result="PASS",
                details=(
                    f"All {fields_with_tu_total} field(s) with /TU"
                    + " have determinable language"
                ),
            )
        ]
    return [
        CheckResult(
            name="Form field tooltip language determinable",
            standard="Matterhorn 11-005",
            result="FAIL",
            details=(
                f"{len(fields_missing_lang)} field(s) with /TU have no"
                + " determinable language: "
                + ", ".join(fields_missing_lang[:5])
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_metadata_language_determinable
# ---------------------------------------------------------------------------


def _xmp_has_xml_lang(pdf: pikepdf.Pdf) -> bool:
    """Return ``True`` if the XMP metadata stream contains ``xml:lang``.

    Mirrors pdfMax's substring search — no XML parse, just the
    ``xml:lang`` token anywhere in the decoded UTF-8 payload. Returns
    ``False`` when there is no metadata stream or it cannot be decoded.
    The /Metadata entry on the catalog is a Stream (not a Dictionary),
    so this routine narrows via :class:`pikepdf.Stream` directly rather
    than going through :func:`pikepdf_helpers.get_dict`.
    """
    try:
        metadata = pdf.Root["/Metadata"]
    except KeyError:
        return False
    if not isinstance(metadata, pikepdf.Stream):
        return False
    try:
        xmp_bytes = bytes(metadata.read_bytes())
    except (pikepdf.PdfError, AttributeError):
        return False
    try:
        xmp_text = xmp_bytes.decode("utf-8", errors="replace")
    except UnicodeDecodeError:
        return False
    return "xml:lang" in xmp_text


def check_metadata_language_determinable(
    ctx: AuditContext,
) -> list[CheckResult]:
    """Matterhorn 11-006: metadata language is determinable.

    Either the document catalog ``/Lang`` is set, or the XMP metadata
    stream has an ``xml:lang`` attribute. PASSes if either source is
    present, FAILs only when neither is.
    """
    doc_has_lang = _doc_lang(ctx.pdf) is not None
    if doc_has_lang:
        return [
            CheckResult(
                name="Document metadata language determinable",
                standard="Matterhorn 11-006",
                result="PASS",
                details="Metadata language determinable via document /Lang",
            )
        ]
    if _xmp_has_xml_lang(ctx.pdf):
        return [
            CheckResult(
                name="Document metadata language determinable",
                standard="Matterhorn 11-006",
                result="PASS",
                details="Metadata language determinable via XMP xml:lang",
            )
        ]
    return [
        CheckResult(
            name="Document metadata language determinable",
            standard="Matterhorn 11-006",
            result="FAIL",
            details=(
                "Neither document /Lang nor XMP xml:lang is set —"
                " metadata language cannot be determined"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_abbreviation_expansion
# ---------------------------------------------------------------------------


def _span_has_e_attr(node: pikepdf.Dictionary) -> bool:
    """Return ``True`` if a Span structure element carries ``/E`` expansion.

    pdfMax accepts the expansion either as a direct ``/E`` on the
    structure element, or as ``/E`` inside an entry of the ``/A``
    attribute dictionary (which may itself be a single Dictionary or an
    Array of Dictionaries).
    """
    if pikepdf_helpers.get_string(node, "/E") is not None:
        return True
    try:
        a_val = node["/A"]
    except KeyError:
        return False

    a_items: list[pikepdf.Object]
    if isinstance(a_val, pikepdf.Array):
        a_items = [a_val[i] for i in range(len(a_val))]
    else:
        a_items = [a_val]
    for attr in a_items:
        if isinstance(attr, pikepdf.Dictionary):
            if pikepdf_helpers.get_string(attr, "/E") is not None:
                return True
    return False


def check_abbreviation_expansion(ctx: AuditContext) -> list[CheckResult]:
    """WCAG 3.1.4 (PDF8): abbreviation-like spans carry an ``/E`` expansion.

    pdfMax's heuristic for "abbreviation-like": Span structure element
    whose text content is 2-6 characters, of which at least 60% are
    uppercase. WARNs (rather than FAILs) when any such span lacks an
    expansion via ``/E`` or ``/A`` /E.
    """
    abbrev_without_e: list[tuple[int, str]] = []
    for elem in ctx.elements:
        if elem.resolved_tag != "Span" or not elem.text_content:
            continue
        text = elem.text_content.strip()
        if len(text) < _ABBREV_MIN_LEN or len(text) > _ABBREV_MAX_LEN:
            continue
        upper_count = sum(1 for c in text if c.isupper())
        if upper_count < len(text) * _ABBREV_UPPER_FRACTION:
            continue
        if not _span_has_e_attr(elem.obj):
            abbrev_without_e.append((elem.index, text))

    if not abbrev_without_e:
        return [
            CheckResult(
                name="Abbreviations have /E expansion",
                standard="WCAG 3.1.4 (PDF8)",
                result="PASS",
                details=(
                    "No abbreviation-like spans found without /E expansion"
                ),
            )
        ]
    examples = ", ".join(f'[{idx}] "{t}"' for idx, t in abbrev_without_e[:5])
    return [
        CheckResult(
            name="Abbreviations have /E expansion",
            standard="WCAG 3.1.4 (PDF8)",
            result="WARN",
            details=(
                f"{len(abbrev_without_e)} abbreviation-like span(s)"
                + f" without /E expansion: {examples}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------


#: Phase 5.3's pipeline iterates this list in order. Phase 6's check
#: catalogue iterates the same list to enumerate every check name.
LANGUAGE_CHECKS: list[Callable[[AuditContext], list[CheckResult]]] = [
    check_document_language,
    check_lang_values_valid_bcp47,
    check_annotation_language_determinable,
    check_form_field_tooltip_language_determinable,
    check_metadata_language_determinable,
    check_abbreviation_expansion,
]


__all__ = [
    "LANGUAGE_CHECKS",
    "check_abbreviation_expansion",
    "check_annotation_language_determinable",
    "check_document_language",
    "check_form_field_tooltip_language_determinable",
    "check_lang_values_valid_bcp47",
    "check_metadata_language_determinable",
]
