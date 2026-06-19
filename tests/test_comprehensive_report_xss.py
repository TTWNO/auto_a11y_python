"""Regression tests guarding against stored XSS in the comprehensive report.

``ComprehensiveReportGenerator`` is the production HTML generator for
website/project reports and the executive summary. It interpolates untrusted
data (page titles/urls, issue descriptions, xpath/element fragments,
project/website names) and AI-generated executive-summary text into HTML via
raw f-strings. Every dynamic value MUST be HTML-escaped so an injected
``<script>`` cannot execute or break out of an attribute.
"""
from __future__ import annotations

from typing import Any

# The reporting package has a known pre-existing circular import that resolves
# cleanly once the web app module is imported first. Importing the module for
# its side effect; the assignment keeps it referenced (no suppression comment).
import auto_a11y.web.app

_ = auto_a11y.web.app

from auto_a11y.models.test_result import ImpactLevel, TestResult, Violation
from auto_a11y.reporting.comprehensive_report import ComprehensiveReportGenerator

SCRIPT = '<script>alert(1)</script>'
ESCAPED = '&lt;script&gt;alert(1)&lt;/script&gt;'


class _ExposedGenerator(ComprehensiveReportGenerator):
    """Re-expose the protected render helpers so tests can drive the real
    production code paths without tripping pyright's reportPrivateUsage.

    These are thin pass-throughs to the exact shipping methods - no behaviour
    is duplicated or stubbed.
    """

    def analytics_for(self, data: dict[str, Any]) -> dict[str, Any]:
        return self._perform_analytics(data)

    def summary_for_language(
        self,
        data: dict[str, Any],
        analytics: dict[str, Any],
        ai_summary: dict[str, Any] | None,
    ) -> str:
        return self._format_summary_for_language(data, analytics, ai_summary)


def _generator() -> _ExposedGenerator:
    # No API key -> no real Claude calls; we drive the summary method directly.
    return _ExposedGenerator(claude_api_key=None)


def test_executive_summary_ai_fields_are_escaped() -> None:
    """The real shipping AI executive-summary renderer must escape its fields."""
    ai_summary: dict[str, Any] = {
        'overall_assessment': {
            'rating': 'Poor',
            'explanation': f'Explanation {SCRIPT}',
        },
        'key_strengths': [f'Strength {SCRIPT}'],
        'critical_risks': [f'Risk {SCRIPT}'],
        'show_stoppers': [f'Stopper {SCRIPT}'],
        'maturity_assessment': {
            'level': 'Developing',
            'description': f'Maturity {SCRIPT}',
        },
        'user_impact': {
            'vision': f'Vision impact {SCRIPT}',
        },
        'legal_risk': {
            'level': 'High',
            'explanation': f'Legal {SCRIPT}',
        },
        'recommendations': {
            'quick_wins': [f'Quick {SCRIPT}'],
            'short_term': [f'Short {SCRIPT}'],
            'long_term': [f'Long {SCRIPT}'],
        },
        'prioritization': [f'Priority {SCRIPT}'],
        'training_needs': [f'Training {SCRIPT}'],
    }

    data: dict[str, Any] = {
        'project': {'name': f'Proj {SCRIPT}'},
        'statistics': {'total_pages': 1, 'total_websites': 1},
        'websites': [],
    }
    analytics = _generator().analytics_for(data)

    html = _generator().summary_for_language(data, analytics, ai_summary)

    assert SCRIPT not in html
    assert ESCAPED in html
    # Every field family should have produced an escaped occurrence.
    assert html.count(ESCAPED) >= 11


def test_executive_summary_fallback_escapes_project_name() -> None:
    """The no-AI fallback branch interpolates the project name; escape it."""
    data: dict[str, Any] = {
        'project': {'name': f'Proj {SCRIPT}'},
        'statistics': {'total_pages': 1, 'total_websites': 1},
        'websites': [],
    }
    gen = _generator()
    analytics = gen.analytics_for(data)

    html = gen.summary_for_language(data, analytics, ai_summary=None)

    assert SCRIPT not in html
    assert ESCAPED in html


def test_comprehensive_html_escapes_page_and_issue_data() -> None:
    """Full production render path: page titles/urls + issue fields escaped."""
    violation = Violation(
        id=f'code_{SCRIPT}',
        impact=ImpactLevel.HIGH,
        touchpoint='ColorAndContrast',
        description=f'Contrast desc {SCRIPT}',
        xpath=f'/html/body/{SCRIPT}',
        element=f'div{SCRIPT}',
        html=f'<div>{SCRIPT}</div>',
        wcag_criteria=['1.4.3'],
        metadata={'breakpoint': 'default'},
    )
    test_result = TestResult(
        page_id='page-1',
        violations=[violation],
        warnings=[],
        info=[],
        discovery=[],
        passes=[],
    )

    data: dict[str, Any] = {
        'project': {'name': f'Proj {SCRIPT}', 'description': f'Desc {SCRIPT}'},
        'statistics': {'total_pages': 1, 'total_websites': 1, 'total_passes': 0,
                       'total_violations': 1, 'total_warnings': 0},
        'websites': [
            {
                'website': {'name': f'Site {SCRIPT}', 'url': f'http://x/{SCRIPT}'},
                'pages': [
                    {
                        'page': {'title': f'Title {SCRIPT}', 'url': f'http://x/p/{SCRIPT}'},
                        'test_result': test_result,
                    }
                ],
            }
        ],
    }

    html = _generator().generate_comprehensive_html(data, include_ai_summary=False)

    assert SCRIPT not in html
    assert ESCAPED in html
