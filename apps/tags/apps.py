from django.apps import AppConfig


class TagsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.tags"
    verbose_name = "Tags"

    def ready(self):
        # Register signal handlers for cross-app communication
        try:
            from . import signal_handlers  # noqa: F401
        except Exception:  # noqa: S110
            pass

        # Register tag sitemap with the shared sitemap registry (soft-fail to avoid startup errors)
        try:
            from apps.pages.sitemap_registry import register_sitemap

            from .sitemaps import PublishedTagsSitemap

            register_sitemap("tags", PublishedTagsSitemap)
        except Exception:
            return
