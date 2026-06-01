"""Tests that AI-derived executive summary content is HTML-escaped.

AI model output is untrusted text. When interpolated into the report HTML it
must be escaped so that characters like ``<``, ``>`` and ``&`` cannot break the
markup or inject script (content-injection vector).
"""
from __future__ import annotations

# Importing ``auto_a11y.reporting.ai_executive_summary`` through the package
# triggers an unrelated circular import in ``auto_a11y.reporting.__init__``.
# Pre-importing the web package resolves the cycle before we pull in the module.
import auto_a11y.web as _web

from auto_a11y.reporting.ai_executive_summary import AIExecutiveSummaryGenerator

# Reference the side-effect import so static analysers see it as used.
assert _web is not None

PAYLOAD = "<script>alert(1)</script>"
ESCAPED = "&lt;script&gt;alert(1)&lt;/script&gt;"


def _make_summary() -> dict[str, object]:
    """A summary structure where every AI-derived field carries the payload."""
    return {
        'overall_assessment': {'rating': PAYLOAD, 'explanation': PAYLOAD},
        'key_strengths': [PAYLOAD],
        'critical_risks': [PAYLOAD],
        'show_stoppers': [PAYLOAD],
        'maturity_assessment': {'level': PAYLOAD, 'description': PAYLOAD},
        'user_impact': {'vision': PAYLOAD, 'motor': PAYLOAD},
        'legal_risk': {'level': PAYLOAD, 'explanation': PAYLOAD},
        'prioritization': [PAYLOAD],
        'recommendations': {
            'quick_wins': [PAYLOAD],
            'short_term': [PAYLOAD],
            'long_term': [PAYLOAD],
        },
        'training_needs': [PAYLOAD],
    }


def test_format_executive_summary_html_escapes_all_ai_fields() -> None:
    """Every AI-derived field (incl. show-stoppers and user-impact, which are
    rendered through private helpers from the public method) must be escaped."""
    generator = AIExecutiveSummaryGenerator()
    rendered = generator.format_executive_summary_html(_make_summary())

    assert PAYLOAD not in rendered, "raw <script> payload leaked into report HTML"
    assert ESCAPED in rendered, "expected HTML-escaped payload not found"


def test_show_stoppers_section_escaped_via_public_method() -> None:
    summary = _make_summary()
    summary['show_stoppers'] = [PAYLOAD]
    rendered = AIExecutiveSummaryGenerator().format_executive_summary_html(summary)

    # Show-stoppers block is only emitted when the list is non-empty.
    assert "show-stoppers-alert" in rendered
    assert PAYLOAD not in rendered
    assert ESCAPED in rendered


def test_user_impact_section_escaped_via_public_method() -> None:
    summary = _make_summary()
    summary['user_impact'] = {'vision': PAYLOAD}
    rendered = AIExecutiveSummaryGenerator().format_executive_summary_html(summary)

    assert "impact-item" in rendered
    assert PAYLOAD not in rendered
    assert ESCAPED in rendered
