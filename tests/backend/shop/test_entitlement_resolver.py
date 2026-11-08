"""effective_entitlements: the one resolver for quotas, devices, item types and perks."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, override_settings
from django.utils import timezone

from apps.shop import entitlements, request_cache, services
from apps.shop.entitlements import Entitlements
from apps.shop.middleware import EntitlementCacheMiddleware
from apps.shop.models import (
    PackageQuotaOverride,
    PackageTier,
    SizeUnit,
    UserPackage,
    WindowKind,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def free_tier(db) -> PackageTier:
    return PackageTier.objects.create(
        name="Free",
        slug="free",
        is_default=True,
        duration_days=0,
        window=WindowKind.MONTH,
        limit_files=20,
        device_limit=3,
    )


@pytest.fixture
def gold(db) -> PackageTier:
    return PackageTier.objects.create(
        name="Gold",
        slug="gold",
        price=Decimal("9.00"),
        duration_days=30,
        window=WindowKind.PACKAGE,
        limit_files=100,
        limit_size=20,
        size_unit=SizeUnit.GB,
        device_limit=10,
        applies_to_featured=True,
        applies_to_paid=True,
        paid_discount_percent=20,
        ad_free=True,
        direct_links=True,
    )


@pytest.fixture
def clean_modifiers():
    saved = list(entitlements._modifiers)
    entitlements._modifiers.clear()
    yield
    entitlements._modifiers[:] = saved


class TestFreeAndPaidPackage:
    def test_one_package_definition_scales_downloads_devices_and_types(
        self, user, free_tier, gold
    ):
        free = entitlements.effective_entitlements(user)
        assert (free.download_quota, free.window, free.unlimited) == (
            20,
            "month",
            False,
        )
        assert free.device_limit == 3
        assert free.package_name == "Free"
        assert free.item_types == ("free",)
        assert free.sources == ("Free tier: 20 files per month, 3 devices",)
        assert (free.can_access_featured, free.can_upload_paid) == (False, False)
        assert not free.perks.ad_free
        assert free.paid_discount_percent == 0

        services.activate_package(user, gold)
        paid = entitlements.effective_entitlements(user)

        assert (paid.download_quota, paid.device_limit) == (100, 10)
        assert paid.package_name == "Gold"
        assert paid.window == "package"
        assert paid.item_types == ("free", "featured", "paid")
        assert paid.can_access_featured
        assert paid.perks.ad_free and paid.perks.direct_links
        assert paid.paid_discount_percent == 20
        assert paid.limits.size_mb == 20 * 1024
        assert paid.sources == (
            "Gold package: 100 files / 20 GB per 30 days, 10 devices",
        )
        assert paid.plan_summary == gold.summary()

    @override_settings(
        SHOP_FREE_DAILY_DOWNLOADS=4,
        SHOP_FREE_MONTHLY_DOWNLOADS=40,
        SHOP_FREE_DEVICE_LIMIT=2,
    )
    def test_settings_build_a_transient_free_package_when_none_is_default(self, user):
        free = entitlements.effective_entitlements(user)

        assert (free.download_quota, free.window, free.device_limit) == (4, "day", 2)
        assert free.limits.fair_monthly_files == 40
        assert free.tier is None
        assert not PackageTier.objects.exists()

    def test_inactive_default_falls_back_to_settings(self, user, free_tier):
        PackageTier.objects.filter(pk=free_tier.pk).update(is_active=False)

        assert entitlements.effective_entitlements(user).window == "day"

    def test_unlimited_dimensions_are_none(self, user):
        PackageTier.objects.create(
            name="Open",
            slug="open",
            is_default=True,
            duration_days=0,
            bandwidth_enabled=False,
            limit_devices=False,
        )

        result = entitlements.effective_entitlements(user)

        assert result.unlimited
        assert (result.download_quota, result.device_limit) == (None, None)
        assert result.devices_text == "unlimited devices"
        assert "Unlimited downloads" in result.sources[0]

    def test_anonymous_visitors_get_the_free_package_only(self, free_tier, gold):
        result = entitlements.effective_entitlements(AnonymousUser())

        assert result.download_quota == 20
        assert not result.can_access_featured

    def test_expired_and_cancelled_packages_fall_back_to_the_free_package(
        self, user, free_tier, gold
    ):
        package = services.activate_package(user, gold)
        UserPackage.objects.filter(pk=package.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        assert entitlements.active_package(user) is None
        assert entitlements.effective_entitlements(user).package_name == "Free"

        services.cancel_package(services.activate_package(user, gold))
        assert entitlements.effective_entitlements(user).package_name == "Free"

    def test_the_package_window_is_the_package_period(self, user, free_tier, gold):
        package = services.activate_package(user, gold)

        result = entitlements.effective_entitlements(user)

        assert result.window_start == package.started_at
        assert result.resets_at == package.expires_at

    def test_a_lifetime_package_never_resets(self, user, free_tier, gold):
        PackageTier.objects.filter(pk=gold.pk).update(duration_days=0)
        gold.refresh_from_db()
        package = services.activate_package(user, gold)

        assert package.expires_at is None
        assert entitlements.effective_entitlements(user).resets_at is None

    def test_public_helpers_all_read_the_resolver(self, user, free_tier, gold):
        services.activate_package(user, gold)

        assert (
            entitlements.active_package(user)
            == entitlements.effective_entitlements(user).package
        )
        snapshot = entitlements.quota_snapshot(user)
        assert (snapshot.limit, snapshot.package_name) == (100, "Gold")


class TestStaffOverride:
    def override(self, user_package, **values):
        return PackageQuotaOverride.objects.create(user_package=user_package, **values)

    def test_adjusts_quota_and_devices_with_a_source(self, user, free_tier, gold):
        package = services.activate_package(user, gold)
        self.override(package, limit_files=250, device_limit=6, reason="beta tester")

        result = entitlements.effective_entitlements(user)

        assert (result.download_quota, result.device_limit) == (250, 6)
        assert result.sources[-1] == "Staff override (beta tester)"

    def test_zero_means_unlimited(self, user, free_tier, gold):
        package = services.activate_package(user, gold)
        self.override(package, limit_files=0, limit_size_mb=0, device_limit=0)

        result = entitlements.effective_entitlements(user)

        assert result.unlimited
        assert (result.download_quota, result.device_limit) == (None, None)
        assert result.limits.size_mb == -1

    def test_an_override_can_meter_an_unmetered_package(self, user, free_tier):
        open_tier = PackageTier.objects.create(
            name="Open",
            slug="open",
            price=Decimal(1),
            bandwidth_enabled=False,
            applies_to_free=True,
        )
        package = services.activate_package(user, open_tier)
        self.override(package, limit_files=3)

        result = entitlements.effective_entitlements(user)

        assert result.download_quota == 3
        assert result.limits.metered == {"free"}

    def test_expired_override_is_ignored(self, user, free_tier, gold):
        package = services.activate_package(user, gold)
        self.override(
            package, limit_files=999, expires_at=timezone.now() - timedelta(days=1)
        )

        assert entitlements.effective_entitlements(user).download_quota == 100


class TestSellerFlag:
    def test_can_upload_paid_follows_the_marketplace_seller_check(
        self, user, free_tier, monkeypatch
    ):
        monkeypatch.setattr(
            "apps.marketplace.services.is_active_seller", lambda candidate: True
        )

        assert entitlements.effective_entitlements(user).can_upload_paid

    def test_ordinary_users_cannot_upload_paid(self, user, free_tier):
        assert not entitlements.effective_entitlements(user).can_upload_paid


class TestModifiers:
    def test_campaign_modifier_adds_bonus_and_explains_it(
        self, user, free_tier, clean_modifiers
    ):
        def launch_bonus(candidate, current: Entitlements) -> Entitlements:
            return entitlements.grant(
                current,
                "Campaign 'Launch bonus' +10 until Dec 1",
                download_quota=10,
                device_limit=1,
            )

        entitlements.register_entitlement_modifier(launch_bonus)
        entitlements.register_entitlement_modifier(launch_bonus)

        result = entitlements.effective_entitlements(user)

        assert entitlements._modifiers == [launch_bonus]
        assert (result.download_quota, result.device_limit) == (30, 4)
        assert result.limits.files == 30
        assert " + ".join(result.sources) == (
            "Free tier: 20 files per month, 3 devices"
            " + Campaign 'Launch bonus' +10 until Dec 1"
        )
        assert entitlements.quota_snapshot(user).limit == 30

    def test_modifier_can_grant_featured_access_without_a_package(
        self, user, free_tier, clean_modifiers, make_firmware
    ):
        entitlements.register_entitlement_modifier(
            lambda candidate, current: entitlements.grant(
                current, "Trial", can_access_featured=True
            )
        )
        firmware = make_firmware("featured")

        entitlements.consume_quota(user, firmware, reason="featured")

        assert entitlements.effective_entitlements(user).can_access_featured
        assert entitlements.active_package(user) is None

    def test_bonus_never_limits_an_unlimited_dimension(self, user, clean_modifiers):
        PackageTier.objects.create(
            name="Open",
            slug="open",
            is_default=True,
            duration_days=0,
            bandwidth_enabled=False,
            limit_devices=False,
        )
        entitlements.register_entitlement_modifier(
            lambda candidate, current: entitlements.grant(
                current, "Bonus", download_quota=5, device_limit=2
            )
        )

        result = entitlements.effective_entitlements(user)

        assert (result.download_quota, result.device_limit) == (None, None)

    def test_modifiers_do_not_run_for_anonymous_visitors(
        self, free_tier, clean_modifiers
    ):
        entitlements.register_entitlement_modifier(
            lambda candidate, current: entitlements.grant(
                current, "Bonus", download_quota=5
            )
        )

        assert entitlements.effective_entitlements(AnonymousUser()).download_quota == 20


class TestRequestCache:
    def test_there_is_no_cache_outside_a_request(self, user, free_tier):
        first = entitlements.effective_entitlements(user)
        PackageTier.objects.filter(pk=free_tier.pk).update(limit_files=30)

        assert entitlements.effective_entitlements(user).download_quota == 30
        assert first.download_quota == 20

    def test_resolves_once_per_request_and_mutators_invalidate(
        self, user, free_tier, gold, django_assert_num_queries
    ):
        token = request_cache.begin()
        try:
            first = entitlements.effective_entitlements(user)
            with django_assert_num_queries(0):
                assert entitlements.effective_entitlements(user) is first
            services.activate_package(user, gold)
            assert entitlements.effective_entitlements(user).package_name == "Gold"
        finally:
            request_cache.end(token)

    def test_middleware_scopes_the_cache_to_the_request(self, user, free_tier):
        seen: list[Entitlements] = []

        def view(request):
            seen.append(entitlements.effective_entitlements(user))
            seen.append(entitlements.effective_entitlements(user))
            return object()

        EntitlementCacheMiddleware(view)(RequestFactory().get("/"))

        assert seen[0] is seen[1]
        assert request_cache.lookup(user.pk) is None
