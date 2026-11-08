"""Create the standard download packages: Free, Premium and Gold.

Packages are real data, not examples: the Free package is the default every account
starts with, and the paid packages carry the quotas, device limits and perks shown on
the pricing page. Safe to rerun: existing packages are left untouched, so values staff
have edited in the admin are never overwritten.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.shop.baseline import backfill_baseline_packages
from apps.shop.models import LIFETIME, PackageTier

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
        "paid_discount_percent": 10,
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
        "priority_queue": True,
        "resume_support": True,
        "early_access": True,
    },
)


class Command(BaseCommand):
    help = (
        "Create the Free, Premium and Gold download packages (idempotent) and give "
        "every existing account the free package."
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
        result = backfill_baseline_packages()
        self.stdout.write(
            f"free package: {result.created} assigned, {result.already} already had it"
        )
