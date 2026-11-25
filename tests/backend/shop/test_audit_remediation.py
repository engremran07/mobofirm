"""Audit remediation: package rules, lifecycle, windows, file references, admin audit."""

from __future__ import annotations

import uuid
import zoneinfo
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from django.apps import apps
from django.contrib.admin.utils import flatten_fieldsets
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection, models
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.core.events.bus import event_bus
from apps.shop import (
    entitlements,
    file_references,
    request_cache,
    services,
    tasks,
)
from apps.shop.admin import PackageTierAdmin
from apps.shop.models import (
    Order,
    OrderItem,
    PackageQuotaOverride,
    PackageTier,
    QuotaConsumption,
    QuotaOverrideAudit,
    UserPackage,
    UserQuotaOverride,
    WindowKind,
)
from apps.shop.unlock_models import AdUnlockProgress, AdUnlockTicket

pytestmark = pytest.mark.django_db


@pytest.fixture
def gold(db) -> PackageTier:
    return PackageTier.objects.create(
        name="Gold",
        slug="gold",
        price=Decimal("20.00"),
        duration_days=90,
        limit_files=100,
        applies_to_featured=True,
    )


@pytest.fixture
def root(staff) -> Client:
    client = Client(headers={"HX-Request": "true"})
    client.force_login(staff)
    return client


def pay(order: Order, staff) -> Order:
    return services.mark_order_paid(order.pk, staff_user=staff, reference="ref")


def buy(user, tier: PackageTier, staff) -> Order:
    return pay(services.create_order(user, tier), staff)


class TestPackageRules:
    """PackageTier.clean: cross-field rules the admin form reports field by field."""

    @staticmethod
    def build(**fields) -> PackageTier:
        values = {
            "name": "Rule",
            "slug": "rule",
            "price": Decimal(5),
            "limit_files": 10,
            **fields,
        }
        return PackageTier(**values)

    def test_a_consistent_package_is_valid(self):
        self.build(
            extra_daily_files=5, unlock_by_ads_free=True, slot_reset_credits=10
        ).full_clean()

    @pytest.mark.parametrize(
        ("fields", "field"),
        [
            ({"bandwidth_enabled": False, "extra_daily_files": 5}, "extra_daily_files"),
            ({"bandwidth_enabled": False, "extra_daily_size": 5}, "extra_daily_size"),
            ({"unlock_by_ads_free": True, "ad_free": True}, "unlock_by_ads_free"),
            (
                {
                    "unlock_by_ads_free": True,
                    "applies_to_free": False,
                    "applies_to_featured": True,
                },
                "unlock_by_ads_free",
            ),
            (
                {"unlock_by_ads_featured": True, "ad_free": True},
                "unlock_by_ads_featured",
            ),
            (
                {"unlock_by_ads_featured": True, "applies_to_featured": True},
                "unlock_by_ads_featured",
            ),
            (
                {"slot_reset_credits": 10, "limit_devices": False},
                "slot_reset_credits",
            ),
            ({"credit_price_override": 0}, "credit_price_override"),
        ],
    )
    def test_inconsistent_combinations_name_the_field(self, fields, field):
        with pytest.raises(ValidationError) as caught:
            self.build(**fields).full_clean()

        assert field in caught.value.message_dict

    def test_every_seeded_package_passes_validation(self):
        call_command("seed_packages", verbosity=0)

        packages = list(PackageTier.objects.all())
        assert {package.slug for package in packages} >= {"free", "silver", "gold"}
        for package in packages:
            package.full_clean()


