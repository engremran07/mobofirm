from django.apps import AppConfig


class BountyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.bounty"
    label = "bounty"
    verbose_name = "Bounties"

    def ready(self) -> None:
        from . import handlers

        handlers.register_handlers()
