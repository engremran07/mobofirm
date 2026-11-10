from django.core.validators import MaxValueValidator, MinValueValidator
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


class SearchSettings(SingletonModel):
    """Staff-editable behaviour of the unified search and the popular blocks."""

    popular_enabled = models.BooleanField(
        default=True,
        help_text="Show 'Popular models', 'Popular firmware' and 'Popular articles'.",
    )
    popular_window_days = models.PositiveSmallIntegerField(
        default=30,
        validators=[MinValueValidator(1), MaxValueValidator(365)],
        help_text="Downloads and views of the last N days decide what is popular.",
    )
    popular_limit = models.PositiveSmallIntegerField(
        default=6,
        validators=[MinValueValidator(1), MaxValueValidator(24)],
        help_text="How many entries each popular block lists.",
    )
    popular_cache_minutes = models.PositiveSmallIntegerField(
        default=10,
        validators=[MinValueValidator(1), MaxValueValidator(1440)],
        help_text="How long a computed popular list is reused before recomputing.",
    )

    class Meta:
        verbose_name = "Search and popularity settings"
        verbose_name_plural = "Search and popularity settings"

    def __str__(self) -> str:
        return "Search and popularity settings"


__all__ = [
    "AppRegistry",
    "AuditFieldsModel",
    "Metric",
    "SearchSettings",
    "SoftDeleteModel",
    "TimestampedModel",
]
