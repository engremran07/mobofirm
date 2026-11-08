from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.firmwares.models import (
    AccessTier,
    Brand,
    Model,
    OfficialFirmware,
    PendingFirmware,
    Variant,
)
from apps.marketplace import submissions
from apps.marketplace.models import (
    ContactPreference,
    SellerProfile,
    SellerSocialLink,
)

PASSWORD = "Pass-12345!"


def make_user(name: str):
    return get_user_model().objects.create_user(
        username=name,
        email=f"{name}@example.com",
        password=PASSWORD,
        email_verified_at=timezone.now(),
    )


class DashboardTestCase(TestCase):
    def setUp(self):
        self.owner = make_user("dash_owner")
        self.rival = make_user("dash_rival")
        self.seller = SellerProfile.objects.create(
            user=self.owner, display_name="Dash Seller", verified=True
        )
        self.brand = Brand.objects.create(name="Dash Brand", slug="dash-brand")
        self.model = Model.objects.create(brand=self.brand, name="D1", slug="d1")
        self.variant = Variant.objects.create(
            model=self.model, name="Global", slug="global"
        )
        self.client = Client()
        self.client.force_login(self.owner)

    def pending(self, user, name: str, **extra) -> PendingFirmware:
        return PendingFirmware.objects.create(
            original_file_name=name,
            stored_file_path=f"/firmware/{name}",
            uploader=user,
            uploaded_brand=self.brand,
            uploaded_model=self.model,
            uploaded_variant=self.variant,
            **extra,
        )


class SubmissionsTableTests(DashboardTestCase):
    def test_rows_show_variant_tier_prices_and_review_state(self):
        self.pending(
            self.owner,
            "paid-file.zip",
            access_tier=AccessTier.PAID,
            proposed_price=Decimal("9.50"),
        )
        self.pending(
            self.owner,
            "priced-file.zip",
            access_tier=AccessTier.PAID,
            proposed_price=Decimal("9.50"),
            approved_price=Decimal("7.00"),
            admin_decision="approved",
        )
        self.pending(self.owner, "featured-file.zip", access_tier=AccessTier.FEATURED)
        self.pending(
            self.owner,
            "rejected-file.zip",
            admin_decision="rejected",
            admin_notes="Wrong variant selected",
        )

        response = self.client.get(reverse("marketplace:dashboard"))

        self.assertContains(response, "Dash Brand D1 · Global")
        self.assertContains(response, "Proposed: 9.50 USD")
        self.assertContains(response, "Approved: not yet")
        self.assertContains(response, "Approved: 7.00 USD")
        self.assertContains(response, "Waiting for staff to approve the price")
        self.assertContains(response, "Downloadable by package holders only")
        self.assertContains(response, "Wrong variant selected")
        self.assertContains(response, "Waiting for review")
        self.assertContains(response, "Rejected")

    def test_dashboard_never_shows_other_users_submissions(self):
        self.pending(self.owner, "mine.zip")
        self.pending(self.rival, "secret-rival.zip", admin_notes="private note")

        response = self.client.get(reverse("marketplace:dashboard"))

        self.assertContains(response, "mine.zip")
        self.assertNotContains(response, "secret-rival.zip")
        self.assertNotContains(response, "private note")
        rows = submissions.submissions_for(self.owner)
        self.assertEqual([row.file_name for row in rows], ["mine.zip"])
        self.assertEqual(submissions.submission_summary(self.owner).total, 1)

    def test_approved_row_links_the_public_record_only_when_it_is_public(self):
        record = OfficialFirmware.objects.create(
            original_file_name="live.zip",
            stored_file_path="/firmware/live.zip",
            brand=self.brand,
            model=self.model,
            variant=self.variant,
            uploader=self.owner,
            is_verified=True,
        )
        self.pending(
            self.owner,
            "live.zip",
            admin_decision="approved",
            routed_category="official",
            routed_object_id=record.pk,
        )
        detail = reverse(
            "firmwares:firmware_detail",
            kwargs={"firmware_type": "official", "firmware_id": record.pk},
        )

        self.assertContains(self.client.get(reverse("marketplace:dashboard")), detail)

        OfficialFirmware.objects.filter(pk=record.pk).update(is_verified=False)
        self.assertNotContains(
            self.client.get(reverse("marketplace:dashboard")), detail
        )

    def test_empty_state_and_upload_call_to_action(self):
        response = self.client.get(reverse("marketplace:dashboard"))

        self.assertContains(response, "You have not uploaded any firmware yet")
        self.assertContains(response, reverse("firmwares:upload"))
        self.assertContains(response, "Upload firmware")

    @patch("apps.marketplace.wallet_bridge.wallet_enabled", return_value=False)
    def test_rules_and_the_wallet_status_are_stated(self, _enabled):
        response = self.client.get(reverse("marketplace:dashboard"))

        self.assertContains(response, "Staff approve the price")
        self.assertContains(response, "Only people with an active package")
        self.assertContains(response, "credit wallet is not switched on")
        self.assertNotContains(response, "Request a payout")


