"""Download lifetime rules: free re-download allowance, package periods, lifetime files."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.firmwares.access import (
    PACKAGE_EXPIRED_MESSAGE,
    check_download_access,
    finalize_served_download,
    served_status,
)
from apps.firmwares.models import (
    Brand,
    DownloadRule,
    Model,
    OfficialFirmware,
    ServedDownload,
    Variant,
)
from apps.firmwares.package_history import (
    backfill_download_rules,
    purge_superseded_package_history,
)
from apps.shop import services
from apps.shop.models import (
    PackageTier,
    QuotaConsumption,
    ShopSettings,
    UserPackage,
    WindowKind,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(name="member")
def member_fixture(django_user_model) -> Any:
    return django_user_model.objects.create_user(
        username="member",
        email="member@example.com",
        email_verified_at=timezone.now(),
    )


@pytest.fixture(name="make_file")
def make_file_fixture() -> Any:
    brand = Brand.objects.create(name="Acme", slug="acme")
    model = Model.objects.create(brand=brand, name="Phone One", slug="phone-one")
    variant = Variant.objects.create(model=model, name="Global", slug="global")
    counter = iter(range(1, 1000))

    def build(tier: str = "free", price: str | None = None) -> OfficialFirmware:
        number = next(counter)
        return OfficialFirmware.objects.create(
            original_file_name=f"fw-{number}.zip",
            stored_file_path=f"/firmware/fw-{number}.zip",
            brand=brand,
            model=model,
            variant=variant,
            is_verified=True,
            is_active=True,
            access_tier=tier,
            approved_price=Decimal(price) if price else None,
        )

    return build


@pytest.fixture(name="package")
def package_fixture() -> PackageTier:
    return PackageTier.objects.create(
        name="Gold",
        slug="gold",
        price=Decimal("9.00"),
        duration_days=30,
        bandwidth_enabled=False,
        limit_devices=False,
        applies_to_featured=True,
        applies_to_paid=True,
    )


def serve(user: Any, firmware: OfficialFirmware) -> ServedDownload:
    decision = check_download_access(user, firmware)
    assert decision.allowed, decision.reason
    row = finalize_served_download(user, firmware)
    assert row is not None
    return row


def set_policy(window: int, maximum: int, retention: int = 0) -> None:
    settings = ShopSettings.get_solo()
    settings.free_redownload_window_days = window
    settings.free_redownload_max = maximum
    settings.package_history_retention_days = retention
    settings.save()


class TestFreeRedownloadAllowance:
    def test_a_free_file_can_be_fetched_again_a_bounded_number_of_times(
        self, member, make_file
    ) -> None:
        set_policy(30, 2)
        firmware = make_file()

        row = serve(member, firmware)
        assert row.access_rule == DownloadRule.FREE
        serve(member, firmware)
        serve(member, firmware)

        decision = check_download_access(member, firmware)
        assert not decision.allowed
        assert "re-download allowance" in decision.reason
        assert "30 days / 2 re-downloads" in decision.reason
        with pytest.raises(PermissionDenied):
            finalize_served_download(member, firmware)

    def test_the_window_ends_the_allowance(self, member, make_file) -> None:
        set_policy(30, 0)
        firmware = make_file()
        row = serve(member, firmware)
        assert check_download_access(member, firmware).allowed

        ServedDownload.objects.filter(pk=row.pk).update(
            served_at=timezone.now() - timedelta(days=31)
        )

        status = served_status(member, ServedDownload.objects.get(pk=row.pk))
        assert not status.active
        assert status.code == "allowance_ended"
        assert not check_download_access(member, firmware).allowed

    def test_zero_means_unlimited(self, member, make_file) -> None:
        set_policy(0, 0)
        firmware = make_file()
        row = serve(member, firmware)
        ServedDownload.objects.filter(pk=row.pk).update(
            served_at=timezone.now() - timedelta(days=900), times_served=500
        )

        assert check_download_access(member, firmware).allowed

    def test_visitors_are_not_limited(self, make_file) -> None:
        assert check_download_access(None, make_file()).allowed

    def test_a_promoted_file_keeps_the_grandfathered_allowance(
        self, member, make_file
    ) -> None:
        set_policy(30, 5)
        firmware = make_file()
        serve(member, firmware)
        firmware.access_tier = "featured"
        firmware.save(update_fields=["access_tier"])

        decision = check_download_access(member, firmware)

        assert decision.allowed
        assert decision.already_served


class TestPackagePeriods:
    def test_a_package_file_is_served_under_the_package_period(
        self, member, make_file, package
    ) -> None:
        held = services.activate_package(member, package)
        firmware = make_file("featured")

        row = serve(member, firmware)

        assert row.access_rule == DownloadRule.PACKAGE
        assert row.user_package_id == held.pk
        consumption = QuotaConsumption.objects.get(user=member)
        assert consumption.user_package_id == held.pk

    def test_expiry_stops_the_download_and_asks_to_renew(
        self, member, make_file, package
    ) -> None:
        held = services.activate_package(member, package)
        firmware = make_file("featured")
        serve(member, firmware)

        UserPackage.objects.filter(pk=held.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )

        decision = check_download_access(member, firmware)
        assert not decision.allowed
        assert decision.reason == PACKAGE_EXPIRED_MESSAGE
        assert decision.requires == "package"

    def test_cancelling_the_package_stops_the_download(
        self, member, make_file, package
    ) -> None:
        held = services.activate_package(member, package)
        firmware = make_file("featured")
        serve(member, firmware)

        services.cancel_package(held)

        assert not check_download_access(member, firmware).allowed

    def test_renewal_starts_a_new_period_and_counts_the_file_again(
        self, member, make_file, package
    ) -> None:
        first = services.activate_package(member, package)
        firmware = make_file("featured")
        serve(member, firmware)

        second = services.activate_package(member, package)
        assert second.pk != first.pk
        row = serve(member, firmware)

        assert row.user_package_id == second.pk
        assert row.times_served == 1
        consumption = QuotaConsumption.objects.get(user=member)
        assert consumption.user_package_id == second.pk

    def test_the_new_period_quota_window_is_honest(self, member, make_file) -> None:
        tier = PackageTier.objects.create(
            name="Single",
            slug="single",
            price=Decimal("5.00"),
            duration_days=30,
            bandwidth_enabled=True,
            limit_files=1,
            window=WindowKind.PACKAGE,
            limit_devices=False,
            applies_to_featured=True,
        )
        services.activate_package(member, tier)
        first, second = make_file("featured"), make_file("featured")
        serve(member, first)
        assert not check_download_access(member, second).allowed

        services.activate_package(member, tier)

        # The renewed window starts empty: one file fits, counted exactly once.
        assert check_download_access(member, second).allowed
        serve(member, first)
        assert not check_download_access(member, second).allowed

    def test_a_file_bought_one_by_one_never_expires(
        self, member, make_file, package
    ) -> None:
        held = services.activate_package(member, package)
        firmware = make_file("paid", "4.00")
        row = ServedDownload.objects.create(
            user=member,
            content_type=ContentType.objects.get_for_model(
                firmware, for_concrete_model=False
            ),
            object_id=firmware.pk,
            tier="paid",
            access_rule=DownloadRule.LIFETIME,
        )
        UserPackage.objects.filter(pk=held.pk).update(status=UserPackage.Status.EXPIRED)

        assert served_status(member, row).active


class TestPackageHistoryPurge:
    def test_activation_purges_the_previous_period_but_not_free_files(
        self,
        member,
        make_file,
        package,
        django_capture_on_commit_callbacks,
    ) -> None:
        services.activate_package(member, package)
        featured, free = make_file("featured"), make_file()
        serve(member, featured)
        serve(member, free)

        with django_capture_on_commit_callbacks(execute=True):
            services.activate_package(member, package)

        remaining = ServedDownload.objects.filter(user=member)
        assert [row.object_id for row in remaining] == [free.pk]

    def test_retention_keeps_the_history_for_the_configured_days(
        self, member, make_file, package
    ) -> None:
        set_policy(30, 5, retention=10)
        first = services.activate_package(member, package)
        serve(member, make_file("featured"))
        second = services.activate_package(member, package)

        assert purge_superseded_package_history() == 0
        UserPackage.objects.filter(pk=first.pk).update(
            started_at=timezone.now() - timedelta(days=40)
        )
        UserPackage.objects.filter(pk=second.pk).update(
            started_at=timezone.now() - timedelta(days=11)
        )
        assert first.pk != second.pk
        assert purge_superseded_package_history() == 1

    def test_the_live_period_is_never_purged(self, member, make_file, package) -> None:
        services.activate_package(member, package)
        serve(member, make_file("featured"))

        assert purge_superseded_package_history() == 0
        assert ServedDownload.objects.filter(user=member).count() == 1


class TestBackfill:
    def test_existing_grants_get_their_rule_and_it_is_idempotent(
        self, member, make_file, package
    ) -> None:
        held = services.activate_package(member, package)
        featured, paid = make_file("featured"), make_file("paid", "3.00")
        for firmware in (featured, paid):
            ServedDownload.objects.create(
                user=member,
                content_type=ContentType.objects.get_for_model(
                    firmware, for_concrete_model=False
                ),
                object_id=firmware.pk,
                tier=firmware.access_tier,
            )

        first = backfill_download_rules()
        second = backfill_download_rules()

        rows = {row.tier: row for row in ServedDownload.objects.filter(user=member)}
        assert rows["paid"].access_rule == DownloadRule.LIFETIME
        assert rows["featured"].access_rule == DownloadRule.PACKAGE
        assert rows["featured"].user_package_id == held.pk
        assert first.changed == 2
        assert second.changed == 0
