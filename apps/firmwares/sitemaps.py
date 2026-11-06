from __future__ import annotations

from typing import Any

from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from apps.site_settings.services import get_site_settings

from .catalog import PUBLIC_FIRMWARE_TYPES, get_public_firmware_queryset, label_firmware
from .models import Brand, Chipset, FlashingTool, Model


class _FirmwareSitemap(Sitemap):
    def __init__(self) -> None:
        super().__init__()
        self.protocol = "https" if get_site_settings().force_https else "http"


class FirmwareBrandsSitemap(_FirmwareSitemap):
    """Sitemap for firmware brand pages (e.g. /firmwares/samsung/)."""

    changefreq = "weekly"
    priority = 0.7

    def items(self):
        return Brand.objects.order_by("name")

    def lastmod(self, obj: Brand):
        return getattr(obj, "updated_at", None)

    def location(self, obj: Brand) -> str:
        return reverse("firmwares:brand_detail", kwargs={"slug": obj.slug})


class FirmwareChipsetsSitemap(_FirmwareSitemap):
    """Sitemap for chipset pages (e.g. /firmwares/chipsets/mediatek-helio-g100/)."""

    changefreq = "weekly"
    priority = 0.6

    def items(self):
        return Chipset.objects.order_by("vendor", "name")

    def lastmod(self, obj: Chipset):
        return getattr(obj, "updated_at", None)

    def location(self, obj: Chipset) -> str:
        return reverse("firmwares:chipset_detail", kwargs={"slug": obj.slug})


class FirmwareToolsSitemap(_FirmwareSitemap):
    """Sitemap for flashing-tool pages (e.g. /firmwares/tools/odin/)."""

    changefreq = "monthly"
    priority = 0.5

    def items(self):
        return FlashingTool.objects.filter(is_active=True).order_by("name")

    def lastmod(self, obj: FlashingTool):
        return getattr(obj, "updated_at", None)

    def location(self, obj: FlashingTool) -> str:
        return reverse("firmwares:flashing_tool_detail", kwargs={"slug": obj.slug})


class FirmwareModelsSitemap(_FirmwareSitemap):
    """Sitemap for firmware model pages (e.g. /firmwares/samsung/galaxy-s23/)."""

    changefreq = "daily"
    priority = 0.8

    def items(self):
        return (
            Model.objects.select_related("brand")
            .filter(brand__isnull=False, is_active=True)
            .order_by("brand__name", "name")
        )

    def lastmod(self, obj: Model):
        return getattr(obj, "updated_at", None)

    def location(self, obj: Model) -> str:
        return reverse(
            "firmwares:model_detail",
            kwargs={"brand_slug": obj.brand.slug, "model_slug": obj.slug},
        )


class FirmwareFileSitemap(_FirmwareSitemap):
    """Sitemap for firmware detail pages; only publicly listed firmware appears."""

    changefreq = "monthly"
    priority = 0.8
    limit = 5000

    def items(self) -> list:
        """Return every public firmware file across all categories."""
        results: list[Any] = []
        for type_slug, model_cls in PUBLIC_FIRMWARE_TYPES:
            queryset = get_public_firmware_queryset(model_cls).order_by("-created_at")
            results.extend(label_firmware(fw, type_slug) for fw in queryset)
        return results

    def lastmod(self, obj):
        return getattr(obj, "updated_at", None) or getattr(obj, "created_at", None)

    def location(self, obj) -> str:
        return reverse(
            "firmwares:firmware_detail",
            kwargs={
                "firmware_type": obj.firmware_type,
                "firmware_id": str(obj.id),
            },
        )
