"""Promote very popular free firmware to featured (``--dry-run`` only reports)."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.firmwares.auto_feature import run_auto_feature


class Command(BaseCommand):
    """Run the automatic popularity promotion once, as configured in the admin."""

    help = (
        "Promote free firmware that is extremely popular to featured, using the "
        "thresholds of Firmwares > Auto-feature settings. With --dry-run nothing is "
        "changed and the rule is evaluated even while switched off."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        """Declare ``--dry-run``."""
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List the files that would be promoted; change nothing.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Run the rule and print what happened."""
        dry_run = bool(options["dry_run"])
        result = run_auto_feature(force_dry_run=dry_run, ignore_enabled=dry_run)
        if not result.enabled and not dry_run:
            self.stdout.write(
                "Auto-feature is switched off in the admin; nothing done."
            )
            return
        mode = "DRY RUN" if result.dry_run else "APPLIED"
        chosen = result.would_promote if result.dry_run else result.promoted
        self.stdout.write(f"{mode}: {len(chosen)} file(s)")
        for item in chosen:
            self.stdout.write(f"  {item.firmware.original_file_name}: {item.summary()}")
        if result.skipped:
            self.stdout.write(f"skipped: {result.skipped}")
