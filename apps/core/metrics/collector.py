"""
Metrics Collector - Application Metrics
========================================

Collect and track application metrics.
"""

import logging
from typing import Any, ClassVar

from django.db import DatabaseError, models

logger = logging.getLogger(__name__)


class Metric(models.Model):
    """Stored metric data"""

    name = models.CharField(max_length=200, db_index=True)
    value = models.FloatField()
    tags = models.JSONField(default=dict, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-timestamp"]
        indexes = (models.Index(fields=["name", "timestamp"]),)

    def __str__(self):
        return f"{self.name}={self.value} @{self.timestamp}"


class MetricsCollector:
    """
    Collect application metrics.

    Usage:
        metrics = MetricsCollector()
        metrics.track('page_views', 1, tags={'page': 'home'})
        metrics.increment('api_calls')
        metrics.timing('db_query_time', 150)  # milliseconds
    """

    def __init__(self, backend: str | None = None):
        from django.conf import settings

        self.backend = backend or getattr(settings, "METRICS_BACKEND", "database")
        self.enabled = getattr(settings, "METRICS_ENABLED", True)

    def track(self, metric_name: str, value: float, tags: dict[str, Any] | None = None):
        """
        Track a metric value.

        Args:
            metric_name: Metric identifier
            value: Metric value
            tags: Optional tags for filtering
        """
        if not self.enabled:
            return

        tags = tags or {}

        try:
            if self.backend == "database":
                Metric.objects.create(name=metric_name, value=value, tags=tags)
            else:
                logger.warning(
                    "Metrics backend %r is not implemented; dropped %s",
                    self.backend,
                    metric_name,
                )
                return

            logger.debug("Tracked metric: %s=%s %s", metric_name, value, tags)

        except DatabaseError:
            logger.exception("Failed to track metric %s", metric_name)

    def increment(
        self, counter_name: str, amount: int = 1, tags: dict[str, Any] | None = None
    ):
        """
        Increment a counter.

        Args:
            counter_name: Counter identifier
            amount: Amount to increment by
            tags: Optional tags
        """
        self.track(counter_name, amount, tags)

    def decrement(
        self, counter_name: str, amount: int = 1, tags: dict[str, Any] | None = None
    ):
        """
        Decrement a counter.

        Args:
            counter_name: Counter identifier
            amount: Amount to decrement by
            tags: Optional tags
        """
        self.track(counter_name, -amount, tags)

    def timing(
        self,
        timer_name: str,
        milliseconds: float,
        tags: dict[str, Any] | None = None,
    ):
        """
        Track timing/duration.

        Args:
            timer_name: Timer identifier
            milliseconds: Duration in milliseconds
            tags: Optional tags
        """
        self.track(timer_name, milliseconds, tags)

    def gauge(self, gauge_name: str, value: float, tags: dict[str, Any] | None = None):
        """
        Set a gauge value.

        Args:
            gauge_name: Gauge identifier
            value: Current value
            tags: Optional tags
        """
        self.track(gauge_name, value, tags)

    def get_metrics(
        self,
        metric_name: str,
        start_time: Any | None = None,
        end_time: Any | None = None,
        tags: dict[str, Any] | None = None,
    ):
        """
        Query metrics from database.

        Args:
            metric_name: Metric name to query
            start_time: Start of time range
            end_time: End of time range
            tags: Filter by tags

        Returns:
            QuerySet of metrics
        """
        if self.backend != "database":
            logger.warning("get_metrics only works with database backend")
            return []

        qs = Metric.objects.filter(name=metric_name)

        if start_time:
            qs = qs.filter(timestamp__gte=start_time)

        if end_time:
            qs = qs.filter(timestamp__lte=end_time)

        if tags:
            for key, value in tags.items():
                qs = qs.filter(tags__contains={key: value})

        return qs


# Context manager for timing operations
class Timer:
    """
    Context manager for timing operations.

    Usage:
        with Timer('db_query', tags={'table': 'posts'}):
            # Code to time
            posts = Post.objects.all()
    """

    def __init__(self, metric_name: str, tags: dict[str, Any] | None = None):
        self.metric_name = metric_name
        self.tags = tags or {}
        self.collector = MetricsCollector()
        self.start_time: float | None = None

    def __enter__(self):
        import time

        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        import time

        if self.start_time:
            duration_ms = (time.time() - self.start_time) * 1000
            self.collector.timing(self.metric_name, duration_ms, self.tags)


__all__ = ["Metric", "MetricsCollector", "Timer"]
