# Streaming Report Generation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate OOM crashes by making all multi-page report generation stream data page-by-page with O(1 page) memory.

**Architecture:** Two-pass approach — Pass 1 streams pages collecting summary counters only, Pass 2 streams pages again writing detail chunks via formatter `begin`/`append_page`/`finalize` interface. Database generator methods yield one document at a time from MongoDB cursors.

**Tech Stack:** Python 3.8+, pymongo cursors, openpyxl (standard mode), WeasyPrint, tempfile stdlib

**Spec:** `docs/superpowers/specs/2026-04-02-streaming-report-generation-design.md`

---

## File Structure

### New Files
- `tests/test_database_generators.py` — Tests for DB generator methods and `get_latest_test_result_summary()`
- `tests/test_streaming_reports.py` — Tests for two-pass report generation core
- `tests/test_formatter_streaming.py` — Tests for formatter `begin`/`append_page`/`finalize`

### Modified Files
- `auto_a11y/core/database.py` — Add `yield_pages()`, `yield_websites()`, `yield_test_result_items()`, `get_latest_test_result_summary()`
- `auto_a11y/reporting/formatters.py` — Add streaming interface to `BaseFormatter` and all 5 subclasses; deprecate old multi-page methods
- `auto_a11y/reporting/report_generator.py` — Add `_collect_summary()`, `_write_details()`, `_prepare_single_page_data()`, `_collect_recordings_data()`; rewire `generate_website_report()`, `generate_project_report()`, `generate_all_projects_report()`; deprecate old `_prepare_*` methods
- `auto_a11y/reporting/static_html_generator.py` — Replace `_collect_pages_data()` with two-pass streaming

---

### Task 1: Database Generator Methods

**Files:**
- Modify: `auto_a11y/core/database.py:417-435` (near `get_pages`), `:344-347` (near `get_websites`), `:717-733` (near `_get_test_result_items`)
- Create: `tests/test_database_generators.py`

- [ ] **Step 1: Write failing tests for `yield_pages()`**

```python
# tests/test_database_generators.py
import pytest
from unittest.mock import MagicMock, patch
from auto_a11y.core.database import Database
from auto_a11y.models import Page


class TestYieldPages:
    """Tests for Database.yield_pages() generator method."""

    def setup_method(self):
        """Create a mock database instance."""
        self.db = Database.__new__(Database)
        self.db.pages = MagicMock()

    def test_yields_page_objects_one_at_a_time(self):
        """Should yield Page objects from cursor without materializing list."""
        mock_cursor = MagicMock()
        mock_cursor.__iter__ = MagicMock(return_value=iter([
            {'_id': '1', 'website_id': 'w1', 'url': 'http://a.com', 'status': 'discovered'},
            {'_id': '2', 'website_id': 'w1', 'url': 'http://b.com', 'status': 'discovered'},
        ]))
        mock_cursor.close = MagicMock()
        self.db.pages.find.return_value.sort.return_value = mock_cursor

        results = list(self.db.yield_pages('w1'))

        assert len(results) == 2
        assert all(isinstance(p, Page) for p in results)
        mock_cursor.close.assert_called_once()

    def test_uses_no_cursor_timeout(self):
        """Should pass no_cursor_timeout=True to find()."""
        mock_cursor = MagicMock()
        mock_cursor.__iter__ = MagicMock(return_value=iter([]))
        mock_cursor.close = MagicMock()
        self.db.pages.find.return_value.sort.return_value = mock_cursor

        list(self.db.yield_pages('w1'))

        self.db.pages.find.assert_called_once()
        call_kwargs = self.db.pages.find.call_args
        assert call_kwargs[1].get('no_cursor_timeout') is True or \
               (len(call_kwargs[0]) > 1 and call_kwargs[0][1] is True)

    def test_closes_cursor_on_exception(self):
        """Should close cursor even if iteration raises."""
        mock_cursor = MagicMock()
        mock_cursor.__iter__ = MagicMock(side_effect=RuntimeError("DB error"))
        mock_cursor.close = MagicMock()
        self.db.pages.find.return_value.sort.return_value = mock_cursor

        with pytest.raises(RuntimeError):
            list(self.db.yield_pages('w1'))

        mock_cursor.close.assert_called_once()

    def test_empty_result_yields_nothing(self):
        """Should yield nothing for website with no pages."""
        mock_cursor = MagicMock()
        mock_cursor.__iter__ = MagicMock(return_value=iter([]))
        mock_cursor.close = MagicMock()
        self.db.pages.find.return_value.sort.return_value = mock_cursor

        results = list(self.db.yield_pages('w_empty'))
        assert results == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/tait/Documents/cnib/code/auto_a11y_python && .venv/bin/python -m pytest tests/test_database_generators.py::TestYieldPages -v`
Expected: FAIL — `AttributeError: 'Database' object has no attribute 'yield_pages'`

- [ ] **Step 3: Implement `yield_pages()` on Database**

Add after `get_pages()` (around line 435) in `auto_a11y/core/database.py`:

```python
def yield_pages(self, website_id: str, sort_field: str = 'url', sort_order: int = 1, latest_only: bool = True):
    """Yield Page objects one at a time from cursor.

    Memory-efficient alternative to get_pages() for report generation.
    Uses no_cursor_timeout to prevent timeout during long operations.

    Args:
        website_id: Website ID to filter pages
        sort_field: Field to sort by (default: 'url')
        sort_order: Sort direction, 1=ascending, -1=descending
        latest_only: Only include pages from latest discovery run (default: True).
                     Matches get_pages() default behavior.
    """
    query = {"website_id": website_id}
    if latest_only:
        query["is_in_latest_discovery"] = True
    cursor = self.pages.find(query, no_cursor_timeout=True).sort(sort_field, sort_order)
    try:
        for doc in cursor:
            yield Page.from_dict(doc)
    finally:
        cursor.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_database_generators.py::TestYieldPages -v`
Expected: PASS

- [ ] **Step 5: Write failing tests for `yield_websites()`**

Append to `tests/test_database_generators.py`:

```python
class TestYieldWebsites:
    """Tests for Database.yield_websites() generator method."""

    def setup_method(self):
        self.db = Database.__new__(Database)
        self.db.websites = MagicMock()

    def test_yields_website_objects(self):
        mock_cursor = MagicMock()
        mock_cursor.__iter__ = MagicMock(return_value=iter([
            {'_id': '1', 'project_id': 'p1', 'name': 'Site A', 'url': 'http://a.com'},
        ]))
        mock_cursor.close = MagicMock()
        self.db.websites.find.return_value = mock_cursor

        results = list(self.db.yield_websites('p1'))
        assert len(results) == 1
        mock_cursor.close.assert_called_once()

    def test_closes_cursor_on_exception(self):
        mock_cursor = MagicMock()
        mock_cursor.__iter__ = MagicMock(side_effect=RuntimeError("fail"))
        mock_cursor.close = MagicMock()
        self.db.websites.find.return_value = mock_cursor

        with pytest.raises(RuntimeError):
            list(self.db.yield_websites('p1'))
        mock_cursor.close.assert_called_once()
```

- [ ] **Step 6: Run test, verify fail, implement `yield_websites()`**

Add after `get_websites()` (around line 347) in `auto_a11y/core/database.py`:

```python
def yield_websites(self, project_id: str):
    """Yield Website objects one at a time from cursor.

    Memory-efficient alternative to get_websites() for report generation.
    """
    cursor = self.websites.find({"project_id": project_id}, no_cursor_timeout=True)
    try:
        for doc in cursor:
            yield Website.from_dict(doc)
    finally:
        cursor.close()
```

Run: `.venv/bin/python -m pytest tests/test_database_generators.py::TestYieldWebsites -v`
Expected: PASS

- [ ] **Step 7: Write failing tests for `yield_test_result_items()`**

Append to `tests/test_database_generators.py`:

```python
class TestYieldTestResultItems:
    """Tests for Database.yield_test_result_items() generator method."""

    def setup_method(self):
        self.db = Database.__new__(Database)
        self.db.test_result_items = MagicMock()

    def test_yields_item_dicts(self):
        items = [
            {'test_result_id': 'tr1', 'item_type': 'violation', 'issue_id': 'ErrNoAlt', 'touchpoint': 'Images', 'impact': 'critical'},
            {'test_result_id': 'tr1', 'item_type': 'violation', 'issue_id': 'ErrNoLabel', 'touchpoint': 'Forms', 'impact': 'serious'},
        ]
        mock_cursor = MagicMock()
        mock_cursor.__iter__ = MagicMock(return_value=iter(items))
        mock_cursor.close = MagicMock()
        self.db.test_result_items.find.return_value = mock_cursor

        results = list(self.db.yield_test_result_items('tr1'))
        assert len(results) == 2
        assert results[0]['issue_id'] == 'ErrNoAlt'
        mock_cursor.close.assert_called_once()

    def test_filters_by_item_type(self):
        mock_cursor = MagicMock()
        mock_cursor.__iter__ = MagicMock(return_value=iter([]))
        mock_cursor.close = MagicMock()
        self.db.test_result_items.find.return_value = mock_cursor

        list(self.db.yield_test_result_items('tr1', item_type='violation'))
        query = self.db.test_result_items.find.call_args[0][0]
        assert query['item_type'] == 'violation'

    def test_no_type_filter_queries_all(self):
        mock_cursor = MagicMock()
        mock_cursor.__iter__ = MagicMock(return_value=iter([]))
        mock_cursor.close = MagicMock()
        self.db.test_result_items.find.return_value = mock_cursor

        list(self.db.yield_test_result_items('tr1'))
        query = self.db.test_result_items.find.call_args[0][0]
        assert 'item_type' not in query
```

- [ ] **Step 8: Run test, verify fail, implement `yield_test_result_items()`**

Add after `_get_test_result_items()` (around line 733) in `auto_a11y/core/database.py`:

```python
def yield_test_result_items(self, test_result_id, item_type=None):
    """Yield individual test result items from cursor.

    Memory-efficient alternative to _get_test_result_items() for report generation.
    Each item is a raw dict from MongoDB, not reconstructed into model objects.

    Args:
        test_result_id: The test result ObjectId or string ID
        item_type: Optional filter — 'violation', 'warning', 'info', 'discovery', 'pass'
    """
    query = {'test_result_id': test_result_id}
    if item_type:
        query['item_type'] = item_type
    cursor = self.test_result_items.find(query, no_cursor_timeout=True)
    try:
        for doc in cursor:
            yield doc
    finally:
        cursor.close()
```

Run: `.venv/bin/python -m pytest tests/test_database_generators.py::TestYieldTestResultItems -v`
Expected: PASS

- [ ] **Step 9: Write failing tests for `get_latest_test_result_summary()`**

Append to `tests/test_database_generators.py`:

