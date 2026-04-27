"""End-to-end tests for the audit pipeline orchestrator.

Phase 5.3 of the pdfMax → auto_a11y integration: exercises
:func:`auto_a11y.pdf.audit.pipeline.run_audit` against synthetic PDFs
built in-memory with pikepdf. Tests cover the happy path on a minimal
1-page PDF, the well-formed-tagged path with /Title and /Lang, the
corrupt-PDF error case, the progress callback contract, the
crashing-check resilience guarantee, and the AI-stub behaviour.

The synthetic PDFs deliberately stay small (one or two blank pages) so
the suite finishes well under five seconds even when Ghostscript and
pdfminer trigger their full extraction passes.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest

from auto_a11y.pdf.audit import pipeline as pipeline_module
from auto_a11y.pdf.audit import run_audit
from auto_a11y.pdf.audit.checks import ALL_CHECKS
from auto_a11y.pdf.errors import CorruptPdf
from auto_a11y.pdf.models import AuditContext, AuditResult, CheckResult


# ---------------------------------------------------------------------------
# Synthetic-PDF helpers
# ---------------------------------------------------------------------------


def _write_minimal_pdf(out: Path) -> None:
    """One blank page, no /Title, no /Lang, no structure tree.

    Most accessibility checks should report ``FAIL`` or ``WARN`` against
    this document — but no check should raise.
    """
    pdf = pikepdf.Pdf.new()
    try:
        pdf.add_blank_page(page_size=(612, 792))
        pdf.save(out)
    finally:
        pdf.close()


def _write_well_formed_pdf(out: Path) -> None:
    """A 1-page PDF with /Title, /Lang, /MarkInfo /Marked=true, /StructTreeRoot.

    This isn't a fully-tagged document — we don't construct a real
    structure tree with MCIDs — but it does carry the metadata the
    document-properties / language checks look for, so a handful of
    checks should flip from ``FAIL`` on the minimal PDF to ``PASS`` here.
    """
    pdf = pikepdf.Pdf.new()
    try:
        pdf.add_blank_page(page_size=(612, 792))
        with pdf.open_metadata() as meta:
            meta["dc:title"] = "Well-Formed Test Document"
        pdf.Root["/Lang"] = pikepdf.String("en-US")
        pdf.Root["/MarkInfo"] = pikepdf.Dictionary(Marked=True)
        # Minimal StructTreeRoot — empty /K but present so the
        # "PDF is tagged" check passes.
        struct_root = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/StructTreeRoot"),
                K=pikepdf.Array([]),
            )
        )
        pdf.Root["/StructTreeRoot"] = struct_root
        pdf.save(out)
    finally:
        pdf.close()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_run_audit_on_minimal_pdf_returns_audit_result(tmp_path: Path) -> None:
    """A minimal PDF should yield an :class:`AuditResult` with checks.

    No specific outcome distribution is asserted (the catalogue evolves);
    we only require the result to be the right shape, the counts to add
    up, and no synthetic ``Internal check failure`` to be present.
    """
    pdf_path = tmp_path / "minimal.pdf"
    _write_minimal_pdf(pdf_path)

    result = run_audit(pdf_path)

    assert isinstance(result, AuditResult)
    assert result.pdf_path == pdf_path
    assert result.page_count == 1
    assert result.declared_lang is None  # no /Lang on the minimal doc
    assert result.detected_lang is None
    assert result.ai_analysis is None
    assert len(result.check_results) > 0
    assert (
        result.fail_count
        + result.warn_count
        + result.pass_count
        + result.info_count
    ) == len(result.check_results)
    # No check crashed during the minimal-PDF audit.
    crashes = [
        c for c in result.check_results
        if c.name.startswith("Internal check failure")
    ]
    assert crashes == [], f"checks crashed on minimal PDF: {crashes}"


def test_run_audit_well_formed_pdf_has_more_passes_than_minimal(
    tmp_path: Path,
) -> None:
    """Adding /Title, /Lang, /MarkInfo, /StructTreeRoot should raise PASS count."""
    minimal = tmp_path / "minimal.pdf"
    well_formed = tmp_path / "well_formed.pdf"
    _write_minimal_pdf(minimal)
    _write_well_formed_pdf(well_formed)

    minimal_result = run_audit(minimal)
    well_formed_result = run_audit(well_formed)

    assert well_formed_result.pass_count > minimal_result.pass_count
    assert well_formed_result.declared_lang == "en-US"
    # Same set of checks runs on both — total length is identical.
    assert len(well_formed_result.check_results) == len(minimal_result.check_results)


def test_run_audit_pdf_version_is_populated(tmp_path: Path) -> None:
    """The PDF header version round-trips through to :class:`AuditResult`."""
    pdf_path = tmp_path / "minimal.pdf"
    _write_minimal_pdf(pdf_path)

    result = run_audit(pdf_path)

    # pikepdf.Pdf.new() defaults to 1.3; the audit pipeline returns
    # whatever pikepdf reports without modification.
    assert result.pdf_version is not None
    assert result.pdf_version == "1.3"


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------


def test_run_audit_corrupt_pdf_raises_corrupt_pdf(tmp_path: Path) -> None:
    """Bytes that aren't a PDF at all → :class:`CorruptPdf` from the pipeline."""
    bad = tmp_path / "garbage.pdf"
    bad.write_bytes(b"this is not a PDF at all\n")

    with pytest.raises(CorruptPdf) as exc_info:
        run_audit(bad)

    assert str(bad) in str(exc_info.value)


