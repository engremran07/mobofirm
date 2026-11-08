"""The Package model: summary, validation and one-default rule."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.shop.models import PackageTier, SizeUnit, WindowKind

pytestmark = pytest.mark.django_db


def build(**values) -> PackageTier:
    defaults = {"name": "Gold", "slug": "gold"}
    return PackageTier(**{**defaults, **values})


class TestSummary:
    def test_the_documented_example(self):
        package = build(
            price=Decimal(9),
            duration_days=30,
            limit_files=30,
            limit_size=20,
            size_unit=SizeUnit.GB,
            window=WindowKind.PACKAGE,
            applies_to_free=True,
            applies_to_featured=True,
            limit_devices=True,
            device_limit=5,
        )

        assert package.summary() == (
            "30 files / 20 GB per 30 days · free + featured · 5 devices · $9/mo"
        )

    def test_free_default_package_reads_naturally(self):
        package = build(
            price=Decimal(0),
            duration_days=0,
            is_default=True,
            window=WindowKind.DAY,
            limit_files=10,
            limit_size=3,
            device_limit=3,
        )

        assert package.summary() == "10 files / 3 GB per day · free · 3 devices · Free"

    @pytest.mark.parametrize(
        ("values", "expected"),
        [
            ({"bandwidth_enabled": False}, "Unlimited downloads"),
            ({"limit_files": 0, "limit_size": 0}, "Unlimited downloads"),
            ({"limit_files": 1}, "1 file per month"),
            (
                {"limit_files": 5, "window": WindowKind.PACKAGE, "duration_days": 0},
                "5 files in total",
            ),
            (
                {"limit_size": 512, "size_unit": SizeUnit.MB, "duration_days": 365},
                "512 MB per month",
            ),
        ],
    )
    def test_quota_text(self, values, expected):
        base = {"bandwidth_enabled": True, "limit_files": 0, "limit_size": 0}
        assert build(**{**base, **values}).quota_text == expected

    @pytest.mark.parametrize(
        ("days", "price", "expected"),
        [
            (0, "99", "$99 once"),
            (30, "9", "$9/mo"),
            (365, "90", "$90/yr"),
            (7, "3.5", "$3.50/wk"),
            (45, "5", "$5 / 45 days"),
            (30, "0", "Free"),
        ],
    )
    def test_price_text(self, days, price, expected):
        assert build(duration_days=days, price=Decimal(price)).price_text == expected

    def test_unlimited_devices_and_extra_text(self):
        package = build(limit_devices=False, extra_daily_files=2, extra_daily_size=1)

        assert package.devices_text == "unlimited devices"
        assert package.extra_text == "+2 files / 1 GB extra per day"
        assert build(device_limit=1).devices_text == "1 device"

    def test_sizes_are_converted_to_megabytes(self):
        package = build(limit_size=2, extra_daily_size=1, size_unit=SizeUnit.GB)

        assert (package.limit_size_mb, package.extra_daily_size_mb) == (2048, 1024)
        assert build(limit_size=300, size_unit=SizeUnit.MB).limit_size_mb == 300


class TestValidation:
    def valid(self, **values) -> PackageTier:
        return build(limit_files=5, **values)

    def test_a_sensible_package_is_valid(self):
        self.valid().full_clean()

    def test_at_least_one_item_type(self):
        package = self.valid(applies_to_free=False)

        with pytest.raises(ValidationError, match="at least one item type"):
            package.full_clean()

    def test_price_cannot_be_negative(self):
        with pytest.raises(ValidationError) as caught:
            self.valid(price=Decimal(-1)).full_clean()

        assert "price" in caught.value.message_dict

    def test_bandwidth_needs_a_limit_or_must_be_off(self):
        with pytest.raises(ValidationError) as caught:
            build().full_clean()

        assert "limit_files" in caught.value.message_dict
        build(bandwidth_enabled=False).full_clean()

    def test_a_device_cap_needs_a_positive_count(self):
        with pytest.raises(ValidationError, match="at least one device"):
            self.valid(limit_devices=True, device_limit=0).full_clean()

    def test_the_default_package_is_free_lifetime_active_and_not_package_windowed(self):
        package = self.valid(
            is_default=True,
            price=Decimal(5),
            duration_days=30,
            window=WindowKind.PACKAGE,
            is_active=False,
        )

        with pytest.raises(ValidationError) as caught:
            package.full_clean()

        assert set(caught.value.message_dict) >= {
            "price",
            "duration_days",
            "window",
            "is_active",
        }

    def test_only_one_default_package(self):
        self.valid(is_default=True, duration_days=0).save()
        second = self.valid(
            name="Other", slug="other", is_default=True, duration_days=0
        )

        with pytest.raises(ValidationError, match="already the default"):
            second.full_clean()

    def test_a_paid_discount_needs_the_paid_item_type(self):
        with pytest.raises(ValidationError) as caught:
            self.valid(paid_discount_percent=10).full_clean()

        assert "paid_discount_percent" in caught.value.message_dict
        self.valid(paid_discount_percent=10, applies_to_paid=True).full_clean()

    def test_the_monthly_fair_cap_cannot_undercut_the_daily_cap(self):
        with pytest.raises(ValidationError) as caught:
            self.valid(fair_daily_files=10, fair_monthly_files=5).full_clean()

        assert "fair_monthly_files" in caught.value.message_dict

    def test_lifetime_packages_are_allowed_with_a_package_window(self):
        self.valid(
            duration_days=0, window=WindowKind.PACKAGE, price=Decimal(50)
        ).full_clean()
