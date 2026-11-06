from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.seo import jobs


class Command(BaseCommand):
    help = "Process pending SEO outbox jobs (same work as the Celery beat task)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=25)

    def handle(self, *args, **options):
        counts = jobs.process_pending(limit=options["limit"])
        self.stdout.write(
            ", ".join(f"{name}={total}" for name, total in sorted(counts.items()))
        )
