"""Audit pipeline orchestrator.

Phase 5.3 of the pdfMax → auto_a11y integration. :func:`run_audit` is
the sync entry point that opens a PDF, runs every Phase 3 data
collector, populates an :class:`~auto_a11y.pdf.models.AuditContext`,
dispatches each Phase 4 check function in
:data:`~auto_a11y.pdf.audit.checks.ALL_CHECKS`, and returns an aggregated
:class:`~auto_a11y.pdf.models.AuditResult`.

Resilience model:

* Each individual check is wrapped in ``try`` / ``except`` — a crashing
  check yields a synthetic ``FAIL`` :class:`CheckResult` carrying the
  exception type and message, and the audit continues with the
  remaining checks.
* Color and image extraction use third-party libraries (PDFium,
  pdfminer, PIL) that occasionally fail on perfectly readable PDFs;
  failures inside those collectors degrade gracefully (empty result),
  they don't abort the audit.
* Only :class:`pikepdf.PdfError` raised when *opening* the PDF
  propagates as :class:`~auto_a11y.pdf.errors.CorruptPdf` — that's the
  one input we genuinely cannot work with.

AI analysis:

* When ``run_ai=True``, :mod:`auto_a11y.pdf.audit.ai` runs its eight passes
  after the deterministic checks and the report sections — every prompt is
  assembled from those inventories, so the AI step cannot run earlier.
* Two AI verdicts share a name with a deterministic check and replace it
  via :func:`merge_ai_checks`, because the deterministic half of each says
  in its own details that it could not see the page.
* AI never fails the audit. A missing key, a declined request or a crash
  yields an :class:`AIAnalysisResult` whose ``model`` says which happened
  (``(unavailable)`` / ``(failed)``) and whose ``executive_summary`` carries
  the reason, so "AI found nothing" is never confused with "AI never ran".
"""
from __future__ import annotations

import logging
import re
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pikepdf

logger = logging.getLogger(__name__)

