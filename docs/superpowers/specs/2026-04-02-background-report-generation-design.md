# Background Report Generation

## Problem

All report generation runs synchronously in Flask request handlers. For large projects (e.g., YYC Main Website discovery report), this blocks the web server thread for the entire duration, making the application unresponsive to other users.

## Solution

Move all report generation to background threads using the existing `TaskRunner` + `JobManager` infrastructure. Show page-level progress inline in the reports dashboard using native `<progress>` elements.

## Critical: Flask context handling

Report generators need `db`, `config`, and `language` — all of which come from Flask's application/request context (`current_app`, `session`). Background threads do not have Flask context.

**Every route must capture these values into local variables before defining the wrapper closure:**

```python
# Capture BEFORE the closure — these won't be available in the thread
db = current_app.db
config = current_app.app_config.__dict__.copy()
language = session.get('language', 'en')
reports_dir = current_app.app_config.REPORTS_DIR

def wrapper():
    generator = DiscoveryReportGenerator(db, config, language=language)
    report_job = ReportJob(job_id, job_manager, generator.generate_project_discovery_report, ...)
    report_job.run()
```

For routes that do significant data collection before calling the generator (notably `generate_static_html_report` and `generate_deduplicated_report`), all DB queries and `current_app` access must happen in the route handler, and the collected data passed into the closure.

## Components

### 1. ReportJob (`auto_a11y/core/report_job.py`)

New class following the `ScrapingJob`/`TestingJob` pattern.

```python
class ReportJob:
    def __init__(self, job_id, job_manager, generator_func, generator_args, generator_kwargs):
        self.job_id = job_id
        self.job_manager = job_manager
        self.generator_func = generator_func
        self.generator_args = generator_args
        self.generator_kwargs = generator_kwargs

    def run(self):
        """Execute the report generator with progress tracking."""
        # 1. Update job status to RUNNING
        # 2. Inject progress_callback into generator_kwargs
        # 3. Call generator_func(*args, **kwargs)
        # 4. On success: update job to COMPLETED with file path as result
        # 5. On error: update job to FAILED with error message
```

The `progress_callback` passed to generators also checks for cancellation:

```python
def progress_callback(current, total, message):
    job_manager.update_job_progress(job_id, current, total, message)
    if job_manager.is_cancellation_requested(job_id):
        raise ReportCancelled(job_id)
```

Note: The route creates the job in `JobManager` (not `ReportJob.__init__`), because input validation and job metadata (report type, scope, format) are known at the route level. `ReportJob` only handles execution.

### 2. Progress callbacks in report generators

Every report generator method that produces a report gains an optional `progress_callback=None` parameter. Generators call it during page iteration loops. When `progress_callback` is `None`, generators behave exactly as before (no-op).

**`ReportGenerator` (`reporting/report_generator.py`)**
- `generate_page_report()` — callback at data collection and formatting phases (simple 0/1 → 1/1)
- `generate_website_report()` — callback per page processed
- `generate_project_report()` — callback per page across all websites
- `generate_all_projects_report()` — callback per page across all projects

**`DiscoveryReportGenerator` (`reporting/discovery_report.py`)**
- `generate_website_discovery_report()` — passes callback through to `_collect_inspection_data()` which iterates pages
- `generate_project_discovery_report()` — passes callback through; iterates pages across all websites

**`StaticHTMLReportGenerator` (`reporting/static_html_generator.py`)**
- `generate_report()` — passes callback through to `_collect_pages_data()` and `_generate_page_detail_htmls()` which iterate pages
- `generate_project_deduplicated_report()` — passes callback through; iterates pages during deduplication

**`PageStructureReport` (`reporting/page_structure_report.py`)**
- `generate()` — callback per page during tree construction
- Currently `generate()` + `save()` are separate. The progress callback is passed to `generate()`.

**`RecordingsReportGenerator` (`reporting/recordings_report.py`)**
- `generate_project_recordings_report()` — callback per recording processed

**`ProjectReport` (`reporting/project_report.py`)**
- `generate()` — callback per page during data collection

### 3. Route changes

#### `web/routes/reports.py`

Every `generate_*` route follows this pattern:

