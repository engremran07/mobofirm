from __future__ import annotations

from decimal import Decimal

from django.apps import apps as django_apps
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from apps.marketplace.models import Listing, SellerProfile, SellerVerification

DIALOG = {"_form_submitted": "on"}


class MarketplaceAdminActionTests(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        self.staff = user_model.objects.create_superuser(
            username="market-admin", email="market-admin@example.test"
        )
        seller_user = user_model.objects.create_user(
            username="market-seller", email="market-seller@example.test"
        )
        self.seller = SellerProfile.objects.create(
            user=seller_user, display_name="Admin Seller"
        )
        self.verification = SellerVerification.objects.create(
            seller=self.seller, evidence="https://example.test/shop"
        )
        self.listing = Listing.objects.create(
            seller=self.seller, title="Pending item", price=Decimal("5.00")
        )
        self.client = Client()
        self.client.force_login(self.staff)

    def verification_url(self, name: str) -> str:
        return reverse(
            f"admin:marketplace_sellerverification_{name}", args=[self.verification.pk]
        )

    def listing_url(self, name: str) -> str:
        return reverse(f"admin:marketplace_listing_{name}", args=[self.listing.pk])

    def test_approving_a_verification_verifies_the_seller(self) -> None:
        opened = self.client.get(self.verification_url("approve_verification"))
        done = self.client.post(
            self.verification_url("approve_verification"),
            {**DIALOG, "reason": "Checked the shop page"},
        )

        self.assertEqual(opened.status_code, 200)
        self.assertIn("HX-Redirect", done.headers)
        self.verification.refresh_from_db()
        self.seller.refresh_from_db()
        self.assertEqual(self.verification.status, SellerVerification.Status.APPROVED)
        self.assertEqual(self.verification.reviewer, self.staff)
        self.assertEqual(self.verification.review_reason, "Checked the shop page")
        self.assertTrue(self.seller.verified)

    def test_rejecting_a_verification_requires_a_reason(self) -> None:
        missing = self.client.post(self.verification_url("reject_verification"), DIALOG)
        self.assertNotIn("HX-Redirect", missing.headers)
        self.verification.refresh_from_db()
        self.assertEqual(self.verification.status, SellerVerification.Status.PENDING)

        self.client.post(
            self.verification_url("reject_verification"),
            {**DIALOG, "reason": "Evidence is not public"},
        )

        self.verification.refresh_from_db()
        self.seller.refresh_from_db()
        self.assertEqual(self.verification.status, SellerVerification.Status.REJECTED)
        self.assertFalse(self.seller.verified)

    def test_a_reviewed_verification_cannot_be_reviewed_again(self) -> None:
        self.client.post(self.verification_url("approve_verification"), DIALOG)

        self.client.post(
            self.verification_url("reject_verification"), {**DIALOG, "reason": "late"}
        )

        self.verification.refresh_from_db()
        self.assertEqual(self.verification.status, SellerVerification.Status.APPROVED)

    def test_listing_dialogs_record_the_reviewer_and_reason(self) -> None:
        self.client.post(
            self.listing_url("approve_listing"), {**DIALOG, "reason": "Fine"}
        )

        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.APPROVED)
        self.assertEqual(self.listing.reviewed_by, self.staff)
        self.assertEqual(self.listing.review_reason, "Fine")

    def test_rejecting_a_listing_requires_a_reason(self) -> None:
        missing = self.client.post(self.listing_url("reject_listing"), DIALOG)
        self.assertNotIn("HX-Redirect", missing.headers)

        self.client.post(
            self.listing_url("reject_listing"), {**DIALOG, "reason": "Off topic"}
        )

        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.REJECTED)
        self.assertEqual(self.listing.review_reason, "Off topic")

    def test_bulk_actions_review_every_selected_row(self) -> None:
        self.client.post(
            reverse("admin:marketplace_sellerverification_changelist"),
            {"action": "approve", "_selected_action": [self.verification.pk]},
        )
        self.client.post(
            reverse("admin:marketplace_listing_changelist"),
            {"action": "reject", "_selected_action": [self.listing.pk]},
        )

        self.seller.refresh_from_db()
        self.listing.refresh_from_db()
        self.assertTrue(self.seller.verified)
        self.assertEqual(self.listing.status, Listing.Status.REJECTED)

    def test_staff_without_change_permission_cannot_review(self) -> None:
        viewer = get_user_model().objects.create_user(
            username="market-viewer", email="market-viewer@example.test", is_staff=True
        )
        viewer.user_permissions.add(
            *Permission.objects.filter(
                codename__in=("view_listing", "view_sellerverification")
            )
        )
        client = Client()
        client.force_login(viewer)

        listing = client.post(self.listing_url("approve_listing"), DIALOG)
        verification = client.post(
            self.verification_url("approve_verification"), DIALOG
        )

        self.assertEqual(listing.status_code, 403)
        self.assertEqual(verification.status_code, 403)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.PENDING)

    def test_every_marketplace_changelist_and_add_form_render(self) -> None:
        request = RequestFactory().get("/")
        request.user = self.staff
        for model in django_apps.get_app_config("marketplace").get_models():
            if not admin.site.is_registered(model):
                continue
            model_admin = admin.site.get_model_admin(model)
            name = model._meta.model_name
            with self.subTest(model=model._meta.label, page="changelist"):
                self.assertEqual(
                    self.client.get(
                        reverse(f"admin:marketplace_{name}_changelist")
                    ).status_code,
                    200,
                )
            if model_admin.has_add_permission(request):
                with self.subTest(model=model._meta.label, page="add"):
                    self.assertEqual(
                        self.client.get(
                            reverse(f"admin:marketplace_{name}_add")
                        ).status_code,
                        200,
                    )
        for url in (
            reverse("admin:marketplace_sellerprofile_change", args=[self.seller.pk]),
            reverse("admin:marketplace_listing_change", args=[self.listing.pk]),
            reverse(
                "admin:marketplace_sellerverification_change",
                args=[self.verification.pk],
            ),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
