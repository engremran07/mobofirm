"""Create the seeded staff, editorial and reader accounts."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.core.seeding import require_local_database, run_step
from apps.users.seeding import ensure_people


class Command(BaseCommand):
    help = (
        "Create the uploader, reviewer, editorial, publisher and reader accounts "
        "with unusable passwords and reserved .invalid addresses. Idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Required: acknowledge that records are written to this database.",
        )

    def handle(self, *args, **options):
        require_local_database(confirm=options["confirm"])
        run_step(self.stdout.write, "Accounts", lambda: ensure_people()[0])
