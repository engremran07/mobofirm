from importlib import import_module

from django.apps import AppConfig


class CommentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.comments"
    verbose_name = "Comments"

    def ready(self):
        import_module("apps.comments.signals")
