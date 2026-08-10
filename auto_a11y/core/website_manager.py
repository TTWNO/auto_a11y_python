"""
Website management business logic
"""
from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING
from datetime import datetime
from urllib.parse import urlparse

from bson import ObjectId

from auto_a11y.models import Website, ScrapingConfig, Page, PageStatus
from auto_a11y.core.database import Database
from auto_a11y.core.scraping_job import ScrapingJob
from auto_a11y.core.testing_job import TestingJob
from auto_a11y.core.job_manager import JobManager, JobType

if TYPE_CHECKING:
    from auto_a11y.testing.pdf_runner import PdfRunner

logger = logging.getLogger(__name__)


class WebsiteManager:
    """Manages website operations"""
    
    def __init__(
        self,
        database: Database,
        browser_config: dict[str, Any],
        pdf_runner: "PdfRunner | None" = None,
    ):
        """
        Initialize website manager

        Args:
            database: Database connection
            browser_config: Browser configuration
            pdf_runner: Optional PdfRunner. When provided, page discovery
                downloads internal PDFs found during the crawl and
                promotes them to PdfDocuments so they appear in the PDFs
                UI. ``None`` disables that step (e.g. test contexts).
        """
        self.db = database
        self.browser_config = browser_config
        self.pdf_runner: "PdfRunner | None" = pdf_runner
        # Initialize job manager for database-backed job tracking
        self.job_manager: JobManager = JobManager(database)
    
    def cancel_testing(self, job_id: str, user_id: str | None = None) -> bool:
        """
        Cancel a testing job
        
        Args:
            job_id: Job ID to cancel
            user_id: User requesting cancellation
            
        Returns:
            True if cancelled, False if not found
        """
        logger.info(f"WebsiteManager.cancel_testing called for job {job_id} by user {user_id}")
        
        # Check if job exists
        job = self.job_manager.get_job(job_id)
        if not job:
            logger.error(f"Testing job {job_id} not found in database")
            return False
        
        logger.info(f"Found testing job {job_id} with status: {job.get('status')}")
        
        # Request cancellation
        success = self.job_manager.request_cancellation(job_id, requested_by=user_id)
        if success:
            logger.info(f"Successfully requested cancellation for testing job {job_id}")
        else:
            logger.warning(f"Could not cancel testing job {job_id} - may already be completed or cancelled")
        return success
    
    def cancel_discovery(self, job_id: str, user_id: str | None = None) -> bool:
        """
        Cancel a discovery job
        
        Args:
            job_id: Job ID to cancel
            user_id: User requesting cancellation
            
        Returns:
            True if cancelled, False if not found
        """
        logger.info(f"WebsiteManager.cancel_discovery called")
        logger.info(f"  Attempting to cancel job_id: '{job_id}'")
        logger.info(f"  Requested by user: {user_id}")
        
        # Log all discovery jobs in database for debugging
        all_discovery_jobs = self.job_manager.get_active_jobs(job_type=JobType.DISCOVERY)
        logger.info(f"  Active discovery jobs in database: {len(all_discovery_jobs)}")
        for dj in all_discovery_jobs:
            logger.info(f"    - Job ID: '{dj.get('job_id')}' Status: {dj.get('status')}")

        # First check if job exists
        job: dict[str, Any] | None = self.job_manager.get_job(job_id)
        if not job:
            logger.error(f"Job '{job_id}' not found in database")
            
            # Try to find similar job IDs
            _jm: Any = self.job_manager
            all_jobs_cursor: Any = _jm.collection.find({'job_type': JobType.DISCOVERY.value}, {'job_id': 1}).limit(10)
            logger.error("  Recent discovery job IDs in DB:")
            for j in all_jobs_cursor:
                logger.error(f"    - '{j.get('job_id')}'")

            return False
        
        logger.info(f"Found job {job_id} with status: {job.get('status')}")
        
        # Request cancellation
        success = self.job_manager.request_cancellation(job_id, requested_by=user_id)
        if success:
            logger.info(f"Successfully requested cancellation for job {job_id}")
        else:
            logger.warning(f"Could not cancel job {job_id} - may already be completed or cancelled")
        return success
    
    def add_website(
        self,
        project_id: str,
        url: str,
        name: str | None = None,
        scraping_config: ScrapingConfig | None = None
    ) -> Website:
        """
        Add website to project
        
        Args:
            project_id: Project ID
            url: Website URL
            name: Optional display name
            scraping_config: Scraping configuration
            
        Returns:
            Created website
        """
        # Validate project exists
        project = self.db.get_project(project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")
        
        # Validate URL
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"Invalid URL: {url}")
        
        # Normalize URL - remove trailing slash for consistency
        if url.endswith('/') and len(parsed.path) <= 1:
            # Remove trailing slash from root URL
            url = url[:-1]
        
        # Check if website already exists in project
        _db: Any = self.db
        existing: dict[str, Any] | None = _db.websites.find_one({
            'project_id': project_id,
            'url': url
        })
        if existing:
            raise ValueError(f"Website {url} already exists in project")
        
        # Create website
        website = Website(
            project_id=project_id,
            url=url,
            name=name or parsed.netloc,
            scraping_config=scraping_config or ScrapingConfig()
        )
        
        website_id_str = self.db.create_website(website)
        object.__setattr__(website, '_id', ObjectId(website_id_str))

        logger.info(f"Added website: {url} to project {project_id}")
        return website
    
    def get_website(self, website_id: str) -> Website | None:
        """
        Get website by ID
        
        Args:
            website_id: Website ID
            
        Returns:
            Website or None
        """
        return self.db.get_website(website_id)
    
    def list_websites(self, project_id: str) -> list[Website]:
        """
        List websites in project
        
        Args:
            project_id: Project ID
            
        Returns:
            List of websites
        """
        return self.db.get_websites(project_id)
    
    def update_website(
        self,
        website_id: str,
        url: str | None = None,
        name: str | None = None,
        scraping_config: ScrapingConfig | None = None
    ) -> bool:
        """
        Update website details
        
        Args:
            website_id: Website ID
            url: New URL
            name: New name
            scraping_config: New scraping configuration
            
        Returns:
            True if updated successfully
        """
        website = self.get_website(website_id)
        if not website:
            raise ValueError(f"Website {website_id} not found")
        
        # Update fields
        if url is not None:
            website.url = url
        if name is not None:
            website.name = name
        if scraping_config is not None:
            website.scraping_config = scraping_config
        
        return self.db.update_website(website)
    
    def delete_website(self, website_id: str) -> bool:
        """
        Delete website and all related data
        
        Args:
            website_id: Website ID
            
        Returns:
            True if deleted successfully
        """
        website = self.get_website(website_id)
        if not website:
            raise ValueError(f"Website {website_id} not found")
        
        return self.db.delete_website(website_id)
    
    async def discover_pages(
        self,
        website_id: str,
        max_pages: int | None = None,
        job_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        website_user_ids: list[str] | None = None
    ) -> ScrapingJob:
        """
        Start page discovery for website

        Args:
            website_id: Website ID
            max_pages: Optional maximum number of pages to discover
            job_id: Optional job ID
            user_id: User initiating discovery
            session_id: Session ID for tracking
            website_user_ids: Optional list of website user IDs to discover pages for (empty string for guest)

        Returns:
            Scraping job
        """
        website = self.get_website(website_id)
        if not website:
            raise ValueError(f"Website {website_id} not found")
        
        # Log max_pages setting
        if max_pages is not None and max_pages > 0:
            logger.info(f"Discovery will be limited to {max_pages} pages")
        else:
            logger.info(f"Using default max_pages: {website.scraping_config.max_pages}")
        
        # Create job with database backing
        if not job_id:
            job_id = f"discovery_{website_id}_{datetime.now().timestamp()}"
        
        logger.info(f"Creating ScrapingJob with job_id: {job_id}")
        
        try:
            job = ScrapingJob(
                job_manager=self.job_manager,
                website_id=website_id,
                job_id=job_id,
                max_pages=max_pages,
                user_id=user_id,
                session_id=session_id,
                website_user_ids=website_user_ids
            )
            logger.info(f"ScrapingJob created successfully: {job_id}")
        except Exception as e:
            logger.error(f"Failed to create ScrapingJob: {e}")
            raise
        
        # Run discovery
        logger.info(f"Starting job.run for {job_id}")
        await job.run(self.db, self.browser_config, pdf_runner=self.pdf_runner)
        logger.info(f"job.run completed for {job_id}")
        
        logger.info(f"Completed discovery job {job_id} for website {website_id}")
        return job
    
    # _run_discovery method removed - now handled by ScrapingJob.run()
    
    async def test_website(
        self,
        website_id: str,
        page_ids: list[str] | None = None,
        job_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        test_all: bool = False,
        take_screenshot: bool = True,
        run_ai_analysis: bool | None = None,
        ai_api_key: str | None = None,
        website_user_id: str | None = None,
        skip_completion: bool = False
    ) -> TestingJob:
        """
        Start testing for website pages

        Args:
            website_id: Website ID
            page_ids: Specific page IDs to test (if not test_all)
            job_id: Optional job ID
            user_id: User initiating testing
            session_id: Session ID for tracking
            test_all: Whether to test all pages
            take_screenshot: Whether to take screenshots
            run_ai_analysis: Whether to run AI analysis
            ai_api_key: API key for AI analysis
            website_user_id: Optional WebsiteUser ID for authenticated testing
            skip_completion: If True, don't mark job as completed (for multi-user testing)

        Returns:
            Testing job
        """
        website = self.get_website(website_id)
        if not website:
            raise ValueError(f"Website {website_id} not found")
        
        # Create job with database backing
        if not job_id:
            import uuid
            job_id = f"testing_{website_id}_{uuid.uuid4().hex[:8]}"
        
        logger.info(f"Creating TestingJob with job_id: {job_id}")
        
        try:
            job = TestingJob(
                job_manager=self.job_manager,
                website_id=website_id,
                job_id=job_id,
                page_ids=page_ids,
                user_id=user_id,
                session_id=session_id,
                test_all=test_all,
                website_user_id=website_user_id
            )
            logger.info(f"TestingJob created successfully: {job_id}")
        except Exception as e:
            logger.error(f"Failed to create TestingJob: {e}")
            raise
        
        # Run testing
        logger.info(f"Starting testing job.run for {job_id}")
        await job.run(
            self.db,
            self.browser_config,
            take_screenshot=take_screenshot,
            run_ai_analysis=run_ai_analysis,
            ai_api_key=ai_api_key,
            skip_completion=skip_completion
        )
        logger.info(f"Testing job.run completed for {job_id}")
        
        logger.info(f"Completed testing job {job_id} for website {website_id}")
        return job
    
    async def test_project(
        self,
        project_id: str,
        job_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        test_all: bool = True,
        take_screenshot: bool = True,
        run_ai_analysis: bool | None = None,
        ai_api_key: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Test all websites in a project
        
        Args:
            project_id: Project ID to test
            job_id: Optional job ID prefix
            user_id: User who initiated the test
            session_id: Session ID for tracking
            test_all: Test all pages or just untested ones
            take_screenshot: Whether to take screenshots
            run_ai_analysis: Whether to run AI analysis
            ai_api_key: OpenAI API key for AI analysis
            
        Returns:
            List of job results for each website
        """
        logger.info(f"Starting project-level testing for project {project_id}")
        
        # Get project and its websites
        project = self.db.get_project(project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")
        
        websites = self.db.get_websites(project_id)
        if not websites:
            logger.warning(f"No websites found for project {project_id}")
            return []
        
        # Create jobs for each website
        jobs: list[dict[str, Any]] = []
        for i, website in enumerate(websites):
            website_id = website.id
            if website_id is None:
                continue
            website_job_id = f"{job_id or 'proj-test'}_{i}_{website_id}" if job_id else None

            try:
                logger.info(f"Starting test for website {website.name} ({website.url})")

                # Get pages to test
                pages = self.db.get_pages(website_id)

                if not pages:
                    logger.info(f"No pages to test for website {website_id}")
                    continue

                page_id_list: list[str] = [p_id for p in pages if (p_id := p.id) is not None]

                # Start async test for this website
                testing_job = await self.test_website(
                    website_id=website_id,
                    page_ids=page_id_list,
                    job_id=website_job_id,
                    user_id=user_id,
                    session_id=session_id,
                    test_all=test_all,
                    take_screenshot=take_screenshot,
                    run_ai_analysis=run_ai_analysis,
                    ai_api_key=ai_api_key
                )
                
                jobs.append({
                    'website_id': website_id,
                    'website_name': website.name,
                    'website_url': website.url,
                    'job_id': testing_job.job_id,
                    'pages_tested': len(page_id_list)
                })

            except Exception as e:
                logger.error(f"Failed to test website {website_id}: {e}")
                jobs.append({
                    'website_id': website_id,
                    'website_name': website.name,
                    'website_url': website.url,
                    'error': str(e)
                })
        
        logger.info(f"Completed project testing for {project_id} with {len(jobs)} website jobs")
        return jobs
    
    def get_job_status(self, job_id: str) -> dict[str, Any] | None:
        """
        Get job status from database
        
        Args:
            job_id: Job ID
            
        Returns:
            Job status or None
        """
        job = self.job_manager.get_job(job_id)
        if not job:
            return None
        
        return {
            'job_id': job['job_id'],
            'status': job['status'],
            'progress': job.get('progress', {}),
            'error': job.get('error'),
            'created_at': job.get('created_at'),
            'started_at': job.get('started_at'),
            'completed_at': job.get('completed_at')
        }
    
    def add_page_manually(
        self,
        website_id: str,
        url: str,
        priority: str = 'normal'
    ) -> Page:
        """
        Manually add a page to website
        
        Args:
            website_id: Website ID
            url: Page URL
            priority: Page priority
            
        Returns:
            Created page
        """
        website = self.get_website(website_id)
        if not website:
            raise ValueError(f"Website {website_id} not found")
        
        # Check if page already exists
        existing = self.db.get_page_by_url(website_id, url)
        if existing:
            raise ValueError(f"Page {url} already exists")
        
        # Create page
        page = Page(
            website_id=website_id,
            url=url,
            discovered_from='manual',
            priority=priority,
            status=PageStatus.DISCOVERED
        )
        
        page_id_str = self.db.create_page(page)
        object.__setattr__(page, '_id', ObjectId(page_id_str))

        # A manually-added page is never crawled, so nothing would otherwise
        # notice the documents it links to and the project's PDF counts would
        # under-report. Scan it in the background: the fetch is a network call
        # and must not hold up the request that added the page. Testing the page
        # later records the same links, so this failing is not load-bearing —
        # it just means the counts fill in at first test instead of now.
        self._scan_page_for_documents_async(website, page)

        logger.info(f"Manually added page: {url} to website {website_id}")
        return page

    def _scan_page_for_documents_async(self, website: Website, page: Page) -> None:
        """Fetch a page off-thread and record any documents it links to."""
        import threading

        def _scan() -> None:
            try:
                import re
                from urllib.request import Request, urlopen
                from urllib.parse import urljoin

                from auto_a11y.core.document_refs import record_document_references

                request = Request(page.url, headers={'User-Agent': 'Auto A11y'})
                with urlopen(request, timeout=30) as response:  # noqa: S310 — http(s) only
                    if response.status != 200:
                        return
                    # Cap the read: a document scan does not justify pulling an
                    # arbitrarily large body into memory.
                    html = response.read(5_000_000).decode('utf-8', errors='replace')

                hrefs: list[tuple[str, str | None]] = []
                for match in re.finditer(
                    r'<a\b[^>]*\bhref\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                    html, re.I | re.S,
                ):
                    absolute = urljoin(page.url, match.group(1).strip())
                    text = re.sub(r'<[^>]+>', '', match.group(2))
                    text = re.sub(r'\s+', ' ', text).strip()[:200]
                    hrefs.append((absolute, text or None))

                recorded = record_document_references(
                    self.db, website, page.url, hrefs
                )
                if recorded:
                    logger.info(
                        "Recorded %d document reference(s) from manually added page %s",
                        recorded, page.url,
                    )
            except Exception as exc:  # noqa: BLE001 — background, best-effort
                logger.debug(
                    "Background document scan failed for %s: %s", page.url, exc
                )

        threading.Thread(target=_scan, daemon=True, name='doc-scan').start()
    
    def list_pages(
        self,
        website_id: str,
        status: PageStatus | None = None,
        limit: int = 0
    ) -> list[Page]:
        """
        List pages in website
        
        Args:
            website_id: Website ID
            status: Filter by status
            limit: Maximum number of pages
            
        Returns:
            List of pages
        """
        return self.db.get_pages(website_id, status=status, limit=limit)
    
    def get_website_statistics(self, website_id: str) -> dict[str, Any]:
        """
        Get website statistics
        
        Args:
            website_id: Website ID
            
        Returns:
            Website statistics
        """
        website = self.get_website(website_id)
        if not website:
            raise ValueError(f"Website {website_id} not found")
        
        pages = self.list_pages(website_id)
        
        tested_pages = [p for p in pages if p.status == PageStatus.TESTED]
        pages_with_issues = [p for p in pages if p.has_issues]
        
        return {
            'total_pages': len(pages),
            'tested_pages': len(tested_pages),
            'untested_pages': len(pages) - len(tested_pages),
            'pages_with_issues': len(pages_with_issues),
            'total_violations': sum(p.violation_count for p in pages),
            'total_warnings': sum(p.warning_count for p in pages),
            'test_coverage': (len(tested_pages) / len(pages) * 100) if pages else 0
        }
