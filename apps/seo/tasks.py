"""Celery tasks. Only operations that actually run are scheduled (see CELERY_BEAT_SCHEDULE)."""

from __future__ import annotations

from typing import Any

from celery import shared_task

from . import jobs, scan
from .crawler import CrawlUnavailable, run_crawl
from .models import CrawlRun


@shared_task(name="apps.seo.tasks.process_pending_jobs")
def process_pending_jobs(limit: int = 25) -> dict[str, int]:
    """Drain the SEO outbox; failures stay visible on the job rows."""
    return jobs.process_pending(limit=limit)


@shared_task(name="apps.seo.tasks.scheduled_scan")
def scheduled_scan() -> dict[str, Any]:
    """Scan every public URL in the background; resumes a partial earlier scan."""
    try:
        run = scan.run_scan(
            trigger=CrawlRun.Trigger.SCHEDULED,
            budget_seconds=scan.BACKGROUND_BUDGET_SECONDS,
        )
    except scan.ScanUnavailable as reason:
        return {"status": "skipped", "reason": str(reason)}
    return {
        "status": run.status,
        "run": run.pk,
        "scanned": run.pages_crawled,
        "total": run.urls_total,
        "partial": run.partial,
    }


@shared_task(name="apps.seo.tasks.scheduled_crawl")
def scheduled_crawl() -> dict[str, Any]:
    """Crawl and audit the deployed site; skipped with a reason when unavailable."""
    try:
        run = run_crawl(trigger=CrawlRun.Trigger.SCHEDULED)
    except CrawlUnavailable as reason:
        return {"status": "skipped", "reason": str(reason)}
    return {
        "status": run.status,
        "run": run.pk,
        "pages": run.pages_crawled,
        "health_percent": run.health_percent,
    }
