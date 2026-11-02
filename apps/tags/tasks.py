"""
Celery tasks for tag operations.
Background processing for analytics, trending, and notifications.
"""

from __future__ import annotations

import logging

try:
    from celery import shared_task
except Exception:  # pragma: no cover - fallback when Celery not installed

    def shared_task(*dargs, **dkwargs):  # type: ignore[assignment]
        def decorator(func):
            return func

        if dargs and callable(dargs[0]):
            return dargs[0]
        return decorator


from django.contrib.auth import get_user_model

from apps.tags.models import Tag
from apps.tags.services.tag_service import TagService

logger = logging.getLogger(__name__)
User = get_user_model()


@shared_task(
    acks_late=True,
    max_retries=3,
    default_retry_delay=120,
    soft_time_limit=120,
    time_limit=180,
)
def update_trending_tags_hourly():
    """
    Update hourly trending tags.
    Run every hour via cron/celery beat.
    """
    service = TagService()
    service.update_trending_tags("hourly")
    logger.info("Updated hourly trending tags")


@shared_task(
    acks_late=True,
    max_retries=3,
    default_retry_delay=300,
    soft_time_limit=120,
    time_limit=180,
)
def update_trending_tags_daily():
    """
    Update daily trending tags.
    Run once per day via cron/celery beat.
    """
    service = TagService()
    service.update_trending_tags("daily")
    logger.info("Updated daily trending tags")


@shared_task(
    acks_late=True,
    max_retries=3,
    default_retry_delay=600,
    soft_time_limit=120,
    time_limit=180,
)
def update_trending_tags_weekly():
    """
    Update weekly trending tags.
    Run once per week via cron/celery beat.
    """
    service = TagService()
    service.update_trending_tags("weekly")
    logger.info("Updated weekly trending tags")


@shared_task(
    acks_late=True,
    max_retries=3,
    default_retry_delay=3600,
    soft_time_limit=120,
    time_limit=180,
)
def update_trending_tags_monthly():
    """
    Update monthly trending tags.
    Run once per month via cron/celery beat.
    """
    service = TagService()
    service.update_trending_tags("monthly")
    logger.info("Updated monthly trending tags")


@shared_task(
    acks_late=True,
    max_retries=3,
    default_retry_delay=60,
    soft_time_limit=30,
    time_limit=60,
)
def update_tag_analytics(tag_id: int):
    """
    Update analytics for specific tag.
    """
    service = TagService()
    service.update_tag_analytics(tag_id)
    logger.info(f"Updated analytics for tag {tag_id}")


@shared_task(
    acks_late=True,
    max_retries=2,
    default_retry_delay=300,
    soft_time_limit=300,
    time_limit=600,
)
def update_all_tag_analytics():
    """
    Update analytics for all active tags.
    Run daily via cron/celery beat.
    """
    service = TagService()

    # Get all active tags
    tags = Tag.objects.filter(is_active=True, is_deleted=False)

    for tag in tags:
        try:
            service.update_tag_analytics(tag.pk)
        except Exception as e:
            logger.exception(f"Failed to update analytics for tag {tag.pk}: {e}")

    logger.info(f"Updated analytics for {tags.count()} tags")


@shared_task(
    acks_late=True,
    max_retries=2,
    default_retry_delay=600,
    soft_time_limit=300,
    time_limit=600,
)
def discover_tag_relationships():
    """
    Discover related tags based on co-occurrence.
    Run weekly to find new relationships.
    """
    service = TagService()

    # Get popular tags
    tags = Tag.objects.filter(
        is_active=True, is_deleted=False, usage_count__gte=10
    ).order_by("-usage_count")[:100]

    relationships_created = 0

    for tag in tags:
        try:
            # Discover related tags
            related = service.discover_related_tags(tag, min_co_occurrence=3)

            # Create relationships
            for related_tag, count in related[:10]:  # Top 10
                strength = min(1.0, count / 10.0)  # Normalize to 0-1
                service.create_relationship(
                    from_tag=tag,
                    to_tag=related_tag,
                    relationship_type="related",
                    strength=strength,
                )
                relationships_created += 1

        except Exception as e:
            logger.exception(f"Failed to discover relationships for tag {tag.pk}: {e}")

    logger.info(f"Discovered {relationships_created} tag relationships")


# ARCHIVED: Enhanced tag functionality - see apps/core/versions/
# The following functions depend on enhanced models that have been archived.
# Keep them for reference but they won't execute until models are restored.

# def notify_suggestion_approved(suggestion_id: int):
#     """
#     Notify user their tag suggestion was approved.
#     """
#     from apps.tags.models_enhanced import TagSuggestion
#     ...

# def send_tag_subscription_digest(user_id: int, frequency: str = "daily"):
#     """
#     Send digest of new content for subscribed tags.
#     """
#     from apps.tags.models_enhanced import TagSubscription
#     ...

# def send_daily_tag_digests():
#     """
#     Send daily digests to all users with daily subscriptions.
#     """
#     from apps.tags.models_enhanced import TagSubscription
#     ...

# def send_weekly_tag_digests():
#     """
#     Send weekly digests to all users with weekly subscriptions.
#     """
#     from apps.tags.models_enhanced import TagSubscription
#     ...
