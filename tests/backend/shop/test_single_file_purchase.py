"""Buying one featured or paid file with credits: per-file, lifetime, quota-free, no double charge."""

from __future__ import annotations

from decimal import Decimal
from importlib import import_module

import pytest
from django.test import Client
from django.urls import reverse

from apps.firmwares.access import check_download_access
from apps.shop import credits, entitlements, services, wallet_gateway
from apps.shop.models import Order, PackageTier, QuotaConsumption, ShopSettings
from apps.wallet import api as wallet
from apps.wallet.models import LedgerEntry, WalletSettings
from apps.wallet.services import sync_registry

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def real_wallet(monkeypatch) -> None:
    monkeypatch.setattr(
        wallet_gateway, "_api", lambda: import_module("apps.wallet.api")
    )
    sync_registry()
    cfg = WalletSettings.get_solo()
    cfg.credits_per_currency = 10
    cfg.save()


@pytest.fixture
def free_tier(default_tier: PackageTier) -> PackageTier:
    PackageTier.objects.filter(pk=default_tier.pk).update(
        limit_files=2, fair_monthly_files=0
    )
    default_tier.refresh_from_db()
    return default_tier


@pytest.fixture
def make_firmware(make_firmware):
    """Featured files carry no price of their own: a price argument sets the shop-wide one."""

    def build(tier: str = "free", price: str | None = None, **extra):
        if tier == "featured":
            if price is not None:
                settings_row = ShopSettings.get_solo()
                settings_row.featured_file_price = Decimal(price)
                settings_row.save()
            price = None
        return make_firmware(tier, price, **extra)

    return build


@pytest.fixture
def funded(user):
    wallet.credit(user, 500, reason=wallet.LedgerReason.ADJUSTMENT, key="seed-file")
    return user


def serve(user, firmware) -> bool:
    decision = entitlements.check_quota(user, firmware)
    if decision.allowed:
        entitlements.consume_quota(user, firmware, reason=firmware.access_tier)
    return decision.allowed


class TestFeaturedFileWithoutAPackage:
    def test_it_is_locked_until_bought_then_unlocked_for_that_file_only(
        self, funded, free_tier, make_firmware
    ):
        featured = make_firmware("featured", "3.00")
        other_featured = make_firmware("featured", "3.00")
        assert not entitlements.check_quota(funded, featured).allowed

        order = credits.pay_file_with_credits(funded, featured)

        assert order.status == Order.Status.PAID
        assert order.credits_spent == 30
        assert wallet.balance(funded) == 470
        assert entitlements.check_quota(funded, featured).allowed
        assert not entitlements.check_quota(funded, other_featured).allowed

    def test_the_price_is_dollars_times_the_rate_without_the_paid_discount(
        self, funded, free_tier, make_firmware, pro_tier
    ):
        featured = make_firmware("featured", "2.50")

        assert credits.credits_for_file(funded, featured) == 25

        PackageTier.objects.filter(pk=pro_tier.pk).update(
            applies_to_featured=False, applies_to_paid=True, paid_discount_percent=50
        )
        services.activate_package(funded, pro_tier)
        assert credits.credits_for_file(funded, featured) == 25

    def test_it_bypasses_an_exhausted_free_allowance_without_consuming_it(
        self, funded, free_tier, make_firmware
    ):
        first, second, blocked = (make_firmware() for _ in range(3))
        assert serve(funded, first) and serve(funded, second)
        assert not serve(funded, blocked)
        featured = make_firmware("featured", "1.00")
        credits.pay_file_with_credits(funded, featured)
        before = entitlements.quota_snapshot(funded).used

        assert serve(funded, featured) is True

        assert entitlements.quota_snapshot(funded).used == before
        row = QuotaConsumption.objects.get(user=funded, object_id=featured.pk)
        assert row.reason == QuotaConsumption.Reason.PURCHASED
        assert not serve(funded, blocked)

    def test_it_can_be_downloaded_again_forever_without_a_new_charge(
        self, funded, free_tier, make_firmware
    ):
        featured = make_firmware("featured", "1.00")
        credits.pay_file_with_credits(funded, featured)

        assert all(serve(funded, featured) for _ in range(3))

        assert QuotaConsumption.objects.filter(user=funded).count() == 1
        assert LedgerEntry.objects.filter(event_key="file.purchase").count() == 1

    def test_the_download_gate_follows_the_purchase(
        self, funded, free_tier, make_firmware
    ):
        featured = make_firmware("featured", "1.00")
        locked = check_download_access(funded, featured)
        assert not locked.allowed and locked.requires == "package"
        assert "buy this file on its own" in locked.reason

        credits.pay_file_with_credits(funded, featured)

        assert check_download_access(funded, featured).allowed

    def test_it_appears_in_my_files_before_and_after_the_first_download(
        self, funded, free_tier, make_firmware
    ):
        featured = make_firmware("featured", "1.00")
        credits.pay_file_with_credits(funded, featured)
        client = Client()
        client.force_login(funded)

        waiting = entitlements.purchased_unserved(funded)
        page = client.get(reverse("library:my_files"))

        assert [file.label for file in waiting] == [featured.original_file_name]
        assert page.status_code == 200
        assert featured.original_file_name in page.content.decode()
        serve(funded, featured)
        assert entitlements.purchased_unserved(funded) == []


