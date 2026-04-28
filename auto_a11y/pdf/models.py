"""Internal data shapes for the PDF audit pipeline.

These dataclasses are the contract between data collectors (Phase 3),
check modules (Phase 4), and the orchestrator (Phase 5.3). Distinct from
:mod:`auto_a11y.models` — those are persisted DB-shapes; these are
pipeline-internal.

Design notes:

* :class:`CheckResult` is the 4-field shape pdfMax used at line 4446 of
  ``pdf_accessibility_audit.py``. Frozen because each check function
  emits final verdicts.
* :class:`AuditContext` is the *only* mutable dataclass here — the
  orchestrator constructs a partial context, then runs each Phase 3
  data collector and assigns its output onto the matching attribute
  before dispatching Phase 4 check functions. The optional fields cover
  every collector currently implemented in :mod:`auto_a11y.pdf.audit`;
  add new ones here when new collectors land.
* :class:`AuditResult.from_checks` is the pipeline's exit funnel —
  callers don't have to recompute counts, and adding a new outcome to
  :data:`CheckOutcome` will surface as a missing branch in the type
  checker.
* :data:`TagElement` is a type alias for
  :class:`auto_a11y.pdf.audit.structure.StructElement` — pdfMax called
  this concept "TagElement" so check modules read more naturally
  importing it from one place.

Import-cycle invariant: every collector module imports from
:mod:`auto_a11y.pdf.audit.*` only — none of them import from
:mod:`auto_a11y.pdf.models`. This module is the dependency *sink* for
the pipeline; check modules and the orchestrator import from here.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

import pikepdf

from auto_a11y.pdf.audit.colors import (
    ColorPairInfo,
    FgOnlyColorInfo,
    FormColorPairInfo,
    RgbColor,
)
from auto_a11y.pdf.audit.font_metadata import FontMetadata
from auto_a11y.pdf.audit.fonts import FontAnalysis
from auto_a11y.pdf.audit.images import ExtractedImage
from auto_a11y.pdf.audit.reading_order import (
    Column,
    ElementPosition,
    PageDimensions,
    ReadingOrderMismatch,
    VisualBlock,
)
from auto_a11y.pdf.audit.structure import StructElement


# ---------------------------------------------------------------------------
# Check verdicts
# ---------------------------------------------------------------------------


CheckOutcome: TypeAlias = Literal["PASS", "FAIL", "WARN", "INFO"]


@dataclass(frozen=True)
class CheckResult:
    """One check's verdict.

    Attributes:
        name: human-readable check name (e.g. ``"Document title set"``).
        standard: WCAG / PDF/UA / Matterhorn reference
            (e.g. ``"WCAG 2.4.2, PDF/UA"``).
        result: ``"PASS" | "FAIL" | "WARN" | "INFO"``.
        details: human-readable explanation of *why* this verdict was
            reached. Surfaced verbatim in reports.
    """

    name: str
    standard: str
    result: CheckOutcome
    details: str


# pdfMax used a separate ``StructElement`` class; we reuse the one from
# :mod:`auto_a11y.pdf.audit.structure`. Re-export here for convenience so
# check modules can import from a single place.
TagElement: TypeAlias = StructElement


# ---------------------------------------------------------------------------
# AuditContext — pipeline-internal data bundle
# ---------------------------------------------------------------------------


@dataclass
class AuditContext:
    """The bundle of data that every check function reads.

    The orchestrator (Phase 5.3 ``run_audit``) constructs this once with
    the always-present fields (the open Pdf, its path, the structure-tree
    walk output), then runs each Phase 3 data collector and assigns its
    output onto the matching optional field. Phase 4 check functions
    receive the (now fully-populated) context and read whichever fields
    they need.

    Optional fields default to ``None`` so check functions can detect
    "this collector didn't run" and either degrade gracefully or skip.

    Mutability rationale:

    * Every other dataclass in this module is ``frozen=True``.
    * :class:`AuditContext` is mutable so the orchestrator can populate
      optional fields incrementally — a frozen variant would force the
      orchestrator to either build all collectors up-front (preventing
      lazy / conditional collection) or keep the context in a builder
      object until every field was known. Neither matches pdfMax's
      original incremental shape.
    """

    pdf: pikepdf.Pdf
    """The open PDF document."""

    pdf_path: Path
    """The path to the PDF file. Some collectors re-open it themselves."""

    elements: list[TagElement]
    """Output of :func:`auto_a11y.pdf.audit.structure.walk_structure_tree`."""

    role_map: dict[str, str]
    """RoleMap: custom tag → standard PDF tag."""

    locale: str = "en"
    """User's preferred locale for any human-readable output the checks
    produce. Currently used to pick localised messages for failure
    details. Default ``"en"`` matches the project default."""

    # ---- Optional collector outputs ---------------------------------------
    # Each field below corresponds to a Phase 3 collector. They're
    # populated by the orchestrator after the relevant collector runs;
    # checks that don't need a particular product simply ignore its
    # field. The order mirrors the collectors in
    # :mod:`auto_a11y.pdf.audit`.

    mcid_text_map: dict[int, dict[int, str]] | None = None
    """Output of
    :func:`auto_a11y.pdf.audit.content_streams.extract_mcid_text_map_from_content_streams`.

    ``{page_index: {mcid: text}}``. Used to back-fill
    :attr:`StructElement.text_content` and by reading-order checks."""

    font_analysis: FontAnalysis | None = None
    """Output of :func:`auto_a11y.pdf.audit.fonts.extract_font_analysis`.

    Aggregate of fonts, rotations, italics, line-spacing, and alignment
    measurements across the document."""

    font_metadata: FontMetadata | None = None
    """Output of :func:`auto_a11y.pdf.audit.font_metadata.extract_font_metadata`.

    Per-font resource metadata (ToUnicode, encoding /Differences, glyph
    widths, CMap WMode, symbolic flag, descendant CIDFont, etc.) used by
    the Phase 4 Matterhorn font/CMap/encoding checks."""

    color_pairs: dict[tuple[RgbColor, RgbColor], ColorPairInfo] | None = None
    """First half of
    :func:`auto_a11y.pdf.audit.colors.extract_text_colors`.

    Keyed by ``(foreground, background)`` RGB tuples. Used by contrast
    checks."""

    fg_only_colors: dict[RgbColor, FgOnlyColorInfo] | None = None
    """Second half of
    :func:`auto_a11y.pdf.audit.colors.extract_text_colors`.

    Foreground-only aggregate for legacy backward-compat consumers."""

    form_color_pairs: dict[tuple[RgbColor, RgbColor], FormColorPairInfo] | None = None
    """Output of
    :func:`auto_a11y.pdf.audit.colors.extract_form_field_colors`.

    Form-widget ``(foreground, background)`` pairs from /DA, /MK, /AP."""

    images: list[ExtractedImage] | None = None
    """Output of :func:`auto_a11y.pdf.audit.images.extract_images`.

    One entry per image XObject successfully written to disk. Image-alt
    checks consult this together with the structure tree."""

    visual_blocks: list[VisualBlock] | None = None
    """First component of
    :func:`auto_a11y.pdf.audit.reading_order.extract_visual_positions`.

    Flat list of pdfminer-derived text blocks across all pages."""

    page_dimensions: list[PageDimensions] | None = None
    """Second component of ``extract_visual_positions``.

    One :class:`PageDimensions` per page, in page order."""

    columns: list[Column] | None = None
    """Output of :func:`auto_a11y.pdf.audit.reading_order.detect_columns`.

    Detected horizontal columns, one entry per (page, column) pair."""

    element_positions: dict[int, ElementPosition] | None = None
    """Output of
    :func:`auto_a11y.pdf.audit.reading_order.match_elements_to_positions`.

    Maps :attr:`StructElement.index` → its visual position."""

    visual_reading_order: list[int] | None = None
    """Output of
    :func:`auto_a11y.pdf.audit.reading_order.compute_visual_reading_order`.

    Element indices in computed visual reading order."""

    reading_order_mismatches: list[ReadingOrderMismatch] | None = None
    """First non-scalar component of
    :func:`auto_a11y.pdf.audit.reading_order.compare_reading_orders`.

    Detected nearby-pair inversions between structure and visual order."""


# ---------------------------------------------------------------------------
# AI analysis output
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AIFinding:
    """One semantic / visual finding from Claude analysis.

    Mirrors pdfMax's ``AIFinding`` shape. Used by Phase 5.1 (semantic AI)
    and Phase 6 (check_mapper).

    Attributes:
        category: e.g. ``"tagging_structure"``, ``"color_contrast"``.
        severity: ``"high" | "medium" | "low" | "info"``.
        title: short summary, suitable as a report heading.
        description: full multi-sentence explanation.
        page: page index where the issue was observed, if known.
        element_index: link back to :attr:`TagElement.index` if the
            finding can be attributed to a specific structure element.
    """

    category: str
    severity: Literal["high", "medium", "low", "info"]
    title: str
    description: str
    page: int | None = None
    element_index: int | None = None


@dataclass(frozen=True)
class AIAnalysisResult:
    """Aggregated AI output for one audit run.

    Attributes:
        findings: every :class:`AIFinding` produced by the run.
        executive_summary: multi-paragraph human summary of the AI's
            overall assessment.
        overall_severity: top-line severity used to gate report visibility.
        model: which Claude model produced this, e.g.
            ``"claude-opus-4-5-20250929"``.
        cached_input_tokens: prompt-cache telemetry.
        uncached_input_tokens: prompt-cache telemetry.
        output_tokens: prompt-cache telemetry.
    """

    findings: list[AIFinding]
    executive_summary: str
    overall_severity: Literal["high", "medium", "low", "none"]
    model: str
    cached_input_tokens: int = 0
    uncached_input_tokens: int = 0
    output_tokens: int = 0


# ---------------------------------------------------------------------------
# AuditResult — pipeline exit type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditResult:
    """The final audit output from :func:`run_audit`.

    Combines the deterministic check results with the optional AI
    section. The four count fields are derived from
    :attr:`check_results` by :meth:`from_checks` — callers should always
    use that constructor rather than building the dataclass directly,
    so the counts can never drift out of sync with the underlying list.
    """

    pdf_path: Path
    pdf_version: str | None
    page_count: int
    declared_lang: str | None
    detected_lang: str | None
    check_results: list[CheckResult]
    ai_analysis: AIAnalysisResult | None
    fail_count: int
    warn_count: int
    pass_count: int
    info_count: int

    @classmethod
    def from_checks(
        cls,
        *,
        pdf_path: Path,
        pdf_version: str | None,
        page_count: int,
        declared_lang: str | None,
        detected_lang: str | None,
        check_results: list[CheckResult],
        ai_analysis: AIAnalysisResult | None,
    ) -> AuditResult:
        """Construct an :class:`AuditResult`, computing the count fields.

        Adding a new outcome to :data:`CheckOutcome` will require adding
        a matching count field here and a corresponding sum line — both
        sites the type checker can flag if the literal grows.
        """
        fail = sum(1 for c in check_results if c.result == "FAIL")
        warn = sum(1 for c in check_results if c.result == "WARN")
        passed = sum(1 for c in check_results if c.result == "PASS")
        info = sum(1 for c in check_results if c.result == "INFO")
        return cls(
            pdf_path=pdf_path,
            pdf_version=pdf_version,
            page_count=page_count,
            declared_lang=declared_lang,
            detected_lang=detected_lang,
            check_results=check_results,
            ai_analysis=ai_analysis,
            fail_count=fail,
            warn_count=warn,
            pass_count=passed,
            info_count=info,
        )


# ---------------------------------------------------------------------------
# Progress reporting
# ---------------------------------------------------------------------------


#: Progress callback signature for Phase 5.3's ``run_audit``: called with
#: ``(stage_name, fraction_complete)`` where ``fraction`` is in
#: ``[0.0, 1.0]``. The pipeline guarantees ``fraction`` is monotonically
#: non-decreasing across calls within a single run.
ProgressCallback: TypeAlias = Callable[[str, float], None]


__all__ = [
    "AIAnalysisResult",
    "AIFinding",
    "AuditContext",
    "AuditResult",
    "CheckOutcome",
    "CheckResult",
    "ProgressCallback",
    "TagElement",
]
