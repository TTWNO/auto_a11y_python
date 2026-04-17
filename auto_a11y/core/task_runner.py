"""
Simple async task runner for background jobs
In production, this would be replaced with Celery or similar
"""
from __future__ import annotations

import logging
from typing import Any
from collections.abc import Callable
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import uuid

logger = logging.getLogger(__name__)


class TaskRunner:
    """Simple in-memory task runner"""

    _instance: TaskRunner | None = None
    _initialized: bool

    def __new__(cls) -> TaskRunner:
        """Singleton pattern"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        """Initialize task runner"""
        if self._initialized:
            return

        self.tasks: dict[str, Task] = {}
        self.executor = ThreadPoolExecutor(max_workers=5)
        self._initialized = True

    def start(self) -> None:
        """Start task runner"""
        logger.info("Task runner started")

    def stop(self, timeout: float = 30) -> None:
        """Stop task runner, waiting up to *timeout* seconds for running tasks."""
        # Mark all running tasks as cancelled so workers can check and exit early
        for _task_id, task in self.tasks.items():
            if task.status == 'running':
                task.cancel()

        # Wait for the executor to drain.  cancel_futures=True (Python 3.9+)
        # prevents queued-but-not-started work from launching.
        try:
            self.executor.shutdown(wait=True, cancel_futures=True)
        except TypeError:
            # Python 3.8 doesn't support cancel_futures
            self.executor.shutdown(wait=True)
        logger.info("Task runner stopped")

    def submit_task(
        self,
        func: Callable[..., Any],
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
        task_id: str | None = None
    ) -> str:
        """
        Submit a task for execution

        Args:
            func: Function to execute
            args: Function arguments
            kwargs: Function keyword arguments
            task_id: Optional task ID

        Returns:
            Task ID
        """
        if kwargs is None:
            kwargs = {}

        # Generate task ID if not provided
        if not task_id:
            task_id = str(uuid.uuid4())

        # Create task
        task = Task(task_id, func, args, kwargs)
        self.tasks[task_id] = task

        # Run task in thread pool (all report/test tasks are synchronous)
        self.executor.submit(self._run_sync_task, task)

        logger.info(f"Submitted task: {task_id}")
        return task_id

    def _run_sync_task(self, task: Task) -> None:
        """Run sync task"""
        task.status = 'running'
        task.started_at = datetime.now()

        try:
            task.result = task.func(*task.args, **task.kwargs)
            task.status = 'completed'
        except Exception as e:
            logger.error(f"Task {task.task_id} failed: {e}")
            task.status = 'failed'
            task.error = str(e)
        finally:
            task.completed_at = datetime.now()

    def get_task_status(self, task_id: str) -> dict[str, Any] | None:
        """
        Get task status

        Args:
            task_id: Task ID

        Returns:
            Task status or None
        """
        task = self.tasks.get(task_id)
        if not task:
            return None

        return {
            'task_id': task.task_id,
            'status': task.status,
            'started_at': task.started_at.isoformat() if task.started_at else None,
            'completed_at': task.completed_at.isoformat() if task.completed_at else None,
            'error': task.error,
            'result': task.result if task.status == 'completed' else None
        }

    def get_active_tasks(self) -> list[str]:
        """
        Get IDs of tasks that are still pending or running.

        Returns:
            List of active task IDs
        """
        return [
            task_id
            for task_id, task in self.tasks.items()
            if task.status in ('pending', 'running')
        ]

    def cancel_task(self, task_id: str) -> bool:
        """
        Cancel a task

        Args:
            task_id: Task ID

        Returns:
            True if cancelled
        """
        task = self.tasks.get(task_id)
        if not task or task.status != 'running':
            return False

        task.cancel()
        return True

    def cleanup_completed_tasks(self, max_age_seconds: int = 3600) -> None:
        """
        Clean up old completed tasks

        Args:
            max_age_seconds: Maximum age in seconds
        """
        now = datetime.now()
        to_remove: list[str] = []

        for task_id, task in self.tasks.items():
            if task.status in ['completed', 'failed', 'cancelled']:
                if task.completed_at:
                    age = (now - task.completed_at).total_seconds()
                    if age > max_age_seconds:
                        to_remove.append(task_id)

        for task_id in to_remove:
            del self.tasks[task_id]

        if to_remove:
            logger.info(f"Cleaned up {len(to_remove)} old tasks")


class Task:
    """Represents a background task"""

    def __init__(
        self,
        task_id: str,
        func: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:
        """
        Initialize task

        Args:
            task_id: Task ID
            func: Function to execute
            args: Function arguments
            kwargs: Function keyword arguments
        """
        self.task_id = task_id
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.status = 'pending'
        self.started_at: datetime | None = None
        self.completed_at: datetime | None = None
        self.result: Any = None
        self.error: str | None = None
        self._cancelled = False

    def cancel(self) -> None:
        """Cancel task"""
        self._cancelled = True
        self.status = 'cancelled'
        self.completed_at = datetime.now()
        logger.info(f"Task {self.task_id} marked as cancelled")


# Global task runner instance
task_runner = TaskRunner()
