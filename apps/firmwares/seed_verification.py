"""Acceptance checks for a seeded device catalogue.

Counts, review state and automated articles are checked against the database; public
pages are fetched through Django's in-process test client, so the real URL
configuration, middleware and templates are exercised without a running server.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from django.conf import settings
from django.db.models import Count
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.core.seeding import check

from .models import (
    AutomatedBlogLink,
    Brand,
    Chipset,
    DownloadToken,
    FirmwareSourceType,
    Model,
    OfficialFirmware,
)
from .seed_data.types import BrandSeed
from .seeding import (
    EXTERNAL_HOST,
    catalogue_name,
    chipset_slug,
    model_slug,
    variant_chipset_key,
)

MINIMUM_BRANDS = 20
MINIMUM_MODELS_PER_BRAND = 5
MINIMUM_FIRMWARE_PER_MODEL = 2


@dataclass
class CatalogVerification:
    brands: int = 0
    models: int = 0
    firmware: int = 0
    chipsets: int = 0
    pages: int = 0
    posts: dict[str, int] = field(default_factory=dict)

    def lines(self) -> list[str]:
        return [
            f"  brands verified: {self.brands}",
            f"  models verified: {self.models}",
            f"  official external firmware verified: {self.firmware}",
            f"  chipsets verified: {self.chipsets}",
            f"  public pages returning 200: {self.pages}",
            *(
                f"  automated {kind} posts: {count}"
                for kind, count in self.posts.items()
            ),
        ]


def _host() -> str:
    for host in settings.ALLOWED_HOSTS:
        if host and not host.startswith((".", "*")):
            return host
    return "localhost"


def _verify_firmware(
    brands: Sequence[BrandSeed], today: date, result: CatalogVerification
) -> None:
    for brand_seed in brands:
        brand = Brand.objects.get(slug=brand_seed.slug)
        models = Model.objects.filter(brand=brand, is_active=True)
        check(
            models.count() >= MINIMUM_MODELS_PER_BRAND,
            f"{brand.name} has fewer than {MINIMUM_MODELS_PER_BRAND} models.",
        )
        for seed in brand_seed.models:
            model = Model.objects.get(
                brand=brand, slug=model_slug(catalogue_name(brand_seed, seed))
            )
            files = list(
                OfficialFirmware.objects.filter(
                    model=model, is_verified=True, is_active=True
                )
            )
            check(
                len(files) >= MINIMUM_FIRMWARE_PER_MODEL,
                f"{brand.name} {model.name} has fewer than "
                f"{MINIMUM_FIRMWARE_PER_MODEL} approved official firmware files.",
            )
            for firmware in files:
                label = f"{brand.name} {model.name} {firmware.build_number}"
                check(
                    firmware.source_type == FirmwareSourceType.EXTERNAL,
                    f"{label} is not an external listing.",
                )
                check(
                    firmware.external_url.startswith(f"https://{EXTERNAL_HOST}/"),
                    f"{label} does not use the reserved dummy host.",
                )
                check(
                    firmware.reviewed_by_id is not None
                    and firmware.reviewed_at is not None
                    and firmware.reviewed_by_id != firmware.uploader_id,
                    f"{label} lacks an independent review record.",
                )
                check(
                    bool(firmware.build_number and firmware.android_version),
                    f"{label} lacks build details.",
                )
                check(
                    firmware.build_date is not None
                    and firmware.security_patch is not None
                    and firmware.build_date <= today
                    and firmware.security_patch <= today,
                    f"{label} has a missing or future date.",
                )
                check(
                    not firmware.file_hash and firmware.file_size is None,
                    f"{label} carries a fabricated size or checksum.",
                )
            result.models += 1
            result.firmware += len(files)
        result.brands += 1


def _verify_chipsets(brands: Sequence[BrandSeed], result: CatalogVerification) -> None:
    keys = {variant_chipset_key(seed) for brand in brands for seed in brand.models} - {
        ""
    }
    for key in keys:
        chipset = Chipset.objects.filter(slug=chipset_slug(key)).first()
        check(chipset is not None, f"Chipset {key} was not created.")
    check(
        not Model.objects.filter(brand__slug__in=[b.slug for b in brands])
        .exclude(chipset="")
        .filter(chipset_ref__isnull=True)
        .exists(),
        "A model with chipset text has no resolved chipset record.",
    )
    result.chipsets = len(keys)


def _verify_articles(brands: Sequence[BrandSeed], result: CatalogVerification) -> None:
    for kind in ("brand", "model", "chipset"):
        duplicated = (
            AutomatedBlogLink.objects.filter(kind=kind)
            .values(kind)
            .annotate(total=Count("pk"))
            .filter(total__gt=1)
        )
        check(not duplicated.exists(), f"A {kind} owns more than one automated post.")

    for brand_seed in brands:
        brand = Brand.objects.get(slug=brand_seed.slug)
        brand_link = AutomatedBlogLink.objects.filter(kind="brand", brand=brand)
        check(brand_link.count() == 1, f"{brand.name} lacks its automated post.")
        check(
            brand_link.get().post.is_live,
            f"The {brand.name} automated post is not public.",
        )
        for seed in brand_seed.models:
            model = Model.objects.get(
                brand=brand, slug=model_slug(catalogue_name(brand_seed, seed))
            )
            links = AutomatedBlogLink.objects.filter(kind="model", model=model)
            check(links.count() == 1, f"{model} lacks its automated post.")
            post = links.get().post
            check(post.is_live, f"The automated post for {model} is not public.")
            check(
                post.firmware_model_id == model.pk,
                f"The automated post for {model} is linked to another model.",
            )
            files = OfficialFirmware.objects.filter(
                model=model, is_verified=True, is_active=True
            )
            check(
                f"Official firmware ({files.count()})" in post.body,
                f"The post for {model} does not group its official firmware.",
            )
            check(
                all(
                    name in post.body
                    for name in files.values_list("original_file_name", flat=True)
                ),
                f"The post for {model} omits an approved firmware file.",
            )

    chipset_links = AutomatedBlogLink.objects.filter(kind="chipset")
    for chipset in Chipset.objects.filter(
        models__brand__slug__in=[b.slug for b in brands]
    ).distinct():
        check(
            chipset_links.filter(chipset=chipset).count() == 1,
            f"{chipset} lacks its automated post.",
        )
    result.posts = {
        "brand": AutomatedBlogLink.objects.filter(kind="brand").count(),
        "model": AutomatedBlogLink.objects.filter(kind="model").count(),
        "chipset": chipset_links.count(),
    }


def _get_ok(client: Client, path: str, result: CatalogVerification) -> None:
    response = client.get(path, HTTP_HOST=_host(), secure=settings.SECURE_SSL_REDIRECT)
    check(response.status_code == 200, f"GET {path} returned {response.status_code}.")
    result.pages += 1


def _verify_pages(brands: Sequence[BrandSeed], result: CatalogVerification) -> None:
    started = timezone.now()
    client = Client(raise_request_exception=False)
    try:
        for name in ("library_hub", "browse", "brand_list", "chipset_list"):
            _get_ok(client, reverse(f"firmwares:{name}"), result)
        for brand_seed in brands:
            brand = Brand.objects.get(slug=brand_seed.slug)
            _get_ok(
                client,
                reverse("firmwares:brand_detail", kwargs={"slug": brand.slug}),
                result,
            )
            for seed in brand_seed.models:
                model = Model.objects.get(
                    brand=brand, slug=model_slug(catalogue_name(brand_seed, seed))
                )
                _get_ok(
                    client,
                    reverse(
                        "firmwares:model_detail",
                        kwargs={"brand_slug": brand.slug, "model_slug": model.slug},
                    ),
                    result,
                )
                for firmware in OfficialFirmware.objects.filter(model=model):
                    kwargs = {
                        "firmware_type": "official",
                        "firmware_id": str(firmware.pk),
                    }
                    _get_ok(
                        client,
                        reverse("firmwares:firmware_detail", kwargs=kwargs),
                        result,
                    )
                    _get_ok(
                        client,
                        reverse("firmwares:firmware_download", kwargs=kwargs),
                        result,
                    )
        for chipset in Chipset.objects.all():
            _get_ok(
                client,
                reverse("firmwares:chipset_detail", kwargs={"slug": chipset.slug}),
                result,
            )
    finally:
        DownloadToken.objects.filter(created_at__gte=started).delete()


def verify_catalog(
    brands: Sequence[BrandSeed], *, today: date, minimum_brands: int = MINIMUM_BRANDS
) -> CatalogVerification:
    """Assert the catalogue acceptance criteria; raises on the first violation."""
    result = CatalogVerification()
    check(
        Brand.objects.count() >= minimum_brands,
        f"Fewer than {minimum_brands} brands exist.",
    )
    check(
        all(len(brand.models) >= MINIMUM_MODELS_PER_BRAND for brand in brands),
        "A seeded brand defines fewer than the minimum number of models.",
    )
    _verify_firmware(brands, today, result)
    _verify_chipsets(brands, result)
    _verify_articles(brands, result)
    _verify_pages(brands, result)
    return result
