from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.pages.seed import seed_pages


class Command(BaseCommand):
    help = (
        "Publish the starter information and legal pages. Idempotent: existing pages "
        "are kept unless --force is given."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Overwrite the seeded fields of pages that already exist.",
        )
        parser.add_argument(
            "--contact-email",
            default="",
            help="Address published on the contact and takedown pages "
            "(default: SITE_CONTACT_EMAIL).",
        )

    def handle(self, *args, **options) -> None:
        contact_email = options["contact_email"] or settings.SITE_CONTACT_EMAIL
        report = seed_pages(
            site_name=settings.SITE_NAME,
            contact_email=contact_email,
            force=options["force"],
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Pages created: {report.created}; updated: {report.updated}; "
                f"unchanged: {report.unchanged}"
            )
        )
        if contact_email.endswith(".invalid"):
            self.stderr.write(
                self.style.WARNING(
                    f"The published contact address {contact_email} is a dummy value. "
                    "Set SITE_CONTACT_EMAIL and re-run with --force before launch."
                )
            )
