from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.db.models import Q
from django.utils import timezone

from apps.blog.auto import autoplan_topics, generate_post_from_topic
from apps.blog.models import AutoTopic

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Generate blog posts from the AI topic queue. Autopublishes when AI succeeds; drafts on failure."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--limit", type=int, default=3, help="Max topics to process this run."
        )
        parser.add_argument(
            "--draft-only",
            action="store_true",
            help="Force all generated posts to remain drafts (override autopublish).",
        )
        parser.add_argument(
            "--autoplan",
            type=str,
            default="",
            help="Optional seed text to generate new topics before processing (e.g., 'DevOps, AI safety').",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        limit = max(1, options["limit"])
        autopublish = not options["draft_only"]
        seed = (options.get("autoplan") or "").strip()
        max_retries = int(getattr(settings, "BLOG_AUTOGEN_MAX_RETRIES", 3))
        daily_cap = int(getattr(settings, "BLOG_AUTOGEN_DAILY_LIMIT", 25))
        today = timezone.now().date()

        if seed:
            topics = autoplan_topics(seed, count=limit)
            self.stdout.write(
                self.style.SUCCESS(f"Queued {len(topics)} AI topics from seed.")
            )

        processed_today = AutoTopic.objects.filter(
            updated_at__date=today, status__in=["running", "succeeded", "failed"]
        ).count()
        if processed_today >= daily_cap:
            self.stdout.write(
                self.style.WARNING("Daily autogen cap reached; skipping run.")
            )
            return
        limit = min(limit, daily_cap - processed_today)

        qs = AutoTopic.objects.filter(
            Q(status="queued") | Q(status="failed", retry_count__lt=max_retries)
        ).filter(Q(scheduled_for__isnull=True) | Q(scheduled_for__lte=timezone.now()))
        queued = qs.order_by("created_at")[:limit]
        processed = 0
        failures = 0
        for topic in queued:
            try:
                post = generate_post_from_topic(topic, autopublish=autopublish)
                processed += 1
                state = "published" if post.is_published else "draft"
                self.stdout.write(
                    self.style.SUCCESS(f"{topic.topic} -> {state} ({post.slug})")
                )
            except Exception as exc:
                failures += 1
                topic.status = "failed"
                topic.retry_count = (topic.retry_count or 0) + 1
                topic.last_attempt_at = timezone.now()
                topic.last_error = str(exc)[:500]
                topic.save(
                    update_fields=[
                        "status",
                        "last_error",
                        "retry_count",
                        "last_attempt_at",
                        "updated_at",
                    ]
                )
                logger.exception("blog_autogen failed for %s", topic.topic)
                self.stderr.write(self.style.ERROR(f"Failed {topic.topic}: {exc}"))

        self.stdout.write(
            self.style.SUCCESS(f"Processed {processed} topics; failures: {failures}")
        )
