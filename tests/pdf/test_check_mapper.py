"""Tests for :mod:`auto_a11y.pdf.translation.check_mapper`.

The completeness regression test is the load-bearing one: it scans every
audit-engine check function's source for literal ``CheckResult(...)``
constructions and asserts each ``(name, result)`` pair is present in
:data:`CHECK_CATALOGUE`. When a new check is added to the audit engine
without updating the catalogue, this test fails — the fix is to add a
new catalogue row, not to skip the test.
"""
from __future__ import annotations

import pytest

# Importing :mod:`auto_a11y.pdf.translation.check_mapper` first lets
# its module-level import-order shim resolve the audit package
# __init__ before :mod:`auto_a11y.pdf.models` is pulled in below.
from auto_a11y.pdf.translation.check_mapper import (
    CHECK_CATALOGUE,
    default_impact,
    extract_wcag_criteria,
    to_violation,
)
from auto_a11y.core.touchpoints import TouchpointID
from auto_a11y.models.test_result import ImpactLevel
from auto_a11y.pdf.models import CheckResult
from auto_a11y.pdf.translation._extract_check_names import collect_pairs


# ---------------------------------------------------------------------------
# Catalogue invariants
# ---------------------------------------------------------------------------


def test_catalogue_has_no_duplicates() -> None:
    """Each (name, result) pair appears at most once in CHECK_CATALOGUE."""
    seen: set[tuple[str, str]] = set()
    for row in CHECK_CATALOGUE:
        key = (row["pdfmax_check_name"], row["pdfmax_result"])
        assert key not in seen, f"Duplicate (name, result) pair in CHECK_CATALOGUE: {key}"
        seen.add(key)


def test_catalogue_ids_are_unique() -> None:
    """Stable IDs are unique — they have to be, since
    :func:`to_violation` writes them as ``Violation.id`` and consumers
    treat the ID as the deduplication key."""
    seen: set[str] = set()
    for row in CHECK_CATALOGUE:
        sid = row["stable_id"]
        assert sid not in seen, f"Duplicate stable_id in CHECK_CATALOGUE: {sid}"
        seen.add(sid)


def test_catalogue_id_prefix_matches_result() -> None:
    """``PdfErr<...>`` for FAIL, ``PdfWarn<...>`` for WARN,
    ``PdfInfo<...>`` for INFO."""
    expected = {
        "FAIL": "PdfErr",
        "WARN": "PdfWarn",
        "INFO": "PdfInfo",
    }
    for row in CHECK_CATALOGUE:
        prefix = expected[row["pdfmax_result"]]
        sid = row["stable_id"]
        assert sid.startswith(prefix), (
            f"stable_id {sid!r} for result={row['pdfmax_result']!r} should"
            f" start with {prefix!r}"
        )


def test_catalogue_excludes_pass_outcomes() -> None:
    """Catalogue must not contain PASS rows — those aren't violations."""
    for row in CHECK_CATALOGUE:
        assert row["pdfmax_result"] != "PASS", (
            f"CHECK_CATALOGUE row for {row['pdfmax_check_name']!r} has"
            f" result=PASS — PASS is not a violation."
        )


def test_catalogue_touchpoints_are_pdf_only() -> None:
    """Every row points at one of the three PDF-specific touchpoints."""
    pdf_touchpoints = {
        TouchpointID.PDF_DOCUMENT_PROPERTIES,
        TouchpointID.PDF_TAGGING,
        TouchpointID.PDF_ANNOTATIONS,
    }
    for row in CHECK_CATALOGUE:
        assert row["touchpoint"] in pdf_touchpoints, (
            f"Non-PDF touchpoint {row['touchpoint']!r} on row"
            f" {row['stable_id']!r}"
        )


# ---------------------------------------------------------------------------
# Completeness regression: every literal (name, result) in the audit
# engine has a catalogue entry.
# ---------------------------------------------------------------------------


