from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils.text import slugify

from apps.ads.constants import LOCAL_DEMO_CAMPAIGN_NAME, LOCAL_DEMO_PLACEMENT_PREFIX
from apps.ads.models import (
    AdCreative,
    AdNetwork,
    AdPlacement,
    AdUnit,
    AffiliateProvider,
    Campaign,
    PlacementAssignment,
    RewardedAdConfig,
)


class Command(BaseCommand):
    help = (
        "Create local-only, disabled ad-format demo profiles and pending "
        "template placement discoveries."
    )

    def handle(self, *args, **options) -> None:
        self._require_local_database()

        from apps.ads.services.scanner import scan_templates_for_placements

        with transaction.atomic():
            network, _ = AdNetwork.objects.get_or_create(
                name="Mobofirm Local Demo Network",
                defaults={
                    "network_type": "custom",
                    "is_enabled": False,
                    "supports_display": True,
                    "supports_native": True,
                    "supports_video": True,
                    "supports_rewarded": True,
                    "supports_auto_ads": True,
                    "config": {"demo_only": True, "serving_disabled": True},
                },
            )
            network.is_enabled = False
            network.api_key = ""
            network.api_secret = ""
            network.header_script = ""
            network.body_script = ""
            network.save(
                update_fields=[
                    "is_enabled",
                    "api_key",
                    "api_secret",
                    "header_script",
                    "body_script",
                ]
            )

            campaign, _ = Campaign.objects.get_or_create(
                name=LOCAL_DEMO_CAMPAIGN_NAME,
                defaults={"type": "house", "is_active": False, "locked": True},
            )
            campaign.type = "house"
            campaign.is_active = False
            campaign.locked = True
            campaign.save(update_fields=["type", "is_active", "locked"])

            affiliate_campaign, _ = Campaign.objects.get_or_create(
                name="Mobofirm Local Demo Affiliate Campaign",
                defaults={
                    "type": "affiliate",
                    "ad_network": "demo-only",
                    "is_active": False,
                    "locked": True,
                },
            )
            affiliate_campaign.is_active = False
            affiliate_campaign.save(update_fields=["is_active"])

            provider, _ = AffiliateProvider.objects.get_or_create(
                name="Mobofirm Local Demo Affiliate Provider",
                defaults={"provider_type": "custom", "is_enabled": False},
            )
            provider.is_enabled = False
            provider.api_key = ""
            provider.api_secret = ""
            provider.save(update_fields=["is_enabled", "api_key", "api_secret"])

            rewarded_placement = None
            for format_code, format_label in AdUnit.AD_FORMATS:
                slug = slugify(format_code)
                placement_code = f"{LOCAL_DEMO_PLACEMENT_PREFIX}{slug}"
                placement, _ = AdPlacement.objects.get_or_create(
                    code=placement_code,
                    defaults={
                        "name": f"Local Demo {format_label}",
                        "slug": f"local-{LOCAL_DEMO_PLACEMENT_PREFIX.rstrip('-')}-{slug}",
                        "description": "Disabled local test placement.",
                        "allowed_types": format_code,
                        "is_enabled": False,
                        "is_active": False,
                    },
                )
                placement.is_enabled = False
                placement.is_active = False
                placement.name = f"Local Demo {format_label}"
                placement.slug = (
                    f"local-{LOCAL_DEMO_PLACEMENT_PREFIX.rstrip('-')}-{slug}"
                )
                placement.description = (
                    "Disabled local demo placement; not eligible for public serving."
                )
                placement.allowed_types = format_code
                placement.save(
                    update_fields=[
                        "name",
                        "slug",
                        "description",
                        "allowed_types",
                        "is_enabled",
                        "is_active",
                    ]
                )

                creative, _ = AdCreative.objects.get_or_create(
                    campaign=campaign,
                    name=f"Local Demo {format_label}",
                    defaults={"creative_type": "html"},
                )
                creative.creative_type = "html"
                creative.html = (
                    f'<aside class="ad-demo-card" data-ad-format="{format_code}" '
                    'aria-label="Local demo advertisement">'
                    '<span class="ad-demo-card__label">Local demo advertisement</span>'
                    f"<strong>{format_label}</strong>"
                    "<p>Rendered from the saved demo creative. No provider, "
                    "impression, click, or reward is triggered.</p>"
                    "</aside>"
                )
                creative.is_enabled = False
                creative.is_active = False
                creative.save(
                    update_fields=[
                        "creative_type",
                        "html",
                        "is_enabled",
                        "is_active",
                    ]
                )

                assignment, _ = PlacementAssignment.objects.get_or_create(
                    placement=placement,
                    creative=creative,
                    defaults={"is_enabled": False, "is_active": False},
                )
                assignment.weight = 1
                assignment.is_enabled = False
                assignment.is_active = False
                assignment.save(update_fields=["weight", "is_enabled", "is_active"])

                unit, _ = AdUnit.objects.get_or_create(
                    network=network,
                    unit_id=f"mobofirm-local-demo-{slug}",
                    defaults={
                        "name": f"Local Demo {format_label}",
                        "ad_format": format_code,
                        "is_enabled": False,
                        "render_code": "",
                    },
                )
                unit.name = f"Local Demo {format_label}"
                unit.ad_format = format_code
                unit.is_enabled = False
                unit.render_code = ""
                unit.save(
                    update_fields=["name", "ad_format", "is_enabled", "render_code"]
                )
                if format_code == "rewarded":
                    rewarded_placement = placement

            if rewarded_placement:
                rewarded_config, _ = RewardedAdConfig.objects.get_or_create(
                    name="Mobofirm Local Demo Rewarded Ad",
                    defaults={
                        "placement": rewarded_placement,
                        "network": network,
                        "reward_type": "download",
                        "reward_description": "Local demo only; no reward is granted.",
                        "is_enabled": False,
                        "min_watch_seconds": 15,
                    },
                )
                rewarded_config.is_enabled = False
                rewarded_config.save(update_fields=["is_enabled"])

            scan_result = scan_templates_for_placements()
            if scan_result.get("status") != "success":
                raise CommandError(
                    "Template placement scan failed: "
                    f"{scan_result.get('reason', 'unknown error')}"
                )

        self.stdout.write(
            self.style.SUCCESS(
                "Local demo profiles are ready: "
                f"{len(AdUnit.AD_FORMATS)} supported ad formats, "
                "affiliate provider, and rewarded configuration. "
                "All demo serving is disabled."
            )
        )
        self.stdout.write(
            "Template scan created or refreshed pending review discoveries: "
            f"{scan_result['discoveries']} across {scan_result['scanned']} templates. "
            "No blogs, firmware files, or live ad placements were created."
        )

    @staticmethod
    def _require_local_database() -> None:
        if connection.vendor == "sqlite":
            return
        host = connection.settings_dict.get("HOST") or ""
        if host not in {"localhost", "127.0.0.1", "::1"}:
            raise CommandError(
                "Demo ad setup is limited to a local database; refusing "
                "the configured non-local database host."
            )
