"""Celery tasks for the blog. Every task is idempotent and safe to retry."""

from __future__ import annotations

from celery import shared_task
from django.db import DatabaseError
from django.utils import timezone

from apps.blog.models import Post, PostSource, PostStatus

NOTIFY_BATCH = 500


@shared_task(acks_late=True, soft_time_limit=60, time_limit=120)
def publish_due_scheduled_posts() -> int:
    """Publish scheduled posts whose time has come (beat: every minute)."""
    now = timezone.now()
    published = 0
    for post in Post.objects.filter(
        status=PostStatus.SCHEDULED, publish_at__lte=now
    ).iterator():
        post.status = PostStatus.PUBLISHED
        post.published_at = post.published_at or post.publish_at or now
        post.save()
        published += 1
    return published


@shared_task(acks_late=True, soft_time_limit=60, time_limit=120)
def reconcile_guide_fulfilments() -> int:
    """Finish fulfilment of live solutions whose request is still approved.

    Safe to repeat: covers a crash between publishing a solution and settling its
    request and bounty. Schedule with beat (for example every 15 minutes).
    """
    from apps.blog import guide_requests

    return guide_requests.reconcile_fulfilments()


@shared_task(
    autoretry_for=(DatabaseError,),
    retry_backoff=True,
    max_retries=2,
    acks_late=True,
    soft_time_limit=300,
    time_limit=360,
)
def send_post_notifications_batched(post_id: int) -> int:
    """Tell active users about a new non-automated post; at most once per post."""
    from apps.users.models import CustomUser, Notification

    post = Post.objects.filter(pk=post_id, status=PostStatus.PUBLISHED).first()
    if post is None or post.content_source == PostSource.AUTOMATED:
        return 0
    url = post.get_absolute_url()
    if Notification.objects.filter(action_type="post", url=url).exists():
        return 0
    sent = 0
    batch: list = []
    for user in CustomUser.objects.filter(is_active=True).iterator(
        chunk_size=NOTIFY_BATCH
    ):
        batch.append(user)
        if len(batch) >= NOTIFY_BATCH:
            sent += _send_batch(batch, post, url)
            batch = []
    if batch:
        sent += _send_batch(batch, post, url)
    return sent


def _send_batch(batch: list, post: Post, url: str) -> int:
    from apps.users.services.notifications import broadcast_notification

    return broadcast_notification(
        recipients=batch,
        title="New blog post",
        message=f"{post.title} is now live.",
        url=url,
        channel="web",
        action_type="post",
        icon="rss",
    )
