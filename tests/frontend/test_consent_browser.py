"""Consent banner, privacy centre and CSP in a real browser."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.consent import services
from apps.site_settings.models import SiteSettings
from tests.frontend.browser_support import (
    WIDTHS,
    chromium,
    overflow,
    ready,
    watch,
)

BANNER = "section.consent-banner"


def block_google(page) -> None:
    """Analytics hosts are allowed by the CSP after consent; never hit the network."""
    page.route("https://*.googletagmanager.com/**", lambda route: route.abort())
    page.route("https://*.google-analytics.com/**", lambda route: route.abort())


@pytest.mark.django_db(transaction=True)
def test_banner_works_at_every_width_without_console_csp_or_overflow_problems(
    live_server,
):
    failures: list[str] = []
    with chromium() as browser:
        for width in WIDTHS:
            page = browser.new_context(
                viewport={"width": width, "height": 800}
            ).new_page()
            watch(page, failures)
            page.goto(f"{live_server.url}/")
            ready(page)
            banner = page.locator(BANNER)
            assert banner.is_visible(), f"banner missing at {width}px"
            box = banner.bounding_box()
            assert box and box["x"] >= 0 and box["x"] + box["width"] <= width
            dimensions = overflow(page)
            assert dimensions["document"] <= dimensions["viewport"] + 1
            assert banner.get_by_role("button", name="Accept all").is_visible()
            assert banner.get_by_role("button", name="Reject optional").is_visible()
            page.context.close()
    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_reject_is_remembered_and_the_footer_link_reopens_the_choice(live_server):
    failures: list[str] = []
    with chromium() as browser:
        context = browser.new_context(viewport={"width": 1365, "height": 800})
        page = context.new_page()
        watch(page, failures)
        page.goto(f"{live_server.url}/")
        ready(page)
        page.locator(BANNER).get_by_role("button", name="Reject optional").click()
        page.wait_for_load_state("load")
        assert page.locator(BANNER).count() == 0
        cookie = next(
            c for c in context.cookies() if c["name"] == services.cookie_name()
        )
        assert cookie["httpOnly"] and cookie["sameSite"] == "Lax"
        assert not page.evaluate("document.cookie").count(services.cookie_name())

        page.reload()
        assert page.locator(BANNER).count() == 0

        page.get_by_role("link", name="Cookie settings").click()
        page.wait_for_url(f"**{reverse('consent:privacy_centre')}")
        assert page.get_by_role("heading", name="Privacy centre").is_visible()
        assert not page.locator("#consent-analytics").is_checked()
        page.locator("#consent-analytics").check()
        page.get_by_role("button", name="Save my choices").click()
        page.wait_for_load_state("load")
        assert page.locator("#consent-analytics").is_checked()
        assert not page.locator("#consent-advertising").is_checked()
        assert page.locator("#consent-necessary").is_disabled()
    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_customise_expands_the_choices_inside_the_banner(live_server):
    failures: list[str] = []
    with chromium() as browser:
        page = browser.new_context(viewport={"width": 360, "height": 640}).new_page()
        watch(page, failures)
        page.goto(f"{live_server.url}/")
        ready(page)
        banner = page.locator(BANNER)
        toggle = banner.get_by_role("link", name="Customise")
        assert toggle.get_attribute("aria-expanded") == "false"
        assert not banner.locator("#consent-banner-analytics").is_visible()
        toggle.click()
        assert page.url.rstrip("/") == live_server.url.rstrip("/")
        assert toggle.get_attribute("aria-expanded") == "true"
        banner.locator("#consent-banner-analytics").check()
        banner.get_by_role("button", name="Save my choices").click()
        page.wait_for_load_state("load")
        assert page.locator(BANNER).count() == 0
        page.goto(f"{live_server.url}{reverse('consent:privacy_centre')}")
        assert page.locator("#consent-analytics").is_checked()
        assert not page.locator("#consent-advertising").is_checked()
    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_analytics_script_loads_only_after_consent_and_csp_stays_strict(live_server):
    row = SiteSettings.get_solo()
    row.google_analytics_id = "G-ABCD1234"
    row.save()
    failures: list[str] = []
    with chromium() as browser:
        page = browser.new_context(viewport={"width": 1365, "height": 800}).new_page()
        watch(page, failures)
        block_google(page)
        response = page.goto(f"{live_server.url}/")
        ready(page)
        assert response is not None
        csp = response.headers["content-security-policy"]
        assert "googletagmanager" not in csp
        assert page.locator("script[src*='site_settings/analytics.js']").count() == 0
        assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]

        page.locator(BANNER).get_by_role("button", name="Accept all").click()
        page.wait_for_load_state("load")
        assert page.locator("script[src*='site_settings/analytics.js']").count() == 1

        page.goto(f"{live_server.url}{reverse('consent:privacy_centre')}")
        page.get_by_role("button", name="Reject optional").click()
        page.wait_for_load_state("load")
        assert page.locator("script[src*='site_settings/analytics.js']").count() == 0
    assert not failures, "\n".join(failures)
