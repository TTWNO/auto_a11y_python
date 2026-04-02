"""Tests for StaticHTMLReportGenerator streaming (two-pass) approach."""

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock
from auto_a11y.reporting.static_html_generator import StaticHTMLReportGenerator


class TestStaticHTMLSummaryCollection:
    """Tests for _collect_summary_stats() — Pass 1 of the two-pass approach."""

    def setup_method(self):
        self.gen = StaticHTMLReportGenerator.__new__(StaticHTMLReportGenerator)
        self.gen.db = MagicMock()
        self.gen.language = 'en'

    def test_collects_counts_without_loading_full_results(self):
        """Summary collection should use get_latest_test_result_summary, NOT get_latest_test_result."""
        self.gen.db.get_latest_test_result_summary.return_value = {
            'id': 'tr1', 'page_id': 'p1',
            'violation_count': 5, 'warning_count': 2,
            'info_count': 1, 'discovery_count': 0, 'pass_count': 10,
            'test_date': None, 'score': 85.0,
        }
        self.gen.db.yield_test_result_items.return_value = iter([
            {'issue_id': 'ErrNoAlt', 'touchpoint': 'Images', 'impact': 'critical',
             'wcag_criteria': ['1.1.1'], 'item_type': 'violation'},
        ])
        self.gen.db.get_page.return_value = MagicMock(
            title='Test Page', url='http://example.com', screenshot_path=None
        )

        stats = self.gen._collect_summary_stats(['p1'])

        assert stats['total_errors'] == 5
        assert stats['total_warnings'] == 2
        assert stats['total_info'] == 1
        assert stats['total_discovery'] == 0
        assert stats['issue_counts']['ErrNoAlt']['count'] == 1
        assert 'p1' in stats['issue_counts']['ErrNoAlt']['pages']
        # Must NOT call get_latest_test_result (the heavy method)
        self.gen.db.get_latest_test_result.assert_not_called()

    def test_skips_pages_without_results(self):
        """Pages with no test results should be skipped gracefully."""
        self.gen.db.get_latest_test_result_summary.return_value = None
        self.gen.db.get_page.return_value = MagicMock(
            title='Empty Page', url='http://example.com/empty', screenshot_path=None
        )

        stats = self.gen._collect_summary_stats(['p1', 'p2'])

        assert stats['total_errors'] == 0
        assert stats['total_warnings'] == 0
        assert stats['total_pages'] == 2

    def test_counts_pages_with_issues(self):
        """Should correctly count how many pages have each type of issue."""
        self.gen.db.get_latest_test_result_summary.side_effect = [
            {'id': 'tr1', 'page_id': 'p1', 'violation_count': 5, 'warning_count': 0,
             'info_count': 0, 'discovery_count': 0, 'pass_count': 0,
             'test_date': None, 'score': 70.0},
            {'id': 'tr2', 'page_id': 'p2', 'violation_count': 0, 'warning_count': 3,
             'info_count': 0, 'discovery_count': 0, 'pass_count': 0,
             'test_date': None, 'score': 90.0},
        ]
        self.gen.db.yield_test_result_items.return_value = iter([])
        self.gen.db.get_page.side_effect = [
            MagicMock(title='Page 1', url='http://example.com/1', screenshot_path=None),
            MagicMock(title='Page 2', url='http://example.com/2', screenshot_path=None),
        ]

        stats = self.gen._collect_summary_stats(['p1', 'p2'])

        assert stats['pages_with_errors'] == 1
        assert stats['pages_with_warnings'] == 1
        assert stats['pages_with_info'] == 0
        assert stats['pages_with_discovery'] == 0

    def test_aggregates_touchpoint_counts(self):
        """Should aggregate issue counts per touchpoint."""
        self.gen.db.get_latest_test_result_summary.return_value = {
            'id': 'tr1', 'page_id': 'p1',
            'violation_count': 3, 'warning_count': 0,
            'info_count': 0, 'discovery_count': 0, 'pass_count': 0,
            'test_date': None, 'score': 60.0,
        }
        self.gen.db.yield_test_result_items.return_value = iter([
            {'issue_id': 'ErrNoAlt', 'touchpoint': 'Images', 'impact': 'critical',
             'wcag_criteria': ['1.1.1'], 'item_type': 'violation'},
            {'issue_id': 'ErrNoAlt', 'touchpoint': 'Images', 'impact': 'critical',
             'wcag_criteria': ['1.1.1'], 'item_type': 'violation'},
            {'issue_id': 'ErrNoLabel', 'touchpoint': 'Forms', 'impact': 'serious',
             'wcag_criteria': ['1.3.1', '4.1.2'], 'item_type': 'violation'},
        ])
        self.gen.db.get_page.return_value = MagicMock(
            title='Test Page', url='http://example.com', screenshot_path=None
        )

        stats = self.gen._collect_summary_stats(['p1'])

        assert stats['touchpoint_counts']['Images'] == 2
        assert stats['touchpoint_counts']['Forms'] == 1

    def test_aggregates_wcag_counts(self):
        """Should aggregate issue counts per WCAG criterion."""
        self.gen.db.get_latest_test_result_summary.return_value = {
            'id': 'tr1', 'page_id': 'p1',
            'violation_count': 2, 'warning_count': 0,
            'info_count': 0, 'discovery_count': 0, 'pass_count': 0,
            'test_date': None, 'score': 65.0,
        }
        self.gen.db.yield_test_result_items.return_value = iter([
            {'issue_id': 'ErrNoAlt', 'touchpoint': 'Images', 'impact': 'critical',
             'wcag_criteria': ['1.1.1'], 'item_type': 'violation'},
            {'issue_id': 'ErrNoLabel', 'touchpoint': 'Forms', 'impact': 'serious',
             'wcag_criteria': ['1.3.1', '4.1.2'], 'item_type': 'violation'},
        ])
        self.gen.db.get_page.return_value = MagicMock(
            title='Test Page', url='http://example.com', screenshot_path=None
        )

        stats = self.gen._collect_summary_stats(['p1'])

        assert stats['wcag_counts']['1.1.1'] == 1
        assert stats['wcag_counts']['1.3.1'] == 1
        assert stats['wcag_counts']['4.1.2'] == 1

    def test_collects_scores(self):
        """Should collect scores from summaries."""
        self.gen.db.get_latest_test_result_summary.side_effect = [
            {'id': 'tr1', 'page_id': 'p1', 'violation_count': 1, 'warning_count': 0,
             'info_count': 0, 'discovery_count': 0, 'pass_count': 10,
             'test_date': None, 'score': 80.0},
            {'id': 'tr2', 'page_id': 'p2', 'violation_count': 0, 'warning_count': 1,
             'info_count': 0, 'discovery_count': 0, 'pass_count': 10,
             'test_date': None, 'score': 95.0},
        ]
        self.gen.db.yield_test_result_items.return_value = iter([])
        self.gen.db.get_page.side_effect = [
            MagicMock(title='Page 1', url='http://example.com/1', screenshot_path=None),
            MagicMock(title='Page 2', url='http://example.com/2', screenshot_path=None),
        ]

        stats = self.gen._collect_summary_stats(['p1', 'p2'])

        assert stats['scores'] == [80.0, 95.0]

    def test_collects_page_info_list(self):
        """Should collect lightweight page info for index/manifest."""
        self.gen.db.get_latest_test_result_summary.return_value = {
            'id': 'tr1', 'page_id': 'p1',
            'violation_count': 3, 'warning_count': 1,
            'info_count': 2, 'discovery_count': 0, 'pass_count': 10,
            'test_date': None, 'score': 75.0,
        }
        self.gen.db.yield_test_result_items.return_value = iter([])
        self.gen.db.get_page.return_value = MagicMock(
            title='My Page', url='http://example.com/page', screenshot_path='shot.png'
        )

        stats = self.gen._collect_summary_stats(['p1'])

        assert len(stats['page_info']) == 1
        page = stats['page_info'][0]
        assert page['id'] == 'p1'
        assert page['title'] == 'My Page'
        assert page['url'] == 'http://example.com/page'
        assert page['score'] == 75.0
        assert page['issues']['errors'] == 3
        assert page['issues']['warnings'] == 1
        assert page['screenshot_path'] == 'shot.png'

    def test_progress_callback_invoked(self):
        """Progress callback should be called during collection."""
        self.gen.db.get_latest_test_result_summary.return_value = {
            'id': 'tr1', 'page_id': 'p1',
            'violation_count': 0, 'warning_count': 0,
            'info_count': 0, 'discovery_count': 0, 'pass_count': 0,
            'test_date': None, 'score': 100.0,
        }
        self.gen.db.yield_test_result_items.return_value = iter([])
        self.gen.db.get_page.return_value = MagicMock(
            title='Page', url='http://example.com', screenshot_path=None
        )

        callback = MagicMock()
        self.gen._collect_summary_stats(['p1'], progress_callback=callback)

        callback.assert_called()

    def test_multiple_pages_same_issue(self):
        """Same issue across multiple pages should track page count correctly."""
        self.gen.db.get_latest_test_result_summary.side_effect = [
            {'id': 'tr1', 'page_id': 'p1', 'violation_count': 1, 'warning_count': 0,
             'info_count': 0, 'discovery_count': 0, 'pass_count': 0,
             'test_date': None, 'score': 70.0},
            {'id': 'tr2', 'page_id': 'p2', 'violation_count': 1, 'warning_count': 0,
             'info_count': 0, 'discovery_count': 0, 'pass_count': 0,
             'test_date': None, 'score': 70.0},
        ]
        self.gen.db.yield_test_result_items.side_effect = [
            iter([{'issue_id': 'ErrNoAlt', 'touchpoint': 'Images', 'impact': 'critical',
                   'wcag_criteria': ['1.1.1'], 'item_type': 'violation'}]),
            iter([{'issue_id': 'ErrNoAlt', 'touchpoint': 'Images', 'impact': 'critical',
                   'wcag_criteria': ['1.1.1'], 'item_type': 'violation'}]),
        ]
        self.gen.db.get_page.side_effect = [
            MagicMock(title='Page 1', url='http://example.com/1', screenshot_path=None),
            MagicMock(title='Page 2', url='http://example.com/2', screenshot_path=None),
        ]

        stats = self.gen._collect_summary_stats(['p1', 'p2'])

        assert stats['issue_counts']['ErrNoAlt']['count'] == 2
        assert len(stats['issue_counts']['ErrNoAlt']['pages']) == 2
        assert 'p1' in stats['issue_counts']['ErrNoAlt']['pages']
        assert 'p2' in stats['issue_counts']['ErrNoAlt']['pages']


