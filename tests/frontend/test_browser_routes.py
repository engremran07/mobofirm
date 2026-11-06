from __future__ import annotations

import pytest
from django.urls import get_resolver, reverse
from playwright.sync_api import BrowserContext, Page, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from tests.url_inventory import named_routes

ROUTE_BATCH_SIZE = 20


@pytest.mark.django_db(transaction=True)
def test_all_named_views_load_without_browser_errors(live_server):
    routes = list(named_routes(get_resolver().url_patterns))
    assert routes, "No named URL routes were discovered."
    routes = [
        (route_name, kwargs)
        for route_name, kwargs in routes
        if route_name
        not in {"google_login", "google_callback", "google_login_by_token"}
    ]

    failures: list[str] = []
    current_route = ["browser startup"]

    def report_failure(message: str) -> None:
        failures.append(f"{current_route[0]}: {message}")

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as error:
            pytest.fail(
                f"Chromium is required for frontend route checks. "
                f"Install it with `python -m playwright install chromium`.\n{error}"
            )

        def on_console(message) -> None:
            # Response events check status codes; unauthenticated API 4xxs are expected.
            if message.type == "error" and not message.text.startswith(
                "Failed to load resource:"
            ):
                report_failure(f"browser console error: {message.text}")

        def on_page_error(error: Exception) -> None:
            report_failure(f"uncaught browser exception: {error}")

        def on_response(response) -> None:
            if not response.url.startswith(live_server.url):
                return
            request = response.request
            if response.status >= 500:
                report_failure(f"HTTP {response.status} for {response.url}")
            elif response.status >= 400 and request.resource_type in {
                "font",
                "image",
                "script",
                "stylesheet",
            }:
                report_failure(f"asset returned HTTP {response.status}: {response.url}")

        def on_request_failed(request) -> None:
            if request.url.startswith(live_server.url) and "ERR_ABORTED" not in (
                request.failure or ""
            ):
                report_failure(
                    f"same-origin request failed ({request.failure}): {request.url}"
                )

        def open_context(width: int, height: int) -> tuple[BrowserContext, Page]:
            context = browser.new_context(viewport={"width": width, "height": height})
            page = context.new_page()
            page.on("console", on_console)
            page.on("pageerror", on_page_error)
            page.on("response", on_response)
            page.on("requestfailed", on_request_failed)
            return context, page

        def is_html(response) -> bool:
            # Sitemaps, robots.txt and JSON endpoints have no layout to measure.
            content_type = response.headers.get("content-type", "") if response else ""
            return content_type.startswith("text/html")

        def check_horizontal_overflow(page: Page, viewport_width: int) -> None:
            dimensions = page.evaluate(
                """() => ({
                    viewport: document.documentElement.clientWidth,
                    document: document.documentElement.scrollWidth,
                    body: document.body.scrollWidth
                })"""
            )
            if dimensions["document"] > viewport_width + 1:
                overflowing_elements = page.evaluate(
                    """() => Array.from(document.querySelectorAll("body *"))
                        .map(element => {
                            const rect = element.getBoundingClientRect();
                            return {
                                tag: element.tagName.toLowerCase(),
                                id: element.id,
                                className: typeof element.className === "string"
                                    ? element.className
                                    : "",
                                left: Math.round(rect.left),
                                right: Math.round(rect.right),
                                width: Math.round(rect.width),
                                position: getComputedStyle(element).position
                            };
                        })
                        .filter(element =>
                            element.left < -1 ||
                            element.right > document.documentElement.clientWidth + 1
                        )
                        .slice(0, 10)"""
                )
                report_failure(
                    f"horizontal overflow at {viewport_width}px: "
                    f"document={dimensions['document']}px, "
                    f"body={dimensions['body']}px; "
                    f"elements={overflowing_elements}"
                )

        def visit_batches(width: int, height: int, *, wait_for_runtime: bool) -> None:
            # A fresh context per batch keeps the browser's memory bounded
            # (a single long-lived tab exhausted Chromium's resources).
            for start in range(0, len(routes), ROUTE_BATCH_SIZE):
                context, page = open_context(width, height)
                try:
                    for route_name, kwargs in routes[start : start + ROUTE_BATCH_SIZE]:
                        current_route[0] = f"{route_name} at {width}px"
                        url = (
                            f"{live_server.url}"
                            f"{reverse(route_name, kwargs=kwargs or None)}"
                        )
                        try:
                            response = page.goto(url, wait_until="load", timeout=15_000)
                            if not is_html(response):
                                continue
                            check_horizontal_overflow(page, width)
                            if (
                                wait_for_runtime
                                and page.locator(
                                    'script[type="module"][src*="/static/build/site.js"]'
                                ).count()
                            ):
                                page.wait_for_function(
                                    "() => Boolean(window.htmx && window.Alpine)",
                                    timeout=5_000,
                                )
                        except PlaywrightError as error:
                            report_failure(
                                f"navigation or runtime initialization failed: {error}"
                            )
                finally:
                    context.close()

        visit_batches(1365, 900, wait_for_runtime=True)
        visit_batches(390, 844, wait_for_runtime=False)

        context, page = open_context(390, 844)
        try:
            current_route[0] = "mobile navigation"
            page.goto(f"{live_server.url}/", wait_until="load", timeout=15_000)
            toggle = page.get_by_role("button", name="Toggle navigation")
            mobile_navigation = page.locator("#mobile-navigation")
            toggle.click()
            if (
                toggle.get_attribute("aria-expanded") != "true"
                or mobile_navigation.get_attribute("hidden") is not None
            ):
                report_failure("mobile navigation did not open")
            page.keyboard.press("Escape")
            if (
                toggle.get_attribute("aria-expanded") != "false"
                or mobile_navigation.get_attribute("hidden") is None
            ):
                report_failure("Escape did not close mobile navigation")

            current_route[0] = "homepage HTMX search"
            page.locator("#catalog-query").fill("no-such-device")
            results = page.locator("#search-results .empty-state")
            results.wait_for(state="visible", timeout=5_000)
            if "No matching devices were found" not in results.inner_text():
                report_failure("HTMX search returned an unexpected empty state")
        except PlaywrightError as error:
            report_failure(f"frontend interaction failed: {error}")
        finally:
            context.close()

        browser.close()

    assert not failures, (
        f"Inspected {len(routes)} named Django views; found "
        f"{len(failures)} browser/runtime/resource errors:\n" + "\n".join(failures)
    )


