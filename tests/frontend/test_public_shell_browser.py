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
def test_shared_htmx_runtime_is_v4_and_blocks_inline_expression_handlers(live_server):
    failures: list[str] = []
    with chromium() as browser:
        page = browser.new_page()
        _watch(page, failures)
        page.goto(f"{live_server.url}/", wait_until="load")
        _ready(page)
        result = page.evaluate(
            """() => {
                window.inlineExpressionRan = false;
                const button = document.createElement("button");
                button.setAttribute("hx-on:click", "window.inlineExpressionRan = true");
                document.body.append(button);
                htmx.process(button);
                button.click();
                button.remove();
                return {version: htmx.version, ran: window.inlineExpressionRan};
            }"""
        )
        assert result == {"version": "4.0.2", "ran": False}
    assert not failures, "\n".join(failures)


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
@pytest.mark.parametrize("width", (992, 1024, 1280, 1365))
def test_header_uses_one_row_without_overlap_at_desktop_widths(live_server, width):
    with chromium() as browser:
        page = browser.new_page(viewport={"width": width, "height": 800})
        page.goto(f"{live_server.url}/", wait_until="load")
        _ready(page)

        nav = page.locator("#primary-navigation")
        search = page.locator("#site-search")
        assert nav.is_visible() and search.is_visible()
        for label in ("Firmware", "Tools & Guides", "Journal", "Community", "Pricing"):
            assert nav.get_by_role("link", name=label).is_visible()
        top_level = nav.locator(":scope > .nav-item, :scope > .site-nav-link")
        boxes = top_level.evaluate_all(
            """items => items
                .map(item => {
                    const rect = item.getBoundingClientRect();
                    return {y: rect.y, height: rect.height};
                })
                .filter(box => box.height > 0)"""
        )
        assert len(boxes) == 5
        assert max(box["y"] for box in boxes) - min(box["y"] for box in boxes) <= 4
        assert page.locator(".site-header").evaluate(
            "element => element.getBoundingClientRect().height <= 65"
        )
        assert not page.locator("button[aria-label='Toggle navigation']").is_visible()
        assert _overflow(page)["document"] <= width + 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("width", (390, 1365))
def test_header_hides_on_down_scroll_and_returns_on_up_scroll(live_server, width):
    failures: list[str] = []
    with chromium() as browser:
        page = browser.new_page(viewport={"width": width, "height": 800})
        _watch(page, failures)
        page.goto(f"{live_server.url}/", wait_until="load")
        _ready(page)
        header = page.locator(".site-header")
        max_scroll = page.evaluate(
            "() => document.documentElement.scrollHeight - window.innerHeight"
        )
        assert max_scroll > 120

        page.evaluate("window.scrollTo(0, Math.min(320, document.body.scrollHeight))")
        page.wait_for_function(
            "() => document.querySelector('.site-header')?.dataset.headerHidden === 'true'",
            timeout=5_000,
        )
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_function(
            "() => !document.querySelector('.site-header')?.hasAttribute('data-header-hidden')",
            timeout=5_000,
        )

        page.locator(".site-header .site-brand:visible").first.focus()
        page.evaluate("window.scrollTo(0, 160)")
        page.wait_for_function("() => window.scrollY >= 120", timeout=5_000)
        expect(header).not_to_have_attribute("data-header-hidden", "true")
    assert not failures, "\n".join(failures)


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
        for _ in range(24):
            page.keyboard.press("Tab")
            reached.append(page.evaluate("document.activeElement.textContent.trim()"))
        assert {"Brands", "Chipsets", "Blog"} <= set(reached)
        current_navigation = page.locator("#primary-navigation [aria-current]")
        assert current_navigation.inner_text() == "Firmware"
        assert current_navigation.get_attribute("aria-current") == "location"

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
        firmware_group = mobile.locator("details").filter(has_text="Firmware")
        firmware_group.locator("summary").click()
        assert firmware_group.get_by_role("link", name="Brands").is_visible()
        assert mobile.get_by_text("Tools & Guides", exact=True).is_visible()
        groups = page.locator(".site-footer [data-footer-group]")
        expect(groups).to_have_count(3)
        assert groups.evaluate_all("items => items.every(item => item.open)")
        assert (
            page.locator(".site-footer")
            .get_by_role("link", name="Firmware catalogue")
            .is_visible()
        )


