"""Paying for packages and paid files with wallet credits (through a fake wallet API)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from apps.core.events.bus import event_bus
from apps.shop import api, credits, entitlements, services, spend_sinks, wallet_gateway
from apps.shop.models import Order, OrderItem, PackageTier, UserPackage

pytestmark = pytest.mark.django_db


class WalletError(Exception):
    """Base of the fake wallet's other failures (frozen wallet, disabled sink)."""


class InsufficientCredits(Exception):
    def __init__(self, needed: int, available: int) -> None:
        super().__init__("insufficient")
        self.needed = needed
        self.available = available


@dataclass
class FakeSink:
    key: str
    label: str
    app: str
    default_credits: int = 0
    derived: bool = False


class FakeWallet:
    """In-memory stand-in for ``apps.wallet.api`` with the published contract."""

    InsufficientCredits = InsufficientCredits
    WalletError = WalletError
    SpendSink = FakeSink

    def __init__(self) -> None:
        self.balances: dict[int, int] = {}
        self.ledger: dict[str, tuple[int, int, str]] = {}
        self.sinks: list[FakeSink] = []
        self.refunds: list[tuple[str, str]] = []

    def register_spend_sink(self, sink: FakeSink) -> None:
        self.sinks.append(sink)

    def spend(
        self, user, sink_key, *, key, reference="", quantity=1, price_override=None
    ):
        if key in self.ledger:
            return
        available = self.balances.get(user.pk, 0)
        if available < price_override:
            raise InsufficientCredits(price_override, available)
        self.balances[user.pk] = available - price_override
        self.ledger[key] = (user.pk, price_override, sink_key)

    def refund(self, entry_key, *, reason):
        self.refunds.append((entry_key, reason))
        user_id, amount, _ = self.ledger[entry_key]
        self.balances[user_id] = self.balances.get(user_id, 0) + amount

    def balance(self, user):
        return self.balances.get(user.pk, 0)

    def to_credits(self, amount):
        return int(amount * 100)

    def earn_url(self):
        return "/wallet/earn/"


@pytest.fixture
def wallet(monkeypatch) -> FakeWallet:
    fake = FakeWallet()
    monkeypatch.setattr(wallet_gateway, "_api", lambda: fake)
    return fake


@pytest.fixture
def gold(db) -> PackageTier:
    return PackageTier.objects.create(
        name="Gold",
        slug="gold",
        price=Decimal("20.00"),
        credit_price_override=500,
        duration_days=90,
        limit_files=100,
        applies_to_featured=True,
    )


def package_credits_url(package: PackageTier) -> str:
    return reverse("shop:package_credits", kwargs={"slug": package.slug})


def file_credits_url(firmware) -> str:
    return reverse(
        "shop:file_credits",
        kwargs={"firmware_type": "official", "firmware_id": firmware.pk},
    )


class TestGateway:
    def test_credits_are_unavailable_without_the_contract(self):
        assert not wallet_gateway.available()
        with pytest.raises(wallet_gateway.CreditsUnavailable):
            wallet_gateway.balance(object())
        assert wallet_gateway.register_sink("x", "X") is False

    def test_sinks_are_declared_to_the_wallet(self, wallet):
        for key, label in spend_sinks.SINKS:
            assert wallet_gateway.register_sink(key, label)

        assert [(sink.key, sink.app) for sink in wallet.sinks] == [
            ("package.purchase", "shop"),
            ("file.purchase", "shop"),
        ]


