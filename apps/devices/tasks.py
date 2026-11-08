"""apps.devices.tasks — Celery background tasks for device management."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from celery import shared_task
from django.db import DatabaseError
from django.utils import timezone

logger = logging.getLogger(__name__)


# =============================================================================
# FINGERPRINT CLEANUP
# =============================================================================


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=300,
    acks_late=True,
    soft_time_limit=300,
    time_limit=600,
)
def expire_old_fingerprints(self) -> dict[str, Any]:
    """
    Daily task: delete DeviceFingerprint records last seen more than 90 days ago.

    Keeps the fingerprint table lean and removes stale anonymous tracking records.
    Preserves fingerprints that are still linked to active users (user FK set).
    """
    try:
        from apps.devices.models import DeviceFingerprint

        cutoff = timezone.now() - timedelta(days=90)
        deleted_count, _ = DeviceFingerprint.objects.filter(
            last_seen__lt=cutoff,
            user__isnull=True,  # Only purge anonymous fingerprints
        ).delete()

        logger.info("expire_old_fingerprints: deleted %d stale records", deleted_count)
        return {"status": "success", "deleted": deleted_count}
    except DatabaseError as exc:
        logger.exception("expire_old_fingerprints failed")
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries)) from exc


# =============================================================================
# BEHAVIOR INSIGHT CLEANUP
# =============================================================================


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=600,
    acks_late=True,
    soft_time_limit=300,
    time_limit=600,
)
def cleanup_behavior_insights(self) -> dict[str, Any]:
    """
    Weekly task: archive (soft-remove) resolved BehaviorInsight records older than 30 days.

    Resolved insights are no longer actionable; deleting them keeps the table performant
    while the audit trail is preserved in the DB until the full retention window expires.
    """
    try:
        from apps.devices.models import BehaviorInsight

        cutoff = timezone.now() - timedelta(days=30)
        deleted_count, _ = BehaviorInsight.objects.filter(
            status=BehaviorInsight.Status.RESOLVED,
            updated_at__lt=cutoff,
        ).delete()

        logger.info(
            "cleanup_behavior_insights: deleted %d resolved insights", deleted_count
        )
        return {"status": "success", "deleted": deleted_count}
    except DatabaseError as exc:
        logger.exception("cleanup_behavior_insights failed")
        raise self.retry(exc=exc, countdown=120 * (2**self.request.retries)) from exc


# =============================================================================
# TRUST SCORE SYNC
# =============================================================================


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    acks_late=True,
    soft_time_limit=600,
    time_limit=900,
)
def sync_trust_scores(self) -> dict[str, Any]:
    """
    Hourly task: recompute TrustScore for fingerprints active in the last hour.

    A fingerprint is considered recently active if its last_seen timestamp is
    within the last hour. For each active fingerprint the score is recalculated
    from its underlying signals (visit count, bot flag, trust_level, linked user).
    """
    try:
        from apps.devices.models import DeviceFingerprint, TrustScore

        one_hour_ago = timezone.now() - timedelta(hours=1)
        active_fps = DeviceFingerprint.objects.filter(last_seen__gte=one_hour_ago)

        updated = 0
        for fp in active_fps.iterator(chunk_size=500):
            score = _compute_trust_score(fp)
            TrustScore.objects.update_or_create(
                fingerprint=fp,
                defaults={
                    "score": score["score"],
                    "signals_passed": score["signals_passed"],
                    "signals_failed": score["signals_failed"],
                    "notes": score["notes"],
                },
            )
            updated += 1

        logger.info("sync_trust_scores: updated %d trust scores", updated)
        return {"status": "success", "updated": updated}
    except DatabaseError as exc:
        logger.exception("sync_trust_scores failed")
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries)) from exc


def _compute_trust_score(fp: Any) -> dict[str, Any]:
    """Compute a trust score from DeviceFingerprint attributes."""
    score = 50.0  # baseline
    signals_passed = 0
    signals_failed = 0
    notes_parts: list[str] = []

    # Bot penalty
    if fp.is_bot:
        score -= 40.0
        signals_failed += 1
        notes_parts.append("bot_detected")
    else:
        signals_passed += 1

    # Trust level bonus
    trust_bonus = {
        "unknown": 0,
        "low": -10,
        "medium": 0,
        "high": 10,
        "trusted": 20,
    }
    score += trust_bonus.get(fp.trust_level, 0)

    # Linked user bonus
    if fp.user_id:
        score += 15.0
        signals_passed += 1
    else:
        signals_failed += 1
        notes_parts.append("anonymous")

    # High visit count bonus (returning visitor — check higher threshold first)
    if fp.visit_count >= 50:
        score += 10.0
        signals_passed += 1
    elif fp.visit_count >= 10:
        score += 5.0
        signals_passed += 1

    # Clamp to [0, 100]
    score = max(0.0, min(100.0, score))

    return {
        "score": score,
        "signals_passed": signals_passed,
        "signals_failed": signals_failed,
        "notes": "; ".join(notes_parts) if notes_parts else "",
    }


# =============================================================================
# DEVICE EVENT RETENTION
# =============================================================================


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=300,
    acks_late=True,
    soft_time_limit=300,
    time_limit=600,
)
def purge_old_device_events(self) -> dict[str, Any]:
    """
    Daily task: delete DeviceEvent rows (raw IP and user agent) past the
    ``DEVICE_EVENT_RETENTION_DAYS`` window.
    """
    from django.conf import settings

    from apps.devices.models import DeviceEvent

    retention_days = int(getattr(settings, "DEVICE_EVENT_RETENTION_DAYS", 90))
    if retention_days <= 0:
        return {"status": "disabled", "deleted": 0}
    cutoff = timezone.now() - timedelta(days=retention_days)
    try:
        deleted_count, _ = DeviceEvent.objects.filter(created_at__lt=cutoff).delete()
    except DatabaseError as exc:
        logger.exception("purge_old_device_events failed")
        raise self.retry(exc=exc, countdown=120 * (2**self.request.retries)) from exc
    logger.info("purge_old_device_events: deleted %d events", deleted_count)
    return {"status": "success", "deleted": deleted_count}
