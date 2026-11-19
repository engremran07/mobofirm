"""Seed the flashing tool catalogue (official OEM tools first) and its categories."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.core.seeding import require_local_database
from apps.firmwares.seeding import seed_tools


class Command(BaseCommand):
    help = (
        "Create the missing flashing tools with their brand and chipset links, and the "
        "generated tool categories. Additive and idempotent: existing tools are only "
        "linked, never overwritten. Tools without a verified official download page "
        "are stored without a download link."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Required: acknowledge that records are written to this database.",
        )

    def handle(self, *args, **options):
        require_local_database(confirm=options["confirm"])
        report = seed_tools()
        self.stdout.write("== Flashing tools")
        for line in report.lines():
            self.stdout.write(line)
