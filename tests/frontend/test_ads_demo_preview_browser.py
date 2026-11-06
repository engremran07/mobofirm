from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from apps.ads.models import AdUnit
from tests.frontend.browser_support import collect_console


@pytest.mark.django_db(transaction=True)
def test_staff_ad_preview_is_responsive_and_inert(live_server):
    manager = get_user_model().objects.create_user(
        username="browser-ad-preview-manager",
        email="browser-ad-preview-manager@example.test",
        password="test-password",
        is_staff=True,
    )
    django_client = Client()
    django_client.force_login(manager)
    session_cookie = django_client.cookies[settings.SESSION_COOKIE_NAME].value
    with patch(
        "apps.ads.services.scanner.scan_templates_for_placements",
        return_value={"status": "success", "scanned": 0, "discoveries": 0},
    ):
        call_command("setup_test_environment")
    errors: list[str] = []
    warnings: list[str] = []
    external_requests: list[str] = []

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as error:
            pytest.fail(
                "Chromium is required for ad preview browser checks. "
                "Install it with "
                "`.venv\\Scripts\\python.exe -m playwright install chromium`.\n"
                f"{error}"
            )

        context = browser.new_context(viewport={"width": 390, "height": 844})
        context.add_cookies(
            [
                {
                    "name": settings.SESSION_COOKIE_NAME,
                    "value": session_cookie,
                    "url": live_server.url,
                }
            ]
        )
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        collect_console(page, errors, warnings)
        page.on(
            "request",
            lambda request: (
                external_requests.append(request.url)
                if not request.url.startswith(live_server.url)
                else None
            ),
        )
        page.goto(
            f"{live_server.url}{reverse('ads:demo_preview')}",
            wait_until="load",
        )

        assert page.get_by_role("heading", name="Ad slot preview").is_visible()
        assert page.locator("article[data-ad-format]").count() == len(AdUnit.AD_FORMATS)
        assert page.locator("[data-demo-preview='true']").count() == len(
            AdUnit.AD_FORMATS
        )
        demo_copy = page.locator(".ad-demo-card p").first.inner_text()
        assert "No provider, impression, click, or reward is triggered." in demo_copy
        for width, height in ((390, 844), (1365, 900)):
            page.set_viewport_size({"width": width, "height": height})
            dimensions = page.evaluate(
                """() => ({
                    viewport: document.documentElement.clientWidth,
                    document: document.documentElement.scrollWidth
                })"""
            )
            assert dimensions["document"] <= dimensions["viewport"] + 1

        assert not errors, f"Browser errors: {errors}"
        assert not external_requests, f"External requests: {external_requests}"
        browser.close()

    assert not warnings, f"Browser warnings: {warnings}"