def _make_mock_issue(rule_id, xpath='//div', description='Test issue',
                     impact='critical', wcag=None, touchpoint='General',
                     element='<div>', metadata=None):
    """Helper to create a mock issue with a to_dict() method."""
    issue = MagicMock()
    d = {
        'id': rule_id, 'xpath': xpath, 'description': description,
        'impact': impact, 'wcag': wcag or ['1.1.1'], 'touchpoint': touchpoint,
        'element': element, 'metadata': metadata or {}
    }
    issue.to_dict.return_value = d
    issue.xpath = xpath
    issue.id = rule_id
    issue.description = description
    issue.impact = impact
    issue.html = element
    issue.touchpoint = touchpoint
    issue.failure_summary = ''
    issue.metadata = metadata or {}
    return issue


def _make_mock_test_result(violations=None, warnings=None, info=None, discovery=None,
                           passes=None, metadata=None):
    """Helper to create a mock TestResult."""
    tr = MagicMock()
    tr.violations = violations or []
    tr.warnings = warnings or []
    tr.info = info or []
    tr.discovery = discovery or []
    tr.passes = passes or []
    tr.metadata = metadata or {}
    return tr


class TestDedupStreaming:
    """Tests for _collect_dedup_data_streaming() — streaming dedup report data collection."""

    def setup_method(self):
        self.gen = StaticHTMLReportGenerator.__new__(StaticHTMLReportGenerator)
        self.gen.db = MagicMock()
        self.gen.language = 'en'

    @patch('auto_a11y.reporting.static_html_generator.IssueCatalog')
    @patch('auto_a11y.reporting.static_html_generator.force_locale')
    def test_collect_dedup_data_streaming_deduplicates_same_issue(self, mock_force_locale, mock_catalog):
        """Same issue on multiple pages should be deduplicated to 1 entry."""
        mock_force_locale.return_value.__enter__ = MagicMock()
        mock_force_locale.return_value.__exit__ = MagicMock()
        mock_catalog.enrich_issue.return_value = {
            'what': 'No alt', 'description_full': 'No alt text', 'why': '', 'why_it_matters': '',
            'who': '', 'who_it_affects': '', 'full_remediation': '', 'how_to_fix': '',
        }

        page1 = MagicMock(id='p1', url='http://a.com', title='Page A')
        page2 = MagicMock(id='p2', url='http://b.com', title='Page B')

        website = MagicMock(id='w1')
        self.gen.db.yield_pages.return_value = iter([page1, page2])
        self.gen.db.get_pages.return_value = [page1, page2]

        issue1 = _make_mock_issue('ErrNoAlt', xpath='//img')
        issue2 = _make_mock_issue('ErrNoAlt', xpath='//img')

        tr1 = _make_mock_test_result(violations=[issue1])
        tr2 = _make_mock_test_result(violations=[issue2])

        self.gen.db.get_latest_test_result.side_effect = [tr1, tr2]
        self.gen._calculate_page_score = MagicMock(return_value=75)

        components, issues, scores, compliance, total, meta = \
            self.gen._collect_dedup_data_streaming([website])

        # Both pages had same ErrNoAlt on //img — should be deduplicated to 1 issue
        assert len(issues) == 1
        assert issues[0]['rule_id'] == 'ErrNoAlt'
        assert issues[0]['page_count'] == 2
        assert total == 2

    def test_collect_dedup_empty_website(self):
        """Empty website (no pages) should return empty results."""
        website = MagicMock(id='w1')
        self.gen.db.yield_pages.return_value = iter([])
        self.gen.db.get_pages.return_value = []

        components, issues, scores, compliance, total, meta = \
            self.gen._collect_dedup_data_streaming([website])

        assert total == 0
        assert issues == []
        assert components == {}
        assert scores == []
        assert meta == {}

    def test_collect_dedup_skips_pages_without_test_results(self):
        """Pages with no test results should be skipped."""
        page1 = MagicMock(id='p1', url='http://a.com', title='Page A')
        website = MagicMock(id='w1')
        self.gen.db.yield_pages.return_value = iter([page1])
        self.gen.db.get_pages.return_value = [page1]
        self.gen.db.get_latest_test_result.return_value = None

        components, issues, scores, compliance, total, meta = \
            self.gen._collect_dedup_data_streaming([website])

        assert total == 0
        assert issues == []

    @patch('auto_a11y.reporting.static_html_generator.IssueCatalog')
    @patch('auto_a11y.reporting.static_html_generator.force_locale')
    def test_collect_dedup_builds_page_metadata(self, mock_force_locale, mock_catalog):
        """Streaming pass should collect lightweight page metadata for targeted re-reads."""
        mock_force_locale.return_value.__enter__ = MagicMock()
        mock_force_locale.return_value.__exit__ = MagicMock()
        mock_catalog.enrich_issue.return_value = {
            'what': '', 'description_full': '', 'why': '', 'why_it_matters': '',
            'who': '', 'who_it_affects': '', 'full_remediation': '', 'how_to_fix': '',
        }

        page1 = MagicMock(id='p1', url='http://a.com', title='Page A')
        website = MagicMock(id='w1')
        self.gen.db.yield_pages.return_value = iter([page1])
        self.gen.db.get_pages.return_value = [page1]

        tr = _make_mock_test_result(violations=[_make_mock_issue('ErrTest')])
        self.gen.db.get_latest_test_result.return_value = tr
        self.gen._calculate_page_score = MagicMock(return_value=80)

        _, _, _, _, _, meta = self.gen._collect_dedup_data_streaming([website])

        assert 'http://a.com' in meta
        assert meta['http://a.com']['page_id'] == 'p1'
        assert meta['http://a.com']['title'] == 'Page A'
        assert meta['http://a.com']['page_score'] == 80

    @patch('auto_a11y.reporting.static_html_generator.IssueCatalog')
    @patch('auto_a11y.reporting.static_html_generator.force_locale')
    def test_collect_dedup_extracts_common_components(self, mock_force_locale, mock_catalog):
        """Components appearing on 2+ pages should be detected."""
        mock_force_locale.return_value.__enter__ = MagicMock()
        mock_force_locale.return_value.__exit__ = MagicMock()
        mock_catalog.enrich_issue.return_value = {
            'what': '', 'description_full': '', 'why': '', 'why_it_matters': '',
            'who': '', 'who_it_affects': '', 'full_remediation': '', 'how_to_fix': '',
        }

        page1 = MagicMock(id='p1', url='http://a.com/1', title='Page 1')
        page2 = MagicMock(id='p2', url='http://a.com/2', title='Page 2')
        website = MagicMock(id='w1')
        self.gen.db.yield_pages.return_value = iter([page1, page2])
        self.gen.db.get_pages.return_value = [page1, page2]

        # Create discovery items for a nav that appears on both pages
        def make_disco(sig, page_xpath):
            d = MagicMock()
            d.to_dict.return_value = {
                'id': 'DiscoNavFound',
                'xpath': page_xpath,
                'metadata': {
                    'navSignature': sig,
                    'navLabel': 'Main Nav',
                    'pageLang': 'en',
                    'xpath': page_xpath
                }
            }
            return d

        tr1 = _make_mock_test_result(discovery=[make_disco('nav_abc', '//nav[1]')])
        tr2 = _make_mock_test_result(discovery=[make_disco('nav_abc', '//nav[1]')])

        self.gen.db.get_latest_test_result.side_effect = [tr1, tr2]
        self.gen._calculate_page_score = MagicMock(return_value=90)

        components, issues, _, _, _, _ = self.gen._collect_dedup_data_streaming([website])

        assert len(components) == 1
        comp_key = list(components.keys())[0]
        assert components[comp_key]['type'] == 'Navigation'
        assert len(components[comp_key]['pages']) == 2

    @patch('auto_a11y.reporting.static_html_generator.IssueCatalog')
    @patch('auto_a11y.reporting.static_html_generator.force_locale')
    def test_collect_dedup_filters_single_page_components(self, mock_force_locale, mock_catalog):
        """Components on only 1 page should be filtered out (unless fallback merge applies)."""
        mock_force_locale.return_value.__enter__ = MagicMock()
        mock_force_locale.return_value.__exit__ = MagicMock()
        mock_catalog.enrich_issue.return_value = {
            'what': '', 'description_full': '', 'why': '', 'why_it_matters': '',
            'who': '', 'who_it_affects': '', 'full_remediation': '', 'how_to_fix': '',
        }

        page1 = MagicMock(id='p1', url='http://a.com/1', title='Page 1')
        page2 = MagicMock(id='p2', url='http://a.com/2', title='Page 2')
        website = MagicMock(id='w1')
        self.gen.db.yield_pages.return_value = iter([page1, page2])
        self.gen.db.get_pages.return_value = [page1, page2]

        # Nav with sig_1 only on page1, nav with sig_2 only on page2
        # Different signatures, so no exact match across pages
        def make_disco(sig, xpath):
            d = MagicMock()
            d.to_dict.return_value = {
                'id': 'DiscoNavFound', 'xpath': xpath,
                'metadata': {'navSignature': sig, 'navLabel': 'Nav', 'pageLang': 'en', 'xpath': xpath}
            }
            return d

        tr1 = _make_mock_test_result(discovery=[make_disco('sig_1', '//nav[1]')])
        tr2 = _make_mock_test_result(discovery=[make_disco('sig_2', '//nav[1]')])

        self.gen.db.get_latest_test_result.side_effect = [tr1, tr2]
        self.gen._calculate_page_score = MagicMock(return_value=90)

        components, _, _, _, _, _ = self.gen._collect_dedup_data_streaming([website])

        # Should use fallback merge: both are Navigation|Guest with 1 page each -> merged
        assert len(components) == 1
        merged_key = list(components.keys())[0]
        assert 'merged_' in merged_key
        assert len(components[merged_key]['pages']) == 2

    @patch('auto_a11y.reporting.static_html_generator.IssueCatalog')
    @patch('auto_a11y.reporting.static_html_generator.force_locale')
    def test_collect_dedup_reclassifies_issues_in_removed_components(self, mock_force_locale, mock_catalog):
        """Issues matched to components that get filtered out should become unassigned."""
        mock_force_locale.return_value.__enter__ = MagicMock()
        mock_force_locale.return_value.__exit__ = MagicMock()
        mock_catalog.enrich_issue.return_value = {
            'what': '', 'description_full': '', 'why': '', 'why_it_matters': '',
            'who': '', 'who_it_affects': '', 'full_remediation': '', 'how_to_fix': '',
        }

        # Only 1 page, so any component found is single-page and will be filtered
        page1 = MagicMock(id='p1', url='http://a.com', title='Page A')
        website = MagicMock(id='w1')
        self.gen.db.yield_pages.return_value = iter([page1])
        self.gen.db.get_pages.return_value = [page1]

        disco = MagicMock()
        disco.to_dict.return_value = {
            'id': 'DiscoNavFound', 'xpath': '//nav[1]',
            'metadata': {'navSignature': 'nav_only_one', 'navLabel': 'Nav', 'pageLang': 'en', 'xpath': '//nav[1]'}
        }

        # Issue inside the nav component
        issue = _make_mock_issue('ErrBadLink', xpath='//nav[1]/a[1]')
        tr = _make_mock_test_result(violations=[issue], discovery=[disco])
        self.gen.db.get_latest_test_result.return_value = tr
        self.gen._calculate_page_score = MagicMock(return_value=70)

        components, issues, _, _, _, _ = self.gen._collect_dedup_data_streaming([website])

        # No common components (single page, no fallback merge possible with 1 component)
        assert len(components) == 0
        # The issue should be reclassified as unassigned (component_signature=None)
        assert len(issues) == 1
        assert issues[0]['component_signature'] is None

    @patch('auto_a11y.reporting.static_html_generator.IssueCatalog')
    @patch('auto_a11y.reporting.static_html_generator.force_locale')
    def test_collect_dedup_progress_callback(self, mock_force_locale, mock_catalog):
        """Progress callback should be invoked for each page."""
        mock_force_locale.return_value.__enter__ = MagicMock()
        mock_force_locale.return_value.__exit__ = MagicMock()
        mock_catalog.enrich_issue.return_value = {
            'what': '', 'description_full': '', 'why': '', 'why_it_matters': '',
            'who': '', 'who_it_affects': '', 'full_remediation': '', 'how_to_fix': '',
        }

        page1 = MagicMock(id='p1', url='http://a.com', title='Page A')
        page2 = MagicMock(id='p2', url='http://b.com', title='Page B')
        website = MagicMock(id='w1')
        self.gen.db.yield_pages.return_value = iter([page1, page2])
        self.gen.db.get_pages.return_value = [page1, page2]
        self.gen.db.get_latest_test_result.return_value = _make_mock_test_result()
        self.gen._calculate_page_score = MagicMock(return_value=90)

        callback = MagicMock()
        self.gen._collect_dedup_data_streaming([website], progress_callback=callback)

        assert callback.call_count == 2


