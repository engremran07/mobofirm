"""Seed a paused house campaign and draft placements to try the ad zones with.

Everything is created switched off (campaign inactive, placements off) and the master ads
switch is never touched, so running this on any site serves nothing. To see it: switch the
staff *test profile* on in the placement manager, or turn the campaign and the placements
on deliberately. Safe to run repeatedly.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.ads.models import AdCreative, AdPlacement, Campaign, PlacementAssignment
from apps.ads.services import placement_manager

CAMPAIGN_NAME = "Test profile (house)"
# (template path or "" for site-wide, zone) pairs covering the main zones.
TARGETS: tuple[tuple[str, str], ...] = (
    ("", "header"),
    ("", "after_content"),
    ("", "footer"),
    ("blog/post_detail.html", "sidebar"),
    ("blog/post_detail.html", "in_content"),
    ("firmwares/firmware_detail.html", "sidebar"),
    ("firmwares/firmware_detail.html", "in_content"),
    ("blog/post_list.html", "between_items"),
    ("firmwares/browse.html", "between_items"),
)
CREATIVES: dict[str, str] = {
    "728x90": '<div class="ad-zone__house">Mobofirm house ad &middot; 728x90</div>',
    "300x250": '<div class="ad-zone__house">Mobofirm house ad &middot; 300x250</div>',
}


class Command(BaseCommand):
    help = "Create a paused house campaign, creatives and draft zone placements (idempotent)."

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        campaign, campaign_created = Campaign.objects.get_or_create(
            name=CAMPAIGN_NAME,
            defaults={"type": "house", "is_active": False, "priority": 0},
        )
        creatives: dict[str, AdCreative] = {}
        for size, markup in CREATIVES.items():
            creative, _ = AdCreative.objects.get_or_create(
                campaign=campaign,
                name=f"House {size}",
                defaults={
                    "creative_type": "html",
                    "html": markup,
                    "is_enabled": True,
                },
            )
            creatives[size] = creative
        created = linked = skipped = 0
        for template_path, zone_code in TARGETS:
            declared = (
                placement_manager.declared_zones(template_path)
                if template_path
                else placement_manager.site_wide_zones()
            )
            if zone_code not in declared:
                skipped += 1
                continue
            placement = AdPlacement.objects.filter(
                template_reference=template_path, zone=zone_code, is_deleted=False
            ).first()
            if placement is None:
                placement = placement_manager.place_ad(
                    user=None,
                    template_path=template_path,
                    zone_code=zone_code,
                    enabled=False,
                )
                created += 1
            size = placement_manager.clean_sizes(
                placement.allowed_sizes, placement_manager.ZONE_BY_CODE[zone_code]
            ).split(",")[0]
            creative = creatives.get(size) or creatives["300x250"]
            _, was_linked = PlacementAssignment.objects.get_or_create(
                placement=placement, creative=creative
            )
            linked += int(was_linked)
        self.stdout.write(
            self.style.SUCCESS(
                f"Test profile ready: campaign {'created' if campaign_created else 'kept'} "
                f"(paused), {created} placements created, {linked} creatives linked, "
                f"{skipped} zones skipped (not declared). Nothing is switched on."
            )
        )