from auto_a11y.pdf.audit import (
    content_classification,
    issue_map as issue_map_builder,
    colors,
    content_streams,
    font_metadata as font_metadata_collector,
    fonts,
    images,
    non_text_contrast,
    reading_order,
    required_fields,
    structure,
)
from auto_a11y.pdf.audit.checks import ALL_CHECKS
from auto_a11y.pdf.errors import CorruptPdf
from auto_a11y.pdf.models import (
    AIAnalysisResult,
    AuditContext,
    AuditResult,
    CheckResult,
    ProgressCallback,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_audit(
    pdf_path: Path,
    *,
    wcag_level: Literal["AA", "AAA"] = "AA",
    run_ai: bool = False,
    ai_api_key: str | None = None,
    ai_model: str | None = None,
    locale: str = "en",
    images_out_dir: Path | None = None,
    progress: ProgressCallback | None = None,
) -> AuditResult:
    """Audit a PDF for accessibility. Sync entry point.

    Opens the PDF, runs every Phase 3 data collector, populates an
    :class:`AuditContext`, dispatches every check function in
    :data:`ALL_CHECKS`, and returns the aggregated :class:`AuditResult`.

    Each individual check is wrapped in ``try`` / ``except`` — a
    crashing check yields a synthetic ``FAIL`` result with the exception
    type and message, and the audit continues. The pipeline itself only
    raises :class:`CorruptPdf` if the PDF cannot be opened.

    Args:
        pdf_path: Path to the PDF file.
        wcag_level: ``"AA"`` or ``"AAA"`` for contrast checks. Currently
            unused at this layer (the contrast check module reads its own
            threshold); accepted here so the public signature is stable
            once Phase 5.1 wires it through.
        run_ai: If ``True``, run the Claude analysis passes in
            :mod:`auto_a11y.pdf.audit.ai` after the deterministic checks.
        ai_api_key: Anthropic API key (only used when ``run_ai=True``).
            Falls back to ``ANTHROPIC_API_KEY`` / ``CLAUDE_API_KEY``.
        ai_model: Claude model for the AI passes. Defaults to
            :data:`auto_a11y.pdf.audit.ai.DEFAULT_MODEL`.
        locale: Locale for AI-generated summaries. Currently ``"en"`` or
            ``"fr"``; surfaced on :class:`AuditContext.locale` for any
            check that emits localised messages.
        images_out_dir: Where to extract image PNGs. ``None`` skips
            image extraction.
        progress: Callback ``(stage_name, fraction_complete)`` where
            ``fraction`` is in ``[0.0, 1.0]``. Called periodically.
            Fractions are monotonically non-decreasing within a single
            run.
            auto-detection.

    Returns:
        :class:`AuditResult` with check results, optional AI analysis,
        and outcome counts.

    Raises:
        CorruptPdf: if pikepdf cannot open the PDF.
    """
    del wcag_level  # currently advisory; the contrast check has its own threshold.

    _emit(progress, "Opening PDF", 0.0)
    try:
        with pikepdf.open(pdf_path) as pdf:
            return _run_audit_with_pdf(
                pdf=pdf,
                pdf_path=pdf_path,
                run_ai=run_ai,
                ai_api_key=ai_api_key,
                ai_model=ai_model,
                locale=locale,
                images_out_dir=images_out_dir,
                progress=progress,
            )
    except pikepdf.PdfError as exc:
        raise CorruptPdf(str(pdf_path), str(exc)) from exc


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _run_audit_with_pdf(
    *,
    pdf: pikepdf.Pdf,
    pdf_path: Path,
    run_ai: bool,
    ai_api_key: str | None,
    ai_model: str | None,
    locale: str,
    images_out_dir: Path | None,
    progress: ProgressCallback | None,
) -> AuditResult:
    """Body of :func:`run_audit` once the PDF is open.

    Split out so the ``with pikepdf.open`` context manager has a clear
    extent and the orchestrator's stage progression is readable.
    """
    # ---- Step 1: structure tree -----------------------------------------
    _emit(progress, "Walking structure tree", 0.05)
    elements, role_map = structure.walk_structure_tree(pdf)

    # ---- Step 2: MCID → text map ---------------------------------------
    _emit(progress, "Parsing content streams", 0.15)
    mcid_text_map = content_streams.extract_mcid_text_map_from_content_streams(pdf)

    # ---- Step 3: back-fill element text ---------------------------------
    structure.populate_element_text(elements, mcid_text_map)

    # ---- Step 3b: content classification --------------------------------
    _emit(progress, "Classifying page content", 0.25)
    content_class = content_classification.classify_content(pdf)

    # ---- Step 4: font analysis ------------------------------------------
    _emit(progress, "Analyzing fonts", 0.30)
    font_analysis = fonts.extract_font_analysis(pdf_path)

    # ---- Step 4b: font-resource metadata --------------------------------
    # Per-font ToUnicode/encoding/glyph-width walk. Per-page sub-progress
    # so multi-page documents don't sit silent during the walk.
    _emit(progress, "Extracting font metadata", 0.35)
    sub_font_meta = _scale_progress(progress, 0.35, 0.45)
    font_metadata = font_metadata_collector.extract_font_metadata(
        pdf, progress=sub_font_meta,
    )

    # ---- Step 5: colours (best-effort; needs page rasterisation) --------
    # Colours rasterises every page — easily the long pole
    # on multi-page PDFs. We hand the collector a scaled sub-progress
    # callback that maps its 0.0-1.0 fraction onto the band 0.45-0.65 of
    # the overall pipeline so the SSE consumer sees per-page ticks.
    _emit(progress, "Extracting colors", 0.45)
    sub_colors = _scale_progress(progress, 0.45, 0.65)
    color_pairs, fg_only = colors.extract_text_colors(
        pdf_path, progress=sub_colors,
    )
    # ``extract_form_field_colors`` doesn't have its own broad swallow.
    # We absorb the same family of failures here so a missing or
    # malformed AcroForm never aborts the audit.
    try:
        form_pairs = colors.extract_form_field_colors(pdf, pdf_path)
    except (pikepdf.PdfError, OSError, ValueError, TypeError, KeyError, AttributeError):
        form_pairs = {}

    # ---- Step 6: images (only when an output dir is supplied) -----------
    images_list = None
    if images_out_dir is not None:
        _emit(progress, "Extracting images", 0.60)
        try:
            images_list = images.extract_images(pdf_path, images_out_dir)
        except (pikepdf.PdfError, OSError, ValueError, TypeError):
            images_list = []

    # ---- Step 6b: non-text contrast + required-field indicators ---------
    # Both read the AcroForm and both rasterise pages, so they run
    # together while the page images are still warm in the OS cache.
    # Failures are absorbed: a malformed form or an unrenderable page
    # costs these two checks, not the audit.
    _emit(progress, "Measuring non-text contrast", 0.65)
    try:
        non_text = non_text_contrast.collect_non_text_contrast(
            pdf, pdf_path, elements,
        )
    except (pikepdf.PdfError, OSError, ValueError, TypeError, KeyError, AttributeError):
        non_text = None
    try:
        required_field_data = required_fields.collect_required_fields(pdf, elements)
    except (pikepdf.PdfError, OSError, ValueError, TypeError, KeyError, AttributeError):
        required_field_data = None

    # ---- Step 7: visual reading order -----------------------------------
    # The pdfminer parse here is the second-largest cost after colours;
    # tick per page through the band [0.66, 0.78] so the user doesn't see
    # 25% of the bar held still on a multi-minute pdfminer run.
    _emit(progress, "Computing visual reading order", 0.66)
    sub_reading = _scale_progress(progress, 0.66, 0.78)
    visual_blocks, page_dims = reading_order.extract_visual_positions(
        pdf_path, progress=sub_reading,
    )
    _emit(progress, "Reading order detecting columns", 0.78)
    page_width = page_dims[0].width if page_dims else 612.0
    detected_columns = reading_order.detect_columns(visual_blocks, page_width)
    # match_elements_to_positions is O(elements × blocks) — easily a
    # minute on big tagged PDFs. Give it the band [0.79, 0.86] so the
    # per-element ticks visibly move the bar (a 0.5% band would be
    # imperceptible).
    _emit(progress, "Reading order matching elements", 0.79)
    sub_match = _scale_progress(progress, 0.79, 0.86)
    element_positions = reading_order.match_elements_to_positions(
        elements, visual_blocks, progress=sub_match,
    )
    structure_order = [
        e.index
        for e in elements
        if e.resolved_tag != "Document"
        and (e.mcids or e.alt_text)
        and e.index in element_positions
    ]
    visual_order = reading_order.compute_visual_reading_order(
        elements, element_positions, detected_columns
    )
    _correlation, mismatches, _annotations = reading_order.compare_reading_orders(
        elements, structure_order, visual_order, element_positions
    )

    # ---- Step 8: build AuditContext -------------------------------------
    ctx = AuditContext(
        pdf=pdf,
        pdf_path=pdf_path,
        elements=elements,
        role_map=role_map,
        locale=locale,
        mcid_text_map=mcid_text_map,
        font_analysis=font_analysis,
        font_metadata=font_metadata,
        color_pairs=color_pairs,
        fg_only_colors=fg_only,
        form_color_pairs=form_pairs,
        content_classification=content_class,
        non_text_contrast=non_text,
        required_fields=required_field_data,
        images=images_list,
        visual_blocks=visual_blocks,
        page_dimensions=page_dims,
        columns=detected_columns,
        element_positions=element_positions,
        visual_reading_order=visual_order,
        reading_order_mismatches=mismatches,
    )

    # ---- Step 9: run all checks -----------------------------------------
    # ~108 pure-function checks read off the populated AuditContext.
    # Each one is sub-millisecond on a typical doc but the long tail
    # (font_metadata checks iterating per font) can be a couple seconds
    # — emit per-check ticks scaled onto [0.87, 0.97] so the bar moves
    # past matching's [0.79, 0.86] band cleanly.
    _emit(progress, "Running checks", 0.87)
    check_results: list[CheckResult] = []
    total_checks = max(1, len(ALL_CHECKS))
    for idx, check_fn in enumerate(ALL_CHECKS):
        # Tick every ~5% of checks so we don't flood JobManager writes.
        if progress is not None and (idx % max(1, total_checks // 20) == 0):
            check_name = _callable_name(check_fn)
            _emit(
                progress,
                f"Running check {idx + 1} of {total_checks}: {check_name}",
                0.87 + (idx / total_checks) * 0.10,
            )
        try:
            check_results.extend(check_fn(ctx))
        # Catching ``Exception`` is deliberate: a single check crashing
        # must not abort the audit. The synthetic ``FAIL`` surfaces the
        # bug to the user and to whoever reads the report.
        except Exception as exc:  # noqa: BLE001
            check_results.append(
                CheckResult(
                    name=f"Internal check failure: {_callable_name(check_fn)}",
                    standard="—",
                    result="FAIL",
                    details=f"{type(exc).__name__}: {exc}",
                )
            )

    # ---- Step 10: make every verdict locatable --------------------------
    check_results = [
        with_referenced_elements(check, len(elements))
        for check in check_results
    ]

    # ---- Step 11: extract metadata for AuditResult ----------------------
    pdf_version: str | None = pdf.pdf_version if pdf.pdf_version else None
    page_count = len(pdf.pages)
    declared_lang = _read_catalog_lang(pdf)

    # ---- Step 11b: build document-wide report sections -----------------
    # Sections 2-13 of pdfMax's _generate_report_inner. Built from the
    # already-populated AuditContext so we don't re-open the PDF.
    # Local import — keeps the report_sections module out of the
    # pipeline import graph until an audit actually runs.
    from auto_a11y.pdf.audit.report_sections import build_report_sections
    _emit(progress, "Building report sections", 0.99)
    try:
        report_sections = build_report_sections(
            ctx, check_results=check_results,
        )
    except Exception as exc:  # noqa: BLE001
        # A bug in any one section builder must not abort the audit —
        # the per-check verdicts are the primary deliverable.
        logger.warning(
            "Failed to build report sections: %s. "
            + "Per-check verdicts still persisted.",
            exc,
        )
        report_sections = {}

    # The viewer's overlays. Built here because it is the only place that
    # holds both the verdicts and the element geometry; stored alongside
    # the report sections so serving it needs no cache file on disk.
    try:
        report_sections["issue_map"] = issue_map_builder.build_issue_map(
            list(check_results), element_positions, page_dims,
            non_text_contrast=non_text,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to build the issue map: %s", exc)

    # ---- Step 12: optional AI analysis ---------------------------------
    # Runs last because every pass reads the inventories built above — the
    # semantic prompt is assembled from the tag tree, reading order, heading
    # map, alt text and form inventory, so it cannot run before them.
    ai_analysis: AIAnalysisResult | None = None
    if run_ai:
        _emit(progress, "Running AI analysis", 0.98)
        from auto_a11y.pdf.audit import ai as ai_module
        try:
            ai_analysis, ai_sections, ai_checks = ai_module.analyze(
                ctx,
                check_results=check_results,
                report_sections=report_sections,
                images_dir=images_out_dir,
                api_key=ai_api_key,
                model=ai_model or ai_module.DEFAULT_MODEL,
            )
            # AI sections sit alongside the deterministic ones; the
            # images_of_text key replaces its own "AI required" placeholder.
            report_sections.update(ai_sections)
            # The AI verdicts are checks like any other, so they belong in
            # the same list. Two of them share a name with a deterministic
            # check that said in its own details it could not see the
            # page; those replace it in place rather than appearing
            # twice. The executive summary was built before this step, so
            # rebuild it or its counts disagree with the list directly
            # beneath it.
            merge_ai_checks(check_results, ai_checks)
            try:
                from auto_a11y.pdf.audit.report_sections import (
                    build_executive_summary,
                )
                report_sections["executive_summary"] = build_executive_summary(
                    check_results
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not refresh the executive summary: %s", exc)
        except ai_module.AIUnavailable as exc:
            # An explicit opt-in that cannot run is worth saying out loud —
            # returning a clean audit would imply the AI passes found nothing.
            logger.warning("AI analysis was requested but could not run: %s", exc)
            ai_analysis = AIAnalysisResult(
                findings=[],
                executive_summary=str(exc),
                overall_severity="none",
                model="(unavailable)",
            )
        except Exception as exc:  # noqa: BLE001 — AI must not fail the audit
            logger.exception("AI analysis failed")
            ai_analysis = AIAnalysisResult(
                findings=[],
                executive_summary=(
                    f"AI analysis did not complete: {type(exc).__name__}: {exc}"
                ),
                overall_severity="none",
                model="(failed)",
            )

    _emit(progress, "Done", 1.0)
    return AuditResult.from_checks(
        pdf_path=pdf_path,
        pdf_version=pdf_version,
        page_count=page_count,
        declared_lang=declared_lang,
        # Phase 3's language helper currently only reads the catalog —
        # there's no separate "detected" pass yet, so we leave it None.
        # Task 5.x will populate this once a heuristic detector lands.
        detected_lang=None,
        check_results=check_results,
        ai_analysis=ai_analysis,
        report_sections=report_sections,
    )


def _callable_name(fn: object) -> str:
    """Best-effort identifier for a callable, for use in error messages.

    Wraps ``__qualname__`` access in ``getattr`` because ``Callable``
    isn't required to expose ``__qualname__`` — only function objects
    do. ``ty`` (correctly) flags direct ``fn.__qualname__`` access on a
    bare ``Callable`` because of this. Real check functions defined
    with ``def`` always have it; the ``getattr`` fallback covers the
    typing edge case without a suppression comment.
    """
    qualname = getattr(fn, "__qualname__", None)
    if isinstance(qualname, str):
        return qualname
    name = getattr(fn, "__name__", None)
    if isinstance(name, str):
        return name
    return repr(fn)


def _read_catalog_lang(pdf: pikepdf.Pdf) -> str | None:
    """Read ``/Lang`` off the catalog, returning ``None`` if absent or empty.

    Mirrors the convention used in
    :mod:`auto_a11y.pdf.audit.checks.language` and
    :mod:`auto_a11y.pdf.audit.checks.links_navigation`: catch ``KeyError``,
    coerce via ``str()``, treat whitespace-only as missing.
    """
    try:
        lang_obj = pdf.Root["/Lang"]
    except KeyError:
        return None
    rendered = str(lang_obj)
    if not rendered.strip():
        return None
    return rendered


#: Element references as the report prints them: ``[7]``, 1-based.
_ELEMENT_REF_RE = re.compile(r"\[(\d+)\]")


def with_referenced_elements(
    check: CheckResult, element_count: int
) -> CheckResult:
    """Fill in ``elements`` from the references the details already print.

    Almost every check that blames particular elements says so in its
    details — ``2 table(s) missing THead/TBody: [12] Table; [30] Table``.
    Until this ran, that text was the only place the information existed:
    :attr:`CheckResult.elements` stayed empty, so
    :mod:`auto_a11y.pdf.audit.issue_map` had nothing to locate and the
    viewer drew no overlays for any of it.

    Parsing the details rather than asking each check to duplicate the
    list keeps the two in step by construction — the overlay is drawn on
    exactly the element the report names, and neither can drift from the
    other. A check that sets ``elements`` explicitly is left alone.

    References are 1-based, matching what the report prints; anything
    outside the document's element range is dropped rather than clamped,
    since a number that does not name an element is not a location.
    """
    if check.elements:
        return check
    seen: dict[int, None] = {}
    for match in _ELEMENT_REF_RE.finditer(check.details):
        printed = int(match.group(1))
        index = printed - 1
        if 0 <= index < element_count:
            seen.setdefault(index, None)
    if not seen:
        return check
    return replace(check, elements=tuple(seen))


def merge_ai_checks(
    check_results: list[CheckResult], ai_checks: list[CheckResult]
) -> None:
    """Fold AI verdicts into the check list, replacing same-named ones.

    A verdict that shares its name with a deterministic check is the same
    check answered better — ``Non-text contrast sufficient`` and
    ``Required fields visually indicated`` both say in their
    deterministic details that they could not see the page. Replacing in
    place keeps the check's position in the report; appending would show
    the reader two verdicts for one criterion and leave them to guess
    which counts.
    """
    by_name = {result.name: index for index, result in enumerate(check_results)}
    for verdict in ai_checks:
        existing = by_name.get(verdict.name)
        if existing is None:
            by_name[verdict.name] = len(check_results)
            check_results.append(verdict)
        else:
            check_results[existing] = verdict


def _emit(
    progress: ProgressCallback | None, stage: str, fraction: float
) -> None:
    """Invoke the progress callback if one was supplied.

    Centralised so the pipeline can change how it reports without
    rewriting every call site.
    """
    if progress is not None:
        progress(stage, fraction)


def _scale_progress(
    parent: ProgressCallback | None,
    start: float,
    end: float,
) -> ProgressCallback | None:
    """Build a sub-progress callback whose ``[0.0, 1.0]`` range maps onto
    ``[start, end]`` of the parent.

    Long-running collectors (colour extraction, font metadata, visual
    reading order) accept their own progress callback and tick per page.
    The pipeline owns the overall fraction, so it scales the sub-stage's
    fraction onto a band of its own progression. Returns ``None`` when
    no parent is set so collectors can still be called cheaply.
    """
    if parent is None:
        return None
    span = max(0.0, end - start)

    def _sub(stage: str, fraction: float) -> None:
        bounded = max(0.0, min(1.0, fraction))
        parent(stage, start + bounded * span)

    return _sub
