from __future__ import annotations

import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.firmwares.models import (
    Brand,
    FlashingTool,
    FlashingToolCategory,
    Model,
    OfficialFirmware,
)
from tests.frontend.browser_support import chromium, ready, visit_all, watch


@pytest.mark.django_db(transaction=True)
def test_seeded_pages_render_without_console_csp_or_overflow_problems(live_server):
    call_command("seed_pages")
    paths = [reverse("pages:index")] + [
        reverse("pages:page", kwargs={"slug": slug})
        for slug in (
            "about",
            "contact",
            "privacy",
            "terms",
            "consent",
            "dmca",
            "firmware-disclaimer",
            "how-to-flash",
            "faq",
            "advertise",
        )
    ]
    failures: list[str] = []
    with chromium() as browser:
        page = browser.new_page()
        watch(page, failures)
        visit_all(page, live_server, paths, failures)

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_contents_links_jump_to_headings_and_the_page_has_one_main_landmark(
    live_server,
):
    call_command("seed_pages")
    failures: list[str] = []
    with chromium() as browser:
        page = browser.new_page(viewport={"width": 1365, "height": 700})
        watch(page, failures)
        page.goto(
            f"{live_server.url}{reverse('pages:page', kwargs={'slug': 'privacy'})}"
        )
        ready(page)

        toc = page.get_by_role("navigation", name="On this page")
        assert toc.is_visible()
        link = toc.get_by_role("link", name="Cookies")
        link.click()
        assert page.url.endswith("#section-cookies")
        assert page.locator("#section-cookies").is_visible()
        assert page.locator("main").count() == 1
        assert page.locator("article.eco-prose").count() == 1
        assert page.get_by_text("Last updated").is_visible()
        breadcrumbs = page.get_by_role("navigation", name="Breadcrumb")
        assert breadcrumbs.get_by_role("link", name="Help").is_visible()

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_contents_box_sits_beside_the_article_on_wide_screens_and_above_it_on_phones(
    live_server,
):
    call_command("seed_pages")
    url = f"{live_server.url}{reverse('pages:page', kwargs={'slug': 'privacy'})}"
    with chromium() as browser:
        page = browser.new_page(viewport={"width": 1365, "height": 800})
        page.goto(url)
        article = page.locator("article.eco-prose").bounding_box()
        toc = page.locator("nav.eco-toc").bounding_box()
        assert article and toc
        assert toc["x"] > article["x"] + article["width"] - 1

        page.set_viewport_size({"width": 360, "height": 800})
        article = page.locator("article.eco-prose").bounding_box()
        toc = page.locator("nav.eco-toc").bounding_box()
        assert article and toc
        assert (
            toc["y"] > article["y"] + article["height"] - 1
            or toc["x"] <= article["x"] + 1
        )


@pytest.mark.django_db(transaction=True)
def test_help_index_uses_help_urls_and_separates_widgets_from_page_cards(live_server):
    call_command("seed_pages")
    brand = Brand.objects.create(name="Spacing Brand", slug="spacing-brand")
    model = Model.objects.create(brand=brand, name="S1", slug="s1")
    OfficialFirmware.objects.create(
        original_file_name="spacing.zip",
        stored_file_path="/firmware/spacing.zip",
        brand=brand,
        model=model,
        is_verified=True,
        is_active=True,
    )
    with chromium() as browser:
        page = browser.new_page(viewport={"width": 1090, "height": 800})
        page.goto(f"{live_server.url}/help/")
        ready(page)

        assert page.url.endswith("/help/")
        cards = page.locator(".card-grid").bounding_box()
        widgets = page.locator(".widget-grid").bounding_box()
        assert cards and widgets
        assert widgets["y"] - (cards["y"] + cards["height"]) >= 24
        assert page.locator(".card-grid a[href^='/pages/']").count() == 0

        dimensions = page.evaluate(
            "() => ({width: document.documentElement.clientWidth, "
            "scroll: document.documentElement.scrollWidth})"
        )
        assert dimensions["scroll"] <= dimensions["width"], dimensions

        page.get_by_role("button", name="Community menu").click()
        panel = page.locator("#mega-community").bounding_box()
        assert panel
        assert panel["x"] >= 0
        width = page.evaluate("() => document.documentElement.clientWidth")
        assert panel["x"] + panel["width"] <= width
        dimensions = page.evaluate(
            "() => ({width: document.documentElement.clientWidth, "
            "scroll: document.documentElement.scrollWidth})"
        )
        assert dimensions["scroll"] <= dimensions["width"], dimensions

        response = page.goto(f"{live_server.url}/pages/")
        assert response and response.status == 200
        assert page.url.endswith("/help/")


@pytest.mark.django_db(transaction=True)
def test_homepage_integration_blocks_and_footer_navigation(live_server):
    call_command("seed_pages")
    brand = Brand.objects.create(name="Browser Brand", slug="browser-brand")
    model = Model.objects.create(brand=brand, name="B1", slug="b1")
    OfficialFirmware.objects.create(
        original_file_name="browser.zip",
        stored_file_path="/firmware/browser.zip",
        brand=brand,
        model=model,
        is_verified=True,
        is_active=True,
    )
    category = FlashingToolCategory.objects.create(name="Browser", slug="browser")
    FlashingTool.objects.create(
        category=category,
        name="Browser Flasher",
        slug="browser-flasher",
        is_active=True,
    )
    failures: list[str] = []
    with chromium() as browser:
        page = browser.new_page(viewport={"width": 1365, "height": 900})
        watch(page, failures)
        page.goto(f"{live_server.url}/")
        ready(page)

        section = page.get_by_role("region", name="Fresh from the library")
        assert section.is_visible()
        assert section.get_by_role("link", name="Browser Brand B1").count() >= 1
        assert section.get_by_role("link", name="Browser Flasher").is_visible()
        more = page.get_by_role("region", name="Packages, marketplace and help")
        assert more.get_by_role("link", name="Marketplace").is_visible()

        footer = page.locator("footer")
        footer.get_by_role("link", name="How to flash").click()
        page.wait_for_url("**/help/how-to-flash/")
        assert page.locator("h1").inner_text().startswith("How to flash firmware")

        sitemap_link = page.get_by_role(
            "navigation", name="Footer legal navigation"
        ).get_by_role("link", name="XML sitemap")
        assert sitemap_link.is_visible()
        with page.expect_navigation() as navigation:
            sitemap_link.click()
        response = navigation.value
        assert response and response.status == 200
        assert response.headers["content-type"].startswith("application/xml")

    assert not failures, "\n".join(failures)