@pytest.mark.django_db(transaction=True)
def test_light_first_theme_toggle_and_preference_persistence(live_server):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(f"{live_server.url}/", wait_until="load", timeout=15_000)
        page.wait_for_function("() => Boolean(window.htmx && window.Alpine)")

        theme_toggle = page.locator("button[data-theme-toggle]:visible").first
        dark_theme_icon = theme_toggle.locator('[data-theme-icon="dark"]')
        light_theme_icon = theme_toggle.locator('[data-theme-icon="light"]')
        light_theme = page.evaluate(
            """() => ({
                page: getComputedStyle(document.documentElement)
                    .getPropertyValue("--mf-page").trim(),
                background: getComputedStyle(document.body).backgroundColor
            })"""
        )
        assert page.locator("html").get_attribute("data-bs-theme") == "light"
        assert light_theme["page"]
        assert light_theme["background"] not in {
            "transparent",
            "rgba(0, 0, 0, 0)",
        }
        assert theme_toggle.get_attribute("aria-label") == "Switch to dark theme"
        assert dark_theme_icon.is_visible() and not light_theme_icon.is_visible()

        theme_toggle.click()
        dark_theme = page.evaluate(
            """() => ({
                page: getComputedStyle(document.documentElement)
                    .getPropertyValue("--mf-page").trim(),
                background: getComputedStyle(document.body).backgroundColor
            })"""
        )
        assert page.locator("html").get_attribute("data-bs-theme") == "dark"
        assert dark_theme["page"] != light_theme["page"]
        assert dark_theme["background"] != light_theme["background"]
        assert theme_toggle.get_attribute("aria-label") == "Switch to light theme"
        assert not dark_theme_icon.is_visible() and light_theme_icon.is_visible()

        page.reload(wait_until="load")
        page.wait_for_function("() => Boolean(window.htmx && window.Alpine)")
        theme_toggle = page.locator("button[data-theme-toggle]:visible").first
        assert page.locator("html").get_attribute("data-bs-theme") == "dark"
        assert theme_toggle.get_attribute("aria-label") == "Switch to light theme"

        theme_toggle.click()
        assert page.locator("html").get_attribute("data-bs-theme") == "light"

        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_role("button", name="Toggle navigation").click()
        mobile_theme_toggle = page.locator(
            "#mobile-navigation button[data-theme-toggle]"
        )
        assert mobile_theme_toggle.is_visible()
        mobile_theme_toggle.click()
        assert page.locator("html").get_attribute("data-bs-theme") == "dark"
        assert (
            page.locator("body").evaluate(
                "(body) => getComputedStyle(body).backgroundColor"
            )
            == dark_theme["background"]
        )

        page.reload(wait_until="load")
        page.wait_for_function("() => Boolean(window.htmx && window.Alpine)")
        assert page.locator("html").get_attribute("data-bs-theme") == "dark"
        browser.close()
