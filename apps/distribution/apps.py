from django.apps import AppConfig


class DistributionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.distribution"
    label = "distribution"
    verbose_name = "Distribution"

    def ready(self) -> None:
        from . import handlers

        handlers.register_handlers()
