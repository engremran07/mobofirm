from importlib import import_module

from django.apps import AppConfig


class StorageConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.storage"
    label = "storage"
    verbose_name = "Storage"

    def ready(self) -> None:
        import_module("apps.storage.signals")