class TestNoDoubleCharge:
    def test_a_package_holder_uses_the_package_and_is_never_charged(
        self, funded, free_tier, pro_tier, make_firmware
    ):
        services.activate_package(funded, pro_tier)
        featured = make_firmware("featured", "3.00")

        assert credits.purchase_block_reason(funded, featured)
        assert credits.credits_for_file(funded, featured) == 0
        with pytest.raises(entitlements.PurchaseError, match="package already"):
            credits.pay_file_with_credits(funded, featured)

        assert wallet.balance(funded) == 500
        assert serve(funded, featured) is True
        assert (
            QuotaConsumption.objects.get(user=funded).reason
            == QuotaConsumption.Reason.FEATURED
        )

    def test_buying_twice_is_refused_and_charges_once(
        self, funded, free_tier, make_firmware
    ):
        featured = make_firmware("featured", "3.00")
        credits.pay_file_with_credits(funded, featured)

        with pytest.raises(entitlements.PurchaseError, match="already own"):
            credits.pay_file_with_credits(funded, featured)

        assert wallet.balance(funded) == 470
        assert Order.objects.filter(user=funded).count() == 1

    def test_a_file_bought_before_a_package_stays_quota_free(
        self, funded, free_tier, pro_tier, make_firmware
    ):
        featured = make_firmware("featured", "3.00")
        credits.pay_file_with_credits(funded, featured)
        services.activate_package(funded, pro_tier)

        serve(funded, featured)

        assert (
            QuotaConsumption.objects.get(user=funded).reason
            == QuotaConsumption.Reason.PURCHASED
        )


