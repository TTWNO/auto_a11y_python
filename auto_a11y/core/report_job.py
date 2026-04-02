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

    @staticmethod
    def run_in_wrapper(job_id, job_manager, func, *args, **kwargs):
        """
        Safe entry point for wrapper closures. Catches ANY exception
        (including errors in generator construction, app context issues, etc.)
        and marks the job as FAILED in JobManager.

        Use this instead of calling ReportJob(...).run() directly in wrappers,
        so that errors outside of run() are still reported to the job.

        Args:
            job_id: The job ID
            job_manager: JobManager instance
            func: The generator function to call
            *args: Positional args for the generator
            **kwargs: Keyword args for the generator
        """
        try:
            job = ReportJob(job_id, job_manager, func,
                           generator_args=args, generator_kwargs=kwargs)
            job.run()
        except Exception as e:
            # This catches errors that happen OUTSIDE run()'s try/except,
            # e.g. generator constructor fails, app context issues, etc.
            logger.error(f"Report job {job_id} wrapper failed: {e}", exc_info=True)
            try:
                job_manager.update_job_status(
                    job_id,
                    JobStatus.FAILED,
                    error=str(e)
                )
            except Exception:
                logger.error(f"Could not mark job {job_id} as failed in DB")

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
