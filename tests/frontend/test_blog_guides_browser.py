"""Browser checks for featured articles and guide requests (one Chromium at a time)."""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from django.contrib.auth.models import Permission
from playwright.sync_api import Browser, expect

from apps.blog.models import GuideRequest, Post, PostAccess, PostStatus
from apps.bounty.models import Bounty
from apps.consent import services as consent_services
from apps.core.app_service import AppService
from apps.firmwares.models import Brand, Model
from apps.wallet import api as wallet
from tests.frontend.browser_support import (
    chromium,
    dismiss_dialogs,
    login_cookie,
    make_user,
    overflow,
    ready,
    session_context,
    watch,
)
from tests.frontend.editor_support import fill_rich_text

BROWSER_LOCK = Path(__file__).resolve().parents[2] / ".browser_lock"
LOCK_WAIT_SECONDS = 600
LOCK_RETRY_SECONDS = 10
HIDDEN = "SECRETTAIL"
BODY = (
    "<p>"
    + " ".join(f"word{index}" for index in range(300))
    + f" {HIDDEN}</p><h2>Hidden heading</h2><p>More hidden text.</p>"
)


def file_request(page, base_url: str, *, model_code: str, title: str, tried: str):
    """Search first, then continue to the form and send the request."""
    page.goto(f"{base_url}/blog/requests/new/")
    ready(page)
    page.get_by_label("Model code").fill(model_code)
    continue_link = page.get_by_test_id("guide-continue")
    expect(continue_link).to_be_visible()
    continue_link.click()
    page.wait_for_url("**/blog/requests/new/form/**")
    page.get_by_label("What do you need?").fill(title)
    page.get_by_label("What have you tried?").fill(tried)
    page.get_by_role("button", name="Send for review").click()
    page.wait_for_url("**/blog/requests/*/")


@contextmanager
def locked_chromium() -> Iterator[Browser]:
    """One Chromium at a time on this machine: hold the shared lock directory."""
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            BROWSER_LOCK.mkdir()
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                pytest.fail("The browser lock stayed busy for 10 minutes.")
            time.sleep(LOCK_RETRY_SECONDS)
    try:
        with chromium() as browser:
            yield browser
    finally:
        BROWSER_LOCK.rmdir()


def decided_cookies(cookies: dict[str, str]) -> dict[str, str]:
    """Add a consent decision so the banner does not cover the page under test."""
    policy = consent_services.active_policy()
    value = consent_services.encode_cookie(policy.version, [], uuid.uuid4())
    return {**cookies, consent_services.cookie_name(): value}


class FakeShop:
    """The shop's featured-blog contract (``apps/shop/api.py``) for the browser run."""

    def __init__(self) -> None:
        self.allowed: set[int] = set()
        self.purchases: list[str] = []
        self.checks: list[tuple[int, object]] = []
        self.charges: list[tuple[int, object]] = []

    def featured_blog_access(self, user, post_id, price_usd):
        self.checks.append((user.pk, price_usd))
        return SimpleNamespace(
            allowed=post_id in self.allowed,
            via="purchase" if post_id in self.allowed else "",
            credit_price=120,
            reason="Buy it with credits or get a package.",
        )

    def purchase_featured_blog(self, user, post_id, price_usd, key):
        self.purchases.append(key)
        self.charges.append((user.pk, price_usd))
        self.allowed.add(post_id)
        return SimpleNamespace(
            allowed=True, via="purchase", credit_price=120, reason=""
        )


