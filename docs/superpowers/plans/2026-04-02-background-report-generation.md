# Background Report Generation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move all report generation from synchronous Flask routes to background threads with page-level progress shown inline on the reports dashboard.

**Architecture:** Routes capture Flask context into locals (including the Flask `app` object for app context in threads), create a JobManager job, submit a wrapper to the existing TaskRunner (ThreadPoolExecutor). The wrapper pushes a Flask app context (required for `force_locale` and translations), then creates a ReportJob that calls the generator with a progress_callback. The dashboard polls for status and shows a native `<progress>` element per active job.

**Tech Stack:** Flask, MongoDB (JobManager), ThreadPoolExecutor (TaskRunner), native HTML `<progress>` element, vanilla JS fetch + polling.

**Spec:** `docs/superpowers/specs/2026-04-02-background-report-generation-design.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `auto_a11y/core/report_job.py` | **New.** ReportJob class — wraps any generator call with JobManager lifecycle and progress_callback injection |
| `auto_a11y/reporting/report_generator.py` | **Modify.** Add `progress_callback` param to `generate_page_report`, `generate_website_report`, `generate_project_report`, `generate_all_projects_report` |
| `auto_a11y/reporting/discovery_report.py` | **Modify.** Add `progress_callback` param to `generate_website_discovery_report`, `generate_project_discovery_report`, thread through to `_collect_inspection_data` |
| `auto_a11y/reporting/static_html_generator.py` | **Modify.** Add `progress_callback` param to `generate_report`, `generate_project_deduplicated_report`, thread through to `_collect_pages_data` and `_generate_page_detail_htmls` |
| `auto_a11y/reporting/page_structure_report.py` | **Modify.** Add `progress_callback` param to `generate()` |
| `auto_a11y/reporting/recordings_report.py` | **Modify.** Add `progress_callback` param to `generate_project_recordings_report` |
| `auto_a11y/reporting/project_report.py` | **Modify.** Add `progress_callback` param to `generate()` |
| `auto_a11y/web/routes/reports.py` | **Modify.** Convert all 11 generate routes to background jobs; add job status endpoint; update dashboard route to include active jobs |
| `auto_a11y/web/routes/projects.py` | **Modify.** Convert `/<project_id>/report` to background job |
| `auto_a11y/web/templates/reports/dashboard.html` | **Modify.** Add active job rows with `<progress>`, rewrite form JS to use fetch + poll |
| `auto_a11y/web/templates/websites/view.html` | **Modify.** Update `generateSiteStructureReport()` and `generateAccessibilityReport()` to use fetch |

---

### Task 1: Create ReportJob class

**Files:**
- Create: `auto_a11y/core/report_job.py`

This is the core building block. All subsequent tasks depend on it.

- [ ] **Step 1: Create ReportJob**

```python
# auto_a11y/core/report_job.py
"""
Report generation job - wraps any report generator with JobManager lifecycle
"""

import logging
from pathlib import Path
from auto_a11y.core.job_manager import JobManager, JobStatus

logger = logging.getLogger(__name__)


class ReportCancelled(Exception):
    """Raised when a report job is cancelled"""
    pass


class ReportJob:
    """
    Wraps a report generator function with JobManager lifecycle management.

    The route creates the job in JobManager (it knows the metadata).
    ReportJob only handles execution: RUNNING -> progress -> COMPLETED/FAILED.

    IMPORTANT: The caller (wrapper function) must push a Flask app context
    before calling run(), because report generators use force_locale() and
    Flask-Babel translations which require an active app context.
    """

    def __init__(self, job_id, job_manager, generator_func, generator_args=None, generator_kwargs=None):
        self.job_id = job_id
        self.job_manager = job_manager
        self.generator_func = generator_func
        self.generator_args = generator_args or ()
        self.generator_kwargs = generator_kwargs or {}

    def run(self):
        """Execute the report generator with progress tracking."""
        try:
            # Update job status to RUNNING
            self.job_manager.update_job_status(self.job_id, JobStatus.RUNNING)

            # Inject progress_callback into kwargs
            self.generator_kwargs['progress_callback'] = self._progress_callback

            # Call the generator
            result_path = self.generator_func(*self.generator_args, **self.generator_kwargs)

            # Extract filename from path
            filename = Path(result_path).name

            # Mark completed
            self.job_manager.update_job_status(
                self.job_id,
                JobStatus.COMPLETED,
                result={'filename': filename, 'path': str(result_path)}
            )

            logger.info(f"Report job {self.job_id} completed: {filename}")

        except ReportCancelled:
            self.job_manager.update_job_status(
                self.job_id,
                JobStatus.CANCELLED,
                error='Report generation was cancelled'
            )
            logger.info(f"Report job {self.job_id} cancelled")

        except Exception as e:
            logger.error(f"Report job {self.job_id} failed: {e}", exc_info=True)
            self.job_manager.update_job_status(
                self.job_id,
                JobStatus.FAILED,
                error=str(e)
            )

    def _progress_callback(self, current, total, message):
        """Progress callback injected into generators. Also checks cancellation."""
        self.job_manager.update_job_progress(
            self.job_id,
            current=current,
            total=total,
            message=message
        )
        if self.job_manager.is_cancellation_requested(self.job_id):
            raise ReportCancelled(self.job_id)
```

- [ ] **Step 2: Verify import works**

Run: `cd /home/tait/Documents/cnib/code/auto_a11y_python && .venv/bin/python -c "from auto_a11y.core.report_job import ReportJob, ReportCancelled; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add auto_a11y/core/report_job.py
git commit -m "feat: add ReportJob class for background report generation"
```

---

### Task 2: Add progress_callback to ReportGenerator

**Files:**
- Modify: `auto_a11y/reporting/report_generator.py`

Add `progress_callback=None` parameter to all four generate methods. Call it per-page in the iteration loops.

- [ ] **Step 1: Add progress_callback to generate_page_report (line 123)**

Change signature from:
```python
def generate_page_report(self, page_id: str, format: str = 'html', include_ai: bool = True) -> str:
```
to:
```python
def generate_page_report(self, page_id: str, format: str = 'html', include_ai: bool = True, progress_callback=None) -> str:
```

Add after the `test_result` check (around line 150), before `report_data`:
```python
        if progress_callback:
            progress_callback(0, 1, 'Collecting page data...')
```

Add after `content = formatter.format_page_report(report_data)` (around line 171), before saving:
```python
        if progress_callback:
            progress_callback(1, 1, 'Saving report...')
```

- [ ] **Step 2: Add progress_callback to generate_website_report (line 189)**

Change signature from:
```python
def generate_website_report(self, website_id: str, format: str = 'html', include_ai: bool = True) -> str:
```
to:
```python
def generate_website_report(self, website_id: str, format: str = 'html', include_ai: bool = True, progress_callback=None) -> str:
```

In the page iteration loop (lines 215-221), add progress callback:
```python
        page_results = []
        for i, page in enumerate(pages):
            if progress_callback:
                progress_callback(i, len(pages), f'Processing page {i + 1} of {len(pages)}...')
            test_result = self.db.get_latest_test_result(page.id)
            if test_result:
                page_results.append({
                    'page': page.__dict__ if hasattr(page, '__dict__') else page,
                    'test_result': test_result
                })
