# Streaming Report Generation Design

**Date:** 2026-04-02
**Status:** Approved
**Problem:** Multi-page reports (website, project, static HTML, deduplicated) build entire output in memory, causing OOM crashes in production for large sites (10K+ pages, 1K+ violations per page).
**Solution:** Two-pass architecture with generator-based DB loading that bounds memory to O(1 page) at any time.

---

## 1. Database Layer — Generator Methods

Add generator variants to `auto_a11y/core/database.py` alongside existing list-based methods. Existing methods are untouched — no breakage.

### New Methods

```python
def yield_pages(self, website_id, sort_field='url', sort_order=1, **kwargs):
    """Yields Page objects one at a time from cursor.
    Uses no_cursor_timeout to prevent timeout during long report generation."""
    query = {"website_id": website_id}
    cursor = self.pages.find(query, no_cursor_timeout=True).sort(sort_field, sort_order)
    try:
        for doc in cursor:
            yield Page.from_dict(doc)
    finally:
        cursor.close()

def yield_websites(self, project_id):
    """Yields Website objects one at a time."""
    cursor = self.websites.find({"project_id": project_id}, no_cursor_timeout=True)
    try:
        for doc in cursor:
            yield Website.from_dict(doc)
    finally:
        cursor.close()

def yield_test_result_items(self, test_result_id, item_type=None):
    """Yields individual test result items from cursor."""
    query = {"test_result_id": test_result_id}
    if item_type:
        query["item_type"] = item_type
    cursor = self.test_result_items.find(query, no_cursor_timeout=True)
    try:
        for doc in cursor:
            yield doc
    finally:
        cursor.close()
```

Generator calls create fresh cursors each time, so Pass 1 and Pass 2 each get independent iteration. All report-oriented cursors use `no_cursor_timeout=True` paired with explicit `cursor.close()` in a `finally` block to prevent leaks.

---

## 2. Two-Pass Report Generator

Multi-page reports in `auto_a11y/reporting/report_generator.py` use a new two-pass flow. Single-page reports are unchanged.

### Pass 1 — Summary Collection

Streams through all pages, collecting aggregate counters. Items are streamed one at a time via `yield_test_result_items()` — no arrays are materialized, but each item is visited individually for counting.

**Prerequisite change — `get_latest_test_result_summary()`:** The current `get_latest_test_result()` always loads full item arrays (via `_get_test_result_items()`). A new method `get_latest_test_result_summary()` is needed that returns only the summary document from the `test_results` collection without loading items from `test_result_items`. The summary document already stores `violation_count`, `warning_count`, `info_count`, `discovery_count`, and `pass_count` fields (written at database.py lines 626-630). This method returns a lightweight dict (or a new `TestResultSummary` namedtuple), NOT a full `TestResult` object — because `TestResult.violation_count` is a computed property (`len(self.violations)`) that would return 0 without loaded items.

```python
# New method on Database:
def get_latest_test_result_summary(self, page_id):
    """Get summary counts for the latest test result without loading items.
    Returns dict with stored counts, or None if no result exists."""
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
        'score': doc.get('score'),
    }
```

Item-level counting (touchpoints, codes, impacts) uses the separate `yield_test_result_items()` generator, keyed by the summary's `id`.

```python
def _collect_summary(self, page_generator_fn, progress_callback):
    """Stream through all pages, collecting only aggregate stats.

    Memory: one counter dict + one item document at a time.
    """
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
        'page_scores': [],        # list of (page_id, score) — small per-page tuple
        'top_issue_codes': Counter(),
    }
    for page in page_generator_fn():
        # Load summary doc only — no item arrays loaded
        result_summary = self.db.get_latest_test_result_summary(page.id)
        if not result_summary:
            continue
        summary['total_pages'] += 1
        summary['total_violations'] += result_summary['violation_count']
        summary['total_warnings'] += result_summary['warning_count']
        summary['total_info'] += result_summary['info_count']
        summary['total_discovery'] += result_summary['discovery_count']
        summary['total_passes'] += result_summary['pass_count']
        if result_summary.get('score') is not None:
            summary['page_scores'].append((page.id, result_summary['score']))
        # Stream items one at a time for per-item counting:
        for item in self.db.yield_test_result_items(result_summary['id']):
            summary['touchpoint_counts'][item.get('touchpoint', 'unknown')] += 1
            summary['top_issue_codes'][item.get('code', 'unknown')] += 1
            summary['impact_counts'][item.get('impact', 'unknown')] += 1
            # Item discarded at end of loop iteration — not accumulated
        progress_callback(current, total, "Collecting summary...")
    return summary
```

