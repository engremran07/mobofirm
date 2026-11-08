from __future__ import annotations

import re

import pytest
from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client
from django.urls import reverse
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from tests.frontend.browser_support import dismiss_dialogs


@pytest.mark.django_db(transaction=True)
def test_email_code_verification_is_responsive_and_completes_signup(live_server):
    client = Client()
    signup_response = client.post(
        reverse("users:account_signup"),
        {
            "email": "browser-code@example.com",
            "password1": "Another-valid-password-983!",
            "password2": "Another-valid-password-983!",
        },
    )
    assert signup_response.status_code == 302
    assert len(mail.outbox) == 1
    message = str(mail.outbox[0].body)
    code_match = re.search(
        r"one-time verification code.*?\b([0-9A-Z]{6})\b",
        message,
        re.DOTALL,
    )
    link_match = re.search(r"/accounts/confirm-email/([-\w:]+)/", message)
    assert code_match
    assert link_match

    user = get_user_model().objects.get(email="browser-code@example.com")
    email_address = EmailAddress.objects.get(user=user, email=user.email)
    assert not email_address.verified

    session_cookie = client.cookies[settings.SESSION_COOKIE_NAME].value
    browser_diagnostics: list[str] = []
    native_dialogs: list[str] = []

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as error:
            pytest.fail(
                "Chromium is required for email verification browser coverage. "
                "Install it with `.venv\\Scripts\\python.exe -m playwright install chromium`.\n"
                f"{error}"
            )

        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.on("pageerror", lambda error: browser_diagnostics.append(str(error)))

        def capture_console_diagnostic(message) -> None:
            if message.type in {"error", "warning"}:
                browser_diagnostics.append(f"{message.type}: {message.text}")

        page.on("console", capture_console_diagnostic)
        dismiss_dialogs(page, native_dialogs)
        page.context.add_cookies(
            [
                {
                    "name": settings.SESSION_COOKIE_NAME,
                    "value": session_cookie,
                    "url": live_server.url,
                }
            ]
        )

        page.goto(
            f"{live_server.url}{reverse('users:verify_email')}",
            wait_until="load",
            timeout=15_000,
        )
        page.get_by_role("heading", name="Verify your email address").wait_for()
        assert page.get_by_label("Verification code").is_visible()
        assert (
            page.locator('#main-content form input[name="csrfmiddlewaretoken"]').count()
            == 2
        )
        assert page.get_by_text("browser-code@example.com").is_visible()

        for width in (390, 1365):
            page.set_viewport_size({"width": width, "height": 900})
            dimensions = page.evaluate(
                "() => ({viewport: document.documentElement.clientWidth, page: document.documentElement.scrollWidth})"
            )
            assert dimensions["page"] <= dimensions["viewport"] + 1

        page.get_by_label("Verification code").fill(code_match.group(1))
        page.get_by_role("button", name="Verify email").click()
        page.wait_for_url(f"{live_server.url}{reverse('users:tell_us_about_you')}")
        assert not browser_diagnostics
        assert not native_dialogs
        browser.close()

    email_address.refresh_from_db()
    assert email_address.verified
