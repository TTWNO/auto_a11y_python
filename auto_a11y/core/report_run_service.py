"""Service helpers for queueing report-generation jobs.

The legacy :mod:`auto_a11y.web.routes.reports` blueprint exposes ~11
HTML routes that each create a :class:`JobManager` record, wire up a
generator function based on the scope/type, and submit a wrapper task
to ``task_runner``. The bodies repeat the same ~30-line boilerplate
with a single substitution per scope (page / website / project /
page-structure / discovery / static-html / deduplicated / recordings).

This module extracts the *orchestration* step — building the generator
callable for a given scope, creating the JobManager record, and
submitting the wrapped coroutine — into a small typed surface so the
REST handlers in ``auto_a11y/web/routes/api.py`` can dispatch through
the same path without duplicating any of the boilerplate.

Mirror of :mod:`auto_a11y.core.test_run_service`: domain exceptions are
raised so the caller can map them to whichever response shape the
surface needs (REST → RFC 7807, HTML → flash + redirect).

Two flavours:

- :func:`start_report_generation` — submit a fresh report job. Scope
  and type are supplied by the caller; all the legacy ``/generate/...``
  endpoints route here.
- :func:`restart_report_generation` — re-submit using the metadata of
  an existing (typically failed/cancelled) job. The new job_id is
  fresh; the old job is unchanged unless it was still pending/running
  (in which case it gets a cancellation request as a courtesy).

The output directory and Flask app object are passed in explicitly
rather than read from ``current_app`` / ``Config`` so this module is
unit-testable without a Flask request context.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from auto_a11y.core.job_manager import JobManager, JobStatus, JobType
from auto_a11y.core.report_job import ReportJob
from auto_a11y.core.task_runner import task_runner
from auto_a11y.models import PageStatus

if TYPE_CHECKING:
    from flask import Flask

    from auto_a11y.core.database import Database


# ---------------------------------------------------------------------------
# Scope identifiers — kept as bare strings to match what JobManager
# already stores in ``metadata['scope']`` for older jobs. A literal
# union would be more type-safe but would force callers to migrate all
# their constants today.
# ---------------------------------------------------------------------------

ReportScope = str

VALID_SCOPES: frozenset[str] = frozenset({
    "all",
    "project",
    "website",
    "page",
    "page_structure",
    "discovery_website",
    "discovery_project",
    "static_html",
    "deduplicated",
    "recordings",
})


class ReportRunServiceError(Exception):
    """Base for service-layer errors."""


class ReportScopeError(ReportRunServiceError):
    """Raised when the requested scope is not recognised."""


class ProjectNotFoundError(ReportRunServiceError):
    """Raised when the requested project id does not resolve."""


class WebsiteNotFoundError(ReportRunServiceError):
    """Raised when the requested website id does not resolve."""


class PageNotFoundError(ReportRunServiceError):
    """Raised when the requested page id does not resolve."""


class NoTestedPagesError(ReportRunServiceError):
    """Raised when a scope requires tested pages and none exist.

    Specific to the ``static_html`` scope, which fails fast in
    ``_build_generator`` rather than letting the worker thread raise a
    misleading ``ValueError`` deep inside the static HTML pipeline.
    """


class JobNotFoundError(ReportRunServiceError):
    """Raised when :func:`restart_report_generation` cannot find the
    old job id to read metadata from."""


@dataclass(frozen=True)
class ReportRunHandle:
    """Result of :func:`start_report_generation` / :func:`restart_report_generation`.

    Attributes:
        job_id: The :class:`JobManager` / ``task_runner`` id assigned
            to the new job. Callers should surface this to clients so
            they can poll ``GET /jobs/<id>`` for progress and pick up
            the output filename from the completed job's ``result``.
        scope: Echoed scope, useful for clients reconstructing the
            request without re-reading the body.
        display_name: Human-readable label stored on the job's
            metadata. Surfaced by the legacy frontend in the active-jobs
            list.
    """

    job_id: str
    scope: ReportScope
    display_name: str


def _new_report_job_id() -> str:
    return f"report_{uuid4().hex[:8]}"


def _build_display_name(
    *,
    scope: ReportScope,
    project_name: str | None,
    website_name: str | None,
    page_title: str | None,
    page_url: str | None,
) -> str:
    if scope == "all":
        return "All Projects Accessibility Report"
    if scope == "project":
        return f"Project Report - {project_name or 'Project'}"
    if scope == "website":
        return f"Website Report - {website_name or 'Website'}"
    if scope == "page":
        return f"Page Report - {page_title or page_url or 'Page'}"
    if scope == "page_structure":
        return f"Page Structure - {website_name or 'Website'}"
    if scope == "discovery_website":
        return f"Discovery Report - {website_name or 'Website'}"
    if scope == "discovery_project":
        return f"Discovery Report - {project_name or 'Project'}"
    if scope == "static_html":
        return "Static HTML Accessibility Report"
    if scope == "deduplicated":
        return "Deduplicated Accessibility Report"
    if scope == "recordings":
        return f"Recordings Report - {project_name or 'Project'}"
    return "Report"


def _resolve_display_name(
    db: Database,
    *,
    scope: ReportScope,
    project_id: str | None,
    website_id: str | None,
    page_id: str | None,
) -> str:
    """Read the relevant project/website/page from the db so the job
    metadata carries a user-friendly title without callers having to
    pre-fetch.

    Best-effort: a missing target just produces a generic fallback —
    name resolution is *not* validation. The route handlers are
    expected to have already validated existence and raised the
    appropriate 404 before calling this module.
    """
    project_name: str | None = None
    website_name: str | None = None
    page_title: str | None = None
    page_url: str | None = None

    if project_id:
        project = db.get_project(project_id)
        project_name = project.name if project else None
    if website_id:
        website = db.get_website(website_id)
        website_name = website.name if website else None
    if page_id:
        page = db.get_page(page_id)
        if page is not None:
            page_title = page.title
            page_url = page.url

    return _build_display_name(
        scope=scope,
        project_name=project_name,
        website_name=website_name,
        page_title=page_title,
        page_url=page_url,
    )


def _build_generator(
    *,
    scope: ReportScope,
    report_format: str,
    project_id: str | None,
    website_id: str | None,
    page_id: str | None,
    include_ai: bool,
    db: Database,
    config: dict[str, Any],
    language: str,
    output_dir: Path,
) -> tuple[Callable[..., Any], dict[str, Any]]:
    """Map (scope, type, format, ids) → (generator_callable, kwargs).

    Equivalent to the legacy ``_build_restart_generator`` in
    ``reports.py`` but extended to also handle the *initial*
    generation cases. The legacy version supported page restart
    indirectly via the page-scope branch failing — here we accept
    ``scope='page'`` and route to the matching generator method.

    Raises:
        ReportScopeError: scope is not in :data:`VALID_SCOPES`.
        NoTestedPagesError: scope='static_html' with no tested pages.
    """
    if scope not in VALID_SCOPES:
        raise ReportScopeError(f"unknown report scope: {scope}")

    # ``excel`` is a UI affordance; the generators want ``xlsx``.
    fmt_map = {"excel": "xlsx"}
    fmt = fmt_map.get(report_format, report_format)

    # Lazy imports keep this module import-cheap and side-step a
    # circular if ``auto_a11y.reporting`` ever imports back into core.
    if scope == "all":
        from auto_a11y.reporting import ReportGenerator
        generator = ReportGenerator(db, config, language=language)
        return generator.generate_all_projects_report, {"format": fmt}

    if scope == "project":
        from auto_a11y.reporting import ReportGenerator
        generator = ReportGenerator(db, config, language=language)
        return generator.generate_project_report, {
            "project_id": project_id, "format": fmt,
        }

    if scope == "website":
        from auto_a11y.reporting import ReportGenerator
        generator = ReportGenerator(db, config, language=language)
        return generator.generate_website_report, {
            "website_id": website_id,
            "format": fmt,
            "include_ai": include_ai,
        }

    if scope == "page":
        from auto_a11y.reporting import ReportGenerator
        generator = ReportGenerator(db, config, language=language)
        return generator.generate_page_report, {
            "page_id": page_id,
            "format": fmt,
            "include_ai": include_ai,
        }

    if scope == "page_structure":
        from auto_a11y.reporting import PageStructureReport
        # ``_validate_scope_inputs`` has already enforced website_id is
        # present for this scope, but the type checker can't see that —
        # narrow with an explicit check + raise so the constructor call
        # below sees ``Website`` not ``Website | None``.
        assert website_id is not None
        website_obj = db.get_website(website_id)
        if website_obj is None:
            raise WebsiteNotFoundError(f"website {website_id} not found")
        # Pin to a non-Optional local so the closure below carries the
        # narrowed type — mypy doesn't propagate isinstance narrowings
        # through nested function bodies.
        ps_website = website_obj
        ps_pages = db.get_pages(website_id)
        ps_project = (
            db.get_project(ps_website.project_id)
            if ps_website.project_id else None
        )

        def generate_page_structure(
            progress_callback: Callable[..., Any] | None = None,
        ) -> Any:
            report = PageStructureReport(
                db, ps_website, ps_pages, ps_project, language=language
            )
            report.generate(progress_callback=progress_callback)
            return report.save(fmt)

        return generate_page_structure, {}

    if scope == "discovery_website":
        from auto_a11y.reporting.discovery_report import DiscoveryReportGenerator
        disco = DiscoveryReportGenerator(db, config, language=language)
        return disco.generate_website_discovery_report, {
            "website_id": website_id, "format": fmt,
        }

    if scope == "discovery_project":
        from auto_a11y.reporting.discovery_report import DiscoveryReportGenerator
        disco = DiscoveryReportGenerator(db, config, language=language)
        return disco.generate_project_discovery_report, {
            "project_id": project_id, "format": fmt,
        }

    if scope == "static_html":
        from auto_a11y.reporting.static_html_generator import StaticHTMLReportGenerator

        page_ids: list[str] = []
        project_name = "Accessibility Report"
        website_url: str | None = None
        if project_id:
            project = db.get_project(project_id)
            if project is not None:
                project_name = project.name
            for w in db.get_websites(project_id):
                w_pages = db.get_pages(w.id) if w.id else []
                page_ids.extend(
                    str(p.id) for p in w_pages
                    if p.id and p.status == PageStatus.TESTED
                )
                if not website_url and w.url:
                    website_url = w.url
        elif website_id:
            website = db.get_website(website_id)
            if website is not None:
                website_url = website.url
                website_label = website.name or "Website"
                if website.project_id:
                    project = db.get_project(website.project_id)
                    project_name = (
                        f"{project.name} - {website_label}"
                        if project else website_label
                    )
                else:
                    project_name = website_label
            w_pages = db.get_pages(website_id)
            page_ids = [
                str(p.id) for p in w_pages
                if p.id and p.status == PageStatus.TESTED
            ]
        else:
            project_name = "All Projects Accessibility Report"
            for proj in db.get_projects():
                for w in db.get_websites(proj.id) if proj.id else []:
                    w_pages = db.get_pages(w.id) if w.id else []
                    page_ids.extend(
                        str(p.id) for p in w_pages
                        if p.id and p.status == PageStatus.TESTED
                    )

        if not page_ids:
            raise NoTestedPagesError(
                "no tested pages found to generate static-html report"
            )

        touchpoints_tested: list[str] | None = None
        first_result = db.get_latest_test_result(page_ids[0])
        if first_result is not None and first_result.violations:
            touchpoints_tested = sorted({
                v.touchpoint for v in first_result.violations if v.touchpoint
            })

        static_gen = StaticHTMLReportGenerator(
            db, output_dir=output_dir, language=language
        )

        def generate_static_html(
            progress_callback: Callable[..., Any] | None = None,
        ) -> Any:
            return static_gen.generate_report(
                page_ids=page_ids,
                project_name=project_name,
                website_url=website_url,
                wcag_level="AA",
                touchpoints_tested=touchpoints_tested,
                include_screenshots=True,
                include_discovery=True,
                ai_tests_enabled=True,
                progress_callback=progress_callback,
            )

        return generate_static_html, {}

    if scope == "deduplicated":
        from auto_a11y.reporting.static_html_generator import StaticHTMLReportGenerator
        dedup_gen = StaticHTMLReportGenerator(
            db, output_dir=output_dir, language=language
        )

        def generate_deduplicated(
            progress_callback: Callable[..., Any] | None = None,
        ) -> Any:
            return dedup_gen.generate_project_deduplicated_report(
                project_id=project_id,
                website_id=website_id,
                progress_callback=progress_callback,
            )

        return generate_deduplicated, {}

    if scope == "recordings":
        from auto_a11y.reporting.recordings_report import RecordingsReportGenerator
        rec_gen = RecordingsReportGenerator(db, config, language=language)
        return rec_gen.generate_project_recordings_report, {
            "project_id": project_id,
            "format": fmt,
            "language": language,
        }

    # Defensive — the VALID_SCOPES check up top should make this dead.
    raise ReportScopeError(f"unhandled report scope: {scope}")


def _wrap_for_task_runner(
    *,
    app: Flask,
    job_id: str,
    job_manager: JobManager,
    language: str,
    generator: Callable[..., Any],
    generator_kwargs: dict[str, Any],
) -> Callable[[], None]:
    """Build the no-argument wrapper that ``task_runner`` will call.

    Pushes a Flask app context and a forced locale so the report
    generators (which use Flask-Babel translations under the hood) work
    from a background thread. Any exception that escapes the wrapper —
    including those from generator construction — is captured and
    written back to the job record as FAILED so the UI doesn't see a
    forever-running job.
    """
    # Lazy import — ``fluent`` pulls Flask context machinery.
    from auto_a11y.web.fluent import force_locale

    def wrapper() -> None:
        try:
            with app.app_context(), force_locale(language):
                ReportJob.run_in_wrapper(
                    job_id, job_manager, generator, **generator_kwargs
                )
        except Exception as exc:
            # ``run_in_wrapper`` already writes FAILED for errors inside
            # ``run()``. This branch only covers context-setup failures —
            # e.g. the app vanished — where the inner wrapper never got
            # a chance to mark the job.
            try:
                job_manager.update_job_status(
                    job_id, JobStatus.FAILED, error=str(exc)
                )
            except Exception:
                pass

    return wrapper


def _validate_scope_inputs(
    *,
    scope: ReportScope,
    project_id: str | None,
    website_id: str | None,
    page_id: str | None,
) -> None:
    """Best-effort guard on which id is required for which scope.

    Routes typically validate this *before* calling the service (so
    they can return a structured 400 with field paths). This is a
    backstop for callers that skip that step — surface the misuse as
    :class:`ReportScopeError` rather than letting the generator crash
    deep inside the reporting pipeline.
    """
    if scope == "page" and not page_id:
        raise ReportScopeError("scope='page' requires page_id")
    if scope in ("website", "page_structure", "discovery_website") and not website_id:
        raise ReportScopeError(f"scope={scope!r} requires website_id")
    if scope in ("project", "discovery_project", "recordings") and not project_id:
        raise ReportScopeError(f"scope={scope!r} requires project_id")


def start_report_generation(
    database: Database,
    *,
    scope: ReportScope,
    report_format: str = "xlsx",
    project_id: str | None = None,
    website_id: str | None = None,
    page_id: str | None = None,
    include_ai: bool = True,
    config: dict[str, Any],
    language: str,
    output_dir: Path,
    app: Flask,
    user_id: str | None = None,
    session_id: str | None = None,
) -> ReportRunHandle:
    """Queue a fresh report-generation job.

    Args:
        database: The shared :class:`Database` instance.
        scope: One of :data:`VALID_SCOPES`. Determines which generator
            method on which generator class is invoked.
        report_format: ``xlsx``/``csv``/``pdf``/``html``. Ignored by
            scopes whose generators emit a single fixed format
            (``static_html``, ``deduplicated``).
        project_id / website_id / page_id: Target ids; which one(s)
            are required depends on ``scope`` —
            :func:`_validate_scope_inputs` enforces.
        include_ai: Whether to embed AI-analysis findings in the
            report. Only meaningful for ``website`` and ``page`` scopes;
            the other generators don't read this flag.
        config / language / output_dir: Captured here (rather than
            looked up from ``current_app``) so the background thread
            sees a consistent snapshot.
        app: The Flask app object — :func:`_wrap_for_task_runner`
            pushes a context with it before invoking the generator,
            since Flask-Babel translations need one.
        user_id / session_id: Optional ids for ``JobManager`` attribution.

    Returns:
        :class:`ReportRunHandle` carrying the new ``job_id``.

    Raises:
        ReportScopeError: scope is invalid OR scope/id mismatch.
        NoTestedPagesError: scope='static_html' with no tested pages.
        ProjectNotFoundError / WebsiteNotFoundError / PageNotFoundError:
            the requested target does not exist. Routes should validate
            and 404 before calling, but this is a backstop.
    """
    _validate_scope_inputs(
        scope=scope, project_id=project_id, website_id=website_id, page_id=page_id,
    )

    if project_id and database.get_project(project_id) is None:
        raise ProjectNotFoundError(f"project {project_id} not found")
    if website_id and database.get_website(website_id) is None:
        raise WebsiteNotFoundError(f"website {website_id} not found")
    if page_id and database.get_page(page_id) is None:
        raise PageNotFoundError(f"page {page_id} not found")

    display_name = _resolve_display_name(
        database,
        scope=scope,
        project_id=project_id,
        website_id=website_id,
        page_id=page_id,
    )

    job_id = _new_report_job_id()
    job_manager = JobManager(database)
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.REPORT_GENERATION,
        project_id=project_id,
        website_id=website_id,
        user_id=user_id,
        session_id=session_id,
        metadata={
            "report_type": report_format,
            "scope": scope,
            "display_name": display_name,
            "include_ai": include_ai,
            "page_id": page_id,
        },
    )

    generator, generator_kwargs = _build_generator(
        scope=scope,
        report_format=report_format,
        project_id=project_id,
        website_id=website_id,
        page_id=page_id,
        include_ai=include_ai,
        db=database,
        config=config,
        language=language,
        output_dir=output_dir,
    )

    wrapper = _wrap_for_task_runner(
        app=app,
        job_id=job_id,
        job_manager=job_manager,
        language=language,
        generator=generator,
        generator_kwargs=generator_kwargs,
    )
    task_runner.submit_task(func=wrapper, task_id=job_id)

    return ReportRunHandle(
        job_id=job_id, scope=scope, display_name=display_name
    )


def restart_report_generation(
    database: Database,
    *,
    old_job_id: str,
    config: dict[str, Any],
    language: str,
    output_dir: Path,
    app: Flask,
    user_id: str | None = None,
    session_id: str | None = None,
) -> ReportRunHandle:
    """Re-queue a report-generation job using an existing job's metadata.

    Reads ``scope``/``report_type``/``include_ai`` and the target ids
    off the old job document, then delegates to
    :func:`start_report_generation`. The old job is *not* deleted —
    if it was still pending/running we issue a cancellation request so
    the worker can exit cleanly, but its record remains so listings of
    "all reports" can show that there was an earlier attempt.

    Page-scope restarts are supported here (the legacy
    ``_build_restart_generator`` rejected them because the page_id
    wasn't stored on the job). The new
    :func:`start_report_generation` now persists ``page_id`` into the
    job metadata, so a restart of a page report works as long as the
    original was queued through this service.

    Raises:
        JobNotFoundError: ``old_job_id`` does not resolve.
        ReportScopeError: the stored metadata is missing scope or
            describes a scope we no longer support.
        Whatever :func:`start_report_generation` raises.
    """
    job_manager = JobManager(database)
    old_job = job_manager.get_job(old_job_id)
    if old_job is None:
        raise JobNotFoundError(f"job {old_job_id} not found")

    metadata_any: Any = old_job.get("metadata") or {}
    if not isinstance(metadata_any, dict):
        raise ReportScopeError(
            f"job {old_job_id} has no usable metadata to restart from"
        )
    metadata = cast(dict[str, Any], metadata_any)

    scope_raw = metadata.get("scope")
    if not isinstance(scope_raw, str):
        raise ReportScopeError(
            f"job {old_job_id} has no scope in metadata"
        )
    scope: ReportScope = scope_raw

    report_format_raw = metadata.get("report_type", "xlsx")
    report_format = (
        report_format_raw if isinstance(report_format_raw, str) else "xlsx"
    )
    include_ai_raw = metadata.get("include_ai", True)
    include_ai = include_ai_raw if isinstance(include_ai_raw, bool) else True

    page_id_raw = metadata.get("page_id")
    page_id = page_id_raw if isinstance(page_id_raw, str) else None
    project_id_raw = old_job.get("project_id")
    project_id = project_id_raw if isinstance(project_id_raw, str) else None
    website_id_raw = old_job.get("website_id")
    website_id = website_id_raw if isinstance(website_id_raw, str) else None

    status_raw = old_job.get("status")
    if status_raw in (JobStatus.PENDING.value, JobStatus.RUNNING.value):
        job_manager.request_cancellation(old_job_id, requested_by=user_id)

    return start_report_generation(
        database,
        scope=scope,
        report_format=report_format,
        project_id=project_id,
        website_id=website_id,
        page_id=page_id,
        include_ai=include_ai,
        config=config,
        language=language,
        output_dir=output_dir,
        app=app,
        user_id=user_id,
        session_id=session_id,
    )