**Memory during Pass 1:** One summary dict of counters (~KB) + one item document at a time.

### Pass 2 — Detail Writing (per-page chunks)

Streams through all pages again, writing detail chunks via the formatter's append interface. Each page's full test result (including all items) is loaded via the existing `get_latest_test_result()` (without `summary_only`), processed, written, then discarded.

```python
def _write_details(self, page_generator_fn, summary, formatter, output_file, progress_callback):
    """Stream through all pages again, writing detail chunks.

    Memory: summary dict (small) + one page's full data at a time.
    """
    formatter.begin(output_file, summary)
    for page in page_generator_fn():
        test_result = self.db.get_latest_test_result(page.id)
        if not test_result:
            continue
        # New method — see Section 2.1
        page_data = self._prepare_single_page_data(page, test_result)
        formatter.append_page(output_file, page_data)
        del page_data  # Explicit discard
        progress_callback(current, total, f"Writing page {page.url}...")
    formatter.finalize(output_file, summary)
```

**Memory during Pass 2:** Summary dict (small) + one page's full data at a time. `get_latest_test_result()` without `summary_only` loads all items for that single page as a list — this is bounded and acceptable per the O(1 page) constraint.

### 2.1 New Method: `_prepare_single_page_data()`

This is a **new method** to be created on `ReportGenerator`. It extracts the per-page data preparation logic that currently lives inside `_prepare_website_report_data()` and `_prepare_project_report_data()`:

```python
def _prepare_single_page_data(self, page, test_result, include_ai=False):
    """Prepare data dict for a single page's test result.

    Extracted from the inner loops of _prepare_website_report_data()
    and _prepare_project_report_data(). Takes a Page and TestResult,
    returns a dict with enriched violations, warnings, scores, etc.

    Args:
        page: Page model object
        test_result: TestResult with full items loaded
        include_ai: Whether to include AI findings

    Returns:
        Dict with keys: page, test_result, violations, warnings,
        info, discovery, passes, ai_findings, score
    """
```

The existing `_prepare_page_report_data()` requires `website` and `project` parameters and returns a richer structure for single-page standalone reports. `_prepare_single_page_data()` is simpler — it prepares one page's data as a chunk within a multi-page report.

### Page Generator Factory Pattern

A zero-arg callable that returns a fresh generator each time:

```python
# Website report:
page_generator_fn = lambda: self.db.yield_pages(website_id)

# Project report — flattens websites → pages:
def project_page_generator():
    for website in self.db.yield_websites(project_id):
        yield from self.db.yield_pages(website.id)
page_generator_fn = project_page_generator

# All projects — flattens projects → websites → pages:
def all_projects_page_generator():
    for project in self.db.get_all_projects():  # small list
        for website in self.db.yield_websites(project.id):
            yield from self.db.yield_pages(website.id)
page_generator_fn = all_projects_page_generator
```

Progress callbacks fire on every page in both passes. Pass 1 shows "Collecting summary (page N/total)...", Pass 2 shows "Writing details (page N/total)...". Cancellation checks happen inside the progress callback via the existing `is_cancellation_requested()` mechanism.

### 2.2 Project Reports — Recordings Data

`_prepare_project_report_data()` currently also loads recordings and recording issues (lines 626-648 of report_generator.py). In the streaming model, recordings are loaded separately after the two-pass page loop completes — they are a small, bounded dataset (typically dozens, not thousands). The recordings data is passed to `formatter.finalize()` as part of the summary dict:

