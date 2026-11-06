"""Submit reader comments through the comments service."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.comments.seeding import seed_comments
from apps.core.seeding import (
    eager_background_tasks,
    require_local_database,
    require_user,
    run_step,
)


class Command(BaseCommand):
    help = (
        "Submit the reader comments and staff replies through the comment service "
        "so automatic moderation decides which are published and which are "
        "queued. Idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Required: acknowledge that records are written to this database.",
        )

    def handle(self, *args, **options):
        require_local_database(confirm=options["confirm"])
        with eager_background_tasks():
            run_step(
                self.stdout.write,
                "Comments",
                lambda: seed_comments(require_user),
            )
