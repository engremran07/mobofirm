"""Give grants made before the download lifetime rules their rule (safe to re-run)."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.firmwares.package_history import backfill_download_rules


class Command(BaseCommand):
    """Backfill ``ServedDownload.access_rule`` and the package period tags."""

    help = (
        "Classify existing served downloads: paid and bought files become lifetime, "
        "other featured files become package files tagged with the package period "
        "that covered the day they were served. Running it again changes nothing."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        """Declare ``--dry-run``."""
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Run the backfill and print a summary."""
        dry_run = bool(options["dry_run"])
        result = backfill_download_rules(dry_run=dry_run)
        prefix = "would update" if dry_run else "updated"
        self.stdout.write(
            f"{prefix}: {result.lifetime} lifetime, {result.package} package "
            f"({result.untagged} without a covering period), "
            f"{result.consumption_periods} quota rows tagged"
        )