```python
# After two-pass page streaming:
summary['recordings'] = self._collect_recordings_data(project_id)
formatter.finalize(output_file, summary)
```

---

## 3. Formatter Append Interface

Each formatter gets three new methods plus a cleanup method. Existing `format_page_report()` stays for single-page reports.

### Base Class

```python
class BaseFormatter:
    # ... existing methods unchanged ...

    def begin(self, output_file, summary):
        """Write report header/preamble using summary stats."""
        raise NotImplementedError

    def append_page(self, output_file, page_data):
        """Write one page's detail section. Called once per page."""
        raise NotImplementedError

    def finalize(self, output_file, summary):
        """Write report footer/closing using final summary stats."""
        raise NotImplementedError

    def cleanup(self):
        """Delete any temp files created during streaming. Default no-op."""
        pass
```

### Per-Format Implementation

**HTML:**
- `begin(output_file, summary)`: Opens a temp body file (stored as `self._body_tempfile`). Does NOT write to the final output yet.
- `append_page(output_file, page_data)`: Appends one page's `<div>` section (violations/warnings tables) to the temp body file.
- `finalize(output_file, summary)`: Assembles the final output file by writing in order: `<html><head>` + CSS + summary dashboard (using summary stats from Pass 1) + body content (read from temp body file in 64KB chunks — never fully in memory) + `</html>`. The temp body file is then marked for cleanup.
- `cleanup()`: Deletes the temp body file.

**CSV:**
- `begin(output_file, summary)`: Opens the output file, writes the CSV header row via `csv.writer`.
- `append_page(output_file, page_data)`: Writes one row per violation/warning for that page.
- `finalize(output_file, summary)`: Flushes and closes.

**JSON:**
- `begin(output_file, summary)`: Writes `{"summary": <summary_json>, "pages": [\n` to the output file.
- `append_page(output_file, page_data)`: Writes one page's JSON object. Tracks `self._is_first_page` boolean to handle comma separation (no leading comma on first entry, comma prefix on subsequent entries).
- `finalize(output_file, summary)`: Writes `\n]}`.

**Excel:**
- `begin(output_file, summary)`: Creates a standard workbook (`openpyxl.Workbook()` — NOT write-only mode, see note below). Writes summary sheet with merged header cells and column widths. Writes header rows on detail sheets (Violations, Warnings, etc.).
- `append_page(output_file, page_data)`: Appends rows to the detail sheets for that page's issues. Rows are appended sequentially — only the current page's rows are in working memory.
- `finalize(output_file, summary)`: Saves the workbook to the output file.
- **Note on write-only mode:** The current Excel formatter uses `merge_cells()` and `column_dimensions` which are incompatible with `openpyxl` write-only mode. We use standard mode but achieve memory savings by only holding one page's data at a time during the append loop — the workbook itself accumulates rows on disk via openpyxl's internal mechanisms. For extremely large reports, the openpyxl Workbook object will still grow, but this is bounded by row count (not by Python dicts holding all violation data). If this proves insufficient for the largest reports, a future optimization can switch to `xlsxwriter` which supports streaming writes natively.

**PDF:**
- `begin()`: Delegates to HTML `begin()` (writes to temp HTML file).
- `append_page()`: Delegates to HTML `append_page()`.
- `finalize()`: HTML `finalize()` completes the temp HTML file on disk, then WeasyPrint converts the on-disk HTML file to PDF. WeasyPrint reads from the file — the full HTML is not loaded into Python memory.

---

## 4. Report Types — Streaming vs. Unchanged

### 4.1 Formatter-based reports (use `begin`/`append_page`/`finalize`):

These live on `ReportGenerator` and use the formatter system:

- `generate_website_report()` — iterates all pages in a website
- `generate_project_report()` — iterates all websites → all pages (plus recordings in finalize)
- `generate_all_projects_report()` — iterates all projects → all websites → all pages. Note: this method builds data inline (no separate `_prepare_*` method exists). The inline accumulation code is replaced by the two-pass flow.

