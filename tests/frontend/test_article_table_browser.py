from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect, sync_playwright

from apps.blog.models import Post, PostStatus
from tests.frontend.browser_support import collect_console


@pytest.mark.django_db(transaction=True)
def test_wide_article_table_scrolls_without_viewport_overflow(live_server):
    author = get_user_model().objects.create_user(
        username="article-table-author",
        email="article-table-author@example.test",
        password="SafePassword123!",
    )
    headers = "".join(f"<th>Heading {index}</th>" for index in range(1, 7))
    cells = "".join(
        f"<td>Specification {index} with a long model detail</td>"
        for index in range(1, 7)
    )
    post = Post.objects.create(
        title="Wide table article",
        slug="wide-table-article",
        body=f"<table><thead><tr>{headers}</tr></thead>"
        f"<tbody><tr>{cells}</tr></tbody></table>",
        author=author,
    )
    Post.objects.filter(pk=post.pk).update(
        status=PostStatus.PUBLISHED,
        is_published=True,
        publish_at=timezone.now(),
        published_at=timezone.now(),
    )

    errors: list[str] = []
    warnings: list[str] = []
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as error:
            pytest.fail(
                "Chromium is required for article-table browser checks. "
                "Install it with `.venv\\Scripts\\python.exe -m playwright install chromium`.\n"
                f"{error}"
            )

        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.on("pageerror", lambda error: errors.append(str(error)))
        collect_console(page, errors, warnings)
        page.goto(
            f"{live_server.url}"
            f"{reverse('blog:post_detail', kwargs={'slug': post.slug})}",
            wait_until="load",
        )

        table_region = page.locator(".article-table--firmware")
        expect(table_region).to_be_visible()
        expect(table_region).to_have_attribute("tabindex", "0")
        narrow_layout = table_region.evaluate(
            "(element) => ({client: element.clientWidth, scroll: element.scrollWidth})"
        )
        assert narrow_layout["scroll"] > narrow_layout["client"]

        for width in (390, 768, 1365):
            page.set_viewport_size({"width": width, "height": 900})
            dimensions = page.evaluate(
                "() => ({viewport: document.documentElement.clientWidth, "
                "document: document.documentElement.scrollWidth})"
            )
            assert dimensions["document"] <= dimensions["viewport"] + 1

        assert not errors, f"Browser errors: {errors}"
        browser.close()

    assert not warnings, f"Browser warnings: {warnings}"
