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
    tags_enabled = models.BooleanField(default=True)
    users_enabled = models.BooleanField(default=True)
    device_identity_enabled = models.BooleanField(default=True)
    storage_enabled = models.BooleanField(default=True)
    seo_enabled = models.BooleanField(default=True)
    pages_enabled = models.BooleanField(default=True)
    shop_enabled = models.BooleanField(default=True)
    marketplace_enabled = models.BooleanField(default=True)
    wallet_enabled = models.BooleanField(default=True)
    referral_enabled = models.BooleanField(default=True)
    consent_enabled = models.BooleanField(default=True)
    gamification_enabled = models.BooleanField(default=True)
    bounty_enabled = models.BooleanField(default=True)
    distribution_enabled = models.BooleanField(default=True)
    promotions_enabled = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Application Registry"

    def __str__(self) -> str:
        return "Application Registry"


__all__ = [
    "AppRegistry",
    "AuditFieldsModel",
    "Metric",
    "SoftDeleteModel",
    "TimestampedModel",
]