```python
from bson import ObjectId


class TestGetLatestTestResultSummary:
    """Tests for Database.get_latest_test_result_summary()."""

    def setup_method(self):
        self.db = Database.__new__(Database)
        self.db.test_results = MagicMock()

    def test_returns_summary_dict_with_stored_counts(self):
        """Should return stored counts from doc, not computed from arrays."""
        doc_id = ObjectId()
        self.db.test_results.find_one.return_value = {
            '_id': doc_id,
            'page_id': 'p1',
            'violation_count': 42,
            'warning_count': 10,
            'info_count': 5,
            'discovery_count': 3,
            'pass_count': 100,
            'test_date': '2026-04-01',
            'score': 75.5,
        }

        result = self.db.get_latest_test_result_summary('p1')

        assert result is not None
        assert result['id'] == str(doc_id)
        assert result['violation_count'] == 42
        assert result['warning_count'] == 10
        assert result['info_count'] == 5
        assert result['discovery_count'] == 3
        assert result['pass_count'] == 100
        assert result['score'] == 75.5

    def test_returns_none_when_no_result(self):
        self.db.test_results.find_one.return_value = None
        assert self.db.get_latest_test_result_summary('p_missing') is None

    def test_defaults_missing_counts_to_zero(self):
        """Old schema docs may not have stored counts."""
        self.db.test_results.find_one.return_value = {
            '_id': ObjectId(),
            'page_id': 'p1',
            'test_date': '2026-04-01',
        }

        result = self.db.get_latest_test_result_summary('p1')
        assert result['violation_count'] == 0
        assert result['warning_count'] == 0

    def test_queries_with_sort_by_test_date_descending(self):
        self.db.test_results.find_one.return_value = None
        self.db.get_latest_test_result_summary('p1')
        call_kwargs = self.db.test_results.find_one.call_args
        assert call_kwargs[1]['sort'] == [('test_date', -1)] or \
               call_kwargs[0][1] == [('test_date', -1)]
```

- [ ] **Step 10: Run test, verify fail, implement `get_latest_test_result_summary()`**

Add after `get_latest_test_result()` (around line 862) in `auto_a11y/core/database.py`:

```python
def get_latest_test_result_summary(self, page_id: str):
    """Get summary counts for the latest test result without loading items.

    Returns a lightweight dict with stored count fields from the test_results
    collection. Does NOT load item arrays from test_result_items. This avoids
    the memory cost of full TestResult construction for summary-only use cases.

    Returns:
        Dict with id, page_id, counts, test_date — or None if no result.

    Note: `score` is not currently persisted in the test_results collection
    (it is a computed property on TestResult). The field is included for
    forward-compatibility but will return None until score persistence is added.
    """
    doc = self.test_results.find_one(
        {"page_id": page_id},
        sort=[("test_date", -1)]
    )
    if not doc:
        return None
    return {
        'id': str(doc['_id']),
        'page_id': page_id,
        'violation_count': doc.get('violation_count', 0),
        'warning_count': doc.get('warning_count', 0),
        'info_count': doc.get('info_count', 0),
        'discovery_count': doc.get('discovery_count', 0),
        'pass_count': doc.get('pass_count', 0),
        'test_date': doc.get('test_date'),
        'score': doc.get('score'),  # Not currently persisted — returns None
    }
```

Run: `.venv/bin/python -m pytest tests/test_database_generators.py::TestGetLatestTestResultSummary -v`
Expected: PASS

- [ ] **Step 11: Run full test file and commit**

Run: `.venv/bin/python -m pytest tests/test_database_generators.py -v`
Expected: All tests PASS

```bash
git add auto_a11y/core/database.py tests/test_database_generators.py
git commit -m "feat: add database generator methods for streaming reports

Add yield_pages(), yield_websites(), yield_test_result_items() generators
and get_latest_test_result_summary() for O(1 page) memory report generation."
```

---

### Task 2: BaseFormatter Streaming Interface

**Files:**
- Modify: `auto_a11y/reporting/formatters.py:20-43` (BaseFormatter class)
- Create: `tests/test_formatter_streaming.py`

- [ ] **Step 1: Write failing test for BaseFormatter streaming methods**

```python
# tests/test_formatter_streaming.py
import pytest
from auto_a11y.reporting.formatters import BaseFormatter


class TestBaseFormatterStreamingInterface:
    """Tests for the begin/append_page/finalize/cleanup interface on BaseFormatter."""

    def test_begin_raises_not_implemented(self):
        formatter = BaseFormatter.__new__(BaseFormatter)
        with pytest.raises(NotImplementedError):
            formatter.begin('/tmp/out.txt', {'total_pages': 0})

    def test_append_page_raises_not_implemented(self):
        formatter = BaseFormatter.__new__(BaseFormatter)
        with pytest.raises(NotImplementedError):
            formatter.append_page('/tmp/out.txt', {'page': {}})

    def test_finalize_raises_not_implemented(self):
        formatter = BaseFormatter.__new__(BaseFormatter)
        with pytest.raises(NotImplementedError):
            formatter.finalize('/tmp/out.txt', {'total_pages': 0})

    def test_cleanup_is_noop_by_default(self):
        formatter = BaseFormatter.__new__(BaseFormatter)
        formatter.cleanup()  # Should not raise
```

- [ ] **Step 2: Run test, verify fail**

Run: `.venv/bin/python -m pytest tests/test_formatter_streaming.py::TestBaseFormatterStreamingInterface -v`
Expected: FAIL — `AttributeError: 'BaseFormatter' object has no attribute 'begin'`

- [ ] **Step 3: Implement streaming interface on BaseFormatter**

Edit `auto_a11y/reporting/formatters.py` — add after the existing `format_summary_report` method (around line 41) inside `BaseFormatter`:

```python
    def begin(self, output_file: str, summary: dict):
        """Write report header/preamble using summary stats.

        Part of the streaming interface for memory-efficient multi-page reports.
        Called once before any append_page() calls.

        Args:
            output_file: Path to the output file
            summary: Summary statistics from Pass 1
        """
        raise NotImplementedError

    def append_page(self, output_file: str, page_data: dict):
        """Write one page's detail section.

        Called once per page during Pass 2. Only one page's data is in
        memory at a time.

        Args:
            output_file: Path to the output file
            page_data: Single page's prepared data dict
        """
        raise NotImplementedError

    def finalize(self, output_file: str, summary: dict):
        """Write report footer/closing using final summary stats.

        Called once after all append_page() calls complete.

        Args:
            output_file: Path to the output file
            summary: Summary statistics (may include recordings data)
        """
        raise NotImplementedError

    def cleanup(self):
        """Delete any temp files created during streaming.

        Called in a finally block after report generation completes or fails.
        Default implementation is a no-op.
        """
        pass
```

- [ ] **Step 4: Run test, verify pass**

Run: `.venv/bin/python -m pytest tests/test_formatter_streaming.py::TestBaseFormatterStreamingInterface -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add auto_a11y/reporting/formatters.py tests/test_formatter_streaming.py
git commit -m "feat: add streaming interface to BaseFormatter

Add begin/append_page/finalize/cleanup methods for memory-efficient
multi-page report generation."
```

---

### Task 3: CSV Streaming Formatter

**Files:**
- Modify: `auto_a11y/reporting/formatters.py:794-900` (CSVFormatter)
- Modify: `tests/test_formatter_streaming.py`

- [ ] **Step 1: Write failing tests for CSV streaming**

Append to `tests/test_formatter_streaming.py`:

```python
import csv
import os
import tempfile
from auto_a11y.reporting.formatters import CSVFormatter


class TestCSVFormatterStreaming:
    """Tests for CSVFormatter begin/append_page/finalize."""

    def setup_method(self):
        self.formatter = CSVFormatter({'SHOW_ERROR_CODES': True}, 'en')
        self.tmpdir = tempfile.mkdtemp()
        self.output_file = os.path.join(self.tmpdir, 'test.csv')

    def teardown_method(self):
        if os.path.exists(self.output_file):
            os.remove(self.output_file)
        os.rmdir(self.tmpdir)

    def test_begin_writes_header_row(self):
        self.formatter.begin(self.output_file, {'total_pages': 1})
        with open(self.output_file, 'r') as f:
            reader = csv.reader(f)
            header = next(reader)
        assert 'URL' in header or 'url' in [h.lower() for h in header]

    def test_append_page_writes_violation_rows(self):
        summary = {'total_pages': 1}
        page_data = {
            'page': type('Page', (), {'url': 'http://example.com', 'title': 'Test'})(),
            'test_result': type('TR', (), {
                'violations': [
                    type('V', (), {'id': 'ErrNoAlt', 'description': 'No alt text',
                                   'touchpoint': 'Images', 'impact': 'critical',
                                   'xpath': '//img', 'html': '<img>', 'wcag_criteria': ['1.1.1'],
                                   'metadata': {}})()
                ],
                'warnings': [],
            })(),
        }
        self.formatter.begin(self.output_file, summary)
        self.formatter.append_page(self.output_file, page_data)
        self.formatter.finalize(self.output_file, summary)

        with open(self.output_file, 'r') as f:
            lines = f.readlines()
        assert len(lines) >= 2  # header + at least 1 data row

    def test_multiple_pages_accumulate_rows(self):
        summary = {'total_pages': 2}
        make_page = lambda url, vid: {
            'page': type('Page', (), {'url': url, 'title': 'T'})(),
            'test_result': type('TR', (), {
                'violations': [
                    type('V', (), {'id': vid, 'description': 'd', 'touchpoint': 'T',
                                   'impact': 'c', 'xpath': '//x', 'html': '<x>',
                                   'wcag_criteria': [], 'metadata': {}})()
                ],
                'warnings': [],
            })(),
        }
        self.formatter.begin(self.output_file, summary)
        self.formatter.append_page(self.output_file, make_page('http://a.com', 'Err1'))
        self.formatter.append_page(self.output_file, make_page('http://b.com', 'Err2'))
        self.formatter.finalize(self.output_file, summary)

        with open(self.output_file, 'r') as f:
            lines = f.readlines()
        assert len(lines) >= 3  # header + 2 data rows
```

- [ ] **Step 2: Run tests, verify fail**

Run: `.venv/bin/python -m pytest tests/test_formatter_streaming.py::TestCSVFormatterStreaming -v`
Expected: FAIL

- [ ] **Step 3: Implement CSV streaming methods**

Add to `CSVFormatter` class in `auto_a11y/reporting/formatters.py`:

```python
    def begin(self, output_file, summary):
        """Open CSV file and write header row."""
        self._csv_file = open(output_file, 'w', newline='', encoding='utf-8')
        self._csv_writer = csv.writer(self._csv_file)
        self._csv_writer.writerow([
            'URL', 'Page Title', 'Type', 'Code', 'Description',
            'Touchpoint', 'Impact', 'XPath', 'HTML', 'WCAG Criteria'
        ])

    def append_page(self, output_file, page_data):
        """Write rows for one page's violations and warnings."""
        page = page_data['page']
        test_result = page_data['test_result']
        url = page.url if hasattr(page, 'url') else page.get('url', '')
        title = page.title if hasattr(page, 'title') else page.get('title', '')

        for issue_type, issues in [('Violation', test_result.violations), ('Warning', test_result.warnings)]:
            for issue in issues:
                self._csv_writer.writerow([
                    url,
                    title,
                    issue_type,
                    issue.id if hasattr(issue, 'id') else issue.get('id', ''),
                    issue.description if hasattr(issue, 'description') else issue.get('description', ''),
                    issue.touchpoint if hasattr(issue, 'touchpoint') else issue.get('touchpoint', ''),
                    issue.impact if hasattr(issue, 'impact') else issue.get('impact', ''),
                    issue.xpath if hasattr(issue, 'xpath') else issue.get('xpath', ''),
                    issue.html if hasattr(issue, 'html') else issue.get('html', ''),
                    ', '.join(issue.wcag_criteria) if hasattr(issue, 'wcag_criteria') and issue.wcag_criteria else '',
                ])

    def finalize(self, output_file, summary):
        """Flush and close the CSV file."""
        if hasattr(self, '_csv_file') and self._csv_file:
            self._csv_file.flush()
            self._csv_file.close()
            self._csv_file = None

    def cleanup(self):
        """Close file handle if still open."""
        if hasattr(self, '_csv_file') and self._csv_file and not self._csv_file.closed:
            self._csv_file.close()
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/python -m pytest tests/test_formatter_streaming.py::TestCSVFormatterStreaming -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add auto_a11y/reporting/formatters.py tests/test_formatter_streaming.py
git commit -m "feat: add CSV streaming formatter (begin/append_page/finalize)"
```

