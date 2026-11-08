"""Browser checks for the ads staff screens and the public ad zones.

The manager, queue and dashboard render in the Unfold admin shell without console/CSP
errors or overflow (light and dark, 360/768/1365). The place flow, test profile and a real
consented zone are exercised end to end with real interaction.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse
from playwright.sync_api import expect

from apps.ads.models import (
    AdCreative,
    AdPlacement,
    AdsSettings,
    AutoAdsScanResult,
    Campaign,
    PlacementAssignment,
    ScanDiscovery,
)
from tests.frontend.browser_support import (
    WIDTHS,
    chromium,
    dismiss_dialogs,
    login_cookie,
    make_user,
    overflow,
    ready,
    session_context,
    visit_all,
    watch,
)

PAGE = "firmwares/library_hub.html"
BANNER = "section.consent-banner"


def ads_manager(username: str):
    user = make_user(username, staff=True)
    user.user_permissions.add(
        *Permission.objects.filter(
            codename__in=["view_adplacement", "change_adplacement", "view_campaign"]
        )
    )
    return user


def seed_scans() -> ScanDiscovery:
    result = AutoAdsScanResult.objects.create(
        template_path=PAGE, template_type="page", score=82, estimated_traffic="high"
    )
    AutoAdsScanResult.objects.create(
        template_path="blog/post_detail.html", template_type="detail", score=70
    )
    return ScanDiscovery.objects.create(
        scan_result=result,
        placement_code="firmwares-library-hub-above-footer",
        placement_name="Library Hub Above Footer",
        confidence=60,
        rationale="Catch users who read to the end",
    )


def seed_serving_ad() -> AdPlacement:
    row = AdsSettings.get_solo()
    row.ads_enabled = True
    row.save()
    campaign = Campaign.objects.create(name="Browser house", type="house")
    creative = AdCreative.objects.create(
        campaign=campaign,
        name="Browser house ad",
        creative_type="html",
        html='<p class="ad-zone__house">Browser house ad copy</p>',
    )
    placement = AdPlacement.objects.create(
        name="Browser header",
        code="browser-header",
        slug="browser-header",
        template_reference=PAGE,
        zone="header",
        allowed_sizes="728x90",
    )
    PlacementAssignment.objects.create(placement=placement, creative=creative)
    return placement


@pytest.mark.django_db(transaction=True)
def test_ads_staff_screens_render_in_the_admin_shell_light_and_dark(live_server):
    user = ads_manager("ads_shell")
    seed_scans()
    cookie = login_cookie(user)
    paths = [
        reverse("ads:dashboard"),
        reverse("ads:placement_manager"),
        reverse("ads:discoveries"),
    ]
    failures: list[str] = []
    with chromium() as browser:
        for scheme in ("light", "dark"):
            context = session_context(browser, live_server, cookie)
            context.add_init_script(
                f"window.localStorage.setItem('adminTheme', '\"{scheme}\"')"
            )
            page = context.new_page()
            watch(page, failures)
            visit_all(page, live_server, paths, failures)
            page.set_viewport_size({"width": 1365, "height": 900})
            for path in paths:
                page.goto(f"{live_server.url}{path}")
                ready(page)
                if page.locator("nav a[href]").count() < 3:
                    failures.append(f"{path}: admin sidebar is missing ({scheme})")
                if page.locator(".site-header, .site-container").count():
                    failures.append(f"{path}: public shell markup rendered")
                is_dark = page.evaluate(
                    "() => document.documentElement.classList.contains('dark')"
                )
                if is_dark != (scheme == "dark"):
                    failures.append(f"{path}: expected {scheme} theme")
            context.close()

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_place_enable_and_remove_with_app_owned_dialogs(live_server):
    user = ads_manager("ads_place")
    seed_scans()
    cookie = login_cookie(user)
    failures: list[str] = []
    native: list[str] = []
    with chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        watch(page, failures)
        dismiss_dialogs(page, native)
        page.goto(f"{live_server.url}{reverse('ads:placement_manager')}")
        ready(page)

        row = page.get_by_role("row").filter(has_text=PAGE)
        row.get_by_role("link", name="Place ad here").click()
        dialog = page.locator("dialog[data-staff-dialog]")
        expect(dialog).to_be_visible(timeout=10_000)
        expect(dialog.get_by_role("heading")).to_have_text("Place an ad")
        page.keyboard.press("Escape")
        expect(page.locator("dialog[data-staff-dialog]")).to_have_count(0)

        row.get_by_role("link", name="Place ad here").click()
        dialog = page.locator("dialog[data-staff-dialog]")
        expect(dialog).to_be_visible(timeout=10_000)
        dialog.get_by_label("Zone").select_option("header")
        dialog.get_by_role("button", name="Create placement").click()
        page.wait_for_url(f"**{reverse('ads:placement_manager')}")
        expect(page.get_by_text("saved.")).to_be_visible()

        row = page.get_by_role("row").filter(has_text=PAGE)
        expect(row.get_by_text("Off", exact=True).first).to_be_visible()
        row.get_by_role("button", name="On", exact=True).click()
        page.wait_for_url(f"**{reverse('ads:placement_manager')}")
        row = page.get_by_role("row").filter(has_text=PAGE)
        expect(row.get_by_text("On", exact=True).first).to_be_visible()

        row.get_by_role("link", name="Remove").click()
        dialog = page.locator("dialog[data-staff-dialog]")
        expect(dialog).to_be_visible(timeout=10_000)
        expect(dialog).to_contain_text("stops serving at once")
        dimensions = overflow(page)
        assert dimensions["document"] <= dimensions["viewport"] + 1
        dialog.get_by_role("button", name="Remove placement").click()
        page.wait_for_url(f"**{reverse('ads:placement_manager')}")
        expect(page.get_by_text("The placement was removed.")).to_be_visible()

    assert not failures, "\n".join(failures)
    assert native == []
    assert AdPlacement.objects.filter(is_deleted=False).count() == 0


@pytest.mark.django_db(transaction=True)
def test_discovery_queue_approves_and_rejects_through_dialogs(live_server):
    user = ads_manager("ads_queue")
    discovery = seed_scans()
    cookie = login_cookie(user)
    failures: list[str] = []
    with chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        watch(page, failures)
        page.goto(f"{live_server.url}{reverse('ads:discoveries')}")
        ready(page)

        page.get_by_label("Select Library Hub Above Footer").check()
        page.get_by_role("button", name="Reject selected").click()
        dialog = page.locator("dialog[data-staff-dialog]")
        expect(dialog).to_be_visible(timeout=10_000)
        expect(dialog).to_contain_text("Reject 1 suggestion?")
        dialog.get_by_role("button", name="Cancel").click()
        expect(page.locator("dialog[data-staff-dialog]")).to_have_count(0)

        page.get_by_role("button", name="Approve", exact=True).first.click()
        page.wait_for_url(f"**{reverse('ads:discoveries')}")
        expect(page.get_by_text("Suggestion approved")).to_be_visible()

    assert not failures, "\n".join(failures)
    discovery.refresh_from_db()
    assert discovery.status == "approved"
    assert AdPlacement.objects.filter(template_reference=PAGE, zone="footer").exists()


@pytest.mark.django_db(transaction=True)
def test_test_profile_marks_zones_for_staff_only_without_layout_overflow(live_server):
    user = ads_manager("ads_profile")
    seed_scans()
    seed_serving_ad()
    cookie = login_cookie(user)
    failures: list[str] = []
    with chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        watch(page, failures)
        page.goto(f"{live_server.url}{reverse('ads:placement_manager')}")
        ready(page)
        page.get_by_role("button", name="Test profile: off (switch on)").click()
        expect(page.get_by_text("Test profile on: every public page")).to_be_visible()

        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(f"{live_server.url}/")
            ready(page)
            zones = page.locator("[data-ad-zone]")
            assert zones.count() >= 3, f"zones missing at {width}px"
            expect(page.locator("[data-ad-test='test']").first).to_contain_text(
                "TEST AD"
            )
            expect(page.locator("[data-ad-test='test']").first).to_contain_text(
                "zone header"
            )
            dimensions = overflow(page)
            assert dimensions["document"] <= dimensions["viewport"] + 1, width

        anonymous = browser.new_context(viewport={"width": 1365, "height": 900})
        visitor = anonymous.new_page()
        watch(visitor, failures)
        visitor.goto(f"{live_server.url}/")
        ready(visitor)
        assert visitor.locator("[data-ad-zone]").count() == 0
        assert "TEST AD" not in visitor.content()

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_real_zone_serves_only_after_advertising_consent_and_counts_it(live_server):
    from apps.ads.models import AdEvent

    seed_serving_ad()
    failures: list[str] = []
    with chromium() as browser:
        page = browser.new_context(viewport={"width": 360, "height": 800}).new_page()
        watch(page, failures)
        page.goto(f"{live_server.url}/")
        ready(page)
        assert page.locator("[data-ad-zone]").count() == 0

        page.locator(BANNER).get_by_role("button", name="Accept all").click()
        page.wait_for_load_state("load")
        page.goto(f"{live_server.url}/")
        ready(page)
        zone = page.locator("[data-ad-zone='header']")
        expect(zone).to_be_visible()
        expect(zone.get_by_text("Advertisement", exact=True)).to_be_visible()
        expect(zone).to_contain_text("Browser house ad copy")
        dimensions = overflow(page)
        assert dimensions["document"] <= dimensions["viewport"] + 1

    assert not failures, "\n".join(failures)
    assert AdEvent.objects.filter(event_type="impression").count() >= 1