class TestPackagePurchase:
    def test_spending_credits_activates_the_package_instantly(self, user, wallet, gold):
        wallet.balances[user.pk] = 800

        order = credits.pay_package_with_credits(user, gold)

        key = f"shop:package:{user.pk}:{gold.pk}:1"
        assert wallet.ledger[key] == (user.pk, 500, "package.purchase")
        assert wallet.balances[user.pk] == 300
        assert (order.status, order.payment_method, order.credits_spent) == (
            Order.Status.PAID,
            Order.PaymentMethod.CREDITS,
            500,
        )
        assert order.payment_reference == key
        held = UserPackage.objects.get(user=user)
        assert (held.package, held.order) == (gold, order)
        assert entitlements.active_package(user) == held

    def test_each_purchase_gets_its_own_idempotency_key(self, user, wallet, gold):
        wallet.balances[user.pk] = 2000

        credits.pay_package_with_credits(user, gold)
        credits.pay_package_with_credits(user, gold)

        assert sorted(wallet.ledger) == [
            f"shop:package:{user.pk}:{gold.pk}:1",
            f"shop:package:{user.pk}:{gold.pk}:2",
        ]

    def test_insufficient_credits_leave_no_order_and_no_package(
        self, user, wallet, gold
    ):
        wallet.balances[user.pk] = 100

        with pytest.raises(wallet_gateway.InsufficientCreditsError) as caught:
            credits.pay_package_with_credits(user, gold)

        assert (caught.value.needed, caught.value.available) == (500, 100)
        assert not Order.objects.exists()
        assert not UserPackage.objects.exists()

    def test_packages_without_a_credit_price_or_unavailable_are_rejected(
        self, user, wallet, pro_tier, gold, default_tier
    ):
        wallet.balances[user.pk] = 10_000
        pro_tier.allow_credits = False
        pro_tier.save()
        for package in (pro_tier, default_tier):
            with pytest.raises(entitlements.PurchaseError):
                credits.pay_package_with_credits(user, package)
        services.set_package_active(gold, active=False)
        with pytest.raises(entitlements.PurchaseError, match="not available"):
            credits.pay_package_with_credits(user, gold)
        assert not wallet.ledger

    def test_an_unavailable_wallet_changes_nothing(self, user, gold):
        with pytest.raises(wallet_gateway.CreditsUnavailable):
            credits.pay_package_with_credits(user, gold)

        assert not Order.objects.exists()

    def test_other_wallet_failures_are_reported_and_roll_back(
        self, user, wallet, gold, monkeypatch
    ):
        def frozen(*args, **kwargs):
            raise WalletError("Wallet is frozen.")

        monkeypatch.setattr(wallet, "spend", frozen)

        with pytest.raises(wallet_gateway.CreditsUnavailable, match="frozen"):
            credits.pay_package_with_credits(user, gold)

        assert not Order.objects.exists()

    def test_refunding_a_credit_order_reverses_the_spend(
        self, user, staff, wallet, gold
    ):
        wallet.balances[user.pk] = 500
        order = credits.pay_package_with_credits(user, gold)

        services.refund_order(order.pk, staff_user=staff, reason="mistake")

        assert wallet.refunds == [(order.payment_reference, "mistake")]
        assert wallet.balances[user.pk] == 500
        assert entitlements.active_package(user) is None

    def test_refund_fails_cleanly_when_the_wallet_is_gone(
        self, user, staff, monkeypatch, wallet, gold
    ):
        wallet.balances[user.pk] = 500
        order = credits.pay_package_with_credits(user, gold)
        monkeypatch.setattr(wallet_gateway, "_api", lambda: None)

        with pytest.raises(services.OrderError, match="not available"):
            services.refund_order(order.pk, staff_user=staff)

        order.refresh_from_db()
        assert order.status == Order.Status.PAID


