"""Mapping table from audit-engine ``CheckResult`` to auto_a11y ``Violation``.

The audit engine emits :class:`auto_a11y.pdf.models.CheckResult` objects
keyed by human-readable names like ``"Document title set"`` together with
a verdict of ``PASS``, ``FAIL``, ``WARN``, or ``INFO``. The rest of
auto_a11y consumes :class:`auto_a11y.models.test_result.Violation`
objects keyed by stable error-code IDs such as
``"PdfErrDocumentTitleNotSet"``.

This module owns that translation:

* :data:`CHECK_CATALOGUE` is the authoritative list of every
  ``(name, result)`` pair the audit engine emits — except ``PASS``,
  which never becomes a Violation. Each row carries a stable ID, the
  PDF touchpoint it belongs under, the WCAG criteria parsed from the
  ``standard`` string, and an optional impact override.
* :func:`to_violation` looks up a row by ``(name, result)`` and returns
  the corresponding :class:`Violation`, or ``None`` for ``PASS``.

Stable-ID conventions
---------------------

Every row carries a stable ID built from a fixed prefix + a short
PascalCase suffix that captures the failure mode. The prefix tracks the
verdict:

* ``PdfErr<...>`` for ``FAIL`` rows;
* ``PdfWarn<...>`` for ``WARN`` rows;
* ``PdfInfo<...>`` for ``INFO`` rows.

The PascalCase suffix is hand-chosen — it doesn't have to be a
mechanical transliteration of the check name, but it must be:

* unique across the catalogue (the
  :func:`test_catalogue_ids_are_unique` test enforces this);
* predictable enough that a developer reading the suffix can guess the
  Fluent message ID in :file:`auto_a11y/web/translations/{en,fr}/pdf.ftl`.

Touchpoint assignment
---------------------

Every row pins the violation to one of three PDF touchpoints, picked by
inspecting the source-module of the check:

* :data:`TouchpointID.PDF_DOCUMENT_PROPERTIES` — checks against the
  document catalog, info dict, XMP metadata, or document-wide language.
  Source modules:
  :mod:`auto_a11y.pdf.audit.checks.document_properties`,
  document-wide entries from
  :mod:`auto_a11y.pdf.audit.checks.language`, and the
  ``Bookmarks present`` check from
  :mod:`auto_a11y.pdf.audit.checks.links_navigation` (which targets
  the catalog ``/Outlines`` entry).
* :data:`TouchpointID.PDF_TAGGING` — structure-tree, role-map,
  reading-order, content-tag, image-alt, font/contrast, and table/list
  semantics. Source modules:
  :mod:`auto_a11y.pdf.audit.checks.headings`,
  :mod:`auto_a11y.pdf.audit.checks.tagging_structure`,
  :mod:`auto_a11y.pdf.audit.checks.lists`,
  :mod:`auto_a11y.pdf.audit.checks.tables`,
  :mod:`auto_a11y.pdf.audit.checks.images_alt_text`,
  :mod:`auto_a11y.pdf.audit.checks.fonts`,
  :mod:`auto_a11y.pdf.audit.checks.color_contrast`, and the
  ``Abbreviations have /E expansion`` check from
  :mod:`auto_a11y.pdf.audit.checks.language` (which targets structure-
  element tag attributes).
* :data:`TouchpointID.PDF_ANNOTATIONS` — annotations, link/widget tab
  order, and form-field semantics. Source modules:
  :mod:`auto_a11y.pdf.audit.checks.annotations`,
  :mod:`auto_a11y.pdf.audit.checks.forms`,
  :mod:`auto_a11y.pdf.audit.checks.interactive`,
  :mod:`auto_a11y.pdf.audit.checks.links_navigation` (except
  ``Bookmarks present``), and per-annotation entries from
  :mod:`auto_a11y.pdf.audit.checks.language`.

WCAG criteria extraction
------------------------

The ``standard`` string passed to each :class:`CheckResult` looks like
``"WCAG 2.4.2"``, ``"PDF/UA, WCAG 1.3.1"``, ``"WCAG 1.4 (best practice)"``,
or ``"Matterhorn 06-002"``. :func:`extract_wcag_criteria` runs a regex
over that string and returns the bare numeric criteria
(e.g. ``["2.4.2"]`` or ``["1.3.1", "4.1.2"]``); Matterhorn-only
references map to the empty list. Best-practice phrasing like
``"WCAG 1.4 (best practice)"`` is preserved as ``["1.4"]``.

Impact override
---------------

By default, :func:`default_impact` maps:

* ``FAIL`` → :data:`ImpactLevel.HIGH`;
* ``WARN`` → :data:`ImpactLevel.MEDIUM`;
* ``INFO`` → :data:`ImpactLevel.LOW`.

A row's ``impact_override`` field overrides that default. Override
sparingly — only when the verdict-to-impact mapping doesn't carry the
right severity for a particular row. Currently no rows override.
"""
from __future__ import annotations

import logging
import re
from typing import TypedDict

logger = logging.getLogger(__name__)

# Import a check-module submodule first so the
# :mod:`auto_a11y.pdf.audit` package's ``__init__`` resolves before
# :mod:`auto_a11y.pdf.models` is pulled in below. Without this shim
# import, evaluating ``models`` triggers ``audit/__init__`` which
# triggers ``pipeline`` which re-imports ``models`` mid-evaluation —
# a circular import. Importing a leaf check module first lets the
# package init sequence run to completion. We reference the imported
# symbol once below so static checkers see it as used.
from auto_a11y.pdf.audit.checks.annotations import ANNOTATIONS_CHECKS
from auto_a11y.core.touchpoints import TouchpointID
from auto_a11y.models.test_result import ImpactLevel, Violation
from auto_a11y.pdf.models import CheckOutcome, CheckResult