# ---------------------------------------------------------------------------
# Progress callback contract
# ---------------------------------------------------------------------------


def test_run_audit_progress_callback_invoked_monotonically(
    tmp_path: Path,
) -> None:
    """The callback fires multiple times with non-decreasing fractions in [0, 1]."""
    pdf_path = tmp_path / "minimal.pdf"
    _write_minimal_pdf(pdf_path)

    events: list[tuple[str, float]] = []

    def cb(stage: str, fraction: float) -> None:
        events.append((stage, fraction))

    run_audit(pdf_path, progress=cb)

    assert len(events) >= 3, f"expected several progress events, got {events}"
    # First event is the open phase; last is "Done" at fraction 1.0.
    assert events[0][1] == 0.0
    assert events[-1][1] == 1.0
    fractions = [f for _stage, f in events]
    assert fractions == sorted(fractions), (
        f"progress fractions must be monotonic, got {fractions}"
    )
    for _stage, f in events:
        assert 0.0 <= f <= 1.0


def test_run_audit_no_progress_callback_does_not_raise(tmp_path: Path) -> None:
    """``progress=None`` is a fully-supported configuration."""
    pdf_path = tmp_path / "minimal.pdf"
    _write_minimal_pdf(pdf_path)

    result = run_audit(pdf_path, progress=None)
    assert result.page_count == 1


# ---------------------------------------------------------------------------
# Resilience: a single crashing check must not abort the audit
# ---------------------------------------------------------------------------


def test_run_audit_individual_check_crash_yields_synthetic_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If one check raises, surface a synthetic FAIL but run all the others.

    We monkey-patch the ``ALL_CHECKS`` list seen by the pipeline module
    to insert a guaranteed-crashing check at the front. The pipeline
    should keep going through the remaining checks and the final result
    should contain a synthetic ``"Internal check failure: …"`` entry.
    """
    pdf_path = tmp_path / "minimal.pdf"
    _write_minimal_pdf(pdf_path)

    crash_marker = "deliberate test failure 9d3a"

    def _crashing_check(_ctx: AuditContext) -> list[CheckResult]:
        raise RuntimeError(crash_marker)

    # Prepend the crashing check to the existing list. We patch the
    # symbol the pipeline module imported, not the source list, so we
    # don't have to worry about restoration order.
    patched = [_crashing_check, *ALL_CHECKS]
    # Establish the baseline first — number of CheckResults the real
    # ALL_CHECKS produces on this PDF without any crash injection.
    baseline_result = run_audit(pdf_path)
    baseline_count = len(baseline_result.check_results)

    monkeypatch.setattr(pipeline_module, "ALL_CHECKS", patched)
    result = run_audit(pdf_path)

    # The synthetic FAIL is present.
    crashes = [
        c for c in result.check_results
        if c.name.startswith("Internal check failure") and crash_marker in c.details
    ]
    assert len(crashes) == 1, (
        f"expected one synthetic FAIL containing {crash_marker!r}, "
        f"got {[c.details for c in result.check_results if 'Internal' in c.name]}"
    )
    assert crashes[0].result == "FAIL"
    assert "RuntimeError" in crashes[0].details

    # The other checks still ran — final length is baseline + 1 synthetic.
    assert len(result.check_results) == baseline_count + 1


# ---------------------------------------------------------------------------
# AI hook (currently stubbed)
# ---------------------------------------------------------------------------


def test_run_audit_run_ai_false_yields_no_ai_analysis(tmp_path: Path) -> None:
    """``run_ai=False`` must leave :attr:`AuditResult.ai_analysis` as ``None``."""
    pdf_path = tmp_path / "minimal.pdf"
    _write_minimal_pdf(pdf_path)

    result = run_audit(pdf_path, run_ai=False)
    assert result.ai_analysis is None


def test_run_audit_run_ai_true_returns_stub_placeholder(tmp_path: Path) -> None:
    """Until Task 5.1 lands, ``run_ai=True`` produces an explanatory stub.

    Documents the current contract so the test breaks loudly when the
    real implementation arrives — at which point this test gets rewritten.
    """
    pdf_path = tmp_path / "minimal.pdf"
    _write_minimal_pdf(pdf_path)

    result = run_audit(pdf_path, run_ai=True, ai_api_key=None)

    assert result.ai_analysis is not None
    assert result.ai_analysis.findings == []
    assert "not yet implemented" in result.ai_analysis.executive_summary
    assert result.ai_analysis.overall_severity == "none"
    assert result.ai_analysis.model == "(stub)"


# ---------------------------------------------------------------------------
# AuditResult counts agree with the underlying check_results list
# ---------------------------------------------------------------------------


def test_run_audit_counts_match_check_results(tmp_path: Path) -> None:
    """``fail_count`` + ``warn_count`` + ``pass_count`` + ``info_count`` ==
    ``len(check_results)`` and each individual count matches the underlying
    list filtered by outcome."""
    pdf_path = tmp_path / "well_formed.pdf"
    _write_well_formed_pdf(pdf_path)

    result = run_audit(pdf_path)

    assert result.fail_count == sum(
        1 for c in result.check_results if c.result == "FAIL"
    )
    assert result.warn_count == sum(
        1 for c in result.check_results if c.result == "WARN"
    )
    assert result.pass_count == sum(
        1 for c in result.check_results if c.result == "PASS"
    )
    assert result.info_count == sum(
        1 for c in result.check_results if c.result == "INFO"
    )


def test_run_audit_locale_is_threaded_into_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``locale`` reaches the :class:`AuditContext` each check sees."""
    pdf_path = tmp_path / "minimal.pdf"
    _write_minimal_pdf(pdf_path)

    captured_locales: list[str] = []

    def _capture_check(ctx: AuditContext) -> list[CheckResult]:
        captured_locales.append(ctx.locale)
        return []

    monkeypatch.setattr(pipeline_module, "ALL_CHECKS", [_capture_check])

    run_audit(pdf_path, locale="fr")

    assert captured_locales == ["fr"]