```

After content generation, before save:
```python
        if progress_callback:
            progress_callback(len(pages), len(pages), 'Saving report...')
```

- [ ] **Step 3: Add progress_callback to generate_all_projects_report (line 260)**

Change signature to add `progress_callback=None`.

Pre-compute total pages, then track progress. Replace the nested loop (lines 294-320) with:
```python
        # First pass: count total pages for progress
        all_project_websites = []
        total_pages_count = 0
        for project in projects:
            websites = self.db.get_websites(project.id)
            all_project_websites.append((project, websites))
            for website in websites:
                total_pages_count += len(self.db.get_pages(website.id))

        processed_pages = 0

        for project, websites in all_project_websites:
            project_data = {
                'project': project.__dict__,
                'websites': [],
                'stats': self.db.get_project_stats(project.id)
            }

            for website in websites:
                pages = self.db.get_pages(website.id)

                if progress_callback:
                    progress_callback(processed_pages, max(total_pages_count, 1),
                                    f'Processing {website.name}...')

                website_data = {
                    'website': website.__dict__,
                    'pages': len(pages),
                    'tested': sum(1 for p in pages if p.status.value == 'tested'),
                    'violations': sum(p.violation_count for p in pages),
                    'warnings': sum(p.warning_count for p in pages)
                }
                project_data['websites'].append(website_data)

                total_stats['total_websites'] += 1
                total_stats['total_pages'] += len(pages)
                total_stats['total_tested'] += website_data['tested']
                total_stats['total_violations'] += website_data['violations']
                total_stats['total_warnings'] += website_data['warnings']

                processed_pages += len(pages)

            all_projects_data.append(project_data)
```

- [ ] **Step 4: Add progress_callback to generate_project_report (line 383)**

Change signature to add `progress_callback=None`.

In the nested page loop (lines 406-416), add progress tracking:
```python
        website_data = []
        page_count = 0
        # Count total pages first for accurate progress
        total_page_count = sum(len(self.db.get_pages(w.id)) for w in websites)

        for website in websites:
            pages = self.db.get_pages(website.id)

            page_results = []
            for page in pages:
                if progress_callback:
                    progress_callback(page_count, total_page_count,
                                    f'Processing page {page_count + 1} of {total_page_count}...')
                test_result = self.db.get_latest_test_result(page.id)
                if test_result:
                    page_results.append({
                        'page': page.__dict__ if hasattr(page, '__dict__') else page,
                        'test_result': test_result
                    })
                page_count += 1

            website_data.append({
                'website': website.__dict__ if hasattr(website, '__dict__') else website,
                'pages': page_results
            })
```

- [ ] **Step 5: Verify the app still starts**

Run: `cd /home/tait/Documents/cnib/code/auto_a11y_python && .venv/bin/python -c "from auto_a11y.reporting.report_generator import ReportGenerator; print('OK')"`

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add auto_a11y/reporting/report_generator.py
git commit -m "feat: add progress_callback to ReportGenerator methods"
```

---

### Task 3: Add progress_callback to DiscoveryReportGenerator

**Files:**
- Modify: `auto_a11y/reporting/discovery_report.py`

- [ ] **Step 1: Add progress_callback to generate_website_discovery_report (line 281)**

Change signature to:
```python
def generate_website_discovery_report(self, website_id: str, format: str = 'html', progress_callback=None) -> str:
```

Pass it through to `_collect_inspection_data` (line 304):
```python
        inspection_data = self._collect_inspection_data(pages, progress_callback=progress_callback)
```

After `_collect_inspection_data` returns, before HTML generation:
```python
        if progress_callback:
            progress_callback(len(pages), len(pages), 'Generating report...')
```

- [ ] **Step 2: Add progress_callback to generate_project_discovery_report (line 337)**

Change signature to:
```python
def generate_project_discovery_report(self, project_id: str, format: str = 'html', progress_callback=None) -> str:
```

Pass it through to `_collect_inspection_data` (line 365):
```python
        inspection_data = self._collect_inspection_data(all_pages, progress_callback=progress_callback)
```

- [ ] **Step 3: Add progress_callback to _collect_inspection_data (line 398)**

Change signature to:
```python
def _collect_inspection_data(self, pages: List[Page], progress_callback=None) -> Dict[str, Any]:
```

Inside the `for page in pages:` loop (line 463), add at the start of the loop body:
```python
        for i, page in enumerate(pages):
            if progress_callback:
                progress_callback(i, len(pages), f'Collecting data for page {i + 1} of {len(pages)}...')
```

(Change the existing `for page in pages:` to `for i, page in enumerate(pages):`)

- [ ] **Step 4: Verify import**

Run: `cd /home/tait/Documents/cnib/code/auto_a11y_python && .venv/bin/python -c "from auto_a11y.reporting.discovery_report import DiscoveryReportGenerator; print('OK')"`

- [ ] **Step 5: Commit**

```bash
git add auto_a11y/reporting/discovery_report.py
git commit -m "feat: add progress_callback to DiscoveryReportGenerator"
```

---

### Task 4: Add progress_callback to StaticHTMLReportGenerator

**Files:**
- Modify: `auto_a11y/reporting/static_html_generator.py`

- [ ] **Step 1: Add progress_callback to generate_report (line 794)**

Change signature to add `progress_callback=None` after `ai_tests_enabled`:
```python
def generate_report(self, page_ids, project_name="Accessibility Report", website_url=None,
                    wcag_level="AA", touchpoints_tested=None, include_screenshots=True,
                    include_discovery=True, ai_tests_enabled=True, progress_callback=None) -> Path:
```

To avoid the progress bar resetting to 0 between phases, use a two-phase total. Pass offset info so each phase reports against the combined total:

```python
            num_pages = len(page_ids)
            # Phase 1: data collection (0 to num_pages)
            pages_data = self._collect_pages_data(
                page_ids, include_discovery,
                progress_callback=progress_callback, progress_offset=0, progress_total=num_pages * 2)
            ...
            # Phase 2: HTML generation (num_pages to num_pages*2)
            self._generate_page_detail_htmls(
                report_dir, pages_data, project_name,
                wcag_level, touchpoints_tested,
                progress_callback=progress_callback, progress_offset=num_pages, progress_total=num_pages * 2)
```

The private methods then call:
```python
if progress_callback:
    progress_callback(progress_offset + i, progress_total, f'Phase message {i + 1} of {len(items)}...')
```

Alternatively, a simpler approach: use the message to distinguish phases and let the progress bar reset. This is simpler and still informative. **Use the simpler approach** — just let each phase report 0-to-N independently with a descriptive message prefix like "Collecting data..." vs "Generating pages...". The user still sees meaningful progress.

- [ ] **Step 2: Add progress_callback to _collect_pages_data (line 863)**

