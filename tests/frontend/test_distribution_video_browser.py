"""Browser check of the staff Videos page: clean at 360 and 1280 px in light and dark, with the
rendering-tools report, a ready video with its player and the live region."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from apps.distribution.models import VideoJob
from tests.frontend.browser_support import chromium, login_cookie
from tests.frontend.console_browser_support import (
    ConsoleServer,
    Probe,
    crawl_page,
    open_console,
)

pytestmark = pytest.mark.django_db(transaction=True)
JPEG = (
    bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffdb004300")
    + b"\x00" * 80
    + b"\xff\xd9"
)


def _server(live_server) -> ConsoleServer:
    user = get_user_model().objects.create_superuser(
        username="video-admin",
        email="video-admin@example.test",
        email_verified_at=timezone.now(),
    )
    for index, status in enumerate(("ready", "approved", "rendering", "failed")):
        token = f"browser{index}"
        VideoJob.objects.create(
            content_kind="firmware",
            object_ref=f"official:{index}",
            title=f"Northstar Atlas {index} Global firmware",
            template="firmware-release",
            idempotency_key=f"browser-{index}",
            status=status,
            video_name=f"distribution/videos/{token}/video.mp4"
            if status in {"ready", "approved"}
            else "",
            poster_name=f"distribution/videos/{token}/poster.jpg"
            if status in {"ready", "approved"}
            else "",
            duration_ms=22_000,
            size_bytes=9_000_000,
            error="Render failed in the test" if status == "failed" else "",
        )
        if status in {"ready", "approved"}:
            default_storage.save(
                f"distribution/videos/{token}/video.mp4", ContentFile(b"\x00" * 2048)
            )
            default_storage.save(
                f"distribution/videos/{token}/poster.jpg", ContentFile(JPEG)
            )
    return ConsoleServer(live_server.url, user, login_cookie(user))


def test_videos_page_is_clean_at_every_width_in_light_and_dark(live_server):
    server = _server(live_server)
    path = reverse("admin:distribution_channel_videos")
    with chromium() as browser:
        context = server.context(browser)
        page = context.new_page()
        probe = Probe(origin=server.url)
        probe.attach(page)
        result = crawl_page(
            page, probe, server.url, "distribution", "videos", path, (200,)
        )
        context.close()
    assert not result.problems, "\n".join(result.problems)


def test_videos_page_filters_and_keyboard_flow(live_server):
    server = _server(live_server)
    path = reverse("admin:distribution_channel_videos")
    problems: list[str] = []
    with chromium() as browser:
        context = server.context(browser)
        page = context.new_page()
        probe = Probe(origin=server.url)
        probe.attach(page)
        open_console(page, probe, server, path, problems)
        page.set_viewport_size({"width": 1280, "height": 900})
        expect(page.locator(".c-video")).to_have_count(4)
        expect(
            page.get_by_text("Nothing is posted without your approval")
        ).to_be_visible()
        page.select_option("#vf-status", "ready")
        expect(page.locator(".c-video")).to_have_count(1)
        approve = page.get_by_role("button", name="Approve")
        approve.focus()
        page.keyboard.press("Enter")
        page.wait_for_load_state("load")
        expect(page.locator(".c-video--approved")).to_have_count(2)
        context.close()
    assert not problems, "\n".join(problems)
