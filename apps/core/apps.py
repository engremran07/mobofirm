from django.apps import AppConfig
from django.utils.module_loading import autodiscover_modules


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    label = "core"
    verbose_name = "Core"

    def ready(self):
        """
        Core app initialization:
        - Safely clear the django.contrib.sites cache after registry load
        - Autodiscover signals or other startup modules
        """
        try:
            from django.contrib.sites.models import Site

            Site.objects.clear_cache()
        except Exception:  # noqa: S110
            pass

        # Auto-discover signals.py in submodules
        autodiscover_modules("signals")
