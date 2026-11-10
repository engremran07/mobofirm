"""Browser check of the OS registration against a CDP virtual platform authenticator.

Runs the built production bundle (``static/build/site.js``, under the strict CSP) on the
real server-rendered dialog: no injected scripts, no CSP bypass.
"""

from __future__ import annotations

import pytest
from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from apps.devices.models import Device, DeviceBrowserLink
from apps.devices.services import DEVICE_COOKIE_NAME, hash_browser_key

VIRTUAL_PLATFORM_AUTHENTICATOR = {
    "protocol": "ctap2",
    "transport": "internal",
    "hasResidentKey": True,
    "hasUserVerification": True,
    "isUserVerified": True,
    "automaticPresenceSimulation": True,
}


def signed_in_cookies(user, browser_key: str, *, pending: bool) -> dict[str, str]:
    """Session + device cookies for a browser; optionally with a pending fallback row."""
    client = Client()
    client.force_login(user)
    # force_login registers a cookie-less stray placeholder; this browser is explicit.
    Device.objects.filter(user=user, os_credential_id_hash="").delete()
    if pending:
        Device.objects.create(
            user=user,
            browser_key_hash=hash_browser_key(browser_key),
            is_trusted=False,
            display_name="Windows computer · Chrome",
            os_family="Windows",
            browser_family="Chrome",
        )
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
    return {
        settings.SESSION_COOKIE_NAME: client.cookies[
            settings.SESSION_COOKIE_NAME
        ].value,
        DEVICE_COOKIE_NAME: browser_key,
    }


def open_page(browser, live_server, cookies, errors):
    context = browser.new_context(viewport={"width": 1365, "height": 900})
    context.add_cookies(
        [
            {"name": name, "value": value, "url": live_server.url}
            for name, value in cookies.items()
        ]
    )
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console",
        lambda message: (
            errors.append(message.text) if message.type == "error" else None
        ),
    )
    cdp = context.new_cdp_session(page)
    cdp.send("WebAuthn.enable")
    authenticator = cdp.send(
        "WebAuthn.addVirtualAuthenticator", {"options": VIRTUAL_PLATFORM_AUTHENTICATOR}
    )["authenticatorId"]
    page.goto(f"{live_server.url}{reverse('devices:my_devices')}", wait_until="load")
    return context, page, cdp, authenticator


@pytest.mark.django_db(transaction=True)
def test_register_computer_then_switch_browser_without_a_new_registration(live_server):
    user = get_user_model().objects.create_user(
        username="os-identity-browser",
        email="os-identity-browser@example.com",
        password="Pass-12345!",
    )
    EmailAddress.objects.create(
        user=user, email=user.email, primary=True, verified=True
    )
    first_key = "first-browser-profile-key-for-playwright-test-1234"
    second_key = "second-browser-profile-key-for-playwright-test-123"
    # The second browser is prepared first: each call clears the stray placeholder that
    # ``force_login`` registers, which must not remove the first browser's pending row.
    second_cookies = signed_in_cookies(user, second_key, pending=False)
    first_cookies = signed_in_cookies(user, first_key, pending=True)
    errors: list[str] = []

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as error:
            pytest.fail(
                "Chromium is required for the OS-identity browser check. Install it "
                "with `.venv\\Scripts\\python.exe -m playwright install chromium`.\n"
                f"{error}"
            )

        context, page, cdp, authenticator_id = open_page(
            browser, live_server, first_cookies, errors
        )
        dialog = page.get_by_role("dialog", name="Review this computer")
        register = dialog.get_by_role("button", name="Register this computer")
        register.wait_for(state="visible")
        browser_only = dialog.get_by_role("button", name="Register this browser only")
        assert browser_only.is_visible()
        # The computer action is the primary one; the browser-only fallback is secondary.
        assert "btn-primary" in (register.get_attribute("class") or "")
        assert "btn-primary" not in (browser_only.get_attribute("class") or "")
        assert not dialog.get_by_role(
            "button", name="Verify this computer"
        ).is_visible()
        register.click()
        page.get_by_text("This computer is now registered to your account.").wait_for()
        assert not page.get_by_role("dialog", name="Review this computer").is_visible()
        credentials = cdp.send(
            "WebAuthn.getCredentials", {"authenticatorId": authenticator_id}
        )["credentials"]
        assert len(credentials) == 1
        context.close()

        # Another browser on the same computer: new cookie jar and profile, but the
        # same OS keystore, so the credential is copied into its virtual authenticator.
        context, page, cdp, authenticator_id = open_page(
            browser, live_server, second_cookies, errors
        )
        cdp.send(
            "WebAuthn.addCredential",
            {"authenticatorId": authenticator_id, "credential": credentials[0]},
        )
        dialog = page.get_by_role("dialog", name="Review this computer")
        verify = dialog.get_by_role("button", name="Verify this computer")
        verify.wait_for(state="visible")
        assert "btn-primary" in (verify.get_attribute("class") or "")
        verify.click()
        page.get_by_text(
            "This computer is recognised. No new registration is needed."
        ).wait_for()
        assert not page.get_by_role("dialog", name="Review this computer").is_visible()
        context.close()
        browser.close()

    assert not errors
    devices = list(Device.objects.filter(user=user))
    assert len(devices) == 1
    assert devices[0].is_os_identity and devices[0].is_trusted
    assert devices[0].browser_key_hash == ""
    linked = set(
        DeviceBrowserLink.objects.filter(device=devices[0]).values_list(
            "browser_key_hash", flat=True
        )
    )
    assert linked == {hash_browser_key(first_key), hash_browser_key(second_key)}
