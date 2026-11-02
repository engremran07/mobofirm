from django.apps import AppConfig


class GovernanceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.governance"
    verbose_name = "Content Governance"

    def ready(self) -> None:
        from . import signals  # noqa: F401
