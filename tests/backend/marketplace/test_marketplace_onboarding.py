from __future__ import annotations

from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.firmwares.models import Brand, Chipset
from apps.marketplace import services
from apps.marketplace.models import (
    MAX_SOCIAL_LINKS,
    SellerProfile,
    SellerVerification,
)
from apps.marketplace.validators import validate_https_url
from apps.users.models import Notification
from apps.users.profile_models import Profile
from apps.users.services.profile_completion import profile_completion_url

PASSWORD = "Pass-12345!"
BIO = (
    "I repack and verify official MediaTek firmware and publish tested flashing notes."
)
SELL_URL = "marketplace:become_seller"


def make_user(name: str, *, complete: bool = True, **extra):
    user = get_user_model().objects.create_user(
        username=name,
        email=f"{name}@example.com",
        password=PASSWORD,
        email_verified_at=timezone.now(),
        **extra,
    )
    if complete:
        user.full_name = f"{name.title()} Person"
        user.country = "PK"
        user.profile_completed = True
        user.save()
        Profile.objects.update_or_create(
            user=user, defaults={"social_links": {"interests": ["flashing"]}}
        )
    return user


def payload(links: list[tuple[str, str]] | None = None, **overrides: object) -> dict:
    data: dict[str, object] = {
        "bio": BIO,
        "website": "",
        "contact_preference": "social",
        "accept_terms": "on",
        "links-TOTAL_FORMS": str(MAX_SOCIAL_LINKS),
        "links-INITIAL_FORMS": "0",
        "links-MIN_NUM_FORMS": "0",
        "links-MAX_NUM_FORMS": str(MAX_SOCIAL_LINKS),
    }
    rows = list(links or [])
    for index in range(MAX_SOCIAL_LINKS):
        platform, url = rows[index] if index < len(rows) else ("", "")
        data[f"links-{index}-platform"] = platform
        data[f"links-{index}-url"] = url
    data.update(overrides)
    return data


class OnboardingTestCase(TestCase):
    def setUp(self):
        self.applicant = make_user("applicant")
        self.staff = make_user("review_staff", is_staff=True, is_superuser=True)
        self.client = Client()
        self.client.force_login(self.applicant)
        self.url = reverse(SELL_URL)


class ProfileGateTests(OnboardingTestCase):
    def test_anonymous_visitors_are_sent_to_login(self):
        response = Client().get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_unverified_email_cannot_apply(self):
        unverified = get_user_model().objects.create_user(
            username="unverified", email="unverified@example.com", password=PASSWORD
        )
        client = Client()
        client.force_login(unverified)

        response = client.get(self.url)

        self.assertRedirects(
            response,
            reverse("marketplace:listing_browse"),
            fetch_redirect_response=False,
        )

    def test_incomplete_profile_is_sent_to_completion_with_next_and_a_message(self):
        incomplete = make_user("incomplete", complete=False)
        client = Client()
        client.force_login(incomplete)

        response = client.get(self.url)

        self.assertRedirects(
            response, profile_completion_url(self.url), fetch_redirect_response=False
        )
        text = " ".join(str(m) for m in get_messages(response.wsgi_request))
        self.assertIn("Finish your profile", text)
        self.assertIn("your full name", text)
        self.assertIn("your country", text)
        self.assertIn("at least one interest", text)

    def test_incomplete_profile_cannot_post_an_application(self):
        incomplete = make_user("incomplete", complete=False)
        client = Client()
        client.force_login(incomplete)

        response = client.post(self.url, payload())

        self.assertEqual(response.status_code, 302)
        self.assertFalse(SellerProfile.objects.filter(user=incomplete).exists())

    def test_complete_profile_sees_only_the_seller_part(self):
        response = self.client.get(self.url)

        self.assertContains(response, "About your work")
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertContains(response, 'aria-label="Breadcrumb"')
        for profile_label in ("Full name", "Country", "Phone", "Username"):
            self.assertNotContains(response, profile_label)

    def test_form_is_csrf_protected(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.applicant)

        self.assertEqual(client.post(self.url, payload()).status_code, 403)


