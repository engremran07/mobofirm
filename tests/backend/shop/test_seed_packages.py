"""The standard Free, Premium and Gold packages created by ``seed_packages``."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.management import call_command

from apps.shop.entitlements import effective_entitlements
from apps.shop.models import PackageTier

pytestmark = pytest.mark.django_db


def test_creates_the_standard_ladder_of_packages():
    call_command("seed_packages")

    packages = {package.slug: package for package in PackageTier.objects.all()}
    assert set(packages) == {"free", "silver", "premium", "gold", "platinum"}
    assert packages["free"].is_default and packages["free"].price == Decimal(0)
    assert packages["premium"].price == Decimal(9)
    assert packages["gold"].device_limit == 10
    assert not packages["free"].applies_to_featured
    assert packages["premium"].applies_to_featured
    assert packages["gold"].applies_to_paid


def test_the_ladder_carries_the_ad_rules():
    call_command("seed_packages")

    free, silver, gold, platinum = (
        PackageTier.objects.get(slug=slug)
        for slug in ("free", "silver", "gold", "platinum")
    )
    assert (free.unlock_by_ads_free, free.unlock_ads_free) == (True, 3)
    assert free.unlock_by_ads_featured and not free.direct_links and not free.ad_free
    assert silver.direct_links and not silver.ad_free and silver.unlock_by_ads_free
    assert gold.ad_free and gold.direct_links
    assert platinum.ad_free and platinum.direct_links
    assert (
        platinum.limit_files > gold.limit_files > silver.limit_files > free.limit_files
    )
    assert [pkg.sort_order for pkg in (free, silver, gold, platinum)] == sorted(
        pkg.sort_order for pkg in (free, silver, gold, platinum)
    )
    for package in (free, silver, gold, platinum):
        package.full_clean(exclude=["slug"])


def test_existing_rows_are_kept_and_only_get_untouched_ad_fields():
    PackageTier.objects.create(
        name="Free",
        slug="free",
        is_default=True,
        duration_days=0,
        limit_files=7,
        window="day",
        description="Edited by staff",
    )
    PackageTier.objects.create(
        name="Gold",
        slug="gold",
        price=Decimal(60),
        duration_days=365,
        applies_to_featured=True,
        ad_free=True,
    )
    PackageTier.objects.create(
        name="Premium",
        slug="premium",
        price=Decimal(9),
        duration_days=30,
    )

    call_command("seed_packages")
    call_command("seed_packages")

    free = PackageTier.objects.get(slug="free")
    gold = PackageTier.objects.get(slug="gold")
    assert (free.limit_files, free.description) == (7, "Edited by staff")
    assert free.unlock_by_ads_free and free.unlock_by_ads_featured
    assert gold.price == Decimal(60) and gold.direct_links
    assert PackageTier.objects.get(slug="premium").price == Decimal(9)
    assert PackageTier.objects.filter(slug__in=["silver", "platinum"]).count() == 2
    assert PackageTier.objects.count() == 5


def test_non_default_staff_values_survive_a_rerun():
    call_command("seed_packages")
    PackageTier.objects.filter(slug="free").update(unlock_ads_free=7)
    PackageTier.objects.filter(slug="gold").update(unlock_cooldown_seconds=90)

    call_command("seed_packages")

    assert PackageTier.objects.get(slug="free").unlock_ads_free == 7
    assert PackageTier.objects.get(slug="gold").unlock_cooldown_seconds == 90


def test_the_disabled_unlock_ad_is_created_once():
    from apps.ads.models import RewardedAdConfig

    call_command("seed_packages")
    call_command("seed_packages")

    configs = RewardedAdConfig.objects.filter(name="Download unlock")
    assert configs.count() == 1
    config = configs.get()
    assert config.reward_type == "download_unlock" and not config.is_enabled


def test_exactly_one_default_package_and_summaries_render():
    call_command("seed_packages")

    assert PackageTier.objects.filter(is_default=True).count() == 1
    for package in PackageTier.objects.all():
        assert package.summary()


def test_rerun_keeps_values_staff_edited():
    call_command("seed_packages")
    PackageTier.objects.filter(slug="premium").update(price=Decimal(12))

    call_command("seed_packages")

    assert PackageTier.objects.count() == 5
    assert PackageTier.objects.get(slug="premium").price == Decimal(12)


def test_new_accounts_get_the_seeded_free_allowance(django_user_model):
    call_command("seed_packages")
    user = django_user_model.objects.create_user(
        username="fresh", email="fresh@example.com", password="Pass-12345!"
    )

    entitlements = effective_entitlements(user)

    assert entitlements.device_limit == 3
    assert not entitlements.can_access_featured
