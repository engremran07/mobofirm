"""Profile completion: auto-populated, type-ahead country and calling-code pickers."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from allauth.account.models import EmailAddress
from django.urls import reverse
from playwright.sync_api import Page, Response

from tests.frontend.browser_support import (
    chromium,
    dismiss_dialogs,
    login_cookie,
    make_user,
    overflow,
    ready,
    session_context,
    watch,
)

COUNTRY = "#id_country"
CODE = "#id_phone_country_code"


def _fresh_user(username: str):
    user = make_user(username)
    EmailAddress.objects.create(
        user=user, email=user.email, primary=True, verified=True
    )
    return user


def _query_matches(prefix: str) -> Callable[[Response], bool]:
    def matches(response: Response) -> bool:
        return f"q={prefix}&" in response.url or response.url.endswith(f"q={prefix}")

    return matches


def _option_titles(page: Page, input_id: str) -> list[str]:
    return page.locator(f"#{input_id}-results .combobox-title").all_inner_texts()


def _wait_listbox(page: Page, input_id: str) -> None:
    page.locator(f"#{input_id}-results").wait_for(state="visible")
    page.wait_for_function(
        "id => document.querySelectorAll(`#${id}-results [role=option]`).length > 0",
        arg=input_id,
    )


@pytest.mark.django_db(transaction=True)
def test_timezone_fills_empty_country_and_code_with_a_change_hint(live_server):
    cookies = login_cookie(_fresh_user("tz_fill"))
    failures: list[str] = []
    dialogs: list[str] = []

    with chromium() as browser:
        context = browser.new_context(
            timezone_id="Asia/Karachi",
            locale="en-US",
            viewport={"width": 1365, "height": 900},
        )
        context.add_cookies(
            [
                {"name": name, "value": value, "url": live_server.url}
                for name, value in cookies.items()
            ]
        )
        page = context.new_page()
        watch(page, failures)
        dismiss_dialogs(page, dialogs)
        page.goto(f"{live_server.url}{reverse('users:tell_us_about_you')}")
        ready(page)

        page.wait_for_function(
            "() => document.getElementById('id_country-value')?.value === 'PK'"
        )
        assert "Pakistan" in page.locator(COUNTRY).input_value()
        page.wait_for_function(
            "() => document.getElementById('id_phone_country_code-value')?.value === '+92'"
        )
        hint = page.locator("[data-locale-hint]")
        hint.wait_for(state="visible")
        assert "Detected: " in hint.inner_text()
        assert "change" in hint.inner_text()

        # "change" moves focus to the country field so it can be overwritten.
        hint.get_by_role("button", name="change").click()
        assert page.evaluate("document.activeElement.id") == "id_country"
        context.close()

    assert not dialogs
    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_typed_country_is_never_overridden_by_a_late_detection(live_server):
    cookies = login_cookie(_fresh_user("tz_keep"))
    failures: list[str] = []
    held: list = []

    with chromium() as browser:
        context = browser.new_context(
            timezone_id="Asia/Karachi",
            locale="en-US",
            viewport={"width": 1365, "height": 900},
        )
        context.add_cookies(
            [
                {"name": name, "value": value, "url": live_server.url}
                for name, value in cookies.items()
            ]
        )
        page = context.new_page()
        watch(page, failures)

        def hold(route) -> None:
            held.append(route)

        page.route("**/users/locale-hint/**", hold)
        page.goto(f"{live_server.url}{reverse('users:tell_us_about_you')}")
        ready(page)

        page.locator(COUNTRY).click()
        page.keyboard.type("germ")
        _wait_listbox(page, "id_country")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        page.wait_for_function(
            "() => document.getElementById('id_country-value')?.value === 'DE'"
        )

        assert held, "the browser hint request should be pending"
        with page.expect_response("**/users/locale-hint/**"):
            held[0].continue_()
        page.evaluate(
            "() => new Promise(done => setTimeout(() => requestAnimationFrame(done), 0))"
        )

        assert page.locator("#id_country-value").input_value() == "DE"
        assert "Germany" in page.locator(COUNTRY).input_value()
        context.close()

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_country_and_code_comboboxes_shortlist_on_every_keystroke(live_server):
    cookies = login_cookie(_fresh_user("typeahead"))
    failures: list[str] = []
    suggest_queries: list[str] = []

    with chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page = context.new_page()
        watch(page, failures)
        page.on(
            "request",
            lambda request: (
                suggest_queries.append(request.url)
                if "/search/suggest/" in request.url
                else None
            ),
        )
        page.route(
            "**/users/locale-hint/**",
            lambda route: route.fulfill(json={"detected": False}),
        )
        page.goto(f"{live_server.url}{reverse('users:tell_us_about_you')}")
        ready(page)

        country = page.locator(COUNTRY)
        country.click()
        for prefix in ("p", "pa", "pak"):
            with page.expect_response(_query_matches(prefix)):
                page.keyboard.type(prefix[-1])
            _wait_listbox(page, "id_country")
        page.wait_for_function(
            "() => [...document.querySelectorAll('#id_country-results .combobox-title')]"
            ".some(node => node.textContent.includes('Pakistan'))"
        )
        titles = _option_titles(page, "id_country")
        assert any("Pakistan" in title for title in titles)
        assert not any("Canada" in title for title in titles)
        assert len(titles) <= 10
        assert country.get_attribute("aria-expanded") == "true"

        # One request per keystroke, each carrying the typed prefix and the kind.
        queried = [url for url in suggest_queries if "kinds=country" in url]
        assert any("q=pak" in url for url in queried)
        assert any("q=pa&" in url or url.endswith("q=pa") for url in queried)

        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        assert page.locator("#id_country-value").input_value() == "PK"
        page.wait_for_function(
            "() => document.getElementById('id_phone_country_code-value')?.value === '+92'"
        )

        code = page.locator(CODE)
        code.fill("")
        code.click()
        page.keyboard.type("+44")
        page.wait_for_function(
            "() => [...document.querySelectorAll('#id_phone_country_code-results .combobox-title')]"
            ".some(node => node.textContent.includes('United Kingdom'))"
        )
        assert not any(
            "Pakistan" in title
            for title in _option_titles(page, "id_phone_country_code")
        )
        page.keyboard.press("Escape")
        assert code.get_attribute("aria-expanded") == "false"

        dimensions = overflow(page)
        assert dimensions["document"] <= dimensions["viewport"] + 1
        context.close()

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_profile_page_is_responsive_and_next_survives_the_form(live_server):
    cookies = login_cookie(_fresh_user("responsive_profile"))
    failures: list[str] = []

    with chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page = context.new_page()
        watch(page, failures)
        for width in (360, 768, 1365):
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(
                f"{live_server.url}{reverse('users:tell_us_about_you')}?next=/files/"
            )
            ready(page)
            dimensions = overflow(page)
            assert dimensions["document"] <= dimensions["viewport"] + 1, width
            assert page.locator("nav[aria-label='Breadcrumb']").count() == 1
            assert (
                page.locator("#main-content input[name='next']").input_value()
                == "/files/"
            )
        context.close()

    assert not failures, "\n".join(failures)
