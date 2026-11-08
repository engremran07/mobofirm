from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.seo import jobs


class Command(BaseCommand):
    help = "Process pending SEO outbox jobs (same work as the Celery beat task)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=25)
        parser.add_argument(
            "--all",
            action="store_true",
            help="Keep going until the queue is empty or the time budget runs out.",
        )
        parser.add_argument("--budget", type=float, default=60.0)

    def handle(self, *args, **options):
        if options["all"]:
            counts = jobs.process_queue_now(budget_seconds=options["budget"])
        else:
            counts = jobs.process_pending(limit=options["limit"])
        self.stdout.write(
            ", ".join(f"{name}={total}" for name, total in sorted(counts.items()))
        )