class TestPackageBuilder:
    def test_every_editable_field_is_in_the_builder_form(self):
        editable = {
            field.name
            for field in PackageTier._meta.concrete_fields
            if field.editable and not field.primary_key
        }

        in_form = set(flatten_fieldsets(PackageTierAdmin.fieldsets))

        assert editable <= in_form, sorted(editable - in_form)

    def test_the_preview_reads_every_limit_a_holder_meets(self, root):
        package = PackageTier.objects.create(
            name="Wide",
            slug="wide",
            price=Decimal(9),
            limit_files=30,
            fair_daily_files=20,
            fair_monthly_files=300,
            max_concurrent_downloads=2,
            cooldown_seconds=15,
            applies_to_featured_blog=True,
            featured_blog_limit=3,
            slot_reset_credits=40,
            description="Plenty for repair shops",
            is_popular=True,
        )

        text = root.get(
            reverse("admin:shop_packagetier_change", args=[package.pk])
        ).content.decode()

        for expected in (
            "20 files per day, 300 files per month",
            "2 downloads at a time",
            "15 seconds between new files",
            "3 articles per month",
            "40 credits",
            "Plenty for repair shops",
            "Popular choice",
        ):
            assert expected in text

    def test_pricing_and_detail_render_the_fair_use_and_blog_rules(self, client):
        package = PackageTier.objects.create(
            name="Wide",
            slug="wide",
            price=Decimal(9),
            limit_files=30,
            fair_daily_files=20,
            fair_monthly_files=300,
            max_concurrent_downloads=2,
            cooldown_seconds=15,
            applies_to_featured_blog=True,
            featured_blog_limit=3,
        )

        pricing = client.get(reverse("shop:pricing")).content.decode()
        detail = client.get(
            reverse("shop:package_detail", kwargs={"slug": package.slug})
        ).content.decode()

        for text in (pricing, detail):
            assert "20 files per day, 300 files per month" in text
            assert "2 downloads at a time" in text
            assert "15 seconds between new files" in text
            assert "3 articles per month" in text

    def test_the_default_package_cannot_be_deleted(self, root, default_tier):
        url = reverse("admin:shop_packagetier_delete", args=[default_tier.pk])

        assert root.get(url).status_code == 403
        assert PackageTier.objects.filter(pk=default_tier.pk).exists()

    def test_a_package_with_holders_is_protected_from_deletion(
        self, root, staff, user, pro_tier
    ):
        buy(user, pro_tier, staff)

        response = root.post(
            reverse("admin:shop_packagetier_delete", args=[pro_tier.pk]),
            {"post": "yes"},
        )

        assert PackageTier.objects.filter(pk=pro_tier.pk).exists()
        assert response.status_code in {200, 302, 403}
        assert UserPackage.objects.filter(package=pro_tier).exists()


