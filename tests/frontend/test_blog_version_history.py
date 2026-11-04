import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from playwright.sync_api import sync_playwright

from apps.blog.models import Post, PostStatus
from apps.blog.services.history import PostHistoryService


@pytest.mark.django_db(transaction=True)
def test_draft_version_restore_is_responsive_and_functional(live_server):
    author = get_user_model().objects.create_user(
        username="browser-history-author",
        email="browser-history-author@example.com",
        password="test-password",
    )
    post = Post.objects.create(
        title="Browser history original",
        body="<p>Original content for browser history.</p>",
        author=author,
        status=PostStatus.DRAFT,
    )
    revision = PostHistoryService.record_current_version(post, user=author)
    PostHistoryService.update_post(
        post,
        user=author,
        change_summary="Changed before browser restore",
        title="Browser history changed",
    )

    authenticated_client = Client()
    authenticated_client.force_login(author)
    session_cookie = authenticated_client.cookies[settings.SESSION_COOKIE_NAME]
    errors = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 390, "height": 844})
        context.add_cookies(
            [
                {
                    "name": settings.SESSION_COOKIE_NAME,
                    "value": session_cookie.value,
                    "url": live_server.url,
                }
            ]
        )
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(
            f"{live_server.url}"
            f"{reverse('blog:post_history', kwargs={'slug': post.slug})}",
            wait_until="load",
        )

        assert page.get_by_role(
            "button", name="Restore this draft version"
        ).is_visible()
        dimensions = page.evaluate(
            """() => ({
                viewport: document.documentElement.clientWidth,
                document: document.documentElement.scrollWidth
            })"""
        )
        assert dimensions["document"] <= dimensions["viewport"] + 1

        page.get_by_role("button", name="Restore this draft version").click()
        page.get_by_text("Draft restored from the selected version.").wait_for()
        assert page.get_by_text("Browser history original").count() >= 1
        assert not errors

        browser.close()

    post.refresh_from_db()
    assert post.title == "Browser history original"
    assert post.status == PostStatus.DRAFT
    assert post.version == 3
    assert post.revisions.filter(pk=revision.pk).exists()
