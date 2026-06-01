"""Tests for StaticHTMLReportGenerator site-wide average score calculation.

Regression test: zero-score pages (tested pages where every test failed — the
worst pages) must NOT be excluded from the site-wide average. Excluding them
biases the average upward and inflates the downstream compliance_level.
"""
from __future__ import annotations

import importlib
from typing import Any

from unittest.mock import MagicMock

# Import the web package first to break a collection-order-dependent circular
# import (auto_a11y.reporting -> report_generator -> auto_a11y.web.fluent ->
# auto_a11y.web.app -> ... -> auto_a11y.reporting). When this test file is the
# first to touch auto_a11y.reporting in a process it would otherwise fail to
# import; fully initialising web.app first completes the cycle.
importlib.import_module('auto_a11y.web.app')
from auto_a11y.reporting.static_html_generator import StaticHTMLReportGenerator


def _make_generator() -> StaticHTMLReportGenerator:
    gen = StaticHTMLReportGenerator.__new__(StaticHTMLReportGenerator)
    gen.db = MagicMock()
    gen.language = 'en'
    return gen


def _page(score: float, *, tested: bool, errors: int = 0) -> dict[str, Any]:
    """Build a minimal pages_data entry matching _collect_pages_data's shape."""
    return {
        'id': f'p{score}',
        'title': 'Page',
        'url': 'http://example.com',
        # Untested pages have test_date None; tested pages have a date string.
        'test_date': '2026-01-01 00:00:00' if tested else None,
        'score': score,
        'screenshot_path': None,
        'violations': [],
        'warnings': [],
        'informational': [],
        'discovery': [],
        'issues': {'errors': errors, 'warnings': 0, 'info': 0, 'discovery': 0},
    }


def test_zero_score_tested_pages_included_in_average() -> None:
    """A tested page scoring exactly 0 (worst page) must count toward the average."""
    gen = _make_generator()
    pages_data = [
        _page(0.0, tested=True, errors=10),
        _page(50.0, tested=True, errors=5),
        _page(100.0, tested=True, errors=0),
    ]

    summary = getattr(gen, '_generate_summary_stats')(pages_data)

    # (0 + 50 + 100) / 3 == 50.0, NOT (50 + 100) / 2 == 75.0
    assert summary['average_score'] == 50.0


def test_untested_pages_excluded_from_average() -> None:
    """Pages that were never tested (test_date None) must not dilute the average."""
    gen = _make_generator()
    pages_data = [
        _page(0.0, tested=False),  # never tested -> excluded
        _page(80.0, tested=True),
        _page(100.0, tested=True),
    ]

    summary = getattr(gen, '_generate_summary_stats')(pages_data)

    # (80 + 100) / 2 == 90.0; the untested 0 must not be counted.
    assert summary['average_score'] == 90.0


def test_no_tested_pages_yields_zero_average() -> None:
    """Empty / all-untested input must not raise ZeroDivisionError."""
    gen = _make_generator()
    pages_data = [_page(0.0, tested=False)]

    summary = getattr(gen, '_generate_summary_stats')(pages_data)

    assert summary['average_score'] == 0.0
