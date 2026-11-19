"""Browser check of the distribution platform picker: clean at every width in light and dark,
search narrows the menu, and the setup guide works from the keyboard."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from tests.frontend.browser_support import chromium, login_cookie
from tests.frontend.console_browser_support import (
    ConsoleServer,
    Probe,
    crawl_page,
    open_console,
)

pytestmark = pytest.mark.django_db(transaction=True)


def _server(live_server) -> ConsoleServer:
    user = get_user_model().objects.create_superuser(
        username="picker-admin",
        email="picker-admin@example.test",
        email_verified_at=timezone.now(),
    )
    return ConsoleServer(live_server.url, user, login_cookie(user))


def test_picker_is_clean_at_every_width_in_light_and_dark(live_server):
    server = _server(live_server)
    path = reverse("admin:distribution_channel_platforms")
    with chromium() as browser:
        context = server.context(browser)
        page = context.new_page()
        probe = Probe(origin=server.url)
        probe.attach(page)
        result = crawl_page(
            page, probe, server.url, "distribution", "picker", path, (200,)
        )
        context.close()
    assert not result.problems, "\n".join(result.problems)


def test_feeds_index_and_admin_feeds_page_are_clean(live_server):
    server = _server(live_server)
    with chromium() as browser:
        context = server.context(browser)
        page = context.new_page()
        probe = Probe(origin=server.url)
        probe.attach(page)
        problems: list[str] = []
        for path, group in (
            (reverse("feeds:index"), "public"),
            (reverse("admin:distribution_channel_feeds"), "admin"),
        ):
            result = crawl_page(page, probe, server.url, group, path, path, (200,))
            problems.extend(f"{path}: {p}" for p in result.problems)
        response = page.request.get(f"{server.url}/feeds/latest.rss")
        assert response.status == 200
        assert "rss" in response.headers["content-type"]
        context.close()
    assert not problems, "\n".join(problems)


def test_search_and_setup_guide_work_from_the_keyboard(live_server):
    server = _server(live_server)
    path = reverse("admin:distribution_channel_platforms")
    problems: list[str] = []
    with chromium() as browser:
        context = server.context(browser)
        page = context.new_page()
        probe = Probe(origin=server.url)
        probe.attach(page)
        open_console(page, probe, server, path, problems)
        page.set_viewport_size({"width": 1280, "height": 900})
        search = page.get_by_role("searchbox", name="Search platforms")
        search.focus()
        page.keyboard.type("whats")
        expect(page.locator(".c-platform")).to_have_count(2)
        expect(page.locator(".c-platform__name")).to_contain_text(
            ["WhatsApp (Cloud API)", "WhatsApp Channel (assisted)"]
        )
        guide = page.get_by_role(
            "link", name="Setup guide for WhatsApp Channel (assisted)"
        )
        guide.focus()
        page.keyboard.press("Enter")
        dialog = page.locator("#console-dialog .modal-title")
        expect(dialog).to_have_text("WhatsApp Channel (assisted)")
        expect(page.locator("#console-dialog")).to_contain_text(
            "Assisted, not automatic"
        )
        expect(page.locator("#console-dialog.show")).to_have_count(1)
        page.wait_for_timeout(400)
        page.keyboard.press("Escape")
        expect(page.locator("#console-dialog.show")).to_have_count(0)
        context.close()
    assert not problems, "\n".join(problems)