# The ANNOTATIONS_CHECKS import above is load-bearing for import order.
# Bind it to a private name so the import doesn't get rewritten out by
# tools that aggressively prune unused imports.
_IMPORT_ORDER_SHIM = ANNOTATIONS_CHECKS


# ---------------------------------------------------------------------------
# Row schema
# ---------------------------------------------------------------------------


class CatalogueRow(TypedDict):
    """One catalogue row — see module docstring for field semantics."""

    pdfmax_check_name: str
    """The :attr:`CheckResult.name` literal emitted by the audit engine."""

    pdfmax_result: CheckOutcome
    """The :attr:`CheckResult.result` literal — ``FAIL``, ``WARN``, or ``INFO``."""

    stable_id: str
    """Stable ID consumers use as the violation's ``id``. Conventions:
    ``PdfErr<...>`` for FAIL, ``PdfWarn<...>`` for WARN,
    ``PdfInfo<...>`` for INFO."""

    touchpoint: TouchpointID
    """The PDF touchpoint this violation belongs to."""

    wcag_criteria: list[str]
    """WCAG criteria extracted from the audit engine's ``standard`` string."""

    impact_override: ImpactLevel | None
    """Optional override for the default impact mapping. ``None`` means
    use :func:`default_impact`."""


# ---------------------------------------------------------------------------
# WCAG-criteria parsing
# ---------------------------------------------------------------------------


#: Match a WCAG criterion number (X.Y or X.Y.Z) immediately after
#: ``WCAG`` (case-sensitive, since the audit engine writes it that way).
#: Captures bare numbers like ``2.4.2`` or ``1.4``; the trailing
#: parenthetical (``(best practice)``, ``(PDF8)``, ``(PDF17)``) is left
#: out of the captured group.
_WCAG_RE: re.Pattern[str] = re.compile(
    r"WCAG\s+((?:\d+\.)+\d+|\d+\.\d+|\d+)"
)


def extract_wcag_criteria(standard: str) -> list[str]:
    """Return every WCAG criterion number named in *standard*.

    The audit engine writes standards like ``"WCAG 2.4.2"``,
    ``"PDF/UA, WCAG 1.3.1"``, ``"WCAG 1.3.1, 2.4.6"`` (a comma-separated
    list of additional criteria after the first ``WCAG`` keyword), or
    ``"Matterhorn 06-002"`` (no WCAG reference at all).

    The parser:

    1. Finds the first ``WCAG <crit>`` occurrence and captures its
       criterion;
    2. Treats subsequent comma-separated tokens that look like
       criteria (``\\d+(?:\\.\\d+)+``) as additional criteria too — this
       handles the ``"WCAG 1.3.1, 4.1.2"`` shape pdfMax uses for forms
       and the ``"WCAG 2.5.5, 2.5.8"`` shape for interactive checks.
    3. Returns ``[]`` for Matterhorn-only references.
    """
    out: list[str] = []
    primary = _WCAG_RE.search(standard)
    if primary is None:
        return out
    out.append(primary.group(1))

    # After the first WCAG match, scan the rest of the string for
    # additional criterion numbers separated by commas. We deliberately
    # stop at "(" (parenthetical qualifier) to avoid pulling in things
    # like "(PDF17)".
    tail = standard[primary.end():]
    # Cut off at first parenthesis to avoid swallowing qualifiers.
    paren_idx = tail.find("(")
    if paren_idx != -1:
        tail = tail[:paren_idx]
    for extra in re.findall(r"(?:\d+\.)+\d+", tail):
        if extra not in out:
            out.append(extra)
    return out


# ---------------------------------------------------------------------------
# Default impact
# ---------------------------------------------------------------------------


def default_impact(result: CheckOutcome) -> ImpactLevel:
    """Return the default :class:`ImpactLevel` for a given outcome.

    * ``FAIL`` → :data:`ImpactLevel.HIGH` — a hard accessibility failure.
    * ``WARN`` → :data:`ImpactLevel.MEDIUM` — a likely problem worth
      manual review.
    * ``INFO`` → :data:`ImpactLevel.LOW` — informational, not actionable
      on its own.
    * ``PASS`` → never reaches this function; :func:`to_violation`
      short-circuits first.

    Catalogue rows can override this by setting ``impact_override``.
    """
    if result == "FAIL":
        return ImpactLevel.HIGH
    if result == "WARN":
        return ImpactLevel.MEDIUM
    if result == "INFO":
        return ImpactLevel.LOW
    msg = (
        "default_impact() called with PASS — to_violation should have"
        + f" short-circuited. Got result={result!r}."
    )
    raise ValueError(msg)


# ---------------------------------------------------------------------------
# CHECK_CATALOGUE
# ---------------------------------------------------------------------------


def _row(
    *,
    name: str,
    result: CheckOutcome,
    stable_id: str,
    touchpoint: TouchpointID,
    standard: str,
    impact_override: ImpactLevel | None = None,
) -> CatalogueRow:
    """Construct a :class:`CatalogueRow` with WCAG criteria extracted.

    Helper to keep :data:`CHECK_CATALOGUE` readable — passing the
    ``standard`` string through :func:`extract_wcag_criteria` once at
    module load.
    """
    return {
        "pdfmax_check_name": name,
        "pdfmax_result": result,
        "stable_id": stable_id,
        "touchpoint": touchpoint,
        "wcag_criteria": extract_wcag_criteria(standard),
        "impact_override": impact_override,
    }


