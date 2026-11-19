"""Create the standard download packages: Free, Premium, Silver, Gold and Platinum.

Packages are real data, not examples: the Free package is the default every account
starts with, and the paid packages carry the quotas, device limits, perks and ad rules
shown on the pricing page. The ladder is Free (ads, link shortener, unlock by ads),
Silver (bigger quota, direct links), Gold (larger quota, no download-gate ads), Platinum
(ad-free, highest quotas); Premium is the older mid tier and is kept as it is.

Safe to rerun and never destructive: existing packages are never deleted or renamed and
values staff edited are never overwritten. Existing Free and Gold rows only receive the
new ad fields while those still hold their untouched defaults. A disabled "Download
unlock" rewarded ad is created when the ads app is installed; staff pick its placement
and enable it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.apps import apps as django_apps
from django.core.management.base import BaseCommand
from django.db import models, transaction

from apps.shop.baseline import backfill_baseline_packages
from apps.shop.models import LIFETIME, PackageTier

UNLOCK_AD_NAME = "Download unlock"

# Fixed prices, quotas and perks per package. ``duration_days`` 0 means lifetime.
PACKAGES: tuple[dict[str, Any], ...] = (
    {
        "name": "Free",
        "slug": "free",
        "description": "Every account starts here: free firmware, a small daily allowance.",
        "price": Decimal(0),
        "duration_days": LIFETIME,
        "is_default": True,
        "is_public": True,
        "sort_order": 10,
        "limit_devices": True,
        "device_limit": 3,
        "slot_reset_credits": 20,
        "bandwidth_enabled": True,
        "limit_files": 10,
        "window": "day",
        "applies_to_free": True,
        "applies_to_featured": False,
        "applies_to_paid": False,
        "max_concurrent_downloads": 1,
        "cooldown_seconds": 60,
        "unlock_by_ads_free": True,
        "unlock_ads_free": 3,
        "unlock_by_ads_featured": True,
        "unlock_ads_featured": 5,
        "unlock_cooldown_seconds": 30,
        "unlock_daily_cap": 5,
    },
    {
        "name": "Silver",
        "slug": "silver",
        "description": "A bigger monthly allowance and plain download links, no shortener.",
        "price": Decimal(5),
        "duration_days": 30,
        "is_default": False,
        "is_public": True,
        "sort_order": 15,
        "limit_devices": True,
        "device_limit": 3,
        "bandwidth_enabled": True,
        "limit_files": 40,
        "window": "package",
        "applies_to_free": True,
        "applies_to_featured": False,
        "applies_to_paid": False,
        "fair_daily_files": 15,
        "max_concurrent_downloads": 1,
        "cooldown_seconds": 30,
        "direct_links": True,
        "resume_support": True,
        "unlock_by_ads_free": True,
        "unlock_ads_free": 2,
        "unlock_by_ads_featured": True,
        "unlock_ads_featured": 3,
        "unlock_cooldown_seconds": 15,
        "unlock_daily_cap": 10,
    },
    {
        "name": "Premium",
        "slug": "premium",
        "description": "More downloads, featured firmware and more devices for a month.",
        "price": Decimal(9),
        "duration_days": 30,
        "is_default": False,
        "is_public": True,
        "is_popular": True,
        "sort_order": 20,
        "limit_devices": True,
        "device_limit": 5,
        "bandwidth_enabled": True,
        "limit_files": 100,
        "limit_size": 20,
        "size_unit": "GB",
        "window": "package",
        "extra_daily_files": 10,
        "applies_to_free": True,
        "applies_to_featured": True,
        "applies_to_paid": False,
        "fair_daily_files": 30,
        "max_concurrent_downloads": 2,
        "cooldown_seconds": 15,
        "ad_free": True,
        "resume_support": True,
    },
    {
        "name": "Gold",
        "slug": "gold",
        "description": "The full library for a year: everything, ad-free, up to ten devices.",
        "price": Decimal(79),
        "duration_days": 365,
        "is_default": False,
        "is_public": True,
        "sort_order": 30,
        "limit_devices": True,
        "device_limit": 10,
        "bandwidth_enabled": True,
        "limit_files": 400,
        "limit_size": 200,
        "size_unit": "GB",
        "window": "package",
        "extra_daily_files": 20,
        "applies_to_free": True,
        "applies_to_featured": True,
        "applies_to_paid": True,
        "paid_discount_percent": 20,
        "fair_daily_files": 60,
        "max_concurrent_downloads": 3,
        "cooldown_seconds": 0,
        "ad_free": True,
        "direct_links": True,
        "priority_queue": True,
        "resume_support": True,
        "early_access": True,
    },
    {
        "name": "Platinum",
        "slug": "platinum",
        "description": "Fully ad-free with the highest quotas, plain links and priority.",
        "price": Decimal(149),
        "duration_days": 365,
        "is_default": False,
        "is_public": True,
        "sort_order": 40,
        "limit_devices": True,
        "device_limit": 15,
        "bandwidth_enabled": True,
        "limit_files": 1000,
        "limit_size": 500,
        "size_unit": "GB",
        "window": "package",
        "extra_daily_files": 40,
        "applies_to_free": True,
        "applies_to_featured": True,
        "applies_to_paid": True,
        "paid_discount_percent": 30,
        "fair_daily_files": 120,
        "max_concurrent_downloads": 5,
        "cooldown_seconds": 0,
        "ad_free": True,
        "direct_links": True,
        "priority_queue": True,
        "resume_support": True,
        "early_access": True,
    },
)

# New ad fields given to rows that already exist (Free, Gold). A field is only set while
# it still holds the model default, so anything staff changed is left alone.
LADDER_UPDATES: dict[str, dict[str, Any]] = {
    "free": {
        "unlock_by_ads_free": True,
        "unlock_by_ads_featured": True,
    },
    "gold": {"direct_links": True},
}


def apply_ladder_updates(package: PackageTier) -> list[str]:
    """Set the new ad fields on an existing package that still has the defaults."""
    changed: list[str] = []
    for field, value in LADDER_UPDATES.get(package.slug, {}).items():
        model_field = PackageTier._meta.get_field(field)
        if not isinstance(model_field, models.Field):
            continue
        default = model_field.get_default()
        if getattr(package, field) == default and default != value:
            setattr(package, field, value)
            changed.append(field)
    if changed:
        package.save(update_fields=changed)
    return changed


def ensure_unlock_ad() -> str:
    """Create the disabled rewarded ad that counts toward unlocks (ads app optional)."""
    if not django_apps.is_installed("apps.ads"):
        return "ads app not installed: no unlock ad created"
    config_model = django_apps.get_model("ads", "RewardedAdConfig")
    _, created = config_model.objects.get_or_create(
        name=UNLOCK_AD_NAME,
        defaults={
            "reward_type": "download_unlock",
            "reward_amount": 1,
            "reward_description": "Counts toward unlocking one file.",
            "is_enabled": False,
            "cooldown_minutes": 0,
            "daily_limit_per_user": 0,
            "monthly_limit_per_user": 0,
        },
    )
    return (
        "created the disabled rewarded ad “Download unlock”: pick its placement "
        "under Ads > Rewarded ad configs and enable it"
        if created
        else "rewarded ad “Download unlock” already exists"
    )


class Command(BaseCommand):
    help = (
        "Create the Free, Silver, Gold and Platinum download packages (idempotent; "
        "Premium and existing rows are kept), add the ad-unlock fields to Free and Gold "
        "and give every existing account the free package."
    )

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        for fields in PACKAGES:
            slug = fields["slug"]
            defaults = {key: value for key, value in fields.items() if key != "slug"}
            package, created = PackageTier.objects.get_or_create(
                slug=slug, defaults=defaults
            )
            self.stdout.write(
                f"{'created' if created else 'kept'}: {package.name} - "
                f"{package.summary()}"
            )
            if not created:
                updated = apply_ladder_updates(package)
                if updated:
                    self.stdout.write(f"  new ad fields set: {', '.join(updated)}")
        self.stdout.write(ensure_unlock_ad())
        result = backfill_baseline_packages()
        self.stdout.write(
            f"free package: {result.created} assigned, {result.already} already had it"
        )
