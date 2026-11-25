from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from django.db import DatabaseError

from apps.ads.models import AdCreative, AdEvent, AdPlacement, Campaign
from apps.core.utils import feature_flags
from apps.core.utils.logging import log_event
from apps.users.services.consent import hash_ip, hash_ua

logger = logging.getLogger(__name__)

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def _digest_identifier(value: object, digest: Callable[[str], str]) -> str:
    if not isinstance(value, str) or not value:
        return ""
    return value if _DIGEST_RE.fullmatch(value) else digest(value)


def _safe_request_meta(values: dict[str, Any]) -> dict[str, Any]:
    return {
        "ip_hash": _digest_identifier(values.get("ip_hash", values.get("ip")), hash_ip),
        "user_agent_hash": _digest_identifier(
            values.get("user_agent_hash", values.get("user_agent")), hash_ua
        ),
        "referrer": values.get("referrer", "")
        if isinstance(values.get("referrer", ""), str)
        else "",
        "page_url": values.get("page_url", "")
        if isinstance(values.get("page_url", ""), str)
        else "",
        "site": values.get("site", "")
        if isinstance(values.get("site", ""), str)
        else "",
        "correlation_id": values.get("correlation_id", "")
        if isinstance(values.get("correlation_id", ""), str)
        else "",
        "consent_granted": bool(values.get("consent_granted", False)),
    }


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
        safe_meta = _safe_request_meta(meta)

        payload = {
            "event_type": event_type,
            "placement": placement,
            "creative": creative,
            "campaign": campaign,
            "user": user,
            "request_meta": safe_meta,
            "page_url": safe_meta["page_url"],
            "referrer_url": safe_meta["referrer"],
            "site_domain": safe_meta["site"],
            "consent_granted": safe_meta["consent_granted"],
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
    except DatabaseError:
        logger.warning("record_event failed: database unavailable", exc_info=True)
