"""Tests for the two-pass streaming report generator core methods."""

import pytest
from unittest.mock import MagicMock, call, patch
from collections import defaultdict, Counter

from auto_a11y.reporting.report_generator import ReportGenerator


def _make_generator(db=None):
    """Create a ReportGenerator without calling __init__."""
    rg = ReportGenerator.__new__(ReportGenerator)
    rg.db = db or MagicMock()
    rg.config = {}
    rg.language = 'en'
    return rg


def _make_page(page_id):
    """Create a mock page with a given id."""
    page = MagicMock()
    page.id = page_id
    return page


def _make_result_summary(result_id, violation_count=0, warning_count=0,
                          info_count=0, discovery_count=0, pass_count=0,
                          score=None):
    """Create a mock result summary dict."""
    return {
        'id': result_id,
        'violation_count': violation_count,
        'warning_count': warning_count,
        'info_count': info_count,
        'discovery_count': discovery_count,
        'pass_count': pass_count,
        'score': score,
    }


class TestCollectSummary:
    """Tests for _collect_summary."""

    def test_accumulates_counts_across_pages(self):
        db = MagicMock()
        rg = _make_generator(db)

        pages = [_make_page('p1'), _make_page('p2')]

        def page_gen():
            yield from pages

        db.get_latest_test_result_summary.side_effect = [
            _make_result_summary('r1', violation_count=3, warning_count=1,
                                  info_count=2, discovery_count=0, pass_count=5, score=80),
            _make_result_summary('r2', violation_count=1, warning_count=2,
                                  info_count=0, discovery_count=1, pass_count=3, score=90),
        ]
        # No items for simplicity
        db.yield_test_result_items.return_value = iter([])

        summary = rg._collect_summary(page_gen, None)

        assert summary['total_pages'] == 2
        assert summary['total_violations'] == 4
        assert summary['total_warnings'] == 3
        assert summary['total_info'] == 2
        assert summary['total_discovery'] == 1
        assert summary['total_passes'] == 8
        assert len(summary['page_scores']) == 2
        assert summary['page_scores'][0] == ('p1', 80)
        assert summary['page_scores'][1] == ('p2', 90)

    def test_streams_items_for_touchpoint_counting(self):
        db = MagicMock()
        rg = _make_generator(db)

        pages = [_make_page('p1')]

        def page_gen():
            yield from pages

        db.get_latest_test_result_summary.return_value = _make_result_summary(
            'r1', violation_count=3
        )

        items = [
            {'touchpoint': 'Forms', 'issue_id': 'ErrNoLabel', 'impact': 'critical'},
            {'touchpoint': 'Forms', 'issue_id': 'ErrNoLabel', 'impact': 'critical'},
            {'touchpoint': 'Images', 'code': 'ErrNoAlt', 'impact': 'serious'},
        ]
        db.yield_test_result_items.return_value = iter(items)

        summary = rg._collect_summary(page_gen, None)

        assert summary['touchpoint_counts']['Forms'] == 2
        assert summary['touchpoint_counts']['Images'] == 1
        assert summary['top_issue_codes']['ErrNoLabel'] == 2
        assert summary['top_issue_codes']['ErrNoAlt'] == 1
        assert summary['impact_counts']['critical'] == 2
        assert summary['impact_counts']['serious'] == 1

    def test_skips_pages_without_test_results(self):
        db = MagicMock()
        rg = _make_generator(db)

        pages = [_make_page('p1'), _make_page('p2')]

        def page_gen():
            yield from pages

        # First page has no results, second does
        db.get_latest_test_result_summary.side_effect = [
            None,
            _make_result_summary('r2', violation_count=5, pass_count=10),
        ]
        db.yield_test_result_items.return_value = iter([])

        summary = rg._collect_summary(page_gen, None)

        assert summary['total_pages'] == 1
        assert summary['total_violations'] == 5
        assert summary['total_passes'] == 10

    def test_calls_progress_callback_per_page(self):
        db = MagicMock()
        rg = _make_generator(db)

        pages = [_make_page('p1'), _make_page('p2'), _make_page('p3')]

        def page_gen():
            yield from pages

        db.get_latest_test_result_summary.return_value = None

        progress = MagicMock()
        rg._collect_summary(page_gen, progress)

        assert progress.call_count == 3
        # Verify it was called with incrementing page counts
        progress.assert_any_call(1, 0, "Collecting summary (1)...")
        progress.assert_any_call(2, 0, "Collecting summary (2)...")
        progress.assert_any_call(3, 0, "Collecting summary (3)...")


