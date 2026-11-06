from __future__ import annotations

from unittest.mock import patch

import pytest
from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from tests.frontend.browser_support import dismiss_dialogs


@pytest.mark.django_db(transaction=True)
def test_profile_country_search_suggestion_and_dark_select_colors(live_server):
    user = get_user_model().objects.create_user(
        username="country_search_user",
        email="country-search@example.com",
        password="A-valid-password-983!",
        profile_completed=False,
    )
    EmailAddress.objects.create(
        user=user,
        email=user.email,
        primary=True,
        verified=True,
    )
    django_client = Client()
    django_client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
    session = django_client.session
    session.pop("device_registration_prompt", None)
    session.save()
    session_cookie = django_client.cookies[settings.SESSION_COOKIE_NAME].value
    browser_errors: list[str] = []

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as error:
            pytest.fail(
                "Chromium is required for country-selector interaction checks. "
                f"Install it with `.venv\\Scripts\\python.exe -m playwright install chromium`.\n{error}"
            )

        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.on("pageerror", lambda error: browser_errors.append(str(error)))
        native_dialogs: list[str] = []
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

        with patch(
            "apps.users.services.detect_country",
            return_value="CA",
        ):
            page.goto(
                f"{live_server.url}{reverse('users:tell_us_about_you')}",
                wait_until="load",
                timeout=15_000,
            )

        country = page.locator("#id_country")
        phone_code = page.locator("#id_phone_country_code")
        assert country.input_value() == "CA"
        assert phone_code.input_value() == "+1"
        assert (
            country.evaluate("element => getComputedStyle(element).color")
            != "rgba(0, 0, 0, 0)"
        )

        page.locator("#country-search").fill("Germany")
        visible_countries = country.evaluate(
            "element => Array.from(element.options).filter(option => !option.hidden).map(option => option.textContent)"
        )
        assert any("Germany (DE)" in option for option in visible_countries)
        assert not any("Canada (CA)" in option for option in visible_countries)

        page.locator("#phone-code-search").fill("+44")
        visible_phone_codes = phone_code.evaluate(
            "element => Array.from(element.options).filter(option => !option.hidden).map(option => option.textContent)"
        )
        assert any("United Kingdom (GB)" in option for option in visible_phone_codes)
        assert not any("+1" in option for option in visible_phone_codes)

        dimensions = page.evaluate(
            "() => ({viewport: document.documentElement.clientWidth, page: document.documentElement.scrollWidth})"
        )
        assert dimensions["page"] <= dimensions["viewport"] + 1

        page.set_viewport_size({"width": 1365, "height": 900})
        page.goto(f"{live_server.url}/", wait_until="load", timeout=15_000)
        page.get_by_role("button", name="Sign out").click()
        page.get_by_role("link", name="Sign in").wait_for(state="visible")
        assert not native_dialogs
        assert not browser_errors
        browser.close()
