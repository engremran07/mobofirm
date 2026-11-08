from __future__ import annotations

from typing import Any

from django.utils import timezone

from apps.ads.models import AdEvent, AdPlacement, Campaign
from apps.core.utils import feature_flags


def is_ads_enabled() -> bool:
    return feature_flags.ads_enabled()


def campaign_allowed(campaign: Campaign, context: dict[str, Any]) -> bool:
    if not campaign.is_live():
        return False
    if not feature_flags.ads_enabled():
        return False
    # Consent-aware: block personalized campaigns when ads consent not granted
    consent_val = context.get("consent_ads")
    consent_granted = consent_val is True or (
        isinstance(consent_val, str) and consent_val.lower() in {"1", "true", "yes"}
    )
    if not consent_granted and campaign.type in {"affiliate", "network", "direct"}:
        return False
    rules = campaign.targeting_rules or {}
    page_context = context.get("page_context")
    tags = set(context.get("tags") or [])
    allowed_pages = set(rules.get("page_types") or [])
    if allowed_pages and page_context and page_context not in allowed_pages:
        return False
    allowed_tags = set(rules.get("tags") or [])
    return not (allowed_tags and tags and not (allowed_tags & tags))


def placement_allowed(placement: AdPlacement) -> bool:
    return placement.is_enabled and placement.is_active and not placement.is_deleted


def campaign_within_caps(campaign: Campaign) -> bool:
    """Whether the campaign's daily and lifetime impression caps still have room.

    A cap of 0 means unlimited. Only counted impressions matter: consent-less views
    are never recorded, so they cannot exhaust a cap.
    """
    if not campaign.daily_cap and not campaign.total_cap:
        return True
    served = AdEvent.objects.filter(campaign=campaign, event_type="impression")
    if campaign.total_cap and served.count() >= campaign.total_cap:
        return False
    if campaign.daily_cap:
        today = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
        if served.filter(created_at__gte=today).count() >= campaign.daily_cap:
            return False
    return True
