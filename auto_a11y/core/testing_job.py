"""
Database-backed testing job implementation
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List
from auto_a11y.core.job_manager import JobManager, JobType, JobStatus
from auto_a11y.core.database import Database
from auto_a11y.models import Page, PageStatus
from auto_a11y.testing import TestRunner
from playwright.async_api import Error as PlaywrightError

logger = logging.getLogger(__name__)


class TestingJob:
    """
    Represents a testing job backed by database storage
    """
    
    def __init__(
        self,
        job_manager: JobManager,
        website_id: str,
        job_id: str,
        page_ids: Optional[List[str]] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        test_all: bool = False,
        website_user_id: Optional[str] = None,
        trigger_source: str = "manual",
        schedule_id: Optional[str] = None
    ):
        """
        Initialize testing job

        Args:
            job_manager: JobManager instance
            website_id: Website ID
            job_id: Unique job ID
            page_ids: List of specific page IDs to test (if not test_all)
            user_id: User who initiated the job
            session_id: Session ID for tracking
            test_all: Whether to test all pages in the website
            website_user_id: Optional WebsiteUser ID for authenticated testing
            trigger_source: Source that triggered this job ("manual" or "scheduled")
            schedule_id: Optional schedule ID if triggered by scheduler
        """
        self.job_manager = job_manager
        self.website_id = website_id
        self.job_id = job_id
        self.page_ids = page_ids or []
        self.user_id = user_id
        self.session_id = session_id
        self.test_all = test_all
        self.website_user_id = website_user_id
        self.trigger_source = trigger_source
        self.schedule_id = schedule_id
        
        # Create or get existing job in database
        # For multi-user testing, the same job_id is reused for all users
        logger.info(f"Creating/getting testing job {job_id} in database...")
        try:
            existing_job = job_manager.get_job(job_id)
            if existing_job:
                logger.info(f"Job {job_id} already exists, reusing for next user")
                self.job_doc = existing_job
            else:
                self.job_doc = job_manager.create_job(
                    job_id=job_id,
                    job_type=JobType.TESTING,
                    website_id=website_id,
                    user_id=user_id,
                    session_id=session_id,
                    metadata={
                        'page_ids': page_ids,
                        'test_all': test_all,
                        'total_pages': len(page_ids) if page_ids else 0,
                        'website_user_id': website_user_id,
                        'trigger_source': trigger_source,
                        'schedule_id': schedule_id
                    }
                )
                logger.info(f"Successfully created testing job {job_id} in database for website {website_id}")
        except Exception as e:
            logger.error(f"Failed to create/get job {job_id} in database: {e}")
            raise
    
    def is_cancelled(self) -> bool:
        """
        Check if job is cancelled by querying database
        
        Returns:
            True if cancellation requested
        """
        return self.job_manager.is_cancellation_requested(self.job_id)
    
    def update_progress(
        self,
        pages_tested: int,
        total_pages: int,
        current_page: Optional[str] = None,
        message: str = None,
        pages_passed: int = 0,
        pages_failed: int = 0,
        pages_skipped: int = 0
    ):
        """
        Update job progress in database
        
        Args:
            pages_tested: Number of pages tested
            total_pages: Total number of pages to test
            current_page: URL of current page being tested
            message: Optional status message
            pages_passed: Number of pages that passed
            pages_failed: Number of pages that failed
            pages_skipped: Number of pages skipped
        """
        # Get user label for progress message
        user_label = self._get_user_label()
        
        if not message:
            message = f"Tested {pages_tested}/{total_pages} pages"
            if user_label:
                message = f"[{user_label}] " + message
            if current_page:
                message += f" - Current: {current_page}"
        
        self.job_manager.update_job_progress(
            job_id=self.job_id,
            current=pages_tested,
            total=total_pages,
            message=message,
            details={
                'pages_tested': pages_tested,
                'pages_passed': pages_passed,
                'pages_failed': pages_failed,
                'pages_skipped': pages_skipped,
                'total_pages': total_pages,
                'current_page': current_page,
                'user_label': user_label
            }
        )
    
    def _get_user_label(self) -> str:
        """Get display label for current user being tested"""
        if not self.website_user_id:
            return "Guest"
        # Return just the user ID - the full name lookup would require DB access
        # which we want to avoid in progress updates
        return self._cached_user_label if hasattr(self, '_cached_user_label') else self.website_user_id
    
    def set_user_label(self, label: str):
        """Set cached user label for progress messages"""
        self._cached_user_label = label
    
    def set_running(self, total_pages: int):
        """
        Mark job as running
        
        Args:
            total_pages: Total number of pages to test
        """
        user_label = self._get_user_label()
        message = 'Testing started'
        if user_label:
            message = f'[{user_label}] {message}'
        
        self.job_manager.update_job_status(
            job_id=self.job_id,
            status=JobStatus.RUNNING,
            progress={
                'current': 0,
                'total': total_pages,
                'message': message,
                'details': {
                    'pages_tested': 0,
                    'pages_passed': 0,
                    'pages_failed': 0,
                    'pages_skipped': 0,
                    'total_pages': total_pages,
                    'user_label': user_label
                }
            }
        )
        logger.info(f"Testing job {self.job_id} marked as running with {total_pages} pages to test for user: {user_label}")
    
    def set_completed(
        self,
        pages_tested: int,
        pages_passed: int,
        pages_failed: int,
        pages_skipped: int
    ):
        """
        Mark job as completed
        
        Args:
            pages_tested: Total pages tested
            pages_passed: Number of pages that passed
            pages_failed: Number of pages that failed  
            pages_skipped: Number of pages skipped
        """
        total_pages = pages_tested + pages_skipped
        self.job_manager.update_job_status(
            job_id=self.job_id,
            status=JobStatus.COMPLETED,
            progress={
                'current': pages_tested,
                'total': total_pages,
                'message': f'Testing completed: {pages_tested} tested, {pages_passed} passed, {pages_failed} failed',
                'details': {
                    'pages_tested': pages_tested,
                    'pages_passed': pages_passed,
                    'pages_failed': pages_failed,
                    'pages_skipped': pages_skipped,
                    'total_pages': total_pages
                }
            },
            result={
                'pages_tested': pages_tested,
                'pages_passed': pages_passed,
                'pages_failed': pages_failed,
                'pages_skipped': pages_skipped,
                'completed_at': datetime.now().isoformat()
            }
        )
        logger.info(f"Testing job {self.job_id} completed: {pages_tested} pages tested")
    
    def set_failed(self, error: str):
        """
        Mark job as failed
        
        Args:
            error: Error message
        """
        self.job_manager.update_job_status(
            job_id=self.job_id,
            status=JobStatus.FAILED,
            error=error
        )
        logger.error(f"Testing job {self.job_id} failed: {error}")
    
    def set_cancelled(self):
        """Mark job as cancelled"""
        job = self.job_manager.get_job(self.job_id)
        if job:
            details = job.get('progress', {}).get('details', {})
            pages_tested = details.get('pages_tested', 0)
            pages_passed = details.get('pages_passed', 0)
            pages_failed = details.get('pages_failed', 0)
            pages_skipped = details.get('pages_skipped', 0)
            
            self.job_manager.update_job_status(
                job_id=self.job_id,
                status=JobStatus.CANCELLED,
                progress={
                    'current': pages_tested,
                    'total': details.get('total_pages', 0),
                    'message': 'Testing cancelled by user',
                    'details': {
                        'pages_tested': pages_tested,
                        'pages_passed': pages_passed,
                        'pages_failed': pages_failed,
                        'pages_skipped': pages_skipped,
                        'total_pages': details.get('total_pages', 0)
                    }
                }
            )
            logger.info(f"Testing job {self.job_id} cancelled")
    
    async def run(
        self,
        database: Database,
        browser_config: Dict[str, Any],
        take_screenshot: bool = True,
        run_ai_analysis: Optional[bool] = None,
        ai_api_key: Optional[str] = None,
        skip_completion: bool = False
    ):
        """
        Run the testing job
        
        Args:
            database: Database instance
            browser_config: Browser configuration
            take_screenshot: Whether to take screenshots
            run_ai_analysis: Whether to run AI analysis
            ai_api_key: API key for AI analysis
            skip_completion: If True, don't mark job as completed (for multi-user testing)
        """
        logger.info(f"TestingJob.run started for job {self.job_id}")

        browser_mode = browser_config.get('BROWSER_MODE', 'local')
        if browser_mode in ('disabled', 'remote'):
            raise RuntimeError(f"Browser testing unavailable (BROWSER_MODE={browser_mode})")

        try:
            # Get website
            website = database.get_website(self.website_id)
            if not website:
                raise ValueError(f"Website {self.website_id} not found")
            
            # Set user label for progress messages
            if self.website_user_id:
                user = database.get_project_user(self.website_user_id)
                if user:
                    self.set_user_label(user.name_display)
                else:
                    self.set_user_label(self.website_user_id)
            else:
                self.set_user_label("Guest")
            
            # Get pages to test
            if self.test_all:
                pages = database.get_pages(self.website_id)
                # Filter out pages that are currently being tested
                testable_pages = [p for p in pages if p.status != PageStatus.TESTING]
            elif self.page_ids:
                testable_pages = []
                for page_id in self.page_ids:
                    page = database.get_page(page_id)
                    if page and page.status != PageStatus.TESTING:
                        testable_pages.append(page)
            else:
                raise ValueError("No pages specified for testing")
            
            if not testable_pages:
                logger.warning(f"No testable pages found for job {self.job_id}")
                self.set_completed(0, 0, 0, 0)
                return
            
            # Mark as running
            self.set_running(len(testable_pages))
            logger.info(f"Job {self.job_id} marked as running with {len(testable_pages)} pages to test")

            # Determine worker count
            max_workers = browser_config.get('MAX_TEST_WORKERS', 4)
            stagger_seconds = browser_config.get('WORKER_STAGGER_SECONDS', 1.5)
            num_workers = min(max_workers, len(testable_pages))
            logger.info(f"Starting {num_workers} parallel test workers (max configured: {max_workers})")

            # Fill queue with pages to test
            page_queue = asyncio.Queue()
            for page in testable_pages:
                page_queue.put_nowait(page)

            # Shared progress counters protected by lock
            progress_lock = asyncio.Lock()
            progress = {
                'tested': 0,
                'passed': 0,
                'failed': 0,
                'skipped': 0,
            }

            async def _test_worker(worker_id: int):
                """Worker coroutine: owns a TestRunner, pulls pages from queue."""
                runner = None
                try:
                    # Stagger browser launches to avoid thundering herd
                    if worker_id > 0:
                        await asyncio.sleep(worker_id * stagger_seconds)

                    # Check cancellation after stagger wait
                    if self.is_cancelled():
                        logger.info(f"Worker {worker_id}: cancelled before start")
                        return

                    runner = TestRunner(database, browser_config)
                    logger.info(f"Worker {worker_id}: browser started")

                    while not page_queue.empty():
                        # Check for cancellation before each page
                        if self.is_cancelled():
                            logger.info(f"Worker {worker_id}: cancelled")
                            return

                        try:
                            page = page_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            return

                        # Mark page as testing
                        page.status = PageStatus.TESTING
                        database.update_page(page)

                        try:
                            logger.info(f"Worker {worker_id}: testing {page.url}")
                            test_results_list = await runner.test_page_multi_state(
                                page=page,
                                enable_multi_state=True,
                                take_screenshot=take_screenshot,
                                run_ai_analysis=run_ai_analysis,
                                ai_api_key=ai_api_key,
                                website_user_id=self.website_user_id
                            )

                            test_results = test_results_list[-1] if test_results_list else None

                            if test_results:
                                page.status = PageStatus.TESTED
                                is_pass = page.violation_count == 0
                            else:
                                page.status = PageStatus.ERROR
                                is_pass = False

                            del test_results_list
                            del test_results

                            # Update shared progress under lock
                            async with progress_lock:
                                progress['tested'] += 1
                                if is_pass:
                                    progress['passed'] += 1
                                else:
                                    progress['failed'] += 1

                                user_label = self._get_user_label()
                                self.update_progress(
                                    pages_tested=progress['tested'],
                                    total_pages=len(testable_pages),
                                    current_page=page.url,
                                    message=f"[{user_label}] Completed {progress['tested']}/{len(testable_pages)} pages ({num_workers} workers)",
                                    pages_passed=progress['passed'],
                                    pages_failed=progress['failed'],
                                    pages_skipped=progress['skipped'],
                                )

                        except PlaywrightError as e:
                            logger.error(f"Worker {worker_id}: browser error testing {page.url}: {e}")
                            page.status = PageStatus.ERROR
                            page.error_reason = str(e)
                            database.update_page(page)

                            async with progress_lock:
                                progress['failed'] += 1
                                progress['tested'] += 1
                                self.update_progress(
                                    pages_tested=progress['tested'],
                                    total_pages=len(testable_pages),
                                    current_page=page.url,
                                    message=f"[{self._get_user_label()}] Completed {progress['tested']}/{len(testable_pages)} pages (with errors)",
                                    pages_passed=progress['passed'],
                                    pages_failed=progress['failed'],
                                    pages_skipped=progress['skipped'],
                                )

                            # Attempt browser recovery (skip if OOM-like)
                            error_str = str(e).lower()
                            if 'oom' not in error_str and 'out of memory' not in error_str:
                                try:
                                    logger.info(f"Worker {worker_id}: attempting browser recovery")
                                    await runner.cleanup()
                                    runner = TestRunner(database, browser_config)
                                    logger.info(f"Worker {worker_id}: browser recovered")
                                except Exception as recovery_err:
                                    logger.error(f"Worker {worker_id}: recovery failed: {recovery_err}")
                                    return  # Worker exits, others absorb remaining pages
                            else:
                                logger.warning(f"Worker {worker_id}: OOM detected, exiting")
                                return

                        except Exception as e:
                            logger.error(f"Worker {worker_id}: error testing {page.url}: {e}")
                            page.status = PageStatus.ERROR
                            page.error_reason = str(e)
                            database.update_page(page)

                            async with progress_lock:
                                progress['failed'] += 1
                                progress['tested'] += 1
                                self.update_progress(
                                    pages_tested=progress['tested'],
                                    total_pages=len(testable_pages),
                                    current_page=page.url,
                                    message=f"[{self._get_user_label()}] Completed {progress['tested']}/{len(testable_pages)} pages (with errors)",
                                    pages_passed=progress['passed'],
                                    pages_failed=progress['failed'],
                                    pages_skipped=progress['skipped'],
                                )

                except Exception as e:
                    logger.error(f"Worker {worker_id}: fatal error: {e}")
                finally:
                    if runner:
                        try:
                            await runner.cleanup()
                        except Exception as cleanup_err:
                            logger.warning(f"Worker {worker_id}: cleanup error: {cleanup_err}")

            # Launch all workers and wait for completion
            workers = [_test_worker(i) for i in range(num_workers)]
            results = await asyncio.gather(*workers, return_exceptions=True)

            # Log any worker-level exceptions
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"Worker {i} raised exception: {result}")

            # Check final cancellation status
            if self.is_cancelled():
                logger.info(f"Testing job {self.job_id} was cancelled")
                self.set_cancelled()
            elif skip_completion:
                user_label = self._get_user_label()
                logger.info(f"Testing job {self.job_id} finished for user {user_label}, skipping completion (more users pending)")
            else:
                self.set_completed(
                    progress['tested'], progress['passed'],
                    progress['failed'], progress['skipped']
                )

        except Exception as e:
            logger.error(f"Testing job {self.job_id} failed: {e}")
            self.set_failed(str(e))
            raise
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get current job status from database
        
        Returns:
            Job status dictionary
        """
        job = self.job_manager.get_job(self.job_id)
        if not job:
            return {
                'status': 'unknown',
                'message': 'Job not found'
            }
        
        return {
            'job_id': job['job_id'],
            'status': job['status'],
            'progress': job.get('progress', {}),
            'error': job.get('error'),
            'created_at': job.get('created_at'),
            'started_at': job.get('started_at'),
            'completed_at': job.get('completed_at')
        }