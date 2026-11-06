from __future__ import annotations

from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AppRegistry
from apps.marketplace import services
from apps.marketplace import urls as marketplace_urls
from apps.marketplace.models import (
    Listing,
    SellerProfile,
    SellerVerification,
)

PASSWORD = "Pass-12345!"


class MarketplaceTestCase(TestCase):
    def setUp(self):
        user_model = get_user_model()

        def make(name: str, **extra):
            return user_model.objects.create_user(
                username=name,
                email=f"{name}@example.com",
                password=PASSWORD,
                email_verified_at=timezone.now(),
                **extra,
            )

        self.seller_user = make("seller_user")
        self.other_user = make("other_user")
        self.staff = make("market_staff", is_staff=True, is_superuser=True)
        self.seller = SellerProfile.objects.create(
            user=self.seller_user, display_name="Seller One", verified=True
        )
        self.listing = Listing.objects.create(
            seller=self.seller,
            title="Rare tool",
            description="Hand-picked",
            price=Decimal("20.00"),
            status=Listing.Status.APPROVED,
        )


class VisibilityTests(MarketplaceTestCase):
    def test_only_approved_active_listings_of_verified_sellers_are_public(self):
        pending = Listing.objects.create(
            seller=self.seller, title="Pending item", price=1
        )
        hidden = Listing.objects.create(
            seller=self.seller,
            title="Hidden item",
            price=1,
            status=Listing.Status.APPROVED,
            is_active=False,
        )
        unverified = SellerProfile.objects.create(
            user=self.other_user, display_name="Unverified"
        )
        stranger = Listing.objects.create(
            seller=unverified,
            title="Stranger item",
            price=1,
            status=Listing.Status.APPROVED,
        )

        response = self.client.get(reverse("marketplace:listing_browse"))

        self.assertContains(response, "Rare tool")
        for listing in (pending, hidden, stranger):
            with self.subTest(listing=listing.title):
                self.assertNotContains(response, listing.title)
                self.assertEqual(
                    self.client.get(listing.get_absolute_url()).status_code, 404
                )

    def test_owner_can_preview_a_pending_listing_but_others_cannot(self):
        pending = Listing.objects.create(
            seller=self.seller, title="Pending item", price=1
        )
        self.client.force_login(self.other_user)
        self.assertEqual(self.client.get(pending.get_absolute_url()).status_code, 404)

        self.client.force_login(self.seller_user)
        self.assertEqual(self.client.get(pending.get_absolute_url()).status_code, 200)

    def test_search_sort_and_htmx_fragment(self):
        Listing.objects.create(
            seller=self.seller,
            title="Cheap cable",
            price=2,
            status=Listing.Status.APPROVED,
        )
        url = reverse("marketplace:listing_browse")

        response = self.client.get(url, {"q": "cable", "sort": "price"})
        self.assertContains(response, "Cheap cable")
        self.assertNotContains(response, "Rare tool")

        fragment = self.client.get(url, HTTP_HX_REQUEST="true")
        self.assertContains(fragment, "Rare tool")
        self.assertNotContains(fragment, "<!doctype html>")

    def test_view_count_is_counted_once_per_session_and_not_for_the_owner(self):
        url = self.listing.get_absolute_url()
        self.client.get(url)
        self.client.get(url)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.view_count, 1)

        self.client.force_login(self.seller_user)
        self.client.get(url)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.view_count, 1)

    def test_seller_profile_is_public_only_for_verified_sellers(self):
        self.assertEqual(
            self.client.get(self.seller.get_absolute_url()).status_code, 200
        )
        self.seller.verified = False
        self.seller.save()
        self.assertEqual(
            self.client.get(self.seller.get_absolute_url()).status_code, 404
        )

    def test_detail_states_that_checkout_is_not_available(self):
        response = self.client.get(self.listing.get_absolute_url())

        self.assertContains(response, "Online checkout is not available")


