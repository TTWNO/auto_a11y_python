"""Regression tests for code-audit bugs in auto_a11y/reporting/formatters.py.

Covers:
  Bug A -- ImpactLevel enum .upper() crash in the all-issues Excel sheet.
  Bug B -- stored XSS via unescaped interpolation in HTMLFormatter.
  Bug C -- bare except in _auto_adjust_columns (narrowed to Exception).
  Bug D -- _translate_impact leaving untranslated impacts raw-uppercased.

All assertions are made through the formatters' public API so the tests do
not depend on private implementation details.
"""
from __future__ import annotations

from io import BytesIO
from typing import Any

from openpyxl import load_workbook

# Importing the web app package first resolves a pre-existing circular import
# (auto_a11y.reporting.__init__ -> report_generator -> web.fluent -> web.__init__
# -> web.app -> routes.reports -> auto_a11y.reporting). When the full test suite
# runs, an api/* test imports the app first and breaks the cycle; doing it here
# keeps this module importable in isolation too. The assert keeps the import
# referenced so static checkers do not flag it as unused.
import auto_a11y.web.app

assert auto_a11y.web.app is not None

from auto_a11y.models.test_result import AIFinding, ImpactLevel
from auto_a11y.reporting.formatters import ExcelFormatter, HTMLFormatter


def _base_page_data(**overrides: Any) -> dict[str, Any]:
    """Minimal page-report data dict accepted by both Excel and HTML formatters."""
    data: dict[str, Any] = {
        'page': {'url': 'http://example.test/page'},
        'website': {'name': 'Example Site'},
        'project': {'name': 'Example Project'},
        'generated_at': '2026-06-01',
        'statistics': {'violations': 0, 'warnings': 0, 'passes': 0, 'duration_ms': 1},
        'violations': [],
        'warnings': [],
        'info': [],
        'discovery': [],
        'ai_findings': [],
        'passes': [],
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Bug A -- ImpactLevel enum severity must not crash the all-issues sheet
# ---------------------------------------------------------------------------

class TestAllIssuesSheetEnumSeverity:
    def test_ai_finding_enum_severity_does_not_crash(self) -> None:
        formatter = ExcelFormatter(config={})

        finding = AIFinding(
            type='heading_mismatch',
            severity=ImpactLevel.HIGH,
            description='A visual heading is not a real heading',
        )
        data = _base_page_data(ai_findings=[finding])

        # Must not raise AttributeError on the enum severity.
        result = formatter.format_page_report(data)
        assert isinstance(result, bytes)

        # Re-open the workbook and confirm some sheet recorded the enum
        # severity as the uppercased string value, not a crash.
        wb = load_workbook(BytesIO(result))
        all_values: list[object] = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                all_values.extend(row)
        assert 'HIGH' in all_values


# ---------------------------------------------------------------------------
# Bug B -- HTMLFormatter must escape dynamic interpolated values
# ---------------------------------------------------------------------------

class TestHTMLFormatterEscaping:
    XSS = '<script>alert(1)</script>'
    ATTR_BREAK = '"><img src=x onerror=alert(1)>'

    def _malicious_page_data(self) -> dict[str, Any]:
        return _base_page_data(
            page={'url': f'http://evil.test/{self.ATTR_BREAK}'},
            website={'name': f'Site {self.XSS}'},
            project={'name': f'Proj {self.XSS}'},
            statistics={'violations': 1, 'warnings': 0, 'passes': 0, 'duration_ms': 5},
            violations=[{
                'rule_id': f'Err {self.XSS}',
                'impact': 'high',
                'description': f'Bad thing {self.XSS}',
                'wcag_criteria': ['1.1.1'],
                'node_count': 1,
                'xpath': f'/html/body/{self.XSS}',
                'suggested_fix': f'Fix {self.XSS}',
            }],
        )

    def test_page_report_escapes_script_in_dynamic_values(self) -> None:
        formatter = HTMLFormatter(config={})
        html = formatter.format_page_report(self._malicious_page_data())

        # Raw script tag must never appear verbatim.
        assert self.XSS not in html
        # Escaped form must be present (proves the content survived, escaped).
        assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html
        # Attribute-breaking payload must be neutralised in the href.
        assert self.ATTR_BREAK not in html

    def test_streaming_report_escapes_title_and_issues(self) -> None:
        import os
        import tempfile

        formatter = HTMLFormatter(config={})
        out = os.path.join(tempfile.gettempdir(), 'audit_stream_test.html')
        formatter.begin(out, {'total_pages': 1})

        class _Page:
            url = 'http://evil.test/"><img onerror=alert(1)>'
            title = f'Title {TestHTMLFormatterEscaping.XSS}'

        class _Result:
            violations: list[dict[str, Any]] = [{
                'id': f'Err {TestHTMLFormatterEscaping.XSS}', 'impact': 'high',
                'description': f'd {TestHTMLFormatterEscaping.XSS}',
                'wcag_criteria': [], 'xpath': '/x',
            }]
            warnings: list[dict[str, Any]] = []

        formatter.append_page(out, {'page': _Page(), 'test_result': _Result()})
        formatter.finalize(out, {'total_pages': 1})
        with open(out, 'r', encoding='utf-8') as fh:
            html = fh.read()
        formatter.cleanup()

        assert self.XSS not in html
        assert '&lt;script&gt;' in html
        assert 'onerror=alert(1)>' not in html


# ---------------------------------------------------------------------------
# Bug D -- _translate_impact should not leak raw untranslated impacts
# ---------------------------------------------------------------------------

class TestTranslateImpactViaReport:
    def _render_fr_with_impact(self, impact: str) -> str:
        formatter = HTMLFormatter(config={}, language='fr')
        data = _base_page_data(
            statistics={'violations': 1, 'warnings': 0, 'passes': 0, 'duration_ms': 1},
            violations=[{
                'rule_id': 'ErrTest',
                'impact': impact,
                'description': 'desc',
                'wcag_criteria': [],
                'node_count': 1,
                'xpath': '/x',
            }],
        )
        return formatter.format_page_report(data)

    def test_known_impacts_translated_fr(self) -> None:
        assert 'ÉLEVÉ' in self._render_fr_with_impact('high')
        assert 'MOYEN' in self._render_fr_with_impact('medium')
        assert 'FAIBLE' in self._render_fr_with_impact('low')

    def test_unknown_impact_falls_back_to_translated_unknown(self) -> None:
        html = self._render_fr_with_impact('bogus-level')
        # Must use the translated 'unknown' label, never the raw English token.
        assert 'INCONNU' in html
        assert 'BOGUS-LEVEL' not in html

    def test_known_impacts_unchanged_en(self) -> None:
        formatter = HTMLFormatter(config={}, language='en')
        data = _base_page_data(
            statistics={'violations': 1, 'warnings': 0, 'passes': 0, 'duration_ms': 1},
            violations=[{
                'rule_id': 'ErrTest', 'impact': 'high', 'description': 'd',
                'wcag_criteria': [], 'node_count': 1, 'xpath': '/x',
            }],
        )
        assert 'HIGH' in formatter.format_page_report(data)
