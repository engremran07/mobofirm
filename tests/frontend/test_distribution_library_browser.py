"""Playwright coverage for the public-content media library."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from apps.blog.models import Post, PostStatus
from apps.distribution.models import DistributionSettings, VideoJob
from apps.site_settings.services import clear_site_settings_cache
from tests.frontend.browser_support import chromium, login_cookie
from tests.frontend.console_browser_support import (
    ConsoleServer,
    Probe,
    crawl_page,
    open_console,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def configured_site_url(live_server, settings):
    """Configure the absolute site URL for browser requests to the test server."""
    settings.SITE_URL = live_server.url
    clear_site_settings_cache()
    yield
    clear_site_settings_cache()


def _server(live_server) -> ConsoleServer:
    """Create the staff user and published content used by this browser test."""
    user = get_user_model().objects.create_superuser(
        username="library-admin",
        email="library-admin@example.test",
        email_verified_at=timezone.now(),
    )
    DistributionSettings.objects.update_or_create(
        pk=1, defaults={"video_auto_enlist": False}
    )
    post = Post.objects.create(
        title="A public historical post",
        slug="public-historical-post",
        body="<p>A useful guide to firmware installation.</p>",
        summary="A short summary for the media library.",
        author=user,
    )
    published = timezone.now() - timedelta(minutes=5)
    Post.objects.filter(pk=post.pk).update(
        status=PostStatus.PUBLISHED,
        is_published=True,
        publish_at=published,
        published_at=published,
    )
    return ConsoleServer(live_server.url, user, login_cookie(user))


@pytest.mark.usefixtures("configured_site_url")
def test_media_library_is_responsive_and_creates_selected_drafts(live_server):
    """Verify responsive library rendering and selected-content draft creation."""
    server = _server(live_server)
    path = reverse("admin:distribution_channel_media_library") + "?kind=blog"
    problems: list[str] = []
    with chromium() as browser:
        context = server.context(browser)
        page = context.new_page()
        probe = Probe(origin=server.url)
        probe.attach(page)
        result = crawl_page(
            page, probe, server.url, "distribution", "media library", path, (200,)
        )
        problems.extend(result.problems)

        open_console(page, probe, server, path, problems)
        expect(
            page.get_by_role("link", name="A public historical post")
        ).to_be_visible()
        page.get_by_role("checkbox", name="Select A public historical post").check()
        page.select_option("#library-action", "videos")
        page.get_by_role("button", name="Apply").click()
        expect(page.get_by_text("Created 1 automatic video draft(s)")).to_be_visible()
        expect(page.get_by_role("cell", name="Draft")).to_be_visible()
        context.close()

    post = Post.objects.get(slug="public-historical-post")
    assert (
        VideoJob.objects.filter(
            content_kind="blog", object_ref=f"post:{post.pk}", params__auto=True
        ).count()
        == 1
    )
    assert not problems, "\n".join(problems)