Change signature to:
```python
def _collect_pages_data(self, page_ids, include_discovery, progress_callback=None):
```

In the `for page_id in page_ids:` loop (line 876), add at top of loop:
```python
        for i, page_id in enumerate(page_ids):
            if progress_callback:
                progress_callback(i, len(page_ids), f'Collecting data for page {i + 1} of {len(page_ids)}...')
```

(Change existing `for page_id in page_ids:` to `for i, page_id in enumerate(page_ids):`)

- [ ] **Step 3: Add progress_callback to _generate_page_detail_htmls (line 1739)**

Change signature to add `progress_callback=None`:
```python
def _generate_page_detail_htmls(self, report_dir, pages_data, project_name,
                                wcag_level, touchpoints_tested, progress_callback=None):
```

Find the per-page loop inside this method and add progress reporting. The loop iterates over `pages_data` to generate individual HTML files. Add at the start of each iteration:
```python
            if progress_callback:
                progress_callback(i, len(pages_data), f'Generating page {i + 1} of {len(pages_data)}...')
```

- [ ] **Step 4: Add progress_callback to generate_project_deduplicated_report (line 1932)**

Change signature to:
```python
def generate_project_deduplicated_report(self, project_id=None, website_id=None, progress_callback=None) -> Path:
```

In the nested page iteration loop (lines 1996-2003), add progress:
```python
            page_count = 0
            total_page_count = sum(len(self.db.get_pages(w.id)) for w in websites)

            for page in pages:
                if progress_callback:
                    progress_callback(page_count, max(total_page_count, 1),
                                    f'Processing page {page_count + 1} of {total_page_count}...')
                test_result = self.db.get_latest_test_result(page.id)
                ...
                page_count += 1
```

Note: The `total_page_count` calculation needs to be done before the website loop starts. Move the count outside and track `page_count` across the entire loop.

- [ ] **Step 5: Verify import**

Run: `cd /home/tait/Documents/cnib/code/auto_a11y_python && .venv/bin/python -c "from auto_a11y.reporting.static_html_generator import StaticHTMLReportGenerator; print('OK')"`

- [ ] **Step 6: Commit**

```bash
git add auto_a11y/reporting/static_html_generator.py
git commit -m "feat: add progress_callback to StaticHTMLReportGenerator"
```

---

### Task 5: Add progress_callback to PageStructureReport, RecordingsReportGenerator, ProjectReport

**Files:**
- Modify: `auto_a11y/reporting/page_structure_report.py`
- Modify: `auto_a11y/reporting/recordings_report.py`
- Modify: `auto_a11y/reporting/project_report.py`

- [ ] **Step 1: Add progress_callback to PageStructureReport.generate() (line 185)**

Change signature to:
```python
def generate(self, progress_callback=None) -> Dict[str, Any]:
```

In `_build_tree()` (line 227), the loop at line 240 iterates `self.pages`. Thread the callback through:

Change `generate()` to call `_build_tree` with callback:
```python
        self.root = self._build_tree(progress_callback=progress_callback)
```

Change `_build_tree` signature:
```python
def _build_tree(self, progress_callback=None) -> PageNode:
```

In its loop (line 240):
```python
        for i, page in enumerate(self.pages):
            if progress_callback:
                progress_callback(i, len(self.pages), f'Building tree: page {i + 1} of {len(self.pages)}...')
            self._add_page_to_tree(root, page)
```

After the loop, report completion:
```python
        if progress_callback:
            progress_callback(len(self.pages), len(self.pages), 'Tree structure complete')
```

- [ ] **Step 2: Add progress_callback to RecordingsReportGenerator.generate_project_recordings_report() (line 207)**

Change signature to add `progress_callback=None`:
```python
def generate_project_recordings_report(self, project_id, format='html', include_summary=True,
                                       include_timecodes=True, include_wcag=True,
                                       group_by_touchpoint=True, language='en',
                                       progress_callback=None) -> str:
```

In the recordings iteration loop (line 249):
```python
        for i, recording in enumerate(recordings):
            if progress_callback:
                progress_callback(i, len(recordings), f'Processing recording {i + 1} of {len(recordings)}...')
            all_issues = self.db.get_recording_issues_for_recording(recording.recording_id)
```

(Change existing `for recording in recordings:` to `for i, recording in enumerate(recordings):`)

- [ ] **Step 3: Add progress_callback to ProjectReport.generate() (line 36) and fix save() (line 326)**

Change `generate` signature to:
```python
def generate(self, progress_callback=None) -> Dict[str, Any]:
```

Also fix `save()` to accept an optional `reports_dir` parameter. Currently it accesses `current_app` which won't work in a background thread. Change `save` (around line 326) to:
```python
def save(self, format: str = 'html', reports_dir: str = None) -> str:
```

At the top of `save()`, replace the Flask `current_app` lookup (lines 340-345) with:
```python
        if reports_dir:
            reports_dir = Path(reports_dir)
        else:
            # Try getting from Flask current_app if available
            try:
                from flask import current_app
                if current_app and hasattr(current_app, 'app_config'):
                    reports_dir = Path(current_app.app_config.REPORTS_DIR)
            except:
                pass

            # Fall back to environment variable or default
            if not reports_dir:
                import os
                reports_dir_str = os.environ.get('REPORTS_DIR', 'reports')
                reports_dir = Path(reports_dir_str)
```

In the website loop (line 54):
```python
        page_count = 0
        total_page_count = sum(len(self.pages_by_website.get(w.id, [])) for w in self.websites)

        for website in self.websites:
            pages = self.pages_by_website.get(website.id, [])

            if progress_callback:
                progress_callback(page_count, max(total_page_count, 1),
                                f'Processing {website.name}...')
            ...
            page_count += len(pages)
```

- [ ] **Step 4: Verify imports**

Run: `cd /home/tait/Documents/cnib/code/auto_a11y_python && .venv/bin/python -c "from auto_a11y.reporting.page_structure_report import PageStructureReport; from auto_a11y.reporting.recordings_report import RecordingsReportGenerator; from auto_a11y.reporting.project_report import ProjectReport; print('OK')"`

- [ ] **Step 5: Commit**

```bash
git add auto_a11y/reporting/page_structure_report.py auto_a11y/reporting/recordings_report.py auto_a11y/reporting/project_report.py
git commit -m "feat: add progress_callback to PageStructureReport, RecordingsReportGenerator, ProjectReport"
```

---

### Task 6: Add job status endpoint and update dashboard route

**Files:**
- Modify: `auto_a11y/web/routes/reports.py`

- [ ] **Step 1: Add imports**

At the top of `reports.py`, add:
```python
from auto_a11y.core.job_manager import JobManager, JobType
from auto_a11y.core.task_runner import task_runner
from auto_a11y.core.report_job import ReportJob
from uuid import uuid4
```

**Critical pattern for ALL route wrappers:** Every wrapper closure must push a Flask app context because report generators use `force_locale()` and Flask-Babel translations. Without it, all reports default to English and translations fail silently. Capture the app reference in the route handler:

