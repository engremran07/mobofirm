from django.apps import AppConfig


class FileManagerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.filemanager"
    label = "filemanager"
    verbose_name = "File Manager"
