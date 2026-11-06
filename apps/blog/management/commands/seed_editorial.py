"""Publish the original editorial articles through the blog workflow."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.blog.seeding import seed_editorial
from apps.core.seed_data.people import EDITORIAL_USERNAME, PUBLISHER_USERNAME
from apps.core.seeding import (
    eager_background_tasks,
    require_local_database,
    require_user,
    run_step,
)


class Command(BaseCommand):
    help = (
        "Create the editorial articles as drafts of the editorial account, submit "
        "them for review and publish them as the publishing staff account. "
        "Idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Required: acknowledge that records are written to this database.",
        )

    def handle(self, *args, **options):
        require_local_database(confirm=options["confirm"])
        author = require_user(EDITORIAL_USERNAME)
        publisher = require_user(PUBLISHER_USERNAME)
        with eager_background_tasks():
            run_step(
                self.stdout.write,
                "Editorial articles",
                lambda: seed_editorial(author=author, publisher=publisher),
            )
