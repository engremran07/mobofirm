"""Account-level quota overrides change device registration, not just the number shown."""

from __future__ import annotations

import pytest
from django.test import RequestFactory

from apps.devices.models import Device
from apps.devices.services import (
    DevicePolicyError,
    approve_current_device,
    get_device_limit,
    resolve_or_create_device,
)
from apps.shop import overrides
from apps.shop.models import PackageTier, WindowKind

pytestmark = pytest.mark.django_db

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/121.0 Safari/537"


@pytest.fixture(autouse=True)
def free_tier() -> PackageTier:
    return PackageTier.objects.create(
        name="Free",
        slug="free",
        is_default=True,
        duration_days=0,
        window=WindowKind.MONTH,
        limit_files=20,
        device_limit=2,
    )


def request_for(user, key: str):
    request = RequestFactory().get("/", HTTP_USER_AGENT=UA)
    request.COOKIES["mobofirm_device"] = key
    request.user = user
    request.META["REMOTE_ADDR"] = "203.0.113.10"
    return request


def test_a_higher_override_allows_the_registration_the_package_blocked(
    user, staff, approve_browsers
):
    approve_browsers(user, 2)
    key = "o" * 43
    resolve_or_create_device(request_for(user, key), user)
    with pytest.raises(DevicePolicyError) as error:
        approve_current_device(request_for(user, key), user)
    assert error.value.reason == "limit_reached"

    overrides.grant_override(user, actor=staff, reason="Family", device_limit=4)

    assert get_device_limit(user) == 4
    device = approve_current_device(request_for(user, key), user)
    assert (
        device.is_trusted
        and Device.objects.filter(user=user, is_trusted=True).count() == 3
    )


def test_a_lower_override_blocks_a_registration_the_package_allowed(
    user, staff, approve_browsers
):
    approve_browsers(user, 1)
    key = "n" * 43
    resolve_or_create_device(request_for(user, key), user)
    overrides.grant_override(user, actor=staff, reason="Abuse", device_limit=1)

    with pytest.raises(DevicePolicyError) as error:
        approve_current_device(request_for(user, key), user)

    assert error.value.reason == "limit_reached"


def test_revoking_the_override_restores_the_package_limit(user, staff):
    override = overrides.grant_override(user, actor=staff, reason="x", device_limit=9)
    assert get_device_limit(user) == 9

    overrides.revoke_override(override, actor=staff)

    assert get_device_limit(user) == 2
