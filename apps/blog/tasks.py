"""
Celery tasks for blog app
Handles async operations: search engine pings, notifications, etc.
"""

from __future__ import annotations

import logging

import requests

try:
    from celery import shared_task
except Exception:  # pragma: no cover - fallback when Celery not installed

    def shared_task(*dargs, **dkwargs):  # type: ignore[assignment]
        def decorator(func):
            return func

        if dargs and callable(dargs[0]):
            return dargs[0]
        return decorator


from django.conf import settings
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    soft_time_limit=30,
)
def ping_search_engines(self, sitemap_url: str) -> dict:
    """
    Ping search engines asynchronously to notify them of sitemap updates.

    Args:
        sitemap_url: Full URL to the sitemap

    Returns:
        dict: Status of each ping attempt

    Example:
        ping_search_engines.delay("https://example.com/sitemap.xml")
    """
    results = {}

    # Search engine ping URLs
    pings = [
        f"https://www.google.com/ping?sitemap={sitemap_url}",
        f"https://www.bing.com/ping?sitemap={sitemap_url}",
    ]

    for ping_url in pings:
        engine_name = "Google" if "google" in ping_url else "Bing"
        try:
            response = requests.get(ping_url, timeout=5)
            if response.status_code == 200:
                results[engine_name] = "success"
                logger.info(f"Successfully pinged {engine_name} with sitemap")
            else:
                results[engine_name] = f"failed_status_{response.status_code}"
                logger.warning(
                    f"{engine_name} ping returned status {response.status_code}"
                )
        except requests.RequestException as e:
            results[engine_name] = f"error: {str(e)[:100]}"
            logger.exception(f"Failed to ping {engine_name}: {e}")
            # Retry on network errors
            if not isinstance(e, requests.Timeout):
                raise self.retry(exc=e)  # noqa: B904

    return results


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    soft_time_limit=300,
)
def send_post_notifications_batched(
    self,
    post_id: int,
    notification_title: str,
    notification_body: str,
    batch_size: int = 500,
) -> dict:
    """
    Send notifications to all active users in batches to avoid blocking.

    Args:
        post_id: ID of the post
        notification_title: Notification title
        notification_body: Notification body text
        batch_size: Number of users to process per batch

    Returns:
        dict: Statistics about sent notifications

    Example:
        send_post_notifications_batched.delay(
            post_id=123,
            notification_title="New Post",
            notification_body="Check out our latest article!",
            batch_size=500
        )
    """
    from apps.blog.models import Post
    from apps.users.models import CustomUser
    from apps.users.services.notifications import broadcast_notification

    stats = {
        "total_users": 0,
        "batches_processed": 0,
        "notifications_sent": 0,
        "errors": 0,
        "started_at": timezone.now().isoformat(),
    }

    try:
        try:
            post = Post.objects.get(pk=post_id)
            post_url = reverse("blog:post_detail", kwargs={"slug": post.slug})
        except Post.DoesNotExist:
            logger.exception(f"Post {post_id} not found for notifications")
            return stats

        # Get total count first
        stats["total_users"] = CustomUser.objects.filter(is_active=True).count()
        logger.info(
            f"Starting notification batch send for post {post_id} "
            f"to {stats['total_users']} users"
        )

        # Process in batches using iterator to avoid loading all users into memory
        users_qs = CustomUser.objects.filter(is_active=True).iterator(
            chunk_size=batch_size
        )

        batch = []
        for user in users_qs:
            batch.append(user)

            if len(batch) >= batch_size:
                try:
                    broadcast_notification(
                        recipients=batch,
                        title=notification_title,
                        message=notification_body,
                        url=post_url,
                        channel="web",
                        action_type="post",
                        icon="rss",
                    )
                    stats["notifications_sent"] += len(batch)
                    stats["batches_processed"] += 1
                    batch = []
                except Exception as e:
                    logger.exception(f"Failed to send notification batch: {e}")
                    stats["errors"] += 1

        # Send remaining batch
        if batch:
            try:
                broadcast_notification(
                    recipients=batch,
                    title=notification_title,
                    message=notification_body,
                    url=post_url,
                    channel="web",
                    action_type="post",
                    icon="rss",
                )
                stats["notifications_sent"] += len(batch)
                stats["batches_processed"] += 1
            except Exception as e:
                logger.exception(f"Failed to send final notification batch: {e}")
                stats["errors"] += 1

        stats["completed_at"] = timezone.now().isoformat()
        logger.info(
            f"Completed notification send for post {post_id}: "
            f"{stats['notifications_sent']}/{stats['total_users']} sent "
            f"in {stats['batches_processed']} batches"
        )

    except Exception as e:
        logger.exception(f"Critical error in notification task for post {post_id}")
        stats["critical_error"] = str(e)
        raise self.retry(exc=e)  # noqa: B904

    return stats


@shared_task(soft_time_limit=60)
def sync_tag_usage_counts(tag_ids: list[int]) -> dict:
    """
    Efficiently update usage counts for multiple tags using bulk operations.

    Args:
        tag_ids: List of tag IDs to update

    Returns:
        dict: Statistics about the update
    """
    from django.db.models import Count, Q

    from apps.blog.models import PostStatus
    from apps.tags.models import Tag

    stats: dict[str, int | str] = {"updated_count": 0, "errors": 0}

    try:
        # Annotate tags with their post counts in a single query
        tags_with_counts = Tag.objects.filter(id__in=tag_ids).annotate(
            published_count=Count(
                "posts", filter=Q(posts__status=PostStatus.PUBLISHED), distinct=True
            )
        )

        # Bulk update using case/when for conditional updates
        from django.db.models import Case, When

        cases = [When(id=tag.pk, then=tag.published_count) for tag in tags_with_counts]  # type: ignore[attr-defined]

        updated = Tag.objects.filter(id__in=tag_ids).update(
            usage_count=Case(*cases, default=0)
        )

        stats["updated_count"] = updated
        logger.info(f"Updated usage counts for {updated} tags")

    except Exception as e:
        logger.exception(f"Failed to sync tag usage counts: {e}")
        stats["errors"] = 1
        stats["error_message"] = str(e)

    return stats