class ApplicationSubmissionTests(OnboardingTestCase):
    def test_valid_application_creates_one_pending_review_and_notifies_staff(self):
        brand = Brand.objects.create(name="Tecno", slug="tecno")
        chipset = Chipset.objects.create(
            vendor="MediaTek", name="Helio G99", slug="g99"
        )
        data = payload(
            [
                ("youtube", "https://www.youtube.com/@applicant"),
                ("", "https://example.org/a"),
            ],
            brands=[brand.pk],
            chipsets=[chipset.pk],
            website="https://applicant.example.org/",
            contact_preference="website",
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url, data)

        self.assertRedirects(
            response,
            reverse("marketplace:application_status"),
            fetch_redirect_response=False,
        )
        seller = SellerProfile.objects.get(user=self.applicant)
        self.assertFalse(seller.verified)
        self.assertEqual(seller.display_name, "Applicant Person")
        self.assertEqual(seller.bio, BIO)
        self.assertEqual(seller.contact_preference, "website")
        self.assertEqual(seller.website, "https://applicant.example.org/")
        self.assertIsNotNone(seller.terms_accepted_at)
        self.assertEqual(list(seller.brands.all()), [brand])
        self.assertEqual(list(seller.chipsets.all()), [chipset])
        self.assertEqual(
            list(seller.social_links.values_list("platform", "url")),
            [
                ("youtube", "https://www.youtube.com/@applicant"),
                ("other", "https://example.org/a"),
            ],
        )
        verification = seller.verifications.get()
        self.assertEqual(verification.status, SellerVerification.Status.PENDING)
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.staff, title="New seller application"
            ).exists()
        )

    def test_status_page_shows_the_review_state(self):
        self.client.post(self.url, payload())

        response = self.client.get(reverse("marketplace:application_status"))

        self.assertContains(response, "Waiting for staff review")
        self.assertContains(response, BIO)

    def test_duplicate_submission_keeps_a_single_pending_review(self):
        self.client.post(self.url, payload())
        second = self.client.post(self.url, payload(bio=BIO + " Updated."))

        self.assertRedirects(
            second,
            reverse("marketplace:application_status"),
            fetch_redirect_response=False,
        )
        self.assertEqual(SellerVerification.objects.count(), 1)
        self.assertEqual(SellerProfile.objects.get().bio, BIO)

    def test_service_refuses_to_submit_twice(self):
        data = services.ApplicationData(bio=BIO, accepted_terms=True)
        services.submit_application(self.applicant, data)

        with self.assertRaises(services.MarketplaceError):
            services.submit_application(self.applicant, data)
        with self.assertRaises(services.MarketplaceError):
            services.submit_application(
                make_user("terms_less"), services.ApplicationData(bio=BIO)
            )

    def test_rate_limit_blocks_the_post_without_creating_anything(self):
        with mock.patch("apps.marketplace.views.allow_action", return_value=False):
            response = self.client.post(self.url, payload())

        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Too many attempts", status_code=429)
        self.assertFalse(SellerProfile.objects.exists())


