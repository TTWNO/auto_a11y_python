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
def yield_pages(self, website_id, **kwargs):
    """Yields Page objects one at a time from cursor."""
    cursor = self.pages.find(query).sort(...)
    for doc in cursor:
        yield Page.from_dict(doc)

def yield_websites(self, project_id):
    """Yields Website objects one at a time."""
    for doc in self.websites.find({"project_id": project_id}):
        yield Website.from_dict(doc)

def yield_test_result_items(self, test_result_id, item_type=None):
    """Yields individual test result items from cursor."""
    query = {"test_result_id": test_result_id}
    if item_type:
        query["item_type"] = item_type
    for doc in self.test_result_items.find(query):
        yield doc
```

Generator calls create fresh cursors each time, so Pass 1 and Pass 2 each get independent iteration. Report-oriented cursor calls use `no_cursor_timeout=True` to prevent timeout during long operations, paired with explicit `cursor.close()` in a finally block.

---

## 2. Two-Pass Report Generator

Multi-page reports in `auto_a11y/reporting/report_generator.py` use a new two-pass flow. Single-page reports are unchanged.

### Pass 1 — Summary Collection (lightweight)

Streams through all pages, collecting only aggregate counters. No detail arrays loaded.

```python
def _collect_summary(self, page_generator_fn, progress_callback):
    """Stream through all pages, collecting only aggregate stats."""
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
        'page_scores': [],        # list of (page_id, score) — small
        'top_issue_codes': Counter(),
    }
    for page in page_generator_fn():
        test_result = self.db.get_latest_test_result(page.id)
        if not test_result:
            continue
        summary['total_pages'] += 1
        summary['total_violations'] += test_result.violation_count
        summary['total_warnings'] += test_result.warning_count
        # ... accumulate counts from summary fields
        # Iterate items via generator for touchpoint/code counts:
        for item in self.db.yield_test_result_items(test_result.id):
            summary['touchpoint_counts'][item['touchpoint']] += 1
            summary['top_issue_codes'][item['code']] += 1
            summary['impact_counts'][item.get('impact', 'unknown')] += 1
            # Item discarded at end of loop iteration
        progress_callback(current, total, "Collecting summary...")
    return summary
```

**Memory during Pass 1:** One counter dict + one item at a time.

### Pass 2 — Detail Writing (per-page chunks)

Streams through all pages again, writing detail chunks via the formatter's append interface.

```python
def _write_details(self, page_generator_fn, summary, formatter, output_file, progress_callback):
    """Stream through all pages again, writing detail chunks."""
    formatter.begin(output_file, summary)
    for page in page_generator_fn():
        test_result = self.db.get_latest_test_result(page.id)
        if not test_result:
            continue
        page_data = self._prepare_single_page_data(page, test_result)
        formatter.append_page(output_file, page_data)
        del page_data  # Explicit discard
        progress_callback(current, total, f"Writing page {page.url}...")
    formatter.finalize(output_file, summary)
