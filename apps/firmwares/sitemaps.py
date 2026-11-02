from __future__ import annotations

from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from apps.site_settings.models import SiteSettings

from .models import (
    Brand,
    EngineeringFirmware,
    Model,
    ModifiedFirmware,
    OfficialFirmware,
    OtherFirmware,
    ReadbackFirmware,
)


class FirmwareBrandsSitemap(Sitemap):
    """Sitemap for firmware brand pages (e.g. /firmwares/brand/samsung/)."""

    changefreq = "weekly"
    priority = 0.7

    def __init__(self) -> None:
        super().__init__()
        try:
            ss = SiteSettings.get_solo()
        except Exception:
            ss = None
        force_https = bool(getattr(ss, "force_https", False))
        self.protocol = "https" if force_https else "http"

    def items(self):
        return Brand.objects.order_by("name")

    def lastmod(self, obj: Brand):
        return getattr(obj, "updated_at", None)

    def location(self, obj: Brand) -> str:
        return reverse("firmwares:brand_detail", kwargs={"slug": obj.slug})


class FirmwareModelsSitemap(Sitemap):
    """Sitemap for firmware model pages (e.g. /firmwares/brand/samsung/galaxy-s23/)."""

    changefreq = "daily"
    priority = 0.8

    def __init__(self) -> None:
        super().__init__()
        try:
            ss = SiteSettings.get_solo()
        except Exception:
            ss = None
        force_https = bool(getattr(ss, "force_https", False))
        self.protocol = "https" if force_https else "http"

    def items(self):
        return (
            Model.objects.select_related("brand")
            .filter(brand__isnull=False)
            .order_by("brand__name", "name")
        )

    def lastmod(self, obj: Model):
        return getattr(obj, "updated_at", None)

    def location(self, obj: Model) -> str:
        return reverse(
            "firmwares:model_detail",
            kwargs={"brand_slug": obj.brand.slug, "model_slug": obj.slug},
        )


# Mapping of URL type slug → model class
_FIRMWARE_FILE_TYPES: list[tuple[str, type]] = [
    ("official", OfficialFirmware),
    ("engineering", EngineeringFirmware),
    ("readback", ReadbackFirmware),
    ("modified", ModifiedFirmware),
    ("other", OtherFirmware),
]


class FirmwareFileSitemap(Sitemap):
    """Sitemap for individual firmware file detail pages."""

    changefreq = "monthly"
    priority = 0.8
    limit = 5000

    def __init__(self) -> None:
        super().__init__()
        try:
            ss = SiteSettings.get_solo()
        except Exception:
            ss = None
        force_https = bool(getattr(ss, "force_https", False))
        self.protocol = "https" if force_https else "http"

    def items(self) -> list:
        """Return all active firmware files across all types."""
        results = []
        for type_slug, model_cls in _FIRMWARE_FILE_TYPES:
            qs = model_cls.objects.filter(is_active=True).order_by("-created_at")
            for fw in qs:
                fw._sitemap_type = type_slug  # noqa: SLF001
                results.append(fw)
        return results

    def lastmod(self, obj):  # type: ignore[override]
        return getattr(obj, "updated_at", None) or getattr(obj, "created_at", None)

    def location(self, obj) -> str:  # type: ignore[override]
        return reverse(
            "firmwares:firmware_detail",
            kwargs={
                "firmware_type": obj._sitemap_type,  # noqa: SLF001
                "firmware_id": str(obj.id),
            },
        )
