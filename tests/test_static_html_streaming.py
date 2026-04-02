"""Tests for StaticHTMLReportGenerator streaming (two-pass) approach."""

import pytest
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