@pytest.mark.django_db(transaction=True)
def test_teaser_buy_dialog_then_full_article(live_server):
    author = make_user("browser-premium-author")
    reader = make_user("browser-premium-reader")
    post = Post.objects.create(
        title="Premium unlock walkthrough",
        body=BODY,
        author=author,
        status=PostStatus.PUBLISHED,
        access=PostAccess.FEATURED,
        price=Decimal("4.00"),
    )
    shop = FakeShop()
    real_get = AppService.get
    cookies = decided_cookies(login_cookie(reader))
    failures: list[str] = []
    native: list[str] = []

    with (
        mock.patch(
            "apps.blog.shop_gateway.AppService.get",
            side_effect=lambda label: shop if label == "shop" else real_get(label),
        ),
        locked_chromium() as browser,
    ):
        context = session_context(browser, live_server, cookies)
        page = context.new_page()
        watch(page, failures)
        dismiss_dialogs(page, native)
        page.goto(f"{live_server.url}{post.get_absolute_url()}")
        ready(page)

        expect(page.locator(".article-body")).to_contain_text("word0 word1")
        expect(page.locator(".article-paywall")).to_be_visible()
        assert HIDDEN not in page.content()
        assert "Hidden heading" not in page.content()
        dims = overflow(page)
        assert dims["document"] <= dims["viewport"] + 1

        page.locator("#buy-article").click()
        dialog = page.locator("dialog[open]")
        dialog.wait_for()
        expect(
            dialog.get_by_role("heading", name="Unlock this article")
        ).to_be_visible()
        expect(dialog.get_by_test_id("blog-buy-price")).to_have_text("120 credits")
        dialog.get_by_role("button", name="Pay with credits").click()

        page.wait_for_load_state("load")
        expect(page.locator(".article-body")).to_contain_text(HIDDEN)
        expect(page.locator(".article-paywall")).to_have_count(0)
        expect(page.locator("h2", has_text="Hidden heading")).to_be_visible()
        page.set_viewport_size({"width": 390, "height": 844})
        dims = overflow(page)
        assert dims["document"] <= dims["viewport"] + 1
        context.close()

    assert native == []
    assert shop.purchases == [f"blog-featured:{reader.pk}:{post.pk}"]
    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_request_staff_approval_bounty_solution_and_publication(live_server):
    asker = make_user("browser-asker")
    funder = make_user("browser-funder")
    hunter = make_user("browser-hunter")
    staff = make_user("browser-reviewer", staff=True)
    staff.is_superuser = True
    staff.save()
    staff.user_permissions.add(*Permission.objects.filter(codename="change_post"))
    wallet.credit(
        funder, 300, reason=wallet.LedgerReason.REWARD, key="seed-guide-funder"
    )
    cookies = {
        name: decided_cookies(login_cookie(user))
        for name, user in (
            ("asker", asker),
            ("staff", staff),
            ("funder", funder),
            ("hunter", hunter),
        )
    }
    failures: list[str] = []
    native: list[str] = []

    with locked_chromium() as browser:
        # 1. A verified member asks for a method; it is private until staff decide.
        asker_page = session_context(browser, live_server, cookies["asker"]).new_page()
        watch(asker_page, failures)
        dismiss_dialogs(asker_page, native)
        file_request(
            asker_page,
            live_server.url,
            model_code="AP1",
            title="Bypass FRP on Acme Phone One without a PC",
            tried="Tried SP Flash Tool with the stock ROM, error 0xC0020036 at 12%.",
        )
        expect(asker_page.get_by_test_id("request-status")).to_have_text(
            "Pending review"
        )
        request_url = asker_page.url
        assert asker_page.get_by_text("Add a bounty").count() == 0

        # 2. Staff approve it in the review queue (Unfold dialog action).
        pk = int(request_url.rstrip("/").rsplit("/", 1)[1])
        staff_page = session_context(browser, live_server, cookies["staff"]).new_page()
        watch(staff_page, failures)
        dismiss_dialogs(staff_page, native)
        staff_page.goto(f"{live_server.url}/admin/blog/guiderequest/{pk}/change/")
        staff_page.locator("a[hx-get*='approve']").first.click()
        expect(staff_page.get_by_text("Approve this request?")).to_be_visible()
        staff_page.locator("#modal-content button[type='submit']").click()
        staff_page.wait_for_url(f"**/admin/blog/guiderequest/{pk}/change/")
        expect(staff_page.get_by_text("Request approved.")).to_be_visible()

        # 3. Another member attaches a credit bounty through the escrow.
        funder_page = session_context(
            browser, live_server, cookies["funder"]
        ).new_page()
        watch(funder_page, failures)
        dismiss_dialogs(funder_page, native)
        funder_page.goto(request_url)
        ready(funder_page)
        funder_page.get_by_role("link", name="Add a bounty").click()
        funder_page.get_by_label("Reward (credits)").fill("120")
        funder_page.get_by_role("button", name="Escrow credits and post").click()
        funder_page.wait_for_url("**/bounty/*/")
        expect(funder_page.get_by_text("120 credits").first).to_be_visible()

        # 4. A hunter submits a solution and must state the access.
        hunter_page = session_context(
            browser, live_server, cookies["hunter"]
        ).new_page()
        watch(hunter_page, failures)
        dismiss_dialogs(hunter_page, native)
        hunter_page.goto(request_url)
        ready(hunter_page)
        expect(hunter_page.get_by_text("120 credits are offered")).to_be_visible()
        hunter_page.get_by_test_id("submit-solution").click()
        hunter_page.wait_for_load_state("load")
        assert "/blog/create/" in hunter_page.url, (
            hunter_page.url,
            hunter_page.locator("h1").first.inner_text(),
        )
        hunter_page.locator("#id_title").fill("FRP bypass for Acme Phone One")
        fill_rich_text(
            hunter_page,
            "#id_body",
            "<p>" + "Follow these tested steps carefully. " * 12 + "</p>",
            html=True,
        )
        hunter_page.get_by_role("button", name="Save draft").click()
        hunter_page.wait_for_load_state("load")
        expect(hunter_page.get_by_text("This field is required.").first).to_be_visible()
        hunter_page.locator("#id_access").select_option("free")
        hunter_page.get_by_role("button", name="Save draft").click()
        hunter_page.wait_for_url(request_url)
        expect(hunter_page.get_by_test_id("solution-access")).to_have_text("Free")

        # 5. Staff approve the submission: it publishes and the bounty is paid.
        staff_page.goto(f"{live_server.url}/blog/manage/")
        ready(staff_page)
        row = staff_page.locator("tr", has_text="FRP bypass for Acme Phone One")
        row.get_by_role("link", name="Publish").click()
        dialog = staff_page.locator("dialog[open]")
        dialog.wait_for()
        dialog.get_by_role("button", name="Publish").click()
        staff_page.wait_for_load_state("load")
        expect(
            staff_page.get_by_text("FRP bypass for Acme Phone One: Published.")
        ).to_be_visible()

        asker_page.goto(request_url)
        expect(asker_page.get_by_test_id("request-status")).to_have_text("Fulfilled")
        for page in (asker_page, staff_page, funder_page, hunter_page):
            page.context.close()

    assert native == []
    assert not failures, "\n".join(failures)
    solution = Post.objects.get(guide_request__pk=pk)
    assert GuideRequest.objects.get().status == GuideRequest.Status.FULFILLED
    assert solution.status == PostStatus.PUBLISHED
    assert Bounty.objects.get().status == Bounty.Status.FULFILLED
    assert wallet.balance(hunter) >= 120
    assert wallet.balance(funder) == 300 - 120