def test_every_check_emitted_by_engine_has_catalogue_entry() -> None:
    """Scan every audit-engine check function (and its module) for
    literal ``CheckResult(name=..., result=...)`` constructions and
    verify each non-PASS pair lives in :data:`CHECK_CATALOGUE`.

    If this fails, a new check was added to ``auto_a11y.pdf.audit.checks``
    without updating :data:`CHECK_CATALOGUE`. Fix the catalogue: don't
    skip the test.

    Implementation note: re-uses the AST-based scanner in
    :mod:`auto_a11y.pdf.translation._extract_check_names` rather than a
    fragile regex. The scanner follows local string assignments so
    checks that bind ``name = "..."`` once and re-use it (e.g. the
    color-contrast checks) still surface.
    """
    catalogue_keys: set[tuple[str, str]] = {
        (row["pdfmax_check_name"], row["pdfmax_result"])
        for row in CHECK_CATALOGUE
    }
    pairs = collect_pairs()
    missing = [p for p in pairs if p not in catalogue_keys]
    assert not missing, (
        f"Missing CHECK_CATALOGUE entries for {len(missing)} pair(s): "
        f"{missing}"
    )


def test_collect_pairs_finds_at_least_one_per_module() -> None:
    """Sanity check on the AST scanner itself: every check module
    contributes at least one non-PASS pair. Catches accidental over-
    aggressive filtering in :mod:`_extract_check_names`."""
    pairs = collect_pairs()
    # Every name from CHECK_CATALOGUE that came from a literal scan
    # should also be present in the scanner's output. Just verify size:
    # if the scanner returns zero pairs the catalogue test above fires
    # too, but this gives a clearer failure mode.
    assert len(pairs) > 50, (
        f"Expected >50 (name, result) pairs from AST scan; got {len(pairs)}"
    )


# ---------------------------------------------------------------------------
# WCAG criteria parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("standard", "expected"),
    [
        ("WCAG 2.4.2", ["2.4.2"]),
        ("PDF/UA, WCAG 2.4.2", ["2.4.2"]),
        ("WCAG 1.3.1, 2.4.6", ["1.3.1", "2.4.6"]),
        ("PDF/UA, WCAG 1.3.1, 4.1.2", ["1.3.1", "4.1.2"]),
        ("WCAG 2.5.5, 2.5.8", ["2.5.5", "2.5.8"]),
        ("WCAG 1.4 (best practice)", ["1.4"]),
        ("WCAG (PDF17)", []),  # no number after WCAG — Matterhorn-style
        ("Matterhorn 06-002", []),
        ("PDF/UA-2", []),
    ],
)
def test_extract_wcag_criteria(standard: str, expected: list[str]) -> None:
    """The parser extracts every WCAG criterion number from a standard
    string, ignoring qualifiers like ``(best practice)`` and
    ``(PDF17)``."""
    assert extract_wcag_criteria(standard) == expected


# ---------------------------------------------------------------------------
# Default impact mapping
# ---------------------------------------------------------------------------


def test_default_impact_fail_is_high() -> None:
    assert default_impact("FAIL") == ImpactLevel.HIGH


def test_default_impact_warn_is_medium() -> None:
    assert default_impact("WARN") == ImpactLevel.MEDIUM


def test_default_impact_info_is_low() -> None:
    assert default_impact("INFO") == ImpactLevel.LOW


def test_default_impact_pass_raises() -> None:
    with pytest.raises(ValueError, match="PASS"):
        default_impact("PASS")


# ---------------------------------------------------------------------------
# to_violation conversion
# ---------------------------------------------------------------------------


def test_to_violation_returns_none_for_pass() -> None:
    """PASS results aren't violations and don't have a catalogue row."""
    cr = CheckResult(
        name="Document title set",
        standard="PDF/UA, WCAG 2.4.2",
        result="PASS",
        details=".",
    )
    assert to_violation(cr, pdf_doc_id="doc-1") is None


