from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from enum import StrEnum
from typing import Any
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.events.bus import event_bus
from apps.firmwares.models import (
    AccessTier,
    Brand,
    Model,
    OfficialFirmware,
    Variant,
)
from apps.marketplace import earnings, wallet_bridge
from apps.marketplace.models import SellerEarning, SellerProfile
from apps.marketplace.services import MarketplaceError
from apps.wallet import services as wallet_services
from apps.wallet.models import PayoutRequest

PASSWORD = "Pass-12345!"
RATE = 100
CONTENT_TYPE = "firmwares.officialfirmware"


def make_user(name: str, **extra: Any):
    return get_user_model().objects.create_user(
        username=name,
        email=f"{name}@example.com",
        password=PASSWORD,
        email_verified_at=timezone.now(),
        **extra,
    )


class FakeWallet:
    """In-memory stand-in honouring the documented ``apps.wallet.services`` contract."""

    InsufficientCredits = type("InsufficientCredits", (Exception,), {})
    PayoutError = type("PayoutError", (Exception,), {})

    class LedgerReason(StrEnum):
        EARNING = "earning"
        REFUND = "refund"

    def __init__(self) -> None:
        self.entries: dict[str, int] = {}
        self.balances: defaultdict[int, int] = defaultdict(int)
        self.payouts: list[tuple[int, int, str, str]] = []

    def to_credits(self, amount: Decimal) -> int:
        return int(amount * RATE)

    def balance(self, user: Any) -> int:
        return self.balances[user.pk]

    def withdrawable_balance(self, user: Any) -> int:
        return self.balances[user.pk]

    def credit(self, user: Any, amount: int, *, key: str, **_: Any) -> None:
        if key in self.entries:
            return
        self.entries[key] = amount
        self.balances[user.pk] += amount

    def debit(self, user: Any, amount: int, *, key: str, **_: Any) -> None:
        if key in self.entries:
            return
        if self.balances[user.pk] < amount:
            raise self.InsufficientCredits("Not enough credits.")
        self.entries[key] = -amount
        self.balances[user.pk] -= amount

    def request_payout(
        self, user: Any, amount: int, *, method: str, destination: str
    ) -> None:
        if self.balances[user.pk] < amount:
            raise self.PayoutError("You can only withdraw what you have earned.")
        self.payouts.append((user.pk, amount, method, destination))


class EarningsTestCase(TestCase):
    def setUp(self):
        self.owner = make_user("earn_owner")
        self.buyer = make_user("earn_buyer")
        self.seller = SellerProfile.objects.create(
            user=self.owner, display_name="Earn Seller", verified=True
        )
        brand = Brand.objects.create(name="Earn Brand", slug="earn-brand")
        model = Model.objects.create(brand=brand, name="E1", slug="e1")
        variant = Variant.objects.create(model=model, name="Global", slug="global")
        self.firmware = self.make_firmware(brand, model, variant, "paid.zip")
        self.free_firmware = self.make_firmware(
            brand, model, variant, "free.zip", tier=AccessTier.FREE
        )
        self.wallet = FakeWallet()

    def make_firmware(self, brand, model, variant, name, tier=AccessTier.PAID):
        return OfficialFirmware.objects.create(
            original_file_name=name,
            stored_file_path=f"/firmware/{name}",
            brand=brand,
            model=model,
            variant=variant,
            uploader=self.owner,
            is_verified=True,
            access_tier=tier,
            approved_price=Decimal("5.00") if tier == AccessTier.PAID else None,
        )

    def with_wallet(self):
        stack = [
            patch.object(wallet_bridge, "wallet_enabled", return_value=True),
            patch.object(wallet_bridge, "_services", return_value=self.wallet),
        ]
        for item in stack:
            item.start()
            self.addCleanup(item.stop)

    def payload(self, firmware=None, **line: Any) -> dict[str, Any]:
        firmware = firmware or self.firmware
        return {
            "order_id": 7,
            "user_id": self.buyer.pk,
            "files": [
                {
                    "content_type": CONTENT_TYPE,
                    "object_id": str(firmware.pk),
                    "amount": "5.00",
                    "credits": 500,
                    **line,
                }
            ],
        }


