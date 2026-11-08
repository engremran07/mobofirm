from django.apps import AppConfig
from django.db.models.signals import post_migrate


class ConsentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.consent"
    label = "consent"
    verbose_name = "Consent"

    def ready(self) -> None:
        from . import signals

        post_migrate.connect(
            signals.ensure_defaults_after_migrate,
            sender=self,
            dispatch_uid="consent.ensure_defaults",
        )
