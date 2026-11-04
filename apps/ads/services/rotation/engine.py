from __future__ import annotations

import logging
import random
from collections.abc import Iterable

from apps.ads.models import AdCreative, AdPlacement, AdsSettings, PlacementAssignment
from apps.ads.services.targeting.engine import campaign_allowed, placement_allowed
from apps.core.utils import feature_flags
from apps.core.utils.logging import log_event

logger = logging.getLogger(__name__)


def _aggressiveness_multiplier() -> int:
    """
    Get ad aggressiveness multiplier from AdsSettings singleton.
    Uses app-specific AdsSettings for modularity (not SiteSettings).
    """
    try:
        level = AdsSettings.get_solo().ad_aggressiveness_level
    except Exception:
        level = "balanced"
    if level == "minimal":
        return 1
    if level == "aggressive":
        return 3
    return 2


def choose_creative(
    placement: AdPlacement, context: dict | None = None
) -> AdCreative | None:
    """
    Weighted random selection among enabled assignments for a placement.
    Aggressiveness controls how heavily weights are emphasized.
    """
    if not feature_flags.ads_enabled():
        return None
    if not placement_allowed(placement):
        return None

    context = context or {}
    consent_val = context.get("consent_ads")
    consent_ads = (
        consent_val is True
        or (
            isinstance(consent_val, str) and consent_val.lower() in {"1", "true", "yes"}
        )
        or (isinstance(consent_val, int) and consent_val == 1)
    )
    ad_settings = None
    try:
        ad_settings = AdsSettings.get_solo()
    except Exception:
        ad_settings = None

    # If consent for ads is not granted, do not serve personalized / paid campaigns
    if not consent_ads:
        return None

    allowed_types = {
        t.strip() for t in (placement.allowed_types or "").split(",") if t.strip()
    }
    enabled_networks = bool(getattr(ad_settings, "ad_networks_enabled", False))
    qs: Iterable[PlacementAssignment] = placement.assignments.filter(  # type: ignore[attr-defined]
        is_enabled=True,
        is_active=True,
        creative__is_enabled=True,
        creative__is_active=True,
    ).select_related("creative", "creative__campaign")
    pool = []
    mult = _aggressiveness_multiplier()
    for a in qs:
        creative = a.creative
        if allowed_types and creative.creative_type not in allowed_types:
            continue
        campaign = creative.campaign
        if campaign and not campaign.is_live() and not campaign.locked:
            continue
        if (
            campaign
            and campaign.type in {"network", "affiliate"}
            and not enabled_networks
        ):
            continue
        if campaign and not campaign_allowed(campaign, context):
            continue
        weight = max(1, a.weight * mult)
        pool.extend([creative] * weight)
    if not pool:
        log_event(logger, "warning", "ads.rotation.no_pool", placement=placement.slug)
        return None
    choice = random.choice(pool)  # noqa: S311
    log_event(
        logger,
        "info",
        "ads.rotation.selected",
        placement=placement.slug,
        creative=choice.id,
        campaign=getattr(choice.campaign, "id", None),
    )
    return choice
