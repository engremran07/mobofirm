"""Central search in the browser: header suggestions, request CTAs, results page."""

from __future__ import annotations

import pytest
from django.urls import reverse
from playwright.sync_api import Page

from apps.firmwares.models import Brand, Model
from tests.frontend.browser_support import (
    WIDTHS,
    chromium,
    dismiss_dialogs,
    overflow,
    ready,
    watch,
)

HEADER = "#site-search"
HOSTILE = '"><img src=x onerror=alert(1)>'


def _catalog() -> None:
    zephyrix = Brand.objects.create(name="Zephyrix", slug="zephyrix")
    Brand.objects.create(name="Zebra Mobile", slug="zebra-mobile")
    Model.objects.create(brand=zephyrix, name="Zephyr One", slug="zephyr-one")


def _wait_options(page: Page) -> None:
    page.wait_for_function(
        "() => document.querySelectorAll('#site-search-results [role=option]').length > 0"
    )


@pytest.mark.django_db(transaction=True)
def test_header_search_suggests_per_keystroke_and_navigates_by_keyboard(live_server):
    _catalog()
    failures: list[str] = []
    dialogs: list[str] = []
    requests: list[str] = []

    with chromium() as browser:
        page = browser.new_page(viewport={"width": 1365, "height": 900})
        watch(page, failures)
        dismiss_dialogs(page, dialogs)
        page.on(
            "request",
            lambda request: (
                requests.append(request.url)
                if "/search/suggest/" in request.url
                else None
            ),
        )
        page.goto(f"{live_server.url}/")
        ready(page)

        search = page.locator(HEADER)
        search.click()
        page.keyboard.type("zep")
        _wait_options(page)
        page.wait_for_function(
            "() => [...document.querySelectorAll('#site-search-results .combobox-title')]"
            ".some(node => node.textContent.includes('Zephyrix'))"
        )
        titles = page.locator("#site-search-results .combobox-title").all_inner_texts()
        assert "Zephyrix" in titles
        assert "Zephyr One" in titles
        assert "Zebra Mobile" not in titles
        assert search.get_attribute("aria-expanded") == "true"
        assert any("q=zep" in url for url in requests)

        page.keyboard.press("ArrowDown")
        active = search.get_attribute("aria-activedescendant")
        assert active
        assert page.locator(f"#{active}").get_attribute("aria-selected") == "true"
        page.keyboard.press("Enter")
        page.wait_for_url("**/firmwares/**")
        assert page.url.startswith(f"{live_server.url}/firmwares/")

    assert not dialogs
    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_no_match_offers_request_links_and_results_page_matches(live_server):
    _catalog()
    failures: list[str] = []

    with chromium() as browser:
        page = browser.new_page(viewport={"width": 1365, "height": 900})
        watch(page, failures)
        page.goto(f"{live_server.url}/")
        ready(page)

        page.locator(HEADER).click()
        page.keyboard.type("qqzzvv")
        page.wait_for_function(
            "() => document.querySelector('#site-search-results [data-request-cta]')"
        )
        ctas = page.locator("#site-search-results [data-request-cta]")
        assert ctas.count() == 5
        assert "qqzzvv" in (ctas.first.get_attribute("href") or "")

        page.goto(f"{live_server.url}{reverse('search:results')}?q=qqzzvv")
        ready(page)
        assert page.get_by_text("No results").count() == 1
        assert page.locator("#request-missing a").count() >= 5

        page.goto(f"{live_server.url}{reverse('search:results')}?q=zephyr")
        ready(page)
        assert page.get_by_role("heading", name="Brands").count() == 1
        assert (
            page.locator("section[aria-labelledby='search-group-brand']")
            .get_by_role("link", name="Zephyrix")
            .count()
            == 1
        )

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_hostile_queries_are_rendered_inert(live_server):
    _catalog()
    failures: list[str] = []
    dialogs: list[str] = []

    with chromium() as browser:
        page = browser.new_page(viewport={"width": 1365, "height": 900})
        watch(page, failures)
        dismiss_dialogs(page, dialogs)
        page.goto(f"{live_server.url}{reverse('search:results')}")
        ready(page)

        page.locator(HEADER).click()
        page.keyboard.type(HOSTILE)
        page.wait_for_function(
            "() => document.querySelector('#site-search-results .combobox-empty')"
        )
        assert page.locator("#site-search-results img").count() == 0

        page.goto(f"{live_server.url}{reverse('search:results')}", wait_until="load")
        page.goto(
            f"{live_server.url}/search/?q="
            + "%22%3E%3Cimg+src%3Dx+onerror%3Dalert(1)%3E"
        )
        ready(page)
        assert page.locator("#search-results img").count() == 0
        assert page.locator("#results-query").input_value() == HOSTILE

    assert not dialogs
    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_search_pages_have_no_overflow_or_console_problems(live_server):
    _catalog()
    failures: list[str] = []

    with chromium() as browser:
        page = browser.new_page()
        watch(page, failures)
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            for path in ("/search/", "/search/?q=zephyr", "/search/?q=qqzzvv"):
                page.goto(f"{live_server.url}{path}")
                ready(page)
                dimensions = overflow(page)
                if dimensions["document"] > dimensions["viewport"] + 1:
                    failures.append(f"{path} at {width}px overflows: {dimensions}")
                assert page.locator("h1").count() >= 1

    assert not failures, "\n".join(failures)
