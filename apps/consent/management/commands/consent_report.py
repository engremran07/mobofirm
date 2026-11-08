from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.consent.services import consent_stats, purge_old_records


class Command(BaseCommand):
    help = "Print consent statistics, or purge audit records past the retention window."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--purge",
            action="store_true",
            help="Delete records older than CONSENT_RECORD_RETENTION_DAYS.",
        )
        parser.add_argument(
            "--days", type=int, default=None, help="Override the retention window."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["purge"]:
            deleted = purge_old_records(options["days"])
            self.stdout.write(f"Deleted {deleted} consent record(s).")
            return
        stats = consent_stats()
        policy = stats["policy"]
        self.stdout.write(
            f"Active policy: {'v' + str(policy.version) if policy else 'none'}; "
            f"visitors decided: {stats['visitors']}"
        )
        for row in stats["by_category"]:
            self.stdout.write(f"  {row['name']}: {row['granted']} ({row['percent']}%)")
