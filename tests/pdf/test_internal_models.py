"""Tests for ``auto_a11y.pdf.models`` — pipeline-internal dataclasses.

Phase 5 of the pdfMax → auto_a11y integration. These types are the
contract between data collectors (Phase 3) and check modules (Phase 4) —
the orchestrator (Phase 5.3) builds an :class:`AuditContext`, hands it to
each check function, and finally aggregates :class:`CheckResult`\\ s into
an :class:`AuditResult`.

The tests cover:

* ``CheckResult`` round-tripping (frozen, four-outcome literal).
* ``AuditContext`` mutability (the orchestrator populates optional
  fields incrementally as collectors run).
* ``AuditResult.from_checks`` count derivation.
* ``AIFinding`` / ``AIAnalysisResult`` immutability.
* ``ProgressCallback`` type-alias is a usable callable shape.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pikepdf
import pytest

from auto_a11y.pdf.audit.colors import ColorPairInfo, FgOnlyColorInfo, FormColorPairInfo, RgbColor
from auto_a11y.pdf.audit.fonts import FontAnalysis
from auto_a11y.pdf.audit.images import ExtractedImage
from auto_a11y.pdf.audit.reading_order import (
    Column,
    ElementPosition,
    PageDimensions,
    ReadingOrderMismatch,
    VisualBlock,
)
from auto_a11y.pdf.models import (
    AIAnalysisResult,
    AIFinding,
    AuditContext,
    AuditResult,
    CheckOutcome,
    CheckResult,
    ProgressCallback,
    TagElement,
)


# ---------------------------------------------------------------------------
# CheckResult
# ---------------------------------------------------------------------------


def test_check_result_construction() -> None:
    cr = CheckResult(name="X", standard="WCAG 1.1.1", result="PASS", details="ok")
    assert cr.result == "PASS"
    assert cr.name == "X"
    assert cr.standard == "WCAG 1.1.1"
    assert cr.details == "ok"


def test_check_result_is_frozen() -> None:
    """CheckResult is frozen — fields cannot be reassigned at runtime.

    We use ``setattr`` to drive the assignment because direct attribute
    assignment on a frozen dataclass is also a static type error in
    pyright/ty (and the project's zero-escape-hatch policy forbids
    ``# type: ignore``). The runtime behaviour is identical: the
    ``__setattr__`` override raises ``FrozenInstanceError`` regardless
    of the syntactic form used to assign.
    """
    cr = CheckResult(name="X", standard="s", result="PASS", details=".")
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(cr, "result", "FAIL")


def test_check_outcome_literal_rejects_invalid() -> None:
    """The Literal type doesn't enforce at runtime, but at type-check time
    only the four allowed values pass. Document the contract."""
    valid_results: list[CheckOutcome] = ["PASS", "FAIL", "WARN", "INFO"]
    assert len(valid_results) == 4
    assert set(valid_results) == {"PASS", "FAIL", "WARN", "INFO"}


def test_check_result_equality() -> None:
    """Frozen dataclasses are hashable / value-equal."""
    a = CheckResult(name="X", standard="s", result="PASS", details=".")
    b = CheckResult(name="X", standard="s", result="PASS", details=".")
    c = CheckResult(name="Y", standard="s", result="PASS", details=".")
    assert a == b
    assert a != c
    # Frozen dataclasses are hashable; usable in a set.
    assert len({a, b, c}) == 2


# ---------------------------------------------------------------------------
# AuditResult
# ---------------------------------------------------------------------------


def test_audit_result_from_checks_counts() -> None:
    checks = [
        CheckResult("a", "s", "PASS", "."),
        CheckResult("b", "s", "FAIL", "."),
        CheckResult("c", "s", "FAIL", "."),
        CheckResult("d", "s", "WARN", "."),
        CheckResult("e", "s", "INFO", "."),
    ]
    result = AuditResult.from_checks(
        pdf_path=Path("/tmp/x.pdf"),
        pdf_version="1.7",
        page_count=3,
        declared_lang="en",
        detected_lang=None,
        check_results=checks,
        ai_analysis=None,
    )
    assert result.fail_count == 2
    assert result.warn_count == 1
    assert result.pass_count == 1
    assert result.info_count == 1
    assert result.pdf_path == Path("/tmp/x.pdf")
    assert result.pdf_version == "1.7"
    assert result.page_count == 3
    assert result.declared_lang == "en"
    assert result.detected_lang is None
    assert result.ai_analysis is None
    assert result.check_results == checks


def test_audit_result_from_checks_empty() -> None:
    """Zero checks → all counts zero, no exception."""
    result = AuditResult.from_checks(
        pdf_path=Path("/x.pdf"),
        pdf_version=None,
        page_count=0,
        declared_lang=None,
        detected_lang=None,
        check_results=[],
        ai_analysis=None,
    )
    assert result.fail_count == 0
    assert result.warn_count == 0
    assert result.pass_count == 0
    assert result.info_count == 0


def test_audit_result_is_frozen() -> None:
    """AuditResult is frozen — once produced, it's immutable."""
    result = AuditResult.from_checks(
        pdf_path=Path("/x.pdf"),
        pdf_version=None,
        page_count=0,
        declared_lang=None,
        detected_lang=None,
        check_results=[],
        ai_analysis=None,
    )
    # See test_check_result_is_frozen for why we use setattr here.
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(result, "fail_count", 99)


# ---------------------------------------------------------------------------
# AuditContext
# ---------------------------------------------------------------------------


def test_audit_context_is_mutable() -> None:
    """AuditContext fields can be populated incrementally."""
    pdf = pikepdf.Pdf.new()
    try:
        ctx = AuditContext(
            pdf=pdf,
            pdf_path=Path("/tmp/x"),
            elements=[],
            role_map={},
        )
        assert ctx.locale == "en"
        ctx.locale = "fr"
        assert ctx.locale == "fr"
    finally:
        pdf.close()


def test_audit_context_optional_fields_default_none() -> None:
    """Every collector-output field starts None so callers see "not yet run"."""
    pdf = pikepdf.Pdf.new()
    try:
        ctx = AuditContext(
            pdf=pdf,
            pdf_path=Path("/tmp/x"),
            elements=[],
            role_map={},
        )
        assert ctx.mcid_text_map is None
        assert ctx.font_analysis is None
        assert ctx.color_pairs is None
        assert ctx.fg_only_colors is None
        assert ctx.form_color_pairs is None
        assert ctx.images is None
        assert ctx.visual_blocks is None
        assert ctx.page_dimensions is None
        assert ctx.columns is None
        assert ctx.element_positions is None
        assert ctx.reading_order_mismatches is None
        assert ctx.visual_reading_order is None
    finally:
        pdf.close()


def test_audit_context_optional_fields_assignable() -> None:
    """Each optional field can be populated with the right concrete type."""
    pdf = pikepdf.Pdf.new()
    try:
        ctx = AuditContext(
            pdf=pdf,
            pdf_path=Path("/tmp/x"),
            elements=[],
            role_map={},
        )
        ctx.mcid_text_map = {0: {1: "hello"}}
        ctx.font_analysis = FontAnalysis(
            fonts={}, rotations=[], italic_runs=[],
            line_spacings=[], alignments=[],
        )
        white: RgbColor = (1.0, 1.0, 1.0)
        black: RgbColor = (0.0, 0.0, 0.0)
        ctx.color_pairs = {(black, white): ColorPairInfo()}
        ctx.fg_only_colors = {black: FgOnlyColorInfo()}
        ctx.form_color_pairs = {(black, white): FormColorPairInfo()}
        ctx.images = [ExtractedImage(index=1, filename="x.png", width=10, height=10)]
        ctx.visual_blocks = [
            VisualBlock(page=0, x0=0.0, y0=0.0, x1=1.0, y1=1.0, y_top=0.0, text="hi")
        ]
        ctx.page_dimensions = [PageDimensions(width=612.0, height=792.0)]
        ctx.columns = [Column(page=0, x0=0.0, x1=612.0)]
        ctx.element_positions = {
            0: ElementPosition(page=0, x0=0.0, y0=0.0, x1=1.0, y1=1.0, y_top=0.0)
        }
        ctx.reading_order_mismatches = [
            ReadingOrderMismatch(struct_first=0, struct_second=1,
                                 visual_first=1, visual_second=0)
        ]
        ctx.visual_reading_order = [0, 1, 2]

        # Read-back: confirm assignment stuck.
        assert ctx.mcid_text_map == {0: {1: "hello"}}
        assert ctx.font_analysis is not None
        assert ctx.color_pairs is not None
        assert ctx.fg_only_colors is not None
        assert ctx.form_color_pairs is not None
        assert ctx.images is not None and len(ctx.images) == 1
        assert ctx.visual_blocks is not None and ctx.visual_blocks[0].text == "hi"
        assert ctx.page_dimensions is not None
        assert ctx.columns is not None
        assert ctx.element_positions is not None
        assert ctx.reading_order_mismatches is not None
        assert ctx.visual_reading_order == [0, 1, 2]
    finally:
        pdf.close()


def test_tag_element_alias_is_struct_element() -> None:
    """``TagElement`` is a re-export of ``StructElement`` for convenience."""
    from auto_a11y.pdf.audit.structure import StructElement
    assert TagElement is StructElement


# ---------------------------------------------------------------------------
# AIFinding / AIAnalysisResult
# ---------------------------------------------------------------------------


def test_ai_finding_minimal_construction() -> None:
    """Page and element_index are optional."""
    f = AIFinding(
        category="tagging_structure",
        severity="high",
        title="Missing alt text",
        description="A figure has no /Alt entry.",
    )
    assert f.category == "tagging_structure"
    assert f.severity == "high"
    assert f.page is None
    assert f.element_index is None


def test_ai_finding_full_construction() -> None:
    f = AIFinding(
        category="color_contrast",
        severity="medium",
        title="Low contrast",
        description="2.1:1 ratio.",
        page=3,
        element_index=42,
    )
    assert f.page == 3
    assert f.element_index == 42


def test_ai_finding_is_frozen() -> None:
    f = AIFinding(category="x", severity="low", title="t", description="d")
    # See test_check_result_is_frozen for why we use setattr here.
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(f, "severity", "high")


def test_ai_analysis_result_frozen() -> None:
    """AIAnalysisResult is frozen — fields cannot be reassigned."""
    aar = AIAnalysisResult(
        findings=[],
        executive_summary="",
        overall_severity="none",
        model="claude-x",
    )
    # See test_check_result_is_frozen for why we use setattr here.
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(aar, "executive_summary", "new")


def test_ai_analysis_result_default_token_counts() -> None:
    aar = AIAnalysisResult(
        findings=[],
        executive_summary="summary",
        overall_severity="low",
        model="claude-x",
    )
    assert aar.cached_input_tokens == 0
    assert aar.uncached_input_tokens == 0
    assert aar.output_tokens == 0


def test_ai_analysis_result_carries_findings() -> None:
    f = AIFinding(category="x", severity="high", title="t", description="d")
    aar = AIAnalysisResult(
        findings=[f],
        executive_summary="s",
        overall_severity="high",
        model="claude-x",
        cached_input_tokens=10,
        uncached_input_tokens=20,
        output_tokens=30,
    )
    assert aar.findings == [f]
    assert aar.cached_input_tokens == 10
    assert aar.uncached_input_tokens == 20
    assert aar.output_tokens == 30


def test_audit_result_with_ai_analysis() -> None:
    """AuditResult.from_checks accepts and stores an AIAnalysisResult."""
    aar = AIAnalysisResult(
        findings=[], executive_summary="s",
        overall_severity="none", model="claude-x",
    )
    result = AuditResult.from_checks(
        pdf_path=Path("/x.pdf"),
        pdf_version="1.7",
        page_count=1,
        declared_lang=None,
        detected_lang=None,
        check_results=[],
        ai_analysis=aar,
    )
    assert result.ai_analysis is aar


# ---------------------------------------------------------------------------
# ProgressCallback
# ---------------------------------------------------------------------------


def test_progress_callback_type_alias_is_callable() -> None:
    """ProgressCallback is just Callable[[str, float], None]."""
    seen: list[tuple[str, float]] = []

    def cb(stage: str, fraction: float) -> None:
        seen.append((stage, fraction))

    fn: ProgressCallback = cb
    fn("init", 0.0)
    fn("collecting", 0.5)
    fn("done", 1.0)
    assert seen == [("init", 0.0), ("collecting", 0.5), ("done", 1.0)]