class TestWriteDetails:
    """Tests for _write_details."""

    def test_calls_begin_append_finalize_in_order(self):
        db = MagicMock()
        rg = _make_generator(db)

        pages = [_make_page('p1'), _make_page('p2')]

        def page_gen():
            yield from pages

        test_result = MagicMock()
        db.get_latest_test_result.return_value = test_result

        formatter = MagicMock()
        output_file = MagicMock()
        summary = {'total_pages': 2}

        # Track call order
        call_order = []
        formatter.begin.side_effect = lambda *a: call_order.append('begin')
        formatter.append_page.side_effect = lambda *a: call_order.append('append_page')
        formatter.finalize.side_effect = lambda *a: call_order.append('finalize')

        with patch.object(rg, '_prepare_single_page_data', return_value={'data': True}):
            rg._write_details(page_gen, summary, formatter, output_file, None)

        assert call_order == ['begin', 'append_page', 'append_page', 'finalize']
        formatter.begin.assert_called_once_with(output_file, summary)
        formatter.finalize.assert_called_once_with(output_file, summary)
        assert formatter.append_page.call_count == 2

    def test_skips_pages_without_results(self):
        db = MagicMock()
        rg = _make_generator(db)

        pages = [_make_page('p1'), _make_page('p2')]

        def page_gen():
            yield from pages

        # First page no result, second has result
        test_result = MagicMock()
        db.get_latest_test_result.side_effect = [None, test_result]

        formatter = MagicMock()
        output_file = MagicMock()
        summary = {'total_pages': 1}

        with patch.object(rg, '_prepare_single_page_data', return_value={'data': True}):
            rg._write_details(page_gen, summary, formatter, output_file, None)

        assert formatter.append_page.call_count == 1

    def test_calls_progress_per_page(self):
        db = MagicMock()
        rg = _make_generator(db)

        pages = [_make_page('p1'), _make_page('p2')]

        def page_gen():
            yield from pages

        db.get_latest_test_result.return_value = MagicMock()

        formatter = MagicMock()
        output_file = MagicMock()
        summary = {'total_pages': 2}

        progress = MagicMock()

        with patch.object(rg, '_prepare_single_page_data', return_value={'data': True}):
            rg._write_details(page_gen, summary, formatter, output_file, progress)

        assert progress.call_count == 2
        progress.assert_any_call(1, 2, "Writing details (1/2)...")
        progress.assert_any_call(2, 2, "Writing details (2/2)...")


class TestPrepareSinglePageData:
    """Tests for _prepare_single_page_data."""

    def test_returns_dict_with_page_and_test_result(self):
        rg = _make_generator()

        page = _make_page('p1')
        test_result = MagicMock()

        result = rg._prepare_single_page_data(page, test_result)

        assert isinstance(result, dict)
        assert result['page'] is page
        assert result['test_result'] is test_result


class TestCollectRecordingsData:
    """Tests for _collect_recordings_data."""

    def test_returns_recordings_with_issues(self):
        db = MagicMock()
        rg = _make_generator(db)

        recording = MagicMock()
        recording.recording_id = 'rec1'
        recording.get_key_takeaways.return_value = ['takeaway1']
        recording.get_user_painpoints.return_value = ['pain1']
        recording.get_user_assertions.return_value = ['assertion1']

        issue1 = MagicMock()
        issue2 = MagicMock()

        db.get_recordings.return_value = [recording]
        db.get_recording_issues.return_value = [issue1, issue2]

        result = rg._collect_recordings_data('proj1')

        assert len(result) == 1
        assert result[0]['recording'] is recording
        assert result[0]['issues'] == [issue1, issue2]
        assert result[0]['key_takeaways'] == ['takeaway1']
        assert result[0]['user_painpoints'] == ['pain1']
        assert result[0]['user_assertions'] == ['assertion1']
        db.get_recordings.assert_called_once_with(project_id='proj1')
        db.get_recording_issues.assert_called_once_with(recording_id='rec1')

    def test_returns_empty_list_when_no_recordings(self):
        db = MagicMock()
        rg = _make_generator(db)

        db.get_recordings.return_value = []

        result = rg._collect_recordings_data('proj1')

        assert result == []
        db.get_recordings.assert_called_once_with(project_id='proj1')