```python
# In the route handler (Flask context available):
app = current_app._get_current_object()

# In the wrapper closure (background thread, no Flask context):
def wrapper():
    with app.app_context():
        generator = ...
        job = ReportJob(...)
        job.run()
```

This pattern must be applied to **every** wrapper in Tasks 7-10.

- [ ] **Step 2: Replace the stub status route (line 167-176)**

Remove the existing stub:
```python
@reports_bp.route('/report/<report_id>/status')
def report_status(report_id):
    ...
```

Replace with:
```python
@reports_bp.route('/job/<job_id>/status')
def job_status(job_id):
    """Get report job status"""
    job_manager = JobManager(current_app.db)
    job = job_manager.get_job(job_id)

    if not job:
        return jsonify({'error': 'Job not found'}), 404

    response = {
        'job_id': job['job_id'],
        'status': job['status'],
        'progress': job.get('progress', {}),
        'result': job.get('result'),
        'error': job.get('error'),
        'metadata': job.get('metadata', {})
    }

    return jsonify(response)
```

- [ ] **Step 3: Update reports_dashboard route (line 20-75)**

Add active job query before the return statement. After the `websites` loop (line 70), add:
```python
    # Get active report generation jobs
    job_manager = JobManager(current_app.db)
    active_jobs = job_manager.get_active_jobs(job_type=JobType.REPORT_GENERATION)

    # Also get recently completed jobs (last 5 minutes) so the UI can show downloads
    from datetime import timedelta
    recently_completed = list(job_manager.collection.find({
        'job_type': JobType.REPORT_GENERATION.value,
        'status': {'$in': ['completed', 'failed']},
        'completed_at': {'$gte': datetime.now() - timedelta(minutes=5)}
    }).sort('completed_at', -1))
```

Update the `render_template` call to pass `active_jobs` and `recently_completed`:
```python
    return render_template('reports/dashboard.html',
                         reports=reports,
                         projects=projects,
                         websites=websites,
                         active_jobs=active_jobs,
                         recently_completed=recently_completed)
```

- [ ] **Step 4: Verify app starts**

Run: `cd /home/tait/Documents/cnib/code/auto_a11y_python && .venv/bin/python -c "from auto_a11y.web.routes.reports import reports_bp; print('OK')"`

- [ ] **Step 5: Commit**

```bash
git add auto_a11y/web/routes/reports.py
git commit -m "feat: add report job status endpoint and active jobs in dashboard route"
```

---

### Task 7: Convert discovery report routes to background jobs

**Files:**
- Modify: `auto_a11y/web/routes/reports.py`

Starting with discovery routes because this is the originally reported problem.

- [ ] **Step 1: Convert generate_discovery_website_report (line 459-488)**

Replace the entire function body:
```python
@reports_bp.route('/generate/discovery/website/<website_id>', methods=['POST'])
def generate_discovery_website_report(website_id):
    """Generate discovery report for a website (background job)"""
    format = request.form.get('format', request.json.get('format', 'html') if request.is_json else 'html')

    # Validate
    website = current_app.db.get_website(website_id)
    if not website:
        return jsonify({'success': False, 'error': 'Website not found'}), 404

    # Capture Flask context
    db = current_app.db
    config = current_app.app_config.__dict__.copy()
    language = session.get('language', 'en')
    app = current_app._get_current_object()  # For app context in thread

    # Create job
    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        website_id=str(website_id),
        metadata={
            'report_type': 'discovery',
            'scope': 'website',
            'format': format,
            'display_name': f'Discovery Report - {website.name}'
        }
    )

    # Submit background job (app context required for force_locale/translations)
    def wrapper():
        with app.app_context():
            from auto_a11y.reporting.discovery_report import DiscoveryReportGenerator
            generator = DiscoveryReportGenerator(db, config, language=language)
            job = ReportJob(job_id, job_manager, generator.generate_website_discovery_report,
                           generator_kwargs={'website_id': website_id, 'format': format})
            job.run()

    task_runner.submit_task(func=wrapper, task_id=job_id)

    return jsonify({'success': True, 'job_id': job_id})
```

- [ ] **Step 2: Convert generate_discovery_project_report (line 491-520)**

Same pattern:
```python
@reports_bp.route('/generate/discovery/project/<project_id>', methods=['POST'])
def generate_discovery_project_report(project_id):
    """Generate discovery report for an entire project (background job)"""
    format = request.form.get('format', request.json.get('format', 'html') if request.is_json else 'html')

    # Validate
    project = current_app.db.get_project(project_id)
    if not project:
        return jsonify({'success': False, 'error': 'Project not found'}), 404

    # Capture Flask context
    db = current_app.db
    config = current_app.app_config.__dict__.copy()
    language = session.get('language', 'en')
    app = current_app._get_current_object()

    # Create job
    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        project_id=str(project_id),
        metadata={
            'report_type': 'discovery',
            'scope': 'project',
            'format': format,
            'display_name': f'Discovery Report - {project.name}'
        }
    )

    # Submit background job
    def wrapper():
        with app.app_context():
            from auto_a11y.reporting.discovery_report import DiscoveryReportGenerator
            generator = DiscoveryReportGenerator(db, config, language=language)
            job = ReportJob(job_id, job_manager, generator.generate_project_discovery_report,
                           generator_kwargs={'project_id': project_id, 'format': format})
            job.run()

    task_runner.submit_task(func=wrapper, task_id=job_id)

    return jsonify({'success': True, 'job_id': job_id})
```

- [ ] **Step 3: Commit**

```bash
git add auto_a11y/web/routes/reports.py
git commit -m "feat: convert discovery report routes to background jobs"
```

---

### Task 8: Convert generic and per-scope report routes to background jobs

**Files:**
- Modify: `auto_a11y/web/routes/reports.py`

- [ ] **Step 1: Convert generate_report (line 78-164)**

Replace the function body with the background job pattern. The existing function already handles all scopes (all/project/website) and formats. The key change: capture `db`, `config`, `language` before the closure, move the generator call into the wrapper.