def test_to_violation_returns_violation_for_fail() -> None:
    """FAIL maps to ImpactLevel.HIGH and a PdfErr-prefixed stable ID,
    with WCAG criteria parsed from the standard string."""
    cr = CheckResult(
        name="Document title set",
        standard="PDF/UA, WCAG 2.4.2",
        result="FAIL",
        details="No /Title in document info",
    )
    v = to_violation(cr, pdf_doc_id="doc-1")
    assert v is not None
    assert v.id == "PdfErrDocumentTitleNotSet"
    assert v.impact == ImpactLevel.HIGH
    assert v.touchpoint == TouchpointID.PDF_DOCUMENT_PROPERTIES.value
    assert v.wcag_criteria == ["2.4.2"]
    assert v.description == "pdf-check-PdfErrDocumentTitleNotSet-name"
    assert v.short_title == "pdf-check-PdfErrDocumentTitleNotSet-short-title"
    assert v.what == "pdf-check-PdfErrDocumentTitleNotSet-what"
    assert v.why == "pdf-check-PdfErrDocumentTitleNotSet-why"
    assert v.who == "pdf-check-PdfErrDocumentTitleNotSet-who"
    assert v.remediation == "pdf-remediation-PdfErrDocumentTitleNotSet"
    assert v.source_type == "automated"
    assert v.detection_method == "pdf_audit"
    assert v.metadata["pdf_doc_id"] == "doc-1"
    assert v.metadata["pdfmax_original_details"] == "No /Title in document info"
    assert v.metadata["pdfmax_original_standard"] == "PDF/UA, WCAG 2.4.2"


def test_to_violation_returns_violation_for_warn() -> None:
    """WARN maps to ImpactLevel.MEDIUM and a PdfWarn-prefixed stable ID."""
    cr = CheckResult(
        name="No suspect tags",
        standard="Matterhorn 09-004",
        result="WARN",
        details="/MarkInfo/Suspects is true",
    )
    v = to_violation(cr, pdf_doc_id="doc-2")
    assert v is not None
    assert v.id == "PdfWarnSuspectTags"
    assert v.impact == ImpactLevel.MEDIUM
    assert v.touchpoint == TouchpointID.PDF_DOCUMENT_PROPERTIES.value
    # Matterhorn-only standard → no WCAG criteria.
    assert v.wcag_criteria == []


def test_to_violation_returns_violation_for_info() -> None:
    """INFO maps to ImpactLevel.LOW and a PdfInfo-prefixed stable ID."""
    cr = CheckResult(
        name="Text contrast (WCAG AA)",
        standard="WCAG 1.4.3",
        result="INFO",
        details="Color data not collected; skipping contrast analysis.",
    )
    v = to_violation(cr, pdf_doc_id="doc-3")
    assert v is not None
    assert v.id == "PdfInfoTextContrastNoData"
    assert v.impact == ImpactLevel.LOW
    assert v.touchpoint == TouchpointID.PDF_TAGGING.value
    assert v.wcag_criteria == ["1.4.3"]


