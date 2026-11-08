"""Seller earnings from purchased paid files, credited through the wallet bridge.

Shop publishes ``shop.order_paid`` / ``shop.order_refunded`` on the event bus
(after commit) with one entry per paid-file order line::

    {"order_id": 7, "user_id": 3,
     "files": [{"content_type": "firmwares.officialfirmware",
                "object_id": "<uuid>", "amount": "9.99", "credits": 120}]}

``credits`` is what the buyer paid in credits (omitted or 0 for an offline cash
order); the seller is then credited ``wallet.to_credits(amount)`` instead.
Marketplace never edits shop: this module only reacts to those events.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Final

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.urls import reverse

from apps.core.events.bus import event_bus
from apps.firmwares.models import AccessTier
from apps.users.services.notifications import send_notification

from . import wallet_bridge
from .models import SellerEarning, SellerProfile
from .services import MarketplaceError

logger = logging.getLogger(__name__)

ORDER_PAID_EVENT: Final[str] = "shop.order_paid"
ORDER_REFUNDED_EVENT: Final[str] = "shop.order_refunded"
CENT: Final[Decimal] = Decimal("0.01")
RECENT_EARNINGS: Final[int] = 10
PAYOUT_METHODS: Final[tuple[tuple[str, str], ...]] = (
    ("bank", "Bank transfer"),
    ("paypal", "PayPal"),
    ("crypto", "Crypto wallet"),
)
WALLET_ERRORS: Final = (
    wallet_bridge.WalletRejectedError,
    wallet_bridge.WalletUnavailableError,
)


def commission_percent() -> Decimal:
    """Platform share kept from each sale; ``MARKETPLACE_COMMISSION_PERCENT``."""
    raw = Decimal(str(getattr(settings, "MARKETPLACE_COMMISSION_PERCENT", 0)))
    return min(max(raw, Decimal(0)), Decimal(100))


def firmware_ref(firmware: Any) -> str:
    return f"{firmware._meta.label_lower}:{firmware.pk}"


def _money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def _firmware_title(firmware: Any) -> str:
    parts = (
        getattr(getattr(firmware, "brand", None), "name", ""),
        getattr(getattr(firmware, "model", None), "name", ""),
        getattr(getattr(firmware, "variant", None), "name", ""),
    )
    title = " ".join(part for part in parts if part)
    return (title or getattr(firmware, "original_file_name", "") or "Firmware")[:200]


def _notify_earning(earning: SellerEarning) -> None:
    send_notification(
        earning.seller.user,
        "You earned credits",
        f"{earning.net_credits} credits from a sale of {earning.firmware_title}.",
        url=reverse("marketplace:dashboard"),
        icon="store",
    )


def _split(gross_credits: int) -> tuple[int, int]:
    """``(commission, net)`` credits for a sale, rounding the commission half up."""
    commission = int(
        (Decimal(gross_credits) * commission_percent() / 100).quantize(
            Decimal(1), rounding=ROUND_HALF_UP
        )
    )
    return commission, gross_credits - commission


def _credit(earning: SellerEarning) -> None:
    """Move one awaiting earning into the wallet; leave it awaiting on refusal."""
    if earning.status != SellerEarning.Status.AWAITING_WALLET:
        return
    if not wallet_bridge.wallet_enabled():
        return
    try:
        if earning.gross_credits == 0:
            earning.gross_credits = wallet_bridge.to_credits(earning.gross_amount)
            earning.commission_credits, earning.net_credits = _split(
                earning.gross_credits
            )
        if earning.net_credits > 0:
            wallet_bridge.credit_earning(
                earning.seller.user,
                earning.net_credits,
                key=earning.credit_key,
                reference=f"order:{earning.order_ref}",
                note=f"Sale of {earning.firmware_title}",
            )
    except WALLET_ERRORS:
        logger.warning(
            "Wallet refused earning %s; it stays awaiting.", earning.pk, exc_info=True
        )
        return
    earning.status = SellerEarning.Status.CREDITED
    earning.save()
    if earning.net_credits > 0:
        transaction.on_commit(lambda: _notify_earning(earning))


@transaction.atomic
def record_sale(
    seller: SellerProfile,
    firmware: Any,
    amount: Any,
    order: Any,
    *,
    paid_credits: int = 0,
) -> SellerEarning:
    """Record (and credit when the wallet exists) one purchased paid file.

    Idempotent per ``(order, firmware)``: replays return the existing earning.
    """
    gross = _money(amount)
    if gross <= 0:
        raise MarketplaceError("A sale needs a positive amount.")
    if getattr(firmware, "uploader_id", None) != seller.user_id:
        raise MarketplaceError("This file was not uploaded by that seller.")
    commission, net = _split(max(paid_credits, 0))
    earning, _ = SellerEarning.objects.select_related("seller__user").get_or_create(
        order_ref=str(getattr(order, "pk", order)),
        firmware_ref=firmware_ref(firmware),
        defaults={
            "seller": seller,
            "firmware_title": _firmware_title(firmware),
            "gross_amount": gross,
            "gross_credits": max(paid_credits, 0),
            "commission_credits": commission,
            "net_credits": net,
        },
    )
    _credit(earning)
    return earning


@transaction.atomic
def settle_awaiting_earnings() -> int:
    """Credit earnings recorded before the wallet was enabled. Idempotent."""
    if not wallet_bridge.wallet_enabled():
        return 0
    done = 0
    pending = SellerEarning.objects.select_for_update().filter(
        status=SellerEarning.Status.AWAITING_WALLET
    )
    for earning in pending.select_related("seller__user"):
        _credit(earning)
        done += earning.status == SellerEarning.Status.CREDITED
    return done


@transaction.atomic
def reverse_sale(order: Any, firmware: Any) -> SellerEarning | None:
    """Take a refunded sale back; a refusal is flagged for staff, never hidden."""
    earning = (
        SellerEarning.objects.select_for_update()
        .select_related("seller__user")
        .filter(
            order_ref=str(getattr(order, "pk", order)),
            firmware_ref=firmware_ref(firmware),
        )
        .first()
    )
    if earning is None or earning.status == SellerEarning.Status.REVERSED:
        return earning
    if (
        earning.status == SellerEarning.Status.AWAITING_WALLET
        or earning.net_credits <= 0
    ):
        earning.status = SellerEarning.Status.REVERSED
    else:
        try:
            wallet_bridge.reverse_earning(
                earning.seller.user,
                earning.net_credits,
                key=earning.reversal_key,
                reference=f"order:{earning.order_ref}",
                note=f"Refund of {earning.firmware_title}",
            )
        except WALLET_ERRORS:
            logger.warning("Could not reverse earning %s.", earning.pk, exc_info=True)
            earning.status = SellerEarning.Status.REVERSAL_FAILED
        else:
            earning.status = SellerEarning.Status.REVERSED
    earning.save(update_fields=["status", "updated_at"])
    return earning


@dataclass(frozen=True)
class SaleLine:
    firmware: Any
    amount: Decimal
    credits: int


def _lines(data: dict[str, Any]) -> list[SaleLine]:
    """Valid file lines of an event payload; malformed lines are logged and skipped."""
    lines: list[SaleLine] = []
    for line in data.get("files") or []:
        try:
            model = apps.get_model(str(line["content_type"]))
            firmware = model.objects.filter(pk=line["object_id"]).first()
            amount = _money(line["amount"])
            line_credits = int(line.get("credits") or 0)
        except (
            KeyError,
            TypeError,
            AttributeError,
            LookupError,
            ValueError,
            ValidationError,
            InvalidOperation,
        ):
            logger.warning("Ignoring malformed shop file line: %r", line)
            continue
        if firmware is not None:
            lines.append(SaleLine(firmware, amount, line_credits))
    return lines


def _seller_of(firmware: Any) -> SellerProfile | None:
    return (
        SellerProfile.objects.select_related("user")
        .filter(
            user_id=getattr(firmware, "uploader_id", None),
            is_active=True,
            verified=True,
        )
        .first()
    )


def on_order_paid(data: Any) -> None:
    if not isinstance(data, dict) or data.get("order_id") is None:
        return
    for line in _lines(data):
        if getattr(line.firmware, "access_tier", "") != AccessTier.PAID:
            continue
        seller = _seller_of(line.firmware)
        if seller is None or seller.user_id == data.get("user_id"):
            continue
        try:
            record_sale(
                seller,
                line.firmware,
                line.amount,
                data["order_id"],
                paid_credits=line.credits,
            )
        except MarketplaceError:
            logger.warning(
                "Sale not recorded for %s.", firmware_ref(line.firmware), exc_info=True
            )


def on_order_refunded(data: Any) -> None:
    if not isinstance(data, dict) or data.get("order_id") is None:
        return
    for line in _lines(data):
        reverse_sale(data["order_id"], line.firmware)


def connect() -> None:
    """Subscribe to shop events once (called from ``MarketplaceConfig.ready``)."""
    for event, handler in (
        (ORDER_PAID_EVENT, on_order_paid),
        (ORDER_REFUNDED_EVENT, on_order_refunded),
    ):
        event_bus.unsubscribe(event, handler)
        event_bus.subscribe(event, handler)


@dataclass(frozen=True)
class EarningsSummary:
    wallet_enabled: bool
    credited: int
    awaiting_sales: int
    sales: int
    balance: int | None
    withdrawable: int | None
    needs_attention: int
    recent: list[SellerEarning]


def earnings_summary(seller: SellerProfile) -> EarningsSummary:
    rows = seller.earnings.exclude(status=SellerEarning.Status.REVERSED)
    credited = rows.filter(status=SellerEarning.Status.CREDITED).aggregate(
        total=Sum("net_credits")
    )["total"]
    balances = wallet_bridge.balances(seller.user)
    return EarningsSummary(
        wallet_enabled=balances is not None,
        credited=int(credited or 0),
        awaiting_sales=rows.filter(status=SellerEarning.Status.AWAITING_WALLET).count(),
        sales=rows.count(),
        balance=balances.balance if balances else None,
        withdrawable=balances.withdrawable if balances else None,
        needs_attention=seller.earnings.filter(
            status=SellerEarning.Status.REVERSAL_FAILED
        ).count(),
        recent=list(seller.earnings.all()[:RECENT_EARNINGS]),
    )


def request_payout(
    seller: SellerProfile, amount: int, *, method: str, destination: str
) -> None:
    """Ask staff to pay out earned credits (reviewed in the wallet admin)."""
    if method not in {key for key, _ in PAYOUT_METHODS}:
        raise MarketplaceError("Choose a payout method.")
    destination = destination.strip()
    if not destination:
        raise MarketplaceError("Say where the payout should be sent.")
    if amount <= 0:
        raise MarketplaceError("Enter a number of credits above zero.")
    try:
        wallet_bridge.request_payout(
            seller.user, amount, method=method, destination=destination[:255]
        )
    except wallet_bridge.WalletUnavailableError as exc:
        raise MarketplaceError("Payouts are not enabled yet.") from exc
    except wallet_bridge.WalletRejectedError as exc:
        raise MarketplaceError(str(exc)) from exc
