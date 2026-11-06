from __future__ import annotations

import re
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import get_user_model
from django.forms import ChoiceField
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.users.forms import TellUsAboutYouForm


class AuthSurfaceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="surface_user",
            email="surface-user@example.com",
            password="A-valid-password-983!",
            profile_completed=False,
        )
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            primary=True,
            verified=True,
        )

    def test_account_profile_compatibility_url_redirects_to_user_profile(self):
        response = self.client.get("/accounts/profile/")

        self.assertRedirects(
            response,
            reverse("users:profile"),
            fetch_redirect_response=False,
        )

    def test_login_page_has_forgot_password_and_csrf_form(self):
        response = self.client.get(reverse("users:account_login"))

        self.assertContains(response, reverse("account_reset_password"))
        self.assertContains(response, 'name="csrfmiddlewaretoken"')

    def test_authenticated_header_has_post_logout_form_with_csrf(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("home"))

        self.assertContains(
            response,
            f'action="{reverse("users:account_logout")}"',
        )
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, "Sign out")

    def test_profile_write_redirects_after_saving(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("users:profile"),
            {
                "action": "update_profile",
                "full_name": "Updated Surface User",
                "country": "US",
                "phone_country_code": "+1",
                "phone": "5551234567",
                "bio": "A profile update",
            },
        )

        self.assertRedirects(
            response,
            reverse("users:profile"),
            fetch_redirect_response=False,
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, "Updated Surface User")
        self.assertEqual(self.user.country, "US")

    def test_logout_uses_a_valid_csrf_protected_post(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        page = csrf_client.get(reverse("home"))
        token = re.search(
            r'name="csrfmiddlewaretoken" value="([^"]+)"',
            page.content.decode(),
        )
        assert token is not None

        response = csrf_client.post(
            reverse("users:account_logout"),
            {"csrfmiddlewaretoken": token.group(1)},
        )

        self.assertEqual(response.status_code, 302)
        self.assertNotIn("_auth_user_id", csrf_client.session)

    def test_country_fields_have_searchable_complete_choices(self):
        form = TellUsAboutYouForm(user=self.user)
        country_field = form.fields["country"]
        phone_field = form.fields["phone_country_code"]
        assert isinstance(country_field, ChoiceField)
        assert isinstance(phone_field, ChoiceField)
        country_options = country_field.choices
        phone_options = phone_field.choices
        assert isinstance(country_options, list)
        assert isinstance(phone_options, list)
        country_choices = dict(country_options)
        phone_choices = dict(phone_options)

        self.assertIn("US", country_choices)
        self.assertIn("CA", country_choices)
        self.assertIn("+1", phone_choices)
        self.assertIn("United States", phone_choices["+1"])
        self.assertIn("Canada", phone_choices["+1"])

    @override_settings(COUNTRY_HEADER="CF-IPCountry", TRUSTED_PROXY_COUNT=1)
    def test_profile_completion_suggests_detected_country_without_saving_it(self):
        self.client.force_login(self.user)

        with patch("requests.get") as network:
            response = self.client.get(
                reverse("users:tell_us_about_you"), HTTP_CF_IPCOUNTRY="CA"
            )

        network.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"]["country"].value(), "CA")
        self.assertEqual(response.context["form"]["phone_country_code"].value(), "+1")
        self.user.refresh_from_db()
        self.assertEqual(self.user.country, "")
        self.assertEqual(self.user.phone_country_code, "")

    def test_csrf_middleware_rejects_bad_tokens_with_app_owned_page(self):
        self.assertTrue(
            any(
                middleware == "django.middleware.csrf.CsrfViewMiddleware"
                for middleware in settings.MIDDLEWARE
            )
        )
        self.assertEqual(
            settings.CSRF_FAILURE_VIEW,
            "app.views.csrf_failure",
        )

        csrf_client = Client(enforce_csrf_checks=True)
        response = csrf_client.post(
            reverse("users:account_signup"),
            {
                "email": "csrf-blocked@example.com",
                "password1": "Another-valid-password-983!",
                "password2": "Another-valid-password-983!",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "errors/csrf_failure.html")
        self.assertContains(response, "This form is out of date", status_code=403)
        self.assertFalse(
            get_user_model().objects.filter(email="csrf-blocked@example.com").exists()
        )

    def test_csrf_token_from_login_form_is_accepted(self):
        csrf_client = Client(enforce_csrf_checks=True)
        form_response = csrf_client.get(reverse("users:account_login"))
        token = re.search(
            r'name="csrfmiddlewaretoken" value="([^"]+)"',
            form_response.content.decode(),
        )
        assert token is not None

        response = csrf_client.post(
            reverse("users:account_login"),
            {
                "login": "not-a-user@example.com",
                "password": "not-a-password",
                "csrfmiddlewaretoken": token.group(1),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(
            "errors/csrf_failure.html",
            [template.name for template in response.templates],
        )
