from django.db import models


class RewardedAdConfig(models.Model):
    name = models.CharField(max_length=100, unique=True)
    provider = models.CharField(max_length=50, blank=True, default="")
    configuration = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Rewarded Ad Configuration"
        verbose_name_plural = "Rewarded Ad Configurations"

    def __str__(self) -> str:
        return self.name
