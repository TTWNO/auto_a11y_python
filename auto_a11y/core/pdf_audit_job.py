"""Background-job wrapper for asynchronous PDF audits.

Phase 9.3 (continuation) of the pdfMax → auto_a11y integration. The
synchronous pipeline behind :meth:`PdfRunner.audit_pdf_document` can run
for several minutes on a large PDF, so the ``POST /pdfs/<id>/audit``
route must NOT block the request thread. :class:`PdfAuditJob` mirrors
:class:`~auto_a11y.core.scraping_job.ScrapingJob` and
:class:`~auto_a11y.core.testing_job.TestingJob`:

1. Creates a :class:`~auto_a11y.core.job_manager.JobManager` record with
   :data:`~auto_a11y.core.job_manager.JobType.PDF_AUDIT`.
2. Submits the audit work to the global
   :data:`~auto_a11y.core.task_runner.task_runner` thread pool.
3. The worker invokes :func:`asyncio.run` on
   :meth:`PdfRunner.audit_pdf_document` and pumps progress callbacks
   back to :class:`JobManager` so the existing SSE machinery can stream
   them to the browser.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from pathlib import Path
from typing import Literal

from auto_a11y.core.database import Database
from auto_a11y.core.job_manager import JobManager, JobStatus, JobType
from auto_a11y.core.task_runner import task_runner
from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus
from auto_a11y.testing.pdf_runner import PdfRunner

logger = logging.getLogger(__name__)


def queue_audits_for_website(
    *,
    runner: PdfRunner,
    db: Database,
    website_id: str,
    user_id: str,
    session_id: str | None = None,
) -> list[str]:
    """Queue a :class:`PdfAuditJob` for every auditable PDF on a website.

    Used by the "Test All Pages" route so PDF audits run alongside the
    HTML page tests. Mirrors the HTML test-all behaviour of re-running
    every page on each click — previously-audited PDFs are re-audited.

    Skipped:

    * :data:`PdfDocumentStatus.AUDITING` — an audit is already in flight,
      double-queueing would race against itself.
    * :data:`PdfDocumentStatus.FETCH_FAILED` — the bytes never reached
      storage, so there's nothing to audit.

    Returns the list of :class:`JobManager` job ids that were
    successfully queued. The caller can poll those via
    :meth:`JobManager.get_job` to drive a combined progress indicator.
    """
    pdfs = db.get_pdf_documents(website_id=website_id, limit=100000)
    queued: list[str] = []
    for pdf in pdfs:
        if pdf.id is None:
            continue
        if pdf.status in (
            PdfDocumentStatus.AUDITING,
            PdfDocumentStatus.FETCH_FAILED,
        ):
            continue
        try:
            audit_job = PdfAuditJob(
                runner=runner,
                db=db,
                pdf_document_id=pdf.id,
                run_ai=False,
                ai_api_key=None,
                wcag_level='AA',
                locale='en',
                user_id=user_id,
                session_id=session_id,
            )
            queued.append(audit_job.start())
        except Exception as exc:
            logger.warning(
                "Failed to queue PDF audit for %s: %s", pdf.id, exc
            )
    logger.info(
        "Queued %d PDF audit job(s) for website %s",
        len(queued),
        website_id,
    )
    return queued


class PdfAuditJob:
    """Database-backed asynchronous PDF audit job."""

    def __init__(
        self,
        *,
        runner: PdfRunner,
        db: Database,
        pdf_document_id: str,
        run_ai: bool = False,
        ai_api_key: str | None = None,
        wcag_level: Literal["AA", "AAA"] = "AA",
        locale: str = "en",
        user_id: str,
        session_id: str | None = None,
    ) -> None:
        self._runner = runner
        self._db = db
        self._pdf_document_id = pdf_document_id
        self._run_ai = run_ai
        self._ai_api_key = ai_api_key
        self._wcag_level: Literal["AA", "AAA"] = wcag_level
        self._locale = locale
        self._user_id = user_id
        self._session_id = session_id
        self._job_id = f"pdf_audit_{pdf_document_id}_{uuid.uuid4().hex[:8]}"
        self._job_manager = JobManager.get_instance(db)

    @property
    def job_id(self) -> str:
        """The :class:`JobManager` job id for this audit."""
        return self._job_id

    def start(self) -> str:
        """Create the job record and submit the audit to the task runner.

        Returns the :class:`JobManager` job id so the caller can hand it
        to the SSE/progress UI.
        """
        # Look up the PdfDocument to populate website/project linkage on
        # the job document — these power the existing project-scoped
        # SSE filters.
        pdf = self._db.get_pdf_document(self._pdf_document_id)
        website_id = pdf.website_id if pdf is not None else None
        project_id = pdf.project_id if pdf is not None else None

        self._job_manager.create_job(
            job_id=self._job_id,
            job_type=JobType.PDF_AUDIT,
            website_id=website_id,
            project_id=project_id,
            user_id=self._user_id,
            session_id=self._session_id,
            metadata={
                'pdf_document_id': self._pdf_document_id,
                'wcag_level': self._wcag_level,
                'locale': self._locale,
                'run_ai': self._run_ai,
            },
        )

        task_runner.submit_task(
            func=self._run_audit_in_thread,
            task_id=self._job_id,
        )
        logger.info(
            "Submitted PDF audit job %s for document %s",
            self._job_id,
            self._pdf_document_id,
        )
        return self._job_id

    def _run_audit_in_thread(self) -> None:
        """Worker entry point — runs in a :mod:`task_runner` thread."""
        self._job_manager.update_job_status(
            job_id=self._job_id,
            status=JobStatus.RUNNING,
            progress={
                'current': 0,
                'total': 0,
                'message': 'PDF audit started',
                'details': {'pdf_document_id': self._pdf_document_id},
            },
        )

        # ``asyncio.run`` is monkey-patched by ``nest_asyncio`` once the
        # "Test All Documents" flow runs (the testing wrapper calls
        # ``nest_asyncio.apply()``), and the patched implementation
        # reuses the worker thread's existing loop — which has already
        # been closed by a previous audit on the same task_runner
        # thread. Build and tear down a fresh loop here to bypass the
        # patch entirely so each audit gets its own loop.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            try:
                loop.run_until_complete(
                    self._runner.audit_pdf_document(
                        self._pdf_document_id,
                        run_ai=self._run_ai,
                        ai_api_key=self._ai_api_key,
                        wcag_level=self._wcag_level,
                        locale=self._locale,
                        progress_cb=self._on_progress,
                    )
                )
            finally:
                loop.close()
                asyncio.set_event_loop(None)
        except _AuditCancelled:
            # _on_progress raised after detecting a cancellation flip on
            # either the JobManager record or the in-memory task. Treat as
            # a clean cancellation rather than a failure.
            logger.info(
                "PDF audit job %s cancelled mid-run", self._job_id,
            )
            self._job_manager.update_job_status(
                job_id=self._job_id,
                status=JobStatus.CANCELLED,
                progress={
                    'current': 0,
                    'total': 100,
                    'message': 'Audit cancelled',
                    'details': {'pdf_document_id': self._pdf_document_id},
                },
            )
            return
        except Exception as exc:  # noqa: BLE001 — surface any failure
            logger.error(
                f"PDF audit job {self._job_id} failed: {exc}",
                exc_info=True,
            )
            self._job_manager.update_job_status(
                job_id=self._job_id,
                status=JobStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
            )
            return

        # Re-read the document so we can record the persisted
        # last_audit_result_id on the job (the runner sets it via
        # update_pdf_document after the test result is inserted).
        updated = self._db.get_pdf_document(self._pdf_document_id)
        test_result_id = (
            updated.last_audit_result_id if updated is not None else None
        )

        # Run pdfMax's verbatim audit alongside auto_a11y's own
        # pipeline. The view-pdfmax-report endpoint is then a pure
        # cache retrieval — clicking that button must not block the
        # request thread on a 10-30s audit run. Failures here are
        # logged but don't fail the surrounding audit job: the
        # auto_a11y pipeline has already produced a TestResult and
        # the user will see "no pdfMax report yet" copy on the
        # viewer page.
        if updated is not None:
            self._on_progress("Building pdfMax report", 0.99)
            try:
                self._build_pdfmax_report_cache(updated)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "pdfMax report cache build failed for %s: %s",
                    self._pdf_document_id, exc,
                )

        self._job_manager.update_job_status(
            job_id=self._job_id,
            status=JobStatus.COMPLETED,
            progress={
                'current': 1,
                'total': 1,
                'message': 'PDF audit complete',
                'details': {
                    'pdf_document_id': self._pdf_document_id,
                    'test_result_id': test_result_id,
                },
            },
            result={
                'pdf_document_id': self._pdf_document_id,
                'test_result_id': test_result_id,
            },
        )

    def _build_pdfmax_report_cache(self, doc: PdfDocument) -> None:
        """Run pdfMax verbatim and stash its Markdown alongside the PDF.

        The audit job is the *only* code path that invokes the
        pdfMax subprocess. The route handler for ``View pdfMax
        report`` reads the cached files written here; if no audit has
        ever run, the user is told to click Audit first.

        Failures don't propagate — see the call site in
        :meth:`_run_audit_in_thread`.
        """
        # Lazy import: keeps the Flask config tree out of this
        # module's import graph (matches the runner's pattern) and
        # avoids circular imports during test collection.
        from config import config as cfg
        from auto_a11y.pdf.pdfmax_runner import run_pdfmax
        from auto_a11y.pdf.storage import PdfStorage

        storage = PdfStorage(base_dir=Path(cfg.PDF_STORAGE_DIR))
        pdf_path = storage.local_path(doc)
        if not pdf_path.is_file():
            logger.warning(
                "Skipping pdfMax cache build: PDF bytes missing at %s",
                pdf_path,
            )
            return

        cache_dir = pdf_path.parent / "pdfmax-report"
        # Wipe the previous cache so a stale .md / image set never
        # mixes with this run's output.
        if cache_dir.is_dir():
            for child in cache_dir.iterdir():
                try:
                    if child.is_file():
                        child.unlink()
                    elif child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                except OSError:
                    pass

        pdfmax_dir_str = cfg.PDFMAX_CHECKER_DIR
        pdfmax_dir = Path(pdfmax_dir_str) if pdfmax_dir_str else None

        run_pdfmax(
            pdf_path=pdf_path,
            output_dir=cache_dir,
            wcag_level=self._wcag_level,
            skip_claude=not self._run_ai,
            pdfmax_dir=pdfmax_dir,
        )

    def _on_progress(self, stage_name: str, fraction: float) -> None:
        """Bridge :class:`PdfRunner` progress callbacks to :class:`JobManager`.

        Invoked between every Phase 3 collector and the Phase 4 check
        registry — also serves as the cancellation poll. The audit
        pipeline itself is sync (uninterruptible mid-step), so we check
        for a cancellation request on each progress tick and raise
        :class:`_AuditCancelled` to unwind cleanly.

        Cancellation comes from two places, mirroring the existing
        ``pages.cancel_test`` and ``websites.cancel-discovery`` patterns:

        * :meth:`JobManager.is_cancellation_requested` — set by the
          ``/pdfs/<id>/cancel`` route via
          :meth:`JobManager.request_cancellation`.
        * In-memory ``task_runner.tasks[task_id]._cancelled`` — set by
          :meth:`TaskRunner.cancel_task`.
        """
        if self._job_manager.is_cancellation_requested(self._job_id):
            raise _AuditCancelled()

        task = task_runner.tasks.get(self._job_id)
        if task is not None and task.status == 'cancelled':
            raise _AuditCancelled()

        bounded = max(0.0, min(1.0, fraction))
        current = int(round(bounded * 100))
        self._job_manager.update_job_progress(
            job_id=self._job_id,
            current=current,
            total=100,
            message=stage_name,
            details={
                'pdf_document_id': self._pdf_document_id,
                'stage': stage_name,
                'fraction': bounded,
            },
        )


class _AuditCancelled(Exception):
    """Internal sentinel — raised from the progress callback to unwind a
    cancelled audit cleanly. Caught in :meth:`_run_audit_in_thread`.
    """


__all__ = ["PdfAuditJob"]
