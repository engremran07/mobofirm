"""Create or refresh the generated flashing tool and guide categories."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.core.seeding import require_local_database
from apps.firmwares.tool_categories import sync_reference_categories


class Command(BaseCommand):
    help = (
        "Create the missing chipset vendor and family categories, the operating "
        "system categories and one category per brand. Idempotent and additive; "
        "no tool, guide or article is created or published."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Required: acknowledge that records are written to this database.",
        )

    def handle(self, *args, **options):
        require_local_database(confirm=options["confirm"])
        report = sync_reference_categories()
        self.stdout.write("== Tool categories")
        self.stdout.write(report.line())
