from django.db import models
from solo.models import SingletonModel

from apps.core.metrics.collector import Metric
from apps.users.models import AuditFieldsModel, SoftDeleteModel, TimestampedModel


class AppRegistry(SingletonModel):
    """Controls which installed application APIs are available."""

    ads_enabled = models.BooleanField(default=True)
    blog_enabled = models.BooleanField(default=True)
    comments_enabled = models.BooleanField(default=True)
    devices_enabled = models.BooleanField(default=True)
    firmwares_enabled = models.BooleanField(default=True)
    governance_enabled = models.BooleanField(default=True)
    tags_enabled = models.BooleanField(default=True)
    users_enabled = models.BooleanField(default=True)
    device_identity_enabled = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Application Registry"

    def __str__(self) -> str:
        return "Application Registry"


__all__ = [
    "TimestampedModel",
    "SoftDeleteModel",
    "AuditFieldsModel",
    "AppRegistry",
    "Metric",
]
