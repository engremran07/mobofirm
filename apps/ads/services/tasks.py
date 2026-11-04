from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def enqueue_aggregation() -> None:
    """
    Entry point for background aggregation of ad events.

    In deployments without a worker stack configured this function is a safe no-op,
    but callers can rely on it existing and being idempotent.
    """
    logger.info("ads.tasks.enqueue_aggregation invoked")