class TestPackageLifecycle:
    def test_provisioning_is_keyed_on_the_order(self, user, pro_tier):
        order = services.create_order(user, pro_tier)

        first = services.activate_package(user, pro_tier, order=order)
        again = services.activate_package(user, pro_tier, order=order)

        assert first.pk == again.pk
        assert UserPackage.objects.filter(order=order).count() == 1

    def test_refunding_a_renewal_gives_back_the_period_it_extended(
        self, user, staff, pro_tier
    ):
        first_order = buy(user, pro_tier, staff)
        first = UserPackage.objects.get(order=first_order)
        second_order = buy(user, pro_tier, staff)
        first.refresh_from_db()
        assert first.status == UserPackage.Status.EXPIRED

        services.refund_order(second_order.pk, staff_user=staff, reason="duplicate")

        first.refresh_from_db()
        assert first.status == UserPackage.Status.ACTIVE
        assert entitlements.active_package(user) == first

    def test_refunding_an_upgrade_restores_the_previous_package(
        self, user, staff, pro_tier, gold
    ):
        pro_order = buy(user, pro_tier, staff)
        gold_order = buy(user, gold, staff)

        services.refund_order(gold_order.pk, staff_user=staff)

        held = entitlements.active_package(user)
        assert held is not None and held.order_id == pro_order.pk
        assert UserPackage.objects.get(order=gold_order).status == "cancelled"

    def test_a_period_that_ended_by_time_is_not_brought_back(
        self, user, staff, pro_tier
    ):
        first_order = buy(user, pro_tier, staff)
        second_order = buy(user, pro_tier, staff)
        UserPackage.objects.filter(order=first_order).update(
            expires_at=timezone.now() - timedelta(days=1)
        )

        services.refund_order(second_order.pk, staff_user=staff)

        assert entitlements.active_package(user) is None
        assert (
            UserPackage.objects.get(order=first_order).status
            == UserPackage.Status.EXPIRED
        )

    def test_a_staff_cancelled_period_is_not_brought_back(
        self, user, staff, pro_tier, gold
    ):
        pro_order = buy(user, pro_tier, staff)
        services.cancel_package(UserPackage.objects.get(order=pro_order))
        gold_order = buy(user, gold, staff)

        services.refund_order(gold_order.pk, staff_user=staff)

        assert entitlements.active_package(user) is None

    def test_refunding_twice_changes_nothing_more(self, user, staff, pro_tier, gold):
        buy(user, pro_tier, staff)
        gold_order = buy(user, gold, staff)

        services.refund_order(gold_order.pk, staff_user=staff)
        services.refund_order(gold_order.pk, staff_user=staff)

        assert (
            UserPackage.objects.filter(status="active", is_baseline=False).count() == 1
        )

    def test_the_expiry_task_also_ends_stale_unlock_tickets(self, user, make_firmware):
        firmware = make_firmware("free")
        content_type, object_id = entitlements.file_ref(firmware)
        stale = AdUnlockTicket.objects.create(
            user=user,
            content_type=content_type,
            object_id=object_id,
            kind="free",
            status=AdUnlockTicket.Status.READY,
            required=3,
            progress=3,
            expires_at=timezone.now() - timedelta(minutes=1),
        )

        result = tasks.expire_packages()

        stale.refresh_from_db()
        assert result == {"packages": 0, "unlock_tickets": 1}
        assert stale.status == AdUnlockTicket.Status.EXPIRED
        assert tasks.expire_packages() == {"packages": 0, "unlock_tickets": 0}

    def test_the_sweep_declares_time_limits(self):
        assert tasks.expire_packages.soft_time_limit == tasks.SWEEP_SOFT_TIME_LIMIT
        assert tasks.expire_packages.time_limit == tasks.SWEEP_TIME_LIMIT
        assert tasks.SWEEP_SOFT_TIME_LIMIT < tasks.SWEEP_TIME_LIMIT


class TestWindows:
    def test_day_and_month_bounds_follow_the_local_calendar_across_dst(self):
        berlin = zoneinfo.ZoneInfo("Europe/Berlin")
        utc = zoneinfo.ZoneInfo("UTC")

        def real_seconds(bounds: tuple[datetime, datetime]) -> float:
            start, end = bounds
            return (end.astimezone(utc) - start.astimezone(utc)).total_seconds()

        with timezone.override(berlin):
            spring = datetime(2027, 3, 28, 12, 0, tzinfo=berlin)
            start, end = entitlements._day_bounds(spring)
            assert real_seconds((start, end)) == 23 * 3600
            assert (start.hour, end.hour) == (0, 0)
            month_start, month_end = entitlements._month_bounds(spring)
            assert (month_start.day, month_start.month) == (1, 3)
            assert (month_end.day, month_end.month) == (1, 4)
            autumn = datetime(2027, 10, 31, 12, 0, tzinfo=berlin)
            assert real_seconds(entitlements._day_bounds(autumn)) == 25 * 3600

    def test_december_rolls_over_to_january(self):
        moment = datetime(2027, 12, 31, 23, 59, tzinfo=zoneinfo.ZoneInfo("UTC"))

        with timezone.override(zoneinfo.ZoneInfo("UTC")):
            start, end = entitlements._month_bounds(moment)

        assert (start.month, end.year, end.month, end.day) == (12, 2028, 1, 1)

    def test_a_file_served_just_before_local_midnight_counts_in_the_old_day(
        self, user, default_tier, make_firmware
    ):
        tokyo = zoneinfo.ZoneInfo("Asia/Tokyo")
        PackageTier.objects.filter(pk=default_tier.pk).update(
            limit_files=1, fair_monthly_files=0
        )
        firmware = make_firmware("free")
        content_type, object_id = entitlements.file_ref(firmware)
        with timezone.override(tokyo):
            now = timezone.localtime()
            midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
            QuotaConsumption.objects.create(
                user=user,
                content_type=content_type,
                object_id=object_id,
                reason="free",
                consumed_at=midnight - timedelta(seconds=1),
            )
            other = make_firmware("free")

            assert entitlements.check_quota(user, other).allowed

    def test_the_package_window_starts_at_the_renewal_not_the_first_purchase(
        self, user, staff, make_firmware
    ):
        package = PackageTier.objects.create(
            name="Total",
            slug="total",
            price=Decimal(5),
            duration_days=30,
            window=WindowKind.PACKAGE,
            limit_files=1,
        )
        buy(user, package, staff)
        served = make_firmware("free")
        entitlements.consume_quota(user, served, reason="free")
        assert not entitlements.check_quota(user, make_firmware("free")).allowed

        buy(user, package, staff)

        assert entitlements.check_quota(user, make_firmware("free")).allowed


