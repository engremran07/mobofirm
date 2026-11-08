"""MFA enrolment page in the browser: scannable QR, hidden setup key toggle."""

from __future__ import annotations

import pytest
from allauth.account.models import EmailAddress
from django.urls import reverse

from tests.frontend.browser_support import (
    WIDTHS,
    chromium,
    dismiss_dialogs,
    login_cookie,
    make_user,
    overflow,
    ready,
    session_context,
    watch,
)


@pytest.mark.django_db(transaction=True)
def test_setup_page_shows_qr_and_toggles_the_setup_key(live_server):
    user = make_user("mfa_browser")
    EmailAddress.objects.create(
        user=user, email=user.email, primary=True, verified=True
    )
    cookies = login_cookie(user)
    failures: list[str] = []
    dialogs: list[str] = []

    with chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page = context.new_page()
        watch(page, failures)
        dismiss_dialogs(page, dialogs)
        page.goto(f"{live_server.url}{reverse('users:mfa_setup')}")
        ready(page)

        qr = page.locator("svg.mfa-qr")
        qr.wait_for(state="visible")
        box = qr.bounding_box()
        assert box is not None
        assert 120 <= box["width"] <= 400
        assert abs(box["width"] - box["height"]) < 2
        assert qr.get_attribute("role") == "img"

        key = page.locator("#mfa-setup-key")
        toggle = page.get_by_role("button", name="Can't scan? Show setup key")
        assert key.is_hidden()
        assert toggle.get_attribute("aria-expanded") == "false"

        toggle.click()
        key.wait_for(state="visible")
        shown = page.locator(".mfa-setup-key").first.inner_text().replace(" ", "")
        assert len(shown) >= 16
        assert (
            page.get_by_role("button", name="Hide setup key").get_attribute(
                "aria-expanded"
            )
            == "true"
        )

        page.get_by_role("button", name="Hide setup key").click()
        assert key.is_hidden()

        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            dimensions = overflow(page)
            assert dimensions["document"] <= dimensions["viewport"] + 1, width
        context.close()

    assert not dialogs
    assert not failures, "\n".join(failures)
