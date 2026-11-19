"""Load the full smartphone and tablet chipset catalogue (no devices, no articles)."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.core.seeding import require_local_database
from apps.firmwares.seeding import seed_reference_chipsets


class Command(BaseCommand):
    help = (
        "Create every Snapdragon, Dimensity, Helio, Kompanio, Exynos, Tensor, Kirin, "
        "Unisoc, Spreadtrum, RDA, Rockchip, Allwinner and Apple SoC that is missing. "
        "Idempotent and additive; chipset articles are not published here, they are "
        "created by the automation when a model or firmware attaches to a chipset."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Required: acknowledge that records are written to this database.",
        )

    def handle(self, *args, **options):
        require_local_database(confirm=options["confirm"])
        report = seed_reference_chipsets()
        self.stdout.write("== Chipset catalogue")
        for line in report.lines():
            self.stdout.write(line)
