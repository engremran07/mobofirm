from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.firmwares.models import Brand, Model, OfficialFirmware
from apps.marketplace.models import Listing, SellerProfile, SellerVerification
from tests.frontend.browser_support import (
    chromium,
    login_cookie,
    make_user,
    ready,
    session_context,
    visit_all,
    watch,
)


def approved_listing() -> tuple[SellerProfile, Listing]:
    seller_user = make_user("browser_seller")
    seller = SellerProfile.objects.create(
        user=seller_user, display_name="Browser Seller", verified=True
    )
    brand = Brand.objects.create(name="Market Browser", slug="market-browser")
    model = Model.objects.create(brand=brand, name="MB1", slug="mb1")
    firmware = OfficialFirmware.objects.create(
        original_file_name="market-browser.zip",
        stored_file_path="/firmware/market-browser.zip",
        brand=brand,
        model=model,
        is_verified=True,
        is_active=True,
    )
    listing = Listing.objects.create(
        seller=seller,
        title="Boxed programmer",
        description="Includes cables",
        price=Decimal("30.00"),
        discount_price=Decimal("25.00"),
        firmware=firmware,
        status=Listing.Status.APPROVED,
    )
    return seller, listing


@pytest.mark.django_db(transaction=True)
def test_public_marketplace_pages_have_no_console_csp_or_overflow_problems(live_server):
    seller, listing = approved_listing()
    paths = [
        reverse("marketplace:listing_browse"),
        reverse("marketplace:listing_detail", kwargs={"pk": listing.pk}),
        reverse("marketplace:seller_profile", kwargs={"pk": seller.pk}),
    ]
    failures: list[str] = []
    with chromium() as browser:
        page = browser.new_page()
        watch(page, failures)
        visit_all(page, live_server, paths, failures)

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_browse_search_filters_the_grid_in_place_and_states_the_payment_status(
    live_server,
):
    approved_listing()
    failures: list[str] = []
    with chromium() as browser:
        page = browser.new_page(viewport={"width": 1365, "height": 900})
        watch(page, failures)
        page.goto(f"{live_server.url}{reverse('marketplace:listing_browse')}")
        ready(page)

        assert page.get_by_text("Online checkout is not available").first.is_visible()
        assert page.get_by_role("link", name="Boxed programmer").first.is_visible()
        search = page.locator("#listing-search")
        search.fill("nothing-matches-this")
        page.get_by_text("Boxed programmer").first.wait_for(
            state="detached", timeout=5_000
        )
        assert page.locator("main").count() == 1

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_member_becomes_a_seller_submits_a_listing_and_asks_for_verification(
    live_server,
):
    user = make_user("browser_new_seller")
    cookie = login_cookie(user)
    failures: list[str] = []
    with chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        watch(page, failures)
        page.goto(f"{live_server.url}{reverse('marketplace:become_seller')}")
        ready(page)
        page.get_by_label("Display name", exact=True).fill("Fresh Seller")
        page.get_by_label("Bio", exact=True).fill("Repairs and programmers.")
        page.get_by_role("button", name="Create seller profile").click()
        page.wait_for_url("**/my-listings/")
        assert page.get_by_text("Seller profile created.").is_visible()
        assert page.get_by_text("Not verified").first.is_visible()

        page.get_by_label("Title", exact=True).fill("USB flasher box")
        page.get_by_label("Description", exact=True).fill("Tested, with cables.")
        page.get_by_label("Price", exact=True).fill("12.50")
        page.get_by_role("button", name="Submit for review").click()
        page.wait_for_url("**/my-listings/")
        assert page.get_by_text("Listing submitted for review.").is_visible()
        row = page.get_by_role("row").filter(has_text="USB flasher box")
        assert row.is_visible()

        page.get_by_label("Evidence").fill("https://example.com/me")
        page.get_by_role("button", name="Request verification").click()
        page.wait_for_url("**/my-listings/")
        assert page.get_by_text("Waiting for staff review").first.is_visible()

    assert not failures, "\n".join(failures)
    listing = Listing.objects.get(title="USB flasher box")
    assert listing.status == Listing.Status.PENDING
    assert not listing.is_public
    assert SellerVerification.objects.filter(seller__user=user).count() == 1


@pytest.mark.django_db(transaction=True)
def test_anonymous_visitors_are_sent_to_sign_in_before_selling(live_server):
    with chromium() as browser:
        page = browser.new_page()
        page.goto(f"{live_server.url}{reverse('marketplace:become_seller')}")
        assert "/login" in page.url
