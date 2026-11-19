"""Social provider/posting detail endpoints answer 404, not 500, for a bad id.

Regression for the crawl 500s at ``/users/api/social-providers/smoke-test/`` and
``/users/api/social-posting/smoke-test/``: the ``<str:...>`` id reached a UUID
primary-key lookup, whose ``ValidationError`` was reported as a server error.
"""

from __future__ import annotations

import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from apps.users.models_social import SocialPostingAccount, SocialProviderConfig

BAD_ID = "smoke-test"
UNKNOWN_ID = "00000000-0000-4000-8000-000000000001"


class SocialApiBadIdTests(TestCase):
    def setUp(self):
        staff = get_user_model().objects.create_user(
            username="social_api_staff",
            email="social-api-staff@example.com",
            password="Pass-12345!",
            is_staff=True,
        )
        staff.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="users",
                content_type__model__in=(
                    "socialproviderconfig",
                    "socialpostingaccount",
                ),
            )
        )
        self.client = Client()
        self.client.force_login(get_user_model().objects.get(pk=staff.pk))

    def test_provider_detail_with_a_malformed_id_is_a_json_404(self):
        response = self.client.get(
            reverse("users:social_provider_detail_api", args=[BAD_ID])
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "Provider not found"})

    def test_posting_detail_with_a_malformed_id_is_a_json_404(self):
        response = self.client.get(
            reverse("users:social_posting_detail_api", args=[BAD_ID])
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "Account not found"})

    def test_unknown_but_well_formed_ids_are_still_404(self):
        provider = self.client.get(
            reverse("users:social_provider_detail_api", args=[UNKNOWN_ID])
        )
        account = self.client.get(
            reverse("users:social_posting_detail_api", args=[UNKNOWN_ID])
        )

        self.assertEqual((provider.status_code, account.status_code), (404, 404))

    def test_post_only_actions_reject_get_with_405_and_bad_ids_with_404(self):
        for name in (
            "social_provider_test_api",
            "social_provider_sync_api",
            "social_posting_test_api",
            "social_posting_send_test_api",
        ):
            with self.subTest(name=name, method="GET"):
                response = self.client.get(reverse(f"users:{name}", args=[BAD_ID]))
                self.assertEqual(response.status_code, 405)
            with self.subTest(name=name, method="POST"):
                response = self.client.post(reverse(f"users:{name}", args=[BAD_ID]))
                self.assertEqual(response.status_code, 404)

    def test_an_existing_provider_and_account_are_still_found(self):
        provider = SocialProviderConfig.objects.create(provider="github")
        account = SocialPostingAccount.objects.create(
            platform="telegram", account_name="Mobofirm channel"
        )

        provider_response = self.client.get(
            reverse("users:social_provider_detail_api", args=[str(provider.pk)])
        )
        account_response = self.client.get(
            reverse("users:social_posting_detail_api", args=[str(account.pk)])
        )

        self.assertEqual(provider_response.status_code, 200)
        self.assertEqual(provider_response.json()["id"], str(provider.pk))
        self.assertEqual(account_response.status_code, 200)
        self.assertEqual(uuid.UUID(account_response.json()["id"]), account.pk)
