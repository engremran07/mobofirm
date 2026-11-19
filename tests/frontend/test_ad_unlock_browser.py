"""Browser checks: pricing rows for ads and unlocks, and the unlock-by-ads panel flow."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from playwright.sync_api import expect

from apps.ads.models import AdPlacement, AdsSettings, RewardedAdConfig
from apps.consent import services as consent
from apps.firmwares.models import Brand, Model, OfficialFirmware, Variant
from apps.shop import entitlements
from apps.shop.models import PackageTier
from tests.frontend.browser_support import (
    dismiss_dialogs,
    login_cookie,
    make_user,
    overflow,
    session_context,
    watch,
)
from tests.frontend.catalog_browser_support import locked_chromium


def firmware(name: str, tier: str = "free") -> OfficialFirmware:
    brand, _ = Brand.objects.get_or_create(slug="acme", defaults={"name": "Acme"})
    model, _ = Model.objects.get_or_create(
        brand=brand, slug="phone-one", defaults={"name": "Phone One"}
    )
    variant, _ = Variant.objects.get_or_create(
        model=model, slug="global", defaults={"name": "Global"}
    )
    return OfficialFirmware.objects.create(
        original_file_name=name,
        stored_file_path=f"/firmware/{name}",
        brand=brand,
        model=model,
        variant=variant,
        is_verified=True,
        is_active=True,
        access_tier=tier,
    )


def seed_ladder() -> None:
    PackageTier.objects.create(
        name="Free",
        slug="free",
        is_default=True,
        duration_days=0,
        window="day",
        limit_files=1,
        unlock_by_ads_free=True,
        unlock_ads_free=2,
        unlock_cooldown_seconds=0,
        unlock_by_ads_featured=True,
        unlock_ads_featured=4,
        sort_order=1,
    )
    PackageTier.objects.create(
        name="Silver",
        slug="silver",
        price=Decimal(5),
        duration_days=30,
        window="package",
        limit_files=40,
        direct_links=True,
        unlock_by_ads_free=True,
        sort_order=2,
    )
    PackageTier.objects.create(
        name="Platinum",
        slug="platinum",
        price=Decimal(99),
        duration_days=365,
        bandwidth_enabled=False,
        limit_devices=False,
        applies_to_featured=True,
        applies_to_paid=True,
        ad_free=True,
        direct_links=True,
        sort_order=3,
    )


def seed_unlock_ad() -> None:
    settings_row = AdsSettings.get_solo()
    settings_row.ads_enabled = True
    settings_row.rewarded_ads_enabled = True
    settings_row.save()
    placement = AdPlacement.objects.create(name="Unlock", slug="unlock", code="unlock")
    RewardedAdConfig.objects.create(
        name="Download unlock",
        placement=placement,
        is_enabled=True,
        reward_type=RewardedAdConfig.RewardType.DOWNLOAD_UNLOCK,
        min_watch_seconds=5,
        cooldown_minutes=0,
        daily_limit_per_user=0,
        monthly_limit_per_user=0,
    )


@pytest.mark.django_db(transaction=True)
def test_pricing_rows_and_the_unlock_panel_flow(live_server) -> None:
    seed_ladder()
    seed_unlock_ad()
    member = make_user("unlock_browser")
    entitlements.consume_quota(member, firmware("used.zip"), reason="free")
    locked = firmware("locked.zip")
    cookies = login_cookie(member)
    consent_cookie = consent.encode_cookie(
        consent.active_policy().version, ["advertising"], uuid.uuid4()
    )
    file_url = f"{live_server.url}/firmwares/file/official/{locked.pk}/"
    failures: list[str] = []
    natives: list[str] = []

    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookies)
        context.add_cookies(
            [
                {
                    "name": consent.cookie_name(),
                    "value": consent_cookie,
                    "url": live_server.url,
                }
            ]
        )
        page = context.new_page()
        watch(page, failures)
        dismiss_dialogs(page, natives)

        for width in (360, 1280):
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(f"{live_server.url}/pricing/")
            expect(
                page.get_by_text("Watch 2 ads to unlock a file").first
            ).to_be_visible()
            expect(
                page.get_by_text("Download-gate ads, direct links").first
            ).to_be_visible()
            assert page.get_by_text("No ads, direct links").count() >= 1
            dimensions = overflow(page)
            assert dimensions["document"] <= dimensions["viewport"] + 1, width

            page.goto(file_url)
            panel = page.locator("#ad-unlock-panel")
            expect(panel).to_be_visible()
            expect(panel).to_contain_text("Watch 2 short ads to unlock this one file")
            dimensions = overflow(page)
            assert dimensions["document"] <= dimensions["viewport"] + 1, width

        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(file_url)
        page.locator("[data-unlock-start]").click()
        expect(page.locator("#ad-unlock-panel")).to_contain_text("0 of 2 ads watched")

        for number in (1, 2):
            page.locator("[data-unlock-watch]").click()
            page.wait_for_url("**/ads/rewarded/**")
            page.locator("#rewarded-start").click()
            page.locator("#rewarded-done").wait_for(state="visible", timeout=30_000)
            page.locator("#rewarded-done").click()
            page.wait_for_url(f"**/firmwares/file/official/{locked.pk}/")
            if number == 1:
                expect(page.locator("#ad-unlock-panel")).to_contain_text(
                    "1 of 2 ads watched"
                )

        ready = page.locator("#ad-unlock-panel")
        expect(ready).to_have_attribute("data-unlock-state", "ready")
        expect(ready.get_by_role("link", name="Go to the download")).to_be_visible()
        context.close()

    assert natives == []
    assert failures == []