class TestGroupUnassignedByPageStreaming:
    """Tests for _group_unassigned_by_page_streaming()."""

    def setup_method(self):
        self.gen = StaticHTMLReportGenerator.__new__(StaticHTMLReportGenerator)
        self.gen.db = MagicMock()
        self.gen.language = 'en'

    def test_empty_unassigned_issues(self):
        """No unassigned issues should return empty list."""
        result = self.gen._group_unassigned_by_page_streaming([], {}, {})
        assert result == []

    def test_reloads_test_result_for_pages_with_unassigned(self):
        """Should reload test results from DB only for pages with unassigned issues."""
        unassigned = [{
            'rule_id': 'ErrNoAlt', 'type': 'violation', 'pages': ['http://a.com'],
            'xpath': '//img', 'impact': 'critical'
        }]
        page_metadata = {
            'http://a.com': {'page_id': 'p1', 'title': 'Page A', 'page_score': 80, 'website_id': 'w1'},
            'http://b.com': {'page_id': 'p2', 'title': 'Page B', 'page_score': 90, 'website_id': 'w1'},
        }

        issue_obj = _make_mock_issue('ErrNoAlt', xpath='//img')
        tr = _make_mock_test_result(violations=[issue_obj], metadata={'failed_checks': 1, 'passed_checks': 9})
        self.gen.db.get_latest_test_result.return_value = tr
        self.gen._calculate_page_score = MagicMock(return_value=80)

        result = self.gen._group_unassigned_by_page_streaming(
            unassigned, {}, page_metadata
        )

        # Should only reload for page A (the one with unassigned issues), not page B
        self.gen.db.get_latest_test_result.assert_called_once_with('p1')
        assert len(result) == 1
        assert result[0]['url'] == 'http://a.com'

    def test_filters_out_component_issues(self):
        """Issues inside components should be filtered out from per-page data."""
        unassigned = [{
            'rule_id': 'ErrBadLink', 'type': 'violation',
            'pages': ['http://a.com'], 'xpath': '//body/p/a'
        }]
        common_components = {
            'nav_sig|Guest': {
                'type': 'Navigation', 'label': 'Nav', 'signature': 'nav_sig',
                'xpaths_by_page': {'http://a.com': '//nav[1]'},
                'pages': {'http://a.com', 'http://b.com'},
                'lang': 'en', 'user_context': 'Guest'
            }
        }
        page_metadata = {
            'http://a.com': {'page_id': 'p1', 'title': 'Page A', 'page_score': 80, 'website_id': 'w1'},
        }

        # Two violations: one inside nav (should be filtered), one outside (should remain)
        nav_issue = _make_mock_issue('ErrNavLink', xpath='//nav[1]/a[1]')
        body_issue = _make_mock_issue('ErrBadLink', xpath='//body/p/a')
        tr = _make_mock_test_result(
            violations=[nav_issue, body_issue],
            metadata={'failed_checks': 2, 'passed_checks': 8}
        )
        self.gen.db.get_latest_test_result.return_value = tr
        self.gen._calculate_page_score = MagicMock(return_value=90)

        result = self.gen._group_unassigned_by_page_streaming(
            unassigned, common_components, page_metadata
        )

        assert len(result) == 1
        # Only the body issue should be in violations (nav issue filtered out)
        assert result[0]['errors_count'] == 1
        assert len(result[0]['issues']['violations']) == 1
