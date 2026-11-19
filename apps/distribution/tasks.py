from __future__ import annotations

from celery import shared_task

from apps.distribution import automation, syndication, videos
from apps.distribution.services import process_due_jobs


@shared_task(name="apps.distribution.tasks.process_share_jobs")
def process_share_jobs() -> dict[str, int]:
    """Every minute: deliver due shares, retrying transient failures.

    Retention purge and evergreen scheduling piggy-back here and run at most once an
    hour (``automation.housekeeping`` throttles itself).
    """
    summary = process_due_jobs()
    pings = syndication.process_due()
    automation.housekeeping()
    return {
        "sent": summary.sent,
        "retried": summary.retried,
        "failed": summary.failed,
        "deferred": summary.deferred,
        "pings_sent": pings["sent"],
        "pings_failed": pings["failed"],
    }


@shared_task(name="apps.distribution.tasks.run_housekeeping")
def run_housekeeping() -> dict[str, int]:
    """Force the retention purge and evergreen pass (staff or ops)."""
    return automation.housekeeping(force=True)


@shared_task(
    name="apps.distribution.tasks.render_video",
    soft_time_limit=1800,
    time_limit=1860,
    ignore_result=True,
)
def render_video(job_id: int) -> str:
    """Render one motion video. The renderer enforces ``video_render_timeout`` itself and
    kills Chromium and ffmpeg; the Celery limits are the outer safety net."""
    return videos.render_job(job_id)