class RecordSaleTests(EarningsTestCase):
    def test_sale_credits_the_seller_once_and_replays_are_harmless(self):
        self.with_wallet()

        first = earnings.record_sale(
            self.seller, self.firmware, "5.00", 7, paid_credits=500
        )
        again = earnings.record_sale(
            self.seller, self.firmware, "5.00", 7, paid_credits=500
        )

        self.assertEqual(first.pk, again.pk)
        self.assertEqual(first.status, SellerEarning.Status.CREDITED)
        self.assertEqual(self.wallet.balance(self.owner), 500)
        self.assertEqual(SellerEarning.objects.count(), 1)

    @override_settings(MARKETPLACE_COMMISSION_PERCENT=10)
    def test_commission_is_kept_by_the_platform(self):
        self.with_wallet()

        earning = earnings.record_sale(
            self.seller, self.firmware, "5.00", 7, paid_credits=555
        )

        self.assertEqual(earning.commission_credits, 56)
        self.assertEqual(earning.net_credits, 499)
        self.assertEqual(self.wallet.balance(self.owner), 499)

    def test_other_sellers_files_and_bad_amounts_are_refused(self):
        other = SellerProfile.objects.create(
            user=self.buyer, display_name="Other", verified=True
        )

        with self.assertRaises(MarketplaceError):
            earnings.record_sale(other, self.firmware, "5.00", 7, paid_credits=500)
        with self.assertRaises(MarketplaceError):
            earnings.record_sale(self.seller, self.firmware, "0", 7, paid_credits=0)
        self.assertFalse(SellerEarning.objects.exists())

    def test_without_a_wallet_the_sale_waits_and_is_credited_later(self):
        with patch.object(wallet_bridge, "wallet_enabled", return_value=False):
            earning = earnings.record_sale(
                self.seller, self.firmware, "5.00", 7, paid_credits=0
            )
            self.assertEqual(earnings.settle_awaiting_earnings(), 0)
        self.assertEqual(earning.status, SellerEarning.Status.AWAITING_WALLET)

        self.with_wallet()
        self.assertEqual(earnings.settle_awaiting_earnings(), 1)
        self.assertEqual(earnings.settle_awaiting_earnings(), 0)

        earning.refresh_from_db()
        self.assertEqual(earning.status, SellerEarning.Status.CREDITED)
        self.assertEqual(earning.net_credits, 5 * RATE)
        self.assertEqual(self.wallet.balance(self.owner), 5 * RATE)

    def test_wallet_refusal_keeps_the_earning_awaiting(self):
        self.with_wallet()
        with patch.object(
            self.wallet, "credit", side_effect=FakeWallet.InsufficientCredits("frozen")
        ):
            earning = earnings.record_sale(
                self.seller, self.firmware, "5.00", 7, paid_credits=500
            )

        self.assertEqual(earning.status, SellerEarning.Status.AWAITING_WALLET)
        self.assertEqual(self.wallet.balance(self.owner), 0)


