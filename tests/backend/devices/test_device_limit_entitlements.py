"""The device limit has one authority: effective_entitlements(user).device_limit."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import RequestFactory, override_settings
from django.utils import timezone

from apps.devices.models import Device
from apps.devices.services import (
    UNLIMITED_DEVICES,
    DevicePolicyError,
    approve_current_device,
    get_device_limit,
    get_device_registration_summary,
    hash_browser_key,
    resolve_or_create_device,
)
from apps.shop import entitlements, services
from apps.shop.models import (
    PackageQuotaOverride,
    PackageTier,
    UserPackage,
    WindowKind,
)

pytestmark = pytest.mark.django_db

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/121.0 Safari/537"


@pytest.fixture
def seed_free_tier() -> PackageTier:
    return PackageTier.objects.create(
        name="Free",
        slug="free",
        is_default=True,
        duration_days=0,
        window=WindowKind.MONTH,
        limit_files=20,
        device_limit=2,
    )


@pytest.fixture(name="premium")
def premium_fixture() -> PackageTier:
    return PackageTier.objects.create(
        name="Gold",
        slug="gold",
        price=Decimal("9.00"),
        duration_days=30,
        window=WindowKind.PACKAGE,
        limit_files=100,
        device_limit=6,
    )


@pytest.fixture
def isolated_modifiers(monkeypatch):
    monkeypatch.setattr(entitlements, "_modifiers", [])


def request_for(user, key: str):
    request = RequestFactory().get("/", HTTP_USER_AGENT=UA)
    request.COOKIES["mobofirm_device"] = key
    request.user = user
    request.META["REMOTE_ADDR"] = "203.0.113.10"
    return request


class TestLimitComesFromEntitlements:
    @pytest.mark.usefixtures("seed_free_tier")
    def test_free_account_uses_the_default_package(self, user):
        assert get_device_limit(user) == 2
        assert (
            get_device_limit(user)
            == entitlements.effective_entitlements(user).device_limit
        )

    @override_settings(SHOP_FREE_DEVICE_LIMIT=4)
    def test_settings_are_only_the_free_default_input(self, user):
        assert get_device_limit(user) == 4

    @pytest.mark.usefixtures("seed_free_tier")
    def test_premium_package_raises_the_limit(self, user, premium):
        assert get_device_limit(user) == 2

        services.activate_package(user, premium)

        assert get_device_limit(user) == 6
        assert get_device_registration_summary(user)["max_devices"] == 6

    @pytest.mark.usefixtures("seed_free_tier")
    def test_staff_override_on_the_package_changes_the_limit(
        self, user, staff, premium
    ):
        services.activate_package(user, premium)
        package = UserPackage.objects.get(user=user, package=premium)
        PackageQuotaOverride.objects.create(
            user_package=package, device_limit=9, granted_by=staff
        )

        assert get_device_limit(user) == 9

    @pytest.mark.usefixtures("seed_free_tier")
    def test_expired_override_is_ignored(self, user, premium):
        services.activate_package(user, premium)
        package = UserPackage.objects.get(user=user, package=premium)
        PackageQuotaOverride.objects.create(
            user_package=package,
            device_limit=9,
            expires_at=timezone.now() - timedelta(days=1),
        )

        assert get_device_limit(user) == 6

    @pytest.mark.usefixtures("seed_free_tier", "isolated_modifiers")
    def test_campaign_modifier_adds_device_slots(self, user):
        entitlements.register_entitlement_modifier(
            lambda _user, current: entitlements.grant(
                current, "Campaign: launch", device_limit=3
            )
        )

        assert get_device_limit(user) == 5

    def test_unlimited_package_means_no_limit(self, user):
        PackageTier.objects.create(
            name="Open",
            slug="open",
            is_default=True,
            duration_days=0,
            limit_devices=False,
        )

        assert get_device_limit(user) == UNLIMITED_DEVICES

    @pytest.mark.usefixtures("seed_free_tier")
    def test_superusers_are_never_limited(self, staff):
        assert get_device_limit(staff) == UNLIMITED_DEVICES

    @pytest.mark.usefixtures("seed_free_tier")
    def test_the_removed_device_config_field_cannot_set_the_limit(self):
        from apps.devices.models import DeviceConfig

        assert not hasattr(DeviceConfig.get_solo(), "max_devices_default")


class TestEnforcementUsesThePackageLimit:
    def _pending(self, user, key: str) -> Device:
        request = request_for(user, key)
        device, _, _ = resolve_or_create_device(request, user)
        assert device is not None
        return device

    @pytest.mark.usefixtures("seed_free_tier")
    def test_free_account_cannot_approve_past_its_limit(self, user, approve_browsers):
        approve_browsers(user, 2)
        key = "p" * 43
        pending = self._pending(user, key)

        with pytest.raises(DevicePolicyError) as error:
            approve_current_device(request_for(user, key), user)

        assert error.value.reason == "limit_reached"
        pending.refresh_from_db()
        assert pending.is_trusted is False

    @pytest.mark.usefixtures("seed_free_tier")
    def test_premium_account_can_approve_the_same_browser(
        self, user, premium, approve_browsers
    ):
        approve_browsers(user, 2)
        key = "q" * 43
        self._pending(user, key)
        services.activate_package(user, premium)

        device = approve_current_device(request_for(user, key), user)

        assert device.is_trusted is True
        assert Device.objects.filter(user=user, is_trusted=True).count() == 3

    @pytest.mark.usefixtures("seed_free_tier")
    def test_browser_key_stays_a_digest_in_the_database(self, user):
        key = "d" * 43
        device = self._pending(user, key)

        assert device.browser_key_hash == hash_browser_key(key)
        assert key not in device.browser_key_hash
