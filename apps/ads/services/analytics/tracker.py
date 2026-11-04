from __future__ import annotations

import logging
from typing import Any

from apps.ads.models import AdCreative, AdEvent, AdPlacement, Campaign
from apps.core.utils import feature_flags
from apps.core.utils.logging import log_event

logger = logging.getLogger(__name__)


def record_event(
    event_type: str,
    placement: AdPlacement | None = None,
    creative: AdCreative | None = None,
    campaign: Campaign | None = None,
    user=None,
    request_meta: dict[str, Any] | None = None,
):
    if not feature_flags.ads_enabled():
        return

    try:
        meta = request_meta or {}
        # Respect consent: do not record if explicitly denied.
        if meta.get("consent_granted") is False:
            return

        payload = {
            "event_type": event_type,
            "placement": placement,
            "creative": creative,
            "campaign": campaign,
            "user": user,
            "request_meta": meta,
            "page_url": meta.get("page_url", ""),
            "referrer_url": meta.get("referrer", ""),
            "user_agent": meta.get("user_agent", ""),
            "session_id": meta.get("session_id", ""),
            "site_domain": meta.get("site", ""),
            "consent_granted": bool(meta.get("consent_granted", False)),
        }
        AdEvent.objects.create(**payload)
        log_event(
            logger,
            "info",
            "ads.event.recorded",
            event_type=event_type,
            placement=getattr(placement, "slug", None),
            creative=getattr(creative, "id", None),
            campaign=getattr(campaign, "id", None),
            correlation_id=meta.get("correlation_id"),
        )
    except Exception:
        logger.warning("record_event failed", exc_info=True)
