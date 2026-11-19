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
        - Stamp the correlation id on every log record and forward it to Celery
        - Register the runtime-configuration system checks
        - Clear the django.contrib.sites in-memory cache after registry load
        - Autodiscover signals or other startup modules
        """
        from importlib import import_module

        from django.contrib.sites.models import Site

        from apps.core import celery_context
        from apps.core.utils.logging import install_log_record_factory

        import_module("apps.core.checks")  # registers the system checks
        install_log_record_factory()
        celery_context.connect()

        Site.objects.clear_cache()

        # Auto-discover signals.py in submodules
        autodiscover_modules("signals")
        # Each app registers its search providers from <app>/search_providers.py.
        autodiscover_modules("search_providers")
