from __future__ import annotations

from importlib import import_module

from django.apps import AppConfig


class DevicesConfig(AppConfig):
    name = "apps.devices"
    label = "devices"
    verbose_name = "Device Identity"

    def ready(self) -> None:
        # Import errors must surface; a missing handler would silently stop
        # device registration on login.
        import_module("apps.devices.signals")
