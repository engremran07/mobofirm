"""The free package every account holds from sign-up (the *baseline*).

Each user gets one active ``UserPackage`` row with ``is_baseline=True`` pointing at the
default package. It is a record, not a competing source of truth: the one resolver
(:func:`apps.shop.entitlements.effective_entitlements`) still applies the *current*
default package when the user holds no other live package, and a purchased or granted
package replaces it for its duration without touching this row.

* :func:`ensure_baseline_package` is idempotent and race-safe (a partial unique
  constraint allows one active baseline per user); it is called from the
  ``post_save`` receiver for every new account, whichever path created it (allauth
  e-mail or social sign-up, admin, management commands, tests).
* :func:`backfill_baseline_packages` gives it to accounts that already exist
  (``manage.py backfill_free_package``) and can be re-run safely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef

from .models import PackageTier, UserPackage

BATCH_SIZE = 500


def default_package() -> PackageTier | None:
    """The active default (free) package, or ``None`` before it is seeded."""
    return PackageTier.objects.filter(is_default=True, is_active=True).first()


def _active_baseline(user: Any) -> UserPackage | None:
    return UserPackage.objects.filter(
        user=user, is_baseline=True, status=UserPackage.Status.ACTIVE
    ).first()


def ensure_baseline_package(user: Any) -> UserPackage | None:
    """Give ``user`` the free package record; ``None`` while no default package exists."""
    if user is None or user.pk is None:
        return None
    tier = default_package()
    if tier is None:
        return None
    existing = _active_baseline(user)
    if existing is not None:
        if existing.package_id != tier.pk:
            existing.package = tier
            existing.save(update_fields=["package"])
        return existing
    try:
        with transaction.atomic():
            return UserPackage.objects.create(
                user=user,
                package=tier,
                status=UserPackage.Status.ACTIVE,
                source=UserPackage.Source.DEFAULT,
                is_baseline=True,
                expires_at=None,
            )
    except IntegrityError:
        return _active_baseline(user)


@dataclass(frozen=True)
class BackfillResult:
    created: int
    already: int
    no_default_package: bool


def backfill_baseline_packages(*, batch_size: int = BATCH_SIZE) -> BackfillResult:
    """Create the baseline for every existing account that lacks one (idempotent)."""
    tier = default_package()
    users = get_user_model().objects.all()
    holders = UserPackage.objects.filter(
        user=OuterRef("pk"), is_baseline=True, status=UserPackage.Status.ACTIVE
    )
    missing = users.filter(~Exists(holders)).values_list("pk", flat=True)
    already = users.filter(Exists(holders)).count()
    if tier is None:
        return BackfillResult(created=0, already=already, no_default_package=True)
    pending = list(missing.order_by("pk"))
    for start in range(0, len(pending), batch_size):
        UserPackage.objects.bulk_create(
            [
                UserPackage(
                    user_id=user_id,
                    package=tier,
                    status=UserPackage.Status.ACTIVE,
                    source=UserPackage.Source.DEFAULT,
                    is_baseline=True,
                )
                for user_id in pending[start : start + batch_size]
            ],
            ignore_conflicts=True,
        )
    created = users.filter(Exists(holders)).count() - already
    return BackfillResult(created=created, already=already, no_default_package=False)
