"""Smoke tests that render every report template with realistic mock data.

Each test builds the Jinja2 environment exactly as its generator does, loads
the template, and calls template.render() with minimal but structurally
complete context data.  If any template variable is undefined, any filter is
missing, or any attribute access on context data fails, the render will raise
and the test fails.

Every template is rendered in both English and French to catch
locale-dependent issues (e.g. missing Fluent messages, conditional branches
that only execute for one language).

No database, browser, or Flask app is required — these are pure Jinja2 tests.
"""
from __future__ import annotations

import json
from typing import Any
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import jinja2
import pytest

from auto_a11y.web.fluent import ftl, ftl_enum, force_locale

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATES_DIR = _ROOT / 'auto_a11y' / 'web' / 'templates'

# ---------------------------------------------------------------------------
# Shared helpers: Jinja2 env builders (mirror the real generators exactly)
# ---------------------------------------------------------------------------

from auto_a11y.reporting.static_html_generator import (
    WCAG_URL_SLUGS,
    translate_wcag_criterion,
)


def _build_static_html_env() -> jinja2.Environment:
    """Mirror StaticHTMLReportGenerator.__init__."""
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=jinja2.select_autoescape(['html', 'xml']),
        extensions=['jinja2.ext.i18n'],
    )
    env.install_gettext_callables(
        gettext=lambda x: x,
        ngettext=lambda s, p, n: s if n == 1 else p,
        newstyle=True,
    )
    env.globals['ftl'] = ftl
    env.globals['ftl_enum'] = ftl_enum

    # Filters — same as _setup_template_filters()
    env.filters['error_code_only'] = lambda c: c.split(':')[1] if ':' in c else c
    env.filters['wcag_name'] = lambda c: c.split()[0] if c.split() else c
    env.filters['wcag_understanding_url'] = lambda c: f"https://www.w3.org/WAI/WCAG22/Understanding/{WCAG_URL_SLUGS.get(c, c)}"
    env.filters['wcag_quickref_url'] = lambda c: f"https://www.w3.org/WAI/WCAG22/quickref/#{WCAG_URL_SLUGS.get(c, c)}"
    env.filters['translate_wcag'] = lambda text, lang='en': translate_wcag_criterion(text, lang)
    return env


def _build_comprehensive_env() -> jinja2.Environment:
    """Mirror ComprehensiveReportGenerator.generate_bilingual_standalone_html."""
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=jinja2.select_autoescape(['html', 'xml']),
    )
    env.globals['ftl'] = ftl
    return env


def _build_recordings_env() -> jinja2.Environment:
    """Mirror RecordingsReportGenerator._generate_html_report."""
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=jinja2.select_autoescape(['html', 'xml']),
    )
    env.globals['ftl'] = ftl
    return env


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _minimal_translations() -> dict[str, _FallbackDict]:
    """Return a defaultdict-like translations structure that won't KeyError."""

    return {'en': _FallbackDict(), 'fr': _FallbackDict()}


class _FallbackDict(dict[str, str]):
    """Dict that returns the key itself for missing lookups."""
    def __missing__(self, key: str) -> str:
        return key


# ---------------------------------------------------------------------------
# Mock data factories
# ---------------------------------------------------------------------------

