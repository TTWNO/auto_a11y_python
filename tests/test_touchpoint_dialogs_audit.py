"""Audit regression tests for the floating-dialogs touchpoint aggregation.

These tests target the pure aggregation step that combines the per-breakpoint
JS results produced by ``test_floating_dialogs``. The same dialog is reported
once per breakpoint by the in-browser script; the aggregation must deduplicate
warnings/passes (mirroring the existing error dedup) and must not multiply the
element tested/passed/failed counts by the number of breakpoints.

Bug A: warnings/passes were ``extend``-ed raw and counts summed raw across
        breakpoints, inflating everything by N (= breakpoint count).
Bug B: the check summary ``total`` was a hardcoded ``elements_tested * 3``,
        so ``passed + failed != total``.
"""
from __future__ import annotations

import importlib
import os
from typing import Any

os.environ.setdefault('RUN_AI_ANALYSIS', 'false')

# Importing the web package first establishes the module import order that
# avoids a pre-existing circular import between auto_a11y.testing and
# auto_a11y.web (testing/__init__ -> test_runner -> web.fluent -> ... ->
# testing_job -> auto_a11y.testing). In production the web app is imported
# first, so this mirrors the real load order. importlib is used so the
# side-effect import is not flagged as an unused import.
importlib.import_module('auto_a11y.web')

from auto_a11y.testing.touchpoint_tests.test_floating_dialogs import (
    aggregate_breakpoint_results,
)

# A per-breakpoint JS result is a heterogeneous mapping (the touchpoint result
# contract uses Any-valued dicts), so annotate locals with this alias.
BreakpointResult = dict[str, Any]


def _single_dialog_breakpoint_result() -> BreakpointResult:
    """One dialog that fails heading + close, passes obscuring, with 2 warnings.

    Represents the JS result the in-browser script returns for ONE breakpoint
    when there is exactly one dialog on the page.
    """
    xpath = '/html[1]/body[1]/div[1]'
    return {
        'applicable': True,
        'errors': [
            {
                'err': 'ErrModalNoHeading',
                'type': 'err',
                'xpath': xpath,
                'description': 'no heading',
            },
            {
                'err': 'ErrMissingCloseButton',
                'type': 'err',
                'xpath': xpath,
                'description': 'no close',
            },
        ],
        'warnings': [
            {
                'err': 'WarnMissingAriaModal',
                'type': 'warn',
                'xpath': xpath,
                'description': 'aria-modal',
            },
            {
                'err': 'WarnMissingAriaLabelledby',
                'type': 'warn',
                'xpath': xpath,
                'description': 'aria-labelledby',
            },
        ],
        'passes': [],
        'elements_tested': 1,
        'elements_passed': 1,  # obscuring check passed
        'elements_failed': 2,  # heading + close failed
    }


def _err_codes(issues: list[Any]) -> list[str]:
    return [str(issue['err']) for issue in issues]


def test_same_dialog_across_breakpoints_dedups_warnings() -> None:
    """The same dialog at 3 breakpoints must yield each warning ONCE."""
    per_bp: list[BreakpointResult] = [
        _single_dialog_breakpoint_result() for _ in range(3)
    ]

    agg = aggregate_breakpoint_results(per_bp)

    warnings: list[Any] = agg['warnings']
    # Two distinct warnings on one dialog -> exactly two, not 2 * 3.
    assert len(warnings) == 2, f"expected 2 deduped warnings, got {len(warnings)}"
    assert sorted(_err_codes(warnings)) == [
        'WarnMissingAriaLabelledby',
        'WarnMissingAriaModal',
    ]


