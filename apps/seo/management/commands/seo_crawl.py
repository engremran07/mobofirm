from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.seo.crawler import CrawlUnavailable, run_crawl


class Command(BaseCommand):
    help = "Crawl the deployed site through SafeFetcher and audit the observations."

    def add_arguments(self, parser):
        parser.add_argument("--base-url", default="", help="Override SITE_URL.")

    def handle(self, *args, **options):
        try:
            run = run_crawl(base_url=options["base_url"])
        except CrawlUnavailable as reason:
            raise CommandError(str(reason)) from reason
        health = run.health_percent
        self.stdout.write(
            f"Crawl {run.pk}: {run.pages_crawled} pages, "
            f"{run.pages_with_errors} with errors, "
            f"health {'n/a' if health is None else f'{health}%'}."
        )
