"""Seed brands, models, chipsets, firmware, tools and guides through the services."""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.seed_data.people import (
    PUBLISHER_USERNAME,
    REVIEWER_USERNAME,
    UPLOADER_USERNAME,
)
from apps.core.seeding import (
    eager_background_tasks,
    require_local_database,
    require_user,
    run_step,
)
from apps.firmwares.seed_verification import MINIMUM_BRANDS, verify_catalog
from apps.firmwares.seeding import seed_catalog, select_brands


class Command(BaseCommand):
    help = (
        "Load the device catalogue: chipsets, brands, models and variants, official "
        "firmware as reviewed external listings, flashing tools and guides. "
        "Brand, model and chipset articles are produced by the firmware blog "
        "automation. Idempotent and additive."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Required: acknowledge that records are written to this database.",
        )
        parser.add_argument(
            "--limit-brands",
            type=int,
            default=None,
            help="Seed only the first N brands (faster, for local checks).",
        )
        parser.add_argument(
            "--skip-verify",
            action="store_true",
            help="Skip the acceptance checks that run after seeding.",
        )

    def handle(self, *args, **options):
        require_local_database(confirm=options["confirm"])
        brands = select_brands(options["limit_brands"])
        uploader = require_user(UPLOADER_USERNAME)
        reviewer = require_user(REVIEWER_USERNAME)
        publisher = require_user(PUBLISHER_USERNAME)
        with eager_background_tasks():
            run_step(
                self.stdout.write,
                "Catalogue",
                lambda: seed_catalog(
                    brands, uploader=uploader, reviewer=reviewer, publisher=publisher
                ),
            )
        if options["skip_verify"]:
            return
        result = verify_catalog(
            brands,
            today=timezone.localdate(),
            minimum_brands=min(MINIMUM_BRANDS, len(brands)),
        )
        self.stdout.write("== Catalogue verification")
        for line in result.lines():
            self.stdout.write(line)
