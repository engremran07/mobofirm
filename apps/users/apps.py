"""Application configuration for the Users module."""

from __future__ import annotations

from importlib import import_module

from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.users"
    label = "users"
    verbose_name = "User Management"

    def ready(self) -> None:
        # Import errors must surface at startup; a silently missing handler
        # would disable token revocation and onboarding flags.
        import_module("apps.users.signals")
        import_module("apps.users.signal_handlers")
