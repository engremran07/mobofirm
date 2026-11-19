"""Report, and optionally remove, chipset rows outside the phone/tablet allow-list."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.firmwares.chipset_audit import prune_unreferenced, unsupported_chipsets
from apps.firmwares.chipsets import SUPPORTED_CHIPSET_VENDORS


class Command(BaseCommand):
    help = (
        "List chipset rows whose vendor is not a smartphone or tablet SoC vendor "
        "(allowed: " + ", ".join(SUPPORTED_CHIPSET_VENDORS) + ") together with the "
        "models, variants, firmware, tools and posts that reference them. Dry run "
        "by default; --apply deletes only the rows nothing references."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Delete unsupported chipset rows that nothing references. "
            "Without it the command only reports.",
        )

    def handle(self, *args, **options):
        usages = unsupported_chipsets()
        mode = "APPLY" if options["apply"] else "DRY RUN"
        self.stdout.write(f"== Unsupported chipsets ({mode})")
        self.stdout.write("Allowed vendors: " + ", ".join(SUPPORTED_CHIPSET_VENDORS))
        if not usages:
            self.stdout.write("No chipset rows outside the allow-list.")
            return
        vendors = sorted({usage.chipset.vendor or "(none)" for usage in usages})
        self.stdout.write("Vendors outside the allow-list: " + ", ".join(vendors))
        for usage in usages:
            chipset = usage.chipset
            if usage.is_referenced:
                detail = ", ".join(
                    f"{count} {name}" for name, count in usage.references.items()
                )
                status = f"referenced by {detail}: kept"
            else:
                status = "unreferenced"
            self.stdout.write(f"- {chipset.display_name} [{chipset.slug}]: {status}")
        removable = sum(1 for usage in usages if not usage.is_referenced)
        kept = len(usages) - removable
        if not options["apply"]:
            self.stdout.write(
                f"{removable} unreferenced row(s) would be deleted, {kept} referenced "
                "row(s) are kept. Re-run with --apply to delete."
            )
            return
        deleted = prune_unreferenced(usages)
        self.stdout.write(
            f"Deleted {deleted} unreferenced chipset row(s); {kept} referenced "
            "row(s) kept for manual migration."
        )
