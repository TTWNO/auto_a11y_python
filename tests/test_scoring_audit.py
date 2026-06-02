"""
Regression tests for scoring/wcag_parser audit fixes.

Covers:
- Bug A: aggregate compliance must intersect failed criteria with the applicable set.
- Bug B: extract_criterion_number regex must handle 3-part and 4-part inputs sanely.
- Bug D: get_criteria_for_level must normalize/validate target_level instead of
  raising a bare ValueError for unexpected input.
"""
from __future__ import annotations

import pytest

from auto_a11y.models import Recording, RecordingIssue, ImpactLevel
from auto_a11y.models.recording import WCAGReference
from auto_a11y.scoring import (
    ManualAccessibilityScorer,
    calculate_project_manual_scores,
)
from auto_a11y.wcag_parser import get_wcag_parser, get_scope_mapper


def _empty_scope() -> dict[str, bool]:
    """Scope with every known category explicitly NOT tested.

    With this scope, scope-specific criteria such as 3.3.1 (error-identification)
    are removed from the applicable set, while general criteria such as
    1.4.3 (contrast) remain applicable.
    """
    mapper = get_scope_mapper()
    return {key: False for key in mapper.get_scope_categories()}


def _make_recording(rec_id: str, scope: dict[str, bool]) -> Recording:
    return Recording(recording_id=rec_id, title=rec_id, testing_scope=scope)


def _issue_for(rec_id: str, criteria: str) -> RecordingIssue:
    return RecordingIssue(
        recording_id=rec_id,
        title=f"issue {criteria}",
        impact=ImpactLevel.HIGH,
        wcag=[WCAGReference(criteria=criteria, level="AA")],
    )


# ---------------------------------------------------------------------------
# Bug A: aggregate compliance intersects failed criteria with applicable set
# ---------------------------------------------------------------------------

def test_aggregate_failed_criterion_outside_applicable_not_counted() -> None:
    """A failed criterion that is NOT in the applicable set must not be counted
    as failed in the aggregate path, and passed must never exceed total."""
    scope = _empty_scope()
    rec = _make_recording("REC-A", scope)

    # 3.3.1 is removed from the applicable set under an empty scope -> not applicable.
    issue = _issue_for("REC-A", "3.3.1")

    scores = calculate_project_manual_scores(
        recordings=[rec],
        all_issues={"REC-A": [issue]},
        target_level="AA",
    )

    assert scores.failed_criteria == 0
    assert scores.passed_criteria == scores.total_applicable_criteria
    assert scores.passed_criteria <= scores.total_applicable_criteria
    assert scores.compliance_score == 100.0


def test_aggregate_failed_criterion_inside_applicable_counted() -> None:
    """A failed criterion that IS applicable must be counted as failed."""
    scope = _empty_scope()
    rec = _make_recording("REC-A", scope)

    # 1.4.3 (contrast) is a general criterion that remains applicable.
    issue = _issue_for("REC-A", "1.4.3")

    scores = calculate_project_manual_scores(
        recordings=[rec],
        all_issues={"REC-A": [issue]},
        target_level="AA",
    )

    assert scores.failed_criteria == 1
    assert scores.passed_criteria == scores.total_applicable_criteria - 1


def test_aggregate_passed_never_negative_with_many_out_of_scope_failures() -> None:
    """Even with several out-of-scope failed criteria, passed never goes negative
    and never exceeds the applicable total."""
    scope = _empty_scope()
    rec = _make_recording("REC-A", scope)
    issues = [
        _issue_for("REC-A", "3.3.1"),  # not applicable
        _issue_for("REC-A", "3.3.2"),  # not applicable
        _issue_for("REC-A", "1.2.2"),  # not applicable (video)
    ]

    scores = calculate_project_manual_scores(
        recordings=[rec],
        all_issues={"REC-A": issues},
        target_level="AA",
    )

    assert scores.failed_criteria == 0
    assert 0 <= scores.passed_criteria <= scores.total_applicable_criteria


def test_aggregate_matches_single_recording_compliance() -> None:
    """For a single recording, the aggregate compliance should match the
    per-recording compliance computation."""
    scope = _empty_scope()
    rec = _make_recording("REC-A", scope)
    issues = [_issue_for("REC-A", "1.4.3"), _issue_for("REC-A", "3.3.1")]

    scorer = ManualAccessibilityScorer()
    single_score, single_total, single_failed, single_passed = (
        scorer.calculate_compliance_score(scope, issues, "AA")
    )

    agg = calculate_project_manual_scores(
        recordings=[rec],
        all_issues={"REC-A": issues},
        target_level="AA",
    )

    assert agg.total_applicable_criteria == single_total
    assert agg.failed_criteria == single_failed
    assert agg.passed_criteria == single_passed
    assert agg.compliance_score == single_score


# ---------------------------------------------------------------------------
# Bug B: extract_criterion_number regex
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1.1.1", "1.1.1"),
        ("1.1.1 Non-text Content", "1.1.1"),
        ("WCAG 2.1.1", "2.1.1"),
        ("2.1.1.1", "2.1.1"),  # 4-part: take the leading 3-part criterion
        ("", None),
        ("no number here", None),
    ],
)
def test_extract_criterion_number(raw: str, expected: str | None) -> None:
    scorer = ManualAccessibilityScorer()
    assert scorer.extract_criterion_number(raw) == expected


# ---------------------------------------------------------------------------
# Bug D: get_criteria_for_level normalizes / validates target_level
# ---------------------------------------------------------------------------

def test_get_criteria_for_level_lowercase_normalized() -> None:
    parser = get_wcag_parser()
    canonical = parser.get_criteria_for_level("AA")
    normalized = parser.get_criteria_for_level("aa")
    assert [c.id for c in normalized] == [c.id for c in canonical]


def test_get_criteria_for_level_whitespace_normalized() -> None:
    parser = get_wcag_parser()
    canonical = parser.get_criteria_for_level("AAA")
    normalized = parser.get_criteria_for_level("  aaa  ")
    assert [c.id for c in normalized] == [c.id for c in canonical]


def test_get_criteria_for_level_invalid_raises_clear_error() -> None:
    parser = get_wcag_parser()
    with pytest.raises(ValueError) as exc_info:
        parser.get_criteria_for_level("ZZ")
    # Error message should be specific about the invalid level.
    assert "ZZ" in str(exc_info.value) or "level" in str(exc_info.value).lower()


def test_get_criteria_for_level_empty_raises() -> None:
    parser = get_wcag_parser()
    with pytest.raises(ValueError):
        parser.get_criteria_for_level("")
