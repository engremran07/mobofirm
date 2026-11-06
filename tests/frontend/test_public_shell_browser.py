from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse
from playwright.sync_api import Browser, BrowserContext, Page, expect, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from apps.devices.models import Device
from apps.devices.services import hash_browser_key
from apps.firmwares.models import Brand, Model
from tests.frontend.browser_support import dismiss_dialogs

PUBLIC_ROUTES = (
    "home",
    "firmwares:browse",
    "firmwares:brand_list",
    "firmwares:chipset_list",
    "firmwares:flashing_tools",
    "firmwares:flashing_guides",
    "firmwares:universal_search",
    "blog:post_list",
    "tags:list",
    "users:account_login",
    "users:account_signup",
    "account_reset_password",
    "pages:index",
)
WIDTHS = (360, 390, 768, 820, 1024, 1365)


@contextmanager
def chromium() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as error:
            pytest.fail(
                "Chromium is required for frontend checks. Install it with "
                f"`.venv\\Scripts\\python.exe -m playwright install chromium`.\n{error}"
            )
        try:
            yield browser
        finally:
            browser.close()


def _watch(page: Page, failures: list[str]) -> None:
    def on_console(message) -> None:
        if message.type in {"error", "warning"} and not message.text.startswith(
            "Failed to load resource:"
        ):
            failures.append(f"console {message.type}: {message.text}")

    page.on("console", on_console)
    page.on("pageerror", lambda error: failures.append(f"page error: {error}"))


def _ready(page: Page) -> None:
    page.wait_for_function("() => Boolean(window.htmx && window.Alpine)", timeout=5_000)


def _overflow(page: Page) -> dict[str, int]:
    return page.evaluate(
        """() => ({
            viewport: document.documentElement.clientWidth,
            document: document.documentElement.scrollWidth
        })"""
    )


@pytest.mark.django_db(transaction=True)
def test_public_routes_have_no_console_or_csp_errors_or_horizontal_overflow(
    live_server,
):
    failures: list[str] = []
    with chromium() as browser:
        page = browser.new_page()
        _watch(page, failures)
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            for route in PUBLIC_ROUTES:
                url = f"{live_server.url}{reverse(route)}"
                response = page.goto(url, wait_until="load", timeout=15_000)
                label = f"{route} at {width}px"
                if response is None or response.status >= 400:
                    failures.append(f"{label}: HTTP {response and response.status}")
                    continue
                _ready(page)
                dimensions = _overflow(page)
                if dimensions["document"] > dimensions["viewport"] + 1:
                    failures.append(f"{label}: horizontal overflow {dimensions}")
        page.goto(f"{live_server.url}/no-such-page/", wait_until="load")
        assert page.locator("h1").inner_text() == "This page is unavailable."

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_header_stays_on_two_rows_without_overlap_at_the_lg_breakpoint(live_server):
    with chromium() as browser:
        page = browser.new_page(viewport={"width": 992, "height": 800})
        page.goto(f"{live_server.url}/", wait_until="load")
        _ready(page)

        nav = page.locator("#primary-navigation")
        search = page.locator("#site-search")
        assert nav.is_visible() and search.is_visible()
        for label in ("Firmware", "Brands", "Chipsets", "Tools & Guides", "Blog"):
            assert nav.get_by_role("link", name=label).is_visible()
        nav_box = nav.bounding_box()
        search_box = search.bounding_box()
        assert nav_box and search_box
        assert nav_box["y"] >= search_box["y"] + search_box["height"] - 1
        assert not page.locator("button[aria-label='Toggle navigation']").is_visible()
        assert _overflow(page)["document"] <= 992 + 1


@pytest.mark.django_db(transaction=True)
def test_keyboard_navigation_skip_link_and_current_page_state(live_server):
    with chromium() as browser:
        page = browser.new_page(viewport={"width": 1365, "height": 900})
        page.goto(f"{live_server.url}{reverse('firmwares:brand_list')}")
        _ready(page)

        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.className") == "skip-link"
        page.keyboard.press("Enter")
        assert page.evaluate("document.activeElement.id") == "main-content"

        page.focus("body")
        reached: list[str] = []
        for _ in range(14):
            page.keyboard.press("Tab")
            reached.append(page.evaluate("document.activeElement.textContent.trim()"))
        assert {"Brands", "Chipsets", "Blog"} <= set(reached)
        assert (
            page.locator("#primary-navigation [aria-current='page']").inner_text()
            == "Brands"
        )

        page.set_viewport_size({"width": 390, "height": 844})
        toggle = page.get_by_role("button", name="Toggle navigation")
        toggle.focus()
        page.keyboard.press("Enter")
        assert toggle.get_attribute("aria-expanded") == "true"
        assert page.locator("#mobile-navigation").is_visible()
        page.keyboard.press("Escape")
        assert toggle.get_attribute("aria-expanded") == "false"


