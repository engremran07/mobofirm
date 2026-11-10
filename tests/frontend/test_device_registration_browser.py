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


def platform_authenticator_stub(available: bool) -> str:
    """Make the host's Windows Hello/Touch ID state deterministic for the page."""
    return (
        "PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable = "
        f"() => Promise.resolve({str(available).lower()});"
    )


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
        context.add_init_script(platform_authenticator_stub(False))
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

        dialog = page.get_by_role("dialog", name="Review this computer")
        assert dialog.is_visible()
        assert dialog.get_by_text("Windows computer · Chrome").is_visible()
        assert dialog.get_by_text("Approved devices", exact=True).is_visible()
        # Without a platform authenticator the browser-only action stays the primary one.
        assert "btn-primary" in (
            dialog.get_by_role(
                "button", name="Register this browser only"
            ).get_attribute("class")
            or ""
        )
        for width, height in ((390, 844), (1365, 900)):
            page.set_viewport_size({"width": width, "height": height})
            dimensions = page.evaluate(
                """() => ({
                    viewport: document.documentElement.clientWidth,
                    document: document.documentElement.scrollWidth
                })"""
            )
            assert dimensions["document"] <= dimensions["viewport"] + 1

        page.get_by_role("button", name="Register this browser only").click()
        page.get_by_text(
            "This browser profile is now registered to your account."
        ).wait_for()
        assert not page.get_by_role("dialog", name="Review this computer").is_visible()
        assert not errors
        browser.close()

    device.refresh_from_db()
    assert device.is_trusted


VIEWPORTS = ((1280, 720), (800, 500), (360, 500))
EDGE = 8


def _inside(box, width, height, margin=EDGE):
    return (
        box["x"] >= margin
        and box["y"] >= margin
        and box["x"] + box["width"] <= width - margin
        and box["y"] + box["height"] <= height - margin
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "os_available", [True, False], ids=["os-credential", "browser-only"]
)
def test_device_dialog_fits_every_viewport_scrolls_drags_and_closes_on_escape(
    live_server, os_available
):
    user = get_user_model().objects.create_user(
        username="browser-device-dialog-layout",
        email="browser-device-dialog-layout@example.com",
        password="Pass-12345!",
    )
    EmailAddress.objects.create(
        user=user, email=user.email, primary=True, verified=True
    )
    browser_key = "dialog-layout-profile-key-for-playwright-test-1"
    Device.objects.create(
        user=user,
        browser_key_hash=hash_browser_key(browser_key),
        is_trusted=False,
        browser_family="Chrome",
        os_family="Windows",
        display_name="Windows computer · Chrome",
    )
    client = Client()
    client.force_login(user)
    session = client.session
    session["device_registration_prompt"] = {
        "can_approve": True,
        "device_name": "Windows computer · Chrome",
        "os_family": "Windows",
        "browser_family": "Chrome",
        "active_devices": 0,
        "max_devices": 3,
    }
    session.save()
    cookies = [
        {
            "name": settings.SESSION_COOKIE_NAME,
            "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
            "url": live_server.url,
        },
        {"name": "mobofirm_device", "value": browser_key, "url": live_server.url},
    ]
    errors: list[str] = []

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as error:
            pytest.fail(
                "Chromium is required for device-registration layout checks. "
                "Install it with `.venv\\Scripts\\python.exe -m playwright install chromium`.\n"
                f"{error}"
            )
        for width, height in VIEWPORTS:
            context = browser.new_context(viewport={"width": width, "height": height})
            context.add_init_script(platform_authenticator_stub(os_available))
            context.add_cookies(cookies)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"{live_server.url}{reverse('devices:my_devices')}")
            dialog = page.get_by_role("dialog", name="Review this computer")
            dialog.wait_for(state="visible")
            label = f"{width}x{height}"

            # Fully inside the viewport, centred by the browser (not bottom/right).
            box = dialog.bounding_box()
            assert _inside(box, width, height), (label, box)
            assert abs((box["x"] + box["width"] / 2) - width / 2) <= 16, (label, box)
            assert abs((box["y"] + box["height"] / 2) - height / 2) <= 2, (label, box)
            primary = dialog.get_by_role("button", name="Register this computer")
            browser_only = dialog.get_by_role(
                "button", name="Register this browser only"
            )
            browser_class = browser_only.get_attribute("class") or ""
            if os_available:
                assert primary.is_visible(), label
                assert "btn-primary" in (primary.get_attribute("class") or ""), label
                assert "btn-primary" not in browser_class, label
            else:
                assert primary.count() == 0 or not primary.is_visible(), label
                assert "btn-primary" in browser_class, label

            # Both actions are visible and actionable without scrolling the page.
            for name in ("Register this browser only", "Not now"):
                button = dialog.get_by_role("button", name=name)
                assert button.is_visible(), (label, name)
                assert _inside(button.bounding_box(), width, height, margin=0), (
                    label,
                    name,
                )
                button.click(trial=True)
            assert page.evaluate("window.scrollX + window.scrollY") == 0

            # Only the body scrolls when the content is taller than the dialog.
            body = dialog.locator(".device-registration-dialog__body")
            metrics = body.evaluate(
                "el => { el.scrollTop = el.scrollHeight; "
                "return {client: el.clientHeight, scroll: el.scrollHeight, top: el.scrollTop}; }"
            )
            if (width, height) == (360, 500):
                assert metrics["scroll"] > metrics["client"], (label, metrics)
            if metrics["scroll"] > metrics["client"]:
                assert metrics["top"] > 0, (label, metrics)
            assert _inside(dialog.bounding_box(), width, height), label
            assert _inside(
                dialog.get_by_role("button", name="Not now").bounding_box(),
                width,
                height,
                margin=0,
            ), label

            # Dragging the header moves the dialog but never takes it off-screen.
            handle = dialog.locator("[data-dialog-drag-handle]")
            before = dialog.bounding_box()
            grip = handle.bounding_box()
            start_x = grip["x"] + grip["width"] / 2
            start_y = grip["y"] + grip["height"] / 2
            page.mouse.move(start_x, start_y)
            page.mouse.down()
            page.mouse.move(start_x - 120, start_y + 120, steps=8)
            page.mouse.up()
            after = dialog.bounding_box()
            moved = abs(after["x"] - before["x"]) + abs(after["y"] - before["y"])
            assert moved >= 100, (label, before, after)
            assert after["y"] - before["y"] >= 100, (label, before, after)
            head = handle.bounding_box()
            assert head["x"] >= 0 and head["y"] >= 0, (label, head)
            assert head["x"] + head["width"] <= width + 1, (label, head)
            assert head["y"] + head["height"] <= height, (label, head)

            # Esc closes it.
            page.keyboard.press("Escape")
            assert not dialog.is_visible(), label
            context.close()
        browser.close()
    assert not errors
