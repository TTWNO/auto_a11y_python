"""Tests for streaming interface on report formatters."""

import json
import csv
import os
import tempfile
import shutil
import pytest

from auto_a11y.reporting.formatters import BaseFormatter, CSVFormatter, JSONFormatter


# ---------------------------------------------------------------------------
# Task 2 – BaseFormatter streaming interface
# ---------------------------------------------------------------------------

class TestBaseFormatterStreaming:
    """Verify the abstract streaming methods on BaseFormatter."""

    def setup_method(self):
        self.formatter = BaseFormatter(config={})

    def test_begin_raises(self):
        with pytest.raises(NotImplementedError):
            self.formatter.begin("out.txt", {})

    def test_append_page_raises(self):
        with pytest.raises(NotImplementedError):
            self.formatter.append_page("out.txt", {})

    def test_finalize_raises(self):
        with pytest.raises(NotImplementedError):
            self.formatter.finalize("out.txt", {})

    def test_cleanup_is_noop(self):
        # Should not raise and return None
        result = self.formatter.cleanup()
        assert result is None


# ---------------------------------------------------------------------------
# Helpers – lightweight stand-ins for model objects
# ---------------------------------------------------------------------------

class _FakePage:
    def __init__(self, url='http://example.com', title='Example'):
        self.url = url
        self.title = title


class _FakeIssue:
    def __init__(self, **kwargs):
        self.id = kwargs.get('id', 'ErrTest')
        self.description = kwargs.get('description', 'desc')
        self.touchpoint = kwargs.get('touchpoint', 'Forms')
        self.impact = kwargs.get('impact', 'critical')
        self.xpath = kwargs.get('xpath', '/html/body')
        self.html = kwargs.get('html', '<input>')
        self.wcag_criteria = kwargs.get('wcag_criteria', ['1.1.1'])
        self.metadata = kwargs.get('metadata', {})


class _FakeTestResult:
    def __init__(self, violations=None, warnings=None):
        self.violations = violations or []
        self.warnings = warnings or []


# ---------------------------------------------------------------------------
# Task 3 – CSVFormatter streaming
# ---------------------------------------------------------------------------

