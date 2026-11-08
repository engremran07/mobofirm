"""Idempotent, event-driven SEO jobs (a transactional outbox in ``SEOJob``).

Content signals only *record* a job after commit; a worker (Celery beat or the
``seo_process_jobs`` command) executes it. Duplicate deliveries collapse on
``job_key``; failures stay visible with their error; a job is ``succeeded`` only
after every action it attempted was persisted and verified.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import autonomy, indexnow, scan
from .ledger import LedgerError
from .models import ChangeLedger, SEOJob, SEOSettings
from .siteurls import absolute_url

logger = logging.getLogger(__name__)

CONTENT_CHANGED = "content_changed"
STALE_AFTER = timedelta(minutes=15)
DEBOUNCE_SECONDS = 3600


class RetryableJobError(Exception):
    """A transient failure; the job is retried with backoff."""


@dataclass
class Outcome:
    status: str
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""


def record_content_change(
    label: str,
    pk: str,
    *,
    url: str,
    public: bool,
    previous: tuple[str, bool] | None,
    now: datetime | None = None,
) -> SEOJob | None:
    """Record one debounced job per object/state per hour; ``None`` when off."""
    row = SEOSettings.get_solo()
    if not row.seo_enabled or row.autonomy_mode == SEOSettings.AutonomyMode.OFF:
        return None
    moment = now or timezone.now()
    previous_url, previous_public = previous or ("", False)
    bucket = int(moment.timestamp() // DEBOUNCE_SECONDS)
    fingerprint = hashlib.sha256(
        f"{url}|{public}|{previous_url}|{previous_public}|{bucket}".encode()
    ).hexdigest()[:16]
    job_key = f"{CONTENT_CHANGED}:{label}:{pk}:{fingerprint}"
    try:
        job, _created = SEOJob.objects.get_or_create(
            job_key=job_key,
            defaults={
                "kind": CONTENT_CHANGED,
                "available_at": moment,
                "payload": {
                    "label": label,
                    "pk": pk,
                    "url": url,
                    "public": public,
                    "previous_url": previous_url,
                    "previous_public": previous_public,
                },
            },
        )
    except IntegrityError:
        return SEOJob.objects.filter(job_key=job_key).first()
    return job


def _content_changed(job: SEOJob) -> Outcome:
    row = SEOSettings.get_solo()
    mode = row.autonomy_mode
    if not row.seo_enabled or mode == SEOSettings.AutonomyMode.OFF:
        return Outcome(SEOJob.Status.SKIPPED, {"reason": "automation is off"})
    payload = job.payload
    url, public = str(payload["url"]), bool(payload["public"])
    previous_url = str(payload.get("previous_url", ""))
    previous_public = bool(payload.get("previous_public"))
    result: dict[str, Any] = {}
    acted = False
    failure = ""

    if previous_public and public and previous_url and previous_url != url:
        change = autonomy.propose_redirect(
            previous_url,
            url,
            source=f"event:{payload['label']}",
            reason=f"{payload['label']} {payload['pk']} moved from {previous_url}.",
        )
        if change is not None:
            acted = True
            result["redirect"] = {"ledger": change.pk, "status": change.status}
            if change.status == ChangeLedger.Status.FAILED:
                failure = f"Redirect {previous_url} -> {url} failed: {change.error}"

    notify = [
        absolute_url(path)
        for path, was_public in ((url, public), (previous_url, previous_public))
        if path and was_public and path != ""
    ]
    can_submit = row.indexnow_enabled and mode in {
        SEOSettings.AutonomyMode.SAFE_AUTOPILOT,
        SEOSettings.AutonomyMode.FULL,
    }
    if notify and can_submit:
        try:
            submitted = indexnow.submit([u for u in notify if u])
        except indexnow.IndexNowError as error:
            if error.retryable:
                raise RetryableJobError(str(error)) from error
            raise ValidationError(str(error)) from error
        acted = True
        result["indexnow"] = {
            "submitted": submitted.submitted,
            "status": submitted.status,
        }
    else:
        result["indexnow"] = "not submitted: disabled or mode does not allow it"

    if public and url:
        result["rescan"] = scan.rescan_paths([url], duplicates=False)

    if failure:
        return Outcome(SEOJob.Status.FAILED, result, failure)
    status = SEOJob.Status.SUCCEEDED if acted else SEOJob.Status.SKIPPED
    return Outcome(status, result)


HANDLERS: dict[str, Callable[[SEOJob], Outcome]] = {CONTENT_CHANGED: _content_changed}


def reclaim_stale(now: datetime) -> int:
    """Return jobs abandoned by a crashed worker to the queue."""
    return SEOJob.objects.filter(
        status=SEOJob.Status.RUNNING, started_at__lt=now - STALE_AFTER
    ).update(status=SEOJob.Status.PENDING, available_at=now)


def _claim(now: datetime) -> SEOJob | None:
    with transaction.atomic():
        job = (
            SEOJob.objects.select_for_update(skip_locked=True)
            .filter(status=SEOJob.Status.PENDING, available_at__lte=now)
            .order_by("available_at", "pk")
            .first()
        )
        if job is None:
            return None
        job.status = SEOJob.Status.RUNNING
        job.attempts += 1
        job.started_at = now
        job.save()
        return job


def _finish(job: SEOJob, status: str, result: dict[str, Any], error: str = "") -> None:
    job.status = status
    job.result = result
    job.last_error = error
    job.finished_at = timezone.now()
    job.save()


def run_job(job: SEOJob, now: datetime) -> SEOJob:
    handler = HANDLERS.get(job.kind)
    if handler is None:
        _finish(job, SEOJob.Status.FAILED, {}, f"Unknown job kind {job.kind!r}.")
        return job
    try:
        outcome = handler(job)
    except RetryableJobError as error:
        if job.attempts < job.max_attempts:
            job.status = SEOJob.Status.PENDING
            job.available_at = now + timedelta(minutes=2**job.attempts)
            job.last_error = str(error)
            job.save()
            logger.warning("SEO job %s will retry: %s", job.job_key, error)
        else:
            _finish(job, SEOJob.Status.FAILED, {}, f"Retries exhausted: {error}")
        return job
    except (ValidationError, ObjectDoesNotExist, LedgerError, KeyError) as error:
        logger.error("SEO job %s failed: %s", job.job_key, error)
        _finish(job, SEOJob.Status.FAILED, {}, str(error))
        return job
    _finish(job, outcome.status, outcome.result, outcome.error)
    return job


def process_pending(limit: int = 25, now: datetime | None = None) -> dict[str, int]:
    moment = now or timezone.now()
    counts = {"reclaimed": reclaim_stale(moment)}
    for _ in range(limit):
        job = _claim(moment)
        if job is None:
            break
        finished = run_job(job, moment)
        counts[finished.status] = counts.get(finished.status, 0) + 1
    return counts


def process_queue_now(
    *,
    limit: int = 2000,
    budget_seconds: float = 20.0,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, int]:
    """Drain the outbox synchronously (no Celery needed) within a time budget.

    ``remaining`` reports the jobs still waiting when the budget ran out.
    """
    deadline = clock() + budget_seconds
    totals: dict[str, int] = {}
    handled = 0
    while handled < limit and clock() < deadline:
        batch = process_pending(limit=min(50, limit - handled))
        done = sum(total for name, total in batch.items() if name != "reclaimed")
        for name, total in batch.items():
            totals[name] = totals.get(name, 0) + total
        if not done:
            break
        handled += done
    totals["remaining"] = SEOJob.objects.filter(
        status=SEOJob.Status.PENDING, available_at__lte=timezone.now()
    ).count()
    return totals


def pending_approvals() -> int:
    return ChangeLedger.objects.filter(status=ChangeLedger.Status.PROPOSED).count()
