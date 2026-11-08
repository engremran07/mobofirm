from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.distribution import automation
from apps.distribution.services import process_due_jobs


class Command(BaseCommand):
    help = (
        "Deliver due share jobs once (what the scheduler runs every minute); "
        "--housekeeping also runs the retention purge and evergreen pass now."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--housekeeping", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        summary = process_due_jobs()
        self.stdout.write(
            f"sent={summary.sent} retried={summary.retried} "
            f"failed={summary.failed} deferred={summary.deferred}"
        )
        if options["housekeeping"]:
            self.stdout.write(f"housekeeping={automation.housekeeping(force=True)}")
