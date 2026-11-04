import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from playwright.sync_api import sync_playwright

from apps.users.models import Notification


@pytest.mark.django_db(transaction=True)
def test_notification_detail_uses_explicit_read_action_and_fits_mobile(live_server):
    user = get_user_model().objects.create_user(
        username="browser-notification-reader",
        email="browser-notification-reader@example.com",
        password="test-password",
    )
    notification = Notification.objects.create(
        recipient=user,
        title="Browser notification",
        message="This is an unread notification.",
    )

    authenticated_client = Client()
    authenticated_client.force_login(user)
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
            f"{reverse('users:notification_detail', args=[notification.pk])}",
            wait_until="load",
        )

        assert page.get_by_role("heading", name="Browser notification").is_visible()
        assert page.get_by_role("button", name="Mark as read").is_visible()
        dimensions = page.evaluate(
            """() => ({
                viewport: document.documentElement.clientWidth,
                document: document.documentElement.scrollWidth
            })"""
        )
        assert dimensions["document"] <= dimensions["viewport"] + 1

        page.get_by_role("button", name="Mark as read").click()
        page.get_by_role("status").get_by_text("Read").wait_for()
        assert page.get_by_role("button", name="Mark as read").count() == 0
        assert not errors

        browser.close()

    notification.refresh_from_db()
    assert notification.is_read
