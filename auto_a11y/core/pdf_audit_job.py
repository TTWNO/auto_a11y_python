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
import uuid
from typing import Literal

from auto_a11y.core.database import Database
from auto_a11y.core.job_manager import JobManager, JobStatus, JobType
from auto_a11y.core.task_runner import task_runner
from auto_a11y.testing.pdf_runner import PdfRunner

logger = logging.getLogger(__name__)


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

        try:
            asyncio.run(
                self._runner.audit_pdf_document(
                    self._pdf_document_id,
                    run_ai=self._run_ai,
                    ai_api_key=self._ai_api_key,
                    wcag_level=self._wcag_level,
                    locale=self._locale,
                    progress_cb=self._on_progress,
                )
            )
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

    def _on_progress(self, stage_name: str, fraction: float) -> None:
        """Bridge :class:`PdfRunner` progress callbacks to :class:`JobManager`.

        :data:`auto_a11y.pdf.models.ProgressCallback` is invoked with
        ``(stage_name, fraction)`` where ``fraction`` is in
        ``[0.0, 1.0]``. We project that onto a percentage-friendly
        ``current``/``total`` pair so the existing SSE consumer (which
        reads ``progress.current`` / ``progress.total`` /
        ``progress.percentage``) sees a normal-looking job.
        """
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


__all__ = ["PdfAuditJob"]
