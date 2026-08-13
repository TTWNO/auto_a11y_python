"""Tests for AI-derived check verdicts and how they join the check list.

Two rules are load-bearing here and both exist to keep a report honest.
A pass that could not run degrades to ``INFO``, never ``PASS`` — a check
nobody performed has not found the document compliant, and scoring it as
a pass inflates exactly the documents nobody looked at. And a verdict
that shares a name with a deterministic check *replaces* it rather than
joining it, so a reader is never shown two answers to one criterion and
left to guess which counts.
"""
from __future__ import annotations

from auto_a11y.pdf.audit.ai.verdicts import derive_check_results
from auto_a11y.pdf.audit.pipeline import merge_ai_checks
from auto_a11y.pdf.models import CheckOutcome, CheckResult


def _by_name(results: list[CheckResult]) -> dict[str, CheckResult]:
    return {r.name: r for r in results}


# ---------------------------------------------------------------------------
# Verdicts that exist only when AI runs
# ---------------------------------------------------------------------------


def test_an_absent_pass_degrades_to_info_not_pass() -> None:
    results = _by_name(derive_check_results({}))

    for name in (
        "Alt text adequacy",
        "Images of text have matching alt text",
        "Use of color not sole indicator",
    ):
        assert results[name].result == "INFO", name


def test_no_verdict_at_all_for_the_two_superseding_checks_when_absent() -> None:
    """Silence leaves the deterministic verdict standing, which is correct."""
    names = {r.name for r in derive_check_results({})}

    assert "Non-text contrast sufficient" not in names
    assert "Required fields visually indicated" not in names


# ---------------------------------------------------------------------------
# Non-text contrast
# ---------------------------------------------------------------------------


def _contrast(severity: str) -> dict[str, object]:
    return {
        "findings": [{
            "category": "form_field_border",
            "severity": severity,
            "page": 1,
            "description": "d",
            "recommendation": "r",
        }],
        "overall_assessment": "a",
        "pass": severity == "pass",
    }


def test_contrast_failures_produce_a_fail() -> None:
    results = _by_name(
        derive_check_results({"non_text_contrast_ai": _contrast("fail")})
    )
    verdict = results["Non-text contrast sufficient"]
    assert verdict.result == "FAIL"
    assert "1 non-text contrast failure(s)" in verdict.details


def test_contrast_warnings_produce_a_warn() -> None:
    results = _by_name(
        derive_check_results({"non_text_contrast_ai": _contrast("warning")})
    )
    assert results["Non-text contrast sufficient"].result == "WARN"


def test_contrast_passes_when_the_model_says_so_and_flags_nothing() -> None:
    payload: dict[str, object] = {
        "findings": [], "overall_assessment": "a", "pass": True,
    }
    results = _by_name(derive_check_results({"non_text_contrast_ai": payload}))
    assert results["Non-text contrast sufficient"].result == "PASS"


def test_contrast_declines_to_supersede_when_the_model_neither_passes_nor_flags() -> None:
    payload: dict[str, object] = {
        "findings": [], "overall_assessment": "a", "pass": False,
    }
    names = {r.name for r in derive_check_results({"non_text_contrast_ai": payload})}
    assert "Non-text contrast sufficient" not in names


# ---------------------------------------------------------------------------
# Required-field indicators
# ---------------------------------------------------------------------------


def _required(
    *,
    passed: bool,
    missing: int = 0,
    visual_only: int = 0,
) -> dict[str, object]:
    return {
        "has_legend": False,
        "legend_text": "",
        "field_results": [
            {
                "field_name": f"field{i}",
                "has_visual_indicator": i >= missing,
                "indicator_type": "none" if i < missing else "asterisk",
                "indicator_description": "d",
            }
            for i in range(3)
        ],
        "visual_only_required": [
            {"description": f"a field on page {i}", "page": i}
            for i in range(visual_only)
        ],
        "overall_assessment": "a",
        "pass": passed,
    }


def test_required_indicators_pass_reports_the_field_count() -> None:
    results = _by_name(
        derive_check_results({"required_indicators": _required(passed=True)})
    )
    verdict = results["Required fields visually indicated"]
    assert verdict.result == "PASS"
    assert "all 3 required field(s)" in verdict.details


def test_required_indicators_pass_quotes_a_legend_when_one_was_seen() -> None:
    payload = _required(passed=True)
    payload["legend_text"] = "Fields marked with * are required"
    results = _by_name(derive_check_results({"required_indicators": payload}))
    assert "Fields marked with *" in results[
        "Required fields visually indicated"
    ].details


def test_fields_without_a_visible_indicator_fail() -> None:
    results = _by_name(
        derive_check_results(
            {"required_indicators": _required(passed=False, missing=2)}
        )
    )
    verdict = results["Required fields visually indicated"]
    assert verdict.result == "FAIL"
    assert "2 required field(s) lack visual indicators" in verdict.details


def test_visual_only_required_fields_warn_rather_than_fail() -> None:
    """Sighted users are served; assistive technology is not told."""
    results = _by_name(
        derive_check_results(
            {"required_indicators": _required(passed=False, visual_only=1)}
        )
    )
    verdict = results["Required fields visually indicated"]
    assert verdict.result == "WARN"
    assert "without the semantic /Ff Required flag" in verdict.details


def test_malformed_ai_payloads_do_not_raise() -> None:
    """A schema is enforced at the call, but a section is still untrusted."""
    payload: dict[str, object] = {
        "findings": "not a list", "field_results": 7, "pass": None,
    }
    derive_check_results({
        "non_text_contrast_ai": payload, "required_indicators": payload,
    })


# ---------------------------------------------------------------------------
# Merging into the check list
# ---------------------------------------------------------------------------


def _result(name: str, outcome: CheckOutcome) -> CheckResult:
    return CheckResult(
        name=name, standard="WCAG 1.4.11", result=outcome, details=outcome,
    )


def test_a_same_named_verdict_replaces_rather_than_appends() -> None:
    checks = [_result("A", "NA"), _result("Non-text contrast sufficient", "NA")]

    merge_ai_checks(checks, [_result("Non-text contrast sufficient", "FAIL")])

    assert len(checks) == 2
    assert checks[1].result == "FAIL"


def test_replacement_keeps_the_check_in_its_original_position() -> None:
    checks = [
        _result("Non-text contrast sufficient", "NA"),
        _result("Z", "PASS"),
    ]

    merge_ai_checks(checks, [_result("Non-text contrast sufficient", "PASS")])

    assert [c.name for c in checks] == ["Non-text contrast sufficient", "Z"]


def test_a_new_verdict_is_appended() -> None:
    checks = [_result("A", "PASS")]

    merge_ai_checks(checks, [_result("Alt text adequacy", "WARN")])

    assert [c.name for c in checks] == ["A", "Alt text adequacy"]


def test_two_new_verdicts_of_the_same_name_do_not_duplicate() -> None:
    checks: list[CheckResult] = []

    merge_ai_checks(checks, [_result("B", "WARN"), _result("B", "FAIL")])

    assert len(checks) == 1
    assert checks[0].result == "FAIL"
