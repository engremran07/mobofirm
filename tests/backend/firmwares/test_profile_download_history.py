from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.firmwares.models import (
    Brand,
    DownloadSession,
    DownloadToken,
    EngineeringFirmware,
    Model,
)


class ProfileDownloadHistoryTests(TestCase):
    def test_profile_lists_downloads_of_every_firmware_category(self):
        user = get_user_model().objects.create_user(
            username="history-user", email="history-user@example.test"
        )
        brand = Brand.objects.create(name="History Brand", slug="history-brand")
        model = Model.objects.create(brand=brand, name="History", slug="history")
        firmware = EngineeringFirmware.objects.create(
            original_file_name="history-engineering.zip",
            stored_file_path="/f/history-engineering.zip",
            brand=brand,
            model=model,
            is_verified=True,
        )
        token = DownloadToken.objects.create(
            firmware=firmware,
            user=user,
            token="history-token",
            status=DownloadToken.Status.USED,
            used_at=timezone.now(),
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        DownloadSession.objects.create(
            token=token, user=user, status=DownloadSession.Status.COMPLETED
        )
        client = Client()
        client.force_login(user)

        response = client.get(reverse("users:profile"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "history-engineering.zip")
