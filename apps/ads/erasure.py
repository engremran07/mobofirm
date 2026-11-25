"""Account-erasure contract for user-linked Ads history."""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from django.utils import timezone

from apps.ads.models import AdEvent, AffiliateClick, RewardedAdGrant, RewardedAdView


@dataclass(frozen=True)
class ErasureReport:
    """Counts of Ads records detached from an erased account."""

    affiliate_clicks: int = 0
    ad_events: int = 0
    rewarded_views: int = 0
    rewarded_grants: int = 0
    active_views_invalidated: int = 0


def anonymise_user(user: AbstractBaseUser) -> ErasureReport:
    """Detach Ads history while preserving anonymous analytics and reward audit rows."""
    now = timezone.now()
    with transaction.atomic():
        views = list(
            RewardedAdView.objects.select_for_update()
            .filter(user_id=user.pk)
            .only("pk", "status")
        )
        view_ids = [view.pk for view in views]
        active_views_invalidated = RewardedAdView.objects.filter(
            pk__in=view_ids,
            status=RewardedAdView.Status.STARTED,
        ).update(
            status=RewardedAdView.Status.EXPIRED,
            token_digest="",
            updated_at=now,
        )
        rewarded_views = RewardedAdView.objects.filter(pk__in=view_ids).update(
            user=None,
            updated_at=now,
        )
        rewarded_grants = RewardedAdGrant.objects.filter(user_id=user.pk).update(
            user=None,
            updated_at=now,
        )
        affiliate_clicks = AffiliateClick.objects.filter(user_id=user.pk).update(
            user=None,
            updated_at=now,
        )
        ad_events = AdEvent.objects.filter(user_id=user.pk).update(
            user=None,
            updated_at=now,
        )
    return ErasureReport(
        affiliate_clicks=affiliate_clicks,
        ad_events=ad_events,
        rewarded_views=rewarded_views,
        rewarded_grants=rewarded_grants,
        active_views_invalidated=active_views_invalidated,
    )