class ShopEventTests(EarningsTestCase):
    def test_marketplace_subscribes_to_the_shop_events(self):
        self.assertGreaterEqual(
            event_bus.get_handler_count(earnings.ORDER_PAID_EVENT), 1
        )
        self.assertGreaterEqual(
            event_bus.get_handler_count(earnings.ORDER_REFUNDED_EVENT), 1
        )
        earnings.connect()
        before = event_bus.get_handler_count(earnings.ORDER_PAID_EVENT)
        earnings.connect()
        self.assertEqual(event_bus.get_handler_count(earnings.ORDER_PAID_EVENT), before)

    def test_paid_order_event_credits_the_uploader_once(self):
        self.with_wallet()

        event_bus.publish(earnings.ORDER_PAID_EVENT, self.payload())
        event_bus.publish(earnings.ORDER_PAID_EVENT, self.payload())

        self.assertEqual(self.wallet.balance(self.owner), 500)
        self.assertEqual(SellerEarning.objects.get().seller, self.seller)

    def test_events_that_must_not_pay_are_ignored(self):
        self.with_wallet()
        malformed = {"order_id": 9, "files": [{"content_type": "nope.nope"}, "x"]}

        event_bus.publish(earnings.ORDER_PAID_EVENT, self.payload(self.free_firmware))
        event_bus.publish(
            earnings.ORDER_PAID_EVENT, {**self.payload(), "user_id": self.owner.pk}
        )
        event_bus.publish(earnings.ORDER_PAID_EVENT, malformed)
        event_bus.publish(earnings.ORDER_PAID_EVENT, "not a payload")
        event_bus.publish(earnings.ORDER_PAID_EVENT, {"files": []})
        SellerProfile.objects.filter(pk=self.seller.pk).update(verified=False)
        event_bus.publish(earnings.ORDER_PAID_EVENT, self.payload())

        self.assertFalse(SellerEarning.objects.exists())
        self.assertEqual(self.wallet.balance(self.owner), 0)

    def test_refund_event_takes_the_credits_back_once(self):
        self.with_wallet()
        event_bus.publish(earnings.ORDER_PAID_EVENT, self.payload())

        event_bus.publish(earnings.ORDER_REFUNDED_EVENT, self.payload())
        event_bus.publish(earnings.ORDER_REFUNDED_EVENT, self.payload())

        self.assertEqual(
            SellerEarning.objects.get().status, SellerEarning.Status.REVERSED
        )
        self.assertEqual(self.wallet.balance(self.owner), 0)

    def test_refund_after_the_credits_were_spent_is_flagged_for_staff(self):
        self.with_wallet()
        event_bus.publish(earnings.ORDER_PAID_EVENT, self.payload())
        self.wallet.balances[self.owner.pk] = 0

        event_bus.publish(earnings.ORDER_REFUNDED_EVENT, self.payload())

        self.assertEqual(
            SellerEarning.objects.get().status, SellerEarning.Status.REVERSAL_FAILED
        )
        self.assertEqual(earnings.earnings_summary(self.seller).needs_attention, 1)

    def test_refund_of_an_uncredited_sale_just_reverses_it(self):
        with patch.object(wallet_bridge, "wallet_enabled", return_value=False):
            event_bus.publish(earnings.ORDER_PAID_EVENT, self.payload())
            event_bus.publish(earnings.ORDER_REFUNDED_EVENT, self.payload())

        self.assertEqual(
            SellerEarning.objects.get().status, SellerEarning.Status.REVERSED
        )


class PayoutAndDashboardTests(EarningsTestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.force_login(self.owner)
        self.url = reverse("marketplace:payout_request")

    def data(self, **extra: Any) -> dict[str, Any]:
        return {"credits": 200, "method": "bank", "destination": "IBAN 123", **extra}

    def test_dashboard_shows_balances_sales_and_the_payout_dialog(self):
        self.with_wallet()
        event_bus.publish(earnings.ORDER_PAID_EVENT, self.payload())

        response = self.client.get(reverse("marketplace:dashboard"))

        self.assertContains(response, "500 credits")
        self.assertContains(response, "1 sale")
        self.assertContains(response, 'id="payout-dialog"')
        self.assertContains(response, "Recent sales")
        self.assertContains(response, "Earn Brand E1 Global")

    def test_payout_request_goes_to_the_wallet(self):
        self.with_wallet()
        self.wallet.balances[self.owner.pk] = 500

        response = self.client.post(self.url, self.data())

        self.assertRedirects(response, reverse("marketplace:dashboard"))
        self.assertEqual(
            self.wallet.payouts, [(self.owner.pk, 200, "bank", "IBAN 123")]
        )

    def test_invalid_and_refused_requests_create_nothing(self):
        self.with_wallet()
        self.wallet.balances[self.owner.pk] = 100

        for data, text in (
            (self.data(credits=0), "greater than or equal to 1"),
            (self.data(method="cash-in-hand"), "Select a valid choice"),
            (self.data(destination=""), "required"),
            (self.data(credits=200), "only withdraw what you have earned"),
        ):
            response = self.client.post(self.url, data)
            self.assertContains(response, text)
        self.assertEqual(self.wallet.payouts, [])

    def test_without_a_wallet_the_page_is_an_honest_empty_state(self):
        with patch.object(wallet_bridge, "wallet_enabled", return_value=False):
            page = self.client.get(self.url)
            post = self.client.post(self.url, self.data())

        self.assertContains(page, "Payouts are not enabled yet")
        self.assertContains(post, "Payouts are not enabled yet")

    def test_only_approved_sellers_can_request_a_payout(self):
        self.with_wallet()
        outsider = Client()
        outsider.force_login(self.buyer)

        self.assertEqual(Client().get(self.url).status_code, 302)
        redirected = outsider.post(self.url, self.data())

        self.assertRedirects(
            redirected,
            reverse("marketplace:become_seller"),
            fetch_redirect_response=False,
        )
        self.assertEqual(self.wallet.payouts, [])

    def test_payout_post_requires_csrf(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.owner)

        self.assertEqual(strict.post(self.url, self.data()).status_code, 403)

    def test_payout_requests_are_rate_limited(self):
        self.with_wallet()
        with patch("apps.marketplace.views.allow_action", return_value=False):
            response = self.client.post(self.url, self.data())

        self.assertEqual(response.status_code, 429)
        self.assertEqual(self.wallet.payouts, [])

    def test_earnings_admin_lists_and_loads_for_staff(self):
        earnings.record_sale(self.seller, self.firmware, "5.00", 7, paid_credits=0)
        admin = make_user("earn_admin", is_staff=True, is_superuser=True)
        client = Client()
        client.force_login(admin)

        listing = client.get(reverse("admin:marketplace_sellerearning_changelist"))
        seller_page = client.get(
            reverse("admin:marketplace_sellerprofile_change", args=[self.seller.pk])
        )

        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, "Earn Seller")
        self.assertEqual(seller_page.status_code, 200)


