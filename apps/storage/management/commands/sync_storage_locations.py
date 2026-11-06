from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.storage.services import sync_all_firmware


class Command(BaseCommand):
    help = "Record a storage location for every typed firmware row that has a file."

    def handle(self, *args, **options):
        report = sync_all_firmware()
        self.stdout.write(
            self.style.SUCCESS(
                f"Firmware seen: {report.firmware_seen}; locations synced: "
                f"{report.locations_synced}; unmanaged: {report.unmanaged}"
            )
        )