class TestResolverCost:
    @staticmethod
    def shop_default_reads(queries: CaptureQueriesContext) -> int:
        return sum(
            ' FROM "shop_packagetier"' in q["sql"] for q in queries.captured_queries
        )

    def test_a_package_holder_never_reads_the_default_package(
        self, user, staff, pro_tier, default_tier
    ):
        buy(user, pro_tier, staff)

        with CaptureQueriesContext(connection) as queries:
            entitlements.effective_entitlements(user)

        # package (+tier join), package override, account override; the rest are
        # the registered modifiers of other apps and the seller check.
        shop_queries = [
            q for q in queries.captured_queries if ' FROM "shop_' in q["sql"]
        ]
        assert len(shop_queries) == 3
        assert self.shop_default_reads(queries) == 0

    def test_an_account_without_a_package_reads_the_default_package_once(
        self, user, default_tier
    ):
        with CaptureQueriesContext(connection) as queries:
            entitlements.effective_entitlements(user)

        assert self.shop_default_reads(queries) == 1

    def test_the_usage_meters_cost_one_query_per_window_pair(self, user, default_tier):
        PackageTier.objects.filter(pk=default_tier.pk).update(
            limit_files=5, limit_size=2, fair_daily_files=3, fair_monthly_files=9
        )
        token = request_cache.begin()
        try:
            entitlements.effective_entitlements(user)
            with CaptureQueriesContext(connection) as queries:
                entitlements.quota_windows(user)
        finally:
            request_cache.end(token)

        usage = [
            q for q in queries.captured_queries if "shop_quotaconsumption" in q["sql"]
        ]
        assert len(queries) == len(usage) == 2

    def test_the_pricing_page_reads_the_packages_in_one_query(self, client):
        for number in range(6):
            PackageTier.objects.create(
                name=f"Tier {number}",
                slug=f"tier-{number}",
                price=Decimal(number + 1),
                limit_files=10,
            )

        with CaptureQueriesContext(connection) as queries:
            assert client.get(reverse("shop:pricing")).status_code == 200

        shop_reads = [q for q in queries.captured_queries if ' FROM "shop_' in q["sql"]]
        assert len(shop_reads) <= 3


