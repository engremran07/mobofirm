"""
Queue Service - Task Queue Abstraction
=======================================

Unified interface for background task processing.
Supports multiple backends: Celery, Django-Q, or synchronous fallback.
"""

import importlib
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


class QueueBackend(ABC):
    """Abstract queue backend interface"""

    @abstractmethod
    def enqueue(self, task: str, *args, **kwargs) -> Any:
        """Enqueue a task for async execution"""

    @abstractmethod
    def enqueue_in(self, task: str, seconds: int, *args, **kwargs) -> Any:
        """Enqueue a delayed task"""

    @abstractmethod
    def get_status(self, task_id: str) -> dict:
        """Get task execution status"""


class CeleryBackend(QueueBackend):
    """Celery task queue implementation"""

    def enqueue(self, task: str, *args, **kwargs):
        try:
            from celery import current_app

            task_fn = current_app.tasks.get(task)
            if not task_fn:
                raise ValueError(f"Task {task} not found in Celery registry")
            result = task_fn.apply_async(args=args, kwargs=kwargs)
            return result.id
        except ImportError:
            logger.exception("Celery not installed")
            raise

    def enqueue_in(self, task: str, seconds: int, *args, **kwargs):
        try:
            from celery import current_app

            task_fn = current_app.tasks.get(task)
            if not task_fn:
                raise ValueError(f"Task {task} not found")
            result = task_fn.apply_async(args=args, kwargs=kwargs, countdown=seconds)
            return result.id
        except ImportError:
            logger.exception("Celery not installed")
            raise

    def get_status(self, task_id: str) -> dict:
        try:
            from celery.result import AsyncResult

            result: AsyncResult[Any] = AsyncResult(task_id)
            return {
                "status": result.status,
                "result": result.result,
                "traceback": result.traceback,
            }
        except ImportError:
            return {"status": "unknown", "error": "Celery not installed"}


class DjangoQBackend(QueueBackend):
    """Django-Q task queue implementation"""

    def enqueue(self, task: str, *args, **kwargs):
        try:
            async_task = importlib.import_module("django_q.tasks").async_task
        except ImportError:
            logger.exception("Django-Q not installed")
            raise
        return async_task(task, *args, **kwargs)

    def enqueue_in(self, task: str, seconds: int, *args, **kwargs):
        try:
            schedule = importlib.import_module("django_q.tasks").schedule
        except ImportError:
            logger.exception("Django-Q not installed")
            raise
        run_at = datetime.now() + timedelta(seconds=seconds)
        return schedule(task, *args, schedule_type="O", next_run=run_at, **kwargs)

    def get_status(self, task_id: str) -> dict:
        try:
            task_model = importlib.import_module("django_q.models").Task
        except ImportError:
            return {"status": "unknown", "error": "Django-Q not installed"}
        task = task_model.objects.filter(id=task_id).first()
        if not task:
            return {"status": "not_found"}
        return {
            "status": "success" if task.success else "failed",
            "result": task.result,
            "started": task.started,
            "stopped": task.stopped,
        }


class SyncBackend(QueueBackend):
    """Synchronous fallback - executes tasks immediately"""

    def enqueue(self, task: str, *args, **kwargs):
        """Execute task synchronously"""
        try:
            func = self._resolve_task(task)
            result = func(*args, **kwargs)
            return f"sync_{id(result)}"
        except Exception:
            logger.exception("Sync task execution failed")
            raise

    def enqueue_in(self, task: str, seconds: int, *args, **kwargs):
        """Cannot delay in sync mode - execute immediately with warning"""
        logger.warning(
            "Sync backend cannot delay tasks - executing %s immediately", task
        )
        return self.enqueue(task, *args, **kwargs)

    def get_status(self, task_id: str) -> dict:
        """Sync tasks complete immediately"""
        return {"status": "completed", "note": "Synchronous execution"}

    def _resolve_task(self, task_path: str):
        """Import and return the task function"""
        module_path, func_name = task_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        return getattr(module, func_name)


class QueueService:
    """
    Unified queue service supporting multiple backends.

    Usage:
        queue = QueueService()
        task_id = queue.enqueue('apps.blog.tasks.publish_post', post_id=123)
        status = queue.get_status(task_id)
    """

    def __init__(self, backend: str | None = None):
        from django.conf import settings
        from django.core.exceptions import ImproperlyConfigured

        resolved = str(backend or getattr(settings, "QUEUE_BACKEND", "sync"))

        backends: dict[str, type[QueueBackend]] = {
            "celery": CeleryBackend,
            "django_q": DjangoQBackend,
            "sync": SyncBackend,
        }

        if resolved not in backends:
            raise ImproperlyConfigured(
                f"Unknown QUEUE_BACKEND {resolved!r}; expected one of {sorted(backends)}."
            )
        self.backend = backends[resolved]()
        self.backend_name = resolved
        logger.info("QueueService initialized with %s backend", resolved)

    def enqueue(self, task: str, *args, **kwargs) -> Any:
        """
        Enqueue a task for async execution.

        Args:
            task: Full path to task function (e.g., 'apps.blog.tasks.publish_post')
            *args: Positional arguments for the task
            **kwargs: Keyword arguments for the task

        Returns:
            Task ID for tracking
        """
        return self.backend.enqueue(task, *args, **kwargs)

    def enqueue_in(self, task: str, seconds: int, *args, **kwargs) -> Any:
        """
        Enqueue a delayed task.

        Args:
            task: Full path to task function
            seconds: Delay in seconds
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Task ID for tracking
        """
        return self.backend.enqueue_in(task, seconds, *args, **kwargs)

    def get_status(self, task_id: str) -> dict:
        """
        Get task execution status.

        Args:
            task_id: Task identifier returned by enqueue

        Returns:
            Dictionary with status information
        """
        return self.backend.get_status(task_id)

    @property
    def is_async(self) -> bool:
        """Check if backend supports true async execution"""
        return self.backend_name in ["celery", "django_q"]


__all__ = ["QueueService"]