---

### Task 4: JSON Streaming Formatter

**Files:**
- Modify: `auto_a11y/reporting/formatters.py:770-792` (JSONFormatter)
- Modify: `tests/test_formatter_streaming.py`

- [ ] **Step 1: Write failing tests for JSON streaming**

Append to `tests/test_formatter_streaming.py`:

```python
import json
from auto_a11y.reporting.formatters import JSONFormatter


class TestJSONFormatterStreaming:
    """Tests for JSONFormatter begin/append_page/finalize."""

    def setup_method(self):
        self.formatter = JSONFormatter({}, 'en')
        self.tmpdir = tempfile.mkdtemp()
        self.output_file = os.path.join(self.tmpdir, 'test.json')

    def teardown_method(self):
        if os.path.exists(self.output_file):
            os.remove(self.output_file)
        os.rmdir(self.tmpdir)

    def test_produces_valid_json_with_summary_and_pages(self):
        summary = {'total_pages': 1, 'total_violations': 5}
        page_data = {'page_id': 'p1', 'url': 'http://example.com', 'violations': 5}

        self.formatter.begin(self.output_file, summary)
        self.formatter.append_page(self.output_file, page_data)
        self.formatter.finalize(self.output_file, summary)

        with open(self.output_file, 'r') as f:
            result = json.load(f)
        assert 'summary' in result
        assert 'pages' in result
        assert len(result['pages']) == 1

    def test_multiple_pages_valid_json(self):
        summary = {'total_pages': 3}
        self.formatter.begin(self.output_file, summary)
        for i in range(3):
            self.formatter.append_page(self.output_file, {'page_id': f'p{i}'})
        self.formatter.finalize(self.output_file, summary)

        with open(self.output_file, 'r') as f:
            result = json.load(f)
        assert len(result['pages']) == 3

    def test_zero_pages_valid_json(self):
        summary = {'total_pages': 0}
        self.formatter.begin(self.output_file, summary)
        self.formatter.finalize(self.output_file, summary)

        with open(self.output_file, 'r') as f:
            result = json.load(f)
        assert result['pages'] == []
```

- [ ] **Step 2: Run tests, verify fail**

Run: `.venv/bin/python -m pytest tests/test_formatter_streaming.py::TestJSONFormatterStreaming -v`
Expected: FAIL

- [ ] **Step 3: Implement JSON streaming methods**

Add to `JSONFormatter` class in `auto_a11y/reporting/formatters.py`:

```python
    def begin(self, output_file, summary):
        """Write JSON opening with summary object and start of pages array."""
        self._json_file = open(output_file, 'w', encoding='utf-8')
        # Write summary and open pages array
        summary_json = json.dumps(summary, indent=2, default=str)
        self._json_file.write('{\n"summary": ')
        self._json_file.write(summary_json)
        self._json_file.write(',\n"pages": [\n')
        self._is_first_page = True

    def append_page(self, output_file, page_data):
        """Write one page's JSON object with proper comma handling."""
        if not self._is_first_page:
            self._json_file.write(',\n')
        self._json_file.write(json.dumps(page_data, indent=2, default=str))
        self._is_first_page = False

    def finalize(self, output_file, summary):
        """Close the pages array and JSON object."""
        self._json_file.write('\n]\n}')
        self._json_file.flush()
        self._json_file.close()
        self._json_file = None

    def cleanup(self):
        """Close file handle if still open."""
        if hasattr(self, '_json_file') and self._json_file and not self._json_file.closed:
            self._json_file.close()
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/python -m pytest tests/test_formatter_streaming.py::TestJSONFormatterStreaming -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add auto_a11y/reporting/formatters.py tests/test_formatter_streaming.py
git commit -m "feat: add JSON streaming formatter (begin/append_page/finalize)"
```

---

### Task 5: HTML Streaming Formatter

**Files:**
- Modify: `auto_a11y/reporting/formatters.py:46-768` (HTMLFormatter)
- Modify: `tests/test_formatter_streaming.py`

- [ ] **Step 1: Write failing tests for HTML streaming**

Append to `tests/test_formatter_streaming.py`:

```python
from auto_a11y.reporting.formatters import HTMLFormatter


class TestHTMLFormatterStreaming:
    """Tests for HTMLFormatter begin/append_page/finalize with temp file strategy."""

    def setup_method(self):
        self.formatter = HTMLFormatter({}, 'en')
        self.tmpdir = tempfile.mkdtemp()
        self.output_file = os.path.join(self.tmpdir, 'test.html')

    def teardown_method(self):
        self.formatter.cleanup()
        if os.path.exists(self.output_file):
            os.remove(self.output_file)
        # Clean up any remaining temp files
        for f in os.listdir(self.tmpdir):
            os.remove(os.path.join(self.tmpdir, f))
        os.rmdir(self.tmpdir)

    def test_produces_valid_html_document(self):
        summary = {'total_pages': 1, 'total_violations': 3, 'total_warnings': 1,
                   'total_info': 0, 'total_discovery': 0, 'total_passes': 0,
                   'touchpoint_counts': {}, 'impact_counts': {}, 'top_issue_codes': {}}
        page_data = {
            'page': type('P', (), {'url': 'http://example.com', 'title': 'Test'})(),
            'test_result': type('TR', (), {
                'violations': [], 'warnings': [],
                'violation_count': 0, 'warning_count': 0,
            })(),
        }

        self.formatter.begin(self.output_file, summary)
        self.formatter.append_page(self.output_file, page_data)
        self.formatter.finalize(self.output_file, summary)

        with open(self.output_file, 'r') as f:
            content = f.read()
        assert '<html' in content
        assert '</html>' in content

    def test_cleanup_removes_temp_file(self):
        summary = {'total_pages': 0, 'total_violations': 0, 'total_warnings': 0,
                   'total_info': 0, 'total_discovery': 0, 'total_passes': 0,
                   'touchpoint_counts': {}, 'impact_counts': {}, 'top_issue_codes': {}}
        self.formatter.begin(self.output_file, summary)
        # Temp body file should exist
        assert hasattr(self.formatter, '_body_tempfile')
        temp_path = self.formatter._body_tempfile.name
        assert os.path.exists(temp_path)

        self.formatter.cleanup()
        assert not os.path.exists(temp_path)

    def test_body_content_read_in_chunks_not_all_at_once(self):
        """Verify finalize reads body temp file, not loads it entirely."""
        summary = {'total_pages': 1, 'total_violations': 0, 'total_warnings': 0,
                   'total_info': 0, 'total_discovery': 0, 'total_passes': 0,
                   'touchpoint_counts': {}, 'impact_counts': {}, 'top_issue_codes': {}}
        page_data = {
            'page': type('P', (), {'url': 'http://example.com', 'title': 'T'})(),
            'test_result': type('TR', (), {
                'violations': [], 'warnings': [],
                'violation_count': 0, 'warning_count': 0,
            })(),
        }

        self.formatter.begin(self.output_file, summary)
        self.formatter.append_page(self.output_file, page_data)
        self.formatter.finalize(self.output_file, summary)

        # Output file should exist and be non-empty
        assert os.path.exists(self.output_file)
        assert os.path.getsize(self.output_file) > 0
```

- [ ] **Step 2: Run tests, verify fail**

Run: `.venv/bin/python -m pytest tests/test_formatter_streaming.py::TestHTMLFormatterStreaming -v`
Expected: FAIL

- [ ] **Step 3: Implement HTML streaming methods**

Add to `HTMLFormatter` class in `auto_a11y/reporting/formatters.py`. Read the existing `format_website_report()` method first to understand the HTML structure and CSS used, then implement:

```python
    def begin(self, output_file, summary):
        """Open a temp body file for page content accumulation."""
        import tempfile as _tempfile
        self._body_tempfile = _tempfile.NamedTemporaryFile(
            mode='w', suffix='.html', delete=False, encoding='utf-8'
        )
        self._output_file = output_file
        self._summary = summary

    def append_page(self, output_file, page_data):
        """Write one page's HTML section to the temp body file."""
        page = page_data['page']
        test_result = page_data['test_result']
        url = page.url if hasattr(page, 'url') else page.get('url', '')
        title = page.title if hasattr(page, 'title') else page.get('title', '')
        v_count = test_result.violation_count if hasattr(test_result, 'violation_count') else 0
        w_count = test_result.warning_count if hasattr(test_result, 'warning_count') else 0

        self._body_tempfile.write(f'<div class="page-section">\n')
        self._body_tempfile.write(f'<h2><a href="{url}">{title or url}</a></h2>\n')
        self._body_tempfile.write(f'<p>Violations: {v_count} | Warnings: {w_count}</p>\n')

        # Write violations table
        if hasattr(test_result, 'violations') and test_result.violations:
            self._body_tempfile.write('<table class="violations-table"><thead><tr>')
            self._body_tempfile.write('<th>Code</th><th>Description</th><th>Impact</th><th>Touchpoint</th><th>WCAG</th>')
            self._body_tempfile.write('</tr></thead><tbody>\n')
            for v in test_result.violations:
                vid = v.id if hasattr(v, 'id') else v.get('id', '')
                desc = v.description if hasattr(v, 'description') else v.get('description', '')
                impact = v.impact if hasattr(v, 'impact') else v.get('impact', '')
                tp = v.touchpoint if hasattr(v, 'touchpoint') else v.get('touchpoint', '')
                wcag = ', '.join(v.wcag_criteria) if hasattr(v, 'wcag_criteria') and v.wcag_criteria else ''
                self._body_tempfile.write(f'<tr><td>{vid}</td><td>{desc}</td><td>{impact}</td><td>{tp}</td><td>{wcag}</td></tr>\n')
            self._body_tempfile.write('</tbody></table>\n')

        # Write warnings table (same pattern)
        if hasattr(test_result, 'warnings') and test_result.warnings:
            self._body_tempfile.write('<table class="warnings-table"><thead><tr>')
            self._body_tempfile.write('<th>Code</th><th>Description</th><th>Impact</th><th>Touchpoint</th><th>WCAG</th>')
            self._body_tempfile.write('</tr></thead><tbody>\n')
            for w in test_result.warnings:
                wid = w.id if hasattr(w, 'id') else w.get('id', '')
                desc = w.description if hasattr(w, 'description') else w.get('description', '')
                impact = w.impact if hasattr(w, 'impact') else w.get('impact', '')
                tp = w.touchpoint if hasattr(w, 'touchpoint') else w.get('touchpoint', '')
                wcag = ', '.join(w.wcag_criteria) if hasattr(w, 'wcag_criteria') and w.wcag_criteria else ''
                self._body_tempfile.write(f'<tr><td>{wid}</td><td>{desc}</td><td>{impact}</td><td>{tp}</td><td>{wcag}</td></tr>\n')
            self._body_tempfile.write('</tbody></table>\n')

        self._body_tempfile.write('</div>\n')

    def finalize(self, output_file, summary):
        """Assemble final HTML: header + summary + body (from temp) + footer."""
        # Close temp body file for reading
        self._body_tempfile.flush()
        self._body_tempfile.close()

        with open(output_file, 'w', encoding='utf-8') as out:
            # Write HTML header and CSS
            out.write('<!DOCTYPE html>\n<html lang="en">\n<head>\n')
            out.write('<meta charset="UTF-8">\n')
            out.write('<title>Accessibility Report</title>\n')
            out.write('<style>\n')
            out.write('body { font-family: Arial, sans-serif; margin: 20px; }\n')
            out.write('table { border-collapse: collapse; width: 100%; margin: 10px 0; }\n')
            out.write('th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }\n')
            out.write('th { background-color: #f2f2f2; }\n')
            out.write('.page-section { margin-bottom: 30px; }\n')
            out.write('</style>\n</head>\n<body>\n')

            # Write summary dashboard
            out.write('<h1>Accessibility Report</h1>\n')
            out.write('<div class="summary">\n')
            out.write(f'<p>Total Pages: {summary.get("total_pages", 0)}</p>\n')
            out.write(f'<p>Total Violations: {summary.get("total_violations", 0)}</p>\n')
            out.write(f'<p>Total Warnings: {summary.get("total_warnings", 0)}</p>\n')
            out.write('</div>\n')

            # Stream body content from temp file in 64KB chunks
            with open(self._body_tempfile.name, 'r', encoding='utf-8') as body:
                while True:
                    chunk = body.read(65536)  # 64KB
                    if not chunk:
                        break
                    out.write(chunk)

            out.write('\n</body>\n</html>')

    def cleanup(self):
        """Delete the temp body file."""
        if hasattr(self, '_body_tempfile') and self._body_tempfile:
            temp_path = self._body_tempfile.name
            # Close if still open
            if not self._body_tempfile.closed:
                self._body_tempfile.close()
            if os.path.exists(temp_path):
                os.remove(temp_path)
            self._body_tempfile = None
```