class TestFilePurchase:
    def test_the_price_is_converted_unless_the_file_has_its_own_credit_price(
        self, user, wallet, make_firmware
    ):
        converted = make_firmware("paid", "4.50")
        explicit = make_firmware("paid", "4.50")
        explicit.credit_price = 40

        assert credits.credits_for_file(user, converted) == 450
        assert credits.credits_for_file(user, explicit) == 40

    def test_the_package_discount_reduces_the_credit_price(
        self, user, wallet, make_firmware
    ):
        tier = PackageTier.objects.create(
            name="Deal",
            slug="deal",
            price=Decimal(5),
            limit_files=5,
            applies_to_paid=True,
            paid_discount_percent=50,
        )
        services.activate_package(user, tier)
        explicit = make_firmware("paid", "4.00")
        explicit.credit_price = 41

        assert credits.credits_for_file(user, make_firmware("paid", "4.00")) == 200
        assert credits.credits_for_file(user, explicit) == 21

    def test_credits_are_zero_without_a_wallet(self, user, make_firmware):
        assert credits.credits_for_file(user, make_firmware("paid", "4.50")) == 0

    def test_paying_unlocks_the_file_immediately_and_cancels_a_pending_cash_order(
        self, user, wallet, make_firmware
    ):
        firmware = make_firmware("paid", "4.50")
        cash = entitlements.request_purchase(user, firmware)
        wallet.balances[user.pk] = 450

        order = credits.pay_file_with_credits(user, firmware)

        assert entitlements.has_purchased(user, firmware)
        assert wallet.ledger[f"shop:file:{user.pk}:{firmware.pk}:1"][1:] == (
            450,
            "file.purchase",
        )
        assert (order.total, order.credits_spent) == (Decimal("4.50"), 450)
        cash.refresh_from_db()
        assert cash.status == Order.Status.CANCELLED
        entitlements.consume_quota(user, firmware, reason="paid")

    def test_rejections_do_not_spend(self, user, wallet, make_firmware):
        wallet.balances[user.pk] = 10_000
        own = make_firmware("paid", "5.00", uploader=user)
        bought = make_firmware("paid", "5.00")
        credits.pay_file_with_credits(user, bought)
        for firmware, message in (
            (make_firmware("free"), "not sold"),
            (make_firmware("paid", None), "no approved price"),
            (make_firmware("paid", "5.00", is_verified=False), "not available"),
            (own, "You uploaded"),
            (bought, "already own"),
        ):
            with pytest.raises(entitlements.PurchaseError, match=message):
                credits.pay_file_with_credits(user, firmware)

        assert len(wallet.ledger) == 1

    def test_insufficient_credits_leave_the_file_locked(
        self, user, wallet, make_firmware
    ):
        firmware = make_firmware("paid", "4.50")

        with pytest.raises(wallet_gateway.InsufficientCreditsError):
            credits.pay_file_with_credits(user, firmware)

        assert not entitlements.has_purchased(user, firmware)
        assert not Order.objects.exists()

    def test_the_event_tells_the_marketplace_how_many_credits_were_paid(
        self, user, staff, wallet, make_firmware, django_capture_on_commit_callbacks
    ):
        firmware = make_firmware("paid", "4.50")
        wallet.balances[user.pk] = 450
        seen: list[dict] = []
        event_bus.subscribe(services.ORDER_PAID_EVENT, seen.append)
        try:
            with django_capture_on_commit_callbacks(execute=True):
                credits.pay_file_with_credits(user, firmware)
        finally:
            event_bus.unsubscribe(services.ORDER_PAID_EVENT, seen.append)

        assert seen[0]["files"][0]["credits"] == 450
        assert seen[0]["files"][0]["amount"] == "4.50"

    def test_credits_of_a_multi_file_order_are_split_not_repeated(
        self, user, make_firmware
    ):
        from django.contrib.contenttypes.models import ContentType

        first = make_firmware("paid", "3.00")
        second = make_firmware("paid", "2.00")
        order = Order.objects.create(
            user=user,
            total=Decimal("5.00"),
            payment_method=Order.PaymentMethod.CREDITS,
            credits_spent=101,
        )
        content_type = ContentType.objects.get_for_model(first)
        for firmware, price in ((first, "3.00"), (second, "2.00")):
            OrderItem.objects.create(
                order=order,
                content_type=content_type,
                object_id=firmware.pk,
                description=firmware.original_file_name,
                unit_price=Decimal(price),
                line_total=Decimal(price),
            )

        lines = services._file_lines(order)

        assert [line["credits"] for line in lines] == [60, 41]
        assert sum(line["credits"] for line in lines) == 101