```python
@reports_bp.route('/generate', methods=['POST'])
def generate_report():
    """Generate accessibility report (background job)"""
    data = request.get_json()

    project_id = data.get('project_id')
    website_id = data.get('website_id')
    report_type = data.get('type', 'xlsx')

    # Determine scope and validate
    scope = 'all'
    scope_id = None
    display_name = 'All Projects Report'

    if project_id:
        project = current_app.db.get_project(project_id)
        if not project:
            return jsonify({'error': 'Project not found'}), 404
        scope = 'project'
        scope_id = project_id
        display_name = f'Accessibility Report - {project.name}'
    elif website_id:
        website = current_app.db.get_website(website_id)
        if not website:
            return jsonify({'error': 'Website not found'}), 404
        scope = 'website'
        scope_id = website_id
        display_name = f'Accessibility Report - {website.name}'

    # Capture Flask context
    db = current_app.db
    config = current_app.app_config.__dict__.copy()
    language = str(get_locale()) if get_locale() else 'en'
    app = current_app._get_current_object()

    # Create job
    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        project_id=str(scope_id) if scope == 'project' else None,
        website_id=str(scope_id) if scope == 'website' else None,
        metadata={
            'report_type': report_type,
            'scope': scope,
            'display_name': display_name
        }
    )

    # Submit background job
    def wrapper():
        with app.app_context():
            from auto_a11y.reporting import ReportGenerator
            generator = ReportGenerator(db, config, language=language)

            format_map = {'excel': 'xlsx'}
            fmt = format_map.get(report_type, report_type)

            if scope == 'all':
                func = generator.generate_all_projects_report
                kwargs = {'format': fmt}
            elif scope == 'project':
                func = generator.generate_project_report
                kwargs = {'project_id': scope_id, 'format': fmt}
            else:
                func = generator.generate_website_report
                kwargs = {'website_id': scope_id, 'format': fmt}

            job = ReportJob(job_id, job_manager, func, generator_kwargs=kwargs)
            job.run()

    task_runner.submit_task(func=wrapper, task_id=job_id)

    return jsonify({
        'success': True,
        'job_id': job_id,
        'message': 'Report generation started'
    })
```

- [ ] **Step 2: Convert generate_page_report (line 262-289)**

```python
@reports_bp.route('/generate/page/<page_id>', methods=['POST'])
def generate_page_report(page_id):
    """Generate report for a single page (background job)"""
    format = request.form.get('format', request.json.get('format', 'html') if request.is_json else 'html')
    include_ai = request.form.get('include_ai', 'true') == 'true'

    page = current_app.db.get_page(page_id)
    if not page:
        return jsonify({'success': False, 'error': 'Page not found'}), 404

    db = current_app.db
    config = current_app.app_config.__dict__.copy()
    language = str(get_locale()) if get_locale() else 'en'
    app = current_app._get_current_object()

    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        metadata={
            'report_type': format,
            'scope': 'page',
            'display_name': f'Page Report - {page.title or page.url}'
        }
    )

    def wrapper():
        with app.app_context():
            generator = ReportGenerator(db, config, language=language)
            job = ReportJob(job_id, job_manager, generator.generate_page_report,
                           generator_kwargs={'page_id': page_id, 'format': format, 'include_ai': include_ai})
            job.run()

    task_runner.submit_task(func=wrapper, task_id=job_id)
    return jsonify({'success': True, 'job_id': job_id})
```

- [ ] **Step 3: Convert generate_website_report (line 292-319)**

Same pattern — capture `db`, `config`, `language`, create job, submit wrapper.

- [ ] **Step 4: Convert generate_project_report (line 322-347)**

Same pattern.

- [ ] **Step 5: Commit**

```bash
git add auto_a11y/web/routes/reports.py
git commit -m "feat: convert generic and per-scope report routes to background jobs"
```

---

### Task 9: Convert page-structure, static-html, deduplicated, and recordings routes

**Files:**
- Modify: `auto_a11y/web/routes/reports.py`

- [ ] **Step 1: Convert generate_page_structure_report_download (line 350-396) and generate_page_structure_report (line 399-456)**

Both page-structure routes follow the same pattern. Key difference: PageStructureReport uses `generate()` + `save()` as separate steps, so the wrapper calls both:

```python
def wrapper():
    report = PageStructureReport(db, website, pages, project, language=language)
    report.generate(progress_callback=progress_callback_fn)
    report_path = report.save(format)
    # ReportJob handles the callback, so we need a different approach here.
    # Use ReportJob with a lambda that wraps both calls:
    ...
```

Actually, for PageStructureReport, wrap the two-step process in a single callable for ReportJob:

```python
    def generate_and_save(progress_callback=None):
        report = PageStructureReport(db, website, pages, project, language=language)
        report.generate(progress_callback=progress_callback)
        return report.save(format, reports_dir=reports_dir)

    def wrapper():
        with app.app_context():
            job = ReportJob(job_id, job_manager, generate_and_save)
            job.run()
```

