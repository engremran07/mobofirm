from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.firmwares.models import Brand, Model, OfficialFirmware
from apps.marketplace.models import Listing, SellerProfile, SellerVerification

PASSWORD = "Pass-12345!"


class MarketplaceUiTestCase(TestCase):
    def setUp(self):
        user_model = get_user_model()

        def make(name: str):
            return user_model.objects.create_user(
                username=name,
                email=f"{name}@example.com",
                password=PASSWORD,
                email_verified_at=timezone.now(),
            )

        self.owner = make("ui_owner")
        self.visitor = make("ui_visitor")
        self.seller = SellerProfile.objects.create(
            user=self.owner, display_name="Ui Seller", verified=True
        )
        brand = Brand.objects.create(name="Market Brand", slug="market-brand")
        model = Model.objects.create(brand=brand, name="M1", slug="m1")
        self.firmware = OfficialFirmware.objects.create(
            original_file_name="market.zip",
            stored_file_path="/firmware/market.zip",
            brand=brand,
            model=model,
            is_verified=True,
            is_active=True,
        )
        self.listing = Listing.objects.create(
            seller=self.seller,
            title="Boxed programmer",
            description="Includes cables",
            price=Decimal("30.00"),
            discount_price=Decimal("25.00"),
            firmware=self.firmware,
            is_featured=True,
            status=Listing.Status.APPROVED,
        )

    def login(self, user) -> Client:
        client = Client()
        client.force_login(user)
        return client


class BrowseAndDetailTests(MarketplaceUiTestCase):
    def test_browse_shows_cards_pills_notice_and_a_working_search_form(self):
        response = self.client.get(reverse("marketplace:listing_browse"))

        self.assertContains(response, "Boxed programmer")
        self.assertContains(response, "Verified seller")
        self.assertContains(response, "Featured")
        self.assertContains(response, "Online checkout is not available")
        self.assertContains(response, 'role="search"')
        self.assertContains(response, "<s ")
        self.assertContains(response, "25.00")

    def test_htmx_search_returns_only_the_grid_fragment(self):
        response = self.client.get(
            reverse("marketplace:listing_browse"),
            {"q": "programmer"},
            headers={"HX-Request": "true"},
        )

        self.assertContains(response, "Boxed programmer")
        self.assertNotContains(response, "<html")

    def test_empty_search_shows_an_empty_state(self):
        response = self.client.get(
            reverse("marketplace:listing_browse"), {"q": "nothing"}
        )

        self.assertContains(response, "No listings match your search")

    def test_detail_links_a_public_firmware_record(self):
        response = self.client.get(self.listing.get_absolute_url())

        self.assertContains(response, "Related firmware record")
        self.assertContains(response, "Market Brand M1")
        self.assertContains(response, "Verified by staff")
        self.assertContains(response, "Online checkout is not available")

    def test_detail_does_not_link_firmware_that_is_not_public(self):
        OfficialFirmware.objects.filter(pk=self.firmware.pk).update(is_verified=False)

        response = self.client.get(self.listing.get_absolute_url())

        self.assertNotContains(response, "Related firmware record")

    def test_owner_sees_why_a_pending_listing_is_not_public(self):
        pending = Listing.objects.create(
            seller=self.seller, title="Pending thing", price=5
        )

        owner = self.login(self.owner).get(pending.get_absolute_url())
        stranger = self.login(self.visitor).get(pending.get_absolute_url())

        self.assertContains(owner, "waiting for staff review")
        self.assertEqual(stranger.status_code, 404)

    def test_owner_sees_the_staff_reason_for_a_rejected_listing(self):
        rejected = Listing.objects.create(
            seller=self.seller,
            title="Rejected thing",
            price=5,
            status=Listing.Status.REJECTED,
            review_reason="Photos missing",
        )

        response = self.login(self.owner).get(rejected.get_absolute_url())

        self.assertContains(response, "Photos missing")

    def test_seller_profile_page_lists_listings_and_the_verified_badge(self):
        response = self.client.get(self.seller.get_absolute_url())

        self.assertContains(response, "Boxed programmer")
        self.assertContains(response, "Verified by staff")

    def test_listing_views_are_counted_once_per_session(self):
        client = Client()
        for _ in range(2):
            client.get(self.listing.get_absolute_url())

        self.listing.refresh_from_db()
        self.assertEqual(self.listing.view_count, 1)

    def test_every_marketplace_page_has_breadcrumbs_and_one_h1(self):
        urls = [
            reverse("marketplace:listing_browse"),
            self.listing.get_absolute_url(),
            self.seller.get_absolute_url(),
        ]
        client = self.login(self.owner)
        urls += [
            reverse("marketplace:dashboard"),
            reverse("marketplace:my_listings"),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = client.get(url)
                self.assertContains(response, 'aria-label="Breadcrumb"')
                self.assertContains(response, 'aria-current="page"')
                self.assertEqual(response.content.decode().count("<h1"), 1)


class ListingDashboardTests(MarketplaceUiTestCase):
    def test_listings_page_shows_stats_and_review_state_per_listing(self):
        Listing.objects.create(
            seller=self.seller,
            title="Rejected one",
            price=5,
            status=Listing.Status.REJECTED,
            review_reason="Too vague",
        )
        Listing.objects.create(seller=self.seller, title="Waiting one", price=5)

        response = self.login(self.owner).get(reverse("marketplace:my_listings"))

        self.assertContains(response, "Awaiting review")
        self.assertContains(response, "Too vague")
        self.assertContains(response, "Rejected")
        self.assertContains(response, "Not public yet")
        self.assertContains(response, "Public")
        self.assertNotContains(response, "Request verification")

    def test_forms_on_the_listings_page_are_csrf_protected(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)

        response = client.post(
            reverse("marketplace:my_listings"), {"title": "X", "price": "1"}
        )

        self.assertEqual(response.status_code, 403)

    def test_account_dashboard_tile_reflects_seller_state(self):
        approved = self.login(self.owner).get(reverse("users:dashboard"))
        newcomer = self.login(self.visitor).get(reverse("users:dashboard"))

        self.assertContains(approved, reverse("marketplace:dashboard"))
        self.assertContains(newcomer, reverse("marketplace:become_seller"))
        self.assertContains(newcomer, "Become a seller")

    def test_pending_seller_tile_points_to_the_application_status(self):
        SellerProfile.objects.filter(pk=self.seller.pk).update(verified=False)
        SellerVerification.objects.create(seller=self.seller, evidence="My shop link")

        response = self.login(self.owner).get(reverse("users:dashboard"))

        self.assertContains(response, reverse("marketplace:application_status"))
