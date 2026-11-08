"""The standard Free, Premium and Gold packages created by ``seed_packages``."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.management import call_command

from apps.shop.entitlements import effective_entitlements
from apps.shop.models import PackageTier

pytestmark = pytest.mark.django_db


def test_creates_the_three_standard_packages():
    call_command("seed_packages")

    packages = {package.slug: package for package in PackageTier.objects.all()}
    assert set(packages) == {"free", "premium", "gold"}
    assert packages["free"].is_default and packages["free"].price == Decimal(0)
    assert packages["premium"].price == Decimal(9)
    assert packages["gold"].device_limit == 10
    assert not packages["free"].applies_to_featured
    assert packages["premium"].applies_to_featured
    assert packages["gold"].applies_to_paid


def test_exactly_one_default_package_and_summaries_render():
    call_command("seed_packages")

    assert PackageTier.objects.filter(is_default=True).count() == 1
    for package in PackageTier.objects.all():
        assert package.summary()


def test_rerun_keeps_values_staff_edited():
    call_command("seed_packages")
    PackageTier.objects.filter(slug="premium").update(price=Decimal(12))

    call_command("seed_packages")

    assert PackageTier.objects.count() == 3
    assert PackageTier.objects.get(slug="premium").price == Decimal(12)


def test_new_accounts_get_the_seeded_free_allowance(django_user_model):
    call_command("seed_packages")
    user = django_user_model.objects.create_user(
        username="fresh", email="fresh@example.com", password="Pass-12345!"
    )

    entitlements = effective_entitlements(user)

    assert entitlements.device_limit == 3
    assert not entitlements.can_access_featured