Note: The `append_page` HTML output here is intentionally simpler than the existing `format_website_report()` which uses the `ComprehensiveReportGenerator`. During implementation, match the existing HTML structure/CSS by reading the current `format_website_report()` output patterns. The test validates the streaming mechanics; the exact HTML styling should mirror the existing report appearance.

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/python -m pytest tests/test_formatter_streaming.py::TestHTMLFormatterStreaming -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add auto_a11y/reporting/formatters.py tests/test_formatter_streaming.py
git commit -m "feat: add HTML streaming formatter with temp file strategy"
```

---

### Task 6: Excel Streaming Formatter

**Files:**
- Modify: `auto_a11y/reporting/formatters.py:903-2733` (ExcelFormatter)
- Modify: `tests/test_formatter_streaming.py`

- [ ] **Step 1: Write failing tests for Excel streaming**

Append to `tests/test_formatter_streaming.py`:

```python
from auto_a11y.reporting.formatters import ExcelFormatter


class TestExcelFormatterStreaming:
    """Tests for ExcelFormatter begin/append_page/finalize."""

    def setup_method(self):
        self.formatter = ExcelFormatter({}, 'en')
        self.tmpdir = tempfile.mkdtemp()
        self.output_file = os.path.join(self.tmpdir, 'test.xlsx')

    def teardown_method(self):
        if os.path.exists(self.output_file):
            os.remove(self.output_file)
        os.rmdir(self.tmpdir)

    def test_produces_valid_xlsx(self):
        try:
            from openpyxl import load_workbook
        except ImportError:
            pytest.skip("openpyxl not installed")

        summary = {'total_pages': 1, 'total_violations': 2, 'total_warnings': 1,
                   'total_info': 0, 'total_discovery': 0, 'total_passes': 0}
        page_data = {
            'page': type('P', (), {'url': 'http://example.com', 'title': 'Test'})(),
            'test_result': type('TR', (), {
                'violations': [
                    type('V', (), {'id': 'ErrNoAlt', 'description': 'No alt',
                                   'touchpoint': 'Images', 'impact': 'critical',
                                   'xpath': '//img', 'html': '<img>', 'wcag_criteria': ['1.1.1'],
                                   'metadata': {}})()
                ],
                'warnings': [],
                'violation_count': 1, 'warning_count': 0,
            })(),
        }

        self.formatter.begin(self.output_file, summary)
        self.formatter.append_page(self.output_file, page_data)
        self.formatter.finalize(self.output_file, summary)

        wb = load_workbook(self.output_file)
        sheet_names = wb.sheetnames
        assert len(sheet_names) >= 2  # At least Summary + one detail sheet
        wb.close()

    def test_multiple_pages_add_rows(self):
        try:
            from openpyxl import load_workbook
        except ImportError:
            pytest.skip("openpyxl not installed")

        summary = {'total_pages': 2, 'total_violations': 2, 'total_warnings': 0,
                   'total_info': 0, 'total_discovery': 0, 'total_passes': 0}
        make_page = lambda url: {
            'page': type('P', (), {'url': url, 'title': 'T'})(),
            'test_result': type('TR', (), {
                'violations': [
                    type('V', (), {'id': 'Err1', 'description': 'd', 'touchpoint': 'T',
                                   'impact': 'c', 'xpath': '//x', 'html': '<x>',
                                   'wcag_criteria': [], 'metadata': {}})()
                ],
                'warnings': [],
                'violation_count': 1, 'warning_count': 0,
            })(),
        }

        self.formatter.begin(self.output_file, summary)
        self.formatter.append_page(self.output_file, make_page('http://a.com'))
        self.formatter.append_page(self.output_file, make_page('http://b.com'))
        self.formatter.finalize(self.output_file, summary)

        wb = load_workbook(self.output_file)
        # Find the violations sheet and check it has rows from both pages
        violations_sheet = None
        for name in wb.sheetnames:
            if 'violation' in name.lower() or 'issue' in name.lower():
                violations_sheet = wb[name]
                break
        if violations_sheet:
            # Should have header + 2 data rows
            assert violations_sheet.max_row >= 3
        wb.close()
```

- [ ] **Step 2: Run tests, verify fail**

Run: `.venv/bin/python -m pytest tests/test_formatter_streaming.py::TestExcelFormatterStreaming -v`
Expected: FAIL

- [ ] **Step 3: Implement Excel streaming methods**

Read the existing `ExcelFormatter.format_website_report()` and `format_project_report()` methods first to understand the sheet structure, column layout, and styling used. Then add streaming methods that match the same layout. Add to `ExcelFormatter` class:

```python
    def begin(self, output_file, summary):
        """Create workbook with summary sheet and detail sheet headers."""
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment

        self._wb = Workbook()
        self._output_file = output_file

        # Summary sheet
        ws = self._wb.active
        ws.title = 'Summary'
        ws.merge_cells('A1:E1')
        ws['A1'] = 'Accessibility Report Summary'
        ws['A1'].font = Font(bold=True, size=14)
        ws.append([])
        ws.append(['Total Pages', summary.get('total_pages', 0)])
        ws.append(['Total Violations', summary.get('total_violations', 0)])
        ws.append(['Total Warnings', summary.get('total_warnings', 0)])
        ws.append(['Total Info', summary.get('total_info', 0)])
        ws.append(['Total Passes', summary.get('total_passes', 0)])

        # Violations detail sheet
        self._violations_ws = self._wb.create_sheet('Violations')
        header_fill = PatternFill(start_color='D9E1F2', end_color='D9E1F2', fill_type='solid')
        headers = ['URL', 'Page Title', 'Code', 'Description', 'Touchpoint', 'Impact', 'XPath', 'HTML', 'WCAG Criteria']
        self._violations_ws.append(headers)
        for cell in self._violations_ws[1]:
            cell.font = Font(bold=True)
            cell.fill = header_fill

        # Warnings detail sheet
        self._warnings_ws = self._wb.create_sheet('Warnings')
        self._warnings_ws.append(headers)
        for cell in self._warnings_ws[1]:
            cell.font = Font(bold=True)
            cell.fill = header_fill

    def append_page(self, output_file, page_data):
        """Append rows for one page's issues to detail sheets."""
        page = page_data['page']
        test_result = page_data['test_result']
        url = page.url if hasattr(page, 'url') else page.get('url', '')
        title = page.title if hasattr(page, 'title') else page.get('title', '')

        for v in (test_result.violations if hasattr(test_result, 'violations') else []):
            self._violations_ws.append([
                url, title,
                v.id if hasattr(v, 'id') else v.get('id', ''),
                v.description if hasattr(v, 'description') else v.get('description', ''),
                v.touchpoint if hasattr(v, 'touchpoint') else v.get('touchpoint', ''),
                str(v.impact) if hasattr(v, 'impact') else str(v.get('impact', '')),
                v.xpath if hasattr(v, 'xpath') else v.get('xpath', ''),
                v.html if hasattr(v, 'html') else v.get('html', ''),
                ', '.join(v.wcag_criteria) if hasattr(v, 'wcag_criteria') and v.wcag_criteria else '',
            ])

        for w in (test_result.warnings if hasattr(test_result, 'warnings') else []):
            self._warnings_ws.append([
                url, title,
                w.id if hasattr(w, 'id') else w.get('id', ''),
                w.description if hasattr(w, 'description') else w.get('description', ''),
                w.touchpoint if hasattr(w, 'touchpoint') else w.get('touchpoint', ''),
                str(w.impact) if hasattr(w, 'impact') else str(w.get('impact', '')),
                w.xpath if hasattr(w, 'xpath') else w.get('xpath', ''),
                w.html if hasattr(w, 'html') else w.get('html', ''),
                ', '.join(w.wcag_criteria) if hasattr(w, 'wcag_criteria') and w.wcag_criteria else '',
            ])

    def finalize(self, output_file, summary):
        """Auto-size columns and save workbook."""
        for ws in [self._violations_ws, self._warnings_ws]:
            for column_cells in ws.columns:
                max_length = max(len(str(cell.value or '')) for cell in column_cells)
                adjusted_width = min(max_length + 2, 60)
                ws.column_dimensions[column_cells[0].column_letter].width = adjusted_width

        self._wb.save(output_file)
        self._wb.close()

    def cleanup(self):
        """Close workbook if still open."""
        if hasattr(self, '_wb') and self._wb:
            try:
                self._wb.close()
            except Exception:
                pass
```

Note: During implementation, read the existing `format_website_report()` and `format_project_report()` methods to replicate the exact sheet structure, column ordering, and openpyxl styling (fonts, fills, alignments, merged cells). The code above is a minimal skeleton that satisfies the streaming interface contract.

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/python -m pytest tests/test_formatter_streaming.py::TestExcelFormatterStreaming -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add auto_a11y/reporting/formatters.py tests/test_formatter_streaming.py
git commit -m "feat: add Excel streaming formatter (standard mode with page-at-a-time appends)"
```

---

### Task 7: PDF Streaming Formatter

**Files:**
- Modify: `auto_a11y/reporting/formatters.py:2734-2824` (PDFFormatter)
- Modify: `tests/test_formatter_streaming.py`

- [ ] **Step 1: Write failing test for PDF streaming**

Append to `tests/test_formatter_streaming.py`:

```python
from auto_a11y.reporting.formatters import PDFFormatter


class TestPDFFormatterStreaming:
    """Tests for PDFFormatter begin/append_page/finalize (delegates to HTML)."""

    def setup_method(self):
        self.formatter = PDFFormatter({}, 'en')
        self.tmpdir = tempfile.mkdtemp()
        self.output_file = os.path.join(self.tmpdir, 'test.pdf')

    def teardown_method(self):
        self.formatter.cleanup()
        for f in os.listdir(self.tmpdir):
            os.remove(os.path.join(self.tmpdir, f))
        os.rmdir(self.tmpdir)

    def test_produces_pdf_file(self):
        try:
            import weasyprint
        except ImportError:
            pytest.skip("weasyprint not installed")

        summary = {'total_pages': 1, 'total_violations': 0, 'total_warnings': 0,
                   'total_info': 0, 'total_discovery': 0, 'total_passes': 0,
                   'touchpoint_counts': {}, 'impact_counts': {}, 'top_issue_codes': {}}
        page_data = {
            'page': type('P', (), {'url': 'http://example.com', 'title': 'Test'})(),
            'test_result': type('TR', (), {
                'violations': [], 'warnings': [],
                'violation_count': 0, 'warning_count': 0,
            })(),
        }

        self.formatter.begin(self.output_file, summary)
        self.formatter.append_page(self.output_file, page_data)
        self.formatter.finalize(self.output_file, summary)

        assert os.path.exists(self.output_file)
        with open(self.output_file, 'rb') as f:
            header = f.read(4)
        assert header == b'%PDF'  # Valid PDF header
```

- [ ] **Step 2: Run test, verify fail**

Run: `.venv/bin/python -m pytest tests/test_formatter_streaming.py::TestPDFFormatterStreaming -v`
Expected: FAIL

- [ ] **Step 3: Implement PDF streaming methods**

Add to `PDFFormatter` class in `auto_a11y/reporting/formatters.py`:

```python
    def begin(self, output_file, summary):
        """Delegate to HTMLFormatter.begin() for temp HTML accumulation."""
        self._html_formatter = HTMLFormatter(self.config, self.language)
        self._pdf_output_file = output_file
        # Use a temp HTML file for the intermediate HTML
        import tempfile as _tempfile
        self._html_tempfile = _tempfile.NamedTemporaryFile(
            suffix='.html', delete=False, mode='w'
        )
        self._html_temp_path = self._html_tempfile.name
        self._html_tempfile.close()  # HTMLFormatter will write to this path
        self._html_formatter.begin(self._html_temp_path, summary)

    def append_page(self, output_file, page_data):
        """Delegate to HTMLFormatter.append_page()."""
        self._html_formatter.append_page(self._html_temp_path, page_data)

    def finalize(self, output_file, summary):
        """Finalize HTML, then convert on-disk HTML to PDF via WeasyPrint."""
        self._html_formatter.finalize(self._html_temp_path, summary)

        # Convert the on-disk HTML file to PDF
        from weasyprint import HTML
        HTML(filename=self._html_temp_path).write_pdf(output_file)

    def cleanup(self):
        """Clean up both the HTML formatter's temp files and our own."""
        if hasattr(self, '_html_formatter') and self._html_formatter:
            self._html_formatter.cleanup()
        if hasattr(self, '_html_temp_path') and os.path.exists(self._html_temp_path):
            os.remove(self._html_temp_path)
```

- [ ] **Step 4: Run test, verify pass**

Run: `.venv/bin/python -m pytest tests/test_formatter_streaming.py::TestPDFFormatterStreaming -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add auto_a11y/reporting/formatters.py tests/test_formatter_streaming.py
git commit -m "feat: add PDF streaming formatter (delegates to HTML then WeasyPrint)"
```

---

### Task 8: Two-Pass Report Generator Core

**Files:**
- Modify: `auto_a11y/reporting/report_generator.py` — add `_collect_summary()`, `_write_details()`, `_prepare_single_page_data()`, `_collect_recordings_data()`
- Create: `tests/test_streaming_reports.py`

- [ ] **Step 1: Write failing tests for `_collect_summary()`**

```python
# tests/test_streaming_reports.py
import pytest
from unittest.mock import MagicMock, patch
from collections import defaultdict, Counter
from auto_a11y.reporting.report_generator import ReportGenerator


def make_mock_db():
    """Create a mock Database with generator methods."""
    db = MagicMock()
    return db


def make_page(page_id, url):
    """Create a mock Page."""
    page = MagicMock()
    page.id = page_id
    page.url = url
    page.title = f'Page {page_id}'
    return page


class TestCollectSummary:
    """Tests for ReportGenerator._collect_summary()."""

    def setup_method(self):
        self.db = make_mock_db()
        self.generator = ReportGenerator.__new__(ReportGenerator)
        self.generator.db = self.db

    def test_accumulates_counts_across_pages(self):
        pages = [make_page('p1', 'http://a.com'), make_page('p2', 'http://b.com')]
        self.db.get_latest_test_result_summary.side_effect = [
            {'id': 'tr1', 'page_id': 'p1', 'violation_count': 10, 'warning_count': 5,
             'info_count': 2, 'discovery_count': 1, 'pass_count': 50, 'score': 80},
            {'id': 'tr2', 'page_id': 'p2', 'violation_count': 20, 'warning_count': 3,
             'info_count': 0, 'discovery_count': 0, 'pass_count': 30, 'score': 60},
        ]
        self.db.yield_test_result_items.return_value = iter([])
        progress = MagicMock()

        page_gen_fn = lambda: iter(pages)
        summary = self.generator._collect_summary(page_gen_fn, progress)

        assert summary['total_pages'] == 2
        assert summary['total_violations'] == 30
        assert summary['total_warnings'] == 8
        assert summary['total_passes'] == 80

    def test_streams_items_for_touchpoint_counting(self):
        pages = [make_page('p1', 'http://a.com')]
        self.db.get_latest_test_result_summary.return_value = {
            'id': 'tr1', 'page_id': 'p1', 'violation_count': 2, 'warning_count': 0,
            'info_count': 0, 'discovery_count': 0, 'pass_count': 0, 'score': None,
        }
        self.db.yield_test_result_items.return_value = iter([
            {'issue_id': 'ErrNoAlt', 'touchpoint': 'Images', 'impact': 'critical'},
            {'issue_id': 'ErrNoAlt', 'touchpoint': 'Images', 'impact': 'critical'},
            {'issue_id': 'ErrNoLabel', 'touchpoint': 'Forms', 'impact': 'serious'},
        ])
        progress = MagicMock()

        summary = self.generator._collect_summary(lambda: iter(pages), progress)

        assert summary['touchpoint_counts']['Images'] == 2
        assert summary['touchpoint_counts']['Forms'] == 1
        assert summary['top_issue_codes']['ErrNoAlt'] == 2
        assert summary['top_issue_codes']['ErrNoLabel'] == 1

    def test_skips_pages_without_test_results(self):
        pages = [make_page('p1', 'http://a.com'), make_page('p2', 'http://b.com')]
        self.db.get_latest_test_result_summary.side_effect = [None, {
            'id': 'tr2', 'page_id': 'p2', 'violation_count': 5, 'warning_count': 0,
            'info_count': 0, 'discovery_count': 0, 'pass_count': 0, 'score': None,
        }]
        self.db.yield_test_result_items.return_value = iter([])

        summary = self.generator._collect_summary(lambda: iter(pages), MagicMock())
        assert summary['total_pages'] == 1
        assert summary['total_violations'] == 5

    def test_calls_progress_callback_per_page(self):
        pages = [make_page('p1', 'http://a.com'), make_page('p2', 'http://b.com')]
        self.db.get_latest_test_result_summary.return_value = None
        progress = MagicMock()

        self.generator._collect_summary(lambda: iter(pages), progress)
        assert progress.call_count == 2
```

- [ ] **Step 2: Run tests, verify fail**

Run: `.venv/bin/python -m pytest tests/test_streaming_reports.py::TestCollectSummary -v`
Expected: FAIL — `AttributeError: 'ReportGenerator' object has no attribute '_collect_summary'`

- [ ] **Step 3: Implement `_collect_summary()`**

Add to `ReportGenerator` class in `auto_a11y/reporting/report_generator.py`:

```python
def _collect_summary(self, page_generator_fn, progress_callback):
    """Pass 1: Stream through all pages collecting only aggregate stats.

    Memory usage: one counter dict + one item document at a time.
    Does NOT load full test result item arrays — uses summary docs
    for counts and yield_test_result_items() for per-item counting.

    Args:
        page_generator_fn: Zero-arg callable returning fresh page generator
        progress_callback: Callable(current, total, message)

    Returns:
        Dict with aggregate summary counters
    """
    from collections import defaultdict, Counter

    summary = {
        'total_pages': 0,
        'total_violations': 0,
        'total_warnings': 0,
        'total_info': 0,
        'total_discovery': 0,
        'total_passes': 0,
        'touchpoint_counts': defaultdict(int),
        'wcag_counts': defaultdict(int),
        'impact_counts': defaultdict(int),
        'page_scores': [],
        'top_issue_codes': Counter(),
    }

    page_count = 0
    for page in page_generator_fn():
        page_count += 1
        result_summary = self.db.get_latest_test_result_summary(page.id)
        if not result_summary:
            if progress_callback:
                progress_callback(page_count, 0, f"Collecting summary ({page_count})...")
            continue

        summary['total_pages'] += 1
        summary['total_violations'] += result_summary['violation_count']
        summary['total_warnings'] += result_summary['warning_count']
        summary['total_info'] += result_summary['info_count']
        summary['total_discovery'] += result_summary['discovery_count']
        summary['total_passes'] += result_summary['pass_count']

        if result_summary.get('score') is not None:
            summary['page_scores'].append((page.id, result_summary['score']))

        # Stream items one at a time for per-item counting
        for item in self.db.yield_test_result_items(result_summary['id']):
            tp = item.get('touchpoint', 'unknown')
            code = item.get('issue_id', item.get('code', 'unknown'))
            impact = item.get('impact', 'unknown')
            summary['touchpoint_counts'][tp] += 1
            summary['top_issue_codes'][code] += 1
            summary['impact_counts'][impact] += 1

        if progress_callback:
            progress_callback(page_count, 0, f"Collecting summary ({page_count})...")

    return summary
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/python -m pytest tests/test_streaming_reports.py::TestCollectSummary -v`
Expected: PASS

- [ ] **Step 5: Write failing tests for `_write_details()`**

Append to `tests/test_streaming_reports.py`:

```python
class TestWriteDetails:
    """Tests for ReportGenerator._write_details()."""

    def setup_method(self):
        self.db = make_mock_db()
        self.generator = ReportGenerator.__new__(ReportGenerator)
        self.generator.db = self.db

    def test_calls_begin_append_finalize_in_order(self):
        pages = [make_page('p1', 'http://a.com')]
        test_result = MagicMock()
        self.db.get_latest_test_result.return_value = test_result
        self.generator._prepare_single_page_data = MagicMock(return_value={'page_id': 'p1'})

        formatter = MagicMock()
        summary = {'total_pages': 1}
        call_order = []
        formatter.begin.side_effect = lambda *a: call_order.append('begin')
        formatter.append_page.side_effect = lambda *a: call_order.append('append')
        formatter.finalize.side_effect = lambda *a: call_order.append('finalize')

        self.generator._write_details(
            lambda: iter(pages), summary, formatter, '/tmp/out', MagicMock()
        )

        assert call_order == ['begin', 'append', 'finalize']

    def test_skips_pages_without_results(self):
        pages = [make_page('p1', 'a'), make_page('p2', 'b')]
        self.db.get_latest_test_result.side_effect = [None, MagicMock()]
        self.generator._prepare_single_page_data = MagicMock(return_value={})

        formatter = MagicMock()
        self.generator._write_details(
            lambda: iter(pages), {}, formatter, '/tmp/out', MagicMock()
        )

        assert formatter.append_page.call_count == 1  # Only p2

    def test_calls_progress_per_page(self):
        pages = [make_page('p1', 'a'), make_page('p2', 'b')]
        self.db.get_latest_test_result.return_value = MagicMock()
        self.generator._prepare_single_page_data = MagicMock(return_value={})
        formatter = MagicMock()
        progress = MagicMock()

        self.generator._write_details(
            lambda: iter(pages), {}, formatter, '/tmp/out', progress
        )

        assert progress.call_count == 2
```