class SellerFlowTests(MarketplaceTestCase):
    def test_become_seller_requires_login_verified_email_and_valid_input(self):
        url = reverse("marketplace:become_seller")
        self.assertEqual(self.client.get(url).status_code, 302)

        unverified = get_user_model().objects.create_user(
            username="unverified_seller",
            email="unverified-seller@example.com",
            password=PASSWORD,
        )
        self.client.force_login(unverified)
        self.client.post(url, {"display_name": "Nope"})
        self.assertFalse(SellerProfile.objects.filter(user=unverified).exists())

        newcomer = get_user_model().objects.create_user(
            username="newcomer",
            email="newcomer@example.com",
            password=PASSWORD,
            email_verified_at=timezone.now(),
        )
        self.client.force_login(newcomer)
        too_long = self.client.post(url, {"display_name": "x" * 101, "bio": "b"})
        self.assertEqual(too_long.status_code, 200)
        self.assertFalse(SellerProfile.objects.filter(user=newcomer).exists())

        response = self.client.post(url, {"display_name": "Newcomer", "bio": "Hi"})
        self.assertRedirects(response, reverse("marketplace:my_listings"))
        profile = SellerProfile.objects.get(user=newcomer)
        self.assertFalse(profile.verified)

    def test_new_listings_start_pending_and_validate_discount(self):
        self.client.force_login(self.seller_user)
        url = reverse("marketplace:my_listings")

        bad = self.client.post(
            url, {"title": "Bad", "price": "5.00", "discount_price": "9.00"}
        )
        self.assertEqual(bad.status_code, 200)
        self.assertFalse(Listing.objects.filter(title="Bad").exists())

        self.client.post(url, {"title": "Good", "price": "5.00"})
        good = Listing.objects.get(title="Good")
        self.assertEqual(good.status, Listing.Status.PENDING)
        self.assertEqual(good.seller, self.seller)

    def test_user_without_a_profile_is_sent_to_onboarding(self):
        self.client.force_login(self.other_user)

        response = self.client.get(reverse("marketplace:my_listings"))

        self.assertRedirects(response, reverse("marketplace:become_seller"))

    def test_only_the_owner_can_toggle_a_listing(self):
        url = reverse("marketplace:toggle_listing", kwargs={"pk": self.listing.pk})
        self.client.force_login(self.other_user)
        self.assertEqual(self.client.post(url).status_code, 404)
        SellerProfile.objects.create(user=self.other_user, display_name="Other")
        self.assertEqual(self.client.post(url).status_code, 404)
        self.listing.refresh_from_db()
        self.assertTrue(self.listing.is_active)

        self.client.force_login(self.seller_user)
        self.client.post(url)
        self.listing.refresh_from_db()
        self.assertFalse(self.listing.is_active)

    def test_state_changing_routes_enforce_csrf_and_post(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.seller_user)
        for name, kwargs in (
            ("marketplace:toggle_listing", {"pk": self.listing.pk}),
            ("marketplace:request_verification", {}),
        ):
            with self.subTest(route=name):
                url = reverse(name, kwargs=kwargs)
                self.assertEqual(csrf_client.post(url).status_code, 403)
                self.assertEqual(self.client.get(url).status_code, 405)

    def test_no_api_routes_are_exposed(self):
        names = {route.name for route in marketplace_urls.urlpatterns}

        self.assertFalse({name for name in names if name and "api" in name})


class VerificationAndModerationTests(MarketplaceTestCase):
    def setUp(self):
        super().setUp()
        self.pending_seller = SellerProfile.objects.create(
            user=self.other_user, display_name="Applicant"
        )

    def test_verification_request_rules(self):
        verification = services.request_verification(self.pending_seller, "My site")
        self.assertEqual(verification.status, SellerVerification.Status.PENDING)
        with self.assertRaises(services.MarketplaceError):
            services.request_verification(self.pending_seller, "again")
        with self.assertRaises(services.MarketplaceError):
            services.request_verification(self.seller, "already verified")
        with self.assertRaises(IntegrityError), transaction.atomic():
            SellerVerification.objects.create(
                seller=self.pending_seller, evidence="duplicate pending"
            )

    def test_approval_verifies_the_seller_once(self):
        verification = services.request_verification(self.pending_seller, "My site")

        services.review_verification(
            verification.pk, reviewer=self.staff, approve=True, reason="ok"
        )

        self.pending_seller.refresh_from_db()
        verification.refresh_from_db()
        self.assertTrue(self.pending_seller.verified)
        self.assertEqual(verification.reviewer, self.staff)
        self.assertIsNotNone(verification.reviewed_at)
        with self.assertRaises(services.MarketplaceError):
            services.review_verification(
                verification.pk, reviewer=self.staff, approve=False
            )

    def test_rejection_keeps_the_seller_unverified(self):
        verification = services.request_verification(self.pending_seller, "x")

        services.review_verification(
            verification.pk, reviewer=self.staff, approve=False, reason="no proof"
        )

        self.pending_seller.refresh_from_db()
        self.assertFalse(self.pending_seller.verified)

    def test_listing_review_stamps_the_reviewer_and_cannot_repeat(self):
        listing = Listing.objects.create(seller=self.seller, title="Review me", price=3)

        services.review_listing(listing.pk, reviewer=self.staff, approve=True)

        listing.refresh_from_db()
        self.assertEqual(listing.status, Listing.Status.APPROVED)
        self.assertEqual(listing.reviewed_by, self.staff)
        with self.assertRaises(services.MarketplaceError):
            services.review_listing(listing.pk, reviewer=self.staff, approve=False)

    def test_discount_cannot_exceed_price_in_the_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Listing.objects.create(
                seller=self.seller, title="Bad", price=5, discount_price=9
            )

    def test_admin_actions_review_listings(self):
        listing = Listing.objects.create(
            seller=self.seller, title="Admin review", price=3
        )
        self.client.force_login(self.staff)

        self.client.post(
            reverse("admin:marketplace_listing_changelist"),
            {"action": "approve", "_selected_action": [listing.pk]},
        )

        listing.refresh_from_db()
        self.assertEqual(listing.status, Listing.Status.APPROVED)

    def test_every_model_is_registered_in_the_admin(self):
        for model in (SellerProfile, SellerVerification, Listing):
            with self.subTest(model=model.__name__):
                self.assertIn(model, admin.site._registry)


class FeatureFlagTests(MarketplaceTestCase):
    def test_disabling_the_app_registry_flag_hides_the_marketplace(self):
        registry = AppRegistry.get_solo()
        registry.marketplace_enabled = False
        registry.save()

        self.assertEqual(
            self.client.get(reverse("marketplace:listing_browse")).status_code, 404
        )
