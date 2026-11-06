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
# QUOTA ABUSE DETECTION
# =============================================================================


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=300,
    acks_late=True,
    soft_time_limit=300,
    time_limit=600,
)
def detect_quota_abuse(self) -> dict[str, Any]:
    """
    Daily task: flag fingerprints that have exceeded their QuotaTier download limits.

    Checks RegistryEvent counts against the assigned QuotaTier's daily/hourly limits
    and creates a BehaviorInsight with severity=HIGH when abuse is detected.
    """
    try:
        from apps.devices.models import (
            BehaviorInsight,
            DeviceFingerprint,
            QuotaTier,
            RegistryEvent,
        )

        flagged = 0
        today = timezone.now().date()
        one_hour_ago = timezone.now() - timedelta(hours=1)

        # Get the most permissive tier for comparison
        tiers = list(QuotaTier.objects.order_by("daily_download_limit"))
        if not tiers:
            return {"status": "no_quota_tiers", "flagged": 0}

        default_tier = tiers[0]  # Most restrictive as fallback

        for fp in DeviceFingerprint.objects.filter(is_bot=False).iterator(
            chunk_size=500
        ):
            # Count downloads today
            daily_count = RegistryEvent.objects.filter(
                fingerprint=fp,
                action=RegistryEvent.Action.DOWNLOAD_COMPLETE,
                created_at__date=today,
            ).count()

            # Count downloads in last hour
            hourly_count = RegistryEvent.objects.filter(
                fingerprint=fp,
                action=RegistryEvent.Action.DOWNLOAD_COMPLETE,
                created_at__gte=one_hour_ago,
            ).count()

            # Find appropriate tier based on trust score
            tier = default_tier
            trust_score = getattr(fp, "trust_score", None)
            if trust_score is not None:
                for t in tiers:
                    if t.min_trust_score <= trust_score.score <= t.max_trust_score:
                        tier = t
                        break

            abuse_reasons: list[str] = []
            if daily_count > tier.daily_download_limit * 2:
                abuse_reasons.append(
                    f"daily_downloads={daily_count} limit={tier.daily_download_limit}"
                )
            if hourly_count > tier.hourly_download_limit * 2:
                abuse_reasons.append(
                    f"hourly_downloads={hourly_count} limit={tier.hourly_download_limit}"
                )

            if not abuse_reasons:
                continue

            # Create or update abuse insight
            _, created = BehaviorInsight.objects.get_or_create(
                fingerprint=fp,
                status=BehaviorInsight.Status.OPEN,
                defaults={
                    "severity": BehaviorInsight.Severity.HIGH,
                    "recommendation": "Exceeded quota tier limits. Investigate or block.",
                    "related_user": fp.user,
                    "metadata": {
                        "abuse_reasons": abuse_reasons,
                        "daily_count": daily_count,
                        "hourly_count": hourly_count,
                        "tier": tier.name,
                        "detected_at": timezone.now().isoformat(),
                    },
                },
            )
            if created:
                flagged += 1

        logger.info("detect_quota_abuse: flagged %d abusers", flagged)
        return {"status": "success", "flagged": flagged}
    except DatabaseError as exc:
        logger.exception("detect_quota_abuse failed")
        raise self.retry(exc=exc, countdown=120 * (2**self.request.retries)) from exc


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
