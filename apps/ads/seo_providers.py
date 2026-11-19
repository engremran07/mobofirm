"""Ads contribution to ``/ads.txt`` (registered with ``apps.seo`` on startup).

A line is published only for a network that is switched on and has a real publisher
ID; with nothing configured the site serves no ``ads.txt`` at all.
"""

from __future__ import annotations

import re

from django.db import DatabaseError

from apps.seo.wellknown import register_ads_lines

from .models import AdsSettings

# Google's certification authority ID is fixed and published in its ads.txt guide.
GOOGLE_CERTIFICATION_ID = "f08c47fec0942fa0"
_ADSENSE_ID = re.compile(r"^(?:ca-)?(pub-\d{10,20})$")


def ads_txt_lines() -> list[str]:
    try:
        row = AdsSettings.get_solo()
    except DatabaseError:
        return []
    if not (row.ads_enabled and row.adsense_enabled):
        return []
    match = _ADSENSE_ID.match(row.adsense_publisher_id.strip())
    if match is None:
        return []
    return [f"google.com, {match.group(1)}, DIRECT, {GOOGLE_CERTIFICATION_ID}"]


register_ads_lines("ads.txt", ads_txt_lines)
