"""Browser checks for the staff console: sign-in, every sidebar page, the post workflow."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from apps.blog.models import Post, PostStatus
from tests.frontend.browser_support import chromium, login_cookie, watch
from tests.frontend.console_browser_support import (
    HOOKS,
    ConsoleServer,
    Probe,
    crawl_page,
    open_console,
    visible_first,
)

pytestmark = pytest.mark.django_db(transaction=True)
WIDTHS = (390, 768, 1365)


def _superuser():
    return get_user_model().objects.create_superuser(
        username="browser-admin",
        email="browser-admin@example.test",
        email_verified_at=timezone.now(),
    )


def test_anonymous_admin_visit_goes_through_the_project_login(live_server):
    failures: list[str] = []
    with chromium() as browser:
        page = browser.new_page()
        watch(page, failures)
        page.goto(f"{live_server.url}/admin/", wait_until="load", timeout=20_000)
        assert page.url.startswith(f"{live_server.url}{reverse('users:account_login')}")
        assert "next=%2Fadmin%2F" in page.url
    assert not failures, "\n".join(failures)


def test_every_sidebar_page_loads_cleanly_in_light_and_dark_at_every_width(
    live_server,
):
    user = _superuser()
    client = Client()
    client.force_login(user)
    sidebar = client.get(reverse("admin:index")).context["sidebar_navigation"]
    links = [item["link"] for group in sidebar for item in group["items"]]
    assert len(links) > 50
    server = ConsoleServer(live_server.url, user, login_cookie(user))
    failures: list[str] = []

    with chromium() as browser:
        context = server.context(browser)
        page = context.new_page()
        probe = Probe(origin=server.url)
        probe.attach(page)
        for link in links:
            result = crawl_page(page, probe, server.url, "sidebar", link, link, (200,))
            failures.extend(f"{link}: {problem}" for problem in result.problems)
        context.close()

    assert not failures, "\n".join(failures[:80])


def test_post_workflow_from_dashboard_to_published(live_server):
    user = _superuser()
    posts = [
        Post.objects.create(
            title=title, body=f"<p>Body for {title}.</p>", author=user
        )
        for title in ("Browser admin post", "Second browser post", "Dialog draft")
    ]
    first, second, draft = posts
    server = ConsoleServer(live_server.url, user, login_cookie(user))
    problems: list[str] = []

    with chromium() as browser:
        context = server.context(browser)
        page = context.new_page()
        probe = Probe(origin=server.url)
        probe.attach(page)
        probe.begin()

        # Dashboard: greeting, lazily loaded panels and working HTMX/Alpine under the CSP.
        open_console(page, probe, server, "/admin/", problems)
        expect(page.get_by_role("heading", level=1)).to_be_visible()
        expect(page.locator("#widget-attention")).to_have_attribute(
            "aria-busy", "false"
        )
        assert page.evaluate("() => Boolean(window.Alpine && window.htmx)")

        # Sidebar navigation reaches a changelist.
        page.locator(HOOKS["sidebar"]).get_by_role(
            "link", name="Blog posts"
        ).first.click()
        page.wait_for_url("**/admin/blog/post/")
        expect(page.get_by_text("Browser admin post")).to_be_visible()

        # Bulk action through the changelist form (CSRF-protected POST).
        for post in (first, second):
            page.locator(f"input[data-console-row-select][value='{post.pk}']").check()
        page.locator("select[name='action']").first.select_option("publish_selected")
        page.get_by_role("button", name="Run").first.click()
        page.wait_for_load_state("load")
        expect(page.get_by_text("Published 2 posts")).to_be_visible()

        # Change form: tabs switch and the dialog action works over HTMX.
        open_console(
            page, probe, server, f"/admin/blog/post/{draft.pk}/change/", problems
        )
        page.get_by_role("tab", name="Publishing").click()
        expect(page.get_by_role("tabpanel", name="Publishing")).to_be_visible()
        page.locator("a[hx-get$='/publish/']").first.click()
        dialog = page.locator(HOOKS["dialog_host"])
        expect(dialog.get_by_text("Publish this post?")).to_be_visible()
        dialog.locator("button[type='submit']").click()
        page.wait_for_url(f"**/admin/blog/post/{draft.pk}/change/")
        expect(page.get_by_text("Published.")).to_be_visible()

        # Dark mode is user-selectable and applied to the document.
        visible_first(page, "[data-console-theme-set='dark']").click()
        expect(page.locator("html")).to_have_attribute("data-bs-theme", "dark")

        # Layout at phone, tablet and desktop widths.
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            for path in (
                "/admin/",
                "/admin/blog/post/",
                f"/admin/blog/post/{draft.pk}/change/",
            ):
                open_console(page, probe, server, path, problems)
                viewport, document = page.evaluate(
                    "() => [document.documentElement.clientWidth,"
                    " document.documentElement.scrollWidth]"
                )
                if document > viewport + 1:
                    problems.append(
                        f"{path} at {width}px overflows ({document} > {viewport})"
                    )
        problems.extend(probe.end())
        context.close()

    assert not problems, "\n".join(problems)
    statuses = Post.objects.filter(pk__in=[p.pk for p in posts]).values_list(
        "status", flat=True
    )
    assert set(statuses) == {PostStatus.PUBLISHED}
