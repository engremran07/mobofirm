from importlib import import_module

from django.apps import AppConfig


class PagesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.pages"
    label = "pages"
    verbose_name = "Pages"

    def ready(self) -> None:
        import_module("apps.pages.signal_handlers")