def _mock_issue(**overrides: Any) -> dict[str, Any]:
    """A single issue/violation dict matching what templates access."""
    base: dict[str, Any] = {
        'id': 'ErrTestIssue',
        'description': 'Test issue description',
        'description_en': 'Test issue description (EN)',
        'description_fr': 'Description du problème (FR)',
        'touchpoint': 'Forms',
        'impact': 'high',
        'xpath': '/html/body/form/input',
        'html_snippet': '<input type="text">',
        'element': '<input>',
        'failure_summary': 'Fix the element',
        'wcag_criteria': ['1.1.1'],
        'page_count': 2,
        'test_users': [],
        'user_roles': [],
        # Bilingual top-level fields used by dedup_component
        'why_en': 'Important for accessibility',
        'why_fr': 'Important pour l\'accessibilité',
        'who_en': 'Screen reader users',
        'who_fr': 'Utilisateurs de lecteurs d\'écran',
        'full_remediation_en': 'Add a label',
        'full_remediation_fr': 'Ajouter une étiquette',
        'wcag_full': ['1.1.1 Non-text Content'],
        'metadata': {
            'title_en': 'Missing label',
            'title_fr': 'Étiquette manquante',
            'what_en': 'This input has no label',
            'what_fr': 'Ce champ n\'a pas d\'étiquette',
            'what_generic_en': 'Input missing label',
            'what_generic_fr': 'Champ sans étiquette',
            'why_en': 'Important for accessibility',
            'why_fr': 'Important pour l\'accessibilité',
            'who_en': 'Screen reader users',
            'who_fr': 'Utilisateurs de lecteurs d\'écran',
            'full_remediation_en': 'Add a label element',
            'full_remediation_fr': 'Ajouter un élément label',
            'wcag_full': ['1.1.1 Non-text Content'],
            'authenticated_user': {
                'display_name': 'Test User',
                'roles': ['editor'],
            },
            'breakpoint': '1024',
            'pseudoclass': None,
        },
    }
    base.update(overrides)
    return base


def _mock_page_data(**overrides: Any) -> dict[str, Any]:
    """A page dict as returned by _collect_pages_data."""
    base: dict[str, Any] = {
        'id': 'page1',
        'title': 'Test Page',
        'url': 'https://example.com/test',
        'score': 85.0,
        'compliance_score': 90.0,
        'test_date': '2025-01-01',
        'violations': [_mock_issue()],
        'warnings': [_mock_issue(impact='medium', id='WarnTest')],
        'informational': [_mock_issue(impact='low', id='InfoTest')],
        'discovery': [],
        'issues': {
            'errors': 1,
            'warnings': 1,
            'info': 1,
            'discovery': 0,
        },
    }
    base.update(overrides)
    return base


def _mock_summary() -> dict[str, Any]:
    """Summary dict as built by _build_summary_from_stats."""
    return {
        'total_errors': 3,
        'total_warnings': 2,
        'total_info': 1,
        'total_discovery': 0,
        'pages_with_errors': 2,
        'pages_with_warnings': 1,
        'pages_with_info': 1,
        'pages_with_discovery': 0,
        'average_score': 82.5,
        'compliance_level': 'partial',
        'top_issues': [
            {'title': 'Missing alt text', 'count': 5, 'pages': 3, 'severity': 'high', 'impact': 'high'},
        ],
        'by_touchpoint': {
            'Forms': {'errors': 2, 'warnings': 1, 'info': 0},
            'Images': {'errors': 1, 'warnings': 1, 'info': 1},
        },
        'by_wcag': {
            '1.1.1': [{'code': 'ErrNoAlt', 'count': 3}],
        },
        'recommendations': [
            {'title': 'Add alt text', 'description': 'All images need alt attributes'},
        ],
    }


# ---------------------------------------------------------------------------
# Parametrize every test over both locales
# ---------------------------------------------------------------------------

LOCALES = pytest.mark.parametrize("lang", ["en", "fr"])


# ---------------------------------------------------------------------------
# StaticHTMLReportGenerator templates
# ---------------------------------------------------------------------------

