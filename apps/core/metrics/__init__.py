"""
Metrics Package
===============

Application metrics collection and tracking.
"""

from .collector import Metric, MetricsCollector, Timer

# Global metrics instance
metrics = MetricsCollector()

__all__ = ["Metric", "MetricsCollector", "Timer", "metrics"]
