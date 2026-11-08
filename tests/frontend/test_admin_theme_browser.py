"""Browser checks for the Unfold admin theme switcher (light / dark / system)."""

from __future__ import annotations

import re
from typing import Literal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from playwright.sync_api import Browser, BrowserContext, Page, expect

from tests.frontend.browser_support import (
    chromium,
    login_cookie,
    overflow,
    ready,
    watch,
)

ADMIN_KEY = "adminTheme"
PUBLIC_KEY = "mobofirm-theme"
FIRST_PAINT = """
new MutationObserver((records, observer) => {
  if (document.body && window.__firstPaintDark === undefined) {
    window.__firstPaintDark = document.documentElement.classList.contains('dark');
    observer.disconnect();
  }
}).observe(document, { childList: true, subtree: true });
"""


def _superuser():
    return get_user_model().objects.create_superuser(
        username="theme-admin",
        email="theme-admin@example.test",
        email_verified_at=timezone.now(),
    )


def _context(
    browser: Browser,
    live_server,
    cookies: dict[str, str],
    *,
    scheme: Literal["light", "dark"] = "light",
    width: int = 1365,
) -> BrowserContext:
    context = browser.new_context(
        viewport={"width": width, "height": 900}, color_scheme=scheme
    )
    context.add_cookies(
        [
            {"name": name, "value": value, "url": live_server.url}
            for name, value in cookies.items()
        ]
    )
    return context


def _open_admin(page: Page, live_server, path: str = "/admin/") -> None:
    response = page.goto(f"{live_server.url}{path}", wait_until="load", timeout=20_000)
    assert response is not None and response.status == 200
    ready(page)


def _background_luminance(page: Page) -> float:
    red, green, blue = page.evaluate(
        """() => getComputedStyle(document.body).backgroundColor
            .match(/\\d+/g).slice(0, 3).map(Number)"""
    )
    return (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255


def _choice(page: Page, name: str):
    return page.locator(f"[data-admin-theme-choice='{name}']")


def _stored(page: Page, key: str) -> str | None:
    return page.evaluate("(key) => window.localStorage.getItem(key)", key)


@pytest.mark.django_db(transaction=True)
def test_header_theme_switcher_toggles_persists_and_syncs_the_public_key(live_server):
    cookies = login_cookie(_superuser())
    failures: list[str] = []

    with chromium() as browser:
        context = _context(browser, live_server, cookies)
        page = context.new_page()
        watch(page, failures)
        _open_admin(page, live_server)

        # The switcher is visible in the header without opening any menu.
        switch = page.locator("[data-admin-theme-switch]")
        expect(switch).to_be_visible()
        expect(page.locator("[data-admin-theme-choice]")).to_have_count(3)
        expect(_choice(page, "auto")).to_have_attribute("aria-pressed", "true")
        expect(page.locator("html")).not_to_have_class(re.compile(r"\bdark\b"))
        assert _background_luminance(page) > 0.8

        # Dark: class, colours, persistence and the mirrored public preference.
        _choice(page, "dark").click()
        expect(page.locator("html")).to_have_class(re.compile(r"\bdark\b"))
        expect(_choice(page, "dark")).to_have_attribute("aria-pressed", "true")
        assert _background_luminance(page) < 0.2
        assert _stored(page, ADMIN_KEY) == '"dark"'
        assert _stored(page, PUBLIC_KEY) == "dark"

        # The choice survives a reload and is applied before the first paint.
        page.add_init_script(FIRST_PAINT)
        _open_admin(page, live_server, "/admin/blog/post/")
        expect(page.locator("html")).to_have_class(re.compile(r"\bdark\b"))
        assert page.evaluate("window.__firstPaintDark") is True
        assert _background_luminance(page) < 0.2

        # Light overrides a dark system preference.
        page.emulate_media(color_scheme="dark")
        _choice(page, "light").click()
        expect(page.locator("html")).not_to_have_class(re.compile(r"\bdark\b"))
        assert _stored(page, PUBLIC_KEY) == "light"

        # System follows the emulated OS scheme and clears the public preference.
        _choice(page, "auto").click()
        expect(page.locator("html")).to_have_class(re.compile(r"\bdark\b"))
        assert _stored(page, ADMIN_KEY) == '"auto"'
        assert _stored(page, PUBLIC_KEY) is None
        page.emulate_media(color_scheme="light")
        expect(page.locator("html")).not_to_have_class(re.compile(r"\bdark\b"))
        context.close()

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_admin_adopts_the_public_site_theme_preference(live_server):
    cookies = login_cookie(_superuser())
    failures: list[str] = []

    with chromium() as browser:
        context = _context(browser, live_server, cookies)
        context.add_init_script(f"window.localStorage.setItem('{PUBLIC_KEY}', 'dark')")
        page = context.new_page()
        watch(page, failures)
        page.add_init_script(FIRST_PAINT)
        _open_admin(page, live_server)

        expect(page.locator("html")).to_have_class(re.compile(r"\bdark\b"))
        expect(_choice(page, "dark")).to_have_attribute("aria-pressed", "true")
        assert page.evaluate("window.__firstPaintDark") is True
        assert _stored(page, ADMIN_KEY) == '"dark"'
        context.close()

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_theme_switcher_is_keyboard_operable_and_fits_a_phone(live_server):
    cookies = login_cookie(_superuser())
    failures: list[str] = []

    with chromium() as browser:
        context = _context(browser, live_server, cookies, width=390)
        page = context.new_page()
        watch(page, failures)
        _open_admin(page, live_server)

        expect(page.locator("[data-admin-theme-switch]")).to_be_visible()
        dimensions = overflow(page)
        assert dimensions["document"] <= dimensions["viewport"] + 1, dimensions

        _choice(page, "dark").focus()
        page.keyboard.press("Enter")
        expect(page.locator("html")).to_have_class(re.compile(r"\bdark\b"))
        _choice(page, "light").focus()
        page.keyboard.press("Space")
        expect(page.locator("html")).not_to_have_class(re.compile(r"\bdark\b"))
        for name in ("light", "dark", "auto"):
            expect(_choice(page, name)).to_have_accessible_name(re.compile("theme"))
        context.close()

    assert not failures, "\n".join(failures)
