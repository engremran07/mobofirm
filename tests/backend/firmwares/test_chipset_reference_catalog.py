from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.blog.models import Post
from apps.firmwares.chipsets import SUPPORTED_CHIPSET_VENDORS
from apps.firmwares.models import Chipset
from apps.firmwares.seed_data import CHIPSETS, PLATFORM_COVERAGE_KEYS
from apps.firmwares.seeding import seed_reference_chipsets

pytestmark = pytest.mark.django_db


def test_the_catalogue_covers_every_supported_vendor_and_family():
    vendors = {chip.vendor for chip in CHIPSETS}
    families = {chip.family for chip in CHIPSETS}

    assert vendors == set(SUPPORTED_CHIPSET_VENDORS)
    assert {
        "Snapdragon",
        "Dimensity",
        "Helio",
        "Kompanio",
        "Exynos",
        "Tensor",
        "Kirin",
        "Unisoc T",
        "Spreadtrum",
        "RDA",
        "Rockchip RK",
        "Allwinner A",
        "A-series",
        "M-series",
    } <= families
    assert len({chip.key for chip in CHIPSETS}) == len(CHIPSETS)
    assert len(CHIPSETS) > 300
    assert {chip.key for chip in CHIPSETS if chip.vendor == "Rockchip"} <= (
        PLATFORM_COVERAGE_KEYS
    )


def test_reference_seed_creates_every_row_without_publishing_posts():
    posts_before = Post.objects.count()

    report = seed_reference_chipsets()

    assert Chipset.objects.count() == len(CHIPSETS)
    assert report.created["chipsets"] == len(CHIPSETS)
    assert Post.objects.count() == posts_before
    plus = Chipset.objects.get(slug="qualcomm-snapdragon-888-plus")
    assert "Snapdragon 888+" in plus.aliases
    assert plus.family == "Snapdragon"
    assert Chipset.objects.get(slug="apple-a6").family == "A-series"


def test_reference_seed_is_idempotent_and_keeps_existing_rows():
    seed_reference_chipsets()
    existing = Chipset.objects.get(slug="qualcomm-snapdragon-8-elite")
    Chipset.objects.filter(pk=existing.pk).update(description="Edited by staff")

    report = seed_reference_chipsets()

    assert report.existing["chipsets"] == len(CHIPSETS)
    assert Chipset.objects.count() == len(CHIPSETS)
    existing.refresh_from_db()
    assert existing.description == "Edited by staff"


def test_the_command_refuses_to_write_without_confirmation():
    with pytest.raises(CommandError, match="--confirm"):
        call_command("seed_chipsets")
    assert Chipset.objects.count() == 0
