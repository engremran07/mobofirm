from django.apps import AppConfig
from django.utils.module_loading import autodiscover_modules


class SeoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.seo"
    label = "seo"
    verbose_name = "SEO"

    def ready(self) -> None:
        # Domain apps contribute page providers and content sources from their own
        # ``seo_providers`` modules; apps.seo never imports their models directly.
        autodiscover_modules("seo_providers")
        from .signals import connect_content_sources

        connect_content_sources()