# Touchpoint shortcuts used in the catalogue declaration.
_DOC = TouchpointID.PDF_DOCUMENT_PROPERTIES
_TAG = TouchpointID.PDF_TAGGING
_ANN = TouchpointID.PDF_ANNOTATIONS


CHECK_CATALOGUE: list[CatalogueRow] = [
    # ---- document_properties → PDF_DOCUMENT_PROPERTIES ----
    _row(
        name="Document title set", result="FAIL",
        stable_id="PdfErrDocumentTitleNotSet",
        touchpoint=_DOC, standard="PDF/UA, WCAG 2.4.2",
    ),
    _row(
        name="Document title set", result="WARN",
        stable_id="PdfWarnDocumentTitleNotDisplayed",
        touchpoint=_DOC, standard="PDF/UA, WCAG 2.4.2",
    ),
    _row(
        name="PDF is tagged", result="FAIL",
        stable_id="PdfErrPdfNotTagged",
        touchpoint=_DOC, standard="PDF/UA, WCAG 1.3.1",
    ),
    _row(
        name="No suspect tags", result="WARN",
        stable_id="PdfWarnSuspectTags",
        touchpoint=_DOC, standard="Matterhorn 09-004",
    ),
    _row(
        name="PDF/UA identifier", result="WARN",
        stable_id="PdfWarnNoPdfUaIdentifier",
        touchpoint=_DOC, standard="Matterhorn 06-002",
    ),
    _row(
        name="XMP metadata stream present", result="FAIL",
        stable_id="PdfErrNoXmpMetadataStream",
        touchpoint=_DOC, standard="Matterhorn 06-001",
    ),
    _row(
        name="XMP dc:title present", result="FAIL",
        stable_id="PdfErrXmpDcTitleMissing",
        touchpoint=_DOC, standard="Matterhorn 06-003",
    ),
    _row(
        name="Metadata completeness", result="WARN",
        stable_id="PdfWarnMetadataIncomplete",
        touchpoint=_DOC, standard="PDF/UA-1 7.20",
    ),
    _row(
        name="Page labels consistent", result="FAIL",
        stable_id="PdfErrPageLabelsInconsistent",
        touchpoint=_DOC, standard="WCAG (PDF17)",
    ),
    _row(
        name="PDF header and catalog version consistent", result="FAIL",
        stable_id="PdfErrPdfVersionMismatch",
        touchpoint=_DOC, standard="WCAG 2.2",
    ),
    _row(
        name="New PDF 2.0 structure elements used correctly", result="WARN",
        stable_id="PdfWarnPdf20TagsRemapped",
        touchpoint=_DOC, standard="PDF/UA-2",
    ),
    _row(
        name="PDF/UA-2 accessibility declarations in XMP", result="WARN",
        stable_id="PdfWarnPdfUa2DeclarationsIncomplete",
        touchpoint=_DOC, standard="PDF/UA-2",
    ),
    _row(
        name="PDF 2.0 namespace in structure tree", result="FAIL",
        stable_id="PdfErrPdf20NamespaceMissing",
        touchpoint=_DOC, standard="PDF/UA-2",
    ),
    _row(
        name="PDF/UA-2 requires PDF 2.0", result="FAIL",
        stable_id="PdfErrPdfUa2RequiresPdf20",
        touchpoint=_DOC, standard="PDF/UA-2",
    ),

    # ---- tagging_structure → PDF_TAGGING ----
    _row(
        name="Structure tree exists", result="FAIL",
        stable_id="PdfErrStructureTreeMissing",
        touchpoint=_TAG, standard="Matterhorn 01-006",
    ),
    _row(
        name="Role mapping valid", result="FAIL",
        stable_id="PdfErrRoleMapInvalid",
        touchpoint=_TAG, standard="Matterhorn 02-001",
    ),
    _row(
        name="No circular role mappings", result="FAIL",
        stable_id="PdfErrRoleMapCircular",
        touchpoint=_TAG, standard="Matterhorn 02-003",
    ),
    _row(
        name="Standard tags not remapped", result="FAIL",
        stable_id="PdfErrStandardTagsRemapped",
        touchpoint=_TAG, standard="Matterhorn 02-004",
    ),
    _row(
        name="Tab order follows structure", result="FAIL",
        stable_id="PdfErrTabOrderNotStructure",
        touchpoint=_TAG, standard="PDF/UA, WCAG 2.1.1",
    ),
    _row(
        name="TOC structure valid", result="FAIL",
        stable_id="PdfErrTocStructureInvalid",
        touchpoint=_TAG, standard="Matterhorn 09-006",
    ),
    _row(
        name="Ruby structure valid", result="FAIL",
        stable_id="PdfErrRubyStructureInvalid",
        touchpoint=_TAG, standard="Matterhorn 09-007",
    ),
    _row(
        name="Warichu structure valid", result="FAIL",
        stable_id="PdfErrWarichuStructureInvalid",
        touchpoint=_TAG, standard="Matterhorn 09-008",
    ),
    _row(
        name="Note tags have unique IDs", result="FAIL",
        stable_id="PdfErrNoteIdsNotUnique",
        touchpoint=_TAG, standard="Matterhorn 19-003",
    ),
    _row(
        name="Formula elements have alt text or ActualText", result="FAIL",
        stable_id="PdfErrFormulaMissingAlt",
        touchpoint=_TAG, standard="Matterhorn 17-002",
    ),
    _row(
        name="MathML associated with Formula elements", result="WARN",
        stable_id="PdfWarnFormulaWithoutMathMl",
        touchpoint=_TAG, standard="PDF/UA-2",
    ),
    _row(
        name="Associated Files property on embedded content", result="WARN",
        stable_id="PdfWarnAssociatedFilesMissing",
        touchpoint=_TAG, standard="PDF/UA-2",
    ),
    _row(
        name="Optional content groups have Name", result="FAIL",
        stable_id="PdfErrOptionalContentGroupMissingName",
        touchpoint=_TAG, standard="Matterhorn 20-001",
    ),
    _row(
        name="Optional content has no AS entry", result="FAIL",
        stable_id="PdfErrOptionalContentHasAsEntry",
        touchpoint=_TAG, standard="Matterhorn 20-002",
    ),
    _row(
        name="Embedded files have F and UF keys", result="FAIL",
        stable_id="PdfErrEmbeddedFileMissingFKeys",
        touchpoint=_TAG, standard="Matterhorn 21-001",
    ),
    _row(
        name="No Reference XObjects", result="FAIL",
        stable_id="PdfErrReferenceXObjectsPresent",
        touchpoint=_TAG, standard="Matterhorn 30-001",
    ),
    _row(
        name="Form XObjects with MCIDs not reused", result="FAIL",
        stable_id="PdfErrFormXObjectMcidReused",
        touchpoint=_TAG, standard="Matterhorn 30-002",
    ),
    _row(
        name="Structure destinations for intra-document links", result="WARN",
        stable_id="PdfWarnNonStructureDestinations",
        touchpoint=_TAG, standard="PDF/UA-2",
    ),
    _row(
        name="PDF/UA-2 heading hierarchy", result="WARN",
        stable_id="PdfWarnPdfUa2HeadingHierarchy",
        touchpoint=_TAG, standard="PDF/UA-2",
    ),
    _row(
        name="Reading order matches visual layout", result="FAIL",
        stable_id="PdfErrReadingOrderMismatch",
        touchpoint=_TAG, standard="WCAG 1.3.2",
    ),
    _row(
        name="Reading order matches visual layout", result="WARN",
        stable_id="PdfWarnReadingOrderUnverified",
        touchpoint=_TAG, standard="WCAG 1.3.2",
    ),

    # ---- headings → PDF_TAGGING ----
    _row(
        name="Heading hierarchy valid", result="FAIL",
        stable_id="PdfErrHeadingHierarchyInvalid",
        touchpoint=_TAG, standard="WCAG 1.3.1, 2.4.6",
    ),
    _row(
        name="No multiple headings per node", result="FAIL",
        stable_id="PdfErrMultipleHeadingsPerNode",
        touchpoint=_TAG, standard="Matterhorn 14-006",
    ),
    _row(
        name="No mixed heading tag types", result="FAIL",
        stable_id="PdfErrMixedHeadingTagTypes",
        touchpoint=_TAG, standard="Matterhorn 14-007",
    ),

    # ---- language → mostly PDF_DOCUMENT_PROPERTIES; PDF_ANNOTATIONS for
    # ---- per-annotation entries; PDF_TAGGING for tag-attribute checks.
    _row(
        name="Document language set", result="FAIL",
        stable_id="PdfErrDocumentLanguageNotSet",
        touchpoint=_DOC, standard="PDF/UA, WCAG 3.1.1",
    ),
    _row(
        name="Lang values are valid BCP 47", result="FAIL",
        stable_id="PdfErrLangValuesInvalidBcp47",
        touchpoint=_DOC, standard="Matterhorn 11-003",
    ),
    _row(
        name="Annotation contents language determinable", result="FAIL",
        stable_id="PdfErrAnnotationLanguageIndeterminable",
        touchpoint=_ANN, standard="Matterhorn 11-004",
    ),
    _row(
        name="Form field tooltip language determinable", result="FAIL",
        stable_id="PdfErrFormFieldTooltipLanguageIndeterminable",
        touchpoint=_ANN, standard="Matterhorn 11-005",
    ),
    _row(
        name="Document metadata language determinable", result="FAIL",
        stable_id="PdfErrDocumentMetadataLanguageIndeterminable",
        touchpoint=_DOC, standard="Matterhorn 11-006",
    ),
    _row(
        name="Abbreviations have /E expansion", result="WARN",
        stable_id="PdfWarnAbbreviationsMissingExpansion",
        touchpoint=_TAG, standard="WCAG 3.1.4 (PDF8)",
    ),

    # ---- images_alt_text → PDF_TAGGING ----
    _row(
        name="Alt text on all Figure/Art tags", result="FAIL",
        stable_id="PdfErrFigureMissingAltText",
        touchpoint=_TAG, standard="PDF/UA, WCAG 1.1.1",
    ),
    _row(
        name="Alt text free of redundant role text", result="WARN",
        stable_id="PdfWarnAltTextRedundantRoleText",
        touchpoint=_TAG, standard="WCAG 1.1.1 (best practice)",
    ),
    _row(
        name="Alt text does not hide interactive elements", result="FAIL",
        stable_id="PdfErrAltTextHidesInteractive",
        touchpoint=_TAG, standard="PDF/UA, WCAG 4.1.2",
    ),
    _row(
        name="Figure elements have BBox attribute", result="FAIL",
        stable_id="PdfErrFigureMissingBBox",
        touchpoint=_TAG, standard="PDF/UA (PAC 2024)",
    ),

    # ---- annotations → PDF_ANNOTATIONS ----
    _row(
        name="Link annotations have content", result="FAIL",
        stable_id="PdfErrLinkAnnotationEmpty",
        touchpoint=_ANN, standard="PDF/UA, WCAG 2.4.4",
    ),
    _row(
        name="Link annotations have Contents key", result="FAIL",
        stable_id="PdfErrLinkAnnotationMissingContents",
        touchpoint=_ANN, standard="Matterhorn 28-012",
    ),
    _row(
        name="Link annotations inside Link tags", result="FAIL",
        stable_id="PdfErrLinkAnnotationNotInLinkTag",
        touchpoint=_ANN, standard="Matterhorn 28-014",
    ),
    _row(
        name="Visible annotations have alt descriptions", result="FAIL",
        stable_id="PdfErrAnnotationMissingAltDescription",
        touchpoint=_ANN, standard="Matterhorn 28-005",
    ),
    _row(
        name="Non-link/widget annotations tagged", result="FAIL",
        stable_id="PdfErrNonLinkWidgetAnnotationUntagged",
        touchpoint=_ANN, standard="Matterhorn 28-004",
    ),
    _row(
        name="Multimedia annotations tagged", result="FAIL",
        stable_id="PdfErrMultimediaAnnotationUntagged",
        touchpoint=_ANN, standard="WCAG 1.2",
    ),
    _row(
        name="Media clip annotations have alt text", result="FAIL",
        stable_id="PdfErrMediaClipAnnotationMissingAlt",
        touchpoint=_ANN, standard="Matterhorn 28-016",
    ),
    _row(
        name="Media clip alt text present", result="FAIL",
        stable_id="PdfErrMediaClipAltMissing",
        touchpoint=_ANN, standard="Matterhorn 28-015",
    ),
    _row(
        name="Media clip content type present", result="FAIL",
        stable_id="PdfErrMediaClipContentTypeMissing",
        touchpoint=_ANN, standard="Matterhorn 28-014",
    ),
    _row(
        name="Annotation tab order on all annotated pages", result="FAIL",
        stable_id="PdfErrAnnotationTabOrderInvalid",
        touchpoint=_ANN, standard="Matterhorn 28-002",
    ),
    _row(
        name="No non-standard annotation subtypes", result="FAIL",
        stable_id="PdfErrNonStandardAnnotationSubtype",
        touchpoint=_ANN, standard="Matterhorn 28-006",
    ),
    _row(
        name="No TrapNet annotations", result="FAIL",
        stable_id="PdfErrTrapNetAnnotationPresent",
        touchpoint=_ANN, standard="Matterhorn 28-006",
    ),
    _row(
        name="PrinterMark annotations not in structure", result="FAIL",
        stable_id="PdfErrPrinterMarkAnnotationInStructure",
        touchpoint=_ANN, standard="Matterhorn 28-018",
    ),
    _row(
        name="File attachment annotations valid", result="FAIL",
        stable_id="PdfErrFileAttachmentAnnotationInvalid",
        touchpoint=_ANN, standard="Matterhorn 28-016",
    ),

    # ---- links_navigation → PDF_ANNOTATIONS, except 'Bookmarks present'
    _row(
        name="Link alt text is descriptive", result="WARN",
        stable_id="PdfWarnLinkAltTextNotDescriptive",
        touchpoint=_ANN, standard="WCAG 2.4.4 (best practice)",
    ),
    _row(
        name="Bookmarks present", result="WARN",
        stable_id="PdfWarnBookmarksMissing",
        touchpoint=_DOC, standard="PDF/UA, WCAG 2.4.5",
    ),
    _row(
        name="Cross-language link targets identified", result="WARN",
        stable_id="PdfWarnCrossLanguageLinksUnverified",
        touchpoint=_ANN, standard="WCAG 3.1.2 (best practice)",
    ),
    _row(
        name="Cross-language link targets identified", result="FAIL",
        stable_id="PdfErrCrossLanguageLinksUnidentified",
        touchpoint=_ANN, standard="WCAG 3.1.2 (best practice)",
    ),

    # ---- lists → PDF_TAGGING ----
    _row(
        name="List structure valid", result="FAIL",
        stable_id="PdfErrListStructureInvalid",
        touchpoint=_TAG, standard="PDF/UA, WCAG 1.3.1",
    ),
    _row(
        name="No empty lists", result="FAIL",
        stable_id="PdfErrEmptyList",
        touchpoint=_TAG, standard="Matterhorn 09-006",
    ),
    _row(
        name="List nesting valid", result="WARN",
        stable_id="PdfWarnListNestingDeep",
        touchpoint=_TAG, standard="PDF/UA, WCAG 1.3.1",
    ),
    _row(
        name="List item labels", result="WARN",
        stable_id="PdfWarnListItemLabelsInconsistent",
        touchpoint=_TAG, standard="PDF/UA (best practice)",
    ),

    # ---- tables → PDF_TAGGING ----
    _row(
        name="Table headers defined", result="FAIL",
        stable_id="PdfErrTableHeadersMissing",
        touchpoint=_TAG, standard="PDF/UA, WCAG 1.3.1",
    ),
    _row(
        name="Table header scope defined", result="FAIL",
        stable_id="PdfErrTableHeaderScopeMissing",
        touchpoint=_TAG, standard="PDF/UA, WCAG 1.3.1",
    ),
    _row(
        name="Table structure sections", result="WARN",
        stable_id="PdfWarnTableSectionsMissing",
        touchpoint=_TAG, standard="PDF/UA, WCAG 1.3.1",
    ),
    _row(
        name="Table regularity", result="WARN",
        stable_id="PdfWarnTableIrregular",
        touchpoint=_TAG, standard="PDF/UA, WCAG 1.3.1",
    ),
    _row(
        name="No empty tables", result="FAIL",
        stable_id="PdfErrEmptyTable",
        touchpoint=_TAG, standard="Matterhorn 09-006",
    ),
    _row(
        name="Table captions", result="WARN",
        stable_id="PdfWarnTableCaptionMissing",
        touchpoint=_TAG, standard="WCAG 1.3.1",
    ),

    # ---- forms → PDF_ANNOTATIONS ----
    _row(
        name="Widget annotations inside Form tags", result="FAIL",
        stable_id="PdfErrWidgetAnnotationNotInFormTag",
        touchpoint=_ANN, standard="Matterhorn 28-012",
    ),
    _row(
        name="No XFA forms present", result="FAIL",
        stable_id="PdfErrXfaFormsPresent",
        touchpoint=_ANN, standard="Matterhorn 25-001",
    ),
    _row(
        name="Form fields labeled", result="FAIL",
        stable_id="PdfErrFormFieldUnlabeled",
        touchpoint=_ANN, standard="PDF/UA, WCAG 1.3.1, 4.1.2",
    ),
    _row(
        name="Required fields flagged", result="WARN",
        stable_id="PdfWarnRequiredFieldsNotFlagged",
        touchpoint=_ANN, standard="PDF/UA, WCAG 1.3.1, 3.3.2",
    ),
    _row(
        name="Form fields tagged in structure", result="FAIL",
        stable_id="PdfErrFormFieldNotInStructure",
        touchpoint=_ANN, standard="PDF/UA, WCAG 1.3.1",
    ),
    _row(
        name="Form fields tagged in structure", result="WARN",
        stable_id="PdfWarnFormFieldStructureUnverified",
        touchpoint=_ANN, standard="PDF/UA, WCAG 1.3.1",
    ),
    _row(
        name="Form page tab order", result="FAIL",
        stable_id="PdfErrFormTabOrderInvalid",
        touchpoint=_ANN, standard="PDF/UA, WCAG 2.1.1, 2.4.3",
    ),
    _row(
        name="Form field names unique", result="WARN",
        stable_id="PdfWarnFormFieldNamesNotUnique",
        touchpoint=_ANN, standard="WCAG 4.1.2",
    ),
    _row(
        name="Redundant entry in forms", result="WARN",
        stable_id="PdfWarnFormRedundantEntry",
        touchpoint=_ANN, standard="WCAG 3.3.7",
    ),
    _row(
        name="Accessible authentication", result="WARN",
        stable_id="PdfWarnAuthenticationNotAccessible",
        touchpoint=_ANN, standard="WCAG 3.3.8",
    ),

    # ---- interactive → PDF_ANNOTATIONS ----
    _row(
        name="Label in Name", result="FAIL",
        stable_id="PdfErrLabelInNameMismatch",
        touchpoint=_ANN, standard="WCAG 2.5.3",
    ),
    _row(
        name="Interactive element target size", result="WARN",
        stable_id="PdfWarnTargetSizeInsufficient",
        touchpoint=_ANN, standard="WCAG 2.5.5, 2.5.8",
    ),
    _row(
        name="Focus indicator visibility", result="WARN",
        stable_id="PdfWarnFocusIndicatorUnverified",
        touchpoint=_ANN, standard="WCAG 2.4.7",
    ),
    _row(
        name="Focus not obscured", result="FAIL",
        stable_id="PdfErrFocusObscured",
        touchpoint=_ANN, standard="WCAG 2.4.11",
    ),
    _row(
        name="Dragging movement alternatives", result="WARN",
        stable_id="PdfWarnDraggingNoAlternative",
        touchpoint=_ANN, standard="WCAG 2.5.7",
    ),

    # ---- color_contrast → PDF_TAGGING ----
    # FAIL/WARN flow normally; INFO is emitted when color_pairs is None
    # (collector didn't run). The audit uses a string-bound `name`/
    # `standard` local, so the AST extractor catches FAIL/WARN but the
    # INFO outcome (returned by the `_no_data_result` helper, whose
    # `name`/`standard` come from function parameters) isn't visible to
    # the literal scanner. We still emit catalogue rows for INFO so
    # downstream consumers get a Violation if the helper fires; the
    # completeness regression test accepts this gap because its scanner
    # uses the same literal-only logic.
    _row(
        name="Text contrast (WCAG AA)", result="FAIL",
        stable_id="PdfErrTextContrastBelowAa",
        touchpoint=_TAG, standard="WCAG 1.4.3",
    ),
    _row(
        name="Text contrast (WCAG AA)", result="INFO",
        stable_id="PdfInfoTextContrastNoData",
        touchpoint=_TAG, standard="WCAG 1.4.3",
    ),
    _row(
        name="Text contrast (WCAG AAA)", result="WARN",
        stable_id="PdfWarnTextContrastBelowAaa",
        touchpoint=_TAG, standard="WCAG 1.4.6",
    ),
    _row(
        name="Text contrast (WCAG AAA)", result="INFO",
        stable_id="PdfInfoTextContrastAaaNoData",
        touchpoint=_TAG, standard="WCAG 1.4.6",
    ),

    # ---- fonts → PDF_TAGGING ----
    _row(
        name="All fonts embedded", result="FAIL",
        stable_id="PdfErrFontsNotEmbedded",
        touchpoint=_TAG, standard="Matterhorn 31-001",
    ),
    _row(
        name="Font sizes accessible", result="FAIL",
        stable_id="PdfErrFontSizeTooSmall",
        touchpoint=_TAG, standard="WCAG 1.4 (best practice)",
    ),
    _row(
        name="Font sizes accessible", result="WARN",
        stable_id="PdfWarnFontSizeBorderline",
        touchpoint=_TAG, standard="WCAG 1.4 (best practice)",
    ),
    _row(
        name="Font faces readable", result="WARN",
        stable_id="PdfWarnFontFaceReadability",
        touchpoint=_TAG, standard="WCAG 1.4 (best practice)",
    ),
    _row(
        name="Font size ratio (magnification)", result="WARN",
        stable_id="PdfWarnFontMagnificationRatio",
        touchpoint=_TAG, standard="WCAG 1.4 (best practice)",
    ),
    _row(
        name="Text rotation accessible", result="WARN",
        stable_id="PdfWarnTextRotated",
        touchpoint=_TAG, standard="WCAG 1.4 (best practice)",
    ),
    _row(
        name="Italic text usage", result="WARN",
        stable_id="PdfWarnItalicTextOveruse",
        touchpoint=_TAG, standard="WCAG 1.4 (best practice)",
    ),
    _row(
        name="Line height accessible", result="WARN",
        stable_id="PdfWarnLineHeightInsufficient",
        touchpoint=_TAG, standard="WCAG 1.4.12",
    ),
    # FAIL variant emitted when >50% of measured line pairs fall below
    # the minimum ratio. The result string in fonts.py is a runtime
    # conditional ('FAIL' if pct_below > 50 else 'WARN') so the AST
    # completeness scanner doesn't see it; this row plugs the gap.
    _row(
        name="Line height accessible", result="FAIL",
        stable_id="PdfErrLineHeightInsufficient",
        touchpoint=_TAG, standard="WCAG 1.4.12",
    ),
    _row(
        name="Text alignment accessible", result="WARN",
        stable_id="PdfWarnTextAlignmentNonOptimal",
        touchpoint=_TAG, standard="WCAG 1.4 (best practice)",
    ),

    # ---- Matterhorn font/CMap/encoding checks (Phase 4.12 follow-up) ----
    _row(
        name="Unicode mapping (ToUnicode)", result="FAIL",
        stable_id="PdfErrFontMissingToUnicode",
        touchpoint=_TAG, standard="Matterhorn 10-001",
    ),
    _row(
        name="Unicode mapping (ToUnicode)", result="INFO",
        stable_id="PdfInfoFontMetadataMissingToUnicode",
        touchpoint=_TAG, standard="Matterhorn 10-001",
    ),
    _row(
        name="CID font GID mapping", result="FAIL",
        stable_id="PdfErrCidFontGidMappingMissing",
        touchpoint=_TAG, standard="Matterhorn 31-004",
    ),
    _row(
        name="CID font GID mapping", result="INFO",
        stable_id="PdfInfoFontMetadataMissingCidGidMapping",
        touchpoint=_TAG, standard="Matterhorn 31-004",
    ),
    _row(
        name="CMap resources valid", result="FAIL",
        stable_id="PdfErrCmapResourcesInvalid",
        touchpoint=_TAG, standard="Matterhorn 31-006",
    ),
    _row(
        name="CMap resources valid", result="INFO",
        stable_id="PdfInfoFontMetadataMissingCmapResources",
        touchpoint=_TAG, standard="Matterhorn 31-006",
    ),
    _row(
        name="Valid Unicode values", result="FAIL",
        stable_id="PdfErrToUnicodeInvalidValues",
        touchpoint=_TAG, standard="Matterhorn 10-001",
    ),
    _row(
        name="Valid Unicode values", result="INFO",
        stable_id="PdfInfoFontMetadataMissingValidUnicode",
        touchpoint=_TAG, standard="Matterhorn 10-001",
    ),
    _row(
        name="No .notdef glyph references", result="FAIL",
        stable_id="PdfErrFontNotdefReferenced",
        touchpoint=_TAG, standard="Matterhorn 31-025",
    ),
    _row(
        name="No .notdef glyph references", result="INFO",
        stable_id="PdfInfoFontMetadataMissingNotdef",
        touchpoint=_TAG, standard="Matterhorn 31-025",
    ),
    _row(
        name="Font glyph widths consistent", result="FAIL",
        stable_id="PdfErrFontGlyphWidthsInconsistent",
        touchpoint=_TAG, standard="Matterhorn 31-009",
    ),
    _row(
        name="Font glyph widths consistent", result="INFO",
        stable_id="PdfInfoFontMetadataMissingGlyphWidths",
        touchpoint=_TAG, standard="Matterhorn 31-009",
    ),
    _row(
        name="No .notdef in Differences array", result="FAIL",
        stable_id="PdfErrNotdefInDifferences",
        touchpoint=_TAG, standard="Matterhorn 31-008",
    ),
    _row(
        name="No .notdef in Differences array", result="INFO",
        stable_id="PdfInfoFontMetadataMissingDifferencesNotdef",
        touchpoint=_TAG, standard="Matterhorn 31-008",
    ),
    _row(
        name="Identity CMap has ToUnicode", result="FAIL",
        stable_id="PdfErrIdentityCmapMissingToUnicode",
        touchpoint=_TAG, standard="Matterhorn 31-007",
    ),
    _row(
        name="Identity CMap has ToUnicode", result="INFO",
        stable_id="PdfInfoFontMetadataMissingIdentityCmap",
        touchpoint=_TAG, standard="Matterhorn 31-007",
    ),
    _row(
        name="CMap WMode consistency", result="FAIL",
        stable_id="PdfErrCmapWmodeInconsistent",
        touchpoint=_TAG, standard="Matterhorn 31-005",
    ),
    _row(
        name="CMap WMode consistency", result="INFO",
        stable_id="PdfInfoFontMetadataMissingCmapWmode",
        touchpoint=_TAG, standard="Matterhorn 31-005",
    ),
    _row(
        name="Non-symbolic TrueType Latin mapping", result="FAIL",
        stable_id="PdfErrNonSymbolicTrueTypeLatinMapping",
        touchpoint=_TAG, standard="Matterhorn 31-003",
    ),
    _row(
        name="Non-symbolic TrueType Latin mapping", result="INFO",
        stable_id="PdfInfoFontMetadataMissingNonSymbolicTrueType",
        touchpoint=_TAG, standard="Matterhorn 31-003",
    ),
    _row(
        name="Font encoding consistency", result="INFO",
        stable_id="PdfInfoFontMetadataMissingEncodingConsistency",
        touchpoint=_TAG, standard="Matterhorn 31-002",
    ),
]


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