### 4.2 Separate generator classes (get their own streaming treatment):

These are **independent classes**, not subclasses of `BaseFormatter`. Each needs its own streaming adaptation:

**`StaticHTMLReportGenerator`** (`auto_a11y/reporting/static_html_generator.py`):
- Separate class, does NOT use BaseFormatter. Outputs a ZIP file with multiple HTML files plus assets.
- Currently accumulates all pages in `_collect_pages_data()`.
- Streaming adaptation: Replace `_collect_pages_data()` with a two-pass approach. Pass 1 collects summary stats. Pass 2 iterates pages, writing each page's HTML file to the ZIP incrementally. Summary/index HTML written in finalize using Pass 1 stats.
- Methods like `_calculate_top_issues()`, `_generate_summary_stats()`, `_group_by_touchpoint()` are moved to Pass 1 with incremental counters.

**`DiscoveryReportGenerator`** (`auto_a11y/reporting/discovery_report.py`):
- Separate class. Already has a `_generate_streaming_discovery_report()` method.
- Review the existing streaming implementation and extend if needed. May already be partially safe.

**`RecordingsReportGenerator`** (`auto_a11y/reporting/recordings_report.py`):
- Separate class. Method is `generate_project_recordings_report()` (not `generate_recordings_report()`).
- Iterates recordings (not pages). Recording count is typically small (dozens), so this is lower priority for streaming. Apply generator pattern if recordings grow large.

### 4.3 Stays as-is (already bounded or low risk):

- `generate_page_report()` on `ReportGenerator` — single page, bounded by definition
- Page structure reports via `PageStructureReport` class (separate from `ReportGenerator`) — page metadata only, no test result items loaded

### 4.4 Needs streaming but is lower priority:

- `generate_summary_report()` — Currently loads all pages across all projects via `get_pages()` to read `page.violation_count`. Does NOT load test result items, so memory is proportional to page count (metadata only). For 10K+ pages this is still significant. Apply `yield_pages()` generator and accumulate counts incrementally. Lower priority since it only loads page metadata, not full test results.

### 4.5 Deduplication — Special Handling

Deduplication logic lives on `StaticHTMLReportGenerator.generate_project_deduplicated_report()` and `ExcelFormatter._extract_common_components()`. The deduplicated report route in `reports.py` delegates directly to `StaticHTMLReportGenerator`. Both classes use rich deduplication via `_extract_common_components()` and `_deduplicate_issues_by_component()`.

The current implementation builds:
- A component signature → per-page XPath mapping dict
- A `unique_issues` dict keyed on `(rule_id, component_signature_or_xpath)` with full issue metadata, page URLs, HTML samples, and counts

A simple `Counter` is insufficient. Instead, the dedup index built during Pass 1 is:

```python
dedup_index = {}  # (rule_id, component_signature) → {
                  #   'count': int,
                  #   'pages': set of page_urls,  # just URLs, not full page data
                  #   'impact': str,
                  #   'first_html_sample': str,   # keep one HTML sample
                  #   'touchpoint': str,
                  #   'wcag_criteria': list,
                  # }
```

This index grows proportional to **unique issue signatures** (typically hundreds to low thousands), not total issues. Each entry stores lightweight metadata (URLs, one HTML sample, counts), not full issue objects.

In Pass 2, for each page's issues, look up the dedup index to get counts and emit deduplicated rows with occurrence counts.

---

## 5. Error Handling and Safety

### Mid-stream failure

```python
try:
    summary = self._collect_summary(page_gen_fn, progress_callback)
    self._write_details(page_gen_fn, summary, formatter, output_file, progress_callback)
except Exception:
    if os.path.exists(output_file):
        os.remove(output_file)
    raise  # ReportJob catches this, marks FAILED
finally:
    formatter.cleanup()
```

### Cancellation

