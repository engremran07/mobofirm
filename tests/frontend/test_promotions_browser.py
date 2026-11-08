"""Browser checks for the offer pages, the site banner and the claim dialog.

One Chromium at a time (machine-wide lock); users and promotions are created before
the browser starts because Playwright's event loop forbids ORM calls inside it.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse

from apps.promotions import lifecycle
from apps.promotions.models import Promotion, PromotionGrant, PromotionKind
from tests.frontend.browser_support import (
    chromium,
    login_cookie,
    make_user,
    ready,
    session_context,
    visit_all,
    watch,
)

LOCK = Path(settings.BASE_DIR) / ".browser_lock"
LOCK_WAIT_SECONDS = 600
LOCK_RETRY_SECONDS = 10


@contextmanager
def browser_lock() -> Iterator[None]:
    """Machine-wide lock so only one Chromium runs at a time (low-RAM host)."""
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            os.mkdir(LOCK)
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                pytest.fail("Timed out waiting for the browser lock.")
            time.sleep(LOCK_RETRY_SECONDS)
    try:
        yield
    finally:
        os.rmdir(LOCK)


def running_offer(**fields: object) -> Promotion:
    defaults: dict[str, object] = {
        "name": "Launch bonus",
        "slug": "launch-bonus",
        "headline": "10 bonus downloads on us",
        "description": "Claim 10 extra new files.",
        "terms": "Bonus files last 30 days.",
        "kind": PromotionKind.BONUS_QUOTA,
        "bonus_files": 10,
        "trial_days": 30,
    }
    defaults.update(fields)
    return lifecycle.activate_now(Promotion.objects.create(**defaults))


@pytest.mark.django_db(transaction=True)
def test_offer_pages_and_banner_have_no_console_csp_or_overflow_problems(live_server):
    offer = running_offer()
    user = make_user("browser_offers")
    cookie = login_cookie(user)
    paths = [
        reverse("promotions:index"),
        reverse("promotions:offer", kwargs={"slug": offer.slug}),
        reverse("shop:pricing"),
    ]
    failures: list[str] = []
    with browser_lock(), chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        watch(page, failures)
        visit_all(page, live_server, paths, failures)
        page.goto(f"{live_server.url}{paths[2]}", wait_until="load")
        ready(page)
        banner_visible = page.locator(".site-banner").first.is_visible()
        banner_text = page.locator(".site-banner").first.inner_text()

    assert banner_visible and "10 bonus downloads on us" in banner_text
    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_the_banner_button_opens_the_offer_and_the_claim_dialog_confirms(live_server):
    offer = running_offer()
    user = make_user("browser_claimer")
    cookie = login_cookie(user)
    failures: list[str] = []
    native: list[str] = []
    with browser_lock(), chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        watch(page, failures)

        def record_native(dialog) -> None:
            native.append(dialog.type)
            dialog.dismiss()

        page.on("dialog", record_native)
        page.goto(f"{live_server.url}{reverse('shop:pricing')}", wait_until="load")
        ready(page)

        page.locator(".site-banner").get_by_role("link", name="See the offer").click()
        page.wait_for_url(f"**/offers/{offer.slug}/**")
        ready(page)
        assert page.get_by_role("heading", name="Your status").is_visible()

        page.get_by_role("link", name="Claim this offer").click()
        dialog = page.locator("#claim-offer-dialog")
        dialog.wait_for(state="visible")
        assert "+10 files" in dialog.inner_text()
        page.keyboard.press("Escape")
        assert not dialog.is_visible()

        page.get_by_role("link", name="Claim this offer").click()
        dialog.wait_for(state="visible")
        dialog.get_by_role("button", name="Claim offer").click()
        page.wait_for_selector('[data-offer-state="claimed"]')
        claimed_text = page.locator("#offer-state-message").inner_text()

    assert "You have taken this offer." in claimed_text
    assert native == []
    assert PromotionGrant.objects.filter(user=user, promotion=offer).count() == 1
    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_a_visitor_can_dismiss_the_banner_and_it_stays_hidden(live_server):
    running_offer()
    failures: list[str] = []
    with browser_lock(), chromium() as browser:
        page = browser.new_context(viewport={"width": 1365, "height": 900}).new_page()
        watch(page, failures)
        page.goto(f"{live_server.url}{reverse('shop:pricing')}", wait_until="load")
        ready(page)
        assert page.locator(".site-banner").count() == 1

        page.get_by_role("button", name="Dismiss announcement").first.click()
        page.wait_for_function("() => !document.querySelector('.site-banner')")
        page.reload(wait_until="load")
        ready(page)
        remaining = page.locator(".site-banner").count()

    assert remaining == 0
    assert not failures, "\n".join(failures)
