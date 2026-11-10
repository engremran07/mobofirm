"""Live list filtering, did-you-mean, recent searches and the "/" shortcut."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from playwright.sync_api import Page

from apps.firmwares.models import (
    Brand,
    Chipset,
    DownloadToken,
    Model,
    OfficialFirmware,
)
from tests.frontend.browser_support import WIDTHS, chromium, overflow, ready, watch

CHIPSET_URL = "/firmwares/chipsets/"
HEADER = "#site-search"


def _catalog() -> None:
    for name, vendor, family, extra in (
        ("Dimensity 9200", "MediaTek", "Dimensity", {"aliases": ["MT6985"]}),
        ("Dimensity 9300", "MediaTek", "Dimensity", {}),
        ("Snapdragon 8 Gen 2", "Qualcomm", "Snapdragon", {}),
    ):
        Chipset.objects.create(
            name=name,
            slug=f"{vendor}-{name}".lower().replace(" ", "-"),
            vendor=vendor,
            family=family,
            **extra,
        )
    samsung = Brand.objects.create(name="Samsung", slug="samsung")
    Model.objects.create(brand=samsung, name="Galaxy S23", slug="galaxy-s23")


def _cards(page: Page) -> list[str]:
    return page.locator("#chipset-results a.surface-card strong").all_inner_texts()


def _wait_cards(page: Page, count: int) -> None:
    page.wait_for_function(
        "(count) => document.querySelectorAll("
        "'#chipset-results a.surface-card').length === count",
        arg=count,
    )


@pytest.mark.django_db(transaction=True)
def test_chipset_list_filters_live_per_keystroke_without_reload(live_server):
    """Typing 9200 filters the list over HTMX, pushes the URL and announces it."""
    _catalog()
    failures: list[str] = []
    fragments: list[str] = []

    with chromium() as browser:
        page = browser.new_page(viewport={"width": 1365, "height": 900})
        watch(page, failures)
        page.on(
            "request",
            lambda request: (
                fragments.append(request.url)
                if request.headers.get("hx-request") and "/chipsets/" in request.url
                else None
            ),
        )
        page.goto(f"{live_server.url}{CHIPSET_URL}")
        ready(page)
        assert len(_cards(page)) == 3
        marker = page.evaluate("() => { window.__noReload = true; return true; }")
        assert marker

        page.locator("#chipset-query").click()
        page.keyboard.type("9200")
        _wait_cards(page, 1)
        assert _cards(page) == ["MediaTek Dimensity 9200"]
        assert page.evaluate("() => window.__noReload === true")
        assert "q=9200" in page.url
        assert any("q=9200" in url for url in fragments)
        status = page.locator("#chipset-results [data-search-status]")
        assert "1 chipset" in status.inner_text()
        assert page.locator("#chipset-results").get_attribute("aria-live") == "polite"

        page.locator("#chipset-query").fill("")
        _wait_cards(page, 3)

        page.locator("#chipset-vendor").select_option("Qualcomm")
        _wait_cards(page, 1)
        page.locator("#chipset-query").click()
        page.keyboard.type("mt6985")
        page.wait_for_function(
            "() => document.querySelector('#chipset-results .empty-state')"
        )
        assert "vendor=Qualcomm" in page.url
        assert "q=mt6985" in page.url

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_no_javascript_get_form_filters_chipsets(live_server):
    """Without JavaScript the same form still filters server-side."""
    _catalog()
    with chromium() as browser:
        context = browser.new_context(java_script_enabled=False)
        page = context.new_page()
        page.goto(f"{live_server.url}{CHIPSET_URL}")
        page.fill("#chipset-query", "9200")
        page.click("#chipset-filters button[type=submit]")
        page.wait_for_url("**q=9200**")
        assert _cards(page) == ["MediaTek Dimensity 9200"]
        context.close()


@pytest.mark.django_db(transaction=True)
def test_did_you_mean_link_opens_the_brand(live_server):
    """A misspelt brand on a list page offers the typed suggestion and it works."""
    _catalog()
    failures: list[str] = []

    with chromium() as browser:
        page = browser.new_page(viewport={"width": 1365, "height": 900})
        watch(page, failures)
        page.goto(f"{live_server.url}{CHIPSET_URL}?q=samung")
        ready(page)
        hint = page.locator("[data-did-you-mean]")
        assert "Samsung" in hint.inner_text()
        assert "(brand)" in hint.inner_text()
        assert page.locator("[data-request-cta='chipset']").count() >= 1
        hint.get_by_role("link", name="Samsung").click()
        page.wait_for_url("**/firmwares/samsung/")
        assert page.locator("h1").inner_text().strip() == "Samsung"

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_header_groups_slash_shortcut_and_recent_searches(live_server):
    """Groups carry icons, '/' focuses search, recents are kept and can be cleared."""
    _catalog()
    failures: list[str] = []

    with chromium() as browser:
        page = browser.new_page(viewport={"width": 1365, "height": 900})
        watch(page, failures)
        page.goto(f"{live_server.url}/")
        ready(page)

        page.keyboard.press("/")
        assert page.evaluate("() => document.activeElement.id") == "site-search"
        page.keyboard.type("galaxy")
        group_label = "#site-search-results .combobox-group__label"
        page.wait_for_function(f"() => document.querySelector('{group_label}')")
        assert page.locator(f"{group_label} svg").count() >= 1
        page.keyboard.press("Enter")
        page.wait_for_url("**/search/**")
        assert "q=galaxy" in page.url

        page.goto(f"{live_server.url}/")
        ready(page)
        page.keyboard.press("/")
        recent = "#site-search-results .combobox-recent-option"
        page.wait_for_function(f"() => document.querySelector('{recent}')")
        recents = page.locator(
            "#site-search-results [data-recent-value]"
        ).all_inner_texts()
        assert any("galaxy" in text for text in recents)
        assert page.locator(HEADER).get_attribute("aria-expanded") == "true"

        page.locator("#site-search-results [data-action='clear-recent']").click()
        page.locator("body").click(position={"x": 5, "y": 400})
        stored = page.evaluate(
            "() => window.localStorage.getItem('mf:recent-searches')"
        )
        assert stored is None

        page.keyboard.press("/")
        page.keyboard.type("galaxy")
        page.keyboard.press("Enter")
        page.wait_for_url("**/search/**")
        page.goto(f"{live_server.url}/")
        ready(page)
        page.keyboard.press("/")
        disable = "#site-search-results [data-action=disable-recent]"
        page.wait_for_function(f"() => document.querySelector('{disable}')")
        page.locator("#site-search-results [data-action='disable-recent']").click()
        assert (
            page.evaluate("() => window.localStorage.getItem('mf:recent-searches:off')")
            == "1"
        )
        assert (
            page.evaluate("() => window.localStorage.getItem('mf:recent-searches')")
            is None
        )

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_slash_does_not_hijack_typing_in_other_fields(live_server):
    """Typing '/' inside a field keeps the character and the focus."""
    _catalog()
    with chromium() as browser:
        page = browser.new_page(viewport={"width": 1365, "height": 900})
        page.goto(f"{live_server.url}{CHIPSET_URL}")
        ready(page)
        page.locator("#chipset-query").click()
        page.keyboard.type("a/b")
        assert page.locator("#chipset-query").input_value() == "a/b"
        assert page.evaluate("() => document.activeElement.id") == "chipset-query"


@pytest.mark.django_db(transaction=True)
def test_popular_blocks_load_after_the_page_and_stay_inside_the_viewport(live_server):
    """The placeholder is replaced by the popular blocks without console errors."""
    _catalog()
    brand = Brand.objects.get(slug="samsung")
    model = Model.objects.get(slug="galaxy-s23")
    firmware = OfficialFirmware.objects.create(
        original_file_name="popular-build.zip",
        stored_file_path="/firmware/popular-build.zip",
        brand=brand,
        model=model,
        is_verified=True,
    )
    when = timezone.now()
    DownloadToken.objects.create(
        content_type=ContentType.objects.get_for_model(
            OfficialFirmware, for_concrete_model=False
        ),
        object_id=firmware.pk,
        status=DownloadToken.Status.USED,
        used_at=when,
        expires_at=when + timedelta(hours=1),
    )
    failures: list[str] = []

    with chromium() as browser:
        page = browser.new_page()
        watch(page, failures)
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(f"{live_server.url}/firmwares/")
            ready(page)
            page.wait_for_selector("[data-popular-blocks]")
            titles = page.locator("[data-popular-blocks] h3").all_inner_texts()
            assert "Popular models" in titles
            assert "Popular firmware" in titles
            assert page.locator("[data-popular-slot]").count() == 0
            dimensions = overflow(page)
            assert dimensions["document"] <= dimensions["viewport"] + 1, width

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_list_search_pages_have_no_overflow_or_console_problems(live_server):
    """Search surfaces fit 360, 768 and 1365 px without console or CSP errors."""
    _catalog()
    failures: list[str] = []

    with chromium() as browser:
        page = browser.new_page()
        watch(page, failures)
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            for path in (
                CHIPSET_URL,
                f"{CHIPSET_URL}?q=9200",
                f"{CHIPSET_URL}?q=samung",
                "/firmwares/browse/?q=zzz",
                "/firmwares/brands/?q=samung",
                "/firmwares/tools/?q=odin",
                "/firmwares/guides/?q=galaxy",
                "/tags/?q=boot",
                "/blog/?q=samung",
            ):
                page.goto(f"{live_server.url}{path}")
                ready(page)
                dimensions = overflow(page)
                if dimensions["document"] > dimensions["viewport"] + 1:
                    failures.append(f"{path} at {width}px overflows: {dimensions}")

    assert not failures, "\n".join(failures)