@pytest.mark.django_db(transaction=True)
def test_navigation_is_available_without_javascript(live_server):
    with chromium() as browser:
        context = browser.new_context(
            viewport={"width": 390, "height": 844}, java_script_enabled=False
        )
        page = context.new_page()
        page.goto(f"{live_server.url}/", wait_until="load")

        assert not page.locator(".mobile-nav-toggle").is_visible()
        mobile = page.locator("#mobile-navigation")
        assert mobile.get_by_role("link", name="Brands").is_visible()
        assert mobile.get_by_role("link", name="Tools & Guides").is_visible()


@pytest.mark.django_db(transaction=True)
def test_theme_follows_system_preference_before_the_bundle_runs(live_server):
    with chromium() as browser:
        context = browser.new_context(color_scheme="dark")
        page = context.new_page()
        page.route("**/static/build/site.js", lambda route: route.abort())
        page.goto(f"{live_server.url}/", wait_until="domcontentloaded")
        assert page.locator("html").get_attribute("data-bs-theme") == "dark"

        stored = browser.new_context(color_scheme="dark")
        stored.add_init_script("window.localStorage.setItem('mobofirm-theme', 'light')")
        stored_page = stored.new_page()
        stored_page.goto(f"{live_server.url}/", wait_until="load")
        assert stored_page.locator("html").get_attribute("data-bs-theme") == "light"


@pytest.mark.django_db(transaction=True)
def test_blocked_storage_does_not_break_the_frontend(live_server):
    failures: list[str] = []
    with chromium() as browser:
        context = browser.new_context()
        context.add_init_script(
            """
            const blocked = () => { throw new DOMException("blocked", "SecurityError"); };
            Storage.prototype.getItem = blocked;
            Storage.prototype.setItem = blocked;
            """
        )
        page = context.new_page()
        _watch(page, failures)
        page.goto(f"{live_server.url}/", wait_until="load")
        _ready(page)
        page.locator("button[data-theme-toggle]:visible").first.click()
        assert page.locator("html").get_attribute("data-bs-theme") == "dark"
    assert not failures, failures


def _registration_state(live_server) -> tuple[str, str]:
    """Create a user with a pending browser-registration prompt (ORM, pre-browser)."""
    user = get_user_model().objects.create_user(
        username="shell-dialog-user",
        email="shell-dialog@example.com",
        password="Unusual-Pass-9042!",
    )
    EmailAddress.objects.create(
        user=user, email=user.email, primary=True, verified=True
    )
    browser_key = "shell-dialog-browser-profile-key-0123456789"
    device = Device.objects.create(
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
        "device_name": device.display_name,
        "os_family": device.os_family,
        "browser_family": device.browser_family,
        "active_devices": 0,
        "max_devices": 3,
    }
    session.save()
    return client.cookies[settings.SESSION_COOKIE_NAME].value, browser_key


def _registration_context(
    browser: Browser, live_server, state: tuple[str, str]
) -> BrowserContext:
    session_cookie, browser_key = state
    context = browser.new_context(viewport={"width": 1365, "height": 900})
    context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": session_cookie,
                "url": live_server.url,
            },
            {"name": "mobofirm_device", "value": browser_key, "url": live_server.url},
        ]
    )
    return context


@pytest.mark.django_db(transaction=True)
def test_device_dialog_escape_closes_it_and_leaves_the_page_usable(live_server):
    failures: list[str] = []
    state = _registration_state(live_server)
    with chromium() as browser:
        context = _registration_context(browser, live_server, state)
        page = context.new_page()
        _watch(page, failures)
        page.goto(f"{live_server.url}{reverse('devices:my_devices')}")
        _ready(page)

        dialog = page.get_by_role("dialog", name="Review this browser")
        assert dialog.is_visible()
        assert page.locator(".device-registration-backdrop").count() == 0
        assert dialog.evaluate("element => element.matches(':modal')")

        page.keyboard.press("Escape")
        assert not dialog.is_visible()

        page.get_by_role("link", name="Brands").first.click()
        page.wait_for_url("**/firmwares/brands/")
    assert not failures, failures


