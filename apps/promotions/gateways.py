"""Seams to the apps a promotion plugs into: shop, wallet, marketplace and users.

Every dependency is imported lazily and guarded by the installed-app registry and the
``AppRegistry`` flag, so the promotions app keeps working (and says plainly what is
unavailable) when an optional neighbour is missing or switched off.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from django.apps import apps as django_apps
from django.db.models import QuerySet

from apps.core.app_service import app_enabled

if TYPE_CHECKING:
    from apps.shop.models import PackageTier, UserPackage

    from .models import Promotion


class GatewayUnavailable(Exception):
    """An optional neighbouring app is missing or switched off."""


def available(label: str) -> bool:
    """Whether ``apps.<label>`` is installed and its AppRegistry flag is on."""
    return django_apps.is_installed(f"apps.{label}") and app_enabled(label)


# ---------------------------------------------------------------- shop


def live_package(user: Any) -> UserPackage | None:
    """The user's live (not default) package, ``None`` on the free tier."""
    if not available("shop"):
        return None
    from apps.shop import entitlements

    return entitlements.effective_entitlements(user).package


def grant_package(
    user: Any, package: PackageTier, *, reference: str, days: int | None
) -> UserPackage:
    """Activate ``package`` for the user with source ``promotion``."""
    if not available("shop"):
        raise GatewayUnavailable("Packages are not available right now.")
    from apps.shop import services
    from apps.shop.models import UserPackage

    return services.activate_package(
        user,
        package,
        reference=reference,
        source=UserPackage.Source.PROMOTION,
        duration_days=days,
    )


def cancel_promotion_packages(reference: str) -> int:
    """Cancel live packages a promotion granted (``reference`` = ``promotion:<id>``)."""
    if not django_apps.is_installed("apps.shop"):
        return 0
    from apps.shop import services
    from apps.shop.models import UserPackage

    cancelled = 0
    held = UserPackage.objects.filter(
        payment_reference=reference,
        source=UserPackage.Source.PROMOTION,
        status=UserPackage.Status.ACTIVE,
    )
    for user_package in held:
        services.cancel_package(user_package)
        cancelled += 1
    return cancelled


def package_label(promotion_id: int) -> str:
    """The reference stored on a package a promotion granted."""
    return f"promotion:{promotion_id}"


# ---------------------------------------------------------------- wallet


def credit_bonus(user: Any, amount: int, *, key: str, note: str) -> None:
    """Credit a promotion bonus to the user's wallet (idempotent on ``key``)."""
    if not available("wallet"):
        raise GatewayUnavailable("Credits are not available right now.")
    from apps.wallet import api

    api.credit(user, amount, reason=api.LedgerReason.PROMOTION, key=key, note=note)


def credits_error_types() -> tuple[type[Exception], ...]:
    """Wallet errors that refuse a credit (frozen wallet, bad amount)."""
    if not django_apps.is_installed("apps.wallet"):
        return ()
    from apps.wallet import api

    return (api.WalletError,)


def register_reward_modifier(modifier: Callable[..., int]) -> None:
    if django_apps.is_installed("apps.wallet"):
        from apps.wallet import api

        api.register_reward_modifier(modifier)


def earn_url() -> str:
    if not available("wallet"):
        return ""
    from apps.wallet import api

    return api.earn_url()


# ---------------------------------------------------------------- marketplace


def is_seller(user: Any) -> bool:
    if not available("marketplace"):
        return False
    from apps.marketplace.services import is_active_seller

    return bool(is_active_seller(user))


def seller_user_ids() -> QuerySet[Any]:
    """Ids of approved, active sellers (empty without the marketplace)."""
    if not available("marketplace"):
        return django_apps.get_model("users", "CustomUser").objects.none()
    profile = django_apps.get_model("marketplace", "SellerProfile")
    return profile.objects.filter(is_active=True, verified=True).values("user_id")


def package_holder_ids() -> QuerySet[Any]:
    if not available("shop"):
        return django_apps.get_model("users", "CustomUser").objects.none()
    held = django_apps.get_model("shop", "UserPackage")
    return held.objects.filter(status="active").values("user_id")


# ---------------------------------------------------------------- users


def notify(
    user: Any, title: str, message: str, url: str, *, action_type: str = "award"
) -> None:
    """Send one in-app notification (honours the user's preferences)."""
    from apps.users.services.notifications import send_notification

    send_notification(user, title, message, url=url, action_type=action_type)


def notify_many(users: Sequence[Any], title: str, message: str, url: str) -> int:
    """Bulk system notification; users who switched system notices off are skipped."""
    from apps.users.services.notifications import broadcast_notification

    muted = set(
        django_apps.get_model("users", "NotificationPreferences")
        .objects.filter(user__in=users, web_system=False)
        .values_list("user_id", flat=True)
    )
    recipients = [user for user in users if user.pk not in muted]
    return broadcast_notification(
        recipients, title, message, url=url, action_type="system"
    )


def promotion_for_announcements(
    announcement_ids: Sequence[int],
) -> dict[int, Promotion]:
    """Map announcement id → the promotion that owns it."""
    from .models import Promotion

    return {
        promotion.announcement_id: promotion
        for promotion in Promotion.objects.filter(announcement_id__in=announcement_ids)
        if promotion.announcement_id is not None
    }
