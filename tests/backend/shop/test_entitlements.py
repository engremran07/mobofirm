"""Per-file entitlements and quotas: the contract the firmware download gate uses."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.events.bus import event_bus
from apps.shop import entitlements, services
from apps.shop.entitlements import NotEntitled, PurchaseError, QuotaExceeded
from apps.shop.models import (
    Order,
    OrderItem,
    PackageTier,
    QuotaConsumption,
    SizeUnit,
    UserPackage,
    WindowKind,
)

pytestmark = pytest.mark.django_db


def pay(order: Order, staff, reference: str = "RCPT") -> Order:
    return services.mark_order_paid(order.pk, staff_user=staff, reference=reference)


def package(slug: str, **values) -> PackageTier:
    base = {
        "name": slug.title(),
        "slug": slug,
        "price": Decimal(5),
        "duration_days": 30,
        "window": WindowKind.MONTH,
    }
    return PackageTier.objects.create(**{**base, **values})


def consume(user, firmware, reason: str = "free") -> None:
    entitlements.consume_quota(user, firmware, reason=reason)


class TestAllowanceDefaults:
    def test_default_package_gives_everyone_a_meaningful_snapshot(
        self, user, default_tier
    ):
        snapshot = entitlements.quota_snapshot(user)

        assert snapshot.package_name == "Free"
        assert (snapshot.limit, snapshot.used, snapshot.remaining) == (2, 0, 2)
        assert snapshot.window_label == "today"
        assert not snapshot.unlimited
        assert snapshot.percent == 0
        assert entitlements.active_package(user) is None
        assert not UserPackage.objects.exists()

    def test_unlimited_package_reports_unlimited(self, user, pro_tier):
        services.activate_package(user, pro_tier)

        snapshot = entitlements.quota_snapshot(user)

        assert snapshot.unlimited
        assert (snapshot.limit, snapshot.remaining) == (None, None)
        assert snapshot.package_name == "Pro"
        assert "unlimited" in snapshot.text
        assert snapshot.remaining_text == "Unlimited"

    def test_anonymous_visitors_have_no_usage_and_cannot_consume(
        self, default_tier, make_firmware
    ):
        anonymous = AnonymousUser()

        assert entitlements.quota_snapshot(anonymous).used == 0
        assert entitlements.active_package(anonymous) is None
        assert not entitlements.has_purchased(anonymous, make_firmware("paid", "5.00"))
        with pytest.raises(NotEntitled):
            consume(anonymous, make_firmware())

    def test_meters_cover_files_size_and_fair_usage(self, user):
        package(
            "metered",
            is_default=True,
            duration_days=0,
            limit_files=10,
            limit_size=100,
            size_unit=SizeUnit.MB,
            fair_daily_files=4,
        )

        meters = entitlements.quota_windows(user)

        assert [(m.kind, m.limit) for m in meters] == [
            ("files", 10),
            ("size", 100),
            ("files", 4),
        ]
        assert meters[2].label == "Fair use · files today"
        assert meters[1].text == "0 MB of 100 MB used this month"


class TestWindows:
    def test_day_window_blocks_new_files_but_not_served_ones(
        self, user, default_tier, make_firmware
    ):
        first, second, third = make_firmware(), make_firmware(), make_firmware()
        consume(user, first)
        consume(user, second)

        with pytest.raises(QuotaExceeded, match="File allowance used up today"):
            consume(user, third)
        consume(user, first)

        assert not entitlements.check_quota(user, third).allowed
        assert entitlements.check_quota(user, first).allowed
        assert not QuotaConsumption.objects.filter(object_id=third.pk).exists()

    def test_the_month_window_keeps_counting_across_days(self, user, make_firmware):
        package("month", is_default=True, duration_days=0, limit_files=1)
        consume(user, make_firmware())
        QuotaConsumption.objects.update(consumed_at=timezone.now())

        with pytest.raises(QuotaExceeded, match="this month"):
            consume(user, make_firmware())

    def test_the_month_window_rolls_over_on_the_first(self, user, make_firmware):
        package("month", is_default=True, duration_days=0, limit_files=1)
        consume(user, make_firmware())
        month_start = entitlements._month_bounds(timezone.now())[0]
        QuotaConsumption.objects.update(consumed_at=month_start - timedelta(seconds=1))

        assert entitlements.quota_snapshot(user).used == 0
        assert entitlements.check_quota(user, make_firmware()).allowed

    def test_the_package_window_counts_from_activation(self, user, make_firmware):
        tier = package("span", window=WindowKind.PACKAGE, limit_files=1)
        consume(user, make_firmware())
        QuotaConsumption.objects.update(consumed_at=timezone.now() - timedelta(days=3))
        services.activate_package(user, tier)

        assert entitlements.quota_snapshot(user).used == 0
        consume(user, make_firmware())
        with pytest.raises(QuotaExceeded, match="in this package"):
            consume(user, make_firmware())

    def test_reset_times_are_the_next_local_midnight_and_month_start(self, user):
        package("day", is_default=True, duration_days=0, limit_files=1, window="day")
        day = entitlements.quota_snapshot(user)

        assert day.resets_at is not None
        assert day.resets_at > timezone.now()
        assert timezone.localtime(day.resets_at).replace(
            hour=0, minute=0, second=0, microsecond=0
        ) == timezone.localtime(day.resets_at)
        assert entitlements._month_bounds(timezone.now())[1].day == 1

    def test_size_limits(self, user, make_firmware):
        package(
            "size",
            is_default=True,
            duration_days=0,
            limit_size=100,
            size_unit=SizeUnit.MB,
            window=WindowKind.DAY,
        )
        consume(user, make_firmware(size_mb=60))

        with pytest.raises(QuotaExceeded, match="Size allowance"):
            consume(user, make_firmware(size_mb=50))
        consume(user, make_firmware(size_mb=40))


class TestExtraBandwidth:
    def test_the_extra_daily_allowance_is_added_after_the_window_is_spent(
        self, user, make_firmware
    ):
        package(
            "extra",
            is_default=True,
            duration_days=0,
            limit_files=2,
            extra_daily_files=1,
        )
        consume(user, make_firmware())
        consume(user, make_firmware())

        snapshot = entitlements.quota_snapshot(user)
        assert snapshot.remaining == 1
        consume(user, make_firmware())
        with pytest.raises(QuotaExceeded):
            consume(user, make_firmware())

    def test_extra_size_lets_one_more_file_through(self, user, make_firmware):
        package(
            "extrasize",
            is_default=True,
            duration_days=0,
            limit_size=10,
            size_unit=SizeUnit.MB,
            extra_daily_size=5,
        )
        consume(user, make_firmware(size_mb=10))

        consume(user, make_firmware(size_mb=5))
        with pytest.raises(QuotaExceeded):
            consume(user, make_firmware(size_mb=1))


class TestItemTypeBuckets:
    def test_featured_files_need_a_package_that_covers_them(
        self, user, default_tier, pro_tier, make_firmware
    ):
        firmware = make_firmware("featured")

        with pytest.raises(NotEntitled):
            consume(user, firmware, "featured")
        assert not entitlements.check_quota(user, firmware).allowed

        services.activate_package(user, pro_tier)
        consume(user, firmware, "featured")
        consume(user, firmware, "featured")

        assert QuotaConsumption.objects.get().reason == "featured"

    def test_a_default_package_may_include_featured_files(self, user, make_firmware):
        package(
            "open",
            is_default=True,
            duration_days=0,
            limit_files=5,
            applies_to_featured=True,
        )

        consume(user, make_firmware("featured"), "featured")

    def test_featured_files_share_the_package_quota(self, user, make_firmware):
        tier = package(
            "basic",
            limit_files=1,
            window=WindowKind.DAY,
            applies_to_featured=True,
        )
        services.activate_package(user, tier)
        consume(user, make_firmware("featured"), "featured")

        with pytest.raises(QuotaExceeded):
            consume(user, make_firmware("featured"), "featured")
        with pytest.raises(QuotaExceeded):
            consume(user, make_firmware())

    def test_unmetered_item_types_never_use_the_quota(self, user, make_firmware):
        package(
            "freeunmetered",
            is_default=True,
            duration_days=0,
            limit_files=1,
            applies_to_free=False,
            applies_to_featured=True,
        )

        for _ in range(3):
            consume(user, make_firmware())

        assert entitlements.quota_snapshot(user).used == 0
        assert QuotaConsumption.objects.count() == 3

    def test_paid_files_count_only_when_the_package_covers_them(
        self, user, staff, make_firmware
    ):
        package(
            "counts",
            is_default=True,
            duration_days=0,
            limit_files=5,
            applies_to_paid=True,
        )
        paid = make_firmware("paid", "3.00")
        pay(entitlements.request_purchase(user, paid), staff)

        consume(user, paid, "paid")

        assert entitlements.quota_snapshot(user).used == 1

    def test_paid_files_outside_the_package_are_recorded_but_not_counted(
        self, user, staff, default_tier, make_firmware
    ):
        paid = make_firmware("paid", "3.00")
        pay(entitlements.request_purchase(user, paid), staff)

        consume(user, paid, "paid")

        assert entitlements.quota_snapshot(user).used == 0
        assert QuotaConsumption.objects.get().reason == "paid"


class TestFairUsage:
    def test_daily_cap_applies_even_without_bandwidth_limits(self, user, make_firmware):
        package(
            "fair",
            is_default=True,
            duration_days=0,
            bandwidth_enabled=False,
            fair_daily_files=1,
        )
        consume(user, make_firmware())

        with pytest.raises(QuotaExceeded, match="Fair-usage file limit reached today"):
            consume(user, make_firmware())

    def test_monthly_size_cap(self, user, make_firmware):
        package(
            "fairsize",
            is_default=True,
            duration_days=0,
            bandwidth_enabled=False,
            fair_monthly_size=10,
            size_unit=SizeUnit.MB,
        )
        consume(user, make_firmware(size_mb=8))

        with pytest.raises(QuotaExceeded, match="size limit reached this month"):
            consume(user, make_firmware(size_mb=3))

    def test_bought_paid_files_are_never_blocked_by_fair_usage(
        self, user, staff, make_firmware
    ):
        package(
            "fair",
            is_default=True,
            duration_days=0,
            bandwidth_enabled=False,
            fair_daily_files=1,
        )
        consume(user, make_firmware())
        paid = make_firmware("paid", "3.00")
        pay(entitlements.request_purchase(user, paid), staff)

        assert entitlements.check_quota(user, paid).allowed
        consume(user, paid, "paid")

    def test_cooldown_between_new_files(self, user, make_firmware):
        package(
            "slow",
            is_default=True,
            duration_days=0,
            bandwidth_enabled=False,
            cooldown_seconds=3600,
        )
        first = make_firmware()
        consume(user, first)

        with pytest.raises(QuotaExceeded, match="Wait"):
            consume(user, make_firmware())
        consume(user, first)

        QuotaConsumption.objects.update(consumed_at=timezone.now() - timedelta(hours=2))
        consume(user, make_firmware())


class TestConsumeQuota:
    def test_consuming_is_idempotent_per_user_and_file(
        self, user, default_tier, make_firmware
    ):
        firmware = make_firmware()

        consume(user, firmware)
        consume(user, firmware)

        assert QuotaConsumption.objects.filter(user=user).count() == 1
        assert entitlements.quota_snapshot(user).used == 1

    def test_the_same_file_counts_separately_for_each_user(
        self, user, other, default_tier, make_firmware
    ):
        firmware = make_firmware()

        consume(user, firmware)
        consume(other, firmware)

        assert QuotaConsumption.objects.count() == 2

    def test_reason_must_match_the_files_tier(self, user, default_tier, make_firmware):
        with pytest.raises(ValueError, match="does not match"):
            consume(user, make_firmware(), "paid")
        with pytest.raises(ValueError, match="Unknown"):
            consume(user, make_firmware(), "bogus")

    def test_integrity_error_from_a_racing_request_is_a_no_op(
        self, user, default_tier, make_firmware, monkeypatch
    ):
        firmware = make_firmware()
        real_exists = entitlements._already_consumed
        calls = {"n": 0}

        def racing(candidate, content_type, object_id):
            calls["n"] += 1
            if calls["n"] == 1:
                QuotaConsumption.objects.create(
                    user=candidate,
                    content_type=content_type,
                    object_id=object_id,
                    reason="free",
                )
                return False
            return real_exists(candidate, content_type, object_id)

        monkeypatch.setattr(entitlements, "_already_consumed", racing)

        consume(user, firmware)

        assert QuotaConsumption.objects.count() == 1

    def test_snapshot_binds_to_the_meter_with_least_room(self, user, make_firmware):
        package(
            "tight",
            is_default=True,
            duration_days=0,
            limit_files=10,
            fair_monthly_files=3,
        )
        consume(user, make_firmware())

        snapshot = entitlements.quota_snapshot(user)

        assert snapshot.label == "Fair use · files this month"
        assert (snapshot.used, snapshot.remaining) == (1, 2)


class TestPaidFiles:
    def test_has_purchased_follows_the_paid_order_and_refunds_revoke_it(
        self, user, staff, make_firmware
    ):
        firmware = make_firmware("paid", "7.50")
        order = entitlements.request_purchase(user, firmware)

        assert not entitlements.has_purchased(user, firmware)
        pay(order, staff)
        assert entitlements.has_purchased(user, firmware)

        services.refund_order(order.pk, staff_user=staff, reason="chargeback")
        assert not entitlements.has_purchased(user, firmware)

    def test_purchases_belong_to_one_user_and_one_file(
        self, user, other, staff, make_firmware
    ):
        bought = make_firmware("paid", "7.50")
        other_file = make_firmware("paid", "7.50")
        pay(entitlements.request_purchase(user, bought), staff)

        assert entitlements.has_purchased(user, bought)
        assert not entitlements.has_purchased(other, bought)
        assert not entitlements.has_purchased(user, other_file)

    def test_paid_consumption_needs_a_purchase_and_is_never_blocked(
        self, user, staff, default_tier, make_firmware
    ):
        for _ in range(2):
            consume(user, make_firmware())
        paid = make_firmware("paid", "3.00")
        with pytest.raises(NotEntitled):
            consume(user, paid, "paid")

        pay(entitlements.request_purchase(user, paid), staff)
        assert entitlements.check_quota(user, paid).allowed
        consume(user, paid, "paid")

        assert QuotaConsumption.objects.get(object_id=paid.pk).reason == "paid"

    def test_redownloading_a_paid_file_never_charges_again(
        self, user, staff, default_tier, make_firmware
    ):
        paid = make_firmware("paid", "3.00")
        pay(entitlements.request_purchase(user, paid), staff)

        for _ in range(3):
            consume(user, paid, "paid")

        assert QuotaConsumption.objects.filter(user=user).count() == 1

    def test_purchased_unserved_lists_only_bought_files_not_yet_served(
        self, user, staff, default_tier, make_firmware
    ):
        waiting = make_firmware("paid", "3.00")
        served = make_firmware("paid", "4.00")
        pending = make_firmware("paid", "5.00")
        pay(entitlements.request_purchase(user, waiting), staff)
        pay(entitlements.request_purchase(user, served), staff)
        entitlements.request_purchase(user, pending)
        consume(user, served, "paid")

        files = entitlements.purchased_unserved(user)

        assert [file.label for file in files] == [waiting.original_file_name]
        assert files[0].price == Decimal("3.00")
        assert "/download/" in files[0].download_url


class TestRequestPurchase:
    def test_creates_one_pending_order_with_the_approved_price_snapshot(
        self, user, make_firmware
    ):
        firmware = make_firmware("paid", "7.50")

        order = entitlements.request_purchase(user, firmware)

        assert (order.status, order.total, order.user) == (
            Order.Status.PENDING,
            Decimal("7.50"),
            user,
        )
        item = order.items.get()
        assert item.is_file and item.package is None
        assert item.object_id == firmware.pk
        assert (item.unit_price, item.line_total) == (Decimal("7.50"), Decimal("7.50"))
        assert item.description == firmware.original_file_name

    def test_the_package_discount_applies_to_paid_files(self, user, make_firmware):
        tier = package(
            "discount", applies_to_paid=True, paid_discount_percent=20, limit_files=5
        )
        services.activate_package(user, tier)
        firmware = make_firmware("paid", "10.00")

        assert entitlements.price_for(user, firmware) == Decimal("8.00")
        assert entitlements.request_purchase(user, firmware).total == Decimal("8.00")

    def test_price_snapshot_survives_a_later_price_change(
        self, user, staff, make_firmware
    ):
        firmware = make_firmware("paid", "7.50")
        order = entitlements.request_purchase(user, firmware)
        type(firmware).objects.filter(pk=firmware.pk).update(
            approved_price=Decimal("99.00")
        )

        pay(order, staff)

        order.refresh_from_db()
        assert order.total == Decimal("7.50")

    def test_a_second_request_returns_the_open_order(self, user, make_firmware):
        firmware = make_firmware("paid", "7.50")

        first = entitlements.request_purchase(user, firmware)
        second = entitlements.request_purchase(user, firmware)

        assert first.pk == second.pk
        assert OrderItem.objects.count() == 1

    @pytest.mark.parametrize(
        ("tier", "price", "message"),
        [
            ("free", None, "not sold"),
            ("featured", None, "not sold"),
            ("paid", None, "no approved price"),
        ],
    )
    def test_rejects_files_that_cannot_be_bought(
        self, user, make_firmware, tier, price, message
    ):
        firmware = make_firmware(tier, price)

        with pytest.raises(PurchaseError, match=message):
            entitlements.request_purchase(user, firmware)
        assert not Order.objects.exists()

    def test_rejects_unavailable_own_and_already_owned_files(
        self, user, staff, other, make_firmware
    ):
        hidden = make_firmware("paid", "5.00", is_verified=False)
        inactive = make_firmware("paid", "5.00", is_active=False)
        own = make_firmware("paid", "5.00", uploader=user)
        bought = make_firmware("paid", "5.00")
        pay(entitlements.request_purchase(user, bought), staff)

        for firmware, message in (
            (hidden, "not available"),
            (inactive, "not available"),
            (own, "You uploaded this file"),
            (bought, "already own"),
        ):
            with pytest.raises(PurchaseError, match=message):
                entitlements.request_purchase(user, firmware)
        assert Order.objects.count() == 1

    def test_anonymous_visitors_cannot_purchase(self, make_firmware):
        with pytest.raises(PurchaseError, match="Sign in"):
            entitlements.request_purchase(
                AnonymousUser(), make_firmware("paid", "5.00")
            )

    def test_order_lines_are_exactly_one_of_package_file_or_topup(
        self, user, pro_tier, make_firmware
    ):
        order = entitlements.request_purchase(user, make_firmware("paid", "5.00"))

        with pytest.raises(IntegrityError), transaction.atomic():
            OrderItem.objects.create(
                order=order, unit_price=Decimal(1), line_total=Decimal(1)
            )
        with pytest.raises(IntegrityError), transaction.atomic():
            OrderItem.objects.create(
                order=order,
                package=pro_tier,
                topup_credits=5,
                unit_price=Decimal(1),
                line_total=Decimal(1),
            )


class TestRefunds:
    def test_refund_state_machine(self, user, staff, make_firmware):
        pending = entitlements.request_purchase(user, make_firmware("paid", "5.00"))
        with pytest.raises(services.OrderError):
            services.refund_order(pending.pk, staff_user=staff)

        pay(pending, staff)
        refunded = services.refund_order(pending.pk, staff_user=staff, reason="dup")
        again = services.refund_order(pending.pk, staff_user=staff)

        assert refunded.status == again.status == Order.Status.REFUNDED
        assert "dup" in Order.objects.get(pk=pending.pk).notes
        with pytest.raises(services.OrderError):
            services.mark_order_paid(pending.pk, staff_user=staff)
        with pytest.raises(services.OrderError):
            services.cancel_order(pending.pk, staff_user=staff)

    def test_refunding_a_package_order_cancels_the_package(self, user, staff, pro_tier):
        order = services.create_order(user, pro_tier)
        pay(order, staff)
        assert entitlements.active_package(user) is not None

        services.refund_order(order.pk, staff_user=staff)

        assert entitlements.active_package(user) is None
        assert (
            UserPackage.objects.get(order=order).status == UserPackage.Status.CANCELLED
        )


class TestOrderEvents:
    @pytest.fixture
    def seen(self, django_capture_on_commit_callbacks):
        events: list[tuple[str, dict]] = []

        def record(name):
            def handler(payload):
                events.append((name, payload))

            event_bus.subscribe(name, handler)
            return handler

        handlers = {
            name: record(name)
            for name in (services.ORDER_PAID_EVENT, services.ORDER_REFUNDED_EVENT)
        }
        yield events
        for name, handler in handlers.items():
            event_bus.unsubscribe(name, handler)

    def test_paying_and_refunding_publish_file_lines_after_commit(
        self, user, staff, make_firmware, seen, django_capture_on_commit_callbacks
    ):
        firmware = make_firmware("paid", "9.99")
        order = entitlements.request_purchase(user, firmware)

        with django_capture_on_commit_callbacks(execute=True):
            pay(order, staff)
        with django_capture_on_commit_callbacks(execute=True):
            services.refund_order(order.pk, staff_user=staff)

        (paid_name, paid), (refunded_name, refunded) = seen
        assert paid_name == "shop.order_paid"
        assert refunded_name == "shop.order_refunded"
        assert paid == refunded
        assert paid["order_id"] == order.pk and paid["user_id"] == user.pk
        assert paid["files"] == [
            {
                "content_type": "firmwares.officialfirmware",
                "object_id": str(firmware.pk),
                "amount": "9.99",
                "credits": 0,
            }
        ]

    def test_package_orders_publish_no_file_lines_and_repaying_is_silent(
        self, user, staff, pro_tier, seen, django_capture_on_commit_callbacks
    ):
        order = services.create_order(user, pro_tier)

        with django_capture_on_commit_callbacks(execute=True):
            pay(order, staff)
            pay(order, staff)

        assert len(seen) == 1
        assert seen[0][1]["files"] == []