class TestViews:
    def test_the_confirm_page_shows_price_balance_and_a_csrf_form(
        self, client, user, wallet, gold
    ):
        wallet.balances[user.pk] = 800
        client.force_login(user)

        text = client.get(package_credits_url(gold)).content.decode()

        assert "500 credits" in text and "800 credits" in text
        assert "csrfmiddlewaretoken" in text
        assert f'action="{package_credits_url(gold)}"' in text

    def test_hx_requests_get_only_the_dialog_body(self, client, user, wallet, gold):
        client.force_login(user)

        response = client.get(package_credits_url(gold), HTTP_HX_REQUEST="true")

        assert "<html" not in response.content.decode()
        assert "Buy Gold with credits" in response.content.decode()

    def test_not_enough_credits_links_to_ways_to_earn(self, client, user, wallet, gold):
        wallet.balances[user.pk] = 120
        client.force_login(user)

        text = client.get(package_credits_url(gold)).content.decode()

        assert "You need 380 more credits" in text
        assert 'href="/wallet/earn/"' in text
        assert "Pay 500 credits" not in text

    def test_posting_pays_and_redirects_to_the_order(self, client, user, wallet, gold):
        wallet.balances[user.pk] = 800
        client.force_login(user)

        response = client.post(package_credits_url(gold))

        order = Order.objects.get()
        assert response.status_code == 302
        assert response["Location"] == reverse("shop:order_detail", args=[order.pk])
        assert UserPackage.objects.filter(user=user, package=gold).exists()
        followed = client.get(response["Location"]).content.decode()
        assert "Paid with 500 credits" in followed

    def test_posting_without_enough_credits_rerenders_the_dialog(
        self, client, user, wallet, gold
    ):
        wallet.balances[user.pk] = 10
        client.force_login(user)

        response = client.post(package_credits_url(gold))

        assert response.status_code == 402
        assert "500 credits needed, 10 available" in response.content.decode()
        assert not Order.objects.exists()

    def test_csrf_login_and_verification_are_enforced(
        self, user, make_user, wallet, gold
    ):
        wallet.balances[user.pk] = 800
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(user)
        assert csrf_client.post(package_credits_url(gold)).status_code == 403
        assert Client().post(package_credits_url(gold)).status_code == 302

        unverified = Client()
        unverified.force_login(make_user("unverified", verified=False))
        unverified.post(package_credits_url(gold))
        assert not Order.objects.exists()

    def test_without_a_wallet_credits_are_unavailable(self, client, user, gold):
        client.force_login(user)

        response = client.get(package_credits_url(gold), follow=True)

        assert "Credits are not available" in response.content.decode()

    def test_packages_without_a_credit_price_redirect_back(
        self, client, user, wallet, pro_tier
    ):
        client.force_login(user)
        pro_tier.allow_credits = False
        pro_tier.save()

        response = client.get(package_credits_url(pro_tier), follow=True)

        assert "cannot be bought with credits" in response.content.decode()

    def test_the_package_page_offers_credits_when_the_wallet_exists(
        self, client, user, wallet, gold
    ):
        wallet.balances[user.pk] = 800
        client.force_login(user)

        text = client.get(
            reverse("shop:package_detail", kwargs={"slug": "gold"})
        ).content.decode()

        assert "Pay 500 credits" in text
        assert "Your balance: 800 credits" in text

    def test_pricing_shows_credit_prices_next_to_currency_prices(
        self, client, wallet, gold
    ):
        text = client.get(reverse("shop:pricing")).content.decode()

        assert 'class="plan-card__credits">&middot; 500 credits</span>' in text
        assert "Price in credits" in text

    def test_file_purchase_flow(self, client, user, wallet, make_firmware):
        firmware = make_firmware("paid", "4.50")
        wallet.balances[user.pk] = 450
        client.force_login(user)

        page = client.get(file_credits_url(firmware)).content.decode()
        response = client.post(file_credits_url(firmware))

        assert "Buy this file with credits" in page and "450 credits" in page
        assert response.status_code == 302
        assert entitlements.has_purchased(user, firmware)

    def test_the_firmware_page_offers_paying_with_credits(
        self, client, user, wallet, default_tier, make_firmware
    ):
        firmware = make_firmware("paid", "4.50")
        client.force_login(user)
        url = reverse(
            "firmwares:firmware_detail",
            kwargs={"firmware_type": "official", "firmware_id": str(firmware.pk)},
        )

        text = client.get(url).content.decode()

        assert "Pay 450 credits" in text
        assert file_credits_url(firmware) in text


class TestTopups:
    def test_a_topup_order_is_an_offline_order_that_publishes_the_credits(
        self, user, staff, django_capture_on_commit_callbacks
    ):
        order_id = api.create_credit_topup_order(
            user, credit_amount=1000, price=Decimal("10.00"), reference="wallet-1"
        )
        seen: list[dict] = []
        event_bus.subscribe(services.ORDER_PAID_EVENT, seen.append)
        try:
            with django_capture_on_commit_callbacks(execute=True):
                services.mark_order_paid(order_id, staff_user=staff)
        finally:
            event_bus.unsubscribe(services.ORDER_PAID_EVENT, seen.append)

        item = OrderItem.objects.get(order_id=order_id)
        assert (item.is_topup, item.topup_credits) == (True, 1000)
        assert seen[0]["topup_credits"] == 1000 and seen[0]["files"] == []
        assert not UserPackage.objects.exists()

    def test_topups_need_positive_credits_and_price(self, user):
        with pytest.raises(ValueError):
            api.create_credit_topup_order(user, credit_amount=0, price=Decimal(1))
        with pytest.raises(ValueError):
            api.create_credit_topup_order(user, credit_amount=5, price=Decimal(0))
