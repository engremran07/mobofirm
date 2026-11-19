"""Catalogue pages run a constant number of queries however much data they list.

Each page is requested with a small and then a larger catalogue; the query count must not
grow with the number of brands, models, variants or firmware rows (no N+1).
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.firmwares.catalog import PUBLIC_FIRMWARE_TYPES
from apps.firmwares.models import Brand, Chipset, Model, Variant

PAGES = {
    "hub": lambda ctx: reverse("firmwares:library_hub"),
    "brand_list": lambda ctx: reverse("firmwares:brand_list"),
    "brand_detail": lambda ctx: reverse("firmwares:brand_detail", args=[ctx.slug]),
    "chipset_detail": lambda ctx: reverse(
        "firmwares:chipset_detail", args=[ctx.chipset_slug]
    ),
    "chipset_list": lambda ctx: reverse("firmwares:chipset_list"),
    "browse": lambda ctx: reverse("firmwares:browse"),
    "model_detail": lambda ctx: reverse(
        "firmwares:model_detail", args=[ctx.slug, ctx.model_slug]
    ),
}


class Context:
    slug = "budget-brand"
    chipset_slug = "budget-soc"
    model_slug = "budget-model-0"


def grow(count: int, start: int = 0) -> None:
    """Make sure the catalogue holds ``count`` models (with variants and firmware)."""
    brand, _ = Brand.objects.get_or_create(name="Budget Brand", slug=Context.slug)
    Chipset.objects.get_or_create(
        vendor="Budget", name="SoC", slug=Context.chipset_slug
    )
    for index in range(start, count):
        model = Model.objects.create(
            brand=brand,
            name=f"Budget Model {index}",
            slug=f"budget-model-{index}",
            chipset="Budget SoC",
        )
        variant = Variant.objects.create(
            model=model, name=f"Variant {index}", slug=f"variant-{index}"
        )
        for key, cls in PUBLIC_FIRMWARE_TYPES[:3]:
            cls.objects.create(
                original_file_name=f"{key}-{index}.zip",
                stored_file_path=f"/files/{key}-{index}.zip",
                brand=brand,
                model=model,
                variant=variant,
                is_verified=True,
                build_number=f"B{index}",
            )
    if not Brand.objects.exclude(pk=brand.pk).exists():
        for other in range(3):
            Brand.objects.create(name=f"Other {other}", slug=f"other-{other}")


def query_count(url: str) -> int:
    client = Client()
    client.get(url)  # warm the shared caches (settings, content types)
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url)
    assert response.status_code == 200
    return len(queries)


@pytest.mark.django_db
@pytest.mark.parametrize("page", sorted(PAGES))
def test_the_query_count_does_not_grow_with_the_catalogue(page):
    grow(3)
    Model.objects.update(chipset_ref=Chipset.objects.get(slug=Context.chipset_slug))
    url = PAGES[page](Context)
    small = query_count(url)

    grow(12, start=3)
    Model.objects.update(chipset_ref=Chipset.objects.get(slug=Context.chipset_slug))
    large = query_count(url)

    assert large <= small, f"{page}: {small} queries for 3 models, {large} for 12"