Cancellation is checked inside `progress_callback` via `is_cancellation_requested()`. When cancelled, `ReportCancelled` is raised, caught by `ReportJob`, job marked FAILED. Same cleanup path.

### DB cursor timeout

All generator methods use `no_cursor_timeout=True`, paired with explicit `cursor.close()` in a `finally` block to prevent cursor leaks.

### Formatter cleanup

`cleanup()` method on `BaseFormatter` deletes any temp files (e.g., HTML body temp file). Called in the `finally` block. Default implementation is a no-op.

### No behavioral change for existing code

Single-page reports, fixture tests, and anything using the list-based methods are completely untouched.

---

## 6. Deprecations

The following methods are deprecated now with `warnings.warn(DeprecationWarning)`.

### On formatters (`auto_a11y/reporting/formatters.py`):

- `format_website_report(data)` on `BaseFormatter` and all subclasses — replaced by `begin`/`append_page`/`finalize`
- `format_project_report(data)` on `BaseFormatter` and all subclasses — same
- `format_all_projects_report(data)` on `HTMLFormatter`, `ExcelFormatter`, and `PDFFormatter` only (does not exist on `BaseFormatter`, `JSONFormatter`, or `CSVFormatter`) — same

### On ReportGenerator (`auto_a11y/reporting/report_generator.py`):

- `_prepare_website_report_data()` — replaced by `_collect_summary` + `_prepare_single_page_data` per page
- `_prepare_project_report_data()` — same (note: this method also loads recordings; recordings loading moves to a separate `_collect_recordings_data()` call)

Note: `generate_all_projects_report()` builds data inline (no separate `_prepare_*` method exists). The inline accumulation code is replaced by the two-pass flow; there is no method to deprecate.

### Kept (not deprecated):

- `format_page_report(data)` — still used for single-page reports
- `format_summary_report(data)` — summary-only
- `_prepare_page_report_data()` — single page, bounded

### Deprecation pattern:

```python
import warnings
warnings.warn(
    "format_website_report() is deprecated, use begin/append_page/finalize streaming interface",
    DeprecationWarning,
    stacklevel=2
)
```

---

## 7. Scope Boundaries — What We're NOT Doing

- **No queue system** — Existing `ThreadPoolExecutor(max_workers=5)` stays. Fixing memory, not concurrency.
- **No sub-page batching** — A single page with 1K+ violations loads fully into memory via `get_latest_test_result()`. Confirmed acceptable.
- **No API changes** — Routes, job polling, download endpoints unchanged. Frontend sees no difference.
- **No schema migration** — Existing split schema (summary in `test_results`, items in `test_result_items`) supports this. No new collections or indexes.
- **No changes to single-page reports** — Already bounded.
- **No changes to fixture testing** — `test_fixtures.py` doesn't use the report generator.
- **No new dependencies** — Uses stdlib (`tempfile`, `os`, `collections`) and existing packages.
- **Excel write-only deferred** — `openpyxl` write-only mode is incompatible with current `merge_cells`/`column_dimensions` usage. Standard mode with per-page data loading is the initial approach. Switch to `xlsxwriter` for true streaming if needed later.

---

## 8. Memory Guarantee

After this change, memory during any report generation is bounded to:

```
O(summary_counters + 1_page_data + formatter_state)
```

Where:
- `summary_counters`: Dict of integers, Counters, and dedup index (~KB to low MB for dedup)
- `1_page_data`: One page's full test result including all violations/warnings (~MB for worst case 1K+ items)
- `formatter_state`: Format-specific overhead — Excel workbook object grows with row count but not with Python violation dicts; HTML uses temp file on disk; CSV/JSON write directly

For the worst case (10K+ pages, 1K+ violations per page), total Python memory stays under ~50MB regardless of report size, compared to the current unbounded accumulation that can exceed available RAM.

**Caveat:** The Excel workbook object (`openpyxl` standard mode) will accumulate cell data internally. For extremely large reports (100K+ rows), this may still be significant. This is documented as a known limitation with a clear upgrade path (switch to `xlsxwriter`).