class DashboardAccessTests(DashboardTestCase):
    def test_anonymous_and_unapproved_users_cannot_open_the_dashboard(self):
        url = reverse("marketplace:dashboard")

        self.assertEqual(Client().get(url).status_code, 302)
        rival = Client()
        rival.force_login(self.rival)
        self.assertRedirects(
            rival.get(url),
            reverse("marketplace:become_seller"),
            fetch_redirect_response=False,
        )
        SellerProfile.objects.filter(pk=self.seller.pk).update(verified=False)
        self.assertRedirects(
            self.client.get(url),
            reverse("marketplace:application_status"),
            fetch_redirect_response=False,
        )

    def test_deactivated_seller_sees_the_suspended_status(self):
        SellerProfile.objects.filter(pk=self.seller.pk).update(is_active=False)

        response = self.client.get(reverse("marketplace:application_status"))

        self.assertContains(response, "Suspended")
        self.assertNotContains(response, "Update and apply again")


class PublicProfileTests(DashboardTestCase):
    def test_links_website_specialities_files_and_badge_render_safely(self):
        self.seller.bio = "<script>alert('x')</script> Firmware person"
        self.seller.website = "https://seller.example.org/"
        self.seller.contact_preference = ContactPreference.STAFF
        self.seller.save()
        self.seller.brands.add(self.brand)
        SellerSocialLink.objects.create(
            seller=self.seller, platform="youtube", url="https://youtube.com/@seller"
        )
        OfficialFirmware.objects.create(
            original_file_name="public.zip",
            stored_file_path="/firmware/public.zip",
            brand=self.brand,
            model=self.model,
            variant=self.variant,
            uploader=self.owner,
            is_verified=True,
            access_tier=AccessTier.FEATURED,
        )
        OfficialFirmware.objects.create(
            original_file_name="hidden.zip",
            stored_file_path="/firmware/hidden.zip",
            brand=self.brand,
            model=self.model,
            uploader=self.owner,
            is_verified=False,
        )

        response = self.client.get(self.seller.get_absolute_url())

        self.assertContains(response, "Verified by staff")
        self.assertContains(
            response, "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;"
        )
        self.assertNotContains(response, "<script>alert")
        self.assertContains(response, 'href="https://seller.example.org/"')
        self.assertContains(response, 'href="https://youtube.com/@seller"')
        self.assertContains(response, 'rel="nofollow noopener noreferrer"')
        self.assertContains(response, "Dash Brand")
        self.assertContains(response, "public.zip")
        self.assertContains(response, "Featured")
        self.assertNotContains(response, "hidden.zip")
        self.assertContains(response, "Contact me through site staff")

    def test_unsafe_stored_links_are_not_rendered_as_anchors(self):
        SellerSocialLink.objects.bulk_create(
            [
                SellerSocialLink(
                    seller=self.seller, platform="other", url="javascript:alert(1)"
                ),
                SellerSocialLink(
                    seller=self.seller,
                    platform="other",
                    url="http://plain.example.org/",
                ),
            ]
        )
        SellerProfile.objects.filter(pk=self.seller.pk).update(
            website="javascript:alert(2)"
        )

        response = self.client.get(self.seller.get_absolute_url())

        self.assertNotContains(response, 'href="javascript:')
        self.assertNotContains(response, 'href="http://plain.example.org/"')

    def test_profile_without_files_shows_an_empty_state(self):
        response = self.client.get(self.seller.get_absolute_url())

        self.assertContains(response, "No approved files yet")
