"""Dashboard numbers and the 'why is nothing showing?' checklist for ad operations."""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Count, Q
from django.http import HttpRequest

from apps.ads.models import (
    AdEvent,
    AdPlacement,
    AdsSettings,
    AutoAdsScanResult,
    PlacementAssignment,
    ScanDiscovery,
)
from apps.ads.services import zones as zone_service


@dataclass(frozen=True, slots=True)
class Check:
    label: str
    ok: bool
    detail: str
    url_name: str = ""


def _servable_placements() -> int:
    """Zone placements that are on and have an assignment a visitor could be shown."""
    live = PlacementAssignment.objects.filter(
        is_enabled=True,
        is_active=True,
        is_deleted=False,
        creative__is_enabled=True,
        creative__is_active=True,
        creative__is_deleted=False,
        creative__campaign__is_active=True,
        creative__campaign__is_deleted=False,
        placement__is_enabled=True,
        placement__is_active=True,
        placement__is_deleted=False,
    ).exclude(placement__zone="")
    return live.values("placement_id").distinct().count()


def overview() -> dict[str, object]:
    settings_row = AdsSettings.get_solo()
    zoned = AdPlacement.objects.filter(is_deleted=False).exclude(zone="")
    counts = zoned.aggregate(
        total=Count("id"), on=Count("id", filter=Q(is_enabled=True, is_active=True))
    )
    servable = _servable_placements()
    pending = ScanDiscovery.objects.filter(status="pending").count()
    scanned = AutoAdsScanResult.objects.count()
    events = AdEvent.objects.aggregate(
        impressions=Count("id", filter=Q(event_type="impression")),
        clicks=Count("id", filter=Q(event_type="click")),
    )
    checks = [
        Check(
            "Ads are switched on",
            settings_row.ads_enabled,
            "Visitors see ads only while the master switch is on."
            if settings_row.ads_enabled
            else "The master switch is off, so no ad renders anywhere.",
            "ads:dashboard",
        ),
        Check(
            "Templates scanned",
            scanned > 0,
            f"{scanned} templates scanned."
            if scanned
            else "Run a scan to list the templates that can carry ads.",
            "ads:placement_manager",
        ),
        Check(
            "Zones placed",
            counts["total"] > 0,
            f"{counts['total']} placements fill a layout zone ({counts['on']} switched on)."
            if counts["total"]
            else "No template has an ad placed yet.",
            "ads:placement_manager",
        ),
        Check(
            "Creatives assigned",
            servable > 0,
            f"{servable} placements have a live creative to show."
            if servable
            else "No switched-on placement has an enabled creative in an active campaign.",
            "admin:ads_placementassignment_changelist",
        ),
    ]
    return {
        "checks": checks,
        "ready": all(check.ok for check in checks),
        "placements_total": counts["total"],
        "placements_on": counts["on"],
        "servable": servable,
        "pending_discoveries": pending,
        "scanned": scanned,
        "impressions": events["impressions"],
        "clicks": events["clicks"],
        "ctr": round(events["clicks"] / events["impressions"] * 100, 2)
        if events["impressions"]
        else 0,
    }


def profile_state(request: HttpRequest) -> dict[str, object]:
    """Whether the staff test profile is on for this session and until when."""
    until = float(request.session.get(zone_service.PREVIEW_SESSION_KEY, 0) or 0)
    return {"on": zone_service.profile_enabled(request), "until": until or None}