```

**Memory during Pass 2:** Summary dict (small) + one page's full data at a time.

### Page Generator Factory Pattern

A zero-arg callable that returns a fresh generator each time:

```python
page_generator_fn = lambda: self.db.yield_pages(website_id)
```

Progress callbacks fire on every page in both passes. Pass 1 shows "Collecting summary (page N/total)...", Pass 2 shows "Writing details (page N/total)...". Cancellation checks happen inside the progress callback via the existing `is_cancellation_requested()` mechanism.

---

## 3. Formatter Append Interface

Each formatter gets three new methods. Existing `format_page_report()` stays for single-page reports.

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

| Format | `begin()` | `append_page()` | `finalize()` |
|--------|-----------|-----------------|---------------|
| **HTML** | Write `<html><head>`, CSS, nav skeleton to temp body file | Append one page's section `<div>` with violations/warnings tables to temp body file | Assemble final file: header + summary dashboard (from summary stats) + body content (read from temp file in 64KB chunks) + `</html>` |
| **CSV** | Write header row to output file | Write one row per violation/warning for that page | No-op (flush) |
| **JSON** | Write `{"summary": {...}, "pages": [\n` | Write one page JSON object (with comma tracking for first entry) | Write `]}` |
| **Excel** | Create workbook in write-only mode (`openpyxl.Workbook(write_only=True)`), write summary sheet, write header rows on detail sheets | Append rows to detail sheets for that page's issues | Save workbook to output file |
| **PDF** | Delegate to HTML `begin()` | Delegate to HTML `append_page()` | HTML `finalize()` completes the temp HTML file, then WeasyPrint converts the on-disk HTML file to PDF |

**HTML two-file strategy:** `append_page()` writes to a temp body file. `finalize()` writes the real output file by streaming: header + summary section (using Pass 1 stats) + body content (read from temp file in 64KB chunks, never fully in memory) + footer. The temp body file is cleaned up by `cleanup()`.

**Excel write-only mode:** `openpyxl.Workbook(write_only=True)` flushes rows to the underlying zip stream after writing — rows are not kept in memory.

**JSON comma handling:** `append_page()` tracks whether it's the first page via a boolean flag to avoid leading comma issues.

---

## 4. Report Types — Streaming vs. Unchanged

### Gets two-pass streaming:
- `generate_website_report()` — iterates all pages in a website
- `generate_project_report()` — iterates all websites → all pages
- `generate_all_projects_report()` — iterates all projects → all websites → all pages
- `StaticHTMLGenerator.generate_report()` — same accumulation pattern
- `generate_deduplicated_report()` — special handling (see Section 4.1)
- `generate_discovery_website_report()` — iterates all pages' discovery items
- `generate_discovery_project_report()` — same, at project level
- `generate_recordings_report()` — iterates all recordings + their issues

### Stays as-is (already O(1 page)):
- `generate_page_report()` — single page, bounded
- `generate_summary_report()` — summary counts only, no detail items
- `generate_page_structure_report()` — page metadata only, no test results

### 4.1 Deduplication — Special Handling

Deduplication needs to see all issues to group by component/XPath. In the two-pass model:

- **Pass 1:** In addition to summary stats, build a dedup index — a `Counter` keyed on `(code, normalized_xpath_prefix)` (the dedup signature). This collapses duplicates during collection, so index size equals unique issue signatures, not total issues. Typically hundreds to low thousands of unique signatures.
- **Pass 2:** For each page, emit issues with a dedup count column. Issues whose signature has already been fully written can be skipped or summarized, controlled by the dedup mode.

Memory for dedup index is proportional to unique issue types, not total issues.

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

Generator methods use `no_cursor_timeout=True` for report-oriented cursors, paired with explicit `cursor.close()` in a finally block to prevent cursor leaks.

### Formatter cleanup

`cleanup()` method on `BaseFormatter` deletes any temp files (e.g., HTML body temp file). Called in the `finally` block. Default implementation is a no-op.

### No behavioral change for existing code

Single-page reports, fixture tests, and anything using the list-based methods are completely untouched.

---

## 6. Deprecations

The following methods are deprecated now with `warnings.warn(DeprecationWarning)`.

### On formatters (`auto_a11y/reporting/formatters.py`):
- `format_website_report(data)` — replaced by `begin`/`append_page`/`finalize`
- `format_project_report(data)` — same
- `format_all_projects_report(data)` — same

### On ReportGenerator (`auto_a11y/reporting/report_generator.py`):
- `_prepare_website_report_data()` — replaced by `_collect_summary` + per-page loading
- `_prepare_project_report_data()` — same
- `_prepare_all_projects_report_data()` — same

### Kept (not deprecated):
- `format_page_report(data)` — still used for single-page reports
- `format_summary_report(data)` — summary-only, no detail items
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
- **No sub-page batching** — A single page with 1K+ violations loads fully. Confirmed acceptable.
- **No format-specific library changes** — `openpyxl` write-only mode and WeasyPrint-from-file are sufficient.
- **No API changes** — Routes, job polling, download endpoints unchanged. Frontend sees no difference.
- **No schema migration** — Existing split schema supports this. No new collections or indexes.
- **No changes to single-page reports** — Already bounded.
- **No changes to fixture testing** — `test_fixtures.py` doesn't use the report generator.
- **No new dependencies** — Uses stdlib (`tempfile`, `os`, `collections`) and existing packages.

---

## 8. Memory Guarantee

After this change, memory during any report generation is bounded to:

```
O(summary_counters + 1_page_data + formatter_buffer)
```

Where:
- `summary_counters`: Fixed-size dict of integers and counters (~KB)
- `1_page_data`: One page's full test result including all violations/warnings (~MB for worst case)
- `formatter_buffer`: Format-specific overhead (Excel write-only sheet state, HTML temp file handle, etc. — ~KB)

For the worst case (10K+ pages, 1K+ violations per page), total memory stays under ~50MB regardless of report size, compared to the current unbounded accumulation that can exceed available RAM.