class ApplicationValidationTests(OnboardingTestCase):
    def assert_rejected(self, response, *, text: str | None = None):
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SellerProfile.objects.filter(user=self.applicant).exists())
        if text:
            self.assertContains(response, text)

    def test_short_bio_and_missing_terms_are_rejected(self):
        self.assert_rejected(self.client.post(self.url, payload(bio="Too short")))
        no_terms = payload()
        del no_terms["accept_terms"]
        self.assert_rejected(
            self.client.post(self.url, no_terms), text="Accept the seller terms"
        )

    @mock.patch("apps.marketplace.views.allow_action", return_value=True)
    def test_link_scheme_and_host_abuse_is_rejected(self, _allowed):
        bad_links = [
            ("other", "javascript:alert(1)"),
            ("other", "JaVaScRiPt:alert(1)"),
            ("other", "data:text/html;base64,PHNjcmlwdD4="),
            ("other", "http://example.org/plain"),
            ("other", "ftp://example.org/file"),
            ("other", "//example.org/protocol-relative"),
            ("other", "https://user:secret@example.org/"),
            ("other", "https://localhost/admin"),
            ("other", "https://127.0.0.1/admin"),
            ("other", "https://[::1]/admin"),
            ("other", "https://example.org:8443/x"),
            ("other", "https://exa mple.org/x"),
            ("youtube", "https://youtube.com.evil.example/x"),
            ("telegram", "https://example.org/x"),
        ]
        for platform, url in bad_links:
            with self.subTest(url=url):
                response = self.client.post(self.url, payload([(platform, url)]))
                self.assert_rejected(response)

    def test_website_must_be_https(self):
        for website in ("http://example.org", "javascript:alert(1)", "example.org"):
            with self.subTest(website=website):
                self.assert_rejected(
                    self.client.post(self.url, payload(website=website))
                )

    def test_duplicate_links_are_rejected(self):
        response = self.client.post(
            self.url,
            payload(
                [
                    ("github", "https://github.com/me"),
                    ("github", "https://github.com/me/"),
                ]
            ),
        )

        self.assert_rejected(response, text="only be added once")

    def test_more_than_the_maximum_links_never_saves_extra_rows(self):
        data = payload()
        data["links-TOTAL_FORMS"] = "9"
        for index in range(9):
            data[f"links-{index}-platform"] = "other"
            data[f"links-{index}-url"] = f"https://example.org/{index}"

        self.client.post(self.url, data)

        seller = SellerProfile.objects.filter(user=self.applicant).first()
        saved = seller.social_links.count() if seller else 0
        self.assertLessEqual(saved, MAX_SOCIAL_LINKS)

    def test_too_many_specialities_are_rejected(self):
        brands = [
            Brand.objects.create(name=f"Brand {i}", slug=f"brand-{i}")
            for i in range(11)
        ]

        response = self.client.post(
            self.url, payload(brands=[brand.pk for brand in brands])
        )

        self.assert_rejected(response, text="Choose at most 10 brands")

    def test_unknown_speciality_ids_are_rejected(self):
        self.assert_rejected(self.client.post(self.url, payload(brands=[999999])))

    def test_invalid_input_keeps_what_the_user_typed(self):
        response = self.client.post(
            self.url,
            payload([("other", "http://example.org/plain")], bio=BIO + " Keep me."),
        )

        self.assertContains(response, "Keep me.")
        self.assertContains(response, "Links must start with https://")


class ValidatorTests(TestCase):
    def test_accepts_plain_https_links(self):
        for platform, url in (
            ("youtube", "https://youtu.be/abc"),
            ("x", "https://twitter.com/me"),
            ("xda", "https://xdaforums.com/u/me.1/"),
            ("other", "https://example.org/path?q=1"),
        ):
            with self.subTest(url=url):
                self.assertEqual(validate_https_url(url, platform), url)

    def test_rejects_empty_and_oversized_links(self):
        for url in ("", "   ", "https://example.org/" + "a" * 300):
            with self.subTest(url=url[:30]), self.assertRaises(ValidationError):
                validate_https_url(url)


class ReviewFlowTests(OnboardingTestCase):
    def apply(self):
        self.client.post(
            self.url, payload([("github", "https://github.com/applicant")])
        )
        return SellerVerification.objects.get(seller__user=self.applicant)

    def test_approval_makes_an_active_seller_and_notifies_the_applicant(self):
        verification = self.apply()
        self.assertFalse(services.is_active_seller(self.applicant))

        with self.captureOnCommitCallbacks(execute=True):
            services.review_verification(
                verification.pk, reviewer=self.staff, approve=True, reason="Looks good"
            )

        self.assertTrue(services.is_active_seller(self.applicant))
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.applicant, title="Seller application approved"
            ).exists()
        )
        self.assertRedirects(
            self.client.get(self.url),
            reverse("marketplace:dashboard"),
            fetch_redirect_response=False,
        )
        self.assertEqual(
            self.client.get(reverse("marketplace:dashboard")).status_code, 200
        )

    def test_rejection_shows_the_reason_and_allows_a_corrected_application(self):
        verification = self.apply()
        with self.captureOnCommitCallbacks(execute=True):
            services.review_verification(
                verification.pk,
                reviewer=self.staff,
                approve=False,
                reason="Links are private",
            )

        self.assertFalse(services.is_active_seller(self.applicant))
        status = self.client.get(reverse("marketplace:application_status"))
        self.assertContains(status, "Links are private")
        self.assertContains(status, "Update and apply again")
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.applicant, title="Seller application not approved"
            ).exists()
        )
        form = self.client.get(self.url)
        self.assertContains(form, "Links are private")
        self.assertContains(form, BIO)

        retry = self.client.post(self.url, payload(bio=BIO + " Now with public links."))

        self.assertRedirects(
            retry,
            reverse("marketplace:application_status"),
            fetch_redirect_response=False,
        )
        self.assertEqual(
            SellerVerification.objects.filter(
                seller__user=self.applicant, status=SellerVerification.Status.PENDING
            ).count(),
            1,
        )

    def test_admin_dialog_shows_the_application_and_approves(self):
        verification = self.apply()
        client = Client()
        client.force_login(self.staff)

        detail = client.get(
            reverse(
                "admin:marketplace_sellerverification_change", args=[verification.pk]
            )
        )
        self.assertContains(detail, "https://github.com/applicant")
        self.assertContains(detail, 'rel="nofollow noopener noreferrer"')

        client.post(
            reverse(
                "admin:marketplace_sellerverification_approve_verification",
                args=[verification.pk],
            ),
            {"_form_submitted": "on", "reason": "ok"},
        )
        self.assertTrue(services.is_active_seller(self.applicant))


