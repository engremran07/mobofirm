"""Browser checks of the public tool and guide categories.

Only categories with an active tool or a published guide are listed; an empty
category is a 404 and never appears in the navigation. Pages must not overflow at
360 and 1280 px and must log no console errors or warnings.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect, sync_playwright

from apps.blog.models import Post, PostStatus
from apps.firmwares.chipsets import create_chipset_record
from apps.firmwares.models import (
    Brand,
    FlashingGuideTemplate,
    FlashingTool,
    FlashingToolCategory,
    GeneratedFlashingGuide,
)
from apps.firmwares.tool_categories import sync_reference_categories

WIDTHS = (360, 1280)


def _seed():
    sync_reference_categories()
    acme = Brand.objects.create(name="Acme", slug="acme")
    Brand.objects.create(name="Lonely", slug="lonely")
    legacy = FlashingToolCategory.objects.create(name="Legacy", slug="legacy")
    chipset = create_chipset_record(
        vendor="MediaTek", family="Helio", name="Helio G99", slug="mediatek-helio-g99"
    )
    tool = FlashingTool.objects.create(
        category=legacy, name="Acme Flasher", slug="acme-flasher"
    )
    tool.chipsets.add(chipset)
    tool.supported_brands.add(acme)
    author = get_user_model().objects.create_user(
        username="cat-author", email="cat-author@example.test"
    )
    post = Post.objects.create(
        title="Flash an Acme", body="<p>Steps</p>", author=author
    )
    Post.objects.filter(pk=post.pk).update(
        status=PostStatus.PUBLISHED,
        is_published=True,
        publish_at=timezone.now() - timedelta(minutes=1),
        published_at=timezone.now() - timedelta(minutes=1),
    )
    template = FlashingGuideTemplate.objects.create(
        title_template="Flash an Acme",
        body_template="{steps}",
        guide_type=FlashingGuideTemplate.GuideType.STOCK_FLASH,
        brand=acme,
    )
    GeneratedFlashingGuide.objects.create(template=template, post=post, brand=acme)


@pytest.mark.django_db(transaction=True)
def test_only_non_empty_categories_are_public_and_pages_render_cleanly(live_server):
    _seed()
    console_messages: list[tuple[str, str]] = []
    page_errors: list[str] = []

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as error:
            pytest.fail(
                "Chromium is required for category browser checks. Install it with "
                "`.venv\\Scripts\\python.exe -m playwright install chromium`.\n"
                f"{error}"
            )
        page = browser.new_page(viewport={"width": 360, "height": 800})
        page.on(
            "console",
            lambda message: console_messages.append((message.type, message.text)),
        )
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        base = live_server.url

        def check_layout() -> None:
            for width in WIDTHS:
                page.set_viewport_size({"width": width, "height": 900})
                dims = page.evaluate(
                    "() => ({v: document.documentElement.clientWidth, d: document.documentElement.scrollWidth})"
                )
                assert dims["d"] <= dims["v"] + 1, (page.url, width)

        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(
                f"{base}{reverse('firmwares:flashing_tools')}", wait_until="networkidle"
            )
            browse = page.locator("[data-browse-categories]")
            expect(browse).to_be_visible()
            for slug in (
                "chipsets-mediatek",
                "chipsets-mediatek-helio",
                "brand-acme",
                "os-android",
            ):
                expect(browse.locator(f'a[href$="/category/{slug}/"]')).to_have_count(1)
            for slug in (
                "chipsets-qualcomm",
                "brand-lonely",
                "os-ios",
                "chipsets-mediatek-dimensity",
            ):
                expect(browse.locator(f'a[href*="{slug}"]')).to_have_count(0)
        check_layout()

        page.goto(
            f"{base}/firmwares/tools/category/brand-acme/", wait_until="networkidle"
        )
        expect(page.get_by_role("heading", name="Acme", exact=True)).to_be_visible()
        expect(page.get_by_role("link", name="Acme Flasher")).to_be_visible()
        expect(page.get_by_role("link", name="Flash an Acme")).to_be_visible()
        check_layout()

        page.goto(
            f"{base}/firmwares/tools/category/chipsets-mediatek/",
            wait_until="networkidle",
        )
        expect(page.get_by_role("link", name="MediaTek Helio")).to_be_visible()
        assert page.locator('a[href*="dimensity"]').count() == 0
        check_layout()

        for slug in ("chipsets-qualcomm", "brand-lonely", "os-ios"):
            assert (
                page.request.get(f"{base}/firmwares/tools/category/{slug}/").status
                == 404
            )
        redirect = page.request.get(
            f"{base}/firmwares/tools/category/chipsets-mediatek-dimensity/",
            max_redirects=0,
        )
        assert redirect.status == 302
        assert redirect.headers["location"].endswith("/chipsets-mediatek/")

        page.goto(
            f"{base}{reverse('firmwares:flashing_guides')}", wait_until="networkidle"
        )
        select = page.locator("#guide-category")
        expect(select).to_be_visible()
        options = select.locator("option").all_text_contents()
        assert (
            "Acme" in options
            and "Lonely" not in options
            and "Qualcomm chipsets" not in options
        )
        select.select_option("brand-acme")
        expect(page.get_by_role("link", name="Flash an Acme")).to_be_visible()
        check_layout()

        assert not page_errors
        assert not [
            item for item in console_messages if item[0] in {"error", "warning"}
        ]
        browser.close()
