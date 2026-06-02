"""Regression guards for code-audit fixes in auto_a11y/testing/test_runner.py.

These tests cover three audit findings:

  A. The script-violation log line must reference ``Violation.description``
     (the real field), not a non-existent ``.message`` attribute.
  B. The ``DOCUMENT_METADATA`` fallback (in an ``except`` block) must emit
     valid JavaScript ``window.DOCUMENT_METADATA = {};`` -- the original used
     a doubled-brace ``{{}}`` literal in a *non*-f-string, which reaches the
     browser verbatim and is a JS syntax error.
  C. Script violations appended after ``process_test_results`` should also be
     reflected in the per-touchpoint ``metadata['checks']`` summary so the
     summary stays consistent with ``violation_count``.

The log line and the fallback live deep inside a long async method, so the
pragmatic, fast regression guards here combine:

  * a structural assertion on the ``Violation`` dataclass, and
  * a source-level assertion that ``test_runner.py`` references the correct
    attribute / emits the correct JS literal.
"""

from __future__ import annotations

import inspect
from pathlib import Path

# ``auto_a11y.core`` must load before ``auto_a11y.testing`` to break the
# package init's circular import (testing -> core -> testing). The
# ``import as _``/``del`` pattern keeps strict type-checkers from flagging
# the otherwise-unused import.
import auto_a11y.core as _core_preload
del _core_preload

import auto_a11y.testing.test_runner as test_runner_module
from auto_a11y.models import Violation
from auto_a11y.models.test_result import ImpactLevel


TEST_RUNNER_SOURCE = inspect.getsource(test_runner_module)


# ---------------------------------------------------------------------------
# Bug A: Violation has .description, not .message
# ---------------------------------------------------------------------------

def _make_violation() -> Violation:
    return Violation(
        id="Test_Err_Example",
        impact=ImpactLevel.HIGH,
        touchpoint="Forms",
        description="A human-readable description of the violation.",
    )


def test_violation_has_description_not_message() -> None:
    """The Violation dataclass exposes .description (used by the log line)."""
    violation = _make_violation()
    # The attribute the fixed log line uses must exist and be accessible.
    assert violation.description == "A human-readable description of the violation."
    # The buggy attribute must NOT exist -- accessing it would raise.
    assert not hasattr(violation, "message")


def test_test_runner_log_line_uses_description() -> None:
    """The script-violation log line must reference .description, not .message."""
    # The buggy line accessed result['violation'].message
    assert "result['violation'].message" not in TEST_RUNNER_SOURCE
    assert '"violation"].message' not in TEST_RUNNER_SOURCE
    # The fixed line references the real attribute.
    assert "Script reported violation" in TEST_RUNNER_SOURCE
    assert ".description" in TEST_RUNNER_SOURCE


# ---------------------------------------------------------------------------
# Bug B: DOCUMENT_METADATA fallback must emit valid JS (single braces)
# ---------------------------------------------------------------------------

def test_document_metadata_fallback_is_valid_js() -> None:
    """The fallback must emit `window.DOCUMENT_METADATA = {};` (single braces)."""
    source_path = Path(test_runner_module.__file__)
    source = source_path.read_text(encoding="utf-8")
    # The buggy doubled-brace literal (non-f-string) must be gone.
    assert "window.DOCUMENT_METADATA = {{}};" not in source
    # The valid single-brace JS must be present in the fallback.
    assert "window.DOCUMENT_METADATA = {};" in source


# ---------------------------------------------------------------------------
# Bug C: script violations reflected in metadata['checks'] touchpoint summary
# ---------------------------------------------------------------------------

def test_script_violations_extend_touchpoint_summary() -> None:
    """When script violations are appended, the touchpoint summary must reflect them.

    This exercises the test_runner-side helper that reconciles
    ``metadata['checks']`` after script violations are appended.
    """
    # A TestResult whose metadata already has a checks summary (as produced by
    # process_test_results) for an unrelated touchpoint.
    from auto_a11y.models import TestResult

    result = TestResult(
        page_id="page123",
        metadata={
            "checks": [
                {
                    "test_name": "Images",
                    "description": "Accessibility checks for images",
                    "wcag": ["1.1.1"],
                    "total": 1,
                    "passed": 0,
                    "failed": 1,
                    "violations": 1,
                    "warnings": 0,
                    "info": 0,
                    "discovery": 0,
                }
            ]
        },
    )

    script_violation = Violation(
        id="Forms_Err_ScriptDetected",
        impact=ImpactLevel.HIGH,
        touchpoint="Forms",
        description="Script-detected form violation.",
        wcag_criteria=["3.3.2"],
    )
    result.violations.append(script_violation)

    test_runner_module.merge_violations_into_checks(
        result.metadata, [script_violation]
    )

    checks = result.metadata["checks"]
    forms_check = next((c for c in checks if c["test_name"] == "Forms"), None)
    assert forms_check is not None, "Forms touchpoint should appear in checks summary"
    assert forms_check["violations"] == 1
    assert forms_check["failed"] == 1
    assert forms_check["total"] == 1
    assert "3.3.2" in forms_check["wcag"]

    # An existing touchpoint should be incremented, not duplicated.
    result2 = TestResult(
        page_id="page456",
        metadata={
            "checks": [
                {
                    "test_name": "Forms",
                    "description": "Accessibility checks for forms",
                    "wcag": ["3.3.1"],
                    "total": 1,
                    "passed": 0,
                    "failed": 1,
                    "violations": 1,
                    "warnings": 0,
                    "info": 0,
                    "discovery": 0,
                }
            ]
        },
    )
    test_runner_module.merge_violations_into_checks(
        result2.metadata, [script_violation]
    )
    forms_checks = [c for c in result2.metadata["checks"] if c["test_name"] == "Forms"]
    assert len(forms_checks) == 1, "should not duplicate the touchpoint row"
    assert forms_checks[0]["violations"] == 2
    assert forms_checks[0]["total"] == 2
    assert "3.3.1" in forms_checks[0]["wcag"]
    assert "3.3.2" in forms_checks[0]["wcag"]
