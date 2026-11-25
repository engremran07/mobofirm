"""Browser checks for the console theme switcher (light / dark / system) and its persistence."""

from __future__ import annotations

import re
from typing import Literal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from playwright.sync_api import Browser, BrowserContext, Page, expect

from tests.frontend.browser_support import chromium, login_cookie, overflow, watch
from tests.frontend.console_browser_support import ConsoleServer, ready

pytestmark = pytest.mark.django_db(transaction=True)

STORAGE_KEY = "mobofirm-theme"
FIRST_PAINT = """
new MutationObserver((records, observer) => {
  if (document.body && window.__firstPaintTheme === undefined) {
    window.__firstPaintTheme = document.documentElement.dataset.bsTheme || null;
    observer.disconnect();
  }
}).observe(document, { childList: true, subtree: true });
"""


def _server(live_server) -> ConsoleServer:
    user = get_user_model().objects.create_superuser(
        username="theme-admin",
        email="theme-admin@example.test",
        email_verified_at=timezone.now(),
    )
    return ConsoleServer(live_server.url, user, login_cookie(user))


def _context(
    browser: Browser,
    server: ConsoleServer,
    *,
    scheme: Literal["light", "dark"] = "light",
    width: int = 1365,
) -> BrowserContext:
    """A signed-in context that, unlike the crawler's, starts with no stored theme."""
    context = browser.new_context(
        viewport={"width": width, "height": 900}, color_scheme=scheme
    )
    context.add_cookies(
        [
            {"name": name, "value": value, "url": server.url}
            for name, value in server.cookies.items()
        ]
    )
    return context


def _open(page: Page, server: ConsoleServer, path: str = "/admin/") -> None:
    response = page.goto(f"{server.url}{path}", wait_until="load", timeout=30_000)
    assert response is not None and response.status == 200
    problems: list[str] = []
    ready(page, problems)
    assert not problems, problems


def _surface_luminance(page: Page) -> float:
    red, green, blue = page.evaluate(
        """() => getComputedStyle(document.body).backgroundColor
            .match(/\\d+/g).slice(0, 3).map(Number)"""
    )
    return (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255


def _choice(page: Page, name: str):
    return page.locator(
        f"header [data-console-theme-set='{name}'], "
        f"[data-ui-panel][data-ui-open].dropdown-menu [data-console-theme-set='{name}']"
    ).first


def _stored(page: Page) -> str | None:
    return page.evaluate("(key) => window.localStorage.getItem(key)", STORAGE_KEY)


def test_header_theme_switcher_toggles_and_persists_across_pages(live_server):
    server = _server(live_server)
    failures: list[str] = []

    with chromium() as browser:
        context = _context(browser, server)
        page = context.new_page()
        watch(page, failures)
        _open(page, server)
        html = page.locator("html")

        # The switcher is visible in the header and starts on "follow the system".
        expect(page.locator("header [data-console-theme-set]:visible")).to_have_count(3)
        expect(_choice(page, "auto")).to_have_attribute("aria-pressed", "true")
        expect(html).to_have_attribute("data-bs-theme", "light")
        assert _surface_luminance(page) > 0.5

        # Dark: attribute, colours, pressed state and the preference shared with the site.
        _choice(page, "dark").click()
        expect(html).to_have_attribute("data-bs-theme", "dark")
        expect(_choice(page, "dark")).to_have_attribute("aria-pressed", "true")
        expect(_choice(page, "auto")).to_have_attribute("aria-pressed", "false")
        assert _surface_luminance(page) < 0.2
        assert _stored(page) == "dark"

        # The choice survives navigation and is applied before the first paint.
        page.add_init_script(FIRST_PAINT)
        _open(page, server, "/admin/blog/post/")
        expect(html).to_have_attribute("data-bs-theme", "dark")
        assert page.evaluate("window.__firstPaintTheme") == "dark"
        assert _surface_luminance(page) < 0.2

        # Light overrides a dark system preference.
        page.emulate_media(color_scheme="dark")
        _choice(page, "light").click()
        expect(html).to_have_attribute("data-bs-theme", "light")
        assert _stored(page) == "light"

        # System follows the emulated OS scheme live and clears the stored preference.
        _choice(page, "auto").click()
        expect(html).to_have_attribute("data-bs-theme", "dark")
        assert _stored(page) is None
        page.emulate_media(color_scheme="light")
        expect(html).to_have_attribute("data-bs-theme", "light")
        context.close()

    assert not failures, "\n".join(failures)


def test_console_adopts_the_public_site_theme_preference(live_server):
    server = _server(live_server)
    failures: list[str] = []

    with chromium() as browser:
        context = _context(browser, server)
        context.add_init_script(f"window.localStorage.setItem('{STORAGE_KEY}', 'dark')")
        page = context.new_page()
        watch(page, failures)
        page.add_init_script(FIRST_PAINT)
        _open(page, server)

        expect(page.locator("html")).to_have_attribute("data-bs-theme", "dark")
        expect(_choice(page, "dark")).to_have_attribute("aria-pressed", "true")
        assert page.evaluate("window.__firstPaintTheme") == "dark"
        assert _surface_luminance(page) < 0.2
        context.close()

    assert not failures, "\n".join(failures)


def test_theme_switcher_is_keyboard_operable_in_the_phone_account_menu(live_server):
    server = _server(live_server)
    failures: list[str] = []

    with chromium() as browser:
        context = _context(browser, server, width=390)
        page = context.new_page()
        watch(page, failures)
        _open(page, server)

        # The header switcher is hidden on a phone; the account menu carries it.
        expect(
            page.locator("header .btn-group.d-none[aria-label='Colour theme']")
        ).to_be_hidden()
        page.get_by_role("button", name="Account menu").click()
        menu_choice = page.locator(
            "[data-ui-panel][data-ui-open].dropdown-menu [data-console-theme-set]"
        )
        expect(menu_choice).to_have_count(3)
        dimensions = overflow(page)
        assert dimensions["document"] <= dimensions["viewport"] + 1, dimensions

        page.locator(
            "[data-ui-panel][data-ui-open].dropdown-menu [data-console-theme-set='dark']"
        ).focus()
        page.keyboard.press("Enter")
        expect(page.locator("html")).to_have_attribute("data-bs-theme", "dark")
        page.locator(
            "[data-ui-panel][data-ui-open].dropdown-menu [data-console-theme-set='light']"
        ).focus()
        page.keyboard.press("Space")
        expect(page.locator("html")).to_have_attribute("data-bs-theme", "light")
        for index in range(3):
            expect(menu_choice.nth(index)).to_have_accessible_name(
                re.compile("theme", re.IGNORECASE)
            )
        context.close()

    assert not failures, "\n".join(failures)
