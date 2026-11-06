"""Ads context processor — injects AdsSettings + enabled networks into every template."""

from __future__ import annotations

import logging
from typing import Any

from django.db import DatabaseError
from django.http import HttpRequest

from apps.ads.models import AdNetwork, AdsSettings

logger = logging.getLogger(__name__)


def ads_context(request: HttpRequest) -> dict[str, Any]:
    """Provide ads configuration to all templates.

    Injects:
    - ``ads_settings``: The AdsSettings singleton (or ``None`` on error).
    - ``ads_enabled_networks``: List of enabled AdNetwork objects with their
      ``header_script`` and ``body_script`` for injection into ``<head>``/``</body>``.
    - ``ads_show``: Quick boolean — ``True`` when ads should render for the
      current user/page.
    """
    try:
        settings_obj = AdsSettings.get_solo()
    except DatabaseError:
        logger.debug("ads_context: AdsSettings unavailable", exc_info=True)
        return {
            "ads_settings": None,
            "ads_enabled_networks": [],
            "ads_show": False,
        }

    show = settings_obj.should_show_ads(
        user=getattr(request, "user", None),
        page_url=request.path,
    )

    networks: list[AdNetwork] = []
    if show and settings_obj.ad_networks_enabled:
        try:
            networks = list(
                AdNetwork.objects.filter(is_enabled=True, is_deleted=False)
                .exclude(header_script="", body_script="")
                .only("name", "network_type", "header_script", "body_script")
                .order_by("-priority")
            )
        except DatabaseError:
            logger.debug("ads_context: AdNetwork query failed", exc_info=True)

    return {
        "ads_settings": settings_obj,
        "ads_enabled_networks": networks,
        "ads_show": show,
    }
