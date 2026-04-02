# Background Report Generation

## Problem

All report generation runs synchronously in Flask request handlers. For large projects (e.g., YYC Main Website discovery report), this blocks the web server thread for the entire duration, making the application unresponsive to other users.

## Solution

Move all report generation to background threads using the existing `TaskRunner` + `JobManager` infrastructure. Show page-level progress inline in the reports dashboard using native `<progress>` elements.

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

The `progress_callback` passed to generators:

```python
def progress_callback(current, total, message):
    job_manager.update_job_progress(job_id, current, total, message)
```

### 2. Progress callbacks in report generators

Every report generator method that produces a report gains an optional `progress_callback=None` parameter. Generators call it during page iteration loops.

**`ReportGenerator` (`reporting/report_generator.py`)**
- `generate_page_report()` — callback at data collection and formatting phases
- `generate_website_report()` — callback per page processed
- `generate_project_report()` — callback per page across all websites
- `generate_all_projects_report()` — callback per page across all projects

**`DiscoveryReportGenerator` (`reporting/discovery_report.py`)**
- `generate_website_discovery_report()` — callback per page during data collection
- `generate_project_discovery_report()` — callback per page across all websites

**`StaticHTMLReportGenerator` (`reporting/static_html_generator.py`)**
- `generate_report()` — callback per page during HTML generation
- `generate_project_deduplicated_report()` — callback per page during deduplication

**`PageStructureReport` (`reporting/page_structure_report.py`)**
- `generate()` — callback per page during tree construction
- Requires refactoring: currently `generate()` + `save()` are separate. The progress callback is passed to `generate()`.

**`RecordingsReportGenerator` (`reporting/recordings_report.py`)**
- `generate_project_recordings_report()` — callback per recording processed

When `progress_callback` is `None`, generators behave exactly as before (no-op). This preserves backward compatibility for any direct calls.

### 3. Route changes (`web/routes/reports.py`)

Every `generate_*` route follows this pattern:

```python
@reports_bp.route('/generate/discovery/project/<project_id>', methods=['POST'])
def generate_discovery_project_report(project_id):
    # 1. Validate inputs (project exists, etc.)
    # 2. Create job via JobManager
    job_id = f"report_{uuid4().hex[:8]}"
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        project_id=project_id,
        metadata={'report_type': 'discovery', 'scope': 'project', 'format': format}
    )
    # 3. Create wrapper and submit to TaskRunner
    def wrapper():
        report_job = ReportJob(job_id, job_manager, generator.generate_project_discovery_report, ...)
        report_job.run()

    task_runner.submit_task(func=wrapper, task_id=job_id)
    # 4. Return immediately
    return jsonify({'success': True, 'job_id': job_id})
```

**Routes converted (9 total):**

| Route | Report type |
|-------|------------|
| `POST /generate` | Generic (xlsx/pdf/json/html) at page/website/project/all scope |
| `POST /generate/page/<page_id>` | Single page |
| `POST /generate/website/<website_id>` | Single website |
| `POST /generate/project/<project_id>` | Single project |
| `POST /generate/page-structure` | Site structure tree |
| `POST /generate/discovery/website/<website_id>` | Discovery (website) |
| `POST /generate/discovery/project/<project_id>` | Discovery (project) |
| `POST /generate/static-html` | Offline HTML ZIP |
| `POST /generate/deduplicated` | Deduplicated ZIP |
| `POST /generate/recordings/<project_id>` | Recordings |

All routes return JSON: `{success: true, job_id: "..."}`.

The legacy page-structure download route (`POST /generate/page-structure/<website_id>`) is also converted.

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
1. POST to generate endpoint via fetch
2. Receive `{job_id}` in response
3. Add a new row to the reports table at the top with:
   - Report name/type in the name column
   - Native `<progress>` element with `value` and `max` attributes
   - Status text: "Processing page 12 of 87..."
4. Start polling `GET /reports/job/<job_id>/status` every 2 seconds
5. On each poll: update `<progress value="12" max="87">` and status text
6. On completion: replace progress bar with download link and file size
7. On failure: show error message in the row

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

### 5. Dashboard route changes

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

## File changes summary

| File | Change |
|------|--------|
| `auto_a11y/core/report_job.py` | **New** — `ReportJob` class |
| `auto_a11y/core/job_manager.py` | No changes needed (already supports `REPORT_GENERATION`) |
| `auto_a11y/core/task_runner.py` | No changes needed |
| `auto_a11y/web/routes/reports.py` | Convert all 10 generate routes to background jobs; add status endpoint; update dashboard route |
| `auto_a11y/reporting/report_generator.py` | Add `progress_callback` to all generate methods |
| `auto_a11y/reporting/discovery_report.py` | Add `progress_callback` to generate methods |
| `auto_a11y/reporting/static_html_generator.py` | Add `progress_callback` to generate methods |
| `auto_a11y/reporting/page_structure_report.py` | Add `progress_callback` to `generate()` |
| `auto_a11y/reporting/recordings_report.py` | Add `progress_callback` to generate method |
| `auto_a11y/web/templates/reports/dashboard.html` | Add progress rows, polling JS, active job rendering |

## Edge cases

- **Concurrent reports**: Multiple report jobs can run simultaneously. Each gets its own row and poll loop. The `ThreadPoolExecutor` has 5 workers, so up to 5 concurrent reports.
- **Navigation away**: Job continues in background. On return to dashboard, active jobs appear with current progress.
- **Server restart**: Running jobs in `TaskRunner` are lost (in-memory). `JobManager` jobs stay in MongoDB as RUNNING. The stale job cleanup marks them as FAILED after 24 hours.
- **Generator errors**: `ReportJob.run()` catches exceptions, marks job as FAILED, logs the error.
- **Empty results**: If a generator finds no data (no pages, no recordings), it should still complete and produce an empty/minimal report, or fail with a clear message. This matches current behavior.
