from __future__ import annotations

from typing import Any

from apps.ads.models import AdPlacement, Campaign
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
    if allowed_tags and tags and not (allowed_tags & tags):
        return False
    return True


def placement_allowed(placement: AdPlacement) -> bool:
    return placement.is_enabled and placement.is_active and not placement.is_deleted