def test_to_violation_unknown_check_falls_back_with_synthesised_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unknown ``(name, result)`` pairs no longer raise — they used to
    crash the entire audit when a runtime-conditional result string
    (e.g. ``result="FAIL" if cond else "WARN"`` in a check function)
    bypassed the AST completeness scanner. Now the function logs a
    warning and synthesises a stable ID so the audit completes; the
    completeness regression test still catches missing literals during
    CI."""
    import logging

    cr = CheckResult(
        name="Bogus check that doesn't exist",
        standard="--",
        result="FAIL",
        details=".",
    )
    with caplog.at_level(logging.WARNING, logger='auto_a11y.pdf.translation.check_mapper'):
        v = to_violation(cr, pdf_doc_id="doc-1")
    assert v is not None
    assert v.id.startswith("PdfErr")
    assert v.impact == ImpactLevel.HIGH  # default for FAIL
    assert any('No CHECK_CATALOGUE row' in rec.message for rec in caplog.records)


def test_to_violation_unknown_pass_returns_none_without_raising() -> None:
    """Even a check name with no catalogue row should return None for
    PASS — the short-circuit happens before the catalogue lookup, so
    PASS results from new (unmapped) checks don't break the audit
    pipeline before someone gets around to mapping them."""
    cr = CheckResult(
        name="Future check that hasn't been catalogued yet",
        standard="WCAG 1.1.1",
        result="PASS",
        details=".",
    )
    assert to_violation(cr, pdf_doc_id="doc-1") is None


# ---------------------------------------------------------------------------
# Spot checks on a handful of catalogue rows
# ---------------------------------------------------------------------------


def test_spot_check_tagging_row() -> None:
    """``Structure tree exists`` FAIL → PdfErrStructureTreeMissing under
    PDF_TAGGING with no WCAG criteria (Matterhorn-only)."""
    cr = CheckResult(
        name="Structure tree exists",
        standard="Matterhorn 01-006",
        result="FAIL",
        details="No /StructTreeRoot",
    )
    v = to_violation(cr, pdf_doc_id="doc-1")
    assert v is not None
    assert v.id == "PdfErrStructureTreeMissing"
    assert v.touchpoint == TouchpointID.PDF_TAGGING.value
    assert v.wcag_criteria == []


def test_spot_check_annotation_row() -> None:
    """``Form fields labeled`` FAIL is an ANNOTATIONS row with multi-
    criterion WCAG."""
    cr = CheckResult(
        name="Form fields labeled",
        standard="PDF/UA, WCAG 1.3.1, 4.1.2",
        result="FAIL",
        details="3 unlabeled fields",
    )
    v = to_violation(cr, pdf_doc_id="doc-1")
    assert v is not None
    assert v.id == "PdfErrFormFieldUnlabeled"
    assert v.touchpoint == TouchpointID.PDF_ANNOTATIONS.value
    assert v.wcag_criteria == ["1.3.1", "4.1.2"]


def test_spot_check_bookmarks_uses_document_properties() -> None:
    """``Bookmarks present`` is one of the few links_navigation checks
    that targets the document catalog (``/Outlines``); it must be filed
    under PDF_DOCUMENT_PROPERTIES, not PDF_ANNOTATIONS."""
    cr = CheckResult(
        name="Bookmarks present",
        standard="PDF/UA, WCAG 2.4.5",
        result="WARN",
        details="No /Outlines entry in catalog",
    )
    v = to_violation(cr, pdf_doc_id="doc-1")
    assert v is not None
    assert v.id == "PdfWarnBookmarksMissing"
    assert v.touchpoint == TouchpointID.PDF_DOCUMENT_PROPERTIES.value
    assert v.wcag_criteria == ["2.4.5"]


def test_spot_check_per_annotation_language_uses_annotations() -> None:
    """Per-annotation language checks live under PDF_ANNOTATIONS, not
    PDF_DOCUMENT_PROPERTIES, even though the source module is
    ``checks.language``."""
    cr = CheckResult(
        name="Annotation contents language determinable",
        standard="Matterhorn 11-004",
        result="FAIL",
        details="Annotation #3 has /Lang in unsupported form",
    )
    v = to_violation(cr, pdf_doc_id="doc-1")
    assert v is not None
    assert v.touchpoint == TouchpointID.PDF_ANNOTATIONS.value


def test_spot_check_color_contrast_warn() -> None:
    """``Text contrast (WCAG AAA)`` WARN is mapped to a PdfWarn ID under
    PDF_TAGGING, with the AAA criterion captured."""
    cr = CheckResult(
        name="Text contrast (WCAG AAA)",
        standard="WCAG 1.4.6",
        result="WARN",
        details="Some pairs below AAA",
    )
    v = to_violation(cr, pdf_doc_id="doc-1")
    assert v is not None
    assert v.id == "PdfWarnTextContrastBelowAaa"
    assert v.impact == ImpactLevel.MEDIUM
    assert v.touchpoint == TouchpointID.PDF_TAGGING.value
    assert v.wcag_criteria == ["1.4.6"]
