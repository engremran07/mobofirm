from __future__ import annotations

import pytest
from django.urls import reverse
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect, sync_playwright


@pytest.mark.django_db(transaction=True)
def test_live_site_map_renders_categories_and_stays_responsive(live_server, tmp_path):
    browser_errors: list[str] = []
    console_errors: list[str] = []
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as error:
            pytest.fail(
                "Chromium is required for sitemap browser checks. "
                "Install it with `.venv\\Scripts\\python.exe -m playwright install chromium`.\n"
                f"{error}"
            )

        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.on("pageerror", lambda error: browser_errors.append(str(error)))
        page.on(
            "console",
            lambda message: (
                console_errors.append(message.text)
                if message.type in {"error", "warning"}
                else None
            ),
        )
        page.goto(
            f"{live_server.url}{reverse('seo_public:human_sitemap')}",
            wait_until="networkidle",
            timeout=15_000,
        )

        expect(page.get_by_role("heading", name="Site map")).to_be_visible()
        sitemap = page.locator(".sitemap-grid")
        for heading in (
            "Explore",
            "Devices and firmware",
            "Tools and guides",
            "Blog and community",
            "Help and information",
        ):
            expect(sitemap.get_by_role("heading", name=heading)).to_be_visible()
        expect(
            page.locator(".sitemap-note").get_by_role("link", name="XML sitemap")
        ).to_have_attribute(
            "href",
            "/sitemap.xml",
        )
        page.screenshot(path=str(tmp_path / "categorized-site-map.png"), full_page=True)

        for width in (390, 768, 1365):
            page.set_viewport_size({"width": width, "height": 900})
            dimensions = page.evaluate(
                "() => ({viewport: document.documentElement.clientWidth, "
                "document: document.documentElement.scrollWidth})"
            )
            assert dimensions["document"] <= dimensions["viewport"] + 1

        page.set_viewport_size({"width": 1365, "height": 900})
        page.goto(
            f"{live_server.url}/sitemap.xml",
            wait_until="networkidle",
            timeout=15_000,
        )
        xml_index = page.locator("sitemapindex")
        expect(xml_index).to_be_visible()
        category_notice = xml_index.evaluate(
            "(node) => getComputedStyle(node, '::after').content"
        )
        for category in (
            "Explore",
            "Devices and firmware",
            "Tools and guides",
            "Blog and community",
            "Help and information",
        ):
            assert category in category_notice
        for width in (390, 768, 1365):
            page.set_viewport_size({"width": width, "height": 900})
            dimensions = page.evaluate(
                "() => ({viewport: document.documentElement.clientWidth, "
                "document: document.documentElement.scrollWidth})"
            )
            assert dimensions["document"] <= dimensions["viewport"] + 1
        page.set_viewport_size({"width": 1365, "height": 900})
        page.screenshot(path=str(tmp_path / "styled-sitemap-index.png"))

        assert not browser_errors
        assert not console_errors
        browser.close()
