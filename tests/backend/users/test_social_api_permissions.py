"""Social provider and posting-account APIs follow per-model permissions.

``is_staff`` only admits someone to the console; these endpoints read and write OAuth
secrets, so each method also needs the matching model permission, and a failure never
echoes the exception text.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import DatabaseError
from django.test import Client, TestCase
from django.urls import reverse

from apps.users.models_social import SocialPostingAccount, SocialProviderConfig

PROVIDER_MODEL = "socialproviderconfig"
POSTING_MODEL = "socialpostingaccount"


def staff_client(*codenames: str, superuser: bool = False) -> Client:
    user = get_user_model().objects.create_user(
        username=f"staff{len(codenames)}{superuser}",
        email=f"staff-{len(codenames)}-{superuser}@example.com",
        password="Pass-12345!",
        is_staff=True,
        is_superuser=superuser,
    )
    user.user_permissions.set(Permission.objects.filter(codename__in=codenames))
    client = Client()
    client.force_login(get_user_model().objects.get(pk=user.pk))
    return client


class SocialApiPermissionTests(TestCase):
    def setUp(self):
        self.provider = SocialProviderConfig.objects.create(provider="github")
        self.account = SocialPostingAccount.objects.create(
            platform="telegram", account_name="News"
        )

    def test_plain_staff_cannot_read_or_change_anything(self):
        client = staff_client()
        urls = (
            ("get", reverse("users:social_providers_api")),
            ("get", reverse("users:social_posting_api")),
            (
                "get",
                reverse("users:social_provider_detail_api", args=[self.provider.pk]),
            ),
            (
                "put",
                reverse("users:social_provider_detail_api", args=[self.provider.pk]),
            ),
            (
                "delete",
                reverse("users:social_provider_detail_api", args=[self.provider.pk]),
            ),
            ("post", reverse("users:social_provider_create_api")),
            (
                "post",
                reverse("users:social_provider_test_api", args=[self.provider.pk]),
            ),
            (
                "post",
                reverse("users:social_provider_sync_api", args=[self.provider.pk]),
            ),
            ("post", reverse("users:social_posting_create_api")),
            ("get", reverse("users:social_posting_detail_api", args=[self.account.pk])),
            ("put", reverse("users:social_posting_detail_api", args=[self.account.pk])),
            (
                "delete",
                reverse("users:social_posting_detail_api", args=[self.account.pk]),
            ),
            ("post", reverse("users:social_posting_test_api", args=[self.account.pk])),
            (
                "post",
                reverse("users:social_posting_send_test_api", args=[self.account.pk]),
            ),
        )
        for method, url in urls:
            with self.subTest(method=method, url=url):
                response = getattr(client, method)(url)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json(), {"error": "Permission denied"})
        self.assertTrue(
            SocialProviderConfig.objects.filter(pk=self.provider.pk).exists()
        )
        self.assertTrue(
            SocialPostingAccount.objects.filter(pk=self.account.pk).exists()
        )

    def test_the_view_permission_allows_reading_but_not_writing(self):
        client = staff_client(f"view_{PROVIDER_MODEL}")
        detail = reverse("users:social_provider_detail_api", args=[self.provider.pk])

        self.assertEqual(
            client.get(reverse("users:social_providers_api")).status_code, 200
        )
        self.assertEqual(client.get(detail).status_code, 200)
        self.assertEqual(
            client.put(
                detail,
                json.dumps({"display_name": "x"}),
                content_type="application/json",
            ).status_code,
            403,
        )
        self.assertEqual(client.delete(detail).status_code, 403)
        self.assertEqual(
            client.get(reverse("users:social_posting_api")).status_code, 403
        )

    def test_the_change_permission_allows_updating(self):
        client = staff_client(f"change_{PROVIDER_MODEL}")
        detail = reverse("users:social_provider_detail_api", args=[self.provider.pk])

        response = client.put(
            detail,
            json.dumps({"display_name": "Renamed"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.provider.refresh_from_db()
        self.assertEqual(self.provider.display_name, "Renamed")

    def test_deleting_needs_the_permission_and_a_superuser(self):
        detail = reverse("users:social_provider_detail_api", args=[self.provider.pk])
        with_perm = staff_client(f"delete_{PROVIDER_MODEL}")

        self.assertEqual(with_perm.delete(detail).status_code, 403)
        self.assertTrue(
            SocialProviderConfig.objects.filter(pk=self.provider.pk).exists()
        )
        self.assertEqual(staff_client(superuser=True).delete(detail).status_code, 200)
        self.assertFalse(
            SocialProviderConfig.objects.filter(pk=self.provider.pk).exists()
        )

    def test_a_server_failure_never_echoes_the_exception_text(self):
        client = staff_client(f"view_{PROVIDER_MODEL}")

        with patch.object(
            SocialProviderConfig,
            "update_user_count",
            side_effect=DatabaseError("password=hunter2 host=db.internal"),
        ):
            response = client.get(reverse("users:social_providers_api"))

        self.assertEqual(response.status_code, 500)
        self.assertNotIn("hunter2", response.content.decode())
        self.assertNotIn("db.internal", response.content.decode())

    def test_anonymous_requests_are_turned_away(self):
        response = Client().get(reverse("users:social_providers_api"))

        self.assertEqual(response.status_code, 302)