```python
@reports_bp.route('/generate/discovery/project/<project_id>', methods=['POST'])
def generate_discovery_project_report(project_id):
    # 1. Validate inputs
    # 2. Capture Flask context into local variables
    db = current_app.db
    config = current_app.app_config.__dict__.copy()
    language = session.get('language', 'en')

    # 3. Create job via JobManager
    job_id = f"report_{uuid4().hex[:8]}"
    job_manager = JobManager(db)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        project_id=project_id,
        metadata={'report_type': 'discovery', 'scope': 'project', 'format': format,
                  'display_name': f'Discovery Report - {project.name}'}
    )

    # 4. Define wrapper (closure captures local variables, NOT current_app)
    def wrapper():
        generator = DiscoveryReportGenerator(db, config, language=language)
        report_job = ReportJob(job_id, job_manager,
            generator.generate_project_discovery_report,
            generator_args=(),
            generator_kwargs={'project_id': project_id, 'format': format})
        report_job.run()

    task_runner.submit_task(func=wrapper, task_id=job_id)

    # 5. Return immediately
    return jsonify({'success': True, 'job_id': job_id})
```

**Routes converted (12 total):**

| Route | Report type | Frontend callers |
|-------|------------|------------------|
| `POST /reports/generate` | Generic (xlsx/pdf/json/html) | `reports/dashboard.html` |
| `POST /reports/generate/page/<page_id>` | Single page | No known frontend caller (API only) |
| `POST /reports/generate/website/<website_id>` | Single website | `websites/view.html` (dynamic form) |
| `POST /reports/generate/project/<project_id>` | Single project | No known frontend caller (API only) |
| `POST /reports/generate/page-structure` | Site structure tree | `reports/dashboard.html` |
| `POST /reports/generate/page-structure/<website_id>` | Site structure (legacy) | `websites/view.html` (dynamic form) |
| `POST /reports/generate/discovery/website/<website_id>` | Discovery (website) | `reports/dashboard.html` |
| `POST /reports/generate/discovery/project/<project_id>` | Discovery (project) | `reports/dashboard.html` |
| `POST /reports/generate/static-html` | Offline HTML ZIP | `reports/dashboard.html` |
| `POST /reports/generate/deduplicated` | Deduplicated ZIP | `reports/dashboard.html` |
| `POST /reports/generate/recordings/<project_id>` | Recordings | `reports/dashboard.html` |

#### `web/routes/projects.py`

| Route | Report type | Frontend callers |
|-------|------------|------------------|
| `GET/POST /<project_id>/report` | Project report | Unknown (may be API only) |

All routes return JSON: `{success: true, job_id: "..."}`.

**New status route:**

```
GET /reports/job/<job_id>/status
```

Returns:
```json
{
    "job_id": "report_abc123",
    "status": "running",
    "progress": {
        "current": 12,
        "total": 87,
        "message": "Processing page 12 of 87...",
        "percentage": 13.8
    },
    "result": null,
    "error": null
}
```

When completed, `result` contains the filename:
```json
{
    "status": "completed",
    "result": {"filename": "discovery_yyc_20260402_143022.html"}
}
```

The existing stub route `GET /report/<report_id>/status` is removed.

### 4. Dashboard UI changes (`templates/reports/dashboard.html`)

**On generate form submit:**

All form handlers change from form-post-redirect to:
1. POST to generate endpoint via `fetch`
2. Receive `{job_id}` in response
3. Close the modal
4. Add a new row to the reports table at the top with:
   - Report name/type in the name column (from job metadata `display_name`)
   - Native `<progress>` element with `value` and `max` attributes
   - Status text: "Processing page 12 of 87..."
5. Poll `GET /reports/job/<job_id>/status` — first poll immediately, then every 2 seconds
6. On each poll: update `<progress value="12" max="87">` and status text
7. On completion: replace progress bar with download link and file size; stop polling
8. On failure: show error message in the row; stop polling

**On page load:**

The dashboard route queries `JobManager.get_active_jobs(job_type=REPORT_GENERATION)` and passes active jobs to the template. The template renders in-progress rows with progress bars and starts polling for each.

**HTML structure for an in-progress row:**