def test_same_dialog_across_breakpoints_dedups_passes() -> None:
    """Passes must be deduped the same way as errors/warnings."""
    xpath = '/html[1]/body[1]/div[1]'
    bp: BreakpointResult = {
        'applicable': True,
        'errors': [],
        'warnings': [],
        'passes': [
            {'err': 'PassDialogHasHeading', 'type': 'pass', 'xpath': xpath},
        ],
        'elements_tested': 1,
        'elements_passed': 3,
        'elements_failed': 0,
    }
    per_bp: list[BreakpointResult] = [dict(bp) for _ in range(4)]

    agg = aggregate_breakpoint_results(per_bp)

    passes: list[Any] = agg['passes']
    assert len(passes) == 1, f"expected 1 deduped pass, got {len(passes)}"


def test_element_counts_not_multiplied_by_breakpoints() -> None:
    """One dialog tested at 3 breakpoints is still ONE tested element."""
    per_bp: list[BreakpointResult] = [
        _single_dialog_breakpoint_result() for _ in range(3)
    ]

    agg = aggregate_breakpoint_results(per_bp)

    assert agg['elements_tested'] == 1, agg['elements_tested']
    assert agg['elements_passed'] == 1, agg['elements_passed']
    assert agg['elements_failed'] == 2, agg['elements_failed']


def test_check_total_reconciles_with_passed_plus_failed() -> None:
    """Bug B: check summary total must equal passed + failed."""
    per_bp: list[BreakpointResult] = [
        _single_dialog_breakpoint_result() for _ in range(3)
    ]

    agg = aggregate_breakpoint_results(per_bp)

    checks: list[Any] = agg['checks']
    assert len(checks) == 1
    check: dict[str, Any] = checks[0]
    assert check['total'] == check['passed'] + check['failed'], check


def test_content_obscuring_kept_per_breakpoint() -> None:
    """ErrContentObscuring is breakpoint-specific and must NOT be deduped."""
    def bp(width: int) -> BreakpointResult:
        return {
            'applicable': True,
            'errors': [
                {
                    'err': 'ErrContentObscuring',
                    'type': 'err',
                    'xpath': '/html[1]/body[1]/div[1]',
                    'metadata': {'breakpoint': width},
                },
            ],
            'warnings': [],
            'passes': [],
            'elements_tested': 1,
            'elements_passed': 0,
            'elements_failed': 1,
        }

    agg = aggregate_breakpoint_results([bp(320), bp(768), bp(1200)])

    errors: list[Any] = agg['errors']
    obscuring = [code for code in _err_codes(errors) if code == 'ErrContentObscuring']
    assert len(obscuring) == 3, "obscuring errors are per-breakpoint"


def test_distinct_dialogs_counted_separately() -> None:
    """Two different dialogs (different xpaths) are two tested elements."""
    bp: BreakpointResult = {
        'applicable': True,
        'errors': [
            {'err': 'ErrMissingCloseButton', 'type': 'err', 'xpath': '/html[1]/body[1]/div[1]'},
            {'err': 'ErrMissingCloseButton', 'type': 'err', 'xpath': '/html[1]/body[1]/div[2]'},
        ],
        'warnings': [],
        'passes': [],
        'elements_tested': 2,
        'elements_passed': 0,
        'elements_failed': 2,
    }
    per_bp: list[BreakpointResult] = [dict(bp) for _ in range(3)]

    agg = aggregate_breakpoint_results(per_bp)

    assert agg['elements_tested'] == 2
    errors: list[Any] = agg['errors']
    close_errors = [code for code in _err_codes(errors) if code == 'ErrMissingCloseButton']
    assert len(close_errors) == 2


def test_not_applicable_when_no_dialogs() -> None:
    """No dialogs at any breakpoint -> not applicable, no checks."""
    bp: BreakpointResult = {
        'applicable': False,
        'not_applicable_reason': 'No visible dialogs found on the page',
        'errors': [],
        'warnings': [],
        'passes': [],
        'elements_tested': 0,
        'elements_passed': 0,
        'elements_failed': 0,
    }

    agg = aggregate_breakpoint_results([dict(bp), dict(bp)])

    assert agg['applicable'] is False
    assert agg['checks'] == []
    assert agg['not_applicable_reason'] == 'No visible dialogs found on the page'