class TestFailureAndRefusals:
    def test_a_failure_after_the_charge_rolls_the_charge_back(
        self, funded, free_tier, make_firmware, monkeypatch
    ):
        featured = make_firmware("featured", "3.00")

        def explode(*args, **kwargs):
            raise RuntimeError("grant failed")

        monkeypatch.setattr(services, "mark_order_paid", explode)

        with pytest.raises(RuntimeError):
            credits.pay_file_with_credits(funded, featured)

        assert wallet.balance(funded) == 500
        assert not Order.objects.exists()
        assert not LedgerEntry.objects.filter(event_key="file.purchase").exists()
        assert not entitlements.check_quota(funded, featured).allowed

    def test_not_enough_credits_buys_nothing(self, user, free_tier, make_firmware):
        wallet.credit(user, 5, reason=wallet.LedgerReason.ADJUSTMENT, key="tiny-file")
        featured = make_firmware("featured", "3.00")

        with pytest.raises(wallet_gateway.InsufficientCreditsError):
            credits.pay_file_with_credits(user, featured)

        assert wallet.balance(user) == 5
        assert not Order.objects.exists()
        assert not entitlements.check_quota(user, featured).allowed

    def test_a_featured_file_that_is_not_sold_one_by_one_cannot_be_bought(
        self, funded, free_tier, make_firmware
    ):
        featured = make_firmware("featured", "0")

        with pytest.raises(entitlements.PurchaseError, match="cannot be bought one"):
            credits.pay_file_with_credits(funded, featured)

        assert credits.credits_for_file(funded, featured) == 0
        assert not check_download_access(funded, featured).allowed
        assert "on its own" not in check_download_access(funded, featured).reason

    def test_free_files_are_not_sold(self, funded, free_tier, make_firmware):
        with pytest.raises(entitlements.PurchaseError, match="not sold"):
            credits.pay_file_with_credits(funded, make_firmware())

    def test_inactive_files_cannot_be_bought(self, funded, free_tier, make_firmware):
        featured = make_firmware("featured", "3.00", is_active=False)

        with pytest.raises(entitlements.PurchaseError, match="not available"):
            credits.pay_file_with_credits(funded, featured)

    def test_the_uploader_already_owns_their_file(
        self, funded, free_tier, make_firmware
    ):
        featured = make_firmware("featured", "3.00", uploader=funded)

        with pytest.raises(entitlements.PurchaseError, match="already yours"):
            credits.pay_file_with_credits(funded, featured)

    def test_a_refund_returns_the_credits_and_revokes_the_entitlement(
        self, funded, free_tier, make_firmware, staff
    ):
        featured = make_firmware("featured", "3.00")
        order = credits.pay_file_with_credits(funded, featured)

        services.refund_order(order.pk, staff_user=staff, reason="Mistake")

        assert wallet.balance(funded) == 500
        assert not entitlements.check_quota(funded, featured).allowed


class TestPaidFiles:
    def test_a_paid_file_is_unlocked_for_an_account_without_a_package(
        self, funded, free_tier, make_firmware
    ):
        paid = make_firmware("paid", "4.00")
        first, second, third = (make_firmware() for _ in range(3))
        serve(funded, first)
        serve(funded, second)

        credits.pay_file_with_credits(funded, paid)

        assert serve(funded, paid) is True
        assert not serve(funded, third)
        assert wallet.balance(funded) == 460


class TestPages:
    def test_the_shop_settings_page_shows_the_credits_equivalent(self, staff):
        ShopSettings.get_solo()
        client = Client()
        client.force_login(staff)

        text = client.get(
            reverse("admin:shop_shopsettings_changelist"), follow=True
        ).content.decode()

        assert "featured_file_price" in text
        assert "$1.00 · 10 credits" in text

    def test_the_firmware_page_offers_the_credit_purchase_with_both_prices(
        self, funded, free_tier, make_firmware
    ):
        featured = make_firmware("featured", "4.00")
        client = Client()
        client.force_login(funded)
        url = reverse(
            "firmwares:firmware_detail",
            kwargs={"firmware_type": "official", "firmware_id": str(featured.pk)},
        )

        text = client.get(url).content.decode()

        assert 'data-testid="buy-featured-file"' in text
        assert "Pay 40 credits" in text
        assert "$4.00 · 40 credits" in text

    def test_the_dialog_pays_and_unlocks(self, funded, free_tier, make_firmware):
        featured = make_firmware("featured", "4.00")
        client = Client()
        client.force_login(funded)
        url = reverse(
            "shop:file_credits",
            kwargs={"firmware_type": "official", "firmware_id": featured.pk},
        )

        page = client.get(url)
        done = client.post(url)

        assert page.status_code == 200 and "40" in page.content.decode()
        assert done.status_code == 302
        assert wallet.balance(funded) == 460
        assert check_download_access(funded, featured).allowed

    def test_package_holders_are_sent_back_with_the_reason(
        self, funded, free_tier, pro_tier, make_firmware
    ):
        services.activate_package(funded, pro_tier)
        featured = make_firmware("featured", "4.00")
        client = Client()
        client.force_login(funded)
        url = reverse(
            "shop:file_credits",
            kwargs={"firmware_type": "official", "firmware_id": featured.pk},
        )

        response = client.get(url, follow=True)

        assert "package already includes" in response.content.decode()
        assert wallet.balance(funded) == 500