class TestFileReferences:
    def test_every_generic_reference_has_a_declared_disposition(self):
        holders = {
            model.__name__
            for model in apps.get_app_config("shop").get_models()
            if any(
                isinstance(field, models.ForeignKey)
                and field.related_model is ContentType
                for field in model._meta.concrete_fields
            )
        }

        assert holders == set(file_references.FILE_REFERENCES)

    def test_deleting_a_file_purges_its_unlocks_and_quota_but_keeps_the_order(
        self, user, staff, make_firmware
    ):
        firmware = make_firmware("paid", "4.00")
        keep = make_firmware("free")
        content_type, object_id = entitlements.file_ref(firmware)
        ticket = AdUnlockTicket.objects.create(
            user=user,
            content_type=content_type,
            object_id=object_id,
            kind="free",
            required=3,
            progress=1,
        )
        AdUnlockProgress.objects.create(ticket=ticket, view_id=991, number=1)
        QuotaConsumption.objects.create(
            user=user, content_type=content_type, object_id=object_id, reason="paid"
        )
        kept_type, kept_id = entitlements.file_ref(keep)
        QuotaConsumption.objects.create(
            user=user, content_type=kept_type, object_id=kept_id, reason="free"
        )
        order = Order.objects.create(user=user, total=Decimal("4.00"), status="paid")
        OrderItem.objects.create(
            order=order,
            content_type=content_type,
            object_id=object_id,
            description="fw.zip",
            unit_price=Decimal("4.00"),
            line_total=Decimal("4.00"),
        )

        firmware.delete()

        assert not AdUnlockTicket.objects.filter(object_id=object_id).exists()
        assert not AdUnlockProgress.objects.filter(view_id=991).exists()
        assert not QuotaConsumption.objects.filter(object_id=object_id).exists()
        assert QuotaConsumption.objects.filter(object_id=kept_id).exists()
        assert OrderItem.objects.filter(order=order).count() == 1
        assert entitlements.purchased_unserved(user) == []

    def test_purging_twice_is_a_no_op(self, user, make_firmware):
        firmware = make_firmware("free")
        content_type, object_id = entitlements.file_ref(firmware)
        QuotaConsumption.objects.create(
            user=user, content_type=content_type, object_id=object_id, reason="free"
        )

        first = file_references.purge_file_references(firmware)
        second = file_references.purge_file_references(firmware)

        assert first == {"unlock_rows": 0, "quota_rows": 1}
        assert second == {"unlock_rows": 0, "quota_rows": 0}

    def test_a_deleted_purchase_is_not_listed_in_the_library(self, user, make_firmware):
        firmware = make_firmware("paid", "4.00")
        content_type, object_id = entitlements.file_ref(firmware)
        order = Order.objects.create(
            user=user, total=Decimal("4.00"), status="paid", paid_at=timezone.now()
        )
        OrderItem.objects.create(
            order=order,
            content_type=content_type,
            object_id=object_id,
            description="fw.zip",
            unit_price=Decimal("4.00"),
            line_total=Decimal("4.00"),
        )
        assert [file.label for file in entitlements.purchased_unserved(user)] == [
            "fw.zip"
        ]

        OrderItem.objects.filter(order=order).update(object_id=uuid.uuid4())

        assert entitlements.purchased_unserved(user) == []