class _SimpleObj:
    """Simple object that supports __dict__ without MagicMock conflicts."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def _make_full_generator(db):
    """Create a ReportGenerator with formatters initialised."""
    rg = ReportGenerator.__new__(ReportGenerator)
    rg.db = db
    rg.config = {}
    rg.language = 'en'
    rg.report_dir = MagicMock()
    rg.report_dir.__truediv__ = MagicMock(return_value='fake/path.html')
    formatter = MagicMock()
    formatter.extension = 'html'
    rg.formatters = {'html': formatter}
    return rg, formatter


class TestGenerateWebsiteReport:
    """Tests for generate_website_report two-pass wiring."""

    def test_calls_collect_summary_and_write_details(self):
        db = MagicMock()
        rg, formatter = _make_full_generator(db)

        website = _SimpleObj(name='TestSite', project_id='proj1', id='w1',
                             url='https://test.com')
        db.get_website.return_value = website

        project = _SimpleObj(name='TestProject', id='proj1')
        db.get_project.return_value = project

        with patch.object(rg, '_collect_summary', return_value={
            'total_pages': 0,
        }) as mock_collect, \
             patch.object(rg, '_write_details') as mock_write, \
             patch('os.path.exists', return_value=False):
            rg.generate_website_report('w1', format='html')

        mock_collect.assert_called_once()
        mock_write.assert_called_once()
        # Summary should have website and project metadata attached
        summary_arg = mock_write.call_args[0][1]
        assert summary_arg['website']['name'] == 'TestSite'
        assert summary_arg['project']['name'] == 'TestProject'

    def test_cleans_up_on_error(self):
        db = MagicMock()
        rg, formatter = _make_full_generator(db)

        website = _SimpleObj(name='TestSite', project_id='proj1', id='w1',
                             url='https://test.com')
        db.get_website.return_value = website

        project = _SimpleObj(name='TestProject', id='proj1')
        db.get_project.return_value = project

        with patch.object(rg, '_collect_summary', side_effect=RuntimeError("boom")), \
             patch('os.path.exists', return_value=True) as mock_exists, \
             patch('os.remove') as mock_remove:
            with pytest.raises(RuntimeError):
                rg.generate_website_report('w1', format='html')

        formatter.cleanup.assert_called_once()
        mock_remove.assert_called_once()

    def test_raises_for_unknown_website(self):
        db = MagicMock()
        rg, _ = _make_full_generator(db)
        db.get_website.return_value = None

        with pytest.raises(ValueError, match="Website"):
            rg.generate_website_report('missing')

    def test_raises_for_unsupported_format(self):
        db = MagicMock()
        rg, _ = _make_full_generator(db)

        website = _SimpleObj(name='TestSite', project_id='proj1', id='w1',
                             url='https://test.com')
        db.get_website.return_value = website

        project = _SimpleObj(name='TestProject', id='proj1')
        db.get_project.return_value = project

        with pytest.raises(ValueError, match="Unsupported format"):
            rg.generate_website_report('w1', format='docx')


class TestGenerateProjectReport:
    """Tests for generate_project_report two-pass wiring."""

    def test_calls_collect_summary_and_write_details(self):
        db = MagicMock()
        rg, formatter = _make_full_generator(db)

        project = _SimpleObj(name='TestProject', id='proj1')
        db.get_project.return_value = project

        with patch.object(rg, '_collect_summary', return_value={
            'total_pages': 0,
        }) as mock_collect, \
             patch.object(rg, '_write_details') as mock_write, \
             patch.object(rg, '_collect_recordings_data', return_value=[]) as mock_rec, \
             patch('os.path.exists', return_value=False):
            rg.generate_project_report('proj1', format='html')

        mock_collect.assert_called_once()
        mock_write.assert_called_once()
        mock_rec.assert_called_once_with('proj1')
        # Summary should have project metadata and recordings attached
        summary_arg = mock_write.call_args[0][1]
        assert summary_arg['project']['name'] == 'TestProject'
        assert summary_arg['recordings'] == []

    def test_cleans_up_on_error(self):
        db = MagicMock()
        rg, formatter = _make_full_generator(db)

        project = _SimpleObj(name='TestProject', id='proj1')
        db.get_project.return_value = project

        with patch.object(rg, '_collect_summary', side_effect=RuntimeError("boom")), \
             patch('os.path.exists', return_value=True), \
             patch('os.remove') as mock_remove:
            with pytest.raises(RuntimeError):
                rg.generate_project_report('proj1', format='html')

        formatter.cleanup.assert_called_once()
        mock_remove.assert_called_once()

    def test_raises_for_unknown_project(self):
        db = MagicMock()
        rg, _ = _make_full_generator(db)
        db.get_project.return_value = None

        with pytest.raises(ValueError, match="Project"):
            rg.generate_project_report('missing')

    def test_raises_for_unsupported_format(self):
        db = MagicMock()
        rg, _ = _make_full_generator(db)

        project = _SimpleObj(name='TestProject', id='proj1')
        db.get_project.return_value = project

        with pytest.raises(ValueError, match="Unsupported format"):
            rg.generate_project_report('proj1', format='docx')


class TestGenerateAllProjectsReport:
    """Tests for generate_all_projects_report two-pass wiring."""

    def test_calls_collect_summary_and_write_details(self):
        db = MagicMock()
        rg, formatter = _make_full_generator(db)
        # _t and _sanitize_filename need to work
        rg._t = ReportGenerator._t.__get__(rg)
        rg._sanitize_filename = ReportGenerator._sanitize_filename.__get__(rg)

        project = _SimpleObj(name='ProjectA', id='p1')
        db.get_projects.return_value = [project]

        with patch.object(rg, '_collect_summary', return_value={
            'total_pages': 0,
        }) as mock_collect, \
             patch.object(rg, '_write_details') as mock_write, \
             patch('os.path.exists', return_value=False):
            rg.generate_all_projects_report(format='html')

        mock_collect.assert_called_once()
        mock_write.assert_called_once()
        summary_arg = mock_write.call_args[0][1]
        assert 'title' in summary_arg
        assert 'projects' in summary_arg
        assert summary_arg['projects'][0]['name'] == 'ProjectA'

    def test_cleans_up_on_error(self):
        db = MagicMock()
        rg, formatter = _make_full_generator(db)
        rg._t = ReportGenerator._t.__get__(rg)
        rg._sanitize_filename = ReportGenerator._sanitize_filename.__get__(rg)

        project = _SimpleObj(name='ProjectA', id='p1')
        db.get_projects.return_value = [project]

        with patch.object(rg, '_collect_summary', side_effect=RuntimeError("boom")), \
             patch('os.path.exists', return_value=True), \
             patch('os.remove') as mock_remove:
            with pytest.raises(RuntimeError):
                rg.generate_all_projects_report(format='html')

        formatter.cleanup.assert_called_once()
        mock_remove.assert_called_once()

    def test_raises_for_no_projects(self):
        db = MagicMock()
        rg, _ = _make_full_generator(db)
        db.get_projects.return_value = []

        with pytest.raises(ValueError, match="No projects found"):
            rg.generate_all_projects_report()

    def test_raises_for_unsupported_format(self):
        db = MagicMock()
        rg, _ = _make_full_generator(db)

        project = _SimpleObj(name='ProjectA', id='p1')
        db.get_projects.return_value = [project]

        with pytest.raises(ValueError, match="Unsupported format"):
            rg.generate_all_projects_report(format='docx')


class TestDeprecationWarnings:
    """Tests for deprecation warnings on old methods."""

    def test_base_formatter_format_website_report_emits_warning(self):
        from auto_a11y.reporting.formatters import BaseFormatter
        fmt = BaseFormatter.__new__(BaseFormatter)
        with pytest.warns(DeprecationWarning, match="format_website_report.*deprecated"):
            with pytest.raises(NotImplementedError):
                fmt.format_website_report({})

    def test_base_formatter_format_project_report_emits_warning(self):
        from auto_a11y.reporting.formatters import BaseFormatter
        fmt = BaseFormatter.__new__(BaseFormatter)
        with pytest.warns(DeprecationWarning, match="format_project_report.*deprecated"):
            with pytest.raises(NotImplementedError):
                fmt.format_project_report({})

    def test_prepare_website_report_data_emits_warning(self):
        import warnings as w
        rg = _make_generator()
        with pytest.warns(DeprecationWarning, match="_prepare_website_report_data.*deprecated"):
            # Call with minimal mocks to trigger the warning (will likely fail internally)
            try:
                rg._prepare_website_report_data(
                    MagicMock(), MagicMock(), [], True
                )
            except Exception:
                pass

    def test_prepare_project_report_data_emits_warning(self):
        rg = _make_generator()
        with pytest.warns(DeprecationWarning, match="_prepare_project_report_data.*deprecated"):
            try:
                rg._prepare_project_report_data(MagicMock(), [])
            except Exception:
                pass