class RealWalletTests(EarningsTestCase):
    """The bridge against the installed ``apps.wallet`` contract, without fakes."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.force_login(self.owner)

    def wallet_balances(self) -> wallet_bridge.WalletBalances:
        result = wallet_bridge.balances(self.owner)
        assert result is not None
        return result

    def test_sale_is_credited_withdrawable_and_refunds_take_it_back(self):
        event_bus.publish(earnings.ORDER_PAID_EVENT, self.payload(credits=1000))
        event_bus.publish(earnings.ORDER_PAID_EVENT, self.payload(credits=1000))

        balances = self.wallet_balances()
        self.assertEqual((balances.balance, balances.withdrawable), (1000, 1000))
        self.assertEqual(
            SellerEarning.objects.get().status, SellerEarning.Status.CREDITED
        )

        event_bus.publish(earnings.ORDER_REFUNDED_EVENT, self.payload(credits=1000))
        self.assertEqual(self.wallet_balances().balance, 0)
        self.assertEqual(
            SellerEarning.objects.get().status, SellerEarning.Status.REVERSED
        )

    def test_cash_orders_are_converted_with_the_wallet_rate(self):
        event_bus.publish(earnings.ORDER_PAID_EVENT, self.payload(credits=0))

        earning = SellerEarning.objects.get()
        self.assertEqual(earning.net_credits, wallet_services.to_credits(Decimal(5)))
        self.assertEqual(self.wallet_balances().balance, earning.net_credits)

    def test_payout_request_holds_the_credits_and_reaches_staff(self):
        event_bus.publish(earnings.ORDER_PAID_EVENT, self.payload(credits=1000))

        response = self.client.post(
            reverse("marketplace:payout_request"),
            {"credits": 600, "method": "bank", "destination": "IBAN 123"},
        )

        self.assertRedirects(response, reverse("marketplace:dashboard"))
        payout = PayoutRequest.objects.get()
        self.assertEqual((payout.destination, payout.credits), ("IBAN 123", 600))
        self.assertEqual(self.wallet_balances().withdrawable, 400)

    def test_payout_below_the_minimum_is_refused_with_the_wallet_message(self):
        event_bus.publish(earnings.ORDER_PAID_EVENT, self.payload(credits=1000))

        response = self.client.post(
            reverse("marketplace:payout_request"),
            {"credits": 5, "method": "bank", "destination": "IBAN 123"},
        )

        self.assertContains(response, "minimum payout")
        self.assertFalse(PayoutRequest.objects.exists())
