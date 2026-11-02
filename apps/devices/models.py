from django.db import models


class Device(models.Model):
    name = models.CharField(max_length=200)
    model_code = models.CharField(max_length=100, blank=True, default="")
    brand = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["brand", "name"]
        indexes = [models.Index(fields=["brand", "name"], name="device_brand_name_idx")]

    def __str__(self) -> str:
        return f"{self.brand} {self.name}".strip()