Critical: `website`, `pages`, `project`, `reports_dir`, `app` must all be captured in the route handler (Flask context). `ProjectReport.save()` currently accesses `current_app` to find `REPORTS_DIR` — we must pass `reports_dir` explicitly (add a `reports_dir` parameter to `save()` if it doesn't exist, with fallback to current behavior).

Apply this to both page-structure routes. The legacy route now also returns JSON instead of `send_file`.

- [ ] **Step 2: Convert generate_static_html_report (line 522-630)**

This route does significant data collection (page_ids, project_name, website_url, touchpoints_tested) before calling the generator. **All of this must stay in the route handler.**

```python
@reports_bp.route('/generate/static-html', methods=['POST'])
def generate_static_html_report():
    """Generate static HTML report (background job)"""
    project_id = request.form.get('project_id')
    website_id = request.form.get('website_id')
    include_screenshots = request.form.get('include_screenshots', 'true') in ['true', 'True', '1', 'on']
    include_discovery = request.form.get('include_discovery', 'true') in ['true', 'True', '1', 'on']
    wcag_level = request.form.get('wcag_level', 'AA')

    # --- All data collection happens HERE in Flask context ---
    page_ids = []
    project_name = "Accessibility Report"
    website_url = None
    touchpoints_tested = None

    # ... (keep existing data collection logic from lines 539-599, unchanged) ...

    if not page_ids:
        return jsonify({'success': False, 'error': 'No tested pages found'}), 400

    # Capture Flask context
    db = current_app.db
    reports_dir = current_app.app_config.REPORTS_DIR
    language = session.get('language', 'en')

    # Create job
    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        project_id=str(project_id) if project_id else None,
        website_id=str(website_id) if website_id else None,
        metadata={
            'report_type': 'static-html',
            'scope': 'project' if project_id else ('website' if website_id else 'all'),
            'display_name': f'Offline Report - {project_name}'
        }
    )

    # Submit — all collected data is captured in the closure
    app = current_app._get_current_object()

    def wrapper():
        with app.app_context():
            generator = StaticHTMLReportGenerator(db, output_dir=reports_dir, language=language)
            job = ReportJob(job_id, job_manager, generator.generate_report,
                           generator_kwargs={
                               'page_ids': page_ids,
                               'project_name': project_name,
                               'website_url': website_url,
                               'wcag_level': wcag_level,
                               'touchpoints_tested': touchpoints_tested,
                               'include_screenshots': include_screenshots,
                               'include_discovery': include_discovery,
                               'ai_tests_enabled': True
                           })
            job.run()

    task_runner.submit_task(func=wrapper, task_id=job_id)
    return jsonify({'success': True, 'job_id': job_id})
```

- [ ] **Step 3: Convert generate_deduplicated_report (line 633-664)**

```python
@reports_bp.route('/generate/deduplicated', methods=['POST'])
def generate_deduplicated_report():
    """Generate deduplicated report (background job)"""
    project_id = request.form.get('project_id')
    website_id = request.form.get('website_id')

    # Validate
    display_name = 'Deduplicated Report'
    if project_id:
        project = current_app.db.get_project(project_id)
        if project:
            display_name = f'Deduplicated Report - {project.name}'

    # Capture Flask context
    db = current_app.db
    reports_dir = current_app.app_config.REPORTS_DIR
    language = session.get('language', 'en')

    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        project_id=str(project_id) if project_id else None,
        website_id=str(website_id) if website_id else None,
        metadata={
            'report_type': 'deduplicated',
            'display_name': display_name
        }
    )

    app = current_app._get_current_object()

    def wrapper():
        with app.app_context():
            generator = StaticHTMLReportGenerator(db, output_dir=reports_dir, language=language)
            job = ReportJob(job_id, job_manager, generator.generate_project_deduplicated_report,
                           generator_kwargs={
                               'project_id': project_id,
                               'website_id': website_id if website_id else None
                           })
            job.run()

    task_runner.submit_task(func=wrapper, task_id=job_id)
    return jsonify({'success': True, 'job_id': job_id})
```

- [ ] **Step 4: Convert generate_recordings_report (line 667-736)**

Same pattern. Note: this route checks for no recordings — keep that validation in the route handler (Flask context).

```python
@reports_bp.route('/generate/recordings/<project_id>', methods=['POST'])
def generate_recordings_report(project_id):
    """Generate recordings report (background job)"""
    format = request.form.get('format', 'html')
    include_summary = request.form.get('include_summary', 'true') in ['true', 'True', '1', 'on']
    include_timecodes = request.form.get('include_timecodes', 'true') in ['true', 'True', '1', 'on']
    include_wcag = request.form.get('include_wcag', 'true') in ['true', 'True', '1', 'on']
    group_by_touchpoint = request.form.get('group_by_touchpoint', 'true') in ['true', 'True', '1', 'on']

    # Validate
    project = current_app.db.get_project(project_id)
    if not project:
        return jsonify({'success': False, 'error': 'Project not found'}), 404

    recordings = current_app.db.get_recordings(project_id=project_id)
    if not recordings:
        return jsonify({
            'success': False,
            'info': True,
            'title': _('No Recordings Available'),
            'message': _('There are no recordings for this project yet.')
        }), 200

    # Capture Flask context
    db = current_app.db
    config = current_app.app_config.__dict__.copy()
    language = session.get('language', 'en')
    app = current_app._get_current_object()

    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        project_id=str(project_id),
        metadata={
            'report_type': 'recordings',
            'display_name': f'Recordings Report - {project.name}'
        }
    )

    def wrapper():
        with app.app_context():
            from auto_a11y.reporting.recordings_report import RecordingsReportGenerator
            generator = RecordingsReportGenerator(db, config)
            job = ReportJob(job_id, job_manager, generator.generate_project_recordings_report,
                           generator_kwargs={
                               'project_id': project_id,
                               'format': format,
                               'include_summary': include_summary,
                               'include_timecodes': include_timecodes,
                               'include_wcag': include_wcag,
                               'group_by_touchpoint': group_by_touchpoint,
                               'language': language
                           })
            job.run()

    task_runner.submit_task(func=wrapper, task_id=job_id)
    return jsonify({'success': True, 'job_id': job_id})
```

- [ ] **Step 5: Commit**

```bash
git add auto_a11y/web/routes/reports.py
git commit -m "feat: convert page-structure, static-html, deduplicated, recordings routes to background jobs"
```

---

### Task 10: Convert projects.py report route

**Files:**
- Modify: `auto_a11y/web/routes/projects.py`

- [ ] **Step 1: Convert generate_project_report in projects.py (line 824-869)**

Add imports at the top of the file:
```python
from auto_a11y.core.job_manager import JobManager, JobType
from auto_a11y.core.task_runner import task_runner
from auto_a11y.core.report_job import ReportJob
from uuid import uuid4
```

Replace the function body:
```python
@projects_bp.route('/<project_id>/report', methods=['GET', 'POST'])
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT)
def generate_project_report(project_id):
    """Generate accessibility report for entire project (background job)"""
    from auto_a11y.reporting.project_report import ProjectReport

    project = current_app.db.get_project(project_id)
    if not project:
        return jsonify({'success': False, 'error': 'Project not found'}), 404

    format = request.args.get('format', 'html')

    # Capture Flask context and collect data
    db = current_app.db
    app = current_app._get_current_object()
    reports_dir = str(current_app.app_config.REPORTS_DIR)
    websites = db.get_websites(project_id)
    pages_by_website = {}
    for website in websites:
        pages_by_website[website.id] = db.get_pages(website.id)

    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        project_id=str(project_id),
        metadata={
            'report_type': format,
            'scope': 'project',
            'display_name': f'Project Report - {project.name}'
        }
    )

    def wrapper():
        with app.app_context():
            def generate_and_save(progress_callback=None):
                report = ProjectReport(db, project, websites, pages_by_website)
                report.generate(progress_callback=progress_callback)
                return report.save(format, reports_dir=reports_dir)

            job = ReportJob(job_id, job_manager, generate_and_save)
            job.run()

    task_runner.submit_task(func=wrapper, task_id=job_id)
    return jsonify({'success': True, 'job_id': job_id})
```

- [ ] **Step 2: Commit**

```bash
git add auto_a11y/web/routes/projects.py
git commit -m "feat: convert projects.py report route to background job"
```

---

### Task 11: Update dashboard template — active job rows and polling JS

**Files:**
- Modify: `auto_a11y/web/templates/reports/dashboard.html`

This is the largest frontend change. The key additions:

1. Render active jobs as rows with `<progress>` elements
2. Rewrite all form JS handlers to use `fetch` → receive job_id → start polling
3. Polling function that updates progress and replaces with download link on completion

- [ ] **Step 1: Add active job rows in the reports table**

In the `<tbody>` section (after line 151, before the `{% for report in reports %}` loop), add:

```html
                    {# Active report generation jobs #}
                    {% for job in active_jobs %}
                    <tr data-job-id="{{ job.job_id }}">
                        <td>
                            <i class="bi bi-arrow-repeat spinner-icon" aria-hidden="true"></i>
                            {{ job.metadata.get('display_name', 'Report') }}
                        </td>
                        <td colspan="2">
                            <progress value="{{ job.progress.current|default(0) }}" max="{{ job.progress.total|default(1) }}"></progress>
                            <span class="progress-text ms-2" role="status" aria-live="polite">{{ job.progress.message|default('Starting...') }}</span>
                        </td>
                        <td>{{ _('Just now') }}</td>
                        <td>—</td>
                        <td>—</td>
                    </tr>
                    {% endfor %}
                    {# Recently completed jobs from this session #}
                    {% for job in recently_completed %}
                    {% if job.status == 'completed' and job.result %}
                    <tr>
                        <td>
                            <i class="bi bi-check-circle text-success" aria-hidden="true"></i>
                            {{ job.metadata.get('display_name', 'Report') }}
                        </td>
                        <td><span class="badge bg-success">{{ _('Complete') }}</span></td>
                        <td>—</td>
                        <td>{{ job.completed_at.strftime('%Y-%m-%d %H:%M') if job.completed_at else '' }}</td>
                        <td>—</td>
                        <td>
                            <a href="{{ url_for('reports.download_report', filename=job.result.filename) }}" class="btn btn-sm btn-primary">
                                <i class="bi bi-download" aria-hidden="true"></i> {{ _('Download') }}
                            </a>
                        </td>
                    </tr>
                    {% elif job.status == 'failed' %}
                    <tr>
                        <td>
                            <i class="bi bi-x-circle text-danger" aria-hidden="true"></i>
                            {{ job.metadata.get('display_name', 'Report') }}
                        </td>
                        <td colspan="4"><span class="text-danger">{{ _('Failed') }}: {{ job.error|default('Unknown error') }}</span></td>
                        <td>—</td>
                    </tr>
                    {% endif %}
                    {% endfor %}
```

Also handle the case where there are no reports but there ARE active jobs — the `{% if reports %}` guard should also check `active_jobs`:
```html
            {% if reports or active_jobs or recently_completed %}
```

- [ ] **Step 2: Add the polling JavaScript**

Add before the closing `{% endblock %}`, a new `<script>` block with the core polling infrastructure:

```javascript
// Report job polling system
const activePolls = {};

function startJobPoll(jobId, displayName) {
    // Add row to table if not already there
    const tbody = document.querySelector('.table tbody');
    if (tbody && !document.querySelector(`tr[data-job-id="${jobId}"]`)) {
        const row = document.createElement('tr');
        row.dataset.jobId = jobId;
        row.innerHTML = `
            <td>
                <i class="bi bi-arrow-repeat spinner-icon" aria-hidden="true"></i>
                ${displayName}
            </td>
            <td colspan="2">
                <progress value="0" max="1"></progress>
                <span class="progress-text ms-2" role="status" aria-live="polite">Starting...</span>
            </td>
            <td>${new Date().toLocaleString()}</td>
            <td>—</td>
            <td>—</td>
        `;
        // Insert at top of tbody
        tbody.insertBefore(row, tbody.firstChild);

        // Show the table if it was hidden (no reports case)
        const noReportsMsg = document.querySelector('.no-reports-message');
        if (noReportsMsg) noReportsMsg.style.display = 'none';
        const table = document.querySelector('.table-responsive');
        if (table) table.style.display = '';
    }

    // Start polling
    const poll = () => {
        fetch(`/reports/job/${jobId}/status`)
            .then(r => r.json())
            .then(data => {
                const row = document.querySelector(`tr[data-job-id="${jobId}"]`);
                if (!row) return;

                if (data.status === 'running' || data.status === 'pending') {
                    // Update progress
                    const progress = row.querySelector('progress');
                    const text = row.querySelector('.progress-text');
                    if (progress && data.progress) {
                        progress.value = data.progress.current || 0;
                        progress.max = data.progress.total || 1;
                    }
                    if (text && data.progress) {
                        text.textContent = data.progress.message || 'Processing...';
                    }
                    // Continue polling
                    activePolls[jobId] = setTimeout(poll, 2000);
                } else if (data.status === 'completed' && data.result) {
                    // Replace with download link
                    const filename = data.result.filename;
                    const name = (data.metadata && data.metadata.display_name) || 'Report';
                    row.removeAttribute('data-job-id');
                    row.innerHTML = `
                        <td><i class="bi bi-check-circle text-success" aria-hidden="true"></i> ${name}</td>
                        <td><span class="badge bg-success">Complete</span></td>
                        <td>—</td>
                        <td>${new Date().toLocaleString()}</td>
                        <td>—</td>
                        <td>
                            <a href="/reports/download/${encodeURIComponent(filename)}" class="btn btn-sm btn-primary">
                                <i class="bi bi-download"></i> Download
                            </a>
                        </td>
                    `;
                    delete activePolls[jobId];
                } else if (data.status === 'failed') {
                    const name = (data.metadata && data.metadata.display_name) || 'Report';
                    row.removeAttribute('data-job-id');
                    row.innerHTML = `
                        <td><i class="bi bi-x-circle text-danger" aria-hidden="true"></i> ${name}</td>
                        <td colspan="4"><span class="text-danger">Failed: ${data.error || 'Unknown error'}</span></td>
                        <td>—</td>
                    `;
                    delete activePolls[jobId];
                }
            })
            .catch(() => {
                // On error, retry after delay
                activePolls[jobId] = setTimeout(poll, 5000);
            });
    };

    // First poll immediately
    poll();
}

// Helper: submit report form via fetch, start polling
function submitReportJob(url, options, displayName) {
    return fetch(url, options)
        .then(r => r.json())
        .then(data => {
            if (data.success && data.job_id) {
                startJobPoll(data.job_id, displayName);
                return data;
            } else if (data.info) {
                // Informational message (e.g., no recordings)
                alert(data.message || data.title);
                return data;
            } else {
                throw new Error(data.error || 'Failed to start report generation');
            }
        });
}

// Start polling for any active jobs on page load
{% for job in active_jobs %}
startJobPoll('{{ job.job_id }}', '{{ job.metadata.get("display_name", "Report") }}');
{% endfor %}

// Clean up on page unload
window.addEventListener('beforeunload', () => {
    Object.values(activePolls).forEach(clearTimeout);
});
```

Add CSS for the spinner icon:
```css
.spinner-icon {
    animation: spin 1s linear infinite;
}
@keyframes spin {
    from { transform: rotate(0deg); }
    to { transform: rotate(360deg); }
}
```

- [ ] **Step 3: Rewrite the generic report form handler (around line 377-423)**

Replace the existing `generateReportForm` submit handler:
```javascript
document.getElementById('generateReportForm').addEventListener('submit', function(e) {
    e.preventDefault();

    const form = e.target;
    const submitBtn = form.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>' + {{ _("Generating...") | tojson }};

    // Collect form data as JSON
    const projectId = form.querySelector('[name="project_id"]')?.value;
    const websiteId = form.querySelector('[name="website_id"]')?.value;
    const reportType = form.querySelector('[name="type"]')?.value || 'xlsx';

    const displayName = projectId ?
        (form.querySelector('[name="project_id"] option:checked')?.text || 'Report') :
        (websiteId ? 'Website Report' : 'All Projects Report');

    // Close modal
    const modal = bootstrap.Modal.getInstance(document.getElementById('generateReportModal'));
    if (modal) modal.hide();

    submitReportJob('/reports/generate', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            project_id: projectId || null,
            website_id: websiteId || null,
            type: reportType
        })
    }, displayName).catch(err => {
        alert(err.message);
    }).finally(() => {
        submitBtn.disabled = false;
        submitBtn.innerHTML = '<i class="bi bi-download me-2"></i>' + {{ _("Generate Report") | tojson }};
    });
});
```

- [ ] **Step 4: Rewrite the site structure form handler (around line 479-581)**

Replace with fetch + poll pattern, using `submitReportJob`:
```javascript
siteStructureForm.addEventListener('submit', function(e) {
    e.preventDefault();

    const submitBtn = document.querySelector('#siteStructureForm button[type="submit"]');
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>' + {{ _("Generating...") | tojson }};

    const formData = new FormData(siteStructureForm);
    const websiteName = siteStructureForm.querySelector('[name="website_id"] option:checked')?.text || 'Website';

    // Close modal
    const modal = bootstrap.Modal.getInstance(document.getElementById('siteStructureModal'));
    if (modal) modal.hide();

    submitReportJob('/reports/generate/page-structure', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            website_id: formData.get('website_id'),
            format: formData.get('format') || 'html'
        })
    }, `Site Structure - ${websiteName}`).catch(err => {
        alert(err.message);
    }).finally(() => {
        submitBtn.disabled = false;
        submitBtn.innerHTML = {{ _("Generate Report") | tojson }};
    });
});
```

- [ ] **Step 5: Rewrite the discovery form handler (around line 637-739)**

The discovery form creates a dynamic form and does `form.submit()`. Replace with fetch + poll. The logic selects either website or project scope based on radio selection.

```javascript
discoveryForm.addEventListener('submit', function(e) {
    e.preventDefault();

    const scope = discoveryForm.querySelector('[name="discovery_scope"]:checked')?.value;
    const format = discoveryForm.querySelector('[name="format"]')?.value || 'html';
    let url, displayName;

    if (scope === 'website') {
        const websiteId = discoveryForm.querySelector('[name="discovery_website_id"]')?.value;
        const websiteName = discoveryForm.querySelector('[name="discovery_website_id"] option:checked')?.text || 'Website';
        url = `/reports/generate/discovery/website/${websiteId}`;
        displayName = `Discovery Report - ${websiteName}`;
    } else {
        const projectId = discoveryForm.querySelector('[name="discovery_project_id"]')?.value;
        const projectName = discoveryForm.querySelector('[name="discovery_project_id"] option:checked')?.text || 'Project';
        url = `/reports/generate/discovery/project/${projectId}`;
        displayName = `Discovery Report - ${projectName}`;
    }

    // Close modal
    const modal = bootstrap.Modal.getInstance(document.getElementById('discoveryModal'));
    if (modal) modal.hide();

    submitReportJob(url, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({format: format})
    }, displayName).catch(err => {
        alert(err.message);
    });
});
```

- [ ] **Step 6: Rewrite static-html form handler (around line 983-1024)**

```javascript
document.getElementById('staticHtmlForm').addEventListener('submit', function(e) {
    e.preventDefault();

    const submitBtn = e.target.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Generating...';

    const formData = new FormData(e.target);
    const projectName = e.target.querySelector('[name="project_id"] option:checked')?.text || 'Report';

    // Close modal
    const modal = bootstrap.Modal.getInstance(document.getElementById('staticHtmlModal'));
    if (modal) modal.hide();

    submitReportJob('/reports/generate/static-html', {
        method: 'POST',
        body: formData
    }, `Offline Report - ${projectName}`).catch(err => {
        alert(err.message);
    }).finally(() => {
        submitBtn.disabled = false;
        submitBtn.innerHTML = {{ _("Generate Offline Report") | tojson }};
    });
});
```

- [ ] **Step 7: Rewrite deduplicated form handler (around line 1130-1164)**

Same pattern with `submitReportJob('/reports/generate/deduplicated', ...)`.

- [ ] **Step 8: Rewrite recordings form handler (around line 1282-1349)**

Same pattern with `submitReportJob('/reports/generate/recordings/' + projectId, ...)`.

- [ ] **Step 9: Commit**

```bash
git add auto_a11y/web/templates/reports/dashboard.html
git commit -m "feat: add progress rows and polling JS to reports dashboard"
```

---

### Task 12: Update websites/view.html report functions

**Files:**
- Modify: `auto_a11y/web/templates/websites/view.html`

- [ ] **Step 1: Rewrite generateSiteStructureReport (line 1669-1694)**

```javascript
function generateSiteStructureReport(format) {
    const btn = event.target.closest('a');
    const originalText = btn?.innerHTML;
    if (btn) {
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
        btn.classList.add('disabled');
    }

    fetch('{{ url_for("reports.generate_page_structure_report") }}', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            website_id: '{{ website.id }}',
            format: format
        })
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            if (window.showNotification) {
                showNotification('{{ _("Report Generation") }}',
                    '{{ _("Report is being generated. Check the Reports dashboard for progress.") }}', 'info');
            }
            // Optionally redirect to reports dashboard
            // window.location.href = '/reports/dashboard';
        } else {
            alert(data.error || '{{ _("Failed to generate report") }}');
        }
    })
    .catch(() => alert('{{ _("Failed to generate report") }}'))
    .finally(() => {
        if (btn) {
            btn.innerHTML = originalText;
            btn.classList.remove('disabled');
        }
    });
}
```

- [ ] **Step 2: Rewrite generateAccessibilityReport (line 1696-1715)**

Same pattern, posting to the website report endpoint:
```javascript
function generateAccessibilityReport(format) {
    const btn = event.target.closest('a');
    const originalText = btn?.innerHTML;
    if (btn) {
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
        btn.classList.add('disabled');
    }

    fetch('{{ url_for("reports.generate_website_report", website_id=website.id) }}', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({format: format})
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            if (window.showNotification) {
                showNotification('{{ _("Report Generation") }}',
                    '{{ _("Report is being generated. Check the Reports dashboard for progress.") }}', 'info');
            }
        } else {
            alert(data.error || '{{ _("Failed to generate report") }}');
        }
    })
    .catch(() => alert('{{ _("Failed to generate report") }}'))
    .finally(() => {
        if (btn) {
            btn.innerHTML = originalText;
            btn.classList.remove('disabled');
        }
    });
}
```

- [ ] **Step 3: Commit**

```bash
git add auto_a11y/web/templates/websites/view.html
git commit -m "feat: update website view report functions to use background jobs"
```

---

### Task 13: Manual integration test

- [ ] **Step 1: Start the app**

Run: `cd /home/tait/Documents/cnib/code/auto_a11y_python && .venv/bin/python run.py --debug`

- [ ] **Step 2: Test discovery report generation**

1. Navigate to Reports dashboard
2. Click "Generate Discovery Report"
3. Select a project and click Generate
4. Verify: a new row appears in the table with a `<progress>` element
5. Verify: progress updates (e.g., "Collecting data for page 3 of 45...")
6. Verify: on completion, row shows download link
7. Verify: clicking download works

- [ ] **Step 3: Test navigation away and back**

1. Start generating a report
2. Navigate to a different page
3. Return to Reports dashboard
4. Verify: in-progress report appears with current progress

- [ ] **Step 4: Test from website view**

1. Navigate to a website's detail page
2. Click Reports dropdown → HTML (site structure)
3. Verify: notification shows, report generates in background

- [ ] **Step 5: Test concurrent reports**

1. Start two report generations simultaneously
2. Verify: both appear as rows with independent progress

- [ ] **Step 6: Commit any fixes**

```bash
git add -u
git commit -m "fix: integration test fixes for background report generation"
```
