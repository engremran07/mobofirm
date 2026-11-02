from django.apps import AppConfig


class CommentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.comments"
    verbose_name = "Comments"

    def ready(self):
        """Import signals when app is ready."""
        try:
            import apps.comments.signals  # noqa
        except ImportError:
            pass
