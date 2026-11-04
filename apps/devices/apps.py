from __future__ import annotations

from django.apps import AppConfig


class DevicesConfig(AppConfig):
    name = "apps.devices"
    label = "devices"
    verbose_name = "Device Identity"

    def ready(self) -> None:
        # Import signals to keep device registration alive on login.
        try:
            from . import signals  # noqa: F401
        except Exception:  # noqa: S110
            pass