def test_run_audit_images_out_dir_extracts_images(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``images_out_dir`` is set, the directory is created and
    :class:`AuditContext.images` is populated.

    Our minimal PDF has no embedded images, so the resulting list is
    empty — but the side-effect that matters for the orchestrator
    contract is "the field stops being ``None``".
    """
    pdf_path = tmp_path / "minimal.pdf"
    _write_minimal_pdf(pdf_path)
    out = tmp_path / "extracted_images"

    captured: list[AuditContext] = []

    def _capture_check(ctx: AuditContext) -> list[CheckResult]:
        captured.append(ctx)
        return []

    monkeypatch.setattr(pipeline_module, "ALL_CHECKS", [_capture_check])
    run_audit(pdf_path, images_out_dir=out)

    assert out.exists()
    assert len(captured) == 1
    assert captured[0].images == []  # no image XObjects in a blank page


def test_run_audit_no_images_out_dir_leaves_images_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default ``images_out_dir=None`` skips extraction —
    :attr:`AuditContext.images` stays ``None``."""
    pdf_path = tmp_path / "minimal.pdf"
    _write_minimal_pdf(pdf_path)

    captured: list[AuditContext] = []

    def _capture_check(ctx: AuditContext) -> list[CheckResult]:
        captured.append(ctx)
        return []

    monkeypatch.setattr(pipeline_module, "ALL_CHECKS", [_capture_check])
    run_audit(pdf_path)

    assert len(captured) == 1
    assert captured[0].images is None


def test_run_audit_declared_lang_missing_when_no_catalog_lang(
    tmp_path: Path,
) -> None:
    """A PDF without ``/Lang`` produces ``declared_lang=None``."""
    pdf_path = tmp_path / "no_lang.pdf"
    _write_minimal_pdf(pdf_path)

    result = run_audit(pdf_path)
    assert result.declared_lang is None


def test_run_audit_declared_lang_whitespace_treated_as_missing(
    tmp_path: Path,
) -> None:
    """Whitespace-only ``/Lang`` is treated as "no language declared".

    Mirrors the convention used by the language and links_navigation
    check modules.
    """
    pdf_path = tmp_path / "ws_lang.pdf"
    pdf = pikepdf.Pdf.new()
    try:
        pdf.add_blank_page(page_size=(612, 792))
        pdf.Root["/Lang"] = pikepdf.String("   ")
        pdf.save(pdf_path)
    finally:
        pdf.close()

    result = run_audit(pdf_path)
    assert result.declared_lang is None


def test_run_audit_declared_lang_round_trips_normal_value(
    tmp_path: Path,
) -> None:
    """A normal ``/Lang`` value passes through to :attr:`AuditResult.declared_lang`."""
    pdf_path = tmp_path / "fr_lang.pdf"
    pdf = pikepdf.Pdf.new()
    try:
        pdf.add_blank_page(page_size=(612, 792))
        pdf.Root["/Lang"] = pikepdf.String("fr-CA")
        pdf.save(pdf_path)
    finally:
        pdf.close()

    result = run_audit(pdf_path)
    assert result.declared_lang == "fr-CA"
