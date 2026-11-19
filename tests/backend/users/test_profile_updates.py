"""Inline profile and password forms validate input instead of failing in the database."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework.authtoken.models import Token

from apps.users.profile_models import Profile

PASSWORD = "Strong-pass-4271!"
NEW_PASSWORD = "Different-pass-9937!"


def make_user(name: str, **fields):
    return get_user_model().objects.create_user(
        username=name,
        email=f"{name}@example.com",
        password=PASSWORD,
        profile_completed=True,
        **fields,
    )


class ProfileFormValidationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("editor", phone="+15550001111")
        self.client.force_login(self.user)

    def post_profile(self, **fields):
        data = {"action": "update_profile", "full_name": "Edi Tor", **fields}
        return self.client.post(reverse("users:profile"), data)

    def test_a_valid_form_saves_and_redirects(self):
        response = self.post_profile(
            website="example.org",
            bio="Hello",
            location="Lahore",
            phone="+1 555 000 2222",
        )

        self.assertRedirects(
            response, reverse("users:profile"), fetch_redirect_response=False
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, "+15550002222")
        profile = Profile.objects.get(user=self.user)
        self.assertEqual(profile.website, "https://example.org")
        self.assertEqual(profile.bio, "Hello")

    def test_a_phone_number_used_by_someone_else_is_refused_without_a_server_error(
        self,
    ):
        make_user("other", phone="+15559998888")

        response = self.post_profile(phone="+1 555 999 8888")

        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, "+15550001111")
        self.assertFalse(Profile.objects.filter(user=self.user).exists())

    def test_keeping_your_own_phone_number_is_fine(self):
        self.post_profile(phone="+15550001111")

        self.assertTrue(Profile.objects.filter(user=self.user).exists())

    def test_an_invalid_website_saves_nothing(self):
        self.post_profile(website="javascript:alert(1)", bio="should not persist")

        self.assertFalse(Profile.objects.filter(user=self.user).exists())

    def test_over_long_values_are_refused_not_sent_to_the_database(self):
        self.post_profile(full_name="x" * 151, bio="should not persist")

        self.user.refresh_from_db()
        self.assertNotEqual(self.user.full_name, "x" * 151)
        self.assertFalse(Profile.objects.filter(user=self.user).exists())

    def test_the_standalone_name_form_enforces_the_length_too(self):
        response = self.client.post(
            reverse("users:profile"),
            {"action": "update_full_name", "full_name": "y" * 200},
        )

        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.full_name, "y" * 200)


class PasswordChangeTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox.clear()
        self.user = make_user("changer")
        self.client.force_login(self.user)

    def change(self, current: str = PASSWORD, new: str = NEW_PASSWORD):
        return self.client.post(
            reverse("users:profile"),
            {
                "action": "change_password",
                "current_password": current,
                "new_password1": new,
                "new_password2": new,
            },
        )

    def test_changing_the_password_revokes_api_tokens_and_keeps_the_session(self):
        Token.objects.create(user=self.user)

        response = self.change()

        self.assertRedirects(
            response, reverse("users:profile"), fetch_redirect_response=False
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PASSWORD))
        self.assertFalse(Token.objects.filter(user=self.user).exists())
        self.assertEqual(self.client.get(reverse("users:profile")).status_code, 200)

    def test_a_wrong_current_password_changes_nothing(self):
        Token.objects.create(user=self.user)

        self.change(current="wrong")

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))
        self.assertTrue(Token.objects.filter(user=self.user).exists())

    def test_guessing_the_current_password_is_rate_limited(self):
        for _ in range(5):
            self.change(current="wrong")

        self.change(current=PASSWORD)

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))
