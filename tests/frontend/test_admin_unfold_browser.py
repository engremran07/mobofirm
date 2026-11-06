"""Browser checks for the Unfold admin: sign-in, shell, workflows, theme and layout."""

from __future__ import annotations

import re

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect

from apps.blog.models import Post, PostStatus
from tests.frontend.browser_support import (
    chromium,
    login_cookie,
    overflow,
    ready,
    session_context,
    watch,
)

WIDTHS = (390, 768, 1365)


def _superuser():
    return get_user_model().objects.create_superuser(
        username="browser-admin",
        email="browser-admin@example.test",
        email_verified_at=timezone.now(),
    )


def _go(page: Page, live_server, path: str, failures: list[str]) -> None:
    response = page.goto(f"{live_server.url}{path}", wait_until="load", timeout=20_000)
    if response is None or response.status != 200:
        failures.append(f"{path}: HTTP {response and response.status}")
        return
    ready(page)


def _no_overflow(page: Page, label: str, failures: list[str]) -> None:
    dimensions = overflow(page)
    if dimensions["document"] > dimensions["viewport"] + 1:
        failures.append(f"{label}: horizontal overflow {dimensions}")


@pytest.mark.django_db(transaction=True)
def test_anonymous_admin_visit_goes_through_the_project_login(live_server):
    failures: list[str] = []
    with chromium() as browser:
        page = browser.new_page()
        watch(page, failures)
        page.goto(f"{live_server.url}/admin/", wait_until="load", timeout=20_000)
        assert page.url.startswith(f"{live_server.url}{reverse('users:account_login')}")
        assert "next=%2Fadmin%2F" in page.url
    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_every_sidebar_page_loads_without_console_or_csp_errors(live_server):
    user = _superuser()
    client = Client()
    client.force_login(user)
    sidebar = client.get(reverse("admin:index")).context["sidebar_navigation"]
    links = [item["link"] for group in sidebar for item in group["items"]]
    assert len(links) > 50
    cookies = login_cookie(user)
    failures: list[str] = []

    with chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page = context.new_page()
        watch(page, failures)
        for link in links:
            response = page.goto(
                f"{live_server.url}{link}", wait_until="load", timeout=20_000
            )
            if response is None or response.status != 200:
                failures.append(f"{link}: HTTP {response and response.status}")
                continue
            if not link.startswith("/admin/"):
                continue
            try:
                ready(page)
            except PlaywrightError:
                failures.append(f"{link}: Alpine/htmx did not start")
                continue
            _no_overflow(page, link, failures)
        context.close()

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_admin_shell_workflow_theme_and_layout(live_server):
    user = _superuser()
    post = Post.objects.create(
        title="Browser admin post",
        body="<p>Body for the browser admin test.</p>",
        author=user,
    )
    other = Post.objects.create(
        title="Second browser post",
        body="<p>Another body for the bulk action.</p>",
        author=user,
    )
    draft = Post.objects.create(
        title="Dialog draft",
        body="<p>Body for the dialog workflow.</p>",
        author=user,
    )
    cookies = login_cookie(user)
    failures: list[str] = []

    with chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page = context.new_page()
        watch(page, failures)

        # Dashboard: KPI cards, sidebar and working Alpine/HTMX under the admin CSP.
        _go(page, live_server, "/admin/", failures)
        expect(page.locator("#kpi-pending_comments")).to_be_visible()
        page.get_by_role("heading", name="Content").click()
        expect(page.get_by_role("link", name="Blog posts").first).to_be_visible()
        assert page.evaluate("() => Boolean(window.Alpine && window.htmx)")

        # Sidebar navigation reaches a changelist.
        page.get_by_role("link", name="Blog posts").first.click()
        page.wait_for_url("**/admin/blog/post/")
        expect(page.get_by_text("Browser admin post")).to_be_visible()

        # Bulk action through the changelist form (CSRF-protected POST).
        for title in ("Browser admin post", "Second browser post"):
            page.locator("tr", has_text=title).locator(
                "input[name='_selected_action']"
            ).check()
        page.locator("select[name='action']").select_option("publish_selected")
        page.get_by_role("button", name="Run").click()
        page.wait_for_load_state("load")
        expect(page.get_by_text("Published 2 posts")).to_be_visible()

        # Change form: tabs switch and the dialog action works over HTMX.
        _go(page, live_server, f"/admin/blog/post/{draft.pk}/change/", failures)
        page.locator("nav a", has_text="Publishing").click()
        expect(page.locator(".fieldset-publishing")).to_be_visible()
        page.locator("a[hx-get$='/publish/']").first.click()
        expect(page.get_by_text("Publish this post?")).to_be_visible()
        page.locator("#modal-content button[type='submit']").click()
        page.wait_for_url(f"**/admin/blog/post/{draft.pk}/change/")
        expect(page.get_by_text("Published.")).to_be_visible()

        # Dark mode is user-selectable and applied to the document.
        page.locator("[x-data*='openUserLinks'] div.cursor-pointer").first.click()
        page.locator("[x-on\\:click*=\"switchTheme('dark')\"]").click()
        expect(page.locator("html")).to_have_class(re.compile("dark"))

        # Layout at phone, tablet and desktop widths.
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            for path in (
                "/admin/",
                "/admin/blog/post/",
                f"/admin/blog/post/{draft.pk}/change/",
            ):
                _go(page, live_server, path, failures)
                _no_overflow(page, f"{path} at {width}px", failures)
        context.close()

    assert not failures, "\n".join(failures)
    statuses = Post.objects.filter(pk__in=[post.pk, other.pk, draft.pk]).values_list(
        "status", flat=True
    )
    assert set(statuses) == {PostStatus.PUBLISHED}

    assert not failures, "\n".join(failures)