```html
<tr data-job-id="report_abc123">
    <td>Discovery Report - YYC Main Website</td>
    <td>
        <progress value="12" max="87"></progress>
        <span class="progress-text">Processing page 12 of 87...</span>
    </td>
    <td>—</td>
    <td>—</td>
</tr>
```

**HTML structure after completion:**

```html
<tr>
    <td>Discovery Report - YYC Main Website</td>
    <td>html</td>
    <td>2.3 MB</td>
    <td><a href="/reports/download/discovery_yyc_20260402.html">Download</a></td>
</tr>
```

### 5. `websites/view.html` changes

The website view page has two report generation functions (`generateSiteStructureReport`, `generateAccessibilityReport`) that create dynamic forms and submit them. These must be converted to:
1. `fetch` POST to the route
2. Show a brief inline status (e.g., "Generating report..." text next to the button)
3. On completion, redirect to the reports dashboard or show a download link

### 6. Dashboard route changes

The `reports_dashboard()` route adds:

```python
# Get active report generation jobs
job_manager = JobManager(current_app.db)
active_report_jobs = job_manager.get_active_jobs(job_type=JobType.REPORT_GENERATION)

return render_template('reports/dashboard.html',
    reports=reports,
    active_jobs=active_report_jobs,
    projects=projects,
    websites=websites)
```

## Filename uniqueness

With concurrent report jobs, two reports of the same type generated within the same second could collide on filename (timestamps use `%Y%m%d_%H%M%S`). Each generator should append the job_id hex suffix to filenames, e.g., `discovery_yyc_20260402_143022_a1b2c3d4.html`.

## File changes summary

| File | Change |
|------|--------|
| `auto_a11y/core/report_job.py` | **New** — `ReportJob` class with cancellation support |
| `auto_a11y/core/job_manager.py` | No changes needed (already supports `REPORT_GENERATION`) |
| `auto_a11y/core/task_runner.py` | No changes needed |
| `auto_a11y/web/routes/reports.py` | Convert all 11 generate routes to background jobs; add status endpoint; update dashboard route; remove stub status route |
| `auto_a11y/web/routes/projects.py` | Convert `/<project_id>/report` route to background job |
| `auto_a11y/reporting/report_generator.py` | Add `progress_callback` to all generate methods |
| `auto_a11y/reporting/discovery_report.py` | Add `progress_callback` to generate methods and `_collect_inspection_data()` |
| `auto_a11y/reporting/static_html_generator.py` | Add `progress_callback` to generate methods, `_collect_pages_data()`, `_generate_page_detail_htmls()` |
| `auto_a11y/reporting/page_structure_report.py` | Add `progress_callback` to `generate()` |
| `auto_a11y/reporting/recordings_report.py` | Add `progress_callback` to generate method |
| `auto_a11y/reporting/project_report.py` | Add `progress_callback` to `generate()` |
| `auto_a11y/web/templates/reports/dashboard.html` | Rewrite all form JS handlers to use fetch + poll + progress rows; render active jobs on page load |
| `auto_a11y/web/templates/websites/view.html` | Update `generateSiteStructureReport()` and `generateAccessibilityReport()` to use fetch |

## Edge cases

- **Concurrent reports**: Multiple report jobs can run simultaneously. Each gets its own row and poll loop. The `ThreadPoolExecutor` has 5 workers, so up to 5 concurrent reports.
- **Navigation away**: Job continues in background. On return to dashboard, active jobs appear with current progress.
- **Server restart**: Running jobs in `TaskRunner` are lost (in-memory). `JobManager` jobs stay in MongoDB as RUNNING. The stale job cleanup marks them as FAILED after 24 hours.
- **Generator errors**: `ReportJob.run()` catches exceptions, marks job as FAILED, logs the error.
- **Empty results**: If a generator finds no data (no pages, no recordings), it should still complete and produce an empty/minimal report, or fail with a clear message. This matches current behavior.
- **Cancellation**: `ReportJob` checks for cancellation on each progress callback. If cancelled, the partially generated file is cleaned up and the job is marked CANCELLED.
- **Download security**: The existing `GET /reports/download/<filename>` route validates that files are within `REPORTS_DIR`. This is reused for download links from completed jobs.
