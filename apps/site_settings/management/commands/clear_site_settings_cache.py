from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.site_settings.services import clear_site_settings_cache


class Command(BaseCommand):
    help = "Invalidate the cached site settings snapshot."

    def handle(self, *args: object, **options: object) -> None:
        clear_site_settings_cache()
        self.stdout.write(self.style.SUCCESS("Site settings cache cleared."))
