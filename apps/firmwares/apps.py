from importlib import import_module

from django.apps import AppConfig


class FirmwaresConfig(AppConfig):
    """
    Firmware management app for multi-brand GSM device firmware distribution
    Features: Tracking, analytics, trending, request management
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.firmwares"
    verbose_name = "Firmwares"

    def ready(self) -> None:
        """Register firmware models and signal handlers when the app is ready."""
        import_module(f"{self.name}.access_change_models")
        import_module(f"{self.name}.signal_handlers")
        import_module(f"{self.name}.package_history").connect()
