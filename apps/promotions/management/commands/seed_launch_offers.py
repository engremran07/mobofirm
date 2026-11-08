"""Create the two launch offers as disabled drafts for staff to review and schedule."""

from __future__ import annotations

from typing import Any

from django.apps import apps as django_apps
from django.core.management.base import BaseCommand

from apps.promotions.models import Promotion, PromotionKind, PromotionStatus


def _premium_package() -> Any:
    """The most expensive active public package, when the shop has one."""
    if not django_apps.is_installed("apps.shop"):
        return None
    package_model = django_apps.get_model("shop", "PackageTier")
    return (
        package_model.objects.filter(is_active=True, is_public=True, is_default=False)
        .order_by("-price", "-sort_order")
        .first()
    )


class Command(BaseCommand):
    help = (
        "Create the 'Launch bonus' and 'Contribute & unlock premium' offers as drafts. "
        "Safe to rerun: existing offers are left untouched. Review and schedule them "
        "in the admin (Campaigns > Promotions)."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        package = _premium_package()
        offers = (
            (
                "launch-bonus",
                {
                    "name": "Launch bonus",
                    "headline": "Welcome to Mobofirm: 10 bonus downloads on us.",
                    "description": (
                        "Claim 10 extra new files on top of your free allowance "
                        "while the launch offer runs."
                    ),
                    "terms": "Bonus files are added to your download quota for 30 days.",
                    "kind": PromotionKind.BONUS_QUOTA,
                    "bonus_files": 10,
                    "trial_days": 30,
                },
            ),
            (
                "contribute-unlock-premium",
                {
                    "name": "Contribute & unlock premium",
                    "headline": "Get 3 uploads approved and unlock a premium package.",
                    "description": (
                        "Share working firmware. When three of your uploads are "
                        "approved within 30 days you earn premium access."
                    ),
                    "terms": "Only uploads approved by staff count; self-approved items never do.",
                    "kind": PromotionKind.EARN_BY_CONTRIBUTING,
                    "earn_rules": {
                        "events": ["contribution.approved"],
                        "count": 3,
                        "within_days": 30,
                    },
                    "target_package": package,
                    "trial_days": 7,
                },
            ),
        )
        for slug, fields in offers:
            _, created = Promotion.objects.get_or_create(
                slug=slug,
                defaults={**fields, "status": PromotionStatus.DRAFT, "announce": True},
            )
            self.stdout.write(f"{'created' if created else 'kept'}: {slug} (draft)")
        if package is None:
            self.stdout.write(
                self.style.WARNING(
                    "No public paid package exists yet: choose the reward package on "
                    "'Contribute & unlock premium' before scheduling it."
                )
            )
