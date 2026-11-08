"""Fixtures for the shop and My files entitlement tests."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from django.utils import timezone

from apps.firmwares.models import Brand, Model, OfficialFirmware, Variant
from apps.shop import wallet_gateway
from apps.shop.models import PackageTier, WindowKind

PASSWORD = "Pass-12345!"


@pytest.fixture(autouse=True)
def no_wallet(monkeypatch) -> None:
    """Shop tests run without credits unless a test installs a wallet."""
    monkeypatch.setattr(wallet_gateway, "_api", lambda: None)


@pytest.fixture
def make_user(django_user_model) -> Callable[..., Any]:
    def build(username: str, *, staff: bool = False, verified: bool = True) -> Any:
        return django_user_model.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password=PASSWORD,
            is_staff=staff,
            is_superuser=staff,
            email_verified_at=timezone.now() if verified else None,
        )

    return build


@pytest.fixture
def user(make_user) -> Any:
    return make_user("buyer")


@pytest.fixture
def other(make_user) -> Any:
    return make_user("other_buyer")


@pytest.fixture
def staff(make_user) -> Any:
    return make_user("shop_staff", staff=True)


@pytest.fixture
def device(db) -> tuple[Brand, Model, Variant]:
    brand = Brand.objects.create(name="Acme", slug="acme")
    model = Model.objects.create(brand=brand, name="Phone One", slug="phone-one")
    variant = Variant.objects.create(model=model, name="Global", slug="global")
    return brand, model, variant


@pytest.fixture
def make_firmware(device) -> Callable[..., Any]:
    brand, model, variant = device
    counter = iter(range(1, 1000))

    def build(
        tier: str = "free",
        price: str | None = None,
        *,
        size_mb: int = 0,
        **extra: Any,
    ) -> Any:
        number = next(counter)
        fields: dict[str, Any] = {
            "original_file_name": f"fw-{number}.zip",
            "stored_file_path": f"/firmware/fw-{number}.zip",
            "file_size": size_mb * 1024 * 1024,
            "brand": brand,
            "model": model,
            "variant": variant,
            "is_verified": True,
            "is_active": True,
            "access_tier": tier,
            "approved_price": Decimal(price) if price else None,
            **extra,
        }
        return OfficialFirmware.objects.create(**fields)

    return build


@pytest.fixture
def default_tier(db) -> PackageTier:
    """The free default package: 3 new files in total per month window, free only."""
    return PackageTier.objects.create(
        name="Free",
        slug="free",
        is_default=True,
        duration_days=0,
        window=WindowKind.DAY,
        limit_files=2,
        fair_monthly_files=3,
        device_limit=3,
    )


@pytest.fixture
def pro_tier(db) -> PackageTier:
    """A paid package with unlimited downloads and featured access."""
    return PackageTier.objects.create(
        name="Pro",
        slug="pro",
        price=Decimal("9.00"),
        duration_days=30,
        bandwidth_enabled=False,
        limit_devices=False,
        applies_to_featured=True,
        applies_to_paid=True,
    )
