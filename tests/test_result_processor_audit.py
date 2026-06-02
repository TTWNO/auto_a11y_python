"""
Audit-driven regression tests for ResultProcessor.

Covers two bugs found in a code audit:

Bug A (HIGH): per-test result dicts emit dedicated ``discovery`` and ``info``
arrays (e.g. ``test_page.py`` appends ``DiscoResponsiveBreakpoints`` to
``results['discovery']``). ``process_test_results`` used to read only
``errors``/``warnings``/``passes``, so those items were silently dropped.

Bug B (MEDIUM): the "AI analysis ran but found no issues" passing-check
branch was nested inside ``if ai_findings:`` so ``not ai_findings`` could
never be true. AI-clean pages were therefore under-counted in the
applicability-aware score.
"""

from __future__ import annotations

from typing import Any

# ``auto_a11y.core`` must be imported before ``auto_a11y.testing`` because
# ``core`` triggers a chain (``website_manager`` → ``testing_job`` →
# ``auto_a11y.testing``) that re-enters the ``testing`` package mid-init when
# ``testing`` is loaded first. Loading ``core`` up front lets the chain fully
# resolve. The ``import as _``/``del`` pattern keeps strict type-checkers from
# flagging the otherwise-unused import.
import auto_a11y.core as _core_preload

del _core_preload

from auto_a11y.models import ImpactLevel, Violation
from auto_a11y.testing.result_processor import ResultProcessor


def _disco_item() -> dict[str, Any]:
    return {
        'err': 'DiscoResponsiveBreakpoints',
        'type': 'disco',
        'cat': 'page',
        'element': 'html',
        'xpath': '/html',
        'html': '<html>',
        'description': 'Page defines 2 responsive breakpoint(s): 768, 1024px',
    }


def _info_item() -> dict[str, Any]:
    return {
        'err': 'InfoSomeNotice',
        'type': 'info',
        'cat': 'page',
        'element': 'body',
        'xpath': '/html/body',
        'html': '<body>',
        'description': 'An informational notice about the page.',
    }


def test_bug_a_discovery_and_info_arrays_are_routed() -> None:
    """Discovery and info items placed in their dedicated arrays must reach
    the produced TestResult."""
    processor = ResultProcessor()

    raw_results: dict[str, dict[str, Any]] = {
        'page': {
            'applicable': True,
            'errors': [],
            'warnings': [],
            'passes': [],
            'discovery': [_disco_item()],
            'info': [_info_item()],
        }
    }

    result = processor.process_test_results(page_id='p1', raw_results=raw_results)

    disco_codes = [v.id for v in result.discovery]
    info_codes = [v.id for v in result.info]

    assert any('DiscoResponsiveBreakpoints' in code for code in disco_codes), (
        f"discovery array dropped; got discovery={disco_codes}"
    )
    assert any('InfoSomeNotice' in code for code in info_codes), (
        f"info array dropped; got info={info_codes}"
    )
    assert len(result.discovery) == 1
    assert len(result.info) == 1


def test_bug_a_does_not_double_count_existing_error_paths() -> None:
    """Errors still land as violations; discovery/info routing is additive."""
    processor = ResultProcessor()

    raw_results: dict[str, dict[str, Any]] = {
        'page': {
            'applicable': True,
            'errors': [
                {
                    'err': 'ErrNoPageTitle',
                    'type': 'err',
                    'cat': 'page',
                    'element': 'head',
                    'xpath': '/html/head',
                    'html': '<head>',
                    'description': 'Page is missing a title element',
                }
            ],
            'warnings': [],
            'passes': [],
            'discovery': [_disco_item()],
            'info': [_info_item()],
        }
    }

    result = processor.process_test_results(page_id='p1', raw_results=raw_results)

    assert len(result.violations) == 1
    assert len(result.discovery) == 1
    assert len(result.info) == 1


def test_bug_b_ai_clean_page_records_passing_check() -> None:
    """When AI analysis ran but produced zero findings, a passing check must
    be recorded and the applicability counters incremented."""
    processor = ResultProcessor()

    result = processor.process_test_results(
        page_id='p1',
        raw_results={},
        ai_findings=[],
        ai_analysis_results={'headings': {}, 'language': {}},
    )

    assert result.metadata['passed_checks'] == 2, (
        f"expected 2 passing AI checks, got {result.metadata['passed_checks']}"
    )
    # applicable_checks in metadata is derived as passed+failed
    assert result.metadata['applicable_checks'] == 2
    assert result.metadata['failed_checks'] == 0


def test_bug_b_ai_with_findings_still_counts_failures() -> None:
    """A page where AI found an issue must not be treated as clean."""
    processor = ResultProcessor()

    finding = Violation(
        id='AI_ErrSomething',
        impact=ImpactLevel.HIGH,
        touchpoint='headings',
        description='AI detected a problem',
        metadata={'ai_analysis_type': 'headings'},
    )

    result = processor.process_test_results(
        page_id='p1',
        raw_results={},
        ai_findings=[finding],
        ai_analysis_results={'headings': {}},
    )

    assert result.metadata['failed_checks'] == 1
    assert result.metadata['passed_checks'] == 0
    assert len(result.violations) == 1