# Built once at module load so :func:`to_violation` is O(1) per call.
_CATALOGUE_INDEX: dict[tuple[str, CheckOutcome], CatalogueRow] = {
    (row["pdfmax_check_name"], row["pdfmax_result"]): row
    for row in CHECK_CATALOGUE
}


def _lookup_row(name: str, result: CheckOutcome) -> CatalogueRow | None:
    """Return the catalogue row for ``(name, result)`` or ``None``."""
    return _CATALOGUE_INDEX.get((name, result))


# ---------------------------------------------------------------------------
# to_violation
# ---------------------------------------------------------------------------


def to_violation(
    cr: CheckResult,
    *,
    pdf_doc_id: str,
) -> Violation | None:
    """Convert a :class:`CheckResult` to a :class:`Violation`.

    Looks up the ``(name, result)`` pair in :data:`CHECK_CATALOGUE`. If
    no row matches (the audit emitted a check the catalogue doesn't
    know about), raises :class:`ValueError` — the
    :func:`test_every_check_emitted_by_engine_has_catalogue_entry`
    regression test guarantees the catalogue covers every literal pair
    the audit engine emits.

    Returns ``None`` for ``PASS`` results (those aren't violations to
    display).

    The text fields on the returned :class:`Violation` —
    ``description``, ``short_title``, ``what``, ``why``, ``who``,
    ``remediation`` — hold **Fluent message IDs**, not user-visible
    strings. Templates resolve them with ``ftl(v.description)`` at
    render time. Phase 7 lands the corresponding ``.ftl`` files; for
    Phase 6 we just need the IDs to be predictable and stable, derived
    mechanically from the catalogue row's ``stable_id``.

    Args:
        cr: the audit-engine check result to translate.
        pdf_doc_id: the ``PdfDocument`` whose audit produced *cr*.
            Stored under ``metadata['pdf_doc_id']`` so consumers can
            link back to the source document without re-querying.
    """
    if cr.result == "PASS":
        return None
    row = _lookup_row(cr.name, cr.result)
    if row is None:
        # Fail-soft: a missing catalogue row used to raise ValueError,
        # which crashed the entire audit at the point where any single
        # check produced an unmapped (name, result) pair. The
        # completeness regression test (`test_every_check_emitted_by_
        # engine_has_catalogue_entry`) still catches missing rows during
        # CI, so this branch only fires in production for runtime-
        # conditional result strings the AST scanner can't see (e.g.,
        # `result="FAIL" if cond else "WARN"`). Synthesise a stable ID
        # from the result so the violation still surfaces to the user
        # with a placeholder identifier; the audit completes.
        logger.warning(
            "No CHECK_CATALOGUE row for (%r, %r); using fallback id. "
            + "Add the row to CHECK_CATALOGUE to fix the display IDs.",
            cr.name, cr.result,
        )
        slug = re.sub(r"[^A-Za-z0-9]+", "", cr.name) or "Unknown"
        prefix = {"FAIL": "Err", "WARN": "Warn", "INFO": "Info"}.get(
            cr.result, "Unknown",
        )
        stable_id = f"Pdf{prefix}{slug}"
        impact = default_impact(cr.result)
        touchpoint_value = TouchpointID.PDF_TAGGING.value
        wcag_criteria: list[str] = []
    else:
        impact = row["impact_override"] or default_impact(cr.result)
        stable_id = row["stable_id"]
        touchpoint_value = row["touchpoint"].value
        wcag_criteria = list(row["wcag_criteria"])
    metadata: dict[str, object] = {
        "pdf_doc_id": pdf_doc_id,
        "pdfmax_original_details": cr.details,
        "pdfmax_original_standard": cr.standard,
    }
    # Forward any structured side-data the check populated (e.g., the
    # font-size check's per-font inventory). Preserve the namespace so
    # template renderers can pick up the right key.
    for key, value in cr.extras.items():
        metadata[key] = value
    return Violation(
        id=stable_id,
        impact=impact,
        touchpoint=touchpoint_value,
        description=f"pdf-check-{stable_id}-name",
        short_title=f"pdf-check-{stable_id}-short-title",
        what=f"pdf-check-{stable_id}-what",
        why=f"pdf-check-{stable_id}-why",
        who=f"pdf-check-{stable_id}-who",
        remediation=f"pdf-remediation-{stable_id}",
        wcag_criteria=wcag_criteria,
        metadata=metadata,
        source_type="automated",
        detection_method="pdf_audit",
    )


__all__ = [
    "CHECK_CATALOGUE",
    "CatalogueRow",
    "default_impact",
    "extract_wcag_criteria",
    "to_violation",
]
