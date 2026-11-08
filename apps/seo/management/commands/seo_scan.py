from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.seo import scan


class Command(BaseCommand):
    help = (
        "Scan every public URL (pages, posts, brands, chipsets, models, firmware, "
        "tools, tags) in-process and sync the SEO issues. No HTTP, no Celery."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--budget",
            type=float,
            default=scan.BACKGROUND_BUDGET_SECONDS,
            help="Time budget in seconds before the scan stops and can be resumed.",
        )
        parser.add_argument(
            "--restart",
            action="store_true",
            help="Ignore the cursor of an earlier partial scan.",
        )

    def handle(self, *args, **options):
        try:
            run = scan.run_scan(
                budget_seconds=options["budget"], resume=not options["restart"]
            )
        except scan.ScanUnavailable as reason:
            raise CommandError(str(reason)) from reason
        summary = run.summary
        self.stdout.write(
            f"Scan {run.pk}: {run.pages_crawled}/{run.urls_total} URLs, "
            f"{summary.get('findings', 0)} findings, "
            f"{summary.get('resolved', 0)} resolved, "
            f"{summary.get('open_after', 0)} open"
            f"{' (partial: run again to continue)' if run.partial else ''}."
        )