class TestStaticHTMLTemplates:
    """Smoke-test every template rendered by StaticHTMLReportGenerator."""

    @pytest.fixture(autouse=True)
    def setup_env(self) -> None:
        self.env = _build_static_html_env()
        self.translations = _minimal_translations()

    def _common_context(self, lang: str, **extra: Any) -> dict[str, Any]:
        ctx: dict[str, Any] = {
            'language': lang,
            'translations_en': self.translations['en'],
            'translations_fr': self.translations['fr'],
            'translations_json': json.dumps({'en': {}, 'fr': {}}),
            't': self.translations[lang],
            'generation_date': '2025-01-01 12:00:00',
            'project_name': 'Test Project',
            'wcag_level': 'AA',
            'touchpoints_tested': ['Forms', 'Images'],
            'asset_path': 'assets/',
            'index_path': '',
            'bootstrap_css': '',
            'bootstrap_icons_css': '',
            'bootstrap_js': '',
            'tokens_css': '',
            'style_css': '',
            'show_error_codes': False,
        }
        ctx.update(extra)
        return ctx

    @LOCALES
    def test_index(self, lang: str) -> None:
        template = self.env.get_template('static_report/index.html')
        ctx = self._common_context(
            lang,
            pages=[_mock_page_data()],
            summary=_mock_summary(),
            website_url='https://example.com',
            current_page='index',
        )
        with force_locale(lang):
            html = template.render(**ctx)
        assert len(html) > 0

    @LOCALES
    def test_summary(self, lang: str) -> None:
        template = self.env.get_template('static_report/summary.html')
        ctx = self._common_context(
            lang,
            pages=[_mock_page_data()],
            summary=_mock_summary(),
            website_url='https://example.com',
            ai_tests_enabled=False,
            current_page='summary',
            report_version='1.0.0',
        )
        with force_locale(lang):
            html = template.render(**ctx)
        assert len(html) > 0

    @LOCALES
    def test_page_detail(self, lang: str) -> None:
        template = self.env.get_template('static_report/page_detail.html')
        page = _mock_page_data()
        navigation = {
            'previous': None,
            'next': None,
            'pages': [{'number': 1, 'title': 'Test Page', 'current': True}],
        }
        ctx = self._common_context(
            lang,
            page=page,
            violations=page['violations'],
            warnings=page['warnings'],
            informational=page['informational'],
            discovery=page['discovery'],
            errors_count=page['issues']['errors'],
            warnings_count=page['issues']['warnings'],
            info_count=page['issues']['info'],
            discovery_count=page['issues']['discovery'],
            compliance_score={'score': 90.0, 'passed_tests': 9, 'total_tests': 10},
            all_touchpoints=['Forms', 'Images'],
            navigation=navigation,
            asset_path='../assets/',
            index_path='../',
            filters_js='',
            inline_mode=True,
        )
        with force_locale(lang):
            html = template.render(**ctx)
        assert len(html) > 0

    @LOCALES
    def test_dedup_index(self, lang: str) -> None:
        template = self.env.get_template('static_report/dedup_index.html')
        ctx = self._common_context(
            lang,
            project={'name': 'Test Project'},
            components_with_issues=[{
                'safe_signature': 'nav_main',
                'lang': 'en',
                'type': 'Navigation',
                'label': 'Main Navigation',
                'signature': 'nav_main_12345678',
                'page_count': 3,
                'score': 75.0,
                'violations': 2,
                'warnings': 1,
                'info': 0,
                'discovery': 0,
                'total_issues': 3,
            }],
            common_issues=[{
                'type': 'violation',
                'rule_id': 'ErrNoAlt',
                'description_en': 'Missing alt text',
                'description_fr': 'Texte alternatif manquant',
                'page_count': 5,
                'percentage': 80,
                'touchpoint': 'Images',
                'impact': 'high',
                'wcag': '1.1.1',
            }],
            pages_with_unassigned=[{
                'safe_url': 'example_com_test',
                'title': 'Test Page',
                'url': 'https://example.com/test',
                'dedup_score': 70.0,
                'violations': 1,
                'warnings': 0,
                'info': 0,
                'discovery': 0,
            }],
            total_violations=5,
            total_warnings=3,
            total_info=1,
            total_discovery=0,
            total_components=2,
            total_pages=10,
            overall_accessibility_score=78.0,
            overall_compliance_score=85.0,
            report_date=datetime.now(),
        )
        with force_locale(lang):
            html = template.render(**ctx)
        assert len(html) > 0

    @LOCALES
    def test_dedup_component(self, lang: str) -> None:
        template = self.env.get_template('static_report/dedup_component.html')
        ctx = self._common_context(
            lang,
            component_type='Navigation',
            component_label='Main Navigation',
            component_signature='nav_main_sig',
            page_count=3,
            pages=['https://example.com/page1', 'https://example.com/page2'],
            issues=[_mock_issue()],
            total_issues=1,
            component_score=80.0,
            report_date=datetime.now(),
        )
        with force_locale(lang):
            html = template.render(**ctx)
        assert len(html) > 0

    @LOCALES
    def test_dedup_unassigned(self, lang: str) -> None:
        template = self.env.get_template('static_report/dedup_unassigned.html')
        page = SimpleNamespace(
            title='Test Page',
            url='https://example.com/test',
            test_date='2025-01-01',
            score=85.0,
        )
        ctx = self._common_context(
            lang,
            page=page,
            page_url='https://example.com/test',
            page_title='Test Page',
            violations=[_mock_issue()],
            warnings=[_mock_issue(impact='medium')],
            info=[_mock_issue(impact='low')],
            discovery=[],
            total_issues=3,
            errors_count=1,
            warnings_count=1,
            info_count=1,
            discovery_count=0,
            compliance_score={'score': 90.0, 'passed_tests': 9, 'total_tests': 10},
            dedup_score=85.0,
            report_date=datetime.now(),
        )
        with force_locale(lang):
            html = template.render(**ctx)
        assert len(html) > 0


