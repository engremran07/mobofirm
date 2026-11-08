"""Download access tiers: who may be served a file and what has been served.

Contract used by the download gate and by the shop, marketplace and My files apps:

* :func:`check_download_access` decides whether a user may download a file.
* :func:`record_served_download` / :func:`finalize_served_download` write the single
  record of files served to a user (:class:`ServedDownload`).
* :func:`is_seller` tells whether a user may offer paid or featured files.

Shop entitlements (``apps.shop.entitlements``) and marketplace sellers
(``apps.marketplace.services``) are optional seams imported lazily. A missing shop
module never opens a file: paid and featured files simply stay locked.
Per-item-type quotas, fair usage and the package's concurrent-download cap all come from
``effective_entitlements``; this module only asks and enforces.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from importlib import import_module
from types import ModuleType
from typing import Literal

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import (
    PRICE_CURRENCY,
    AccessTier,
    BaseFirmware,
    DownloadSession,
    ServedDownload,
)

Requirement = Literal["login", "purchase", "package"]
UserLike = AbstractBaseUser | AnonymousUser | None

ENTITLEMENTS_MODULE = "apps.shop.entitlements"
SELLER_MODULE = "apps.marketplace.services"
RUNNING_DOWNLOAD_WINDOW = timedelta(minutes=30)


@dataclass(frozen=True)
class AccessDecision:
    """Outcome of an access check; ``requires`` names the way to unlock a file."""

    allowed: bool
    reason: str = ""
    requires: Requirement | None = None
    price: Decimal | None = None
    tier: str = AccessTier.FREE
    already_served: bool = False


def format_price(amount: Decimal | None) -> str:
    """Display form of a price, e.g. ``USD 4.50``."""
    if amount is None:
        return ""
    return f"{PRICE_CURRENCY} {amount:.2f}"


def optional_module(name: str) -> ModuleType | None:
    """Import an optional app module; ``None`` only when that module is absent."""
    try:
        return import_module(name)
    except ModuleNotFoundError as error:
        if error.name == name:
            return None
        raise


def _entitlements() -> ModuleType | None:
    return optional_module(ENTITLEMENTS_MODULE)


def has_perk(user: UserLike, perk: str) -> bool:
    """Whether the user's package grants a perk such as ``ad_free`` (shop optional)."""
    if user is None or not user.is_authenticated:
        return False
    entitlements = _entitlements()
    if entitlements is None:
        return False
    return bool(getattr(entitlements.effective_entitlements(user).perks, perk, False))


def is_seller(user: UserLike) -> bool:
    """Whether ``user`` is an approved marketplace seller (False when unavailable)."""
    if user is None or not user.is_authenticated:
        return False
    module = optional_module(SELLER_MODULE)
    check = getattr(module, "is_active_seller", None)
    return bool(check(user)) if callable(check) else False


def _content_type(firmware: BaseFirmware) -> ContentType:
    return ContentType.objects.get_for_model(firmware, for_concrete_model=False)


def _served_row(
    user: AbstractBaseUser, firmware: BaseFirmware
) -> ServedDownload | None:
    return ServedDownload.objects.filter(
        user_id=user.pk, content_type=_content_type(firmware), object_id=firmware.pk
    ).first()


def check_download_access(user: UserLike, firmware: BaseFirmware) -> AccessDecision:
    """Decide whether ``user`` may be served ``firmware`` under its access tier.

    Free files are open to everyone (login and daily limits are separate gate
    settings). Paid files need a purchase entitlement for that file; featured files
    need featured access (an active package). Signed-in users must also fit their
    allowance. A file already served to the user stays re-downloadable.
    """
    tier = firmware.access_tier
    price = firmware.approved_price if tier == AccessTier.PAID else None
    authenticated = user is not None and user.is_authenticated
    if tier == AccessTier.FREE and not authenticated:
        return AccessDecision(True, tier=tier)
    if tier not in AccessTier.values:
        return AccessDecision(False, "This file has an unsupported access tier.")
    if user is None or not user.is_authenticated:
        return AccessDecision(
            False,
            "Sign in to unlock paid and featured files.",
            requires="login",
            price=price,
            tier=tier,
        )
    entitlements = _entitlements()
    if _served_row(user, firmware) is not None:
        decision = AccessDecision(True, tier=tier, price=price, already_served=True)
        return _concurrency_block(user, entitlements, decision) or decision

    decision = AccessDecision(True, tier=tier, price=price)
    if tier == AccessTier.PAID:
        decision = _paid_decision(user, firmware, entitlements)
    elif tier == AccessTier.FEATURED:
        decision = _featured_decision(user, firmware, entitlements)
    if decision.allowed and entitlements is not None:
        allowance = entitlements.check_quota(user, firmware)
        if not allowance.allowed:
            return AccessDecision(
                False,
                allowance.reason,
                requires="package",
                price=price,
                tier=tier,
            )
    if decision.allowed:
        return _concurrency_block(user, entitlements, decision) or decision
    return decision


