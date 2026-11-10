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

from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal
from importlib import import_module
from types import ModuleType
from typing import Any, Literal

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
    DownloadRule,
    DownloadSession,
    ServedDownload,
)

Requirement = Literal["login", "purchase", "package"]
UserLike = AbstractBaseUser | AnonymousUser | None

ENTITLEMENTS_MODULE = "apps.shop.entitlements"
RULES_MODULE = "apps.shop.download_rules"
SELLER_MODULE = "apps.marketplace.services"
RUNNING_DOWNLOAD_WINDOW = timedelta(minutes=30)
ACTIVE = "active"
ALLOWANCE_ENDED = "allowance_ended"
PACKAGE_EXPIRED = "package_expired"
PACKAGE_EXPIRED_MESSAGE = "Package expired — renew to download again."


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


@dataclass(frozen=True)
class ServedStatus:
    """Whether a served file can still be fetched, and why not when it cannot."""

    active: bool
    code: str = ACTIVE
    message: str = ""


_ACTIVE = ServedStatus(True)


def _allowance_message(row: ServedDownload, policy: Any = None) -> str:
    """Why a free file's re-download allowance is spent (empty while it is open)."""
    if policy is None:
        rules = optional_module(RULES_MODULE)
        if rules is None:
            return ""
        policy = rules.redownload_policy()
    window_over = bool(
        policy.window_days
        and timezone.now() > row.served_at + timedelta(days=policy.window_days)
    )
    count_over = bool(
        policy.max_redownloads and row.times_served - 1 >= policy.max_redownloads
    )
    if not (window_over or count_over):
        return ""
    return (
        "Your free re-download allowance for this file has ended "
        f"({policy.describe()} after the first download)."
    )


def served_status(
    user: AbstractBaseUser, row: ServedDownload, policy: Any = None
) -> ServedStatus:
    """Apply the lifetime rule of a served file (free / package / lifetime).

    ``package_expired`` means the package period the file was served under is over
    (expired, cancelled or replaced by a new activation); ``allowance_ended`` means a
    free file used up its re-download allowance. Without the shop nothing expires.
    ``policy`` lets a caller that checks many rows read the allowance setting once.
    """
    rules = optional_module(RULES_MODULE)
    if row.access_rule == DownloadRule.FREE:
        message = _allowance_message(row, policy)
        return ServedStatus(False, ALLOWANCE_ENDED, message) if message else _ACTIVE
    if (
        row.access_rule == DownloadRule.PACKAGE
        and rules is not None
        and not rules.period_is_live(user, row.user_package_id)
    ):
        return ServedStatus(False, PACKAGE_EXPIRED, PACKAGE_EXPIRED_MESSAGE)
    return _ACTIVE


def served_statuses(
    user: AbstractBaseUser, rows: list[ServedDownload]
) -> dict[int, ServedStatus]:
    """:func:`served_status` of many rows (keyed by row pk), reading settings once."""
    rules = optional_module(RULES_MODULE)
    policy = rules.redownload_policy() if rules is not None else None
    return {row.pk: served_status(user, row, policy) for row in rows}


def _anonymous_decision(tier: str, price: Decimal | None) -> AccessDecision:
    if tier == AccessTier.FREE:
        return AccessDecision(True, tier=tier)
    return AccessDecision(
        False,
        "Sign in to unlock paid and featured files.",
        requires="login",
        price=price,
        tier=tier,
    )


def check_download_access(user: UserLike, firmware: BaseFirmware) -> AccessDecision:
    """Decide whether ``user`` may be served ``firmware`` under its access tier.

    Free files are open to everyone (login and daily limits are separate gate
    settings). Paid files need a purchase entitlement for that file; featured files
    need featured access (an active package). Signed-in users must also fit their
    allowance. A file already served to the user stays fetchable only while its
    :func:`served_status` is active: a free file within its re-download allowance, a
    featured file while the package period it was served under is live, a bought
    file for good.
    """
    tier = firmware.access_tier
    price = firmware.approved_price if tier == AccessTier.PAID else None
    if tier not in AccessTier.values:
        return AccessDecision(False, "This file has an unsupported access tier.")
    if user is None or not user.is_authenticated:
        return _anonymous_decision(tier, price)
    entitlements = _entitlements()
    row = _served_row(user, firmware)
    lapsed: ServedStatus | None = None
    if row is not None:
        status = served_status(user, row)
        if status.active:
            decision = AccessDecision(True, tier=tier, price=price, already_served=True)
            return _concurrency_block(user, entitlements, decision) or decision
        if status.code == ALLOWANCE_ENDED and tier == AccessTier.FREE:
            return AccessDecision(False, status.message, tier=tier)
        lapsed = status
    decision = _first_serve_decision(user, firmware, entitlements)
    if lapsed is not None and lapsed.code == PACKAGE_EXPIRED and not decision.allowed:
        return replace(decision, reason=PACKAGE_EXPIRED_MESSAGE, requires="package")
    return decision