@pytest.mark.django_db(transaction=True)
def test_search_related_articles_article_button_and_hunter_context(live_server):
    brand = Brand.objects.create(name="Infinix", slug="infinix")
    phone = Model.objects.create(
        brand=brand, name="Hot 40", slug="hot-40", model_code="X6836"
    )
    writer = make_user("browser-guide-writer")
    article = Post.objects.create(
        title="Hot 40 FRP bypass method",
        body="<p>" + "Steps of the existing method. " * 12 + f" {HIDDEN}</p>",
        author=writer,
        status=PostStatus.PUBLISHED,
        access=PostAccess.PAID,
        price=Decimal("3.00"),
        firmware_model=phone,
        firmware_brand=brand,
    )
    asker = make_user("browser-ctx-asker")
    hunter = make_user("browser-ctx-hunter")
    staff = make_user("browser-ctx-staff", staff=True)
    staff.is_superuser = True
    staff.save()
    cookies = {
        name: decided_cookies(login_cookie(user))
        for name, user in (("asker", asker), ("staff", staff), ("hunter", hunter))
    }
    tried = "Flashed the stock ROM with SP Flash Tool v5.2316, error 0xC0020036."
    failures: list[str] = []
    native: list[str] = []

    with locked_chromium() as browser:
        # 1. Search first: the related article is listed, its body never is.
        asker_page = session_context(browser, live_server, cookies["asker"]).new_page()
        watch(asker_page, failures)
        dismiss_dialogs(asker_page, native)
        asker_page.goto(f"{live_server.url}/blog/requests/new/")
        ready(asker_page)
        assert asker_page.get_by_test_id("guide-continue").count() == 0
        asker_page.get_by_label("Model code").fill("X6836")
        results = asker_page.get_by_test_id("guide-result")
        expect(asker_page.get_by_test_id("guide-continue")).to_be_visible()
        # The catalogue automation also published posts for this model: all listed.
        assert results.count() >= 1
        mine = results.filter(has_text="Hot 40 FRP bypass method")
        expect(mine).to_have_count(1)
        expect(mine.get_by_test_id("guide-result-access")).to_have_text("Paid")
        assert HIDDEN not in asker_page.content()
        dims = overflow(asker_page)
        assert dims["document"] <= dims["viewport"] + 1

        # 2. "This solved it" opens the article; its button starts the request.
        mine.get_by_test_id("guide-solved").click()
        asker_page.wait_for_url(f"**{article.get_absolute_url()}")
        asker_page.get_by_test_id("request-new-method").click()
        asker_page.wait_for_url("**/blog/requests/new/form/**")
        expect(asker_page.get_by_test_id("guide-related-article")).to_contain_text(
            "Hot 40 FRP bypass method"
        )
        expect(asker_page.get_by_label("What have you tried?")).to_be_focused()
        asker_page.get_by_label("What do you need?").fill(
            "Another way to bypass FRP on Hot 40"
        )
        asker_page.get_by_role("button", name="Send for review").click()
        expect(asker_page.get_by_text("This field is required.").first).to_be_visible()
        asker_page.get_by_label("What have you tried?").fill(tried)
        asker_page.get_by_role("button", name="Send for review").click()
        asker_page.wait_for_url("**/blog/requests/*/")
        request_url = asker_page.url
        pk = int(request_url.rstrip("/").rsplit("/", 1)[1])

        # 3. Staff see the article and the tried text, then approve.
        staff_page = session_context(browser, live_server, cookies["staff"]).new_page()
        watch(staff_page, failures)
        dismiss_dialogs(staff_page, native)
        staff_page.goto(f"{live_server.url}/admin/blog/guiderequest/{pk}/change/")
        expect(staff_page.get_by_text(tried)).to_be_visible()
        expect(staff_page.get_by_text("Hot 40 FRP bypass method").first).to_be_visible()
        staff_page.locator("a[hx-get*='approve']").first.click()
        expect(staff_page.get_by_text("Approve this request?")).to_be_visible()
        staff_page.locator("#modal-content button[type='submit']").click()
        staff_page.wait_for_url(f"**/admin/blog/guiderequest/{pk}/change/")
        expect(staff_page.get_by_text("Request approved.")).to_be_visible()

        # 4. A bounty hunter sees what was tried and the article that failed.
        hunter_page = session_context(
            browser, live_server, cookies["hunter"]
        ).new_page()
        watch(hunter_page, failures)
        dismiss_dialogs(hunter_page, native)
        hunter_page.goto(request_url)
        ready(hunter_page)
        expect(hunter_page.get_by_test_id("request-tried")).to_contain_text(tried)
        expect(hunter_page.get_by_test_id("request-related-post")).to_contain_text(
            "Hot 40 FRP bypass method"
        )
        expect(hunter_page.get_by_test_id("submit-solution")).to_be_visible()
        hunter_page.get_by_test_id("follow-request").click()
        expect(hunter_page.get_by_test_id("follower-count")).to_contain_text("1 member")
        hunter_page.set_viewport_size({"width": 390, "height": 844})
        dims = overflow(hunter_page)
        assert dims["document"] <= dims["viewport"] + 1
        for page in (asker_page, staff_page, hunter_page):
            page.context.close()

    assert native == []
    assert not failures, "\n".join(failures)
    saved = GuideRequest.objects.get(pk=pk)
    assert saved.related_post == article
    assert saved.tried == tried
    assert saved.status == GuideRequest.Status.APPROVED
    assert saved.followers.count() == 1
