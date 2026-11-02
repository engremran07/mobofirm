from django.apps import AppConfig


class BlogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.blog"
    verbose_name = "Blog"

    def ready(self):
        # Connect signals with deferred imports to prevent circular dependencies
        from . import signals

        signals.connect_signals()

        # Register signal handlers for cross-app communication
        try:
            from . import signal_handlers  # noqa: F401
        except Exception:  # noqa: S110
            pass

        # Register blog sitemap with the central registry (soft-fail to keep app modular)
        try:
            from apps.pages.sitemap_registry import register_sitemap

            from .sitemaps import PublishedBlogPostsSitemap

            register_sitemap("blog", PublishedBlogPostsSitemap)
        except Exception:
            # If pages app or registry isn't ready, skip silently to avoid import-time failures.
            return
