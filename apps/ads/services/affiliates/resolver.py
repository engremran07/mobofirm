from __future__ import annotations

from apps.ads.models import AdsSettings, AffiliateLink, AffiliateSource


def affiliate_enabled() -> bool:
    try:
        ss = AdsSettings.get_solo()
        return bool(getattr(ss, "affiliate_enabled", False))
    except Exception:
        return False


def resolve_link(name: str, source_name: str) -> str | None:
    if not affiliate_enabled():
        return None
    source = AffiliateSource.objects.filter(name=source_name, is_enabled=True).first()
    if not source:
        return None
    link = AffiliateLink.objects.filter(
        source=source, name=name, is_enabled=True
    ).first()
    return link.url if link else None