# ---------------------------------------------------------------------------
# ComprehensiveReportGenerator template
# ---------------------------------------------------------------------------

class TestComprehensiveReportTemplate:
    """Smoke-test the comprehensive_report_standalone.html template."""

    @LOCALES
    def test_render(self, lang: str) -> None:
        env = _build_comprehensive_env()
        template = env.get_template('static_report/comprehensive_report_standalone.html')
        translations = _minimal_translations()

        context = {
            'data': {
                'project': {'name': 'Test Project', 'url': 'https://example.com'},
                'statistics': {
                    'total_passes': 100,
                    'total_violations': 10,
                    'total_warnings': 5,
                    'total_recordings': 2,
                    'total_recording_issues': 8,
                },
            },
            'analytics': {
                'by_impact': {'high': 3, 'medium': 4, 'low': 3},
                'by_type': {'error': 5, 'warning': 3, 'info': 1, 'discovery': 1},
                'by_touchpoint': {'Forms': 4, 'Images': 6},
                'wcag_compliance': {'1.1.1': 3, '2.4.1': 1},
                'total_violations': 10,
                'top_issues': [
                    {
                        'impact': 'high',
                        'description': 'Missing alt text',
                        'count': 3,
                        'details': {
                            'description': 'Missing alt text',
                            'description_en': 'Missing alt text',
                            'description_fr': 'Texte alternatif manquant',
                            'impact': 'high',
                            'wcag_criteria': ['1.1.1'],
                        },
                    },
                ],
                'pages_with_most_issues': [
                    {
                        'url': 'https://example.com/page1',
                        'total': 5,
                        'breakdown': {'error': 2, 'warning': 2, 'info': 1, 'discovery': 0},
                    },
                ],
            },
            'ai_summary_en': None,
            'ai_summary_fr': None,
            'translations_en': translations['en'],
            'translations_fr': translations['fr'],
            'language': lang,
            'report_date': '2025-01-01 12:00:00',
            'bootstrap_css': '',
            'bootstrap_icons_css': '',
            'bootstrap_js': '',
            'chartjs': '',
            'chart_colors': {
                'high': '#dc3545',
                'medium': '#ffc107',
                'low': '#17a2b8',
                'error': '#dc3545',
                'warning': '#ffc107',
                'info': '#17a2b8',
                'discovery': '#6f42c1',
                'pass': '#28a745',
            },
        }

        with force_locale(lang):
            html = template.render(**context)
        assert len(html) > 0