class TestCSVFormatterStreaming:
    """CSVFormatter.begin / append_page / finalize / cleanup."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.outfile = os.path.join(self.tmpdir, 'report.csv')
        self.formatter = CSVFormatter(config={})

    def teardown_method(self):
        self.formatter.cleanup()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _read_rows(self):
        with open(self.outfile, newline='', encoding='utf-8') as f:
            return list(csv.reader(f))

    def test_header_row_written(self):
        self.formatter.begin(self.outfile, {})
        self.formatter.finalize(self.outfile, {})
        rows = self._read_rows()
        assert len(rows) == 1
        assert rows[0] == [
            'URL', 'Page Title', 'Type', 'Code', 'Description',
            'Touchpoint', 'Impact', 'XPath', 'HTML', 'WCAG Criteria',
        ]

    def test_violation_rows_written(self):
        page_data = {
            'page': _FakePage(url='http://a.com', title='A'),
            'test_result': _FakeTestResult(
                violations=[_FakeIssue(id='ErrNoAlt', description='Missing alt')],
                warnings=[_FakeIssue(id='WarnContrast', description='Low contrast')],
            ),
        }
        self.formatter.begin(self.outfile, {})
        self.formatter.append_page(self.outfile, page_data)
        self.formatter.finalize(self.outfile, {})

        rows = self._read_rows()
        # header + 1 violation + 1 warning = 3
        assert len(rows) == 3
        assert rows[1][0] == 'http://a.com'  # URL
        assert rows[1][2] == 'Violation'
        assert rows[1][3] == 'ErrNoAlt'
        assert rows[2][2] == 'Warning'
        assert rows[2][3] == 'WarnContrast'

    def test_multiple_pages_accumulate(self):
        self.formatter.begin(self.outfile, {})
        for i in range(3):
            page_data = {
                'page': _FakePage(url=f'http://p{i}.com', title=f'P{i}'),
                'test_result': _FakeTestResult(
                    violations=[_FakeIssue()],
                ),
            }
            self.formatter.append_page(self.outfile, page_data)
        self.formatter.finalize(self.outfile, {})

        rows = self._read_rows()
        assert len(rows) == 4  # header + 3 violation rows

    def test_dict_page_data(self):
        """page_data may use plain dicts instead of model objects."""
        page_data = {
            'page': {'url': 'http://dict.com', 'title': 'Dict Page'},
            'test_result': {
                'violations': [{'id': 'ErrX', 'description': 'd', 'touchpoint': 'T',
                                'impact': 'serious', 'xpath': '/x', 'html': '<b>',
                                'wcag_criteria': ['4.1.2']}],
                'warnings': [],
            },
        }
        self.formatter.begin(self.outfile, {})
        self.formatter.append_page(self.outfile, page_data)
        self.formatter.finalize(self.outfile, {})

        rows = self._read_rows()
        assert len(rows) == 2
        assert rows[1][0] == 'http://dict.com'
        assert rows[1][3] == 'ErrX'

    def test_cleanup_closes_file(self):
        self.formatter.begin(self.outfile, {})
        self.formatter.cleanup()
        assert self.formatter._file.closed


# ---------------------------------------------------------------------------
# Task 4 – JSONFormatter streaming
# ---------------------------------------------------------------------------

class TestJSONFormatterStreaming:
    """JSONFormatter.begin / append_page / finalize / cleanup."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.outfile = os.path.join(self.tmpdir, 'report.json')
        self.formatter = JSONFormatter(config={})

    def teardown_method(self):
        self.formatter.cleanup()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _load_json(self):
        with open(self.outfile, encoding='utf-8') as f:
            return json.load(f)

    def test_valid_json_with_summary_and_pages(self):
        summary = {'total_pages': 1, 'total_violations': 2}
        page_data = {
            'page': _FakePage(url='http://a.com', title='A'),
            'test_result': _FakeTestResult(
                violations=[_FakeIssue(id='ErrNoAlt')],
            ),
        }
        self.formatter.begin(self.outfile, summary)
        self.formatter.append_page(self.outfile, page_data)
        self.formatter.finalize(self.outfile, summary)

        data = self._load_json()
        assert data['summary']['total_pages'] == 1
        assert len(data['pages']) == 1
        assert data['pages'][0]['page']['url'] == 'http://a.com'

    def test_multiple_pages_valid_json(self):
        summary = {'n': 3}
        self.formatter.begin(self.outfile, summary)
        for i in range(3):
            page_data = {
                'page': _FakePage(url=f'http://p{i}.com', title=f'P{i}'),
                'test_result': _FakeTestResult(violations=[_FakeIssue()]),
            }
            self.formatter.append_page(self.outfile, page_data)
        self.formatter.finalize(self.outfile, summary)

        data = self._load_json()
        assert len(data['pages']) == 3
        urls = [p['page']['url'] for p in data['pages']]
        assert urls == ['http://p0.com', 'http://p1.com', 'http://p2.com']

    def test_zero_pages_valid_json(self):
        summary = {'empty': True}
        self.formatter.begin(self.outfile, summary)
        self.formatter.finalize(self.outfile, summary)

        data = self._load_json()
        assert data['summary'] == {'empty': True}
        assert data['pages'] == []

    def test_cleanup_closes_file(self):
        self.formatter.begin(self.outfile, {})
        self.formatter.cleanup()
        assert self.formatter._file.closed

    def test_dict_page_data(self):
        """page_data with plain dicts instead of model objects."""
        page_data = {
            'page': {'url': 'http://dict.com', 'title': 'Dict'},
            'test_result': {
                'violations': [{'id': 'ErrX', 'description': 'd'}],
                'warnings': [],
            },
        }
        self.formatter.begin(self.outfile, {})
        self.formatter.append_page(self.outfile, page_data)
        self.formatter.finalize(self.outfile, {})

        data = self._load_json()
        assert len(data['pages']) == 1
        assert data['pages'][0]['page']['url'] == 'http://dict.com'