@shared_task(soft_time_limit=120, time_limit=180)
def generate_ai_post(topic: str, user_id: int) -> int | None:
    """
    Generate a blog post using AI based on a topic.
    """
    import json

    from django.contrib.auth import get_user_model
    from django.utils.text import slugify

    from apps.ai.services import test_completion
    from apps.blog.models import Post

    prompt = f"""
    Write a comprehensive blog post about: {topic}.
    Return ONLY a valid JSON object with the following keys:
    - title: A catchy title (max 200 chars)
    - summary: A short summary (max 500 chars)
    - body: The full blog post content in HTML format (use <h2>, <p>, <ul>, etc.). Do not include <html> or <body> tags.
    - tags: A list of 5-10 relevant tags (strings)
    - seo_title: SEO optimized title
    - seo_description: SEO optimized description
    """

    try:
        response = test_completion(prompt)
        content_text = response.get("text", "")

        # Clean up markdown code blocks if present
        if content_text.startswith("```json"):
            content_text = content_text.replace("```json", "").replace("```", "")
        elif content_text.startswith("```"):
            content_text = content_text.replace("```", "")

        data = json.loads(content_text)

        User = get_user_model()
        user = User.objects.get(pk=user_id)

        post = Post.objects.create(
            title=data.get("title", topic),
            slug=slugify(data.get("title", topic))[:240],
            summary=data.get("summary", ""),
            body=data.get("body", ""),
            seo_title=data.get("seo_title", ""),
            seo_description=data.get("seo_description", ""),
            author=user,
            status="draft",  # Default to draft for review
            is_ai_generated=True,
        )

        # Add tags
        from apps.tags.models import Tag

        for tag_name in data.get("tags", []):
            tag_slug = slugify(tag_name)
            tag, _ = Tag.objects.get_or_create(
                slug=tag_slug, defaults={"name": tag_name}
            )
            post.tags.add(tag)

        return post.pk

    except Exception as e:
        logger.exception(f"Failed to generate AI post: {e}")
        return None


@shared_task(soft_time_limit=300, time_limit=600)
def auto_translate_post(post_id: int) -> dict:
    """
    Automatically translate a blog post into all enabled languages.
    Uses DeepL if configured, otherwise falls back to AI.
    """
    from apps.ai.services import test_completion
    from apps.blog.models import Post, PostTranslation

    try:
        from apps.i18n.models import Locale  # type: ignore[import-not-found]
        from apps.i18n.translation_provider import (  # type: ignore[import-not-found]
            DeepLTranslator,
        )
    except ImportError:
        return {"error": "i18n module not available"}

    results = {"translated": [], "errors": []}

    try:
        post = Post.objects.get(pk=post_id)
    except Post.DoesNotExist:
        return {"error": "Post not found"}

    # Get target languages
    # 1. Try database Locales
    target_langs = list(
        Locale.objects.filter(enabled_global=True).values_list("code", flat=True)
    )
    # 2. Fallback to settings.LANGUAGES
    if not target_langs:
        target_langs = [code for code, _ in getattr(settings, "LANGUAGES", [])]

    # Remove source language (assuming 'en' or site default)
    source_lang = getattr(settings, "LANGUAGE_CODE", "en")
    if source_lang in target_langs:
        target_langs.remove(source_lang)

    if not target_langs:
        return {"status": "No target languages found"}

    # Initialize Translator
    deepl_key = getattr(settings, "DEEPL_API_KEY", None)
    translator = DeepLTranslator(deepl_key) if deepl_key else None

    for lang in target_langs:
        if PostTranslation.objects.filter(post=post, language=lang).exists():
            continue

        try:
            title_tr = ""
            summary_tr = ""
            body_tr = ""

            # Strategy 1: DeepL
            if translator:
                try:
                    translations = translator.translate(
                        [post.title, post.summary, post.body],
                        target=lang,
                        source=source_lang,
                    )
                    if len(translations) == 3:
                        title_tr, summary_tr, body_tr = translations
                except Exception as e:
                    logger.warning(f"DeepL failed for {lang}: {e}")

            # Strategy 2: AI Fallback (if DeepL failed or not configured)
            if not title_tr:
                prompt = f"""
                Translate the following blog post content into {lang} (ISO code).
                Return ONLY a valid JSON object with keys: title, summary, body.

                Title: {post.title}
                Summary: {post.summary}
                Body: {post.body}
                """
                resp = test_completion(prompt)
                text = resp.get("text", "")
                # Clean JSON
                if text.startswith("```"):
                    text = text.split("```")[1].replace("json", "").strip()

                import json

                data = json.loads(text)
                title_tr = data.get("title", "")
                summary_tr = data.get("summary", "")
                body_tr = data.get("body", "")

            if title_tr and body_tr:
                PostTranslation.objects.create(
                    post=post,
                    language=lang,
                    title=title_tr[:200],
                    summary=summary_tr,
                    body=body_tr,
                    seo_title=title_tr[:240],
                    seo_description=summary_tr[:320],
                )
                results["translated"].append(lang)

        except Exception as e:
            logger.exception(f"Translation failed for {lang}: {e}")
            results["errors"].append(f"{lang}: {e!s}")

    return results
