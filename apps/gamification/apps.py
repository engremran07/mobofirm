from __future__ import annotations

from django.apps import AppConfig
from django.db.models.signals import post_migrate


class GamificationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.gamification"
    label = "gamification"
    verbose_name = "Gamification"

    def ready(self) -> None:
        from . import handlers, signals

        handlers.register_handlers()
        post_migrate.connect(
            signals.sync_defaults_after_migrate,
            sender=self,
            dispatch_uid="gamification.sync_defaults",
        )