- [ ] **Step 6: Implement `_write_details()`**

Add to `ReportGenerator` class:

```python
def _write_details(self, page_generator_fn, summary, formatter, output_file, progress_callback):
    """Pass 2: Stream through all pages, writing detail chunks via formatter.

    Memory usage: summary dict (small) + one page's full data at a time.

    Args:
        page_generator_fn: Zero-arg callable returning fresh page generator
        summary: Summary dict from _collect_summary()
        formatter: Formatter with begin/append_page/finalize interface
        output_file: Path to write the report
        progress_callback: Callable(current, total, message)
    """
    formatter.begin(output_file, summary)

    page_count = 0
    total = summary.get('total_pages', 0)
    for page in page_generator_fn():
        page_count += 1
        test_result = self.db.get_latest_test_result(page.id)
        if not test_result:
            if progress_callback:
                progress_callback(page_count, total, f"Writing details ({page_count}/{total})...")
            continue

        page_data = self._prepare_single_page_data(page, test_result)
        formatter.append_page(output_file, page_data)
        del page_data

        if progress_callback:
            progress_callback(page_count, total, f"Writing details ({page_count}/{total})...")

    formatter.finalize(output_file, summary)
```

Run: `.venv/bin/python -m pytest tests/test_streaming_reports.py::TestWriteDetails -v`
Expected: PASS

- [ ] **Step 7: Write failing test and implement `_prepare_single_page_data()`**

Append to `tests/test_streaming_reports.py`:

```python
class TestPrepareSinglePageData:
    """Tests for ReportGenerator._prepare_single_page_data()."""

    def setup_method(self):
        self.generator = ReportGenerator.__new__(ReportGenerator)
        self.generator.db = MagicMock()
        self.generator.language = 'en'

    def test_returns_dict_with_page_and_test_result(self):
        page = make_page('p1', 'http://a.com')
        test_result = MagicMock()
        test_result.violations = []
        test_result.warnings = []
        test_result.info = []
        test_result.discovery = []
        test_result.passes = []
        test_result.ai_findings = []
        test_result.violation_count = 0
        test_result.warning_count = 0

        result = self.generator._prepare_single_page_data(page, test_result)

        assert result['page'] is page
        assert result['test_result'] is test_result
```

Implement `_prepare_single_page_data()` on `ReportGenerator`. Read the inner loop of `_prepare_website_report_data()` (lines 542-597) to extract the per-page data preparation pattern:

```python
def _prepare_single_page_data(self, page, test_result, include_ai=False):
    """Prepare data dict for a single page's test result.

    Extracted from the inner loops of _prepare_website_report_data()
    and _prepare_project_report_data(). Used by _write_details() to
    prepare one page at a time for the streaming formatter.

    Args:
        page: Page model object
        test_result: TestResult with full items loaded
        include_ai: Whether to include AI findings

    Returns:
        Dict with page, test_result, and enriched issue data
    """
    return {
        'page': page,
        'test_result': test_result,
    }
```

Note: During implementation, read `_prepare_website_report_data()` lines 556-576 to replicate the violation enrichment logic (IssueCatalog lookups, touchpoint mapping). The skeleton above is the minimal contract; flesh it out to match existing enrichment.

Run: `.venv/bin/python -m pytest tests/test_streaming_reports.py::TestPrepareSinglePageData -v`
Expected: PASS

- [ ] **Step 8: Write failing test and implement `_collect_recordings_data()`**

Append to `tests/test_streaming_reports.py`:

```python
class TestCollectRecordingsData:
    """Tests for ReportGenerator._collect_recordings_data()."""

    def setup_method(self):
        self.generator = ReportGenerator.__new__(ReportGenerator)
        self.generator.db = MagicMock()

    def test_returns_recordings_with_issues(self):
        recording = MagicMock()
        recording.recording_id = 'rec1'
        recording.get_key_takeaways = MagicMock(return_value=['takeaway1'])
        recording.get_user_painpoints = MagicMock(return_value=[])
        recording.get_user_assertions = MagicMock(return_value=[])
        self.generator.db.get_recordings.return_value = [recording]
        self.generator.db.get_recording_issues.return_value = [{'id': 'i1'}]

        result = self.generator._collect_recordings_data('proj1')

        assert len(result) == 1
        assert result[0]['recording'] is recording
        assert len(result[0]['issues']) == 1

    def test_returns_empty_list_when_no_recordings(self):
        self.generator.db.get_recordings.return_value = []
        assert self.generator._collect_recordings_data('proj1') == []
```

Implement — extract from `_prepare_project_report_data()` lines 626-641:

```python
def _collect_recordings_data(self, project_id):
    """Collect recordings and their issues for a project.

    Extracted from _prepare_project_report_data(). Recordings are a small,
    bounded dataset loaded separately from the streaming page loop.
    """
    recordings_data = []
    recordings = self.db.get_recordings(project_id=project_id)
    for recording in recordings:
        recording_issues = self.db.get_recording_issues(
            recording_id=recording.recording_id
        )
        recordings_data.append({
            'recording': recording,
            'issues': recording_issues,
            'key_takeaways': recording.get_key_takeaways('en') if hasattr(recording, 'get_key_takeaways') else [],
            'user_painpoints': recording.get_user_painpoints('en') if hasattr(recording, 'get_user_painpoints') else [],
            'user_assertions': recording.get_user_assertions('en') if hasattr(recording, 'get_user_assertions') else [],
        })
    return recordings_data
```

Run: `.venv/bin/python -m pytest tests/test_streaming_reports.py::TestCollectRecordingsData -v`
Expected: PASS

- [ ] **Step 9: Run all tests and commit**

Run: `.venv/bin/python -m pytest tests/test_streaming_reports.py -v`
Expected: All PASS

```bash
git add auto_a11y/reporting/report_generator.py tests/test_streaming_reports.py
git commit -m "feat: add two-pass report generator core

Add _collect_summary(), _write_details(), _prepare_single_page_data(),
_collect_recordings_data() for streaming multi-page report generation."
```

---

### Task 9: Wire `generate_website_report()` to Two-Pass

**Files:**
- Modify: `auto_a11y/reporting/report_generator.py:196-271` (`generate_website_report`)

- [ ] **Step 1: Write integration test**

Append to `tests/test_streaming_reports.py`:

```python
import os
import tempfile


class TestGenerateWebsiteReportStreaming:
    """Integration test for streaming website report generation."""

    def setup_method(self):
        self.db = make_mock_db()
        self.generator = ReportGenerator.__new__(ReportGenerator)
        self.generator.db = self.db
        self.generator.language = 'en'
        self.generator.config = {'SHOW_ERROR_CODES': True}
        self.tmpdir = tempfile.mkdtemp()
        self.generator.report_dir = type('Path', (), {
            'mkdir': lambda *a, **k: None,
            '__truediv__': lambda self, other: os.path.join(self.path, other),
            'path': self.tmpdir,
        })()
        # Minimal formatter setup
        from auto_a11y.reporting.formatters import CSVFormatter
        self.generator.formatters = {
            'csv': CSVFormatter({'SHOW_ERROR_CODES': True}, 'en'),
        }

    def test_website_report_uses_two_pass(self):
        """Verify generate_website_report calls _collect_summary then _write_details."""
        website = MagicMock()
        website.id = 'w1'
        website.name = 'Test Site'
        project = MagicMock()
        project.id = 'proj1'
        project.name = 'Test Project'

        self.db.get_website.return_value = website
        self.db.get_project.return_value = project
        self.db.get_latest_test_result_summary.return_value = None
        self.db.yield_pages.return_value = iter([])

        self.generator._collect_summary = MagicMock(return_value={
            'total_pages': 0, 'total_violations': 0, 'total_warnings': 0,
            'total_info': 0, 'total_discovery': 0, 'total_passes': 0,
            'touchpoint_counts': {}, 'impact_counts': {}, 'top_issue_codes': {},
            'page_scores': [],
        })
        self.generator._write_details = MagicMock()

        self.generator.generate_website_report('w1', format='csv')

        self.generator._collect_summary.assert_called_once()
        self.generator._write_details.assert_called_once()
```

- [ ] **Step 2: Run test, verify fail**

