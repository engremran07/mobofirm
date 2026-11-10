from __future__ import annotations

from importlib import import_module

from django.apps import AppConfig
from django.contrib import admin
from django.utils.functional import lazy


def _admin_title() -> str:
    from .services import get_site_settings

    return get_site_settings().site_header


class SiteSettingsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.site_settings"
    label = "site_settings"
    verbose_name = "Site settings"

    def ready(self) -> None:
        import_module(f"{self.name}.signals")
        import_module(f"{self.name}.editor_store").register()
        import_module(f"{self.name}.editor_admin")
        import_module(f"{self.name}.editor_cleanup")
        # Resolved when the admin renders, so edits apply without a restart.
        title = lazy(_admin_title, str)
        admin.site.site_header = title()
        admin.site.site_title = title()
