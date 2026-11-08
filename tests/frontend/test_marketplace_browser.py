from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pytest
from django.urls import reverse
from playwright.sync_api import Browser

from apps.firmwares.models import (
    AccessTier,
    Brand,
    Chipset,
    Model,
    OfficialFirmware,
    PendingFirmware,
    Variant,
)
from apps.marketplace.models import (
    Listing,
    SellerProfile,
    SellerSocialLink,
    SellerVerification,
)
from apps.users.profile_models import Profile
from tests.frontend.browser_support import (
    chromium,
    login_cookie,
    make_user,
    ready,
    session_context,
    visit_all,
    watch,
)

BROWSER_LOCK = Path(__file__).resolve().parents[2] / ".browser_lock"
LOCK_WAIT_SECONDS = 600
LOCK_RETRY_SECONDS = 10
BIO = "I verify official MediaTek firmware and publish tested flashing notes."


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


def complete_user(username: str):
    user = make_user(username)
    user.full_name = f"{username.title()} Person"
    user.country = "PK"
    user.profile_completed = True
    user.save()
    Profile.objects.update_or_create(
        user=user, defaults={"social_links": {"interests": ["flashing"]}}
    )
    return user


def approved_listing() -> tuple[SellerProfile, Listing]:
    seller_user = complete_user("browser_seller")
    seller = SellerProfile.objects.create(
        user=seller_user,
        display_name="Browser Seller",
        bio=BIO,
        website="https://seller.example.org/",
        verified=True,
    )
    SellerSocialLink.objects.create(
        seller=seller, platform="youtube", url="https://youtube.com/@browser"
    )
    brand = Brand.objects.create(name="Market Browser", slug="market-browser")
    seller.brands.add(brand)
    model = Model.objects.create(brand=brand, name="MB1", slug="mb1")
    variant = Variant.objects.create(model=model, name="Global", slug="global")
    firmware = OfficialFirmware.objects.create(
        original_file_name="market-browser.zip",
        stored_file_path="/firmware/market-browser.zip",
        brand=brand,
        model=model,
        variant=variant,
        uploader=seller_user,
        is_verified=True,
        is_active=True,
    )
    PendingFirmware.objects.create(
        original_file_name="paid-pack.zip",
        stored_file_path="/firmware/paid-pack.zip",
        uploader=seller_user,
        uploaded_brand=brand,
        uploaded_model=model,
        uploaded_variant=variant,
        access_tier=AccessTier.PAID,
        proposed_price=Decimal("8.00"),
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
    with locked_chromium() as browser:
        page = browser.new_page()
        watch(page, failures)
        visit_all(page, live_server, paths, failures)

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_public_seller_profile_shows_safe_links_specialities_and_files(live_server):
    seller, _listing = approved_listing()
    failures: list[str] = []
    with locked_chromium() as browser:
        page = browser.new_page(viewport={"width": 1365, "height": 900})
        watch(page, failures)
        page.goto(f"{live_server.url}{seller.get_absolute_url()}")
        ready(page)

        assert page.get_by_text("Verified by staff").first.is_visible()
        website = page.get_by_role("link", name="https://seller.example.org/")
        assert website.get_attribute("rel") == "nofollow noopener noreferrer"
        assert website.get_attribute("target") == "_blank"
        youtube = page.get_by_role("link", name="https://youtube.com/@browser")
        assert youtube.is_visible()
        assert page.get_by_role("link", name="Market Browser MB1 Global").is_visible()
        assert (
            page.get_by_label("Specialities").get_by_text("Market Browser").is_visible()
        )

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_browse_search_filters_the_grid_in_place_and_states_the_payment_status(
    live_server,
):
    approved_listing()
    failures: list[str] = []
    with locked_chromium() as browser:
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
def test_incomplete_profile_is_sent_to_profile_completion_and_back(live_server):
    user = make_user("browser_incomplete")
    cookie = login_cookie(user)
    failures: list[str] = []
    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        watch(page, failures)
        page.goto(f"{live_server.url}{reverse('marketplace:become_seller')}")

        assert "next=%2Fmarketplace%2Fsell%2F" in page.url
        assert page.get_by_text("Finish your profile").first.is_visible()
        assert page.get_by_text("your full name").first.is_visible()

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_member_applies_with_pickers_links_and_terms_then_sees_the_status(live_server):
    tecno = Brand.objects.create(name="Tecno", slug="tecno")
    Brand.objects.create(name="Infinix", slug="infinix")
    Chipset.objects.create(vendor="MediaTek", name="Helio G99", slug="helio-g99")
    user = complete_user("browser_applicant")
    cookie = login_cookie(user)
    failures: list[str] = []
    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        watch(page, failures)
        page.goto(f"{live_server.url}{reverse('marketplace:become_seller')}")
        ready(page)

        assert page.get_by_role("heading", name="About your work").is_visible()
        page.get_by_label("What you offer and what makes your work unique").fill(BIO)

        brand_search = page.locator("#picker-brands-q")
        brand_search.fill("tec")
        option = page.locator("#picker-brands-body label.mkt-option", has_text="Tecno")
        option.wait_for(timeout=5_000)
        page.locator(
            "#picker-brands-body label.mkt-option", has_text="Infinix"
        ).wait_for(state="detached", timeout=5_000)
        option.locator("input").check()
        brand_search.fill("inf")
        page.locator(
            "#picker-brands-body label.mkt-option", has_text="Infinix"
        ).wait_for(timeout=5_000)
        assert page.locator(f'input[name="brands"][value="{tecno.pk}"]').is_checked()

        chip_search = page.locator("#picker-chipsets-q")
        chip_search.fill("g99")
        chip = page.locator(
            "#picker-chipsets-body label.mkt-option", has_text="Helio G99"
        )
        chip.wait_for(timeout=5_000)
        chip.locator("input").check()

        page.locator("#id_links-0-platform").select_option("youtube")
        page.locator("#id_links-0-url").fill("https://www.youtube.com/@applicant")
        page.get_by_label("Website (optional)").fill("https://applicant.example.org")
        page.get_by_label("Contact me through site staff").check()
        page.get_by_label("I agree to the seller terms").check()
        page.get_by_role("button", name="Send application").click()

        page.wait_for_url("**/marketplace/sell/status/")
        assert page.get_by_text("Waiting for staff review").first.is_visible()
        assert page.get_by_text("Application sent.").is_visible()

    assert not failures, "\n".join(failures)
    seller = SellerProfile.objects.get(user=user)
    assert not seller.verified
    assert list(seller.brands.all()) == [tecno]
    assert seller.chipsets.count() == 1
    assert seller.social_links.get().url == "https://www.youtube.com/@applicant"
    assert (
        SellerVerification.objects.filter(seller=seller, status="pending").count() == 1
    )


@pytest.mark.django_db(transaction=True)
def test_invalid_link_shows_an_accessible_error_and_keeps_the_form(live_server):
    user = complete_user("browser_badlink")
    cookie = login_cookie(user)
    failures: list[str] = []
    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        watch(page, failures)
        page.goto(f"{live_server.url}{reverse('marketplace:become_seller')}")
        ready(page)
        page.get_by_label("What you offer and what makes your work unique").fill(BIO)
        page.locator("#id_links-0-url").fill("javascript:alert(1)")
        page.get_by_label("I agree to the seller terms").check()
        page.get_by_role("button", name="Send application").click()

        error = page.get_by_role("alert").filter(
            has_text="Links must start with https://"
        )
        error.first.wait_for(timeout=5_000)
        assert (
            page.get_by_label(
                "What you offer and what makes your work unique"
            ).input_value()
            == BIO
        )
        assert page.locator("#id_links-0-url").get_attribute("aria-invalid") == "true"

    assert not failures, "\n".join(failures)
    assert not SellerProfile.objects.filter(user=user).exists()


@pytest.mark.django_db(transaction=True)
def test_seller_dashboard_and_application_pages_fit_every_width(live_server):
    seller, _listing = approved_listing()
    applicant = complete_user("browser_pending")
    pending = SellerProfile.objects.create(
        user=applicant, display_name="Pending One", bio=BIO
    )
    SellerVerification.objects.create(seller=pending, evidence=BIO)
    approved_cookie = login_cookie(seller.user)
    pending_cookie = login_cookie(applicant)
    failures: list[str] = []
    with locked_chromium() as browser:
        context = session_context(browser, live_server, approved_cookie)
        page = context.new_page()
        watch(page, failures)
        visit_all(
            page,
            live_server,
            [reverse("marketplace:dashboard"), reverse("marketplace:my_listings")],
            failures,
        )
        page.goto(f"{live_server.url}{reverse('marketplace:dashboard')}")
        ready(page)
        assert page.get_by_role("heading", name="My submissions").is_visible()
        assert page.get_by_text("Proposed: 8.00 USD").is_visible()
        assert page.get_by_role("link", name="Upload firmware").first.is_visible()
        assert page.get_by_text("Credit balance").is_visible()
        assert page.get_by_role("heading", name="Recent sales").is_visible()

        pending_page = session_context(browser, live_server, pending_cookie).new_page()
        watch(pending_page, failures)
        visit_all(
            pending_page,
            live_server,
            [
                reverse("marketplace:application_status"),
                reverse("marketplace:become_seller"),
            ],
            failures,
            expect_status=200,
        )

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_anonymous_visitors_are_sent_to_sign_in_before_selling(live_server):
    with locked_chromium() as browser:
        page = browser.new_page()
        page.goto(f"{live_server.url}{reverse('marketplace:become_seller')}")
        assert "/login" in page.url


@pytest.mark.django_db(transaction=True)
def test_payout_dialog_opens_traps_focus_closes_and_reports_server_errors(live_server):
    seller, _listing = approved_listing()
    cookie = login_cookie(seller.user)
    failures: list[str] = []
    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        watch(page, failures)
        page.goto(f"{live_server.url}{reverse('marketplace:dashboard')}")
        ready(page)

        dialog = page.locator("#payout-dialog")
        assert not dialog.is_visible()
        opener = page.get_by_role("button", name="Request a payout")
        opener.click()
        assert dialog.is_visible()
        assert dialog.get_attribute("aria-labelledby") == "payout-dialog-title"
        page.keyboard.press("Escape")
        assert not dialog.is_visible()

        opener.click()
        dialog.get_by_label("Credits to pay out").fill("50")
        dialog.get_by_label("Where should staff send it?").fill("IBAN 123")
        dialog.get_by_role("button", name="Send request").click()
        page.wait_for_url("**/marketplace/dashboard/payout/")
        assert page.get_by_role("alert").first.is_visible()

    assert not failures, "\n".join(failures)