@pytest.mark.django_db(transaction=True)
def test_app_confirm_dialog_opens_cancels_and_closes_on_escape(live_server):
    markup = render_to_string(
        "components/confirm_dialog.html",
        {
            "dialog_id": "demo-confirm",
            "title": "Merge tags?",
            "message": "This cannot be undone.",
            "action": "/tags/merge/",
            "confirm_label": "Merge",
            "danger": True,
        },
    )
    with chromium() as browser:
        page = browser.new_page(viewport={"width": 1024, "height": 800})
        native: list[str] = []
        dismiss_dialogs(page, native)
        page.goto(f"{live_server.url}/", wait_until="load")
        _ready(page)
        page.evaluate(
            """markup => document.querySelector('main').insertAdjacentHTML(
                'beforeend',
                '<button type="button" id="open-demo" data-dialog-open="demo-confirm">Open</button>' + markup
            )""",
            markup,
        )
        dialog = page.locator("#demo-confirm")

        page.locator("#open-demo").click()
        expect(dialog).to_be_visible()
        assert dialog.evaluate("element => element.matches(':modal')")
        assert page.evaluate("document.activeElement.textContent.trim()") == "Cancel"

        page.keyboard.press("Escape")
        expect(dialog).not_to_be_visible()

        page.locator("#open-demo").click()
        dialog.get_by_role("button", name="Cancel").click()
        expect(dialog).not_to_be_visible()

        page.locator("#open-demo").click()
        page.mouse.click(4, 4)
        expect(dialog).not_to_be_visible()
        assert not native


@pytest.mark.django_db(transaction=True)
def test_site_search_autocomplete_is_keyboard_operable(live_server):
    brand = Brand.objects.create(name="Zephyrix", slug="zephyrix")
    Model.objects.create(
        brand=brand, name="Zephyrix Nova 5", slug="zephyrix-nova-5", model_code="ZX-5"
    )
    failures: list[str] = []
    with chromium() as browser:
        page = browser.new_page(viewport={"width": 1365, "height": 900})
        _watch(page, failures)
        page.goto(f"{live_server.url}/", wait_until="load")
        _ready(page)

        search = page.locator("#site-search")
        search.click()
        search.press_sequentially("Zephyrix", delay=30)
        options = page.locator("#site-search-results [role='option']")
        expect(options.first).to_be_visible(timeout=5_000)
        assert search.get_attribute("aria-expanded") == "true"
        assert page.locator("#site-search-results").get_attribute("role") == "listbox"
        assert options.count() >= 2

        page.keyboard.press("ArrowDown")
        active = search.get_attribute("aria-activedescendant")
        assert (
            active
            and page.locator(f"#{active}").get_attribute("aria-selected") == "true"
        )
        page.keyboard.press("Enter")
        page.wait_for_url(lambda url: "/firmwares/" in url)

        page.goto(f"{live_server.url}/", wait_until="load")
        _ready(page)
        page.locator("#site-search").press_sequentially("Zephyrix", delay=30)
        expect(
            page.locator("#site-search-results [role='option']").first
        ).to_be_visible()
        page.keyboard.press("Escape")
        assert page.locator("#site-search").get_attribute("aria-expanded") == "false"
        assert not page.locator("#site-search-results").is_visible()
    assert not failures, failures


@pytest.mark.django_db(transaction=True)
def test_htmx_failures_are_visible_to_sighted_users(live_server):
    with chromium() as browser:
        page = browser.new_page(viewport={"width": 1365, "height": 900})
        page.route(
            "**/firmwares/api/search/autocomplete/**",
            lambda route: route.fulfill(status=500, body="boom"),
        )
        page.goto(f"{live_server.url}/", wait_until="load")
        _ready(page)

        page.locator("#site-search").press_sequentially("camera", delay=30)
        alert = page.locator("#global-feedback .alert-danger")
        expect(alert).to_be_visible(timeout=5_000)
        assert "server hit a problem" in alert.inner_text()
        assert alert.get_attribute("role") == "alert"
        alert.get_by_role("button", name="Dismiss message").click()
        expect(alert).to_have_count(0)
