"""Responsive typography and overflow of the main public routes (light and dark).

Headings use the central fluid scale: page titles top out at 3rem and the home hero at
2.5rem, nothing may scroll horizontally, and no heading may spill out of its box.
These checks read the built stylesheet, so run ``vite build`` after changing SCSS.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.blog.models import Category, Post, PostStatus
from apps.firmwares.models import OfficialFirmware
from tests.frontend.browser_support import ready, watch
from tests.frontend.catalog_browser_support import locked_chromium, seed_catalog

WIDTHS = (360, 768, 1280, 1920)
SCHEMES = ("light", "dark")
TITLE_LIMIT_PX = 48.5
HOME_LIMIT_PX = 40.5
MAX_LINES_AT_PHONE = 4

# Elements wider than the viewport that sit inside a clipping or scrolling ancestor
# (decorative glows, the category chip bar, scrollable tables) cannot cause page scroll.
MEASURE = """() => {
  const limit = document.documentElement.clientWidth;
  const clipped = (element) => {
    for (let node = element.parentElement; node; node = node.parentElement) {
      const overflow = getComputedStyle(node).overflowX;
      if (overflow !== 'visible') return true;
    }
    return false;
  };
  const offenders = [...document.querySelectorAll('body *')]
    .filter((e) => e.getBoundingClientRect().right > limit + 1)
    .filter((e) => !e.closest('.visually-hidden, [hidden]') && !clipped(e))
    .slice(0, 6)
    .map((e) => `${e.tagName.toLowerCase()}.${String(e.className).slice(0, 40)}`);
  const headings = [...document.querySelectorAll('h1')].map((h) => {
    const style = getComputedStyle(h);
    const line = parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.2;
    return {
      text: h.textContent.trim().slice(0, 40),
      size: parseFloat(style.fontSize),
      lines: Math.round(h.getBoundingClientRect().height / line),
      spills: h.scrollWidth > h.clientWidth + 1,
    };
  });
  return {
    viewport: limit,
    document: document.documentElement.scrollWidth,
    offenders,
    headings,
  };
}"""


@pytest.fixture(name="public_site")
def seeded_public_site(request):
    request.getfixturevalue("transactional_db")
    data = seed_catalog()
    firmware = OfficialFirmware.objects.create(
        original_file_name="responsive.zip",
        stored_file_path="/firmware/responsive.zip",
        build_number="RESP.240101",
        brand=data["acme"],
        model=data["model"],
        is_verified=True,
        view_count=1234,
        download_count=56,
    )
    author = get_user_model().objects.create_user(
        username="responsive-author", email="responsive-author@example.test"
    )
    category = Category.objects.create(name="Guides")
    posts = [
        Post.objects.create(
            title=f"Responsive guide {index}",
            summary="A summary that is long enough to wrap on a narrow screen.",
            body="<p>Body text for the responsive guide.</p>" * 3,
            author=author,
            category=category,
            status=PostStatus.PUBLISHED,
            publish_at=timezone.now() - timedelta(hours=index + 1),
            views_count=1000 * (index + 1),
        )
        for index in range(3)
    ]
    return {
        "paths": [
            "/",
            "/firmwares/browse/",
            "/blog/",
            posts[0].get_absolute_url(),
            reverse("firmwares:firmware_detail", args=["official", firmware.pk]),
            "/marketplace/",
            "/shop/",
            "/accounts/login/",
        ]
    }


@pytest.mark.django_db(transaction=True)
def test_public_routes_keep_sane_headings_and_never_overflow(
    live_server, public_site
) -> None:
    failures: list[str] = []
    problems: list[str] = []
    with locked_chromium() as browser:
        for scheme in SCHEMES:
            for width in WIDTHS:
                context = browser.new_context(
                    viewport={"width": width, "height": 900}, color_scheme=scheme
                )
                page = context.new_page()
                watch(page, failures)
                for path in public_site["paths"]:
                    label = f"{path} at {width}px {scheme}"
                    response = page.goto(
                        f"{live_server.url}{path}", wait_until="load", timeout=20_000
                    )
                    if response is None or response.status != 200:
                        problems.append(f"{label}: HTTP {response and response.status}")
                        continue
                    ready(page)
                    measured = page.evaluate(MEASURE)
                    if measured["document"] > measured["viewport"] + 1:
                        problems.append(f"{label}: page scrolls sideways {measured}")
                    if measured["offenders"]:
                        problems.append(f"{label}: overflowing {measured['offenders']}")
                    if not measured["headings"]:
                        problems.append(f"{label}: no <h1>")
                    for heading in measured["headings"]:
                        limit = HOME_LIMIT_PX if path == "/" else TITLE_LIMIT_PX
                        if heading["size"] > limit:
                            problems.append(
                                f"{label}: h1 {heading['size']}px > {limit}px {heading}"
                            )
                        if heading["spills"]:
                            problems.append(f"{label}: h1 spills out of its box")
                        if width == 360 and heading["lines"] > MAX_LINES_AT_PHONE:
                            problems.append(f"{label}: h1 wraps to {heading['lines']} lines")
                context.close()

    assert not problems, "\n".join(problems)
    assert not failures, "\n".join(failures)