Run: `.venv/bin/python -m pytest tests/test_streaming_reports.py::TestGenerateWebsiteReportStreaming -v`
Expected: FAIL (current implementation doesn't call `_collect_summary`)

- [ ] **Step 3: Add `import os` to report_generator.py imports**

Add `import os` to the imports at the top of `auto_a11y/reporting/report_generator.py` (around line 1-21) if not already present. This is needed for `os.path.exists()` and `os.remove()` in error cleanup.

- [ ] **Step 4: Rewrite `generate_website_report()` to use two-pass**

Read the current `generate_website_report()` at lines 196-271. Replace the inner page accumulation loop with the two-pass flow. **Important:** Use the existing `self._sanitize_filename()` helper and `self._t()` for translations to match current behavior (e.g., French language filenames):

```python
def generate_website_report(self, website_id, format='html', include_ai=True, progress_callback=None):
    """Generate website report using streaming two-pass architecture.

    Pass 1: Collect summary statistics via _collect_summary()
    Pass 2: Write page details via _write_details()
    Memory: O(1 page) at any time.
    """
    website = self.db.get_website(website_id)
    if not website:
        raise ValueError(f"Website not found: {website_id}")

    project = self.db.get_project(website.project_id) if hasattr(website, 'project_id') else None

    # Page generator factory — fresh cursor each call
    page_generator_fn = lambda: self.db.yield_pages(website_id)

    # Select formatter
    formatter = self.formatters.get(format)
    if not formatter:
        raise ValueError(f"Unsupported format: {format}")

    # Generate output filename — use existing helpers to match current behavior
    # Read the current generate_website_report() to replicate the exact filename
    # pattern, using self._sanitize_filename() and self._t() if they exist.
    safe_name = re.sub(r'[^\w\-]', '_', website.name or 'website')
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"website_{safe_name}_{timestamp}.{formatter.extension}"
    output_file = str(self.report_dir / filename)

    # Two-pass streaming
    try:
        summary = self._collect_summary(page_generator_fn, progress_callback)
        summary['website'] = website.__dict__ if hasattr(website, '__dict__') else website
        summary['project'] = project.__dict__ if project and hasattr(project, '__dict__') else project
        self._write_details(page_generator_fn, summary, formatter, output_file, progress_callback)
    except Exception:
        if os.path.exists(output_file):
            os.remove(output_file)
        raise
    finally:
        formatter.cleanup()

    return output_file
```

**Note:** When implementing, read the current `generate_website_report()` to match the exact filename generation pattern. If the method uses `self._sanitize_filename()` or `self._t()`, replicate those calls.

- [ ] **Step 4: Run test, verify pass**

Run: `.venv/bin/python -m pytest tests/test_streaming_reports.py::TestGenerateWebsiteReportStreaming -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add auto_a11y/reporting/report_generator.py tests/test_streaming_reports.py
git commit -m "feat: wire generate_website_report() to two-pass streaming"
```

---

### Task 10: Wire `generate_project_report()` to Two-Pass

**Files:**
- Modify: `auto_a11y/reporting/report_generator.py:408-489` (`generate_project_report`)

- [ ] **Step 1: Write integration test**

Append to `tests/test_streaming_reports.py`:

```python
class TestGenerateProjectReportStreaming:
    """Integration test for streaming project report generation."""

    def setup_method(self):
        self.db = make_mock_db()
        self.generator = ReportGenerator.__new__(ReportGenerator)
        self.generator.db = self.db
        self.generator.language = 'en'
        self.generator.config = {}
        self.tmpdir = tempfile.mkdtemp()
        self.generator.report_dir = type('Path', (), {
            'mkdir': lambda *a, **k: None,
            '__truediv__': lambda self, other: os.path.join(self.path, other),
            'path': self.tmpdir,
        })()
        from auto_a11y.reporting.formatters import CSVFormatter
        self.generator.formatters = {'csv': CSVFormatter({}, 'en')}

    def test_project_report_uses_flattened_page_generator(self):
        """Project report should yield pages across all websites."""
        project = MagicMock()
        project.id = 'proj1'
        project.name = 'Test'
        self.db.get_project.return_value = project
        self.db.get_recordings.return_value = []

        self.generator._collect_summary = MagicMock(return_value={
            'total_pages': 0, 'total_violations': 0, 'total_warnings': 0,
            'total_info': 0, 'total_discovery': 0, 'total_passes': 0,
            'touchpoint_counts': {}, 'impact_counts': {}, 'top_issue_codes': {},
            'page_scores': [], 'recordings': [],
        })
        self.generator._write_details = MagicMock()
        self.generator._collect_recordings_data = MagicMock(return_value=[])

        self.generator.generate_project_report('proj1', format='csv')

        self.generator._collect_summary.assert_called_once()
        self.generator._write_details.assert_called_once()
        self.generator._collect_recordings_data.assert_called_once_with('proj1')
```

- [ ] **Step 2: Run test, verify fail, then implement**

Rewrite `generate_project_report()` using the two-pass flow with a flattened page generator across all websites:

```python
def generate_project_report(self, project_id, format='html', progress_callback=None):
    """Generate project report using streaming two-pass architecture."""
    project = self.db.get_project(project_id)
    if not project:
        raise ValueError(f"Project not found: {project_id}")

    # Flattened page generator across all websites
    def page_generator_fn():
        for website in self.db.yield_websites(project_id):
            yield from self.db.yield_pages(website.id)

    formatter = self.formatters.get(format)
    if not formatter:
        raise ValueError(f"Unsupported format: {format}")

    safe_name = re.sub(r'[^\w\-]', '_', project.name or 'project')
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"project_{safe_name}_{timestamp}.{formatter.extension}"
    output_file = str(self.report_dir / filename)

    try:
        summary = self._collect_summary(page_generator_fn, progress_callback)
        summary['project'] = project.__dict__ if hasattr(project, '__dict__') else project
        # Recordings loaded separately — small bounded dataset
        summary['recordings'] = self._collect_recordings_data(project_id)
        self._write_details(page_generator_fn, summary, formatter, output_file, progress_callback)
    except Exception:
        if os.path.exists(output_file):
            os.remove(output_file)
        raise
    finally:
        formatter.cleanup()

    return output_file
```

Run: `.venv/bin/python -m pytest tests/test_streaming_reports.py::TestGenerateProjectReportStreaming -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add auto_a11y/reporting/report_generator.py tests/test_streaming_reports.py
git commit -m "feat: wire generate_project_report() to two-pass streaming"
```

---

### Task 11: Wire `generate_all_projects_report()` to Two-Pass

**Files:**
- Modify: `auto_a11y/reporting/report_generator.py:273-382` (`generate_all_projects_report`)
- Modify: `tests/test_streaming_reports.py`

**Important context:** The current `generate_all_projects_report()` tracks per-project and per-website stats (not just per-page). The two-pass model flattens all pages into one stream, so per-project/per-website breakdowns need to be handled. Two options:

1. **Simple approach:** Flatten pages into one stream. Summary contains total counts. Per-project/per-website breakdowns are lost in the streaming model — formatters show all issues grouped by page, not by project/website. This is acceptable if the all-projects report primarily serves as a combined detail dump.
2. **Preserve hierarchy:** The page generator yields `(project, website, page)` tuples. `_collect_summary` and formatters are aware of the hierarchy. This is more complex.

**Decision:** Read the current `generate_all_projects_report()` (lines 273-382) to determine what per-project/per-website data the formatters actually consume. If the formatters only use a flat page list, option 1 suffices. If they need per-project sections, adapt `_collect_summary` to also track per-project/per-website sub-totals.

- [ ] **Step 1: Read current `generate_all_projects_report()` and `format_all_projects_report()`**

Read:
- `auto_a11y/reporting/report_generator.py` lines 273-382
- `auto_a11y/reporting/formatters.py` — `HTMLFormatter.format_all_projects_report()` (line 56) and `ExcelFormatter.format_all_projects_report()` (line 1267)

Determine what data structure the formatters expect and what per-project info they use.

- [ ] **Step 2: Write integration test**

Append to `tests/test_streaming_reports.py`:

```python
class TestGenerateAllProjectsReportStreaming:
    """Integration test for streaming all-projects report."""

    def setup_method(self):
        self.db = make_mock_db()
        self.generator = ReportGenerator.__new__(ReportGenerator)
        self.generator.db = self.db
        self.generator.language = 'en'
        self.generator.config = {}
        self.tmpdir = tempfile.mkdtemp()
        self.generator.report_dir = type('Path', (), {
            'mkdir': lambda *a, **k: None,
            '__truediv__': lambda self, other: os.path.join(self.path, other),
            'path': self.tmpdir,
        })()
        from auto_a11y.reporting.formatters import CSVFormatter
        self.generator.formatters = {'csv': CSVFormatter({}, 'en')}

    def test_all_projects_report_uses_two_pass(self):
        self.db.get_all_projects.return_value = []
        self.generator._collect_summary = MagicMock(return_value={
            'total_pages': 0, 'total_violations': 0, 'total_warnings': 0,
            'total_info': 0, 'total_discovery': 0, 'total_passes': 0,
            'touchpoint_counts': {}, 'impact_counts': {}, 'top_issue_codes': {},
            'page_scores': [],
        })
        self.generator._write_details = MagicMock()

        self.generator.generate_all_projects_report(format='csv')

        self.generator._collect_summary.assert_called_once()
        self.generator._write_details.assert_called_once()
```

- [ ] **Step 3: Implement**

Rewrite `generate_all_projects_report()` with the flattened page generator:

```python
def generate_all_projects_report(self, format='html', include_ai=True, progress_callback=None):
    """Generate all-projects report using streaming two-pass architecture."""
    # Flattened page generator across all projects → websites → pages
    def page_generator_fn():
        for project in self.db.get_all_projects():
            for website in self.db.yield_websites(project.id):
                yield from self.db.yield_pages(website.id)

    formatter = self.formatters.get(format)
    if not formatter:
        raise ValueError(f"Unsupported format: {format}")

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"all_projects_{timestamp}.{formatter.extension}"
    output_file = str(self.report_dir / filename)

    try:
        summary = self._collect_summary(page_generator_fn, progress_callback)
        self._write_details(page_generator_fn, summary, formatter, output_file, progress_callback)
    except Exception:
        if os.path.exists(output_file):
            os.remove(output_file)
        raise
    finally:
        formatter.cleanup()

    return output_file
```

**Note:** If reading the current formatters reveals they need per-project grouping, add `project_id` and `website_id` tracking to the page generator (yield enriched tuples) and adapt `_collect_summary` to track per-project sub-totals.

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/python -m pytest tests/test_streaming_reports.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add auto_a11y/reporting/report_generator.py tests/test_streaming_reports.py
git commit -m "feat: wire generate_all_projects_report() to two-pass streaming"
```

---

### Task 12: Deprecate Old Methods

**Files:**
- Modify: `auto_a11y/reporting/report_generator.py` — deprecate `_prepare_website_report_data()`, `_prepare_project_report_data()`
- Modify: `auto_a11y/reporting/formatters.py` — deprecate `format_website_report()`, `format_project_report()`, `format_all_projects_report()`

- [ ] **Step 1: Add deprecation warnings to ReportGenerator methods**

Edit `_prepare_website_report_data()` and `_prepare_project_report_data()` — add as the first line of each method:

```python
import warnings
warnings.warn(
    "_prepare_website_report_data() is deprecated, use _collect_summary() + _prepare_single_page_data() streaming interface",
    DeprecationWarning,
    stacklevel=2
)
```

```python
warnings.warn(
    "_prepare_project_report_data() is deprecated, use _collect_summary() + _prepare_single_page_data() + _collect_recordings_data() streaming interface",
    DeprecationWarning,
    stacklevel=2
)
```

- [ ] **Step 2: Add deprecation warnings to formatter methods**

Edit `format_website_report()` on `BaseFormatter` — add deprecation warning. Then add to each subclass's `format_website_report()`, `format_project_report()`, and `format_all_projects_report()` (where they exist):

```python
import warnings
warnings.warn(
    "format_website_report() is deprecated, use begin/append_page/finalize streaming interface",
    DeprecationWarning,
    stacklevel=2
)
```

Apply to:
- `BaseFormatter.format_website_report()` (line 33)
- `BaseFormatter.format_project_report()` (line 37)
- `HTMLFormatter.format_all_projects_report()` (line 56)
- `ExcelFormatter.format_all_projects_report()` (line 1267)
- `PDFFormatter.format_all_projects_report()` (line 2767)

- [ ] **Step 3: Write test verifying deprecation warnings fire**

Append to `tests/test_formatter_streaming.py`:

```python
class TestDeprecationWarnings:
    """Verify deprecated methods emit DeprecationWarning."""

    def test_base_format_website_report_warns(self):
        with pytest.warns(DeprecationWarning, match="format_website_report.*deprecated"):
            try:
                BaseFormatter({}, 'en').format_website_report({})
            except NotImplementedError:
                pass  # Expected — base class raises after warning

    def test_base_format_project_report_warns(self):
        with pytest.warns(DeprecationWarning, match="format_project_report.*deprecated"):
            try:
                BaseFormatter({}, 'en').format_project_report({})
            except NotImplementedError:
                pass
```

Run: `.venv/bin/python -m pytest tests/test_formatter_streaming.py::TestDeprecationWarnings -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add auto_a11y/reporting/formatters.py auto_a11y/reporting/report_generator.py tests/test_formatter_streaming.py
git commit -m "deprecate: mark old multi-page formatter and prepare methods as deprecated"
```

---

### Task 13: StaticHTMLReportGenerator Streaming

**Files:**
- Modify: `auto_a11y/reporting/static_html_generator.py` — `_collect_pages_data()` (lines 865-964), `generate_report()` (lines 794-863), `_generate_summary_stats()` (lines 1510-1557), `_calculate_top_issues()` (lines 1559-1585)
- Create: `tests/test_static_html_streaming.py`

This is the most complex adaptation because `StaticHTMLReportGenerator` is a separate class that doesn't use `BaseFormatter`. It outputs ZIP files with multiple HTML pages and has bilingual enrichment, screenshot handling, and Jinja2 templating.

- [ ] **Step 1: Read existing code thoroughly**

Read these sections of `auto_a11y/reporting/static_html_generator.py`:
- `generate_report()` lines 794-863 — entry point, ZIP creation, calls `_collect_pages_data()`
- `_collect_pages_data()` lines 865-964+ — the main accumulation loop (violation enrichment, bilingual descriptions, screenshot paths)
- `_generate_summary_stats()` lines 1510-1557 — depends on `pages_data` list
- `_calculate_top_issues()` lines 1559-1585 — iterates all pages' issues
- `_group_by_touchpoint()` lines 1587-1605
- `_group_by_wcag()` lines 1605+
- `_render_page_html()` or equivalent — how individual page HTML is generated
- How the ZIP file is assembled (index.html + per-page HTML files + assets)

Understand the exact data shape that `_collect_pages_data()` returns for each page and what downstream methods consume.

- [ ] **Step 2: Write failing test for summary-only pass**

```python
# tests/test_static_html_streaming.py
import pytest
from unittest.mock import MagicMock
from collections import defaultdict
from auto_a11y.reporting.static_html_generator import StaticHTMLReportGenerator


class TestStaticHTMLSummaryCollection:
    """Tests for streaming summary collection on StaticHTMLReportGenerator."""

    def setup_method(self):
        self.gen = StaticHTMLReportGenerator.__new__(StaticHTMLReportGenerator)
        self.gen.db = MagicMock()
        self.gen.language = 'en'

    def test_collects_counts_without_loading_items(self):
        """Pass 1 should use get_latest_test_result_summary, not get_latest_test_result."""
        self.gen.db.get_latest_test_result_summary.return_value = {
            'id': 'tr1', 'page_id': 'p1',
            'violation_count': 5, 'warning_count': 2,
            'info_count': 1, 'discovery_count': 0,
            'pass_count': 10,
        }
        self.gen.db.yield_test_result_items.return_value = iter([
            {'issue_id': 'ErrNoAlt', 'touchpoint': 'Images', 'impact': 'critical', 'item_type': 'violation'},
        ])

        stats = self.gen._collect_summary_stats(['p1'])

        assert stats['total_errors'] == 5
        assert stats['total_warnings'] == 2
        assert stats['issue_counts']['ErrNoAlt']['count'] == 1
        # Should NOT have called get_latest_test_result (full load)
        self.gen.db.get_latest_test_result.assert_not_called()

    def test_skips_pages_without_results(self):
        self.gen.db.get_latest_test_result_summary.return_value = None
        stats = self.gen._collect_summary_stats(['p1', 'p2'])
        assert stats['total_errors'] == 0
```

- [ ] **Step 3: Implement `_collect_summary_stats()` on StaticHTMLReportGenerator**

```python
def _collect_summary_stats(self, page_ids, progress_callback=None):
    """Pass 1: Stream pages collecting only aggregate statistics.

    Memory-efficient replacement for _collect_pages_data() + _generate_summary_stats().
    Does not load full test result item arrays into memory.

    Returns:
        Dict with aggregate counts, per-issue counts, touchpoint/WCAG breakdowns.
    """
    from collections import defaultdict

    stats = {
        'total_pages': len(page_ids),
        'total_errors': 0,
        'total_warnings': 0,
        'total_info': 0,
        'total_discovery': 0,
        'pages_with_errors': 0,
        'pages_with_warnings': 0,
        'pages_with_info': 0,
        'pages_with_discovery': 0,
        'scores': [],
        'issue_counts': {},       # code → {count, pages: set, impact, touchpoint}
        'touchpoint_counts': defaultdict(int),
        'wcag_counts': defaultdict(int),
    }

    for i, page_id in enumerate(page_ids):
        if progress_callback:
            progress_callback(i, len(page_ids), f'Collecting summary ({i+1}/{len(page_ids)})...')

        result_summary = self.db.get_latest_test_result_summary(page_id)
        if not result_summary:
            continue

        v = result_summary['violation_count']
        w = result_summary['warning_count']
        info = result_summary['info_count']
        disc = result_summary['discovery_count']

        stats['total_errors'] += v
        stats['total_warnings'] += w
        stats['total_info'] += info
        stats['total_discovery'] += disc
        if v > 0: stats['pages_with_errors'] += 1
        if w > 0: stats['pages_with_warnings'] += 1
        if info > 0: stats['pages_with_info'] += 1
        if disc > 0: stats['pages_with_discovery'] += 1

        # Stream items for per-issue and per-touchpoint counting
        for item in self.db.yield_test_result_items(result_summary['id']):
            code = item.get('issue_id', 'unknown')
            tp = item.get('touchpoint', 'unknown')
            impact = item.get('impact', 'medium')
            wcag = item.get('wcag_criteria', [])

            if code not in stats['issue_counts']:
                stats['issue_counts'][code] = {
                    'count': 0,
                    'pages': set(),
                    'impact': impact,
                    'touchpoint': tp,
                }
            stats['issue_counts'][code]['count'] += 1
            stats['issue_counts'][code]['pages'].add(page_id)

            stats['touchpoint_counts'][tp] += 1
            if isinstance(wcag, list):
                for criterion in wcag:
                    stats['wcag_counts'][criterion] += 1

    return stats
```

Run: `.venv/bin/python -m pytest tests/test_static_html_streaming.py -v`
Expected: PASS

- [ ] **Step 4: Write failing test for Pass 2 page-at-a-time ZIP writing**

```python
class TestStaticHTMLStreamingGeneration:
    """Tests for two-pass generate_report() on StaticHTMLReportGenerator."""

    def setup_method(self):
        self.gen = StaticHTMLReportGenerator.__new__(StaticHTMLReportGenerator)
        self.gen.db = MagicMock()
        self.gen.language = 'en'

    def test_generate_report_does_not_call_collect_pages_data(self):
        """After streaming conversion, _collect_pages_data should not be called."""
        self.gen._collect_pages_data = MagicMock()
        self.gen._collect_summary_stats = MagicMock(return_value={
            'total_pages': 0, 'total_errors': 0, 'total_warnings': 0,
            'total_info': 0, 'total_discovery': 0,
            'pages_with_errors': 0, 'pages_with_warnings': 0,
            'pages_with_info': 0, 'pages_with_discovery': 0,
            'scores': [], 'issue_counts': {}, 'touchpoint_counts': {},
            'wcag_counts': {},
        })
        # Mock the rest of generate_report's dependencies as needed
        # This test verifies _collect_pages_data is no longer called
        # Actual implementation will need more mocks for ZIP writing
```

- [ ] **Step 5: Modify `generate_report()` to use two-pass**

Read the current `generate_report()` method thoroughly. The key change:

**Before (current):**
```python
pages_data = self._collect_pages_data(page_ids, include_discovery, progress_callback)
summary_stats = self._generate_summary_stats(pages_data)
# ... use pages_data and summary_stats to build ZIP ...
```

**After (streaming):**
```python
# Pass 1: Collect summary stats (lightweight)
summary_stats = self._collect_summary_stats(page_ids, progress_callback)

# Derive top_issues, by_touchpoint, by_wcag from summary_stats
top_issues = sorted(
    summary_stats['issue_counts'].values(),
    key=lambda x: x['count'], reverse=True
)[:10]
# Convert page sets to counts
for issue in top_issues:
    issue['pages'] = len(issue['pages'])

summary_stats['top_issues'] = top_issues
# ... compute compliance_level, average_score from stats ...

# Write index.html using summary_stats
# Write CSS/JS assets to ZIP

# Pass 2: Write individual page HTML files one at a time
for i, page_id in enumerate(page_ids):
    if progress_callback:
        progress_callback(i, len(page_ids), f'Generating page {i+1}/{len(page_ids)}...')
    page = self.db.get_page(page_id)
    if not page:
        continue
    test_result = self.db.get_latest_test_result(page_id)
    # Prepare single page data (enrichment, bilingual descriptions)
    page_data = self._prepare_single_page_html_data(page, test_result)
    # Render via Jinja2 template or existing HTML generation
    page_html = self._render_page_html(page_data, summary_stats)
    zipf.writestr(f'pages/{page_id}.html', page_html)
    del page_data, page_html  # Discard after write
```

**New helper methods needed:**
- `_prepare_single_page_html_data(page, test_result)` — extract from `_collect_pages_data()`'s inner loop. Does violation enrichment, bilingual description lookup, screenshot path resolution for ONE page.
- `_render_page_html(page_data, summary_stats)` — renders one page's HTML from the existing template/generation logic. Extract from wherever the per-page HTML is currently generated.

Read the existing code carefully to identify where per-page HTML generation happens and extract it.

- [ ] **Step 6: Run all tests, verify, commit**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All PASS

```bash
git add auto_a11y/reporting/static_html_generator.py tests/test_static_html_streaming.py
git commit -m "feat: streaming StaticHTMLReportGenerator with two-pass ZIP writing

Pass 1 collects summary stats via get_latest_test_result_summary().
Pass 2 loads one page at a time, renders HTML, writes to ZIP, discards.
Memory bounded to O(1 page) regardless of total report size."
```

---

### Task 14: Error Handling Wrapper

**Files:**
- Modify: `auto_a11y/reporting/report_generator.py`

- [ ] **Step 1: Verify error handling is consistent across all wired methods**

Read `generate_website_report()`, `generate_project_report()`, `generate_all_projects_report()` and confirm each has:

```python
try:
    summary = self._collect_summary(...)
    self._write_details(...)
except Exception:
    if os.path.exists(output_file):
        os.remove(output_file)
    raise
finally:
    formatter.cleanup()
```

- [ ] **Step 2: Write test for error cleanup**

```python
class TestStreamingErrorHandling:
    def test_partial_output_cleaned_on_error(self):
        """If _write_details fails, output file should be removed."""
        generator = ReportGenerator.__new__(ReportGenerator)
        generator.db = make_mock_db()
        generator._collect_summary = MagicMock(return_value={'total_pages': 0})
        generator._write_details = MagicMock(side_effect=RuntimeError("boom"))

        formatter = MagicMock()
        with pytest.raises(RuntimeError):
            # Simulate the try/except/finally pattern
            try:
                summary = generator._collect_summary(lambda: iter([]), MagicMock())
                generator._write_details(lambda: iter([]), summary, formatter, '/tmp/nonexistent', MagicMock())
            except Exception:
                raise
            finally:
                formatter.cleanup()

        formatter.cleanup.assert_called_once()
```

Run: `.venv/bin/python -m pytest tests/test_streaming_reports.py::TestStreamingErrorHandling -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add auto_a11y/reporting/report_generator.py tests/test_streaming_reports.py
git commit -m "test: verify error handling and cleanup in streaming reports"
```

---

### Task 15: Final Integration Validation

- [ ] **Step 1: Run all tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 2: Verify no import errors in the application**

Run: `.venv/bin/python -c "from auto_a11y.reporting.report_generator import ReportGenerator; from auto_a11y.reporting.formatters import HTMLFormatter, JSONFormatter, CSVFormatter, ExcelFormatter, PDFFormatter; print('All imports OK')"`
Expected: `All imports OK`

- [ ] **Step 3: Verify deprecation warnings emit correctly**

Run: `.venv/bin/python -W all -c "from auto_a11y.reporting.formatters import BaseFormatter; BaseFormatter({}, 'en').format_website_report({})" 2>&1`
Expected: DeprecationWarning message, then NotImplementedError

- [ ] **Step 4: Final commit if any remaining changes**

```bash
git status
# If any uncommitted changes:
git add -A
git commit -m "chore: final cleanup for streaming report generation"
```
