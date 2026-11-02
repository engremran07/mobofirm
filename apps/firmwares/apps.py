from django.apps import AppConfig


class FirmwaresConfig(AppConfig):
    """
    Firmware management app for multi-brand GSM device firmware distribution
    Features: Tracking, analytics, trending, request management
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.firmwares"
    verbose_name = "Firmwares"

    def ready(self) -> None:
        """Register signal handlers and sitemaps when app is ready."""
        try:
            from . import signal_handlers  # noqa: F401
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning(
                f"Failed to register firmwares signal handlers: {e}"
            )

        # Register firmware sitemaps for auto-categorized sitemap index
        try:
            from apps.pages.sitemap_registry import register_sitemap

            from .sitemaps import (
                FirmwareBrandsSitemap,
                FirmwareFileSitemap,
                FirmwareModelsSitemap,
            )

            register_sitemap("brands", FirmwareBrandsSitemap)
            register_sitemap("models", FirmwareModelsSitemap)
            register_sitemap("firmware-files", FirmwareFileSitemap)
        except Exception:  # noqa: S110
            pass