def _first_serve_decision(
    user: AbstractBaseUser, firmware: BaseFirmware, entitlements: ModuleType | None
) -> AccessDecision:
    """Access for a file the user holds no valid served record of."""
    tier = firmware.access_tier
    price = firmware.approved_price if tier == AccessTier.PAID else None
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
    """A refusal when the package's concurrent-download cap is hit, else ``None``."""
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
        f"This is a paid file ({format_price(price)}). "
        "Request it to unlock the download.",
        requires="purchase",
        price=price,
        tier=AccessTier.PAID,
    )


def _featured_decision(
    user: AbstractBaseUser, firmware: BaseFirmware, entitlements: ModuleType | None
) -> AccessDecision:
    """Featured files need a package that covers them or a purchase of this file."""
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


def serve_rule(
    user: AbstractBaseUser, firmware: BaseFirmware, tier: str
) -> tuple[str, int | None]:
    """The lifetime rule (and package period) a serve of ``firmware`` falls under.

    Free files keep the bounded re-download allowance, paid files and files the user
    bought on their own are theirs for good, and a featured file served through a
    package is tied to that package period.
    """
    if tier == AccessTier.FREE:
        return DownloadRule.FREE, None
    if tier == AccessTier.PAID:
        return DownloadRule.LIFETIME, None
    entitlements = _entitlements()
    if entitlements is not None and entitlements.has_purchased(user, firmware):
        return DownloadRule.LIFETIME, None
    rules = optional_module(RULES_MODULE)
    period = rules.current_period_id(user) if rules is not None else None
    return DownloadRule.PACKAGE, period


@dataclass(frozen=True)
class ServeTerms:
    """The lifetime rule a serve is recorded under (see :func:`serve_rule`)."""

    rule: str
    period_id: int | None = None
    restart: bool = False


def record_served_download(
    user: UserLike,
    firmware: BaseFirmware,
    *,
    tier: str,
    charged_quota: bool,
    terms: ServeTerms | None = None,
) -> ServedDownload | None:
    """Record that ``firmware`` was served to ``user``; idempotent per file.

    Only signed-in users have a library, so anonymous downloads are not recorded.
    Serving a file again refreshes its timestamp and counter, never the charge.
    ``terms.restart`` starts a new record period for a file whose earlier one lapsed
    (an expired package period or a spent free allowance): the first-serve date, the
    counter, the rule and the package period are all reset.
    """
    if user is None or not user.is_authenticated:
        return None
    if terms is None:
        rule, period_id = serve_rule(user, firmware, tier)
        terms = ServeTerms(rule, period_id)
    paid_amount = firmware.approved_price if tier == AccessTier.PAID else None
    fresh = {
        "tier": tier,
        "paid_amount": paid_amount,
        "charged_quota": charged_quota,
        "access_rule": terms.rule,
        "user_package_id": terms.period_id,
    }
    with transaction.atomic():
        row, created = ServedDownload.objects.get_or_create(
            user_id=user.pk,
            content_type=_content_type(firmware),
            object_id=firmware.pk,
            defaults=fresh,
        )
        if not created:
            now = timezone.now()
            if terms.restart:
                updates: dict[str, object] = {
                    **fresh,
                    "served_at": now,
                    "last_served_at": now,
                    "times_served": 1,
                }
            else:
                updates = {
                    "last_served_at": now,
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

    Every file counts toward the user's allowance the first time it is served in a
    record period (the shop makes that idempotent per file and package period);
    repeat downloads never charge. Serving a file whose earlier record lapsed starts
    a new one. Raises ``PermissionDenied`` when the allowance or entitlement no longer
    holds.
    """
    if user is None or not user.is_authenticated:
        return None
    tier = firmware.access_tier
    with transaction.atomic():
        existing = _served_row(user, firmware)
        status = served_status(user, existing) if existing is not None else None
        if (
            status is not None
            and status.code == ALLOWANCE_ENDED
            and tier == AccessTier.FREE
        ):
            raise PermissionDenied(status.message)
        current = status is not None and status.active
        charged = current and existing is not None and existing.charged_quota
        entitlements = _entitlements()
        if not charged and entitlements is not None:
            try:
                entitlements.consume_quota(user, firmware, reason=str(tier))
            except entitlements.EntitlementError as error:
                raise PermissionDenied(str(error)) from error
            charged = True
        terms = None
        if not current:
            rule, period_id = serve_rule(user, firmware, tier)
            terms = ServeTerms(rule, period_id, restart=existing is not None)
        return record_served_download(
            user, firmware, tier=tier, charged_quota=charged, terms=terms
        )
