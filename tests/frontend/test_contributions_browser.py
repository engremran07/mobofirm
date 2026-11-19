"""Browser checks: My contributions hub and tabs, the legacy redirect and the contribute pages."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from apps.blog.models import BlogSettings, Category
from tests.frontend.browser_support import (
    WIDTHS,
    chromium,
    dismiss_dialogs,
    login_cookie,
    make_user,
    overflow,
    session_context,
    watch,
)


@pytest.mark.django_db(transaction=True)
def test_my_contributions_tabs_redirect_and_contribute_pages(live_server) -> None:
    config = BlogSettings.get_solo()
    config.enable_blog = True
    config.allow_user_blog_posts = True
    config.save()
    Category.objects.create(name="Guides", slug="guides")
    member = make_user("contrib_browser")
    member.profile_completed = True
    member.save(update_fields=["profile_completed"])
    cookies = login_cookie(member)
    failures: list[str] = []
    natives: list[str] = []

    with chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page = context.new_page()
        watch(page, failures)
        dismiss_dialogs(page, natives)

        page.goto(f"{live_server.url}/files/")
        page.wait_for_url("**/contributions/")
        expect(page.get_by_role("heading", level=1)).to_have_text("What you have shared")
        nav = page.get_by_role("navigation", name="My contributions sections")
        for label in (
            "Overview",
            "Firmware contributions",
            "Blog contributions",
            "Downloads",
            "Quota",
        ):
            expect(nav.get_by_role("link", name=label)).to_be_visible()
        expect(page.get_by_role("link", name="My files")).to_have_count(0)

        nav.get_by_role("link", name="Blog contributions").click()
        page.wait_for_url("**/contributions/blog/")
        expect(
            page.get_by_text("You have not contributed a blog post")
        ).to_be_visible()

        page.get_by_role("link", name="Contribute a blog post").first.click()
        page.wait_for_url("**/blog/contribute/")
        expect(page.locator("select[name=access] option[value=free]")).to_have_count(1)
        expect(page.locator("select[name=access] option[value=featured]")).to_have_count(1)
        expect(page.locator("select[name=access] option[value=paid]")).to_have_count(0)
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            dimensions = overflow(page)
            assert dimensions["document"] <= dimensions["viewport"] + 1, width

        page.goto(f"{live_server.url}/firmwares/upload/?brand=1")
        page.wait_for_url("**/firmwares/contribute/?brand=1")
        expect(page.get_by_role("heading", level=1)).to_have_text("Contribute firmware")
        expect(page.get_by_role("link", name="Upload firmware")).to_have_count(0)
        context.close()

    assert natives == []
    assert failures == []
