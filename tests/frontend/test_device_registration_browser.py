import pytest
from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from apps.devices.models import Device
from apps.devices.services import hash_browser_key


@pytest.mark.django_db(transaction=True)
def test_new_browser_registration_prompt_is_accessible_and_functional(live_server):
    user = get_user_model().objects.create_user(
        username="browser-device-registration",
        email="browser-device-registration@example.com",
        password="SafePassword123!",
    )
    EmailAddress.objects.create(
        user=user,
        email=user.email,
        primary=True,
        verified=True,
    )
    browser_key = "browser-profile-key-for-playwright-test-123"
    device = Device.objects.create(
        user=user,
        browser_key_hash=hash_browser_key(browser_key),
        is_trusted=False,
        browser_family="Chrome",
        os_family="Windows",
        display_name="Windows computer · Chrome",
    )

    authenticated_client = Client()
    authenticated_client.force_login(user)
    session = authenticated_client.session
    session["device_registration_prompt"] = {
        "can_approve": True,
        "device_name": device.display_name,
        "os_family": device.os_family,
        "browser_family": device.browser_family,
        "active_devices": 0,
        "max_devices": 3,
    }
    session.save()
    session_cookie = authenticated_client.cookies[settings.SESSION_COOKIE_NAME]
    errors = []

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as error:
            pytest.fail(
                "Chromium is required for device-registration interaction checks. "
                "Install it with `.venv\\Scripts\\python.exe -m playwright install chromium`.\n"
                f"{error}"
            )

        context = browser.new_context(viewport={"width": 390, "height": 844})
        context.add_cookies(
            [
                {
                    "name": settings.SESSION_COOKIE_NAME,
                    "value": session_cookie.value,
                    "url": live_server.url,
                },
                {
                    "name": "mobofirm_device",
                    "value": browser_key,
                    "url": live_server.url,
                },
            ]
        )
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(
            f"{live_server.url}{reverse('devices:my_devices')}",
            wait_until="load",
        )

        dialog = page.get_by_role("dialog", name="Review this browser")
        assert dialog.is_visible()
        assert dialog.get_by_text("Windows computer · Chrome").is_visible()
        assert dialog.get_by_text("Approved devices", exact=True).is_visible()
        for width, height in ((390, 844), (1365, 900)):
            page.set_viewport_size({"width": width, "height": height})
            dimensions = page.evaluate(
                """() => ({
                    viewport: document.documentElement.clientWidth,
                    document: document.documentElement.scrollWidth
                })"""
            )
            assert dimensions["document"] <= dimensions["viewport"] + 1

        page.get_by_role("button", name="Register this browser").click()
        page.get_by_text(
            "This browser profile is now registered to your account."
        ).wait_for()
        assert not page.get_by_role("dialog", name="Review this browser").is_visible()
        assert not errors
        browser.close()

    device.refresh_from_db()
    assert device.is_trusted
