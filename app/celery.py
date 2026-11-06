import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings.production")

app = Celery("mobofirm")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
