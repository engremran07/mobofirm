from __future__ import annotations

import pytest
from django.urls import reverse

from apps.firmwares.blog_automation import FirmwareBlogService
from apps.firmwares.chipsets import create_chipset_record
from apps.firmwares.models import Brand, Model, Variant

pytestmark = pytest.mark.django_db


def test_model_hub_shows_variant_identity_and_different_chipset_siblings():
    brand = Brand.objects.create(name="Samsung", slug="samsung")
    exynos = create_chipset_record(
        vendor="Samsung",
        family="Exynos",
        name="Exynos 2400",
        slug="samsung-exynos-2400",
    )
    snapdragon = create_chipset_record(
        vendor="Qualcomm",
        family="Snapdragon",
        name="8 Gen 3",
        slug="qualcomm-snapdragon-8-gen-3",
    )
    model = Model.objects.create(
        brand=brand,
        name="Galaxy S24 Ultra",
        slug="sm-s928b",
        codename="e3q",
        also_known_as="Galaxy S24 Ultra Global",
        chipset="Samsung Exynos 2400",
        chipset_ref=exynos,
        regions=["AFR"],
    )
    Variant.objects.create(
        model=model,
        name="Global",
        slug="global",
        model_code="SM-S928B",
        region="AFR",
        board_id="E3Q",
        carrier="Unlocked",
        chipset="Samsung Exynos 2400",
        chipset_ref=exynos,
    )
    Variant.objects.create(
        model=model,
        name="Dual SIM",
        slug="dual-sim",
        model_code="SM-S928B/DS",
        region="EUX",
        is_active=False,
        chipset="Samsung Exynos 2400",
        chipset_ref=exynos,
    )
    sibling = Model.objects.create(
        brand=brand,
        name=model.name,
        slug="sm-s928u",
        model_code="SM-S928U",
        chipset="Qualcomm Snapdragon 8 Gen 3",
        chipset_ref=snapdragon,
    )
    same_chipset_sibling = Model.objects.create(
        brand=brand,
        name=model.name,
        slug="sm-s928b-alt",
        chipset="Samsung Exynos 2400",
        chipset_ref=exynos,
    )

    draft = FirmwareBlogService._model_draft(model)

    assert "Samsung Exynos 2400" in draft.title
    assert "All Variants Firmwares" in draft.title
    assert '<th scope="row">Variant codes</th>' in draft.body
    assert "SM-S928B, SM-S928B/DS" in draft.body
    assert "e3q" in draft.body
    assert "Codename" in draft.body
    assert "Model code" not in draft.body
    assert "AFR, EUX" in draft.body
    assert "E3Q" in draft.body
    assert "Unlocked" in draft.body
    assert "Related chipset variants" in draft.body
    assert (
        reverse(
            "firmwares:model_detail",
            kwargs={"brand_slug": brand.slug, "model_slug": sibling.slug},
        )
        in draft.body
    )
    assert (
        reverse(
            "firmwares:model_detail",
            kwargs={"brand_slug": brand.slug, "model_slug": same_chipset_sibling.slug},
        )
        not in draft.body
    )


def test_model_hub_omits_chipset_suffix_when_model_has_no_chipset():
    brand = Brand.objects.create(name="Acme", slug="acme")
    model = Model.objects.create(brand=brand, name="Phone", slug="phone")

    draft = FirmwareBlogService._model_draft(model)

    assert draft.title == "Acme Phone — All Variants Firmwares & Flashing Guide"
    assert "  —" not in draft.title