@pytest.mark.django_db(transaction=True)
def test_footer_groups_reduce_mobile_height_and_expand_on_desktop(
    live_server, tmp_path
):
    failures: list[str] = []
    with chromium() as browser:
        page = browser.new_page(viewport={"width": 360, "height": 800})
        _watch(page, failures)
        page.goto(f"{live_server.url}/", wait_until="load")
        _ready(page)
        consent_banner = page.locator(".consent-banner")
        if consent_banner.is_visible():
            consent_banner.get_by_role("button", name="Reject optional").click()
            expect(consent_banner).to_be_hidden()
        footer = page.locator(".site-footer")
        groups = footer.locator("[data-footer-group]")
        expect(groups).to_have_count(3)
        assert groups.evaluate_all("items => items.every(item => !item.open)")

        height = footer.evaluate(
            "element => Math.ceil(element.getBoundingClientRect().height)"
        )
        assert height <= 560, f"collapsed mobile footer is {height}px tall"
        assert _overflow(page)["document"] <= 361
        footer.screenshot(path=str(tmp_path / "footer-mobile.png"))

        first = groups.nth(0)
        first.locator("summary").focus()
        page.keyboard.press("Enter")
        expect(first).to_have_attribute("open", "")
        expect(first.get_by_role("link", name="Firmware catalogue")).to_be_visible()

        page.set_viewport_size({"width": 1365, "height": 900})
        assert groups.evaluate_all("items => items.every(item => item.open)")

        page.set_viewport_size({"width": 1024, "height": 768})
        assert groups.evaluate_all("items => items.every(item => !item.open)")
        tablet_height = footer.evaluate(
            "element => Math.ceil(element.getBoundingClientRect().height)"
        )
        assert tablet_height <= 460, f"compact tablet footer is {tablet_height}px tall"

        page.set_viewport_size({"width": 1365, "height": 900})
        assert groups.evaluate_all("items => items.every(item => item.open)")
        expect(footer.get_by_role("link", name="Firmware catalogue")).to_be_visible()
        desktop_height = footer.evaluate(
            "element => Math.ceil(element.getBoundingClientRect().height)"
        )
        assert desktop_height <= 500, (
            f"expanded desktop footer is {desktop_height}px tall"
        )
        footer.screenshot(path=str(tmp_path / "footer-desktop.png"))
    assert not failures, "\n".join(failures)


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
        consent_banner = page.locator(".consent-banner")
        if consent_banner.is_visible():
            consent_banner.get_by_role("button", name="Reject optional").click()
        page.locator(".site-footer button[data-theme-set='dark']").click()
        assert page.locator("html").get_attribute("data-bs-theme") == "dark"
    assert not failures, failures


def _registration_state() -> tuple[str, str]:
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
        display_name="Windows device",
    )
    client = Client()
    client.force_login(user)
    session = client.session
    session["device_registration_prompt"] = {
        "device_name": device.display_name,
        "os_family": device.os_family,
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
    state = _registration_state()
    with chromium() as browser:
        context = _registration_context(browser, live_server, state)
        page = context.new_page()
        _watch(page, failures)
        page.goto(f"{live_server.url}{reverse('devices:my_devices')}")
        _ready(page)

        dialog = page.get_by_role("dialog", name="Register this device")
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
        expect(dialog).to_be_visible()  # destructive dialogs ignore backdrop clicks
        page.keyboard.press("Escape")
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
            "**/search/suggest/**",
            lambda route: route.fulfill(status=500, body="boom"),
        )
        page.goto(f"{live_server.url}/", wait_until="load")
        _ready(page)

        page.locator("#site-search").press_sequentially("camera", delay=30)
        alert = page.locator("#toast-region .toast-ui--danger")
        expect(alert).to_be_visible(timeout=5_000)
        assert "server hit a problem" in alert.inner_text()
        assert alert.get_attribute("role") == "alert"
        alert.get_by_role("button", name="Dismiss notification").click()
        expect(alert).to_have_count(0)
