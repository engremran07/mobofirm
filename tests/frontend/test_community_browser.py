"""Community pages (badges, leaderboard, bounties) in a real browser."""

from __future__ import annotations

import uuid

import pytest
from django.conf import settings
from django.utils import timezone
from playwright.sync_api import Dialog

from apps.bounty import services as bounty_services
from apps.consent import services as consent_services
from apps.firmwares.models import Brand, Model
from apps.firmwares.tracking_models import FirmwareRequest
from apps.gamification import services as gamification
from apps.wallet import api as wallet
from tests.frontend.browser_support import (
    WIDTHS,
    chromium,
    login_cookie,
    make_user,
    overflow,
    ready,
    watch,
)


def decided_cookie() -> dict[str, str]:
    policy = consent_services.active_policy()
    value = consent_services.encode_cookie(policy.version, [], uuid.uuid4())
    return {consent_services.cookie_name(): value}


def context_with(browser, live_server, cookies, width=1365):
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.add_cookies(
        [
            {"name": name, "value": value, "url": live_server.url}
            for name, value in cookies.items()
        ]
    )
    return context


@pytest.mark.django_db(transaction=True)
def test_public_community_pages_render_cleanly_at_every_width(live_server):
    member = make_user("ranked")
    gamification.award(member, "firmware.approved", key="browser-1")
    failures: list[str] = []
    cookies = decided_cookie()
    with chromium() as browser:
        for width in WIDTHS:
            page = context_with(browser, live_server, cookies, width).new_page()
            watch(page, failures)
            for path in ("/community/badges/", "/leaderboard/", "/bounty/"):
                response = page.goto(f"{live_server.url}{path}")
                assert response and response.status == 200, path
                ready(page)
                dims = overflow(page)
                assert dims["document"] <= dims["viewport"] + 1, f"{path} at {width}"
                assert page.locator("h1").count() == 1
            page.goto(f"{live_server.url}/leaderboard/")
            assert page.get_by_role("cell", name="ranked").is_visible()
            page.goto(f"{live_server.url}/community/badges/")
            assert page.get_by_text("First upload").first.is_visible()
            assert page.locator("svg.lucide").count() > 0
            page.context.close()
    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_bounty_can_be_posted_viewed_and_cancelled_with_an_app_dialog(live_server):
    poster = make_user("bounty-poster")
    wallet.credit(poster, 300, reason=wallet.LedgerReason.REWARD, key="seed-browser")
    brand = Brand.objects.create(name="Acme", slug="acme")
    model = Model.objects.create(brand=brand, name="Phone One", slug="phone-one")
    request = FirmwareRequest.objects.create(
        brand=brand, model=model, firmware_type="official", requested_by=poster
    )
    cookies = {**login_cookie(poster), **decided_cookie()}
    failures: list[str] = []
    dialogs: list[str] = []
    with chromium() as browser:
        context = context_with(browser, live_server, cookies)
        page = context.new_page()
        watch(page, failures)

        def record_dialog(dialog: Dialog) -> None:
            dialogs.append(dialog.type)
            dialog.dismiss()

        page.on("dialog", record_dialog)
        page.goto(f"{live_server.url}/bounty/new/?request={request.pk}")
        ready(page)
        assert page.get_by_text("Acme Phone One").first.is_visible()
        page.get_by_label("Reward (credits)").fill("120")
        page.get_by_role("button", name="Escrow credits and post").click()
        page.wait_for_url("**/bounty/*/")
        assert page.get_by_role(
            "heading", name="Acme Phone One (official firmware)"
        ).is_visible()
        assert page.get_by_text("120 credits").first.is_visible()

        page.get_by_role("link", name="Cancel and get credits back").click()
        dialog = page.locator("dialog[open]")
        dialog.wait_for()
        assert dialog.get_by_role("heading", name="Cancel this bounty?").is_visible()
        dialog.get_by_role("button", name="Cancel bounty").click()
        page.wait_for_load_state("load")
        page.get_by_text("This bounty is cancelled").wait_for()
    assert dialogs == []
    assert not failures, "\n".join(failures)
    assert wallet.balance(poster) == 300
    assert bounty_services.availability()[0] is True
    assert timezone.now()
    assert settings.SESSION_COOKIE_NAME