class TestOverrideAudit:
    def test_a_package_override_saved_through_the_admin_is_audited(
        self, root, staff, user, pro_tier
    ):
        held = services.activate_package(user, pro_tier)

        response = root.post(
            reverse("admin:shop_packagequotaoverride_add"),
            {
                "user_package": held.pk,
                "limit_files": "500",
                "limit_size_mb": "",
                "device_limit": "",
                "reason": "beta",
                "granted_by": "",
                "expires_at_0": "",
                "expires_at_1": "",
            },
        )

        assert response.status_code == 302
        row = QuotaOverrideAudit.objects.get(user=user)
        assert (row.action, row.actor, row.reason) == ("granted", staff, "beta")
        assert row.snapshot["scope"] == "package"
        assert row.snapshot["limit_files"] == 500

    def test_deleting_overrides_in_bulk_leaves_one_audit_row_each(
        self, root, staff, user, other, pro_tier
    ):
        first = PackageQuotaOverride.objects.create(
            user_package=services.activate_package(user, pro_tier),
            limit_files=5,
            reason="a",
        )
        second = PackageQuotaOverride.objects.create(
            user_package=services.activate_package(other, pro_tier),
            limit_files=6,
            reason="b",
        )

        root.post(
            reverse("admin:shop_packagequotaoverride_changelist"),
            {
                "action": "delete_selected",
                "_selected_action": [first.pk, second.pk],
                "post": "yes",
            },
        )

        assert not PackageQuotaOverride.objects.exists()
        assert sorted(
            QuotaOverrideAudit.objects.values_list("user_id", "action")
        ) == sorted([(user.pk, "deleted"), (other.pk, "deleted")])

    def test_bulk_deleting_account_overrides_is_audited(self, root, staff, user):
        override = UserQuotaOverride.objects.create(
            user=user, reason="Goodwill", limit_files=9, granted_by=staff
        )

        root.post(
            reverse("admin:shop_userquotaoverride_changelist"),
            {
                "action": "delete_selected",
                "_selected_action": [override.pk],
                "post": "yes",
            },
        )

        assert not UserQuotaOverride.objects.exists()
        assert list(
            QuotaOverrideAudit.objects.filter(user=user).values_list(
                "action", flat=True
            )
        ) == ["deleted"]

    def test_the_account_of_an_existing_override_is_read_only(
        self, root, staff, user, other
    ):
        override = UserQuotaOverride.objects.create(
            user=user, reason="Goodwill", limit_files=9, granted_by=staff
        )
        url = reverse("admin:shop_userquotaoverride_change", args=[override.pk])

        text = root.get(url).content.decode()

        assert 'name="user"' not in text

    def test_the_admin_lists_do_not_query_per_row(
        self, root, staff, make_user, pro_tier
    ):
        names = ("packagequotaoverride", "quotaconsumption", "order")
        file_type = ContentType.objects.get_for_model(Order)

        def add_rows(count: int) -> None:
            for _ in range(count):
                holder = make_user(f"holder{uuid.uuid4().hex[:8]}")
                held = services.activate_package(holder, pro_tier)
                PackageQuotaOverride.objects.create(
                    user_package=held, limit_files=5, granted_by=staff
                )
                services.create_order(holder, pro_tier)
                QuotaConsumption.objects.create(
                    user=holder,
                    content_type=file_type,
                    object_id=uuid.uuid4(),
                    reason="free",
                    user_package=held,
                )

        def count_queries(name: str) -> int:
            with CaptureQueriesContext(connection) as queries:
                response = root.get(reverse(f"admin:shop_{name}_changelist"))
            assert response.status_code == 200
            return len(queries)

        add_rows(2)
        count_queries("order")  # warm one-off caches (content types, settings)
        few = {name: count_queries(name) for name in names}
        add_rows(5)
        many = {name: count_queries(name) for name in names}

        assert many == few


class TestUserPackageAdminCreation:
    def test_a_package_created_by_staff_announces_its_activation(
        self, root, user, pro_tier, django_capture_on_commit_callbacks
    ):
        seen: list[dict] = []
        event_bus.subscribe(services.PACKAGE_ACTIVATED_EVENT, seen.append)
        now = timezone.now()

        with django_capture_on_commit_callbacks(execute=True):
            response = root.post(
                reverse("admin:shop_userpackage_add"),
                {
                    "user": user.pk,
                    "package": pro_tier.pk,
                    "status": "active",
                    "source": "staff",
                    "started_at_0": now.strftime("%Y-%m-%d"),
                    "started_at_1": now.strftime("%H:%M:%S"),
                    "payment_reference": "",
                    "quota_overrides-TOTAL_FORMS": "0",
                    "quota_overrides-INITIAL_FORMS": "0",
                    "quota_overrides-MIN_NUM_FORMS": "0",
                    "quota_overrides-MAX_NUM_FORMS": "1000",
                    "_save": "Save",
                },
            )

        assert response.status_code == 302
        created = UserPackage.objects.get(user=user, is_baseline=False)
        assert [event["user_package_id"] for event in seen] == [created.pk]
