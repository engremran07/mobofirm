from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.db.models import Q
from django.utils import timezone

from apps.blog.auto import GenerationError, autoplan_topics, generate_post_from_topic
from apps.blog.models import AutoTopic, PostStatus


class Command(BaseCommand):
    help = "Generate blog drafts from queued topics and submit them for staff review."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--limit", type=int, default=3, help="Max topics to process this run."
        )
        parser.add_argument(
            "--draft-only",
            action="store_true",
            help="Leave generated posts as drafts instead of submitting them for review.",
        )
        parser.add_argument(
            "--autoplan",
            type=str,
            default="",
            help="Seed text used to queue new topic ideas first (e.g. 'Odin flashing').",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        limit = max(1, options["limit"])
        seed = (options.get("autoplan") or "").strip()
        max_retries = int(getattr(settings, "BLOG_AUTOGEN_MAX_RETRIES", 3))
        daily_cap = int(getattr(settings, "BLOG_AUTOGEN_DAILY_LIMIT", 25))

        if seed:
            queued = autoplan_topics(seed, count=limit)
            self.stdout.write(self.style.SUCCESS(f"Queued {len(queued)} topics."))

        attempted_today = AutoTopic.objects.filter(
            last_attempt_at__date=timezone.now().date()
        ).count()
        if attempted_today >= daily_cap:
            self.stdout.write(self.style.WARNING("Daily limit reached; nothing to do."))
            return
        limit = min(limit, daily_cap - attempted_today)

        topics = (
            AutoTopic.objects.filter(
                Q(status="queued") | Q(status="failed", retry_count__lt=max_retries)
            )
            .filter(
                Q(scheduled_for__isnull=True) | Q(scheduled_for__lte=timezone.now())
            )
            .order_by("created_at")[:limit]
        )
        done = failed = 0
        for topic in topics:
            try:
                post = generate_post_from_topic(
                    topic, submit_for_review=not options["draft_only"]
                )
            except (GenerationError, RuntimeError, ValueError) as error:
                failed += 1
                self.stderr.write(self.style.ERROR(f"{topic.topic}: {error}"))
                continue
            done += 1
            state = "in review" if post.status == PostStatus.IN_REVIEW else "draft"
            self.stdout.write(
                self.style.SUCCESS(f"{topic.topic} -> {state} ({post.slug})")
            )
        self.stdout.write(f"Generated {done} posts; {failed} failed.")
