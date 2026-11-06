"""Conservative ads configuration for a freshly seeded site."""

from __future__ import annotations

from apps.core.seeding import SeedReport

from .models import AdsSettings

SWITCHES = (
    "ads_enabled",
    "affiliate_enabled",
    "ad_networks_enabled",
    "auto_ads_enabled",
    "rewarded_ads_enabled",
)


def ensure_ads_disabled() -> SeedReport:
    """Make sure no ad serving is switched on; no campaigns or networks are created."""
    report = SeedReport()
    row = AdsSettings.get_solo()
    changed = [name for name in SWITCHES if getattr(row, name)]
    for name in changed:
        setattr(row, name, False)
    if changed:
        row.save(update_fields=changed)
    report.record("ads settings switched off", created=bool(changed))
    return report
