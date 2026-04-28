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
* Color and image extraction use third-party tools (Ghostscript,
  pdfminer, PIL) that occasionally fail on perfectly readable PDFs;
  failures inside those collectors degrade gracefully (empty result),
  they don't abort the audit.
* Only :class:`pikepdf.PdfError` raised when *opening* the PDF
  propagates as :class:`~auto_a11y.pdf.errors.CorruptPdf` — that's the
  one input we genuinely cannot work with.

AI analysis hook:

* When ``run_ai=True``, the pipeline currently emits a stub
  :class:`AIAnalysisResult` with an explanatory ``executive_summary``.
  Task 5.1 (semantic AI) replaces the stub with a real Claude call.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import pikepdf

from auto_a11y.pdf.audit import (
    colors,
    content_streams,
    font_metadata as font_metadata_collector,
    fonts,
    images,
    reading_order,
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
    locale: str = "en",
    images_out_dir: Path | None = None,
    progress: ProgressCallback | None = None,
    gs_path_override: str | None = None,
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
        run_ai: If ``True``, run Claude semantic analysis. Currently
            stubbed — Task 5.1 wires up the real call.
        ai_api_key: Anthropic API key (only used when ``run_ai=True``).
        locale: Locale for AI-generated summaries. Currently ``"en"`` or
            ``"fr"``; surfaced on :class:`AuditContext.locale` for any
            check that emits localised messages.
        images_out_dir: Where to extract image PNGs. ``None`` skips
            image extraction.
        progress: Callback ``(stage_name, fraction_complete)`` where
            ``fraction`` is in ``[0.0, 1.0]``. Called periodically.
            Fractions are monotonically non-decreasing within a single
            run.
        gs_path_override: Force a specific Ghostscript binary; passed
            through to the colour collectors. ``None`` uses the cached
            auto-detection.

    Returns:
        :class:`AuditResult` with check results, optional AI analysis,
        and outcome counts.

    Raises:
        CorruptPdf: if pikepdf cannot open the PDF.
    """
    del wcag_level  # currently advisory; the contrast check has its own threshold.
    del ai_api_key  # consumed by Task 5.1; ignored by the current stub.

    _emit(progress, "Opening PDF", 0.0)
    try:
        with pikepdf.open(pdf_path) as pdf:
            return _run_audit_with_pdf(
                pdf=pdf,
                pdf_path=pdf_path,
                run_ai=run_ai,
                locale=locale,
                images_out_dir=images_out_dir,
                progress=progress,
                gs_path_override=gs_path_override,
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
    locale: str,
    images_out_dir: Path | None,
    progress: ProgressCallback | None,
    gs_path_override: str | None,
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

    # ---- Step 4: font analysis ------------------------------------------
    _emit(progress, "Analyzing fonts", 0.30)
    font_analysis = fonts.extract_font_analysis(pdf_path)

    # ---- Step 4b: font-resource metadata --------------------------------
    _emit(progress, "Extracting font metadata", 0.40)
    font_metadata = font_metadata_collector.extract_font_metadata(pdf)

    # ---- Step 5: colours (best-effort; Ghostscript-dependent) -----------
    # Colours rasterises every page via Ghostscript — easily the long pole
    # on multi-page PDFs. We hand the collector a scaled sub-progress
    # callback that maps its 0.0-1.0 fraction onto the band 0.45-0.65 of
    # the overall pipeline so the SSE consumer sees per-page ticks.
    _emit(progress, "Extracting colors", 0.45)
    sub_colors = _scale_progress(progress, 0.45, 0.65)
    color_pairs, fg_only = colors.extract_text_colors(
        pdf_path, gs_path_override=gs_path_override, progress=sub_colors,
    )
    # ``extract_form_field_colors`` doesn't have its own broad swallow.
    # We absorb the same family of failures here so a missing or
    # malformed AcroForm never aborts the audit.
    try:
        form_pairs = colors.extract_form_field_colors(
            pdf, pdf_path, gs_path_override=gs_path_override
        )
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

    # ---- Step 7: visual reading order -----------------------------------
    _emit(progress, "Computing visual reading order", 0.70)
    visual_blocks, page_dims = reading_order.extract_visual_positions(pdf_path)
    page_width = page_dims[0].width if page_dims else 612.0
    detected_columns = reading_order.detect_columns(visual_blocks, page_width)
    element_positions = reading_order.match_elements_to_positions(
        elements, visual_blocks
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
        images=images_list,
        visual_blocks=visual_blocks,
        page_dimensions=page_dims,
        columns=detected_columns,
        element_positions=element_positions,
        visual_reading_order=visual_order,
        reading_order_mismatches=mismatches,
    )

    # ---- Step 9: run all checks -----------------------------------------
    _emit(progress, "Running checks", 0.80)
    check_results: list[CheckResult] = []
    for check_fn in ALL_CHECKS:
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

    # ---- Step 10: optional AI (stubbed pending Task 5.1) ----------------
    ai_analysis: AIAnalysisResult | None = None
    if run_ai:
        _emit(progress, "Running AI analysis", 0.90)
        # TODO(Task 5.1): replace with real call into
        # ``auto_a11y.pdf.audit.ai.semantic.analyze``.
        ai_analysis = AIAnalysisResult(
            findings=[],
            executive_summary=(
                "AI analysis not yet implemented (Task 5.1 pending). "
                "When the semantic AI module lands, this stub is replaced "
                "with the real Claude call."
            ),
            overall_severity="none",
            model="(stub)",
        )

    # ---- Step 11: extract metadata for AuditResult ----------------------
    pdf_version: str | None = pdf.pdf_version if pdf.pdf_version else None
    page_count = len(pdf.pages)
    declared_lang = _read_catalog_lang(pdf)

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
