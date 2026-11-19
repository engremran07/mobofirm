from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.distribution import automation, syndication, videos
from apps.distribution.services import process_due_jobs


class Command(BaseCommand):
    help = (
        "Deliver due share jobs once (what the scheduler runs every minute); "
        "--housekeeping also runs the retention purge and evergreen pass now."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--housekeeping", action="store_true")
        parser.add_argument(
            "--render", type=int, metavar="JOB_ID", help="render one motion video now"
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options.get("render"):
            self.stdout.write(
                f"video={videos.render_job(options['render']) or 'nothing to do'}"
            )
            return
        summary = process_due_jobs()
        pings = syndication.process_due()
        self.stdout.write(
            f"sent={summary.sent} retried={summary.retried} "
            f"failed={summary.failed} deferred={summary.deferred} "
            f"pings_sent={pings['sent']} pings_failed={pings['failed']}"
        )
        if options["housekeeping"]:
            self.stdout.write(f"housekeeping={automation.housekeeping(force=True)}")