def _running_downloads(user: AbstractBaseUser) -> int:
    """Download sessions started recently and not yet finished."""
    return DownloadSession.objects.filter(
        user_id=user.pk,
        status=DownloadSession.Status.STARTED,
        started_at__gte=timezone.now() - RUNNING_DOWNLOAD_WINDOW,
    ).count()


def _concurrency_block(
    user: AbstractBaseUser, entitlements: ModuleType | None, decision: AccessDecision
) -> AccessDecision | None:
    """A refusal when the package's concurrent-download cap is reached, else ``None``."""
    if entitlements is None:
        return None
    limit = entitlements.effective_entitlements(user).limits.max_concurrent
    if limit <= 0 or _running_downloads(user) < limit:
        return None
    return AccessDecision(
        False,
        f"Your package allows {limit} download{'' if limit == 1 else 's'} at once. "
        "Wait for a running download to finish.",
        tier=decision.tier,
        price=decision.price,
    )


def _paid_decision(
    user: AbstractBaseUser, firmware: BaseFirmware, entitlements: ModuleType | None
) -> AccessDecision:
    price = firmware.approved_price
    if entitlements is not None and entitlements.has_purchased(user, firmware):
        return AccessDecision(True, tier=AccessTier.PAID, price=price)
    if price is None:
        return AccessDecision(
            False,
            "This paid file is not available yet: its price is awaiting review.",
            requires="purchase",
            tier=AccessTier.PAID,
        )
    return AccessDecision(
        False,
        f"This is a paid file ({format_price(price)}). Request it to unlock the download.",
        requires="purchase",
        price=price,
        tier=AccessTier.PAID,
    )


def _featured_decision(
    user: AbstractBaseUser, firmware: BaseFirmware, entitlements: ModuleType | None
) -> AccessDecision:
    """Featured files need a package that covers them or a purchase of this very file."""
    if entitlements is not None and (
        entitlements.effective_entitlements(user).can_access_featured
        or entitlements.has_purchased(user, firmware)
    ):
        return AccessDecision(True, tier=AccessTier.FEATURED)
    price_for = getattr(entitlements, "price_for", None)
    sellable = callable(price_for) and price_for(user, firmware) is not None
    return AccessDecision(
        False,
        "Featured files are available to members with an active plan"
        + (", or buy this file on its own." if sellable else "."),
        requires="package",
        tier=AccessTier.FEATURED,
    )


def record_served_download(
    user: UserLike,
    firmware: BaseFirmware,
    *,
    tier: str,
    charged_quota: bool,
) -> ServedDownload | None:
    """Record that ``firmware`` was served to ``user``; idempotent per file.

    Only signed-in users have a library, so anonymous downloads are not recorded.
    Serving a file again refreshes its timestamp and counter, never the charge.
    """
    if user is None or not user.is_authenticated:
        return None
    paid_amount = firmware.approved_price if tier == AccessTier.PAID else None
    with transaction.atomic():
        row, created = ServedDownload.objects.get_or_create(
            user_id=user.pk,
            content_type=_content_type(firmware),
            object_id=firmware.pk,
            defaults={
                "tier": tier,
                "paid_amount": paid_amount,
                "charged_quota": charged_quota,
            },
        )
        if not created:
            updates: dict[str, object] = {
                "last_served_at": timezone.now(),
                "times_served": F("times_served") + 1,
            }
            if charged_quota and not row.charged_quota:
                updates["charged_quota"] = True
            ServedDownload.objects.filter(pk=row.pk).update(**updates)
            row.refresh_from_db()
    return row


def finalize_served_download(
    user: UserLike, firmware: BaseFirmware
) -> ServedDownload | None:
    """After a file was served: charge the allowance once and record it.

    Every file counts toward the user's allowance the first time it is served
    (the shop makes that idempotent per file); repeat downloads never charge.
    Raises ``PermissionDenied`` when the allowance or entitlement no longer holds.
    """
    if user is None or not user.is_authenticated:
        return None
    tier = firmware.access_tier
    with transaction.atomic():
        existing = _served_row(user, firmware)
        charged = existing is not None and existing.charged_quota
        entitlements = _entitlements()
        if not charged and entitlements is not None:
            try:
                entitlements.consume_quota(user, firmware, reason=str(tier))
            except entitlements.EntitlementError as error:
                raise PermissionDenied(str(error)) from error
            charged = True
        return record_served_download(user, firmware, tier=tier, charged_quota=charged)
