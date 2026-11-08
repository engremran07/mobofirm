"""Celery tasks for the blog. Every task is idempotent and safe to retry."""

from __future__ import annotations

import json
import logging

from celery import shared_task
from django.conf import settings
from django.db import DatabaseError
from django.utils import timezone

from apps.blog.models import AutoTopic, Post, PostStatus, PostTranslation
from apps.core import ai_client

logger = logging.getLogger(__name__)

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
    """Tell active users about a new editorial post; at most once per post."""
    from apps.users.models import CustomUser, Notification

    post = Post.objects.filter(pk=post_id, status=PostStatus.PUBLISHED).first()
    if post is None or post.is_ai_generated:
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


@shared_task(acks_late=True, soft_time_limit=600, time_limit=900)
def process_queued_topics(limit: int = 3) -> int:
    """Generate drafts for queued or retryable topics (beat: hourly)."""
    from django.db.models import Q

    from apps.blog.auto import GenerationError, generate_post_from_topic

    max_retries = int(getattr(settings, "BLOG_AUTOGEN_MAX_RETRIES", 3))
    topics = (
        AutoTopic.objects.filter(
            Q(status="queued") | Q(status="failed", retry_count__lt=max_retries)
        )
        .filter(Q(scheduled_for__isnull=True) | Q(scheduled_for__lte=timezone.now()))
        .order_by("created_at")[: max(1, limit)]
    )
    done = 0
    for topic in topics:
        try:
            generate_post_from_topic(topic)
        except (GenerationError, RuntimeError, ValueError) as error:
            logger.warning("Topic %s was not generated: %s", topic.pk, error)
        else:
            done += 1
    return done


@shared_task(soft_time_limit=300, time_limit=600)
def auto_translate_post(post_id: int) -> dict:
    """Translate a post into the explicitly configured languages."""
    post = Post.objects.filter(pk=post_id).first()
    if post is None:
        return {"error": "Post not found"}
    targets = [
        code
        for code in dict.fromkeys(settings.BLOG_TRANSLATION_LANGUAGES)
        if code != settings.LANGUAGE_CODE
    ]
    results: dict[str, list[str]] = {"translated": [], "errors": []}
    for language in targets:
        if PostTranslation.objects.filter(post=post, language=language).exists():
            continue
        prompt = (
            f"Translate this blog post into {language} (ISO code). Return only a JSON "
            f"object with keys title, summary, body.\n\nTitle: {post.title}\n"
            f"Summary: {post.summary}\nBody: {post.body}"
        )
        try:
            text = ai_client.generate_answer(question=prompt, user=post.author)
            if text.startswith("```"):
                text = text.split("```")[1].removeprefix("json").strip()
            data = json.loads(text)
        except (ai_client.AiClientError, json.JSONDecodeError, IndexError) as error:
            logger.warning("Translation to %s failed: %s", language, error)
            results["errors"].append(f"{language}: {error}")
            continue
        title, body = data.get("title", ""), data.get("body", "")
        if not (title and body):
            results["errors"].append(f"{language}: incomplete translation")
            continue
        summary = data.get("summary", "")
        PostTranslation.objects.create(
            post=post,
            language=language,
            title=title[:200],
            summary=summary[:1000],
            body=body,
            seo_title=title[:240],
            seo_description=summary[:320],
        )
        results["translated"].append(language)
    return results
