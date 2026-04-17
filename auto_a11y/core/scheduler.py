"""
Scheduler service for scheduled accessibility testing using APScheduler
"""
from __future__ import annotations

import logging
import asyncio
from datetime import datetime
from typing import Any, TYPE_CHECKING
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.mongodb import MongoDBJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger

from auto_a11y.models import (
    TestSchedule, ScheduleType, ScheduleRunStatus, AITestMode
)

if TYPE_CHECKING:
    from auto_a11y.core.database import Database

logger = logging.getLogger(__name__)


class SchedulerService:
    """
    Service for managing scheduled accessibility tests using APScheduler
    """

    _instance: SchedulerService | None = None
    _initialized: bool

    def __new__(cls, *args: Any, **kwargs: Any) -> SchedulerService:
        """Singleton pattern - only one scheduler instance"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, database: Database | None = None, config: Any = None) -> None:
        """
        Initialize scheduler service

        Args:
            database: Database instance
            config: Application configuration
        """
        if self._initialized:
            return

        self.database = database
        self.config = config
        self.scheduler: BackgroundScheduler | None = None
        self._initialized = True

    @classmethod
    def get_initialized_instance(cls) -> SchedulerService | None:
        """Return the singleton instance if it has been initialized, else None."""
        inst = cls._instance
        if inst is not None and inst._initialized:
            return inst
        return None

    def start(self) -> None:
        """Start the scheduler"""
        assert self.config is not None
        assert self.database is not None
        if not self.config.SCHEDULER_ENABLED:
            logger.info("Scheduler is disabled in configuration")
            return

        if self.scheduler and self.scheduler.running:
            logger.warning("Scheduler is already running")
            return

        try:
            # Configure job stores - use MongoDB for persistence
            jobstores = {
                'default': MongoDBJobStore(
                    database=self.config.DATABASE_NAME,
                    collection='apscheduler_jobs',
                    client=self.database.client
                )
            }

            # Configure executors
            executors = {
                'default': ThreadPoolExecutor(max_workers=3)
            }

            # Job defaults
            job_defaults = {
                'coalesce': self.config.SCHEDULER_COALESCE,
                'max_instances': self.config.SCHEDULER_MAX_INSTANCES,
                'misfire_grace_time': self.config.SCHEDULER_MISFIRE_GRACE_TIME
            }

            # Create scheduler
            self.scheduler = BackgroundScheduler(
                jobstores=jobstores,
                executors=executors,
                job_defaults=job_defaults,
                timezone=ZoneInfo(self.config.SCHEDULER_TIMEZONE)
            )

            # Start scheduler
            self.scheduler.start()
            logger.info(f"Scheduler started with timezone {self.config.SCHEDULER_TIMEZONE}")

            # Load existing schedules from database
            self._load_schedules_from_database()

        except Exception as e:
            logger.error(f"Failed to start scheduler: {e}")
            raise

    def shutdown(self, wait: bool = True) -> None:
        """
        Shutdown the scheduler gracefully

        Args:
            wait: If True, wait for running jobs to complete
        """
        if self.scheduler and self.scheduler.running:
            logger.info("Shutting down scheduler...")
            self.scheduler.shutdown(wait=wait)
            logger.info("Scheduler shut down successfully")

    def _load_schedules_from_database(self) -> None:
        """Load all enabled schedules from database and register with APScheduler"""
        assert self.database is not None
        try:
            schedules = self.database.get_enabled_test_schedules()
            logger.info(f"Loading {len(schedules)} enabled schedules from database")

            for schedule in schedules:
                try:
                    self.register_schedule_with_apscheduler(schedule)
                except Exception as e:
                    logger.error(f"Failed to register schedule {schedule.id}: {e}")

        except Exception as e:
            logger.error(f"Failed to load schedules from database: {e}")

    def register_schedule_with_apscheduler(self, schedule: TestSchedule) -> None:
        """
        Register a schedule with APScheduler

        Args:
            schedule: TestSchedule to register
        """
        assert self.database is not None
        assert self.scheduler is not None
        if not schedule.enabled:
            logger.debug(f"Schedule {schedule.id} is disabled, skipping registration")
            return

        # Create trigger based on schedule type
        trigger = self._create_trigger(schedule)
        if not trigger:
            logger.warning(f"Could not create trigger for schedule {schedule.id}")
            return

        # Generate APScheduler job ID
        apscheduler_job_id = f"schedule_{schedule.id}"

        # Remove existing job if any
        try:
            existing_job = self.scheduler.get_job(apscheduler_job_id)
            if existing_job:
                self.scheduler.remove_job(apscheduler_job_id)
        except Exception:
            pass

        # Add job to scheduler - only pass schedule_id, retrieve db/config at runtime
        job = self.scheduler.add_job(
            func=execute_scheduled_test,
            trigger=trigger,
            id=apscheduler_job_id,
            args=[schedule.id],
            name=f"Scheduled test: {schedule.name}",
            replace_existing=True
        )

        # Update schedule with APScheduler job ID and next run time
        if schedule.id:
            self.database.set_test_schedule_apscheduler_id(schedule.id, apscheduler_job_id)

        if job.next_run_time:
            self.database.test_schedules.update_one(
                {"_id": schedule.mongo_id},
                {"$set": {"next_run_at": job.next_run_time}}
            )

        logger.info(f"Registered schedule '{schedule.name}' with APScheduler, next run: {job.next_run_time}")

    def _create_trigger(self, schedule: TestSchedule) -> DateTrigger | CronTrigger | None:
        """
        Create APScheduler trigger from schedule configuration

        Args:
            schedule: TestSchedule instance

        Returns:
            APScheduler trigger or None
        """
        timezone = ZoneInfo(schedule.preset_config.timezone)

        if schedule.schedule_type == ScheduleType.ONE_TIME:
            if not schedule.scheduled_datetime:
                logger.warning(f"One-time schedule {schedule.id} has no datetime set")
                return None
            return DateTrigger(
                run_date=schedule.scheduled_datetime,
                timezone=timezone
            )

        elif schedule.schedule_type == ScheduleType.CRON:
            if not schedule.cron_expression:
                logger.warning(f"Cron schedule {schedule.id} has no expression set")
                return None
            return CronTrigger.from_crontab(
                schedule.cron_expression,
                timezone=timezone
            )

        elif schedule.schedule_type == ScheduleType.DAILY:
            hour, minute = self._parse_time(schedule.preset_config.time)
            return CronTrigger(
                hour=hour,
                minute=minute,
                timezone=timezone
            )

        elif schedule.schedule_type == ScheduleType.WEEKLY:
            hour, minute = self._parse_time(schedule.preset_config.time)
            return CronTrigger(
                day_of_week=schedule.preset_config.day_of_week,
                hour=hour,
                minute=minute,
                timezone=timezone
            )

        elif schedule.schedule_type == ScheduleType.MONTHLY:
            hour, minute = self._parse_time(schedule.preset_config.time)
            return CronTrigger(
                day=schedule.preset_config.day_of_month,
                hour=hour,
                minute=minute,
                timezone=timezone
            )

        return None

    def _parse_time(self, time_str: str) -> tuple[int, int]:
        """
        Parse time string (HH:MM) into hour and minute

        Args:
            time_str: Time string in HH:MM format

        Returns:
            Tuple of (hour, minute)
        """
        try:
            parts = time_str.split(':')
            return int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            logger.warning(f"Invalid time format: {time_str}, defaulting to 02:00")
            return 2, 0

    def add_schedule(self, schedule: TestSchedule) -> bool:
        """
        Add a new schedule and register it with APScheduler

        Args:
            schedule: TestSchedule to add

        Returns:
            True if successful
        """
        assert self.database is not None
        try:
            # Save to database
            _schedule_id = self.database.create_test_schedule(schedule)
            doc = self.database.test_schedules.find_one({"_id": schedule.mongo_id})
            if doc:
                schedule.mongo_id = doc["_id"]

            # Register with APScheduler if enabled
            if schedule.enabled and self.scheduler and self.scheduler.running:
                self.register_schedule_with_apscheduler(schedule)

            return True

        except Exception as e:
            logger.error(f"Failed to add schedule: {e}")
            return False

    def update_schedule(self, schedule: TestSchedule) -> bool:
        """
        Update an existing schedule

        Args:
            schedule: Updated TestSchedule

        Returns:
            True if successful
        """
        assert self.database is not None
        try:
            # Update in database
            self.database.update_test_schedule(schedule)

            # Re-register with APScheduler
            if self.scheduler and self.scheduler.running:
                if schedule.enabled:
                    self.register_schedule_with_apscheduler(schedule)
                else:
                    # Remove from APScheduler if disabled
                    if schedule.id:
                        self.remove_from_apscheduler(schedule.id)

            return True

        except Exception as e:
            logger.error(f"Failed to update schedule: {e}")
            return False

    def remove_schedule(self, schedule_id: str) -> bool:
        """
        Remove a schedule

        Args:
            schedule_id: Schedule ID to remove

        Returns:
            True if successful
        """
        assert self.database is not None
        try:
            # Remove from APScheduler
            self.remove_from_apscheduler(schedule_id)

            # Delete from database
            self.database.delete_test_schedule(schedule_id)

            return True

        except Exception as e:
            logger.error(f"Failed to remove schedule: {e}")
            return False

    def remove_from_apscheduler(self, schedule_id: str) -> None:
        """
        Remove a schedule from APScheduler

        Args:
            schedule_id: Schedule ID
        """
        if not self.scheduler or not self.scheduler.running:
            return

        apscheduler_job_id = f"schedule_{schedule_id}"
        try:
            job = self.scheduler.get_job(apscheduler_job_id)
            if job:
                self.scheduler.remove_job(apscheduler_job_id)
                logger.info(f"Removed schedule {schedule_id} from APScheduler")
        except Exception as e:
            logger.warning(f"Could not remove job {apscheduler_job_id}: {e}")

    def toggle_schedule(self, schedule_id: str, enabled: bool) -> bool:
        """
        Enable or disable a schedule

        Args:
            schedule_id: Schedule ID
            enabled: True to enable, False to disable

        Returns:
            True if successful
        """
        assert self.database is not None
        try:
            # Update in database
            self.database.toggle_test_schedule(schedule_id, enabled)

            # Update APScheduler
            if self.scheduler and self.scheduler.running:
                if enabled:
                    schedule = self.database.get_test_schedule(schedule_id)
                    if schedule:
                        self.register_schedule_with_apscheduler(schedule)
                else:
                    self.remove_from_apscheduler(schedule_id)

            return True

        except Exception as e:
            logger.error(f"Failed to toggle schedule: {e}")
            return False

    def run_now(self, schedule_id: str) -> str | None:
        """
        Trigger a schedule to run immediately

        Args:
            schedule_id: Schedule ID to run

        Returns:
            Job ID if triggered successfully, None otherwise
        """
        assert self.database is not None
        try:
            schedule = self.database.get_test_schedule(schedule_id)
            if not schedule:
                logger.error(f"Schedule {schedule_id} not found")
                return None

            # Execute synchronously in a thread
            job_id = execute_scheduled_test(schedule_id)
            return job_id

        except Exception as e:
            logger.error(f"Failed to run schedule now: {e}")
            return None

    def get_next_run_times(self, schedule_id: str, count: int = 5) -> list[datetime]:
        """
        Get the next N run times for a schedule

        Args:
            schedule_id: Schedule ID
            count: Number of future run times to calculate

        Returns:
            List of datetime objects
        """
        assert self.database is not None
        schedule = self.database.get_test_schedule(schedule_id)
        if not schedule:
            return []

        trigger = self._create_trigger(schedule)
        if not trigger:
            return []

        # Calculate next run times
        run_times: list[datetime] = []
        next_time = datetime.now(ZoneInfo(schedule.preset_config.timezone))

        for _ in range(count):
            fire_time = trigger.get_next_fire_time(None, next_time)
            if fire_time:
                run_times.append(next_time)
                # Move slightly forward to get the next one
                next_time = next_time.replace(second=next_time.second + 1)
            else:
                break

        return run_times

    def get_job_status(self, schedule_id: str) -> dict[str, Any]:
        """
        Get the APScheduler job status for a schedule

        Args:
            schedule_id: Schedule ID

        Returns:
            Status dictionary
        """
        if not self.scheduler or not self.scheduler.running:
            return {"status": "scheduler_not_running"}

        apscheduler_job_id = f"schedule_{schedule_id}"
        job = self.scheduler.get_job(apscheduler_job_id)

        if not job:
            return {"status": "not_registered"}

        return {
            "status": "registered",
            "next_run_time": job.next_run_time,
            "job_id": job.id,
            "name": job.name
        }


def execute_scheduled_test(schedule_id: str) -> str | None:
    """
    Execute a scheduled test - this is the job function called by APScheduler

    Args:
        schedule_id: Schedule ID to execute

    Returns:
        Job ID if successful, None otherwise
    """
    from auto_a11y.core.job_manager import JobManager
    from auto_a11y.core.testing_job import TestingJob

    logger.info(f"Executing scheduled test for schedule {schedule_id}")

    # Get database and config from the scheduler service instance
    scheduler_service = get_scheduler_service()
    if not scheduler_service:
        logger.error("Scheduler service not available")
        return None

    database = scheduler_service.database
    config = scheduler_service.config

    if not database or not config:
        logger.error("Scheduler service has no database or config")
        return None

    job_id = "unknown"
    try:
        # Get schedule
        schedule = database.get_test_schedule(schedule_id)
        if not schedule:
            logger.error(f"Schedule {schedule_id} not found")
            return None

        if not schedule.enabled:
            logger.warning(f"Schedule {schedule_id} is disabled, skipping execution")
            return None

        # Generate job ID
        job_id = f"scheduled_{schedule_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Update schedule status
        database.update_test_schedule_run_status(
            schedule_id=schedule_id,
            job_id=job_id,
            status=ScheduleRunStatus.RUNNING
        )

        # Get website
        website = database.get_website(schedule.website_id)
        if not website:
            logger.error(f"Website {schedule.website_id} not found")
            database.update_test_schedule_run_status(
                schedule_id=schedule_id,
                job_id=job_id,
                status=ScheduleRunStatus.FAILED
            )
            return None

        # Get pages to test
        pages = database.get_pages(schedule.website_id)
        page_ids: list[str] = [p.id for p in pages if p.id]

        if not page_ids:
            logger.warning(f"No pages found for website {schedule.website_id}")
            database.update_test_schedule_run_status(
                schedule_id=schedule_id,
                job_id=job_id,
                status=ScheduleRunStatus.SUCCESS
            )
            return job_id

        # Determine AI page configuration
        ai_page_ids: set[str] = set()
        test_config = schedule.test_config
        if test_config.run_ai_tests:
            if test_config.ai_pages_mode == AITestMode.ALL:
                ai_page_ids = set(page_ids)
            elif test_config.ai_pages_mode == AITestMode.SELECTED:
                ai_page_ids = set(test_config.ai_page_ids)
            # AITestMode.NONE = empty set

        # Browser configuration
        browser_config = {
            'headless': config.BROWSER_HEADLESS,
            'timeout': config.BROWSER_TIMEOUT,
            'viewport': {
                'width': config.BROWSER_VIEWPORT_WIDTH,
                'height': config.BROWSER_VIEWPORT_HEIGHT
            }
        }

        # Get job manager
        job_manager = JobManager.get_instance(database)

        # Process users to test with
        user_ids_to_test = schedule.project_user_ids or ['']  # Empty string = guest

        # Run tests for each user
        async def run_tests() -> None:
            for i, user_id in enumerate(user_ids_to_test):
                is_last_user = (i == len(user_ids_to_test) - 1)

                testing_job = TestingJob(
                    job_manager=job_manager,
                    website_id=schedule.website_id,
                    job_id=job_id,
                    page_ids=page_ids,
                    user_id=None,  # Scheduled, no app user
                    session_id=None,
                    test_all=True,
                    website_user_id=user_id if user_id else None
                )

                # Run the test
                # Note: For selective AI testing, we'd need to modify the test runner
                # to accept per-page AI configuration
                await testing_job.run(
                    database=database,
                    browser_config=browser_config,
                    take_screenshot=test_config.take_screenshots,
                    run_ai_analysis=test_config.run_ai_tests and len(ai_page_ids) > 0,
                    ai_api_key=config.CLAUDE_API_KEY,
                    skip_completion=not is_last_user
                )

        # Run async tests in new event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_tests())
        finally:
            loop.close()

        # Update schedule status to success
        database.update_test_schedule_run_status(
            schedule_id=schedule_id,
            job_id=job_id,
            status=ScheduleRunStatus.SUCCESS
        )

        logger.info(f"Scheduled test completed successfully for schedule {schedule_id}")
        return job_id

    except Exception as e:
        logger.error(f"Scheduled test failed for schedule {schedule_id}: {e}")

        # Update schedule status to failed
        try:
            database.update_test_schedule_run_status(
                schedule_id=schedule_id,
                job_id=job_id if job_id else 'unknown',
                status=ScheduleRunStatus.FAILED
            )
        except Exception:
            pass

        return None


# Singleton instance getter
def get_scheduler_service() -> SchedulerService | None:
    """Get the scheduler service singleton instance"""
    return SchedulerService.get_initialized_instance()