class SellerContractTests(TestCase):
    def test_is_active_seller_states(self):
        user = make_user("contract")
        self.assertFalse(services.is_active_seller(None))
        self.assertFalse(services.is_active_seller(user))
        self.assertIsNone(services.seller_for(user))

        seller = SellerProfile.objects.create(user=user, display_name="C")
        self.assertFalse(services.is_active_seller(user))
        self.assertEqual(services.seller_for(user), seller)

        SellerProfile.objects.filter(pk=seller.pk).update(verified=True)
        self.assertTrue(services.is_active_seller(user))

        SellerProfile.objects.filter(pk=seller.pk).update(is_active=False)
        self.assertFalse(services.is_active_seller(user))
        self.assertIsNone(services.seller_for(user))

    def test_anonymous_users_are_never_sellers(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertFalse(services.is_active_seller(AnonymousUser()))
        self.assertIsNone(services.seller_for(AnonymousUser()))


class PickerTests(OnboardingTestCase):
    def setUp(self):
        super().setUp()
        self.tecno = Brand.objects.create(name="Tecno", slug="tecno")
        self.infinix = Brand.objects.create(name="Infinix", slug="infinix")
        self.chipset = Chipset.objects.create(vendor="Unisoc", name="T606", slug="t606")

    def test_search_returns_matching_options_only(self):
        url = reverse("marketplace:picker", kwargs={"kind": "brands"})

        response = self.client.get(url, {"q": "tec"})

        self.assertContains(response, "Tecno")
        self.assertNotContains(response, "Infinix")
        self.assertNotContains(response, "<html")

    def test_chosen_options_stay_checked_after_a_search(self):
        url = reverse("marketplace:picker", kwargs={"kind": "brands"})

        response = self.client.get(url, {"q": "inf", "brands": [str(self.tecno.pk)]})

        self.assertContains(response, f'value="{self.tecno.pk}" checked')
        self.assertContains(response, "Infinix")

    def test_chipsets_search_by_vendor(self):
        url = reverse("marketplace:picker", kwargs={"kind": "chipsets"})

        self.assertContains(self.client.get(url, {"q": "unisoc"}), "Unisoc T606")

    def test_no_match_shows_a_message_and_bad_ids_are_ignored(self):
        url = reverse("marketplace:picker", kwargs={"kind": "brands"})

        response = self.client.get(
            url, {"q": "zzzz", "brands": ["abc", "-1", "9" * 30]}
        )

        self.assertContains(response, "No brand matches")

    def test_unknown_kind_is_404_and_anonymous_is_redirected(self):
        self.assertEqual(
            self.client.get(
                reverse("marketplace:picker", kwargs={"kind": "users"})
            ).status_code,
            404,
        )
        anonymous = Client().get(
            reverse("marketplace:picker", kwargs={"kind": "brands"})
        )
        self.assertEqual(anonymous.status_code, 302)