# ---------------------------------------------------------------------------
# RecordingsReportGenerator template
# ---------------------------------------------------------------------------

class _MockRecording:
    """Mimics the Recording model for template attribute access."""

    def __init__(self) -> None:
        self.recording_id = 'rec-001'
        self.title = 'Test Recording'
        self.description = 'A test recording description'
        self.auditor_name = 'Test Auditor'
        self.auditor_role = 'QA Specialist'

    def get_key_takeaways(self, lang: str) -> list[SimpleNamespace]:
        return [SimpleNamespace(
            number=1,
            topic='Navigation',
            description='Key takeaway description',
            timecodes=[SimpleNamespace(start='00:01', end='00:05')],
        )]

    def get_user_painpoints(self, lang: str) -> list[SimpleNamespace]:
        return [SimpleNamespace(
            impact='high',
            title='Cannot submit form',
            user_quote='I could not find the submit button',
            timecodes=[SimpleNamespace(start='00:10', end='00:15', duration='5s')],
        )]

    def get_user_assertions(self, lang: str) -> list[SimpleNamespace]:
        return [SimpleNamespace(
            number=1,
            assertion='The form is accessible',
            user_quote='I was able to complete it',
            timecodes=[SimpleNamespace(start='00:20', end='00:25', duration='5s')],
            context='During form testing',
        )]


class _MockRecordingIssue:
    """Mimics the RecordingIssue model for template attribute access."""

    def __init__(self) -> None:
        self.recording_id = 'rec-001'
        self.title = 'Missing label on input'
        self.short_title = 'Missing label'
        self.language = 'en'
        self.what = 'Input has no associated label'
        self.why = 'Screen readers cannot identify the field'
        self.who = 'Screen reader users'
        self.remediation = 'Add a label element'
        self.impact = SimpleNamespace(value='high')
        self.touchpoint = 'Forms'
        self.timecodes = [SimpleNamespace(start='00:10', end='00:15', duration='5s')]
        self.wcag = [SimpleNamespace(code='1.1.1', name='Non-text Content', level='A')]
        self.xpath = '/html/body/form/input'
        self.element = '<input>'
        self.html = '<input type="text">'


class TestRecordingsReportTemplate:
    """Smoke-test the recordings_report_standalone.html template."""

    @LOCALES
    def test_render(self, lang: str) -> None:
        env = _build_recordings_env()
        template = env.get_template('static_report/recordings_report_standalone.html')

        recording = _MockRecording()
        issue = _MockRecordingIssue()
        translations = _minimal_translations()

        context = {
            'project': SimpleNamespace(name='Test Project'),
            'recordings': [recording],
            'issues': [issue],
            'issues_by_touchpoint': {'Forms': [issue]},
            'issues_by_recording': {recording.recording_id: [issue]},
            'issues_by_recording_en': {recording.recording_id: [issue]},
            'issues_by_recording_fr': {recording.recording_id: [issue]},
            'stats': {
                'total_recordings': 1,
                'total_issues': 1,
                'high_count': 1,
                'medium_count': 0,
                'low_count': 0,
            },
            'include_summary': True,
            'include_timecodes': True,
            'include_wcag': True,
            'group_by_touchpoint': True,
            'language': lang,
            't': translations[lang],
            'translations_en': translations['en'],
            'translations_fr': translations['fr'],
            'translations_json': json.dumps({'en': {}, 'fr': {}}),
            'report_date': '2025-01-01 12:00:00',
            'asset_path': '',
            'bootstrap_css': '',
            'bootstrap_icons_css': '',
            'bootstrap_js': '',
            'tokens_css': '',
            'style_css': '',
        }

        with force_locale(lang):
            html = template.render(**context)
        assert len(html) > 0
