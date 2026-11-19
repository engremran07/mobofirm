"""Browser checks for the automatic ad slots (in-article paragraphs and in-feed tiles).

Real consent, real zone resolution, real CSS: on a brand listing the sponsored tile must have
the same box as its sibling tiles at 360, 768 and 1280 px, and on a blog post the in-article
slots must sit between two paragraphs with their space reserved (no layout shift), with no
console errors or overflow.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from apps.ads.models import (
    AdCreative,
    AdPlacement,
    AdsSettings,
    Campaign,
    PlacementAssignment,
)
from apps.blog.models import Post, PostStatus
from apps.firmwares.models import Brand
from tests.frontend.browser_support import chromium, overflow, ready, watch

WIDTHS = (360, 768, 1280)
BANNER = "section.consent-banner"
BRAND_PAGE = "firmwares/brand_list.html"
POST_PAGE = "blog/post_detail.html"
CLS_PROBE = """
window.__cls = 0;
new PerformanceObserver((list) => {
  for (const entry of list.getEntries()) {
    if (!entry.hadRecentInput) window.__cls += entry.value;
  }
}).observe({ type: "layout-shift", buffered: true });
"""
SAME_ROW_BOXES = """() => {
  const ad = document.querySelector("[data-ad-tile]");
  const grid = ad.parentElement;
  const sameRow = [...grid.children].filter(
    (el) => el !== ad && !el.hasAttribute("data-ad-tile")
      && Math.abs(el.getBoundingClientRect().top - ad.getBoundingClientRect().top) < 2
  );
  const box = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return {
      width: r.width, height: r.height, radius: s.borderTopLeftRadius,
      shadow: s.boxShadow, tag: el.tagName, cls: el.className,
    };
  };
  return { ad: box(ad), siblings: sameRow.map(box), tiles: grid.children.length };
}"""
ARTICLE_SLOTS = """() => [...document.querySelectorAll("[data-ad-zone='in_article']")].map((el) => ({
  before: el.previousElementSibling && el.previousElementSibling.tagName,
  after: el.nextElementSibling && el.nextElementSibling.tagName,
  height: el.getBoundingClientRect().height,
  label: el.querySelector(".ad-zone__label")?.textContent,
  parent: el.parentElement.className,
}))"""


def seed() -> str:
    row = AdsSettings.get_solo()
    row.ads_enabled = True
    row.excluded_user_roles = ""
    row.save()
    campaign = Campaign.objects.create(name="Browser house", type="house")
    creatives = [
        AdCreative.objects.create(
            campaign=campaign,
            name=f"Native {index}",
            creative_type="native",
            html=f'<p class="ad-zone__house">Partner offer {index}</p>',
        )
        for index in (1, 2)
    ]
    for page, zone in ((BRAND_PAGE, "between_items"), (POST_PAGE, "in_article")):
        slug = f"{page}-{zone}".replace("/", "-").replace(".", "-")
        placement = AdPlacement.objects.create(
            name=slug, code=slug, slug=slug, template_reference=page, zone=zone
        )
        for creative in creatives:
            PlacementAssignment.objects.create(placement=placement, creative=creative)
    for number in range(1, 13):
        Brand.objects.create(name=f"Alpha {number:02d}", slug=f"alpha-{number:02d}")
    author = get_user_model().objects.create_user(
        username="ad-author", email="ad-author@example.test"
    )
    body = "".join(
        f"<p>Paragraph {number}. "
        + "Reliable flashing needs the right build. " * 14
        + "</p>"
        for number in range(1, 13)
    )
    post = Post.objects.create(
        title="Automatic ad placement guide",
        body=body,
        author=author,
        status=PostStatus.PUBLISHED,
        publish_at=timezone.now() - timedelta(minutes=5),
    )
    return reverse("blog:post_detail", kwargs={"slug": post.slug})


def accept_consent(page, live_server) -> None:
    page.goto(f"{live_server.url}/")
    ready(page)
    page.locator(BANNER).get_by_role("button", name="Accept all").click()
    page.wait_for_load_state("load")


@pytest.mark.django_db(transaction=True)
def test_sponsored_tile_matches_its_sibling_tiles_at_every_width(live_server):
    seed()
    failures: list[str] = []
    with chromium() as browser:
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        context.add_init_script(CLS_PROBE)
        page = context.new_page()
        watch(page, failures)
        accept_consent(page, live_server)

        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(f"{live_server.url}{reverse('firmwares:brand_list')}")
            ready(page)
            tile = page.locator("[data-ad-tile]")
            expect(tile).to_have_count(1)
            expect(tile.get_by_text("Sponsored")).to_be_visible()
            expect(tile.get_by_text("Partner offer", exact=False)).to_be_visible()
            boxes = page.evaluate(SAME_ROW_BOXES)
            assert boxes["siblings"], f"no sibling tile in the ad's row at {width}px"
            for sibling in boxes["siblings"]:
                assert abs(boxes["ad"]["width"] - sibling["width"]) <= 1, (width, boxes)
                assert abs(boxes["ad"]["height"] - sibling["height"]) <= 1, (
                    width,
                    boxes,
                )
                assert boxes["ad"]["radius"] == sibling["radius"], (width, boxes)
                assert boxes["ad"]["shadow"] == sibling["shadow"], (width, boxes)
            assert "card-ui--brand-tile" in boxes["ad"]["cls"]
            assert boxes["tiles"] == 13  # twelve brands and one sponsored tile
            dimensions = overflow(page)
            assert dimensions["document"] <= dimensions["viewport"] + 1, width
            assert page.evaluate("window.__cls") < 0.1, f"layout shift at {width}px"

        visitor = browser.new_context(
            viewport={"width": 1280, "height": 900}
        ).new_page()
        watch(visitor, failures)
        visitor.goto(f"{live_server.url}{reverse('firmwares:brand_list')}")
        ready(visitor)
        assert visitor.locator("[data-ad-tile]").count() == 0, "no ad before consent"

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_in_article_slots_sit_between_paragraphs_with_reserved_space(live_server):
    post_url = seed()
    failures: list[str] = []
    with chromium() as browser:
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        context.add_init_script(CLS_PROBE)
        page = context.new_page()
        watch(page, failures)
        accept_consent(page, live_server)

        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(f"{live_server.url}{post_url}")
            ready(page)
            slots = page.evaluate(ARTICLE_SLOTS)
            assert len(slots) == 2, (
                f"expected two in-article slots at {width}px: {slots}"
            )
            for slot in slots:
                assert (slot["before"], slot["after"]) == ("P", "P"), slot
                assert slot["label"] == "Advertisement"
                assert slot["height"] >= 250, slot
                assert "article-body" in slot["parent"]
            expect(page.locator("[data-ad-zone='in_article']").first).to_contain_text(
                "Partner offer"
            )
            dimensions = overflow(page)
            assert dimensions["document"] <= dimensions["viewport"] + 1, width
            assert page.evaluate("window.__cls") < 0.1, f"layout shift at {width}px"

    assert not failures, "\n".join(failures)
